"""Compare restored fact groups with frozen purchase-feedback inputs on CPU."""
import argparse
import copy
import csv
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.pps.catalog_candidates import CatalogCandidates
from submission.pps.notice_search import NoticeSearch
from submission.pps.purchase_reading import read_purchase
from submission.runtime import source_manifest
from tools.audit_purchase_feedback import annotate,save,sha
from tools.audit_purchase_frontier import WithinNoticeEncoder
from tools.build_submission import build
from tools.score_catalog_scope import rows


def covered(result, reference):
    return any(s['doc_index']==reference['doc_index'] and s['start']<=reference['start']
               and reference['end']<=s['end'] for s in result['spans'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('run','review','seeds','observations','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    folder=args.run/'contrast_output'
    inputs=rows(folder/'current_inputs.jsonl.gz')
    packets=rows(folder/'current_packets.jsonl.gz')
    cases={r['id']:r for r in json.loads(args.review.read_text(encoding='utf8'))['cases']}
    assert {r['id'] for r in inputs}==set(cases)
    seeds=json.loads(args.seeds.read_text(encoding='utf8'))['source_seeds']
    observations=json.loads(args.observations.read_text(encoding='utf8'))
    controls=[];catalogs={}
    for packet in packets:
        if packet['arm'] not in {'A_current','B_seed_lexical','C_catalog_feedback'}:continue
        result=copy.deepcopy(packet['source_search'])
        result.update(arm=packet['arm'],record_id=packet['record_id'],cached_from=packet['request_key'])
        annotate(result,cases[packet['record_id']]);controls.append(result)
        if packet['arm']=='C_catalog_feedback':
            catalogs[packet['record_id']]=result['diagnostics']['purchase_feedback']['catalog']
    assert len(controls)==3*len(inputs)
    source=source_manifest();snapshot=build(args.output/'source_snapshot.zip')
    save(args.output/'preregistered.json',dict(source_manifest=source,source_fingerprint=snapshot['source_fingerprint'],
        input_sha256=sha(folder/'current_inputs.jsonl.gz'),packet_sha256=sha(folder/'current_packets.jsonl.gz'),
        review_sha256=sha(args.review),seeds_sha256=sha(args.seeds),observations_sha256=sha(args.observations),
        tool_sha256=sha(__file__),source_budget=4096,static_catalog_budget=2048,
        new_arms=['D_fact_groups_lexical','E_fact_groups_feedback'],
        frozen_controls=['A_current','B_seed_lexical','C_catalog_feedback'],
        all_prior_seed_source_kept=True,all_source_selected_cases_kept=True,
        classification_labels_read=False,holdout_read=False,gpu_used=False,new_gemma_calls=0,
        hypothesis='Separate specification/eligibility/catalog rank budgets restore missing prerequisite questions without extra source tokens.',
        automatic_line_coverage_is_not_replacement_gold_or_absence_proof=True))
    from transformers import AutoTokenizer
    from submission.pps.embeddings import BGEDenseEncoder
    tokenizer=AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer',local_files_only=True,trust_remote_code=False)
    started=time.monotonic();encoder=BGEDenseEncoder(ROOT/'models/bge-m3')
    with (ROOT/'data_open/data/법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv').open(encoding='utf-8-sig',newline='') as stream:
        catalog=CatalogCandidates(list(csv.DictReader(stream)))
    results=[]
    for n,rec in enumerate(inputs):
        tool=NoticeSearch(rec,tokenizer,WithinNoticeEncoder(encoder));case=cases[rec['id']]
        seed=seeds[rec['id']]['reading']
        for name,method,cm in [('D_fact_groups_lexical','lexical',None),('E_fact_groups_feedback','hybrid','lexical')][::1 if n%2 else -1]:
            result=read_purchase(tool,catalog,token_budget=4096,seed=seed,source_method=method,
                catalog_method=cm,catalog_result=catalogs[rec['id']] if cm else None,
                query_policy='context_blocks',candidate_policy='rank_frontier',followup_policy='fact_groups')
            result.update(arm=name);annotate(result,case);results.append(result)
        save(args.output/(rec['id']+'.json'),{'results':results[-2:]})
        print(json.dumps(dict(notices=n+1,total=len(inputs),seconds=round(time.monotonic()-started,2))),flush=True)
    all_results=controls+results
    by_key={(r['record_id'],r['arm']):r for r in all_results}
    observed=[]
    for ref in observations:
        observed.append({**ref,'covered':{arm:covered(by_key[ref['id'],arm],ref)
            for arm in sorted({r['arm'] for r in all_results})}})
    summary=[]
    for arm in sorted({r['arm'] for r in all_results}):
        group=[r for r in all_results if r['arm']==arm]
        summary.append(dict(arm=arm,notices=len(group),
            related=sum(r['review']['related_sentence_found'] for r in group),
            known_bundle=sum(r['review']['known_required_bundle_found'] for r in group),
            full_bundle=sum(r['review']['full_decision_bundle_found'] for r in group),
            source_tokens=sum(r['source_tokens'] for r in group),
            automatic_source_lines={role:dict(covered=sum(r['covered'][arm] for r in observed if role in r['roles']),
                total=sum(role in r['roles'] for r in observed))
                for role in ('equivalence','direct_production','size_qualification')}))
    assert source_manifest()==source
    save(args.output/'results.json',results)
    save(args.output/'source_observations.json',observed)
    save(args.output/'report.json',dict(summary=summary,source_manifest=source,seconds=time.monotonic()-started,
        encoder=encoder.receipt,new_comparisons=len(results),cached_controls=len(controls),
        gpu_used=False,new_gemma_calls=0,final_F1_measured=False,official_score=None,default_promoted=False))
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
