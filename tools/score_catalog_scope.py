"""Join each complete fresh scope arm to one fixed complete fresh baseline.

Semantic abstentions leave the baseline untouched. Missing/invalid responses
withhold the arm score; there is no per-record best-arm choice or zero filling.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import gzip
import hashlib
import json
from pathlib import Path, PureWindowsPath
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.data import read_csv, write_csv
from tools.evaluate import compare

SCOPE_ITEMS = tuple(range(10, 19))


def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verified_replay_path(baseline_dir, replay_dir, consumer_source):
    replay = json.loads((replay_dir/'freeze.json').read_text(encoding='utf-8'))
    assert replay['kind'] == 'saved_response_current_cpu_replay' and replay['new_model_calls'] == 0
    assert replay['source_sha256'] == consumer_source
    expected = {p.name: sha(p) for p in (baseline_dir/n for n in (
        'current_inputs.jsonl.gz', 'current_packets.jsonl.gz', 'resolved_responses.jsonl.gz'))}
    observed = replay['input_sha256']
    assert len(observed) == len(expected)
    assert {PureWindowsPath(p).name: h for p, h in observed.items()} == expected
    path = replay_dir/'B4.csv'
    assert replay['prediction_sha256']['B4.csv'] == sha(path)
    return path


def join_arm(baseline, observations, expected_ids):
    expected_ids = set(expected_ids)
    index = {row['id']: copy.deepcopy(row) for row in baseline}
    if len(index) != len(baseline) or not expected_ids <= index.keys():
        raise ValueError('Baseline IDs must be unique and contain the complete diagnostic cohort')
    found = {row['record_id']: row for row in observations}
    if len(found) != len(observations) or found.keys() != expected_ids:
        raise ValueError('Every selected notice must have exactly one result in this arm')
    if any(row['parse_error'] is not None for row in observations):
        return None, {'status': 'withheld_unresolved_format',
            'invalid': [row['request_key'] for row in observations if row['parse_error'] is not None]}
    changed = []
    valid_fields = {f'{p}{k}' for k in SCOPE_ITEMS for p in ('v', 'e')}
    for record_id, observation in found.items():
        if observation.get('raw_values') is not None or observation.get('model_judgment_present') is not False:
            raise ValueError('Scope model output is a fact proposal, never raw legal judgment bits')
        proposed = observation['row']
        if proposed is None:
            continue
        if not isinstance(proposed, dict) or set(proposed) - valid_fields:
            raise ValueError('Scope consumer may only propose items10..18')
        for k in SCOPE_ITEMS:
            v, e = f'v{k}', f'e{k}'
            if (v in proposed) != (e in proposed):
                raise ValueError('Each computed value must retain its evidence field')
            if v not in proposed:
                continue
            value = proposed[v]
            evidence = proposed[e]
            if type(value) is not int or value not in (0, 1) or not isinstance(evidence, str):
                raise ValueError('Computed value/evidence has an invalid type')
            if int(index[record_id][v]) != value:
                changed.append({'id': record_id, 'item': v, 'before': int(index[record_id][v]), 'after': value})
            index[record_id][v], index[record_id][e] = str(value), evidence
    return [index[row['id']] for row in baseline], {'status': 'complete_arm_join', 'changed_bits': changed,
        'abstentions': sum(row['row'] is None for row in observations),
        'computed_notices': sum(bool(row['row']) for row in observations)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True, help='Recovered root containing output/ and contrast_output/')
    parser.add_argument('--labels', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, action='append', default=[])
    parser.add_argument('--baseline-run', type=Path,
        help='Explicitly use a preserved complete baseline from a separate engine run')
    parser.add_argument('--baseline-replay', type=Path,
        help='Required with --baseline-run: current-consumer replay of exactly that baseline')
    args = parser.parse_args()
    if bool(args.baseline_run) != bool(args.baseline_replay):
        parser.error('--baseline-run and --baseline-replay must be specified together')
    args.output.mkdir(parents=True, exist_ok=False)
    separate_engine = args.baseline_run is not None
    baseline_dir, contrast_dir = args.baseline_run or args.run/'output', args.run/'contrast_output'
    full = json.loads((baseline_dir/'run_report.json').read_text(encoding='utf-8'))
    full_freeze = json.loads((baseline_dir/'input_freeze.json').read_text(encoding='utf-8'))
    freeze = json.loads((contrast_dir/'input_freeze.json').read_text(encoding='utf-8'))
    summary = json.loads((contrast_dir/'contrast_summary.json').read_text(encoding='utf-8'))
    assert full['mode'] == 'fixed_model' and full['source_unchanged'] and not full['invalid_responses']
    if not separate_engine:
        assert full_freeze['source_sha256'] == freeze['source_sha256']
    assert summary['source_unchanged'] and not summary['labels_read']
    original_baseline_path = baseline_dir/'submission.csv'
    baseline_path = original_baseline_path
    if separate_engine:
        baseline_path = verified_replay_path(baseline_dir, args.baseline_replay, freeze['source_sha256'])
    baseline = read_csv(baseline_path)
    inputs = rows(baseline_dir/'current_inputs.jsonl.gz')
    assert len(baseline) == len(inputs) == full['records']
    assert full['primary_requests'] == 4 * full['records']
    predictions = json.loads((baseline_dir/'prediction_freeze.json').read_text(encoding='utf-8'))
    assert predictions['files']['submission.csv'] == sha(original_baseline_path)
    packets = rows(contrast_dir/'current_packets.jsonl.gz')
    final = rows(contrast_dir/'contrast_results.jsonl.gz')
    primary = [r for p in sorted(contrast_dir.glob('primary_*.jsonl.gz')) for r in rows(p)]
    attempts = primary + [r for p in sorted(contrast_dir.glob('recovery_*.jsonl.gz')) for r in rows(p)]
    index = {p['request_key']: p for p in packets}
    assert len(index) == len(packets) == len(primary) == len(final) == freeze['primary_requests']
    assert {r['request_key'] for r in final} == {r['request_key'] for r in primary} == set(index)
    for row in final:
        packet = index[row['request_key']]
        assert packet['arm'] == row['arm'] and packet['record_id'] == row['record_id']
        assert packet['generation']['response_format'] == 'catalog_scope' and packet['items'] == list(SCOPE_ITEMS)
    arms = sorted({p['arm'] for p in packets})
    scope_inputs = rows(contrast_dir/'current_inputs.jsonl.gz')
    by_id = {r['id']: r for r in inputs}
    assert all(by_id[r['id']] == r for r in scope_inputs)
    selected = {r['id'] for r in scope_inputs}
    assert len(selected) == len(scope_inputs)
    baseline_metrics = compare(args.labels, baseline_path, args.baseline)
    with (args.output/'baseline_metrics.json').open('x', encoding='utf-8') as stream:
        json.dump(baseline_metrics, stream, ensure_ascii=False, indent=2)
    measurements = []
    for arm in arms:
        part = [r for r in final if r['arm'] == arm]
        first = [r for r in primary if r['arm'] == arm]
        joined, join = join_arm(baseline, part, selected)
        detail = {'arm': arm, 'primary': len(first), 'first_valid': sum(r['parse_error'] is None for r in first),
            'final_valid': sum(r['parse_error'] is None for r in part),
            'retry_calls': sum(r['arm'] == arm for r in attempts) - len(first),
            'scope_gates': dict(Counter((r['details'] or {}).get('gate') for r in part)), 'join': join}
        if joined is not None:
            output = args.output/(arm+'.csv')
            write_csv(output, joined, recs=inputs, require_positive_evidence=False)
            evaluation = compare(args.labels, output, [baseline_path, *args.baseline])
            with (args.output/(arm+'_metrics.json')).open('x', encoding='utf-8') as stream:
                json.dump(evaluation, stream, ensure_ascii=False, indent=2)
            score = evaluation['candidate']
            detail.update(macro_f1=score['macro_f1'], fp=sum(m['fp'] for m in score['per_item'].values()),
                fn=sum(m['fn'] for m in score['per_item'].values()),
                comparison_to_fixed_baseline=evaluation['comparisons'][str(baseline_path)],
                prediction=output.as_posix(), prediction_sha256=sha(output))
        else:
            detail['macro_f1'] = None
        measurements.append(detail)
    report = {'kind': ('saved_baseline_current_cpu_plus_fresh_scope_separate_engine' if separate_engine
                      else 'fresh_complete_baseline_plus_fixed_fresh_catalog_scope_arms'),
        'full_fresh_inference': not separate_engine, 'separate_engine_scope_diagnostic': separate_engine,
        'baseline_prediction': baseline_path.as_posix(),
        'baseline_inference_source_sha256': full_freeze['source_sha256'],
        'baseline_macro_f1': baseline_metrics['candidate']['macro_f1'], 'baseline_sha256': sha(baseline_path),
        'labels_sha256': sha(args.labels), 'source_sha256': freeze['source_sha256'],
        'measurements': measurements, 'full_records': full['records'], 'scope_notices': len(selected),
        'primary_calls_experiment': (0 if separate_engine else full['primary_requests']) + freeze['primary_requests'],
        'preserved_baseline_primary_calls': full['primary_requests'] if separate_engine else 0,
        'primary_calls_per_deployable_arm': full['primary_requests'] + len(selected),
        'generation_seconds_full': full['seconds'], 'generation_seconds_scope': summary['generation_seconds'],
        'source_tokens': freeze['source_tokens'], 'official_macro_f1': None, 'default_route_adopted': False,
        'limitations': 'Exposed development set, not held out. Each arm uses the same explicitly recorded baseline and exactly one scope response per selected notice. Scope arms share their recorded engine history. A separate-engine diagnostic joined to preserved baseline responses is a mixed-response measurement, not new full inference. Partial semantic decisions preserve baseline fields; unresolved formats withhold an arm score. Adoption and end-to-end evaluation throughput remain separate decisions.'}
    with (args.output/'report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'baseline_macro_f1': report['baseline_macro_f1'],
        'arms': [{k: m.get(k) for k in ('arm', 'macro_f1', 'fp', 'fn', 'first_valid', 'final_valid', 'scope_gates')} for m in measurements]}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
