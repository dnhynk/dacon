"""Separate observed raw judgments, optional fact consumption and runtime costs."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import restored, parse_error
from submission.pps.data import read_csv
from submission.pps.pipeline import parse_output
from submission.runtime import Journal, sha256
from tools.run_runtime_revival import read, rows
from tools.score_integrated_comparison import compact


def producers(recipe, packets, consumed):
    """Track the actual canonical assembly order and optional abstentions."""
    result, independent = {}, {}
    for families in ({'A', 'Q'}, {'L'}, {'W'}):
        for key in recipe:
            packet = packets[key]
            if packet['family'] not in families:
                continue
            value = consumed[key]['row']
            if value is None:
                continue
            for item, bit in value.items():
                if not re.fullmatch(r'v(?:[1-9]|1[0-9]|2[0-4])', item):
                    continue
                loc = packet['record_id'], item
                result[loc] = key
                if packet['batch'] in {'A1', 'A10', 'A19', 'L19'}:
                    independent[loc] = key
    return result, independent


def analyze(prepared, run, consumed_path, scores, output):
    freeze = read(consumed_path/'prediction_freeze.json')
    scored = read(scores/'report.json')
    if (scored['consumed_freeze_sha256'] != sha256(consumed_path/'prediction_freeze.json')
            or freeze['prepared_sha256'] != sha256(prepared/'input_freeze.json')
            or freeze['recipes_sha256'] != sha256(prepared/'recipes.json')):
        raise ValueError('Scoring or preparation identity differs')
    journal = Journal(output)
    packets = {p['request_key']: p for p in rows(prepared/'primary_packets.jsonl.gz')+rows(prepared/'probe_packets.jsonl.gz')}
    records = {r['id']: r for r in rows(prepared/'current_inputs.jsonl.gz')}
    consumed = {r['request_key']: r for r in rows(consumed_path/'consumed.jsonl.gz')}
    selected = {r['request_key']: r for part in ('normal', 'probes') for r in rows(run/part/'resolved_responses.jsonl.gz')}
    recipes = read(prepared/'recipes.json')
    raw = {}
    for key, answer in selected.items():
        packet, response = packets[key], answer['response']
        if parse_error(packet, response) is not None:
            raw[key] = None
        elif packet['generation']['response_format'] == 'specification_candidates':
            raw[key] = {'v9': json.loads(response['text'])['judgment']['v']}
        elif packet['generation']['response_format'] in {'catalog_scope', 'software_refs'}:
            raw[key] = None  # These formats assert facts, not final model bits.
        else:
            values, _ = parse_output(response['text'], restored(packet)['spans'], tuple(packet['items']), rec=records[packet['record_id']])
            raw[key] = {f'v{i}': values[i-1] for i in packet['items']}
    observations, batches = {}, []
    for part in ('normal', 'probes'):
        input_counts = {}
        for path in sorted((run/part).glob('call_*_requests.jsonl.gz')):
            batch, attempt = map(int, path.name.split('_')[1:3])
            batch_keys = []
            for packet in rows(path):
                key = packet['request_key']; batch_keys.append(key)
                input_counts[key, attempt] = len(packet['token_ids'])
            timing = run/part/f'call_{batch:05d}_{attempt}.json'
            if timing.is_file():
                batches.append({'part': part, 'batch': batch, 'attempt': attempt,
                    'profiles': dict(Counter(packets[k]['batch'] for k in batch_keys)),
                    'requests': len(batch_keys), **read(timing)})
        for path in sorted((run/part).glob('call_*_responses.jsonl.gz')):
            for answer in rows(path):
                pair = answer['request_key'], answer['attempt']
                if pair in observations:
                    raise ValueError('Duplicated recorded attempt')
                response = answer['response']
                observations[pair] = {'input_tokens': input_counts[pair], 'output_tokens': response['output_tokens'],
                    'cached_input_tokens': response.get('cached_input_tokens'), 'part': part,
                    'profile': packets[pair[0]]['batch']}
    def counts(keys):
        observed = [v for (k, _), v in observations.items() if k in keys]
        return {'observed_calls_including_recovery': len(observed),
            'input_tokens': sum(v['input_tokens'] for v in observed),
            'output_tokens': sum(v['output_tokens'] for v in observed),
            'cache_observed_calls': sum(type(v['cached_input_tokens']) is int for v in observed),
            'cached_input_tokens': sum(v['cached_input_tokens'] for v in observed if type(v['cached_input_tokens']) is int)}
    policies = {}
    for name, recipe in recipes.items():
        entry = freeze['predictions'][name]
        if entry['status'] != 'complete':
            policies[name] = {'status': 'incomplete_no_analysis'}
            continue
        prediction = consumed_path/entry['path']
        if sha256(prediction) != entry['sha256']:
            raise ValueError('Frozen prediction changed')
        final = {r['id']: r for r in read_csv(prediction)}
        actual, independent = producers(recipe, packets, consumed)
        if len(actual) != len(records)*24:
            raise ValueError('Incomplete producer attribution')
        for loc, key in actual.items():
            if int(final[loc[0]][loc[1]]) != consumed[key]['row'][loc[1]]:
                raise ValueError('Producer does not reproduce actual final bit')
        metrics = read(scores/(name+'_metrics.json'))['candidate']
        errors = []
        for error in metrics['errors']:
            loc = error['id'], error['item']; key = actual[loc]
            raw_bit = (raw.get(key) or {}).get(error['item'])
            parent = independent.get(loc)
            base_bit = consumed[parent]['row'][error['item']] if parent else None
            errors.append({**error, 'request_key': key, 'profile': packets[key]['batch'],
                'format': packets[key]['generation']['response_format'], 'raw_model_bit': raw_bit,
                'raw_judgment_available': raw_bit is not None,
                'introduced_by_raw_bit_consumption': None if raw_bit is None else raw_bit == error['truth'],
                'independent_request_key': parent, 'independent_cpu_bit': base_bit,
                'optional_update_replaced_correct_independent_bit': key != parent and base_bit == error['truth'],
                'typed_fact_response_has_no_model_violation_bit': raw_bit is None and packets[key]['family'] in {'Q', 'W'}})
        policies[name] = {'status': 'complete', **compact(metrics),
            **counts(set(recipe)), 'errors': errors,
            'raw_judgment_error_count': sum(e['raw_judgment_available'] for e in errors),
            'already_wrong_in_raw': sum(e['raw_judgment_available'] and not e['introduced_by_raw_bit_consumption'] for e in errors),
            'introduced_by_raw_bit_consumption': sum(e['introduced_by_raw_bit_consumption'] is True for e in errors),
            'typed_fact_errors_without_raw_violation_bit': sum(e['typed_fact_response_has_no_model_violation_bit'] for e in errors),
            'standalone_execution': entry['standalone_normal_execution']}
    profile_cost = {}
    for part in ('normal', 'probes'):
        for profile in sorted({v['profile'] for v in observations.values() if v['part'] == part}):
            keys = {k for (k, _), v in observations.items() if v['part'] == part and v['profile'] == profile}
            relevant = [b for b in batches if b['part'] == part and set(b['profiles']) == {profile}]
            profile_cost[part+'/'+profile] = {**counts(keys), 'recorded_batches': len(relevant),
                'batch_seconds': sum(b['seconds'] for b in relevant)}
    result = {'consumed_freeze_sha256': sha256(consumed_path/'prediction_freeze.json'),
        'scores_report_sha256': sha256(scores/'report.json'), 'policies': policies, 'profile_cost': profile_cost,
        'normal_run_report': read(run/'normal/run_report.json'), 'controller_report': read(run/'complete.json'),
        'new_model_calls': 0, 'labels_reopened': False,
        'limitations': 'Observed response attribution, not a causal claim about nondeterminism. Typed Q/W facts have no raw violation bit. Only normal wall time is standalone. A100 measurements do not certify L40S1853 cost.'}
    if (run/'normal/engine.json').is_file():
        result['engine'] = read(run/'normal/engine.json')
    journal.save('report.json', result)
    return {'policies': len(policies), 'profile_cost': profile_cost, 'new_model_calls': 0, 'labels_reopened': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prepared', 'run', 'consumed', 'scores', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.prepared, args.run, args.consumed, args.scores, args.output), ensure_ascii=False, indent=2))
