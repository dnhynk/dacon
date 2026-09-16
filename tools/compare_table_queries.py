"""Equal-source-budget table-candidate queries against a fixed catalog/source reader."""
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
from submission.runtime import source_manifest
from submission.pps.catalog_candidates import CatalogCandidates
from submission.pps.notice_search import NoticeSearch
from submission.pps.products import CODE
from submission.pps.purchase_reading import seed_reading,catalog_source_queries,read_purchase
from tools.audit_purchase_feedback import annotate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('input','review','static-catalog','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    def read(p):return json.loads(p.read_text(encoding='utf8'))
    def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    def save(name,value):
        with (args.output/name).open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
    code=source_manifest();started=time.monotonic()
    cases={c['id']:c for c in read(args.review)['cases']}
    with gzip.open(args.input,'rt',encoding='utf8') as f:records=[r for r in map(json.loads,f) if r['id'] in cases]
    assert len(records)==len(cases)
    catalog_path=ROOT/'data_open/data/법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv'
    with catalog_path.open(encoding='utf-8-sig',newline='') as f:catalog=CatalogCandidates(list(csv.DictReader(f)))
    old=read(args.static_catalog/'report.json');stored=read(args.static_catalog/'static_catalog_rows.json')
    assert catalog.rows==stored['rows'] and catalog.catalog_sha256==stored['catalog_sha256']
    vector_path=args.static_catalog/'static_catalog_vectors.npy'
    assert sha(vector_path)==old['static_vectors_sha256']
    hashes={str(p):sha(p) for p in (args.input,args.review,catalog_path,vector_path)}
    arms=[(policy,method) for policy in ('units','table_candidates') for method in ('lexical','hybrid')]
    save('preregistered.json',dict(source_sha256=code,input_sha256=hashes,arms=arms,
        source_budget=4096,source_method='lexical',candidate_policy='rank_frontier',followup_policy='fact_groups',
        source_seed_fraction='one_third',catalog_budget=2048,max_queries=32,
        labels_read=False,new_gemma_calls=0,gpu_used=False,static_vectors_reused=True,
        question='Does source structure improve catalog candidates and decisive source coverage under the same budget?'))
    import numpy as np
    from transformers import AutoTokenizer
    from submission.pps.embeddings import BGEDenseEncoder
    tokenizer=AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer',local_files_only=True,trust_remote_code=False)
    encoder=BGEDenseEncoder(ROOT/'models/bge-m3')
    assert encoder.receipt['required_revision']==old['encoder']['required_revision']
    assert encoder.receipt['local_provenance']==old['encoder']['local_provenance']
    catalog.encoder=encoder;catalog._vectors=np.load(vector_path,allow_pickle=False)
    results=[];query_counts=[]
    for n,record in enumerate(records):
        case=cases[record['id']]
        assert [hashlib.sha256(d['text'].encode()).hexdigest() for d in record['docs']]==case['doc_sha256']
        for ref in case['related']+case['required_bundle']:
            assert record['docs'][ref['doc_index']]['text'][ref['start']:ref['end']]==ref['quote']
        tool=NoticeSearch(record,tokenizer)
        seed=seed_reading(tool,token_budget=4096)
        outputs={}
        required=sorted(set(CODE.findall(str(record.get('meta',{}).get('세부품명번호목록') or ''))))
        for policy,method in arms:
            queries=catalog_source_queries(record,seed,query_policy=policy)
            query_counts.append(dict(id=record['id'],policy=policy,method=method,count=len(queries),
                structure_queries=sum('structure' in q for q in queries),queries=queries))
            catalog_result=None
            if queries:
                key=(method,tuple(q['query'] for q in queries))
                if key not in outputs:outputs[key]=catalog.search([q['query'] for q in queries],tokenizer,
                    token_budget=2048,method=method,required_codes=required,max_candidates=24,selection_policy='rank_frontier')
                catalog_result=outputs[key]
            result=read_purchase(tool,catalog,token_budget=4096,catalog_method=method,source_method='lexical',
                seed=seed,catalog_result=catalog_result,query_policy=policy,candidate_policy='rank_frontier',followup_policy='fact_groups')
            result['arm']=policy+'/'+method;annotate(result,case);results.append(result)
        print(json.dumps({'notices':n+1,'total':len(records),'seconds':round(time.monotonic()-started,2)}),flush=True)
    summary=[]
    for policy,method in arms:
        selected=[r for r in results if r['arm']==policy+'/'+method]
        summary.append(dict(arm=policy+'/'+method,notices=len(selected),
            related_sentences=sum(r['review']['related_sentence_found'] for r in selected),
            full_bundles=sum(r['review']['full_decision_bundle_found'] for r in selected),
            known_bundles=sum(r['review']['known_required_bundle_found'] for r in selected),
            source_tokens=sum(r['source_tokens'] for r in selected)))
    save('results.json',results);save('queries.json',query_counts)
    assert source_manifest()==code and all(sha(Path(p))==v for p,v in hashes.items())
    save('report.json',dict(source_sha256=code,summary=summary,encoder=encoder.receipt,
        seconds=time.monotonic()-started,labels_read=False,new_gemma_calls=0,gpu_used=False,
        limitation='Source/candidate retrieval comparison only; not a new Gemma result or F1 improvement.'))
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
