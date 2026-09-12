"""Original A/L producers with unchanged B4 CPU consumers, for current inputs."""
from __future__ import annotations
import copy
import dataclasses
import hashlib
import json
import sys
from pathlib import Path
import jsonschema

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'frozen'))
from original_a.knowledge import Knowledge as OriginalKnowledge
from original_a.prompts import Config as OriginalConfig, build_shared_prompts
from pps.knowledge import Knowledge
from pps.pipeline import _response_row, parse_output
from pps.prompts import Config, output_schema
from pps.retrieval import Span
from pps.v20_route import UniformV20Route
from v20_legacy.pipeline import parse_output as legacy_parse

GROUPS=(tuple(range(1,10)),tuple(range(10,19)),tuple(range(19,25)))
PROFILES=('A1','A10','A19','L19')


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False).encode()).hexdigest()


def restored(packet):
    return {**packet,'spans':[Span(**s) if isinstance(s,dict) else s for s in packet['spans']]}


def parse_error(packet,response):
    try:
        if response.get('finish_reason') not in ('stop','eos_token'):
            raise ValueError('Incomplete first/final answer')
        obj=json.loads(response['text'])
        jsonschema.validate(obj,output_schema(packet['generation']['response_format'],len(packet['spans']),packet['items']))
        parser=legacy_parse if packet['family']=='L' else parse_output
        parser(response['text'],restored(packet)['spans'],tuple(packet['items']),rec=None)
        return None
    except (ValueError,TypeError,KeyError,jsonschema.ValidationError) as exc:
        return type(exc).__name__+': '+str(exc).splitlines()[0]


class B4Pipeline:
    def __init__(self,data_dir,tokenizer):
        self.config=Config.load(HERE/'frozen/model/config.json')
        self.original_config=OriginalConfig.load(HERE/'original_a_config.json')
        self.knowledge=Knowledge(data_dir)
        self.original_knowledge=OriginalKnowledge(data_dir)
        self.route=UniformV20Route(data_dir,tokenizer)
        self.tokenizer=tokenizer
        assert self.original_config.rubric_version=='v4'
        assert self.original_config.response_format=='fact_compact'

    def bundle(self,record):
        prompts=build_shared_prompts(record,self.original_knowledge,self.original_config,self.tokenizer,GROUPS)
        prompts.append(self.route.prompt(record))
        result=[]
        for profile,prompt in zip(PROFILES,prompts):
            family=profile[0];first=int(profile[1:]);rid=record['id']
            cfg=self.route.config if family=='L' else self.original_config
            spans=[dataclasses.asdict(s) for s in prompt['spans']]
            ids=prompt['token_ids']
            if ids is None:raise ValueError('A fixed local tokenizer is required')
            if len(ids)+cfg.max_output_tokens+32>cfg.max_model_len:
                raise ValueError('Current prompt exceeds the fixed context budget')
            result.append({'request_key':f'{family}:{rid}:{first}','record_id':rid,'family':family,'batch':profile,
                'items':list(prompt['items']),'messages':prompt['messages'],'token_ids':ids,
                'prompt_sha256':digest(prompt['messages']),'token_ids_sha256':digest(ids),
                'spans':spans,'source_sha256':digest(spans),'comparison_facts':prompt.get('comparison_facts'),
                'coverage':prompt['coverage'],'generation':{'response_format':cfg.response_format,
                    'thinking_budget':cfg.thinking_budget_for(prompt['items']),'max_output_tokens':cfg.max_output_tokens},
                'schema_sha256':digest(output_schema(cfg.response_format,len(spans),prompt['items']))})
        return result

    def packets(self,records):
        if not records or len({r['id'] for r in records})!=len(records):
            raise ValueError('Current records must be nonempty and uniquely identified')
        bundles=[self.bundle(record) for record in records]
        return [bundle[i] for i in range(4) for bundle in bundles]

    def consume(self,record,packet,response):
        prompt=restored(packet)
        if packet['family']=='L':
            return self.route.consume(record,response,prompt)
        items=tuple(packet['items'])
        row,details=_response_row(record,response,prompt,items,self.config,self.knowledge,items)
        return {f'{field}{k}':int(row[f'v{k}']) if field=='v' else row[f'e{k}'] for k in items for field in ('v','e')},details


def assemble(records,packets,call_rows):
    b3={r['id']:{'id':r['id'],**{f'v{k}':None for k in range(1,25)},**{f'e{k}':'' for k in range(1,25)}} for r in records}
    for packet in packets:
        row=call_rows[packet['request_key']]
        if packet['family']=='A' and row is not None:
            b3[packet['record_id']].update(row)
    b4=copy.deepcopy(b3)
    for packet in packets:
        if packet['family']=='L':
            row=call_rows[packet['request_key']]
            if row is not None and set(row)!={'v20','e20'}:
                raise ValueError('Uniform L route may only replace v20/e20')
            b4[packet['record_id']].update(row if row is not None else {'v20':None,'e20':''})
    assert all(b4[rid][key]==row[key] for rid,row in b3.items() for key in row if key not in {'v20','e20'})
    return b3,b4
