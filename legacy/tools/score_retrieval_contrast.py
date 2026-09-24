"""Audit fresh search-arm inputs and join complete partial-item observations.

Every arm replaces the same predeclared fields of one immutable saved baseline.
This mixed-response diagnostic is never reported as a fresh full-set score.
"""
import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import B4Pipeline, digest, parse_error
from submission.pps.data import read_csv, write_csv
from submission.pps.generation_contract import generation_schema
from submission.runtime import source_manifest
from tools.evaluate import compare
from tools.run_retrieval_contrast import verify_packet_source
from tools.score_catalog_scope import rows, sha, verified_replay_path


def join_items(baseline, packets, observations):
    index = {r['id']: copy.deepcopy(r) for r in baseline}
    expected = {p['request_key']: p for p in packets}
    observed = {r['request_key']: r for r in observations}
    if len(index) != len(baseline) or len(expected) != len(packets) or len(observed) != len(observations) or observed.keys() != expected.keys():
        raise ValueError('Unique and complete baseline, packet and observation keys are required')
    if any(r['parse_error'] is not None for r in observations):
        return None, {'status': 'withheld_unresolved_format'}
    written, changes = set(), []
    for key, p in expected.items():
        r = observed[key]
        rid, items = p['record_id'], p['items']
        if rid not in index or r['record_id'] != rid or r['arm'] != p['arm'] or r['items'] != items:
            raise ValueError('Observation does not belong to its prepared notice and item set')
        fields = {f'{prefix}{k}' for k in items for prefix in ('v', 'e')}
        if not isinstance(r['row'], dict) or set(r['row']) != fields:
            raise ValueError('A factored consumer must return exactly its declared fields')
        for k in items:
            if (rid, k) in written:
                raise ValueError('Two cases cannot choose competing predictions for the same notice/item')
            written.add((rid, k))
            v, e = f'v{k}', f'e{k}'
            value, evidence = r['row'][v], r['row'][e]
            if type(value) is not int or value not in (0, 1) or not isinstance(evidence, str):
                raise ValueError('Invalid value/evidence pair')
            if int(index[rid][v]) != value:
                changes.append({'id': rid, 'item': v, 'before': int(index[rid][v]), 'after': value})
            index[rid][v], index[rid][e] = str(value), evidence
    return [index[r['id']] for r in baseline], {
        'status': 'complete_predeclared_field_join', 'fresh_fields': len(written), 'changed_bits': changes}


