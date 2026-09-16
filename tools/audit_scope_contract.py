"""Verify native inputs/samplers and report all outcomes of the Q contract pilot."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.b4_entry import digest
from submission.pps.catalog_scope import output_contract
from submission.pps.generation_contract import generation_schema
from submission.runtime import source_manifest


def rows(path):
    with gzip.open(path,'rt',encoding='utf-8') as stream:return list(map(json.loads,stream))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    assert not args.output.exists()
    folder=args.run/'contrast_output'
    freeze=json.loads((folder/'input_freeze.json').read_text(encoding='utf-8'))
    assert freeze['source_sha256']==source_manifest()
    packets={r['request_key']:r for r in rows(folder/'current_packets.jsonl.gz')}
    final={r['request_key']:r for r in rows(folder/'contrast_results.jsonl.gz')}
    primary_rows={r['request_key']:r for p in sorted(folder.glob('primary_*.jsonl.gz')) for r in rows(p)}
    native=[r for p in sorted(folder.glob('call_*_native.jsonl.gz')) for r in rows(p)]
    primary={r['request_key']:r for r in native if r['attempt']==0}
    assert primary.keys()==packets.keys()==final.keys()==primary_rows.keys()
    assert len(primary)==sum(r['attempt']==0 for r in native)
    samplers={}
    for path in sorted(folder.glob('call_*_sampling.json')):
        call=json.loads(path.read_text(encoding='utf-8'))
        assert len(call['request_keys'])==len(call['before_llm_generate'])
        for key,sampler in zip(call['request_keys'],call['before_llm_generate']):samplers.setdefault(key,sampler)
    details=[]
    aggregate={arm:Counter() for arm in freeze['methods']}
    for key,packet in packets.items():
        observed=primary[key]['native']
        assert digest(packet['messages'])==primary[key]['prompt_sha256']
        assert digest(packet['token_ids'])==primary[key]['token_ids_sha256']
        assert observed['prompt_token_ids']==packet['token_ids']
        expected=generation_schema('catalog_scope',len(packet['spans']),packet['items'])
        actual=samplers[key]['structured_outputs']['json']
        assert actual==expected and list(actual['properties'])==list(expected['properties'])
        assert digest(actual)==packet['generation_schema_sha256']
        result=primary_rows[key]
        assert hashlib.sha256(observed['raw_text'].encode()).hexdigest()==result['response']['raw_output_sha256']
        counts=aggregate[packet['arm']]
        counts['primary_returns']+=1
        counts['output_tokens']+=len(observed['output_token_ids'])
        counts['length_finishes']+=observed['finish_reason']=='length'
        counts['whitespace_stalls']+=bool(result['response'].get('generation_stall'))
        counts['primary_format_valid']+=result['parse_error'] is None
        counts['selected_retry']+=final[key]['attempt']>0
        obj=None
        if result['parse_error'] is None:
            obj=json.loads(result['response']['text'])
            assert list(obj)==list(expected['properties'])
            meaningful=any(c.isalnum() for c in obj['task_summary'])
            counts['summary_has_content']+=meaningful
            counts['punctuation_only_summary']+=not meaningful
            counts['listed_category']+=obj['catalog_relation']=='listed_category'
            counts['unresolved_scope']+=obj['unresolved_scope']
        details.append({'id':packet['record_id'],'arm':packet['arm'],'model_scope':obj,
            'primary_parse_error':result['parse_error'],'final_parse_error':final[key]['parse_error'],
            'gate':(final[key]['details'] or {}).get('gate'),'selected_attempt':final[key]['attempt'],
            'raw_output_sha256':result['response']['raw_output_sha256']})
    pairs=[]
    for record in rows(folder/'current_inputs.jsonl.gz'):
        pair=[next(p for p in packets.values() if p['record_id']==record['id'] and p['arm']==arm)
              for arm in ('control','explicit_contract')]
        for key in ('spans','source_search','catalog_scope','schema_sha256','generation_schema_sha256','generation'):
            assert pair[0][key]==pair[1][key]
        assert pair[0]['messages'][1:]==pair[1]['messages'][1:]
        assert pair[0]['messages'][0]['content']+'\n'+output_contract(len(pair[0]['spans']))==pair[1]['messages'][0]['content']
        assert samplers[pair[0]['request_key']]==samplers[pair[1]['request_key']]
        pairs.append({'id':record['id'],'original_inputs_equal':True,'sampler_equal':True,
            'system_contract_addition_verified':True,
            'input_token_delta':len(pair[1]['token_ids'])-len(pair[0]['token_ids']),
            'identical_answer':final[pair[0]['request_key']]['response']['text']==final[pair[1]['request_key']]['response']['text']})
    report={'kind':'native_scope_instruction_contract_pilot_audit','source_sha256':freeze['source_sha256'],
        'classification_labels_read':False,'new_model_calls':0,'notices':len(pairs),
        'native_returns':len(native),'primary_returns':len(primary),'retry_returns':len(native)-len(primary),
        'aggregates':aggregate,'details':details,'pairs':pairs,'source_input_equality_verified':True,
        'actual_sampler_equality_verified':True,'system_contract_addition_verified':True,
        'limitation':'This measures prompt consumption, not retrieval. Schema-valid content and resolved flags are not semantic gold; final predictions require separate frozen-arm scoring.'}
    with args.output.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'aggregates':aggregate,'paired_verification':'PASS'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
