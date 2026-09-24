"""Isolate contextual queries and candidate selection under frozen source seeds."""
import argparse
import csv
import gzip
import itertools
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.pps.catalog_candidates import CatalogCandidates
from submission.pps.notice_search import NoticeSearch
from submission.pps.products import CODE
from submission.pps.purchase_reading import catalog_source_queries,read_purchase,seed_reading
from submission.runtime import source_manifest
from tools.audit_purchase_feedback import annotate,sha,save
from tools.build_submission import build


class WithinNoticeEncoder:
    """Reuse only this notice's identical batches; discard at the next notice."""
    def __init__(self,encoder):self.encoder,self.cache=encoder,{}
    def encode(self,texts):
        key=tuple(texts)
        if key not in self.cache:self.cache[key]=self.encoder.encode(texts)
        return self.cache[key].copy()


def main():
    p=argparse.ArgumentParser()
    for key in ('input','review','previous','static-catalog','output'):
        p.add_argument('--'+key,type=Path,required=True)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    started=time.monotonic();source=source_manifest();snapshot=build(args.output/'source_snapshot.zip')
    previous=json.loads((args.previous/'preregistered.json').read_text(encoding='utf8'))
    assert previous['input_sha256']==sha(args.input) and previous['review_sha256']==sha(args.review)
    assert previous['source_manifest']['pps/notice_search.py']==source['pps/notice_search.py']
    cases={r['id']:r for r in json.loads(args.review.read_text(encoding='utf8'))['cases']}
    with gzip.open(args.input,'rt',encoding='utf8') as f:recs=[r for r in map(json.loads,f) if r['id'] in cases]
    assert len(recs)==len(cases)
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer',local_files_only=True,trust_remote_code=False)
    seeds=previous['source_seeds'];queries={}
    for rec in recs:
        seed=seeds[rec['id']]['reading']
        assert json.loads(json.dumps(seed_reading(NoticeSearch(rec,tokenizer),token_budget=4096)))==seed
        queries[rec['id']]={q:catalog_source_queries(rec,seed,query_policy=q) for q in ('units','context_blocks')}
        assert queries[rec['id']]['units']==seeds[rec['id']]['catalog_queries']
    catalog_path=ROOT/'data_open/data/법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv'
    with catalog_path.open(encoding='utf-8-sig',newline='') as f:index=CatalogCandidates(list(csv.DictReader(f)))
    static=json.loads((args.static_catalog/'report.json').read_text(encoding='utf8'))
    stored=json.loads((args.static_catalog/'static_catalog_rows.json').read_text(encoding='utf8'))
    assert index.rows==stored['rows'] and index.catalog_sha256==stored['catalog_sha256']
    assert sha(args.static_catalog/'static_catalog_vectors.npy')==static['static_vectors_sha256']
    arms=[(q,c,m) for q,c,m in itertools.product(('units','context_blocks'),('facility','rank_frontier'),('lexical','hybrid'))
          if (q,c)!=('units','facility')]
    cached=[]
    for row in json.loads((args.previous/'results.json').read_text(encoding='utf8')):
        if row['arm'] in {'seed/lexical/hybrid','seed/hybrid/hybrid'}:
            cached.append({**row,'arm':'units/facility/'+row['diagnostics']['purchase_feedback']['catalog_method'],
                           'cached_from':str(args.previous/'results.json')})
    assert len(cached)==len(recs)*2
    save(args.output/'preregistered.json',{'source_manifest':source,'source_fingerprint':snapshot['source_fingerprint'],
        'tool_sha256':sha(__file__),'input_sha256':sha(args.input),'review_sha256':sha(args.review),
        'previous_protocol_sha256':sha(args.previous/'preregistered.json'),
        'cached_results_sha256':sha(args.previous/'results.json'),'cached_controls':len(cached),'new_arms':arms,
        'source_queries':queries,'source_seeds':seeds,'source_token_budget':4096,'static_catalog_token_budget':2048,
        'source_method':'hybrid','source_selection_policy':'rrf','candidate_frontier_depth':3,
        'catalog_file_sha256':sha(catalog_path),'static_vectors_sha256':static['static_vectors_sha256'],
        'hypothesis':'Adjacent source context removes fragment votes; rank frontier removes the preference for cheap rank-tail rows.',
        'classification_labels_read':False,'holdout_read':False,'new_gemma_calls':0,'gpu_used':False,
        'same_original_seed_for_all_arms':True,'all_seed_source_remains_in_final_original_token_budget':True})
    import numpy as np
    from submission.pps.embeddings import BGEDenseEncoder
    encoder=BGEDenseEncoder(ROOT/'models/bge-m3')
    assert encoder.receipt['required_revision']==static['encoder']['required_revision']
    index._vectors=np.load(args.static_catalog/'static_catalog_vectors.npy',allow_pickle=False)
    results=[]
    for n,rec in enumerate(recs):
        scoped=WithinNoticeEncoder(encoder);index.encoder=scoped
        tool=NoticeSearch(rec,tokenizer,scoped)
        for q,c,m in arms[n%len(arms):]+arms[:n%len(arms)]:
            began=time.monotonic()
            result=read_purchase(tool,index,token_budget=4096,catalog_method=m,source_method='hybrid',
                seed=seeds[rec['id']]['reading'],query_policy=q,candidate_policy=c)
            result.update(arm=f'{q}/{c}/{m}',seconds=time.monotonic()-began)
            annotate(result,cases[rec['id']]);results.append(result)
        index.encoder=None
        save(args.output/(rec['id']+'.json'),{'results':results[-len(arms):]})
        print(json.dumps({'notices':n+1,'total':len(recs),'seconds':round(time.monotonic()-started,2)}),flush=True)
    assert source_manifest()==source
    save(args.output/'results.json',results)
    summary=[]
    for arm in sorted({r['arm'] for r in results+cached}):
        group=[r for r in results+cached if r['arm']==arm]
        summary.append({'arm':arm,'notices':len(group),
            'related_sentence_hits':sum(r['review']['related_sentence_found'] for r in group),
            'known_required_bundle_hits':sum(r['review']['known_required_bundle_found'] for r in group),
            'full_decision_bundle_hits':sum(r['review']['full_decision_bundle_found'] for r in group),
            'source_tokens':sum(r['source_tokens'] for r in group),
            'catalog_candidates':sum(len(r['diagnostics']['purchase_feedback']['catalog']['candidates']) for r in group),
            'catalog_tokens':sum(r['diagnostics']['purchase_feedback']['static_catalog_tokens'] for r in group)})
    save(args.output/'report.json',{'source_manifest':source,'summary':summary,'new_comparisons':len(results),
        'cached_comparisons':len(cached),'seconds':time.monotonic()-started,'encoder':encoder.receipt,
        'original_source_budget_includes_intermediate_reading':True,'official_gold':False,
        'final_F1_measured':False,'gpu_used':False,'new_gemma_calls':0})
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
