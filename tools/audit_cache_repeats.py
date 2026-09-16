"""Measure declared same-input native repeats without replacing any answer."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.runtime import Journal,sha256
from tools.run_runtime_revival import rows,read,verify_prepared
from tools.prepare_runtime_context import native_identity

def audit(prepared,run,consumed,verification,output):
    freeze=verify_prepared(prepared)
    checked=read(verification/'report.json')
    if checked['status']!='PASS' or checked['prepared_sha256']!=sha256(prepared/'input_freeze.json'):
        raise ValueError('Native recovery must be verified first')
    ledger=read(consumed/'prediction_freeze.json')
    if ledger['prepared_sha256']!=sha256(prepared/'input_freeze.json') or ledger['source_sha256']!=freeze['source_sha256']:
        raise ValueError('Use original source consumption for this repeat observation')
    packets={p['request_key']:p for name in ('primary_packets.jsonl.gz','probe_packets.jsonl.gz') for p in rows(prepared/name)}
    chosen={r['request_key']:r for part in ('normal','probes') for r in rows(run/part/'resolved_responses.jsonl.gz')}
    native={}
    for part in ('normal','probes'):
        for path in (run/part).glob('call_*_native.jsonl.gz'):
            for r in rows(path): native[r['request_key'],r['attempt']]=r['native']
    consumed_rows={r['request_key']:r['row'] for r in rows(consumed/'consumed.jsonl.gz')}
    recipe_keys={k for keys in read(prepared/'recipes.json').values() for k in keys}
    observations=[]
    for key,packet in packets.items():
        if packet.get('probe_role') not in ('cache2','cache32'): continue
        parent=packet['normal_parent']
        if key in recipe_keys or parent not in recipe_keys or native_identity(packet)!=native_identity(packets[parent]):
            raise ValueError('A repeat is selected for quality or does not match its normal input')
        if key not in chosen or parent not in chosen:
            observations.append({'request_key':key,'normal_parent':parent,'role':packet['probe_role'],'status':'unresolved_no_comparison'})
            continue
        a,b=chosen[parent],chosen[key]
        old,new=native[parent,a['attempt']],native[key,b['attempt']]
        left,right=old['output_token_ids'],new['output_token_ids']
        prefix=next((i for i,(x,y) in enumerate(zip(left,right)) if x!=y),min(len(left),len(right)))
        old_object,new_object=json.loads(a['response']['text']),json.loads(b['response']['text'])
        changes=[f'v{i}' for i in packet['items'] if consumed_rows[parent][f'v{i}']!=consumed_rows[key][f'v{i}']]
        observations.append({'request_key':key,'normal_parent':parent,'role':packet['probe_role'],'status':'complete',
            'record_id':packet['record_id'],'profile':packet['batch'],'same_native_input':True,
            'normal_attempt':a['attempt'],'repeat_attempt':b['attempt'],
            'same_raw_text':old['raw_text']==new['raw_text'],'same_output_token_ids':left==right,
            'common_output_token_prefix':prefix,'normal_output_tokens':len(left),'repeat_output_tokens':len(right),
            'same_final_object':old_object==new_object,
            'same_raw_judgments':old_object['judgments']['v']==new_object['judgments']['v'],
            'same_evidence_refs':old_object['judgments']['e']==new_object['judgments']['e'],
            'same_fact_claims':old_object['facts']==new_object['facts'],
            'changed_cpu_items':changes,'normal_cached_input_tokens':a['response']['cached_input_tokens'],
            'repeat_cached_input_tokens':b['response']['cached_input_tokens']})
    groups=defaultdict(list)
    for entry in observations: groups[entry['role']].append(entry)
    summary={role:{'planned':len(group),'complete':sum(r['status']=='complete' for r in group),
        **{key:sum(r.get(key) is True for r in group) for key in ('same_raw_text','same_output_token_ids','same_final_object',
            'same_raw_judgments','same_evidence_refs','same_fact_claims')},
        'changed_cpu_bits':sum(len(r.get('changed_cpu_items',[])) for r in group)} for role,group in groups.items()}
    journal=Journal(output);journal.save('observations.json',observations)
    report={'groups':summary,'prepared_sha256':sha256(prepared/'input_freeze.json'),
        'native_verification_sha256':sha256(verification/'report.json'),
        'original_consumption_sha256':sha256(consumed/'prediction_freeze.json'),
        'repeats_never_selected_in_recipes':True,'labels_read':False,'new_model_calls':0,
        'interpretation':'Observed numerical/call-history variation on unchanged inputs in one engine. Group2 and group32 also differ in call history; this does not certify cross-device determinism or isolate batch size as the only cause.'}
    journal.save('report.json',report);return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('prepared','run','consumed','verification','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();print(json.dumps(audit(a.prepared,a.run,a.consumed,a.verification,a.output),ensure_ascii=False,indent=2))
