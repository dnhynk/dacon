"""Account for observed call costs and raw/CPU errors after prediction freezing.

This reads completed metric artifacts rather than reopening label files. It
does not generate responses, rewrite predictions, or choose per-item policies.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import restored
from submission.pps.data import read_csv
from submission.pps.pipeline import parse_output


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def rows(path):
    with gzip.open(path, 'rt', encoding='utf8') as stream:
        for line in stream:
            yield json.loads(line)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def analyze(prepared, run, consumed, scores, output):
    if output.exists():
        raise ValueError('Preserve earlier analyses; use a fresh output path')
    freeze = read(consumed / 'prediction_freeze.json')
    scored = read(scores / 'report.json')
    if (scored['consumed_freeze_sha256'] != sha(consumed / 'prediction_freeze.json')
            or freeze['applied_recipes_sha256'] != sha(consumed / 'applied_recipes.json')
            or freeze['prepared_freeze_sha256'] != sha(prepared / 'input_freeze.json')):
        raise ValueError('Prediction, scoring, or preparation identity differs')
    selected_path = run / ('resolved_responses.jsonl.gz'
                           if (run / 'generation_summary.json').is_file()
                           else 'resolved_responses_partial.jsonl.gz')
    if sha(selected_path) != freeze['resolved_responses_sha256']:
        raise ValueError('Selected response ledger changed')
    aliases = read(prepared / 'recipes.json')['native_aliases']
    packets = {p['request_key']: p for p in rows(prepared / 'canonical_packets.jsonl.gz')}
    originals = {r['id']: r for r in rows(prepared / 'current_inputs.jsonl.gz')}
    selected = {r['request_key']: r for r in rows(selected_path)}
    consumed_rows = {r['request_key']: r for r in rows(consumed / 'consumed.jsonl.gz')}
    recipes = read(consumed / 'applied_recipes.json')
    attempts = {}
    input_counts = {}
    for path in sorted(run.glob('call_*_requests.jsonl.gz')):
        attempt = int(path.name.split('_')[2])
        for p in rows(path):
            input_counts[p['request_key'], attempt] = len(p['token_ids'])
    for path in sorted(run.glob('call_*_responses.jsonl.gz')):
        for r in rows(path):
            pair = r['request_key'], r['attempt']
            attempts[pair] = dict(input_tokens=input_counts[pair],
                output_tokens=r['response']['output_tokens'],
                cached_input_tokens=r['response'].get('cached_input_tokens'))
    raw = {}
    for key, packet in packets.items():
        native = aliases[key]
        if native not in selected:
            continue
        response = selected[native]['response']
        if packet['generation']['response_format'] == 'specification_candidates':
            obj = json.loads(response['text'])
            raw[key] = {'v9': obj['judgment']['v']}
        else:
            values, _ = parse_output(response['text'], restored(packet)['spans'],
                tuple(packet['items']), rec=originals[packet['record_id']])
            raw[key] = {f'v{k}': values[k-1] for k in packet['items']}
    report = {'prediction_freeze_sha256': sha(consumed / 'prediction_freeze.json'),
              'scores_report_sha256': sha(scores / 'report.json'),
              'policies': {}, 'new_model_calls': 0, 'labels_reopened': False}
    for name, recipe in recipes.items():
        prediction = freeze['predictions'][name]
        if prediction['status'] != 'complete':
            report['policies'][name] = {'status': 'incomplete_no_analysis'}
            continue
        path = consumed / prediction['path']
        if sha(path) != prediction['sha256']:
            raise ValueError('Frozen prediction changed: ' + name)
        final = {r['id']: r for r in read_csv(path)}
        producers = {}
        # Match canonical A overlays, then the uniform L replacement.
        for family in ('A', 'L'):
            for key in recipe:
                packet = packets[key]
                if packet['family'] != family:
                    continue
                for item in ((20,) if family == 'L' else packet['items']):
                    if f'v{item}' in (consumed_rows[key]['row'] or {}):
                        producers[packet['record_id'], f'v{item}'] = key
        if len(producers) != len(originals) * 24:
            raise ValueError('Incomplete producer attribution: ' + name)
        for (rid, item), key in producers.items():
            if int(final[rid][item]) != int(consumed_rows[key]['row'][item]):
                raise ValueError('Attribution does not reproduce the frozen final bit')
        metric = read(scores / (name + '_metrics.json'))['candidate']
        errors = []
        for error in metric['errors']:
            key = producers[error['id'], error['item']]
            packet = packets[key]
            value = raw[key][error['item']]
            errors.append({**error, 'raw_model_bit': value,
                'introduced_by_consumer': value == error['truth'],
                'request_key': key, 'native_request_key': aliases[key],
                'role': packet['experiment_role'],
                'format': packet['generation']['response_format'],
                'attempt': selected[aliases[key]]['attempt']})
        native_keys = {aliases[key] for key in recipe}
        observed = {pair: count for pair, count in attempts.items() if pair[0] in native_keys}
        report['policies'][name] = {
            'status': 'complete', 'macro_f1': metric['macro_f1'],
            'logical_calls': len(recipe), 'unique_native_calls': len(native_keys),
            'observed_attempts_including_recovery': len(observed),
            'observed_input_tokens': sum(v['input_tokens'] for v in observed.values()),
            'observed_output_tokens': sum(v['output_tokens'] for v in observed.values()),
            'cache_observed_attempts': sum(type(v['cached_input_tokens']) is int for v in observed.values()),
            'cached_input_tokens': sum(v['cached_input_tokens'] for v in observed.values()
                                       if type(v['cached_input_tokens']) is int),
            'logical_roles': dict(Counter(packets[key]['experiment_role'] for key in recipe)),
            'errors': errors, 'error_count': len(errors),
            'already_wrong_in_raw': sum(not e['introduced_by_consumer'] for e in errors),
            'introduced_by_consumer': sum(e['introduced_by_consumer'] for e in errors),
            'standalone_elapsed_seconds': None,
        }
    batch_times = [read(p) for p in sorted(run.glob('call_*.json'))
                   if re.fullmatch(r'call_\d+_\d+\.json', p.name)]
    report['shared_comparison_cost'] = {
        'measured_batches': len(batch_times),
        'batch_seconds': sum(b['seconds'] for b in batch_times),
        'observed_attempts': len(attempts),
        'input_tokens': sum(v['input_tokens'] for v in attempts.values()),
        'output_tokens': sum(v['output_tokens'] for v in attempts.values()),
        'cache_observed_attempts': sum(type(v['cached_input_tokens']) is int for v in attempts.values()),
        'cached_input_tokens': sum(v['cached_input_tokens'] for v in attempts.values()
                                   if type(v['cached_input_tokens']) is int),
        'generation_summary': read(run / 'generation_summary.json')
            if (run / 'generation_summary.json').is_file() else None,
    }
    report['limitations'] = (
        'Raw-versus-CPU attribution is within this observed run, not a causal account '
        'of model nondeterminism. Policies share fixed same-notice inputs. Token counts '
        'are observed; per-policy standalone elapsed time and L40S1853 are unmeasured. '
        'No per-record or per-item policy selection is performed.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prepared', 'run', 'consumed', 'scores', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    a = parser.parse_args()
    result = analyze(a.prepared, a.run, a.consumed, a.scores, a.output)
    print(json.dumps({name: {k: value[k] for k in ('status', 'error_count',
                      'already_wrong_in_raw', 'introduced_by_consumer') if k in value}
                      for name, value in result['policies'].items()}, indent=2))
