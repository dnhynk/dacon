"""Audit actual order-pilot inputs, samplers, native returns and semantic placeholders."""
import argparse
from collections import Counter
import copy
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.b4_entry import digest
from submission.pps.generation_contract import generation_schema
from submission.runtime import source_manifest


def rows(path):
    with gzip.open(path,'rt',encoding='utf-8') as stream:
        return list(map(json.loads,stream))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Preserve existing evidence')
    folder = args.run/'contrast_output'
    freeze = json.loads((folder/'input_freeze.json').read_text(encoding='utf-8'))
    assert freeze['source_sha256'] == source_manifest()
    packets = {r['request_key']:r for r in rows(folder/'current_packets.jsonl.gz')}
    final = {r['request_key']:r for r in rows(folder/'contrast_results.jsonl.gz')}
    native = [r for p in sorted(folder.glob('call_*_native.jsonl.gz')) for r in rows(p)]
    primary = {r['request_key']:r for r in native if r['attempt']==0}
    assert primary.keys() == packets.keys() == final.keys()
    samplers = {}
    for path in sorted(folder.glob('call_*_sampling.json')):
        call = json.loads(path.read_text(encoding='utf-8'))
        for key,sampler in zip(call['request_keys'],call['before_llm_generate']):
            samplers.setdefault(key,sampler)
    details, aggregate = [], {arm:Counter() for arm in freeze['methods']}
    for key,packet in packets.items():
        observed = primary[key]['native']
        assert digest(packet['messages']) == primary[key]['prompt_sha256']
        assert digest(packet['token_ids']) == primary[key]['token_ids_sha256']
        assert observed['prompt_token_ids'] == packet['token_ids']
        expected = generation_schema('catalog_scope',len(packet['spans']),packet['items'],
                                     schema_order=packet['generation'].get('schema_order'))
        actual = samplers[key]['structured_outputs']['json']
        assert actual == expected and list(actual['properties']) == list(expected['properties'])
        assert digest(actual) == packet['generation_schema_sha256']
        result = final[key]
        assert result['attempt']==0, 'Inspect recovery separately before claiming a primary-only pilot'
        assert hashlib.sha256(observed['raw_text'].encode()).hexdigest() == result['response']['raw_output_sha256']
        obj = json.loads(result['response']['text'])
        assert list(obj) == list(expected['properties'])
        counts = aggregate[packet['arm']]
        counts['primary_returns'] += 1
        counts['output_tokens'] += len(observed['output_token_ids'])
        counts['length_finishes'] += observed['finish_reason']=='length'
        counts['whitespace_stalls'] += bool(result['response'].get('generation_stall'))
        meaningful = any(c.isalnum() for c in obj['task_summary'])
        counts['summary_has_content'] += meaningful
        counts['punctuation_only_summary'] += not meaningful
        counts['listed_category'] += obj['catalog_relation']=='listed_category'
        counts['unresolved_scope'] += obj['unresolved_scope']
        details.append({'id':packet['record_id'],'arm':packet['arm'],'task_summary':obj['task_summary'],
            'summary_has_content':meaningful,'raw_output_sha256':result['response']['raw_output_sha256'],
            'model_scope':obj,'gate':result['details']['gate']})
    pairs = []
    for record in rows(folder/'current_inputs.jsonl.gz'):
        pair = [next(p for p in packets.values() if p['record_id']==record['id'] and p['arm']==arm)
                for arm in ('control','facts_first')]
        for key in ('messages','token_ids','spans','source_search','catalog_scope','schema_sha256'):
            assert pair[0][key] == pair[1][key]
        sampled = [copy.deepcopy(samplers[p['request_key']]) for p in pair]
        for sampler in sampled:
            sampler['structured_outputs'].pop('json')
        assert sampled[0] == sampled[1], 'Sampler changed beyond generation JSON order'
        pairs.append({'id':record['id'],'inputs_equal':True,'sampler_equal_except_schema':True,
            'identical_answer':final[pair[0]['request_key']]['response']['text'] == final[pair[1]['request_key']]['response']['text']})
    report = {'kind':'native_scope_order_pilot_audit','source_sha256':freeze['source_sha256'],
        'classification_labels_read':False,'new_model_calls':0,'notices':len(pairs),
        'native_returns':len(native),'primary_returns':len(primary),'retry_returns':len(native)-len(primary),
        'aggregates':aggregate,'details':details,'pairs':pairs,
        'source_input_equality_verified':True,'actual_sampler_equality_except_schema_verified':True,
        'output_property_order_verified':True,
        'limitation':'Schema-valid summaries can contain only punctuation. Greater resolved-flag or source-promotion counts alone do not establish semantic improvement or final F1 gain.'}
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'aggregates':aggregate,'paired_verification':'PASS'},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
