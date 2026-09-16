"""Attribute final development errors to raw bits or deterministic consumption.

Reads a complete recovered ledger, never creates replacement predictions.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.b4_entry import restored
from submission.pps.pipeline import parse_output
from tools.evaluate import evaluate


def rows(path):
    with gzip.open(path,'rt',encoding='utf-8') as stream:
        return list(map(json.loads,stream))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--labels',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): raise ValueError('Use a new output file')
    metrics=evaluate(args.labels,args.run/'submission.csv')
    recs={r['id']:r for r in rows(args.run/'current_inputs.jsonl.gz')}
    selected=rows(args.run/'resolved_responses.jsonl.gz')
    assert len(selected)==len(recs)*4 and len({r['request_key'] for r in selected})==len(selected)
    raw, facts, refs, attempts={}, {}, {}, {}
    for record in sorted(selected,key=lambda r:r['packet']['family']):
        p,r=record['packet'],record['response']
        if p['generation']['response_format']=='software_facts':
            raise ValueError('Typed fact output has no raw model bit; do not invent one')
        items=tuple(p['items'])
        v,e=parse_output(r['text'],restored(p)['spans'],items,rec=recs[p['record_id']])
        obj=json.loads(r['text'])
        for k in ((20,) if p['family']=='L' else items):
            key=(p['record_id'],f'v{k}')
            raw[key]=v[k-1]
            facts[key]=obj.get('facts',{})
            refs[key]=e[k-1]
            attempts[key]=record['attempt']
    assert len(raw)==len(recs)*24
    errors=[]
    for e in metrics['errors']:
        key=(e['id'],e['item'])
        errors.append({**e,'raw':raw[key],'introduced_by_consumer':raw[key]==e['truth'],
            'model_facts':facts[key],'raw_evidence':refs[key],'attempt':attempts[key]})
    report={'errors':errors,'records':len(recs),'raw_model_bits':len(raw),
        'residual_errors':len(errors),'already_wrong_in_raw':sum(not e['introduced_by_consumer'] for e in errors),
        'introduced_by_consumer':sum(e['introduced_by_consumer'] for e in errors),
        'labels_sha256':hashlib.sha256(args.labels.read_bytes()).hexdigest(),
        'selected_ledger_sha256':hashlib.sha256((args.run/'resolved_responses.jsonl.gz').read_bytes()).hexdigest(),
        'new_model_calls':0,'interpretation':'Attribution within one observed response population, not proof of the cause of stochastic drift.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream: json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in report.items() if k!='errors'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
