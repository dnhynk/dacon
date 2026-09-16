"""Reproduce a frozen V29 task-context arm through the canonical packet path.

This is source-only verification. It never opens labels or model responses.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from submission.b4_entry import B4Pipeline
from submission.runtime import source_manifest


ARMS={
    ('task_lexical',False):'lexical_reserved_plain',
    ('task_lexical',True):'lexical_reserved_groups',
    ('task_hybrid',True):'hybrid_reserved_groups',
}
FIELDS=('messages','token_ids','spans','prompt_sha256','token_ids_sha256','source_sha256',
        'coverage','catalog_scope','source_unitization','generation','generation_schema_sha256',
        'task_field_groups')


def rows(path):
    with gzip.open(path,'rt',encoding='utf-8') as stream:
        return list(map(json.loads,stream))


def comparable_source(value):
    result=copy.deepcopy(value)
    result.get('diagnostics',{}).pop('integrated_catalog_producer',None)
    # Frozen packets have passed through JSON, where coordinate tuples become
    # arrays. Compare the exact persisted representation used by the runtime.
    return json.loads(json.dumps(result,ensure_ascii=False))


def verify(inputs,expected_packets,data_dir,tokenizer_dir,output,policy,groups):
    from transformers import AutoTokenizer
    output.mkdir(parents=True,exist_ok=False)
    began=time.monotonic()
    arm=ARMS[policy,groups]
    records=rows(inputs)
    expected={p['record_id']:p for p in rows(expected_packets) if p['arm']==arm}
    if len(records)!=len(expected) or {r['id'] for r in records}!=set(expected):
        raise ValueError('Frozen arm and current input cohorts differ')
    tokenizer=AutoTokenizer.from_pretrained(tokenizer_dir,local_files_only=True,trust_remote_code=False)
    pipe=B4Pipeline(data_dir,tokenizer,catalog_source_policy=policy,catalog_task_groups=groups)
    actual={p['record_id']:p for p in pipe.packets(records) if p['batch']=='Q10'}
    if set(actual)!=set(expected):
        raise ValueError('Canonical Q cohort differs from the frozen arm')
    mismatches=[]
    for rid in sorted(expected):
        left,right=actual[rid],expected[rid]
        for field in FIELDS:
            if left.get(field)!=right.get(field):
                mismatches.append({'record_id':rid,'field':field})
        if comparable_source(left['source_search'])!=comparable_source(right['source_search']):
            mismatches.append({'record_id':rid,'field':'source_search'})
    report={'kind':'source_only_canonical_task_context_promotion_verification',
        'arm':arm,'policy':policy,'task_groups':groups,'records':len(records),
        'canonical_Q_packets':len(actual),'mismatches':mismatches,
        'status':'PASS' if not mismatches else 'FAIL','seconds':time.monotonic()-began,
        'labels_read':False,'model_responses_read':False,'new_model_calls':0,
        'source_sha256':source_manifest(),'preparation':pipe.catalog_source_preparation,
        'encoder':getattr(pipe._source_encoder,'receipt',None),
        'limitation':'Packet identity only; this is not inference, a score, or an official runtime measurement.'}
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if mismatches:
        raise ValueError(f'{len(mismatches)} frozen packet fields differ; see {output / "report.json"}')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--expected-packets',type=Path,required=True)
    parser.add_argument('--data-dir',type=Path,required=True)
    parser.add_argument('--tokenizer-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--policy',choices=('task_lexical','task_hybrid'),required=True)
    parser.add_argument('--task-groups',action='store_true')
    args=parser.parse_args()
    print(json.dumps(verify(args.inputs,args.expected_packets,args.data_dir,args.tokenizer_dir,
                            args.output,args.policy,args.task_groups),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
