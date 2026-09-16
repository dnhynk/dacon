"""Freeze a bounded Q instruction/schema comparison on a prior source-only cohort."""
from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.b4_entry import digest
from submission.pps.catalog_scope import ITEMS, output_contract
from submission.pps.generation_contract import generation_schema, preflight
from submission.pps.prompts import Config, token_ids, output_schema
from submission.runtime import source_manifest
from tools.prepare_retrieval_contrast import read_rows, write_rows
from tools.run_retrieval_contrast import verify_packet_source


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def explain_packet(previous, tokenizer):
    """Change model instructions only; source text, source IDs and sampler stay fixed."""
    packet=copy.deepcopy(previous)
    packet['messages'][0]['content'] += '\n' + output_contract(len(packet['spans']))
    packet['token_ids']=token_ids(tokenizer, packet['messages'], True)
    packet['prompt_sha256']=digest(packet['messages'])
    packet['token_ids_sha256']=digest(packet['token_ids'])
    return packet


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared',type=Path,required=True,help='Verified previous source-selected order pilot inputs')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    code=source_manifest()
    previous=json.loads((args.prepared/'contrast_freeze.json').read_text(encoding='utf-8'))
    for name,key in [('contrast_packets.jsonl.gz','packets_sha256'),('contrast_inputs.jsonl.gz','inputs_sha256')]:
        assert sha(args.prepared/name)==previous[key]
    previous_registration=json.loads((args.prepared/'preregistered.json').read_text(encoding='utf-8'))
    assert previous_registration['classification_labels_read'] is False
    assert previous_registration['selection_model_responses_read'] is False
    records=read_rows(args.prepared/'contrast_inputs.jsonl.gz')
    originals={p['record_id']:p for p in read_rows(args.prepared/'contrast_packets.jsonl.gz') if p['arm']=='control'}
    assert len(records)==len(originals)==18
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer',local_files_only=True,trust_remote_code=False)
    config=Config.load(ROOT/'submission/model/config.json')
    methods=('control','explicit_contract')
    packets=[]
    for index,record in enumerate(records):
        original=originals[record['id']]
        verify_packet_source(original,record,tokenizer)
        assert digest(original['messages'])==original['prompt_sha256']
        assert token_ids(tokenizer,original['messages'],True)==original['token_ids']
        assert original['generation']=={'response_format':'catalog_scope','thinking_budget':0,'max_output_tokens':1536}
        assert original['schema_sha256']==digest(output_schema('catalog_scope',len(original['spans']),ITEMS))
        pair=[copy.deepcopy(original),explain_packet(original,tokenizer)]
        for arm,p in zip(methods,pair):
            p.update(arm=arm,request_key=f'contract:{record["id"]}:{arm}',original_request_key=original['request_key'])
            p['generation_schema_sha256']=digest(generation_schema('catalog_scope',len(p['spans']),ITEMS))
            assert len(p['token_ids'])+1536+32 <= config.max_model_len
            verify_packet_source(p,record,tokenizer)
        for key in ('spans','schema_sha256','source_search','catalog_scope','generation','generation_schema_sha256'):
            assert pair[0][key]==pair[1][key]
        assert pair[0]['messages'][1:]==pair[1]['messages'][1:]
        packets.extend(pair if index%2==0 else pair[::-1])
    prereg={'kind':'scope_instruction_schema_contract_pilot','previous_preparation':str(args.prepared),
        'previous_freeze_sha256':sha(args.prepared/'contrast_freeze.json'),
        'previous_registration_sha256':sha(args.prepared/'preregistered.json'),
        'selection':'Reuse the full18notice source-only cohort frozen before v8 responses; no new label/response selection.',
        'classification_labels_read':False,'selection_model_responses_read':False,'development_exposed':True,'held_out':False,
        'notices':18,'primary_requests':36,'changed_variable':'Add complete output schema and balanced field/enum meanings to Q system instructions only.',
        'fixed':['original source spans and4096token cap','user text','catalog29','schema/field order','thinking0','max output1536','consumer','model revision'],
        'arms':list(methods),'embedding_calls':0,'quality_rerolls':0,
        'hypothesis':'Q grammar forces fields/choices that legacy instructions omit, causing instruction/schema mismatch. This is a prompt-consumption experiment, not retrieval gain.',
        'prior_observation':'v8 control0listed categories17unresolved; schema-valid placeholders exist. Do not assume clearer instructions improve semantic accuracy.',
        'decision':'Count all formatting outcomes; task-field selection, semantic content, unsupported source promotion, full-baseline joined F1. No per-notice arm selection; no full-fresh or held-out claim.',
        'official_reference':'https://docs.vllm.ai/en/v0.26.0/features/structured_outputs/'}
    (args.output/'preregistered.json').write_text(json.dumps(prereg,ensure_ascii=False,indent=2),encoding='utf-8')
    grammar=preflight([('catalog_scope',len(p['spans']),ITEMS) for p in packets])
    (args.output/'generation_grammar_preflight.json').write_text(json.dumps(grammar,indent=2),encoding='utf-8')
    write_rows(args.output/'contrast_packets.jsonl.gz',packets)
    write_rows(args.output/'contrast_inputs.jsonl.gz',records)
    counts=[p['source_search']['source_tokens'] for p in originals.values()]
    spending={'sum':sum(counts),'mean':statistics.mean(counts),'min':min(counts),'max':max(counts)}
    freeze={'kind':prereg['kind'],'source_sha256':code,'config':dataclasses.asdict(config),
        'packets_sha256':sha(args.output/'contrast_packets.jsonl.gz'),'inputs_sha256':sha(args.output/'contrast_inputs.jsonl.gz'),
        'call_plan':[{'number':n//18,'request_keys':[p['request_key'] for p in packets[n:n+18]]} for n in range(0,len(packets),18)],
        'cases':18,'notices':18,'primary_requests':36,'methods':list(methods),
        'source_token_budget':4096,'source_tokens':{arm:spending for arm in methods},
        'input_tokens_total':sum(len(p['token_ids']) for p in packets),'input_tokens_max':max(len(p['token_ids']) for p in packets),
        'input_tokens_by_arm':{arm:sum(len(p['token_ids']) for p in packets if p['arm']==arm) for arm in methods},
        'classification_labels_read':False,'embedding_calls_here':0,'new_full160_score':False,'official_macro_f1':None,
        'preregistered_sha256':sha(args.output/'preregistered.json'),
        'generation_grammar_preflight_sha256':sha(args.output/'generation_grammar_preflight.json'),
        'cpu_seconds':time.monotonic()-started}
    assert source_manifest()==code
    (args.output/'contrast_freeze.json').write_text(json.dumps(freeze,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:freeze[k] for k in ('notices','primary_requests','source_tokens','input_tokens_by_arm','cpu_seconds')},ensure_ascii=False))


if __name__=='__main__':main()
