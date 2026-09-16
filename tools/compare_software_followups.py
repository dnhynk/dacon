"""Measure fact-directed follow-up retrieval with matched cumulative source caps.

The first Gemma read is already observed. This tool executes CPU retrieval only;
it never treats the second candidate context as read or as a fresh F1 result.
"""
import argparse
from collections import defaultdict
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import restored
from submission.pps.embeddings import BGEDenseEncoder, BGE_REVISION
from submission.pps.notice_search import NoticeSearch, factual_queries
from submission.pps.software_facts import decide, followup_plan
from submission.runtime import source_manifest


def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def rows(p):
    with gzip.open(p, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))
def save(p, obj):
    with p.open('x', encoding='utf-8') as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnostic', type=Path, required=True)
    parser.add_argument('--retrieval-baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    freeze = {'hypothesis':'Typed unresolved actors, scope and conditions yield factual follow-up queries that recover complementary source evidence.',
        'source_sha256':source_manifest(), 'tool_sha256':digest(__file__),
        'first_read_cap':4096, 'cumulative_unique_source_cap':8192,
        'maximum_source_tokens_served_across_two_reads':12288,
        'controls':['fixed_facts_with_prior_context','typed_followup_with_prior_context'],
        'selection_policy':'rrf', 'first_context':'All original first-read source ranges pinned in both arms; no forgetting of observed context.',
        'eligibility':'Every format-valid typed diagnostic with a tri-state unknown final decision; resolved and invalid cases reported separately.',
        'queries':'The same fixed item20 factual queries in both arms, plus followup_plan queries in the treatment. No review answers or target strings used.',
        'classification_labels_read':False, 'review_cases_exposed':True,
        'hyperparameter_sweep':False, 'new_gemma_calls':0, 'second_context_read_by_model':False,
        'retrieval_manifest_sha256':digest(args.retrieval_baseline/'preregistered.json'),
        'diagnostic_responses_sha256':digest(args.diagnostic/'contrast_results.jsonl.gz')}
    save(args.output/'preregistered.json', freeze)
    prior = json.loads((args.retrieval_baseline/'preregistered.json').read_text(encoding='utf-8'))
    assert prior['embedding_revision'] == BGE_REVISION
    assert digest(ROOT/'models/gemma-tokenizer/tokenizer.json') == prior['tokenizer_sha256']
    cases = {c['task_id']:c for c in prior['cases']}
    packets = {p['request_key']:p for p in rows(args.diagnostic/'current_packets.jsonl.gz')}
    recs = {r['id']:r for r in rows(args.diagnostic/'current_inputs.jsonl.gz')}
    observed = [r for r in rows(args.diagnostic/'contrast_results.jsonl.gz')
                if packets[r['request_key']]['generation']['response_format']=='software_facts']
    assert len(observed)==18 and len({r['request_key'] for r in observed})==18
    groups=defaultdict(list)
    for r in observed: groups[r['record_id']].append(r)
    import numpy as np
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    encoder=BGEDenseEncoder(ROOT/'models/bge-m3')
    measurements, skipped, cache = [], [], []
    with gzip.open(args.output/'retrieval_results.jsonl.gz','wt',encoding='utf-8') as stream:
        for rid, group in groups.items():
            rec=recs[rid]
            search=NoticeSearch(rec,tokenizer,encoder)
            key=hashlib.sha256(rid.encode()).hexdigest()[:16]
            idx, vec=args.retrieval_baseline/f'{key}.chunks.json',args.retrieval_baseline/f'{key}.vectors.npy'
            metadata=json.loads(idx.read_text(encoding='utf-8'))
            assert metadata['record_id']==rid and metadata['documents_sha256']==search.doc_hashes
            assert metadata['chunks']==[asdict(s) for s in search.chunks]
            vectors=np.load(vec,allow_pickle=False)
            assert vectors.shape==(len(search.chunks),1024) and np.isfinite(vectors).all()
            assert np.allclose(np.linalg.norm(vectors,axis=1),1,atol=1e-5)
            search.vectors=vectors
            cache.append({'record_id':rid,'index_sha256':digest(idx),'vectors_sha256':digest(vec)})
            for row in group:
                packet=packets[row['request_key']]
                if row['parse_error'] or row['row'] is None:
                    skipped.append({'request_key':row['request_key'],'reason':'unresolved_format_or_consumer'})
                    continue
                decision=decide(rec,row['response']['text'],restored(packet)['spans'])
                if decision['value'] is not None:
                    skipped.append({'request_key':row['request_key'],'reason':'already_resolved','value':decision['value']})
                    continue
                plan=followup_plan(decision)
                old=packet['source_search']
                assert old['source_token_budget']==4096
                pinned=[(s['doc_index'],s['start'],s['end']) for s in old['spans']]
                assert search.token_cost(pinned)==old['source_tokens']
                standard=list(factual_queries((20,)))
                adaptive=list(dict.fromkeys(standard+plan['notice_search']['queries']))
                for arm, queries in zip(freeze['controls'],(standard,adaptive)):
                    result=search.search((20,),method='hybrid',token_budget=8192,
                        queries=queries,required_ranges=pinned,selection_policy='rrf',expand_context=True)
                    for di,a,b in pinned:
                        assert any(s['doc_index']==di and s['start']<=a and s['end']>=b for s in result['spans'])
                    case=cases[row['case']]
                    hits={e['evidence_id']:any(s['doc_index']==e['doc_index'] and s['start']<=e['start']
                        and s['end']>=e['end'] for s in result['spans']) for e in case['required_bundle']}
                    measurement={'request_key':row['request_key'],'case':row['case'],'record_id':rid,
                        'first_arm':row['arm'],'arm':arm,'evidence_hits':hits,
                        'any_anchor':any(hits.values()),'complete_bundle':all(hits.values()),
                        'first_source_tokens':old['source_tokens'],'cumulative_unique_source_tokens':result['source_tokens'],
                        'prospective_total_source_tokens_served':old['source_tokens']+result['source_tokens'],
                        'coverage':result['coverage'],'second_model_read_executed':False}
                    measurements.append(measurement)
                    stream.write(json.dumps({'measurement':measurement,'followup_plan':plan,'retrieval':result},ensure_ascii=False)+'\n')
            stream.flush()
            print(json.dumps({'record_id':rid,'measurements':len(measurements),'seconds':time.monotonic()-started}),flush=True)
    aggregates=[]
    for first in sorted({r['first_arm'] for r in measurements}):
        for arm in freeze['controls']:
            part=[m for m in measurements if m['first_arm']==first and m['arm']==arm]
            aggregates.append({'first_arm':first,'arm':arm,'cases':len(part),
                'any_anchor':sum(m['any_anchor'] for m in part),'complete_bundle':sum(m['complete_bundle'] for m in part),
                'quotes':sum(sum(m['evidence_hits'].values()) for m in part),
                'mean_cumulative_unique_tokens':statistics.mean(m['cumulative_unique_source_tokens'] for m in part),
                'mean_prospective_served_tokens':statistics.mean(m['prospective_total_source_tokens_served'] for m in part)})
    assert freeze['source_sha256']==source_manifest()
    report={'aggregates':aggregates,'skipped':skipped,'cache':cache,'measurements':measurements,
        'encoder':encoder.receipt,'seconds':time.monotonic()-started,'new_gemma_calls':0,
        'official_macro_f1':None,'full160_f1':None,'second_read_executed':False,
        'meaning':'CPU follow-up search using already observed current-notice relations. Both arms pin the same first context under the same cumulative cap; no final inference effect measured.'}
    save(args.output/'report.json',report)
    print(json.dumps({'aggregates':aggregates,'skipped':skipped,'seconds':report['seconds']},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
