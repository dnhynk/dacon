"""Freeze an observed-context comparison on a preserved source-only cohort.

This does not read prior answers, labels, model weights or a GPU. Both arms need
new inference; synthetic response lengths are preparation diagnostics only.
"""
import argparse
import copy
import dataclasses
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import digest, parse_error
from submission.pps.generation_contract import generation_schema, prepared_preflight
from submission.pps.knowledge import Knowledge
from submission.pps.prompts import Config, output_schema
from submission.pps import specification_candidate_review as review, specification_context as context
from submission.runtime import source_manifest
from tools.audit_specification_candidates import unknown_response
from tools.prepare_specification_contrast import read_rows, write_rows, save, sha
from tools.score_specification_contrast import primary_and_repeats, verify_context_pair


def synthetic_response(plan, *, dense=False):
    base = review._candidate_plan(plan)
    payload = unknown_response(base)
    for answer in payload[review.NAME].values():
        answer.update(permission_attribute='unknown', permission_effect='unknown')
    if 'source_context' in plan:
        observed = plan['source_context']
        payload = {context.NAME: {p['key']: {'relevance': 'unknown', 'target_candidates': [],
            'target_level': 'unknown', 'attribute': 'unknown', 'effect': 'unknown', 'target_sources': []}
            for p in observed['permission_observations']}, **payload}
    payload.update(unresolved='원문 관계 미확정', judgment={'reason': '원문을 추가 확인해야 함', 'v': 0, 'e': 0})
    if dense:
        refs = list(range(max(1, plan['unit_count']-5), plan['unit_count']+1))
        for c in base['candidates']:
            payload[review.NAME][c['key']].update(
                specificity='named' if c['value_source'] else 'unknown', role='replacement_component',
                requirement='mandatory', scope_sources=refs, permission_scope='whole_product_only',
                permission_sources=refs, permission_attribute='manufacturer_consistency',
                permission_effect='conditional', exception_sources=refs)
        for answer in payload.get(context.NAME, {}).values():
            answer.update(relevance='permission_or_requirement',
                target_candidates=[c['key'] for c in base['candidates']], target_level='whole_product',
                attribute='manufacturer_consistency', effect='conditional', target_sources=refs)
        payload['unresolved'] = '관계 확인 필요. '*12
        payload['judgment']['reason'] = '관계 확인 필요. '*11
    return payload