def audit(run):
    folder = run/'contrast_output'
    freeze = json.loads((folder/'input_freeze.json').read_text(encoding='utf-8'))
    summary = json.loads((folder/'contrast_summary.json').read_text(encoding='utf-8'))
    assert freeze['source_sha256'] == source_manifest() and summary['source_unchanged'] and not summary['labels_read']
    packets = rows(folder/'current_packets.jsonl.gz')
    final = rows(folder/'contrast_results.jsonl.gz')
    primary_rows = [r for p in sorted(folder.glob('primary_*.jsonl.gz')) for r in rows(p)]
    native = [r for p in sorted(folder.glob('call_*_native.jsonl.gz')) for r in rows(p)]
    primary = {r['request_key']: r for r in native if r['attempt'] == 0}
    finals = {r['request_key']: r for r in final}
    first = {r['request_key']: r for r in primary_rows}
    assert len(packets) == len(final) == len(primary) == len(first) == freeze['primary_requests']
    assert primary.keys() == finals.keys() == first.keys() == {p['request_key'] for p in packets}
    samplers = {}
    for path in sorted(folder.glob('call_*_sampling.json')):
        call = json.loads(path.read_text(encoding='utf-8'))
        assert len(call['request_keys']) == len(call['before_llm_generate'])
        for key, sampler in zip(call['request_keys'], call['before_llm_generate']):
            samplers.setdefault(key, sampler)
    records = {r['id']: r for r in rows(folder/'current_inputs.jsonl.gz')}
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    pipeline = B4Pipeline(ROOT/'data_open/data', tokenizer, input_strategy='audited')
    aggregate = {arm: Counter() for arm in freeze['arms']}
    repeat_pairs = freeze.get('repeated_pairs', [])
    repeated_keys = {pair['repeat'] for pair in repeat_pairs}
    assert len(repeated_keys) == len(repeat_pairs)
    expected_keys = {p['request_key'] for p in packets}
    assert repeated_keys <= expected_keys and all(pair['primary'] in expected_keys for pair in repeat_pairs)
    for p in packets:
        key = p['request_key']
        assert p['generation']['response_format'] == 'factored'
        n, r = primary[key]['native'], first[key]
        assert digest(p['messages']) == primary[key]['prompt_sha256']
        assert digest(p['token_ids']) == primary[key]['token_ids_sha256']
        assert n['prompt_token_ids'] == p['token_ids']
        expected = generation_schema('factored', len(p['spans']), p['items'])
        assert samplers[key]['structured_outputs']['json'] == expected
        assert digest(expected) == p['generation_schema_sha256']
        import hashlib
        assert hashlib.sha256(n['raw_text'].encode()).hexdigest() == r['response']['raw_output_sha256']
        verify_packet_source(p, records[p['record_id']], tokenizer)
        assert parse_error(p, finals[key]['response']) == finals[key]['parse_error']
        if finals[key]['parse_error'] is None:
            row, detail = pipeline.consume(records[p['record_id']], p, finals[key]['response'])
            assert row == finals[key]['row'] and detail == finals[key]['details'], 'Current CPU consumer differs from the frozen GPU run'
        a = aggregate.setdefault(p['arm'], Counter())
        a['primary_returns'] += 1
        a['first_valid'] += r['parse_error'] is None
        a['selected_retry'] += finals[key]['attempt'] > 0
        a['length_finishes'] += n['finish_reason'] == 'length'
        a['whitespace_stalls'] += bool(r['response'].get('generation_stall'))
        a['output_tokens'] += len(n['output_token_ids'])
    pairs = []
    for case in freeze['included_cases']:
        pair = [next(p for p in packets if p['case'] == case and p['arm'] == arm) for arm in freeze['arms']]
        p = pair[0]
        before = copy.deepcopy(samplers[p['request_key']])
        del before['structured_outputs']['json']
        for q in pair[1:]:
            after = copy.deepcopy(samplers[q['request_key']])
            del after['structured_outputs']['json']
            assert before == after
            assert p['items'] == q['items'] and p['generation'] == q['generation'] and p['messages'][0] == q['messages'][0]
            assert p['source_search']['source_token_budget'] == q['source_search']['source_token_budget']
            if not freeze.get('allow_adaptive_queries', False):
                assert p['source_search']['diagnostics']['queries'] == q['source_search']['diagnostics']['queries']
        pairs.append({'case': case, 'same_system_generation_sampling_and_budget': True,
            'source_tokens': {p['arm']: p['source_search']['source_tokens'] for p in pair},
            'input_tokens': {p['arm']: len(p['token_ids']) for p in pair},
            'judgments': {p['arm']: finals[p['request_key']]['raw_values'] for p in pair},
            'consumed': {p['arm']: finals[p['request_key']]['row'] for p in pair}})
    repeated_observations = []
    by_key = {p['request_key']: p for p in packets}
    for pair in repeat_pairs:
        a, b = (by_key[pair[k]] for k in ('primary', 'repeat'))
        assert a['messages'] == b['messages'] and a['token_ids'] == b['token_ids']
        assert a['generation'] == b['generation'] and a['source_sha256'] == b['source_sha256']
        assert samplers[a['request_key']] == samplers[b['request_key']]
        repeated_observations.append({**pair,
            'same_raw_text': primary[a['request_key']]['native']['raw_text'] == primary[b['request_key']]['native']['raw_text'],
            'same_raw_values': finals[a['request_key']].get('raw_values') == finals[b['request_key']].get('raw_values'),
            'same_consumed_row': finals[a['request_key']]['row'] == finals[b['request_key']]['row'],
            'primary_parse_error': finals[a['request_key']]['parse_error'],
            'repeat_parse_error': finals[b['request_key']]['parse_error'],
            'better_response_selected': False})
    return freeze, packets, final, records, {
        'kind': 'native_paired_retrieval_input_audit', 'classification_labels_read': False,
        'source_sha256': freeze['source_sha256'], 'aggregates': aggregate, 'pairs': pairs,
        'native_returns': len(native), 'primary_returns': len(primary), 'retry_returns': len(native)-len(primary),
        'cpu_consumer_reproduced': True, 'generation_seconds': summary['generation_seconds'],
        'repeated_observations': repeated_observations,
        'official_macro_f1': None, 'full_fresh_inference': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--baseline-run', type=Path, required=True)
    parser.add_argument('--baseline-replay', type=Path, required=True)
    parser.add_argument('--labels', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    freeze, packets, final, records, audit_report = audit(args.run)
    def save(name, obj):
        with (args.output/name).open('x', encoding='utf-8') as stream:
            json.dump(obj, stream, ensure_ascii=False, indent=2)
    save('generation_audit.json', audit_report)
    baseline_path = verified_replay_path(args.baseline_run, args.baseline_replay, freeze['source_sha256'])
    baseline = read_csv(baseline_path)
    all_records = rows(args.baseline_run/'current_inputs.jsonl.gz')
    all_by_id = {r['id']: r for r in all_records}
    assert all(all_by_id[rid] == r for rid, r in records.items())
    predictions, measurements = {}, []
    for arm in freeze['arms']:
        assert re.fullmatch(r'[A-Za-z0-9_]+', arm)
        part = [r for r in final if r['arm'] == arm]
        joined, join = join_items(baseline, [p for p in packets if p['arm'] == arm], part)
        detail = {'arm': arm, 'join': join, **audit_report['aggregates'][arm]}
        if joined is not None:
            path = args.output/(arm+'.csv')
            write_csv(path, joined, recs=all_records, require_positive_evidence=False)
            predictions[arm] = path
            detail.update(prediction=path.as_posix(), prediction_sha256=sha(path))
        measurements.append(detail)
    # All new complete predictions are frozen before opening classification labels.
    save('prediction_freeze.json', {'kind': 'saved_baseline_plus_fresh_predeclared_fields',
        'source_sha256': freeze['source_sha256'], 'baseline_prediction_sha256': sha(baseline_path),
        'files': {p.name: sha(p) for p in predictions.values()}, 'classification_labels_read': False})
    for detail in measurements:
        arm = detail['arm']
        if arm not in predictions:
            detail['mixed_macro_f1'] = None
            continue
        refs = [baseline_path, *[p for other, p in predictions.items() if other != arm], *args.baseline]
        evaluation = compare(args.labels, predictions[arm], refs)
        save(arm+'_metrics.json', evaluation)
        score = evaluation['candidate']
        detail.update(mixed_macro_f1=score['macro_f1'], fp=sum(r['fp'] for r in score['per_item'].values()),
            fn=sum(r['fn'] for r in score['per_item'].values()), comparisons=evaluation['comparisons'])
    report = {'kind': 'saved_baseline_current_cpu_plus_fresh_paired_retrieval_fields',
        'full_fresh_inference': False, 'official_macro_f1': None, 'default_route_adopted': False,
        'source_sha256': freeze['source_sha256'], 'baseline_prediction': baseline_path.as_posix(),
        'baseline_prediction_sha256': sha(baseline_path), 'labels_sha256': sha(args.labels),
        'cases': freeze['cases'], 'notices': freeze['notices'], 'primary_requests': len(packets),
        'generation_seconds': audit_report['generation_seconds'], 'measurements': measurements,
        'limitations': f"{freeze['cases']} predeclared exposed review cases, all retained. All arms replace the same declared fields. Mixed saved/fresh predictions are not full fresh inference, held-out performance or an official score. No best-arm-per-item choice or semantic rerolls."}
    save('report.json', report)
    print(json.dumps([{k: m.get(k) for k in ('arm', 'mixed_macro_f1', 'fp', 'fn', 'first_valid', 'selected_retry')} for m in measurements], indent=2))


if __name__ == '__main__':
    main()
