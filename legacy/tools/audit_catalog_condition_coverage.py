"""Inventory supplied designation conditions and source-only unresolved purchases."""
import argparse
from collections import Counter
import csv
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.pps.knowledge import Knowledge
from submission.pps.qualification import catalog_condition
from submission.runtime import source_manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--data-dir',type=Path,default=ROOT/'data_open/data')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    code=source_manifest()
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    catalog_path=args.data_dir/'법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv'
    hashes={str(p):sha(p) for p in (args.input,catalog_path)}
    with catalog_path.open(encoding='utf-8-sig',newline='') as stream:catalog=list(csv.DictReader(stream))
    static=[dict(row_id=i+1,code=r['세부품명번호'],name=r['세부품명'],note=r['특이사항'],
                 condition=catalog_condition(r['특이사항'],None,None)) for i,r in enumerate(catalog)]
    with gzip.open(args.input,'rt',encoding='utf8') as stream:records=list(map(json.loads,stream))
    assert len({r['id'] for r in records})==len(records)
    knowledge=Knowledge(args.data_dir);observations=[]
    for record in records:
        _,facts=knowledge.qualification_decisions(record,{f'v{i}':'0' for i in range(10,19)})
        product=facts['product']
        observations.append(dict(id=record['id'],product=product))
    unresolved=[]; needs_facts=[]
    for row in static:
        if row['condition']['status'] not in ('not_evaluated','unknown'):continue
        active=[]
        for obs in observations:
            matched=[r for r in obs['product']['products'] if r['code']==row['code']]
            if matched:active.append(dict(id=obs['id'],purchase_status=obs['product']['status'],
                mechanism=obs['product']['mechanism'],uncertainty=obs['product']['uncertainty'],
                source_condition=matched[0]['condition']))
        if active:
            target=unresolved if row['condition']['status']=='not_evaluated' else needs_facts
            target.append(dict(**row,notices=active))
    report=dict(kind='source_only_catalog_condition_coverage',labels_read=False,new_model_calls=0,
        source_sha256=code,input_sha256=hashes,records=len(records),catalog_rows=len(catalog),
        coded_rows=sum(bool(r['code']) for r in static),
        static_status_counts=dict(Counter(r['condition']['status'] for r in static)),
        source_purchase_status_counts=dict(Counter(r['product']['status'] for r in observations)),
        unresolved_active_rows=unresolved,
        recognized_active_rows_requiring_facts=needs_facts,
        source_condition_status_counts=dict(Counter(p['condition']['status'] for r in observations for p in r['product']['products'])),
        limitations='Source discovery can be incomplete; retrieval relevance is not purchase identity or designation truth.')
    for name,rows in (('catalog',static),('observations',observations)):
        with gzip.open(args.output/(name+'.jsonl.gz'),'wt',encoding='utf8') as stream:
            for row in rows:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    assert source_manifest()==code and all(sha(Path(p))==h for p,h in hashes.items())
    with (args.output/'report.json').open('x',encoding='utf8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in report.items() if k not in {'source_sha256','input_sha256'}},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