def prepare(source_prepared, output, *, batch_size=12):
    if output.exists():
        raise ValueError('Choose a fresh output directory; prepared evidence is immutable')
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError('Batch size must be a positive integer')
    files = [source_prepared/name for name in ('contrast_inputs.jsonl.gz', 'contrast_packets.jsonl.gz', 'contrast_freeze.json')]
    old = json.loads(files[2].read_text('utf8'))
    assert sha(files[0]) == old['inputs_sha256'] and sha(files[1]) == old['packets_sha256']
    raw_records, all_old = read_rows(files[0]), read_rows(files[1])
    records = {r['id']: r for r in raw_records}
    assert len(records) == len(raw_records) == old['notices']
    primary, repeated = primary_and_repeats(all_old, old)
    controls = [p for p in primary if p['arm'] == review.FORMAT]
    assert len(controls) == len(records) and {p['record_id'] for p in controls} == records.keys()
    old_index = {p['request_key']: p for p in all_old}
    repeat_ids = {old_index[p['primary']]['record_id'] for p in repeated}
    source = source_manifest()
    frozen_inputs = {p.resolve().as_posix(): sha(p) for p in files}
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    config = Config.load(ROOT/'submission/model/config.json')
    knowledge = Knowledge(ROOT/'data_open/data')
    arms = [review.FORMAT, review.CONTEXT_ARM]
    packets, comparisons = [], []
    for index, prior in enumerate(controls):
        rid, rec = prior['record_id'], records[prior['record_id']]
        selected = prior['source_search']
        assert selected['source_tokens'] <= old['source_token_budget']
        pair = review.context_prompts(rec, knowledge, config, tokenizer, selected)
        control = pair[review.FORMAT]
        assert control['messages'] == prior['messages'] and control['token_ids'] == prior['token_ids'], 'Control changed'
        assert control['generation'] == prior['generation']
        assert [dataclasses.asdict(s) for s in control['spans']] == prior['spans']
        costs = {}
        for arm in arms[index % 2:] + arms[:index % 2]:
            prompt = pair[arm]
            spans = [dataclasses.asdict(s) for s in prompt['spans']]
            contract = generation_schema(review.FORMAT, len(spans), (9,),
                specification_inventory=prompt['specification_inventory'])
            packet = {'request_key': f'specctx:{rid}:{arm}', 'record_id': rid, 'case': rid,
                'arm': arm, 'family': 'A', 'items': [9], 'messages': prompt['messages'],
                'token_ids': prompt['token_ids'], 'prompt_sha256': digest(prompt['messages']),
                'token_ids_sha256': digest(prompt['token_ids']), 'source_sha256': digest(spans),
                'spans': spans, 'source_layout': 'finite_units', 'source_search': selected,
                'comparison_facts': None, 'coverage': prompt['coverage'], 'generation': prompt['generation'],
                'generation_schema_sha256': digest(contract),
                'schema_sha256': digest(output_schema(review.FORMAT, len(spans), (9,))),
                'specification_inventory': prompt['specification_inventory']}
            review.validate_prepared(rec, prompt)
            costs[arm] = {'input_tokens': len(prompt['token_ids'])}
            for dense in (False, True):
                obj = synthetic_response(prompt['specification_inventory'], dense=dense)
                text = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
                error = parse_error(packet, {'text': text, 'finish_reason': 'stop'})
                assert error is None, error
                costs[arm]['synthetic_dense_tokens' if dense else 'synthetic_unknown_tokens'] = len(tokenizer.encode(text, add_special_tokens=False))
            packets.append(packet)
        verify_context_pair(next(p for p in packets if p['record_id']==rid and p['arm']==arms[0]),
                            next(p for p in packets if p['record_id']==rid and p['arm']==arms[1]))
        comparisons.append({'record_id': rid, 'source_tokens': selected['source_tokens'],
            'control_matches_prior_messages_tokens_generation': True, 'costs': costs,
            'observed_context': pair[review.CONTEXT_ARM]['specification_inventory']['source_context'],
            'synthetic_outputs_are_not_model_measurements': True})
    repeats = []
    for packet in list(packets):
        if packet['record_id'] in repeat_ids:
            again = {**packet, 'request_key': packet['request_key']+':repeat', 'arm': packet['arm']+'_repeat'}
            packets.append(again)
            repeats.append({'primary': packet['request_key'], 'repeat': again['request_key']})
    preflight = prepared_preflight(packets)
    assert source_manifest() == source and all(sha(Path(p)) == h for p, h in frozen_inputs.items())
    output.mkdir(parents=True, exist_ok=False)
    write_rows(output/'contrast_inputs.jsonl.gz', raw_records)
    write_rows(output/'contrast_packets.jsonl.gz', packets)
    save(output/'source_context_inventory.json', comparisons)
    save(output/'generation_preflight.json', preflight)
    freeze = {'source_sha256': source, 'config': dataclasses.asdict(config),
        'source_preparation_sha256': frozen_inputs,
        'packets_sha256': sha(output/'contrast_packets.jsonl.gz'), 'inputs_sha256': sha(output/'contrast_inputs.jsonl.gz'),
        'call_plan': [{'number': i//batch_size, 'request_keys': [p['request_key'] for p in packets[i:i+batch_size]]}
                      for i in range(0, len(packets), batch_size)],
        'notices': len(records), 'cases': len(records), 'primary_requests': len(packets),
        'primary_comparison_requests': 2*len(records), 'arms': arms, 'repeated_pairs': repeats,
        'source_token_budget': old['source_token_budget'], 'source_method': old['source_method'],
        'source_tokens_per_case': {p['record_id']: p['source_tokens'] for p in comparisons},
        'input_tokens_total': sum(len(p['token_ids']) for p in packets),
        'input_tokens_max': max(len(p['token_ids']) for p in packets),
        'candidate_context_intervention': True, 'classification_labels_read': False, 'holdout_read': False,
        'new_model_calls': 0, 'gpu_allocated': False, 'official_score': None,
        'controls': 'Identical prior source-only cohort, original source budget, model and non-schema sampler. Control messages/tokens/generation unchanged. Context arm adds observed numbering and qualifier metadata plus required P reviews.',
        'limitation': 'Exposed development; no retrieval change, source expansion or new inference effect measured. Semantic links and final judgments remain model outputs.'}
    save(output/'contrast_freeze.json', freeze)
    print(json.dumps({'notices': len(records), 'requests': len(packets), 'original_source_budget': old['source_token_budget'],
        'observed_cue_contexts': sum(len(p['observed_context']['permission_observations']) for p in comparisons),
        'max_input_tokens': freeze['input_tokens_max'], 'synthetic_dense_max_tokens': max(
            c['synthetic_dense_tokens'] for p in comparisons for c in p['costs'].values()),
        'max_output_tokens': config.max_output_tokens, 'effective_grammars': preflight['unique_effective_schemas'],
        'gpu_allocated': False}, ensure_ascii=False))
    return freeze


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-prepared', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--batch-size', type=int, default=12)
    args = parser.parse_args()
    prepare(args.source_prepared, args.output, batch_size=args.batch_size)
