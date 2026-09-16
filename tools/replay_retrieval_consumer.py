"""Replay every fixed retrieval arm through a changed CPU consumer.

This measures consumption of stored responses, never the effect of new inputs.
The original audited run, including repeated responses, remains immutable.
"""
import argparse
import copy
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.b4_entry import B4Pipeline, parse_error
from submission.pps.data import read_csv, write_csv
from submission.runtime import source_manifest
from tools.score_catalog_scope import rows, sha, verified_replay_path
from tools.score_retrieval_contrast import join_items
from tools.evaluate import compare


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('run','original-audit','baseline-run','baseline-replay','labels','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--baseline',type=Path,action='append',default=[])
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    def read(p):return json.loads(p.read_text(encoding='utf8'))
    def save(name,obj):
        with (args.output/name).open('x',encoding='utf8') as stream:
            json.dump(obj,stream,ensure_ascii=False,indent=2)
    folder=args.run/'contrast_output';code=source_manifest()
    audit=read(args.original_audit);freeze=read(folder/'input_freeze.json')
    assert audit['cpu_consumer_reproduced'] and audit['source_sha256']==freeze['source_sha256']
    packets=rows(folder/'current_packets.jsonl.gz');original=rows(folder/'contrast_results.jsonl.gz')
    recs={r['id']:r for r in rows(folder/'current_inputs.jsonl.gz')}
    by_key={r['request_key']:r for r in original}
    native={(r['request_key'],r['attempt']):r['native']
            for p in sorted(folder.glob('call_*_native.jsonl.gz')) for r in rows(p)}
    assert len(by_key)==len(original)==len(packets)==freeze['primary_requests']
    assert set(by_key)=={p['request_key'] for p in packets}
    paths=[folder/n for n in ('current_inputs.jsonl.gz','current_packets.jsonl.gz',
                             'contrast_results.jsonl.gz','input_freeze.json')]
    paths+=sorted(folder.glob('call_*_native.jsonl.gz'))+[args.original_audit]
    input_hashes={str(p):sha(p) for p in paths}
    pipeline=B4Pipeline(ROOT/'data_open/data',None,input_strategy='audited')
    observations=[];changed=[]
    for p in packets:
        r=by_key[p['request_key']];n=native[p['request_key'],r['attempt']]
        assert n['prompt_token_ids']==p['token_ids']
        assert hashlib.sha256(n['raw_text'].encode()).hexdigest()==r['response']['raw_output_sha256']
        assert parse_error(p,r['response']) is r['parse_error'] is None
        row,details=pipeline.consume(recs[p['record_id']],p,r['response'])
        new=copy.deepcopy(r);new.update(row=row,details=details)
        observations.append(new)
        difference=[i for i in p['items'] if row[f'v{i}']!=r['row'][f'v{i}']]
        if difference:
            changed.append(dict(request_key=p['request_key'],record_id=p['record_id'],arm=p['arm'],
                items=difference,before=r['row'],after=row,details=details))
    with gzip.open(args.output/'decisions.jsonl.gz','wt',encoding='utf8') as stream:
        for row in observations:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    baseline_path=verified_replay_path(args.baseline_run,args.baseline_replay,code)
    baseline=read_csv(baseline_path);all_records=rows(args.baseline_run/'current_inputs.jsonl.gz')
    all_by_id={r['id']:r for r in all_records}
    assert all(all_by_id[rid]==record for rid,record in recs.items())
    joins=[]
    for arm in freeze['arms']:
        chosen_packets=[p for p in packets if p['arm']==arm]
        chosen_observations=[r for r in observations if r['arm']==arm]
        joined,receipt=join_items(baseline,chosen_packets,chosen_observations)
        assert joined is not None
        path=args.output/(arm+'.csv')
        write_csv(path,joined,recs=all_records,require_positive_evidence=pipeline.config.require_positive_evidence)
        joins.append(dict(arm=arm,prediction=path.name,sha256=sha(path),join=receipt))
    assert source_manifest()==code and all(sha(Path(p))==h for p,h in input_hashes.items())
    save('prediction_freeze.json',dict(source_sha256=code,original_source_sha256=freeze['source_sha256'],
        input_sha256=input_hashes,predictions=joins,model_calls=0,labels_read=False,
        original_response_count=len(original),replayed_response_count=len(observations)))
    # All declared arm predictions are frozen before reading the exposed labels.
    measurements=[]
    for item in joins:
        metrics=compare(args.labels,args.output/item['prediction'],args.baseline)
        save(item['arm']+'_metrics.json',metrics)
        candidate=metrics['candidate']
        measurements.append(dict(arm=item['arm'],macro_f1=candidate['macro_f1'],
            fp=sum(r['fp'] for r in candidate['per_item'].values()),
            fn=sum(r['fn'] for r in candidate['per_item'].values()),prediction=item['prediction'],
            prediction_sha256=item['sha256']))
    save('report.json',dict(kind='saved_retrieval_response_current_consumer_replay',new_model_calls=0,
        original_run=str(args.run),measurements=measurements,changed=changed,source_sha256=code,
        original_responses_unchanged=True,all_repeats_replayed_without_selection=True,
        full_fresh_inference=False,official_score=None,
        limitation='Current CPU consumption on all fixed prior arms and current saved baseline. No changed retrieval input was inferred anew.'))
    print(json.dumps(measurements,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
