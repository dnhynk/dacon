"""Freeze source money observations without labels or model calls."""
import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.pps.comparison import amount_facts
from submission.pps.prices import project_prices
from submission.runtime import source_manifest


def read_rows(path):
    with gzip.open(path,'rt',encoding='utf8') as stream:return list(map(json.loads,stream))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--baseline',type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    records=read_rows(args.input);code=source_manifest()
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    input_sha=digest(args.input)
    observations=[dict(id=r['id'],amount_facts=amount_facts(r),prices=project_prices(r)) for r in records]
    assert len({r['id'] for r in observations})==len(observations)
    report=dict(kind='source_monetary_observations',notices=len(records),new_model_calls=0,labels_read=False,
        source_sha256=code,input_sha256=input_sha,input=str(args.input),
        fact_scopes=dict(Counter(f['scope'] for r in observations for f in r['amount_facts'])),
        prices=dict(Counter(k+':'+v['status'] for r in observations for k,v in r['prices'].items())))
    if args.baseline:
        before=read_rows(args.baseline/'observations.jsonl.gz');index={r['id']:r for r in before}
        assert index.keys()=={r['id'] for r in observations}
        old=json.loads((args.baseline/'report.json').read_text(encoding='utf8'))
        assert old['input_sha256']==input_sha
        report['baseline']=str(args.baseline)
        report['changed_ids']=[r['id'] for r in observations if r!=index[r['id']]]
        report['fact_changed_ids']=[r['id'] for r in observations if r['amount_facts']!=index[r['id']]['amount_facts']]
        price_fields=('status','value_won','candidate_values_won','effective_source')
        report['applicability_changed_ids']=[r['id'] for r in observations if any(
            any(r['prices'][kind][key]!=index[r['id']]['prices'][kind][key] for key in price_fields)
            for kind in r['prices'])]
        report['value_changes']=[dict(id=r['id'],kind=k,before=index[r['id']]['prices'][k]['value_won'],
                                    after=p['value_won'],status=p['status'])
            for r in observations for k,p in r['prices'].items()
            if (index[r['id']]['prices'][k]['value_won'],index[r['id']]['prices'][k]['status'])!=(p['value_won'],p['status'])]
    with gzip.open(args.output/'observations.jsonl.gz','wt',encoding='utf8') as stream:
        for row in observations:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    assert source_manifest()==code and digest(args.input)==input_sha
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    summary={k:v for k,v in report.items() if k not in {'source_sha256','changed_ids'}}
    if 'changed_ids' in report:summary['structurally_changed_count']=len(report['changed_ids'])
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
