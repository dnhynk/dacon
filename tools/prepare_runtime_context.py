"""Freeze a normal context/cache candidate and whole-cohort fixed source probes.

Only canonical B4Pipeline makes prompts. Different arms reuse identical native
calls within the same notice; no labels or other notices choose a policy.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import itertools
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.b4_entry import B4Pipeline,PROFILES,digest
from submission.pps.data import records
from submission.pps.generation_contract import generation_schema,prepared_preflight
from submission.runtime import Journal,source_manifest,sha256,call_plan

OPTIONS={'source_policy':'purchase_context','legal_policy':'direct_production',
    'specification_review':'gated_source_candidates','catalog_review':'explicit',
    'software_review':'current','a10_thinking_budget':768,'a_cohort_size':32}
ARMS=('purchase_context','current','purchase_context_hybrid')
NORMAL='purchase_context_catalog_explicit_v9_gated'


def native_identity(packet):
    return digest({'record_id':packet['record_id'],'tokens':packet['token_ids'],
        'schema':generation_schema(packet['generation']['response_format'],len(packet['spans']),packet['items'],
            specification_inventory=packet['generation'].get('specification_inventory')),
        'generation':{k:v for k,v in packet['generation'].items() if k!='specification_inventory'}})


def source_cost(packet,tokenizer):
    return sum(len(tokenizer.encode(s['text'],add_special_tokens=False)) for s in packet['spans'])


def prepare(input_path,data_dir,tokenizer_dir,embed_dir,output,*,arms=ARMS,cache_notices=12,a_cohort_size=32):
    from transformers import AutoTokenizer
    # Fail before expensive CPU embedding if this preparation environment
    # cannot compile the actual generation contracts.
    import llguidance  # noqa: F401
    if not arms or arms[0]!='purchase_context' or len(set(arms))!=len(arms) or any(a not in ARMS for a in arms):
        raise ValueError('The normal arm must be first, and all source policies declared')
    if type(cache_notices) is not int or cache_notices<0:
        raise ValueError('Cache sample size must be a nonnegative integer')
    if type(a_cohort_size) is not int or not 1 <= a_cohort_size <= 32:
        raise ValueError('A cohort size must be an integer between 1 and 32')
    options={**OPTIONS,'a_cohort_size':a_cohort_size}
    began=time.monotonic();journal=Journal(output);source=source_manifest()
    recs=list(records(input_path));tokenizer=AutoTokenizer.from_pretrained(tokenizer_dir,local_files_only=True,trust_remote_code=False)
    pipes={name:B4Pipeline(data_dir,tokenizer,**{**options,'source_policy':name}) for name in arms}
    # Only current-notice documents and fixed query embeddings enter retrieval.
    if 'purchase_context_hybrid' in pipes:
        from submission.pps.embeddings import BGEDenseEncoder
        pipes['purchase_context_hybrid']._source_encoder=BGEDenseEncoder(embed_dir)
    journal.save('preregistered.json',{'options':options,'normal_policy':NORMAL,'arms':list(arms),
        'records':len(recs),'record_ids':[r['id'] for r in recs],'config':dataclasses.asdict(pipes[arms[0]].config),
        'maximum_format_retries':pipes[arms[0]].config.max_response_retries,
        'labels_read':False,'quality_rerolls':0,'normal_primary_order_unaffected_by_probes':True,
        'execution':f'canonical normal A cohorts{a_cohort_size}, then frozen whole-source comparisons and explicit cache repeats',
        'source_budget':'per-notice canonical A raw-source cap; L source unchanged; no new original-token subsidy',
        'same_notice_identical_native_calls_shared':True,
        'cache_repeat_notices':cache_notices,'cache_sampling':'source-length quantiles, no labels or predictions',
        'cache_repeats_never_replace_normal_or_comparison_outputs':True})
    primary_bundles=[];probes=[];mapping={};source_report=[];packet_index={};aliases={}
    for position,record in enumerate(recs):
        identities={};controls=None
        for name in arms:
            bundle=pipes[name].bundle(record)
            roles={}
            for old in bundle:
                identity=native_identity(old)
                if name==arms[0]:
                    packet=old;key=old['request_key']
                    identities[identity]=key;packet_index[key]=packet
                else:
                    logical=f'probe:{name}:{old["request_key"]}'
                    if identity in identities:
                        key=identities[identity];aliases[logical]=key
                    else:
                        key=logical
                        packet={**old,'request_key':key,'probe_role':name,'input_position':position}
                        identities[identity]=key;packet_index[key]=packet;probes.append(packet)
                roles[old['batch']]=key
            mapping[name,record['id']]=roles
            if name==arms[0]:primary_bundles.append(bundle)
            control={p['batch']:p for p in bundle}
            if name=='current':controls=control
            source_report.append({'record_id':record['id'],'arm':name,
                'A_source_tokens':source_cost(control['A1'],tokenizer),
                'L_source_tokens':source_cost(control['L19'],tokenizer),
                'S9_candidates':len(control.get('S9',{}).get('specification_inventory',{}).get('candidates',[])),
                'S9_missing': 'S9' not in control,
                'source_diagnostics':control['A1'].get('source_search',{}).get('diagnostics',{}),
                'source_fallback':control['A1'].get('source_strategy_fallback')})
        if controls:
            cap=source_cost(controls['A1'],tokenizer)
            for row in source_report[-len(arms):]:
                if row['A_source_tokens']>cap or row['L_source_tokens']!=source_cost(controls['L19'],tokenizer):
                    raise ValueError('Original-source cap or L control changed')
        if (position+1)%10==0 or position+1==len(recs):
            journal.progress(phase='preparing_source_context',notices=position+1,total=len(recs),
                             unique_probe_calls=len(probes),seconds=time.monotonic()-began,gpu_allocated=False)
    primary=[p for profile in (*PROFILES,'Q10','W20','S9') for bundle in primary_bundles for p in bundle if p['batch']==profile]
    declared={}
    for arm,catalog,spec in itertools.product(arms,('none','explicit'),('none','gated')):
        values=[]
        for rec in recs:
            roles=mapping[arm,rec['id']]
            values.extend(roles[p] for p in PROFILES)
            if catalog=='explicit' and 'Q10' in roles:values.append(roles['Q10'])
            if spec=='gated' and 'S9' in roles:values.append(roles['S9'])
        declared[f'{arm}_catalog_{catalog}_v9_{spec}']=values
    declared[NORMAL+'_no_L']=[k for k in declared[NORMAL] if packet_index[k]['family']!='L']
    plan=[]
    for arm in arms[1:]:
        for profile in (*PROFILES,'Q10','S9'):
            selected=[p for p in probes if p['probe_role']==arm and p['batch']==profile]
            for offset in range(0,len(selected),32):
                plan.append({'role':arm,'profile':profile,'request_keys':[p['request_key'] for p in selected[offset:offset+32]]})
    # Native repeat probes are a declared reproducibility observation, not a
    # quality retry. The initial normal response remains the only chosen one.
    ranked=sorted(range(len(recs)),key=lambda i:(len(primary_bundles[i][0]['token_ids']),i))
    count=min(cache_notices,len(recs))
    positions=sorted({ranked[round(j*(len(ranked)-1)/max(1,count-1))] for j in range(count)})
    for size in (2,32):
        for offset in range(0,len(positions),size):
            for profile in PROFILES[:3]:
                keys=[]
                for position in positions[offset:offset+size]:
                    parent=mapping[arms[0],recs[position]['id']][profile]
                    key=f'probe:cache{size}:'+parent
                    p={**copy.deepcopy(packet_index[parent]),'request_key':key,'probe_role':f'cache{size}',
                        'normal_parent':parent,'input_position':position,'diagnostic_repeat_not_quality_retry':True}
                    keys.append(key);probes.append(p)
                plan.append({'role':f'cache{size}','profile':profile,'request_keys':keys})
    edges=[]
    for arm,catalog,spec in itertools.product(arms,('none','explicit'),('none','gated')):
        name=f'{arm}_catalog_{catalog}_v9_{spec}'
        if catalog=='explicit':edges.append(('catalog_explicit',f'{arm}_catalog_none_v9_{spec}',name))
        if spec=='gated':edges.append(('candidate_review',f'{arm}_catalog_{catalog}_v9_none',name))
        if arm!='current' and 'current' in arms:edges.append(('source_'+arm,f'current_catalog_{catalog}_v9_{spec}',name))
    edges.append(('fourth_L_call',NORMAL+'_no_L',NORMAL))
    for name,values in (('current_inputs.jsonl.gz',recs),('primary_packets.jsonl.gz',primary),('probe_packets.jsonl.gz',probes)):
        journal.rows(name,values)
    # Preserve completed source selection even if a later grammar/schema
    # check fails. These are not executable inputs until input_freeze exists.
    journal.save('recipes.json',declared);journal.save('contrasts.json',edges)
    journal.save('probe_plan.json',{'batches':plan,'normal_parent_repeats_never_used_in_recipes':True})
    journal.save('native_aliases.json',aliases)
    journal.save('source_budget_report.json',source_report)
    journal.save('normal_call_plan.json',call_plan(recs,primary,a_cohort_size=a_cohort_size))
    report={'records':len(recs),'primary_prepared':len(primary),'probe_requests':len(probes),'policies':len(declared),
        'primary_profiles':{role:sum(p['batch']==role for p in primary) for role in (*PROFILES,'Q10','W20','S9')},
        'probe_roles':{role:sum(p['probe_role']==role for p in probes) for role in (*arms[1:],'cache2','cache32')},
        'identical_same_notice_aliases':len(aliases),'cache_repeat_positions':positions,
        'max_input_tokens':max(len(p['token_ids']) for p in primary+probes),
        'seconds':time.monotonic()-began,'labels_read':False,'new_model_calls':0,
        'source_preparation':{name:pipe.source_preparation for name,pipe in pipes.items()},
        'encoders':{name:getattr(pipe._source_encoder,'receipt',None) for name,pipe in pipes.items()}}
    journal.save('preparation_report.json',report)
    proof=prepared_preflight(primary+probes)
    journal.save('generation_preflight.json',proof)
    if source_manifest()!=source:raise RuntimeError('Source changed during preparation')
    journal.save('input_freeze.json',{'source_sha256':source,'original_input_sha256':sha256(input_path),
        'records':len(recs),'labels_read':False,'files':{p.name:sha256(p) for p in journal.root.iterdir() if p.is_file()}})
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('input','data-dir','tokenizer-dir','embed-dir','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--a-cohort-size',type=int,default=32)
    args=parser.parse_args()
    print(json.dumps(prepare(args.input,args.data_dir,args.tokenizer_dir,args.embed_dir,args.output,
                             a_cohort_size=args.a_cohort_size),ensure_ascii=False,indent=2))
