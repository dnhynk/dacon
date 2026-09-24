"""Freeze whole-cohort canonical producer alternatives without reading labels."""
from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.b4_entry import B4Pipeline, digest
from submission.pps.data import records
from submission.pps.generation_contract import generation_schema
from submission.pps.prompts import token_ids, verified_search_spans
from submission.runtime import source_manifest

POLICIES=('current','factual_lexical','evidence_cover')
ROLES=('A1','A10','A19','L19','LAW','S9')


def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


def write(stream,row):
    stream.write(json.dumps(row,ensure_ascii=False)+'\n')


def native_identity(packet):
    """Only identical actual calls inside this one notice may be shared."""
    return digest({'record_id':packet['record_id'],'tokens':packet['token_ids'],
        'schema':packet['generation_schema_sha256'],
        'generation':{k:v for k,v in packet['generation'].items()
                      if k not in {'specification_inventory'}}})


def prepare(input_path,data_dir,tokenizer_dir,embed_dir,output,*,limit=None,policies=POLICIES):
    from transformers import AutoTokenizer
    if not policies or len(set(policies))!=len(policies) or any(p not in POLICIES for p in policies):
        raise ValueError('Unknown or duplicate comparison source policy')
    output=Path(output); output.mkdir(parents=True,exist_ok=False)
    source=source_manifest(); input_sha=sha(input_path); began=time.monotonic()
    recs=list(records(input_path,limit))
    tokenizer=AutoTokenizer.from_pretrained(tokenizer_dir,local_files_only=True,trust_remote_code=False)
    # This fixed comparison adds LAW/S9 alternatives itself. A promoted runtime
    # default must not pre-apply them to its historical four-call control.
    pipes={name:B4Pipeline(data_dir,tokenizer,source_policy=name,
        legal_policy='current', specification_review='current', catalog_review='current',
        software_review='current') for name in policies}
    if 'evidence_cover' in pipes:
        from submission.pps.embeddings import BGEDenseEncoder
        pipes['evidence_cover']._source_encoder=BGEDenseEncoder(embed_dir)
    registered={'kind':'whole_cohort_integrated_producer_comparison','labels_read':False,
        'input_sha256':input_sha,'source_sha256':source,'record_ids':[r['id'] for r in recs],
        'source_policies':list(policies),'source_budget':'per-notice actual current raw-source tokens',
        'law_budget':'per-notice actual current law-source tokens',
        'specialist_source':'same A original source; no separate search',
        'alias_policy':'same notice, exact rendered tokens/effective schema/generation; first prepared occurrence',
        'quality_retries':0,'cohort_size':32,'unseen_confirmation_labels_read':False,
        'config':dataclasses.asdict(pipes[policies[0]].config)}
    save(output/'preregistered.json',registered)
    recipes={f'{name}{suffix}':[] for name in policies for suffix in ('','+v9','+law','+law+v9')}
    aliases={}; info={}; source_report=[]; unique_count=logical_count=0
    with gzip.open(output/'current_inputs.jsonl.gz','wt',encoding='utf8') as stream:
        for rec in recs: write(stream,rec)
    with gzip.open(output/'canonical_packets.jsonl.gz','wt',encoding='utf8') as canonical, \
            gzip.open(output/'native_packets.jsonl.gz','wt',encoding='utf8') as native:
        for position,rec in enumerate(recs):
            start=time.monotonic(); identities={}; index={}
            for name in policies:
                pipe=pipes[name]
                base=pipe.bundle(rec)
                law=pipe.legal_packet(rec,base[1])
                specialist=pipe.specification_packet(rec,base[0])
                role_packets=list(zip(ROLES[:4],base))+[('LAW',law)]
                if specialist is not None: role_packets.append(('S9',specialist))
                roles={}
                for role,old in role_packets:
                    packet={**old,'request_key':f'{name}:{role}:{position}',
                        'experiment_policy':name,'experiment_role':role,'input_position':position}
                    assert packet['token_ids']==token_ids(tokenizer,packet['messages'],pipe.config.enable_thinking)
                    effective=generation_schema(packet['generation']['response_format'],len(packet['spans']),packet['items'],
                        specification_inventory=packet['generation'].get('specification_inventory'))
                    packet['generation_schema_sha256']=digest(effective)
                    if packet.get('source_search') is not None:
                        verified_search_spans(rec,packet['source_search'],tokenizer)
                    key=packet['request_key']; identity=native_identity(packet)
                    native_key=identities.setdefault(identity,key)
                    aliases[key]=native_key; roles[role]=key; index[key]=packet
                    logical_count+=1; write(canonical,packet)
                    if native_key==key:
                        unique_count+=1; write(native,packet)
                        info[key]={'policy':name,'role':role,'position':position,
                            'input_tokens':len(packet['token_ids']),
                            'max_output_tokens':packet['generation']['max_output_tokens']}
                normal=[roles[r] for r in ROLES[:4]]
                with_law=[roles['LAW'] if i==1 else key for i,key in enumerate(normal)]
                specialist_keys=[roles['S9']] if 'S9' in roles else []
                for suffix,keys in (('',normal),('+v9',normal+specialist_keys),
                        ('+law',with_law),('+law+v9',with_law+specialist_keys)):
                    recipes[name+suffix].extend(keys)
                source_report.append({'position':position,'record_id':rec['id'],'policy':name,
                    'A_source_tokens':sum(len(tokenizer.encode(s['text'],add_special_tokens=False)) for s in base[0]['spans']),
                    'L_source_tokens':sum(len(tokenizer.encode(s['text'],add_special_tokens=False)) for s in base[3]['spans']),
                    'source_fallbacks':[p.get('source_strategy_fallback') for p in base if p.get('source_strategy_fallback')],
                    'legal_fallback':law.get('legal_strategy_fallback'),
                    'specialist':pipe.specialist_preparation[-1]})
            # These maps are source-local; no document vectors, scores or model
            # answers from one record are used when preparing the next one.
            del identities,index
            print(json.dumps({'prepared':position+1,'records':len(recs),'unique_calls':unique_count,
                'seconds':round(time.monotonic()-start,3),'total_seconds':round(time.monotonic()-began,3)},ensure_ascii=False),flush=True)
            save(output/'progress.json',{'prepared':position+1,'records':len(recs),'unique_calls':unique_count,
                'seconds':time.monotonic()-began,'gpu_allocated_by_this_tool':False})
    plan=[]
    # Same predetermined profile/cohort order for each producer. Identical
    # already-declared calls are omitted, not regenerated until they look good.
    for role_group in (ROLES[:3],('L19',),('LAW',),('S9',)):
        for offset in range(0,len(recs),32):
            for name in policies:
                for role in role_group:
                    keys=[key for key,v in info.items() if v['policy']==name and v['role']==role
                          and offset<=v['position']<offset+32]
                    if keys: plan.append({'number':len(plan),'policy':name,'role':role,
                        'cohort':offset//32,'request_keys':keys})
    assert len([k for row in plan for k in row['request_keys']])==len(info)
    assert set(k for row in plan for k in row['request_keys'])==set(info)
    assert source==source_manifest(), 'Runtime changed during preparation; discard this unsealed preparation'
    assert sha(input_path)==input_sha
    save(output/'recipes.json',{'recipes':recipes,'native_aliases':aliases})
    save(output/'call_plan.json',{'batches':plan,'order':'base A, base L, law, specialist; cohort, policy, profile'})
    save(output/'source_budget_report.json',source_report)
    stats={'records':len(recs),'logical_calls':logical_count,'unique_model_calls':unique_count,
        'identical_same_notice_aliases':logical_count-unique_count,
        'input_tokens':sum(v['input_tokens'] for v in info.values()),
        'max_input_tokens':max(v['input_tokens'] for v in info.values()),
        'preparation_seconds':time.monotonic()-began,'labels_read':False,'gpu_used':False,
        'policies':{name:{'source_preparation':p.source_preparation,
            'encoder':getattr(p._source_encoder,'receipt',None)} for name,p in pipes.items()}}
    save(output/'preparation_report.json',stats)
    protected={p.name:sha(p) for p in output.iterdir() if p.is_file() and p.name!='progress.json'}
    save(output/'input_freeze.json',{'source_sha256':source,'files':protected,'records':len(recs),
        'logical_calls':logical_count,'unique_model_calls':unique_count,'labels_read':False,
        'runtime_call_order_is_separate_deployment_measurement':True})
    return stats


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--data-dir',type=Path,default=ROOT/'data_open/data')
    p.add_argument('--tokenizer-dir',type=Path,default=ROOT/'models/gemma-tokenizer')
    p.add_argument('--embed-dir',type=Path,default=ROOT/'models/bge-m3')
    p.add_argument('--limit',type=int)
    p.add_argument('--policies',nargs='+',choices=POLICIES,default=list(POLICIES))
    args=p.parse_args()
    print(json.dumps(prepare(args.input,args.data_dir,args.tokenizer_dir,args.embed_dir,args.output,
        limit=args.limit,policies=tuple(args.policies)),ensure_ascii=False,indent=2))
