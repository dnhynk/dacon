"""Factorial source/catalog feedback comparison; no new labels or Gemma calls."""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.pps.catalog_candidates import CatalogCandidates
from submission.pps.notice_search import NoticeSearch
from submission.pps.products import CODE
from submission.pps.purchase_reading import seed_reading,catalog_source_queries,read_purchase
from submission.runtime import source_manifest
from tools.audit_purchase_retrieval import included
from tools.build_submission import build

ARMS=[(None,'lexical'),(None,'hybrid'),('lexical','lexical'),('lexical','hybrid'),
      ('hybrid','lexical'),('hybrid','hybrid')]


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,data):
    with path.open('x',encoding='utf8') as f:json.dump(data,f,ensure_ascii=False,indent=2)


def annotate(result,case):
    known=bool(case['required_bundle']) and all(included(result,r) for r in case['required_bundle'])
    result['review']={'related_sentence_found':any(included(result,r) for r in case['related']),
        'known_required_bundle_found':known,'full_decision_bundle_found':known and case['full_decision_bundle_obtainable'],
        'full_decision_bundle_obtainable':case['full_decision_bundle_obtainable'],
        'missing_or_uncertain_scope':case['missing_or_uncertain_scope'],'provenance':case['provenance']}


def main():
    p=argparse.ArgumentParser()
    for key in ('input','review','baseline','static-catalog','output'):
        p.add_argument('--'+key,type=Path,required=True)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    source,started=source_manifest(),time.monotonic()
    snapshot=build(args.output/'source_snapshot.zip')
    cases={r['id']:r for r in json.loads(args.review.read_text(encoding='utf8'))['cases']}
    with gzip.open(args.input,'rt',encoding='utf8') as f:
        recs=[r for r in map(json.loads,f) if r['id'] in cases]
    assert len(recs)==len(cases)
    old=json.loads((args.baseline/'results.json').read_text(encoding='utf8'))
    baseline=[r for r in old if r['arm']=='current/local/rrf' and r['source_token_budget']==4096]
    old_protocol=json.loads((args.baseline/'preregistered.json').read_text(encoding='utf8'))
    assert old_protocol['input_sha256']==sha(args.input) and old_protocol['review_sha256']==sha(args.review)
    assert {r['record_id'] for r in baseline}==set(cases)
    for name in ('pps/retrieval.py','pps/notice_search.py'):
        assert old_protocol['source_manifest'][name]==source[name]
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer',local_files_only=True,trust_remote_code=False)
    seeds={}
    for rec in recs:
        case=cases[rec['id']]
        assert [hashlib.sha256(d['text'].encode()).hexdigest() for d in rec['docs']]==case['doc_sha256']
        for ref in case['related']+case['required_bundle']:
            assert rec['docs'][ref['doc_index']]['text'][ref['start']:ref['end']]==ref['quote']
        seed=seed_reading(NoticeSearch(rec,tokenizer),token_budget=4096)
        seeds[rec['id']]={'reading':seed,'catalog_queries':catalog_source_queries(rec,seed)}
    catalog_path=ROOT/'data_open/data/법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv'
    with catalog_path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    index=CatalogCandidates(rows)
    stored_rows=json.loads((args.static_catalog/'static_catalog_rows.json').read_text(encoding='utf8'))
    static_report=json.loads((args.static_catalog/'report.json').read_text(encoding='utf8'))
    assert index.rows==stored_rows['rows'] and index.catalog_sha256==stored_rows['catalog_sha256']
    assert sha(args.static_catalog/'static_catalog_vectors.npy')==static_report['static_vectors_sha256']
    save(args.output/'preregistered.json',{'source_manifest':source,'source_snapshot':snapshot['source_fingerprint'],
        'tool_sha256':sha(__file__),'input_sha256':sha(args.input),'review_sha256':sha(args.review),
        'baseline_sha256':sha(args.baseline/'results.json'),'catalog_file_sha256':sha(catalog_path),
        'static_vectors_sha256':static_report['static_vectors_sha256'],'arms':ARMS,'seed_fraction':'one_third',
        'source_budget':4096,'static_catalog_budget':2048,'max_catalog_candidates':24,'max_source_queries':32,
        'source_seeds':seeds,'labels_read':False,'new_gemma_calls':0,'gpu_used':False,
        'comparison_question':'Does catalog feedback add decisive evidence beyond the same preserved source seed?',
        'all_intermediate_source_ranges_remain_in_the_final_budget':True,
        'static_catalog_only_cross_notice_cache':True})
    import numpy as np
    from submission.pps.embeddings import BGEDenseEncoder
    encoder=BGEDenseEncoder(ROOT/'models/bge-m3')
    assert encoder.receipt['model']==static_report['encoder']['model']
    assert encoder.receipt['required_revision']==static_report['encoder']['required_revision']
    index.encoder=encoder
    index._vectors=np.load(args.static_catalog/'static_catalog_vectors.npy',allow_pickle=False)
    results=[]
    for n,rec in enumerate(recs):
        tool=NoticeSearch(rec,tokenizer,encoder)
        seed=seeds[rec['id']]['reading']
        queries=seeds[rec['id']]['catalog_queries']
        matched={}
        # The catalog is queried once per method and notice. Its query vectors
        # and candidate outputs do not persist into another notice.
        for method in ('lexical','hybrid'):
            if queries:
                matched[method]=index.search([q['query'] for q in queries],tokenizer,token_budget=2048,method=method,
                    required_codes=sorted(set(CODE.findall(str(rec['meta'].get('세부품명번호목록') or '')))),max_candidates=24)
        arms=ARMS[n%len(ARMS):]+ARMS[:n%len(ARMS)]
        for catalog_method,source_method in arms:
            began=time.monotonic()
            result=read_purchase(tool,index,token_budget=4096,catalog_method=catalog_method,
                source_method=source_method,seed=seed,catalog_result=matched.get(catalog_method))
            result.update(arm=f"seed/{catalog_method or 'no_catalog'}/{source_method}",seconds=time.monotonic()-began)
            annotate(result,cases[rec['id']])
            results.append(result)
        save(args.output/(rec['id']+'.json'),{'seed':seed,'results':results[-len(ARMS):]})
        print(json.dumps({'notices':n+1,'total':len(recs),'seconds':round(time.monotonic()-started,2)}),flush=True)
    assert source_manifest()==source
    save(args.output/'results.json',results)
    summary=[]
    for name,group in [('current',baseline)]+[(f"seed/{c or 'no_catalog'}/{s}",
        [r for r in results if r['arm']==f"seed/{c or 'no_catalog'}/{s}"]) for c,s in ARMS]:
        summary.append({'arm':name,'notices':len(group),
            'related_sentence_hits':sum(r['review']['related_sentence_found'] for r in group),
            'known_required_bundle_hits':sum(r['review']['known_required_bundle_found'] for r in group),
            'full_decision_bundle_hits':sum(r['review']['full_decision_bundle_found'] for r in group),
            'source_tokens':sum(r['source_tokens'] for r in group)})
    report={'source_manifest':source,'comparisons':len(results),'cached_baseline_readings':len(baseline),
        'seconds':time.monotonic()-started,'encoder':encoder.receipt,'summary':summary,
        'final_F1_measured':False,'gpu_used':False,'new_gemma_calls':0,'official_gold':False,
        'cohort_exposure':'Previously exposed source-selected development notices; not unseen confirmation.',
        'original_source_budget_includes_intermediate_reading':True}
    save(args.output/'report.json',report)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
