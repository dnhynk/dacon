"""Recheck stale consumer barriers without changing raw answers or reading labels."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.b4_entry import B4Pipeline,parse_error
from submission.runtime import Journal,source_manifest


def rows(path):
    with gzip.open(path,'rt',encoding='utf8') as f: return list(map(json.loads,f))


def audit(run,output):
    journal=Journal(output); code=source_manifest(); started=time.monotonic()
    paths={name:run/name for name in ('current_inputs.jsonl.gz','current_packets.jsonl.gz','contrast_results.jsonl.gz')}
    hashes={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in paths.items()}
    recs={r['id']:r for r in rows(paths['current_inputs.jsonl.gz'])}
    packets=rows(paths['current_packets.jsonl.gz'])
    old={r['request_key']:r for r in rows(paths['contrast_results.jsonl.gz'])}
    if set(old)!={p['request_key'] for p in packets}: raise ValueError('Incomplete saved population')
    pipe=B4Pipeline(ROOT/'data_open/data',None)
    results=[]; counts=Counter(); changed=[]
    for packet in packets:
        key=packet['request_key']; prior=old[key]; response=prior['response']
        error=parse_error(packet,response); row=detail=None
        if error is None: row,detail=pipe.consume(recs[packet['record_id']],packet,response)
        arms=packet.get('arm',packet['generation']['response_format'])
        counts[(arms,'all')]+=1
        counts[(arms,'invalid' if error else 'valid')]+=1
        delta=[]
        if row is not None and prior['row'] is not None:
            delta=[{'item':f'v{i}','before':prior['row'][f'v{i}'],'after':row[f'v{i}']}
                   for i in packet['items'] if f'v{i}' in row and row[f'v{i}']!=prior['row'][f'v{i}']]
        result={'request_key':key,'record_id':packet['record_id'],'arm':arms,'attempt':prior['attempt'],
            'response':response,'previous_error':prior['parse_error'],'parse_error':error,
            'previous_row':prior['row'],'row':row,'details':detail,'bit_changes':delta}
        results.append(result)
        if delta: changed.append({k:result[k] for k in ('request_key','record_id','arm','bit_changes')})
    assert code==source_manifest()
    assert hashes=={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in paths.items()}
    journal.rows('current_consumption.jsonl.gz',results)
    report={'kind':'all_stored_final_responses_current_cpu_replay','source_sha256':code,'input_hashes':hashes,
        'records':len(recs),'requests':len(packets),'arms':{arm:{k:v for (a,k),v in counts.items() if a==arm}
            for arm in sorted({a for a,k in counts})},'changed':changed,'seconds':time.monotonic()-started,
        'labels_read':False,'new_model_calls':0,'quality_rerolls':0,'old_results_preserved':True,
        'input_change_effect_measured':False}
    journal.save('report.json',report)
    return {k:v for k,v in report.items() if k not in {'source_sha256','input_hashes'}}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();print(json.dumps(audit(args.run,args.output),ensure_ascii=False,indent=2))
