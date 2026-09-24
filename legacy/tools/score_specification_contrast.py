"""Audit all native contract observations; freeze both baseline joins before labels.

Repeated observations are predetermined diagnostics and never replace primaries.
These are mixed V9 measurements on exposed development notices, not full fresh F1.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import B4Pipeline, digest, parse_error, restored
from submission.pps.data import read_csv, write_csv
from submission.pps.generation_contract import generation_schema
from submission.pps.prompts import token_ids
from submission.runtime import source_manifest
from tools.evaluate import compare
from tools.run_retrieval_contrast import verify_packet_source
from tools.score_catalog_scope import rows, sha, verified_replay_path
from tools.score_retrieval_contrast import join_items


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def unique(values, field='request_key'):
    result = {r[field]: r for r in values}
    if len(result) != len(values):
        raise ValueError('Duplicate observation identity')
    return result


def sampler_without_schema(sampler):
    value = copy.deepcopy(sampler)
    del value['structured_outputs']['json']
    return value


def primary_and_repeats(packets, freeze):
    """Select by the pre-inference manifest, never by generated content."""
    index = unique(packets)
    repeats = freeze['repeated_pairs']
    repeated = {r['repeat'] for r in repeats}
    if len(repeated) != len(repeats):
        raise ValueError('Repeated request declared twice')
    for r in repeats:
        first, again = index[r['primary']], index[r['repeat']]
        a = {k: v for k, v in first.items() if k not in ('request_key', 'arm')}
        b = {k: v for k, v in again.items() if k not in ('request_key', 'arm')}
        if (a != b or first['arm'] not in freeze['arms']
                or again['arm'] != first['arm'] + '_repeat'):
            raise ValueError('Repeated packet differs from its declared primary')
    primary = [p for p in packets if p['request_key'] not in repeated]
    if any(p['arm'] not in freeze['arms'] for p in primary):
        raise ValueError('Undeclared primary arm')
    for arm in freeze['arms']:
        part = [p for p in primary if p['arm'] == arm]
        if len(unique(part, 'record_id')) != freeze['notices']:
            raise ValueError('Missing primary notice in an arm')
    return primary, repeats


def execution_context(run, shared_root=None):
    """Bind a phase to its one physical engine and complete execution receipt."""
    run = Path(run).resolve()
    root = run if shared_root is None else Path(shared_root).resolve()
    completion = read(root / 'execution_exit.json')
    if completion['returncode'] != 0 or completion['wall_budget_exhausted']:
        raise ValueError('Execution did not complete; retain partial results separately')
    context = {'shared_engine': shared_root is not None, 'execution_root': root.as_posix()}
    if shared_root is not None:
        if run.parent != root:
            raise ValueError('Phase must be a direct child of the shared execution')
        started = read(run / 'phase_started.json')
        finished = read(run / 'phase_complete.json')
        complete = read(root / 'complete.json')
        authority = read(root / 'authority.json')
        phase, order = started['phase'], authority['phase_order']
        if (not isinstance(phase, str) or phase not in order or len(set(order)) != len(order)
                or run.name != phase + '_run' or complete['phase_order'] != order
                or set(complete['phases']) != set(order)
                or type(complete['engine_loads']) is not int or complete['engine_loads'] != 1
                or authority['maximum_engine_loads'] != 1
                or started['shared_engine'] != '../engine_bootstrap'
                or type(started['engine_loads_here']) is not int or started['engine_loads_here'] != 0
                or type(finished['engine_loads_here']) is not int or finished['engine_loads_here'] != 0
                or started['previous_phases'] != order[:order.index(phase)]
                or finished['prior_phases'] != started['previous_phases']
                or finished['phase'] != phase
                or finished['summary'] != complete['phases'][phase]
                or finished['summary'] != read(run / 'contrast_output/contrast_summary.json')):
            raise ValueError('Shared engine phase provenance mismatch')
        context.update(phase=phase, phase_order=order, prior_phases=started['previous_phases'],
            engine_loads_in_whole_execution=1, engine_loads_in_phase=0,
            phase_seconds=finished['seconds'], duration_is_shared_not_additive=True)
    return (read(root / 'engine_bootstrap/environment.json'),
            read(root / 'engine_bootstrap/engine.json'), completion, context)


def verify_context_pair(control, candidate):
    """Permit only declared source-context metadata/task changes in this contrast."""
    from submission.pps.specification_candidate_review import (
        CONTEXT_SYSTEM, FORMAT, source_context_prompt, _candidate_plan)
    p, q = control, candidate
    if (p['spans'] != q['spans'] or p['source_search'] != q['source_search']
            or p['generation']['response_format'] != FORMAT or q['generation']['response_format'] != FORMAT
            or 'source_context' in p['specification_inventory']
            or _candidate_plan(q['specification_inventory']) != p['specification_inventory']
            or len(p['messages']) != 2 or len(q['messages']) != 2
            or [m['role'] for m in p['messages']] != ['system', 'user']
            or [m['role'] for m in q['messages']] != ['system', 'user']):
        raise ValueError('Context contrast changed its original source or base candidate inventory')
    observed = q['specification_inventory']['source_context']
    if (q['messages'][0]['content'] != p['messages'][0]['content'] + CONTEXT_SYSTEM
            or q['messages'][1]['content'] != p['messages'][1]['content'] + source_context_prompt(observed)):
        raise ValueError('Context contrast contains an undeclared message change')


def audit(run, prepared, *, shared_root=None):
    folder = run / 'contrast_output'
    freeze = read(folder / 'input_freeze.json')
    summary = read(folder / 'contrast_summary.json')
    environment, engine, completion, execution = execution_context(run, shared_root)
    assert freeze == read(prepared / 'contrast_freeze.json')
    assert freeze['source_sha256'] == source_manifest()
    assert summary['source_unchanged'] and not summary['labels_read']
    assert summary['quality_rerolls'] == 0
    packets = rows(folder / 'current_packets.jsonl.gz')
    assert packets == rows(prepared / 'contrast_packets.jsonl.gz')
    assert sha(prepared / 'contrast_packets.jsonl.gz') == freeze['packets_sha256']
    records = rows(folder / 'current_inputs.jsonl.gz')
    assert records == rows(prepared / 'contrast_inputs.jsonl.gz')
    assert sha(prepared / 'contrast_inputs.jsonl.gz') == freeze['inputs_sha256']
    by_id = unique(records, 'id')
    index = unique(packets)
    final = unique(rows(folder / 'contrast_results.jsonl.gz'))
    first = unique([r for p in sorted(folder.glob('primary_*.jsonl.gz')) for r in rows(p)])
    assert len(index) == len(final) == len(first) == freeze['primary_requests']
    assert index.keys() == final.keys() == first.keys()
    primary, repeats = primary_and_repeats(packets, freeze)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT / 'models/gemma-tokenizer',
        local_files_only=True, trust_remote_code=False)
    pipeline = B4Pipeline(ROOT / 'data_open/data', tokenizer, input_strategy='audited')
    calls, native, responses, sampling = {}, {}, {}, {}
    for path in sorted(folder.glob('call_*_requests.jsonl.gz')):
        name = path.name.removesuffix('_requests.jsonl.gz')
        requested = rows(path)
        actual = rows(folder / (name + '_native.jsonl.gz'))
        parsed = rows(folder / (name + '_responses.jsonl.gz'))
        params = read(folder / (name + '_sampling.json'))
        assert len(requested) == len(actual) == len(parsed) == len(params['before_llm_generate'])
        assert params['request_keys'] == [p['request_key'] for p in requested]
        for p, n, r, sampler in zip(requested, actual, parsed, params['before_llm_generate']):
            key, attempt = p['request_key'], n['attempt']
            identity = (key, attempt)
            assert identity not in native and key in index and attempt in (0, 1)
            assert n['request_key'] == r['request_key'] == key and r['attempt'] == attempt
            if attempt == 0:
                assert p == index[key]
            else:
                assert first[key]['parse_error'] is not None, 'Semantic reroll rejected'
                assert p['spans'] == index[key]['spans'] and p['items'] == index[key]['items']
            expected = generation_schema(p['generation']['response_format'], len(p['spans']), p['items'],
                schema_order=p['generation'].get('schema_order'),
                specification_inventory=p['generation'].get('specification_inventory'))
            assert sampler['structured_outputs']['json'] == expected
            assert digest(expected) == p['generation_schema_sha256']
            assert n['prompt_sha256'] == digest(p['messages']) == p['prompt_sha256']
            assert n['token_ids_sha256'] == digest(p['token_ids']) == p['token_ids_sha256']
            assert n['native']['prompt_token_ids'] == p['token_ids']
            assert token_ids(tokenizer, p['messages'], True) == p['token_ids']
            assert n['native']['num_outputs'] == 1
            import hashlib
            assert hashlib.sha256(n['native']['raw_text'].encode()).hexdigest() == r['response']['raw_output_sha256']
            assert len(n['native']['output_token_ids']) == r['response']['output_tokens']
            verify_packet_source(p, by_id[p['record_id']], tokenizer)
            calls[identity], native[identity] = p, n
            responses[identity], sampling[identity] = r['response'], sampler
        if actual[0]['attempt'] == 0:
            planned = next(c for c in freeze['call_plan'] if c['number'] == actual[0]['batch'])
            assert planned['request_keys'] == params['request_keys']
    assert {key for key, attempt in native if attempt == 0} == index.keys()
    aggregate = {arm: Counter() for arm in sorted({p['arm'] for p in packets})}
    for key, p in index.items():
        r = final[key]
        assert r['record_id'] == p['record_id'] and r['arm'] == p['arm'] and r['items'] == p['items']
        assert first[key]['response'] == responses[key, 0]
        assert r['response'] == responses[key, r['attempt']]
        used = calls[key, r['attempt']]
        assert parse_error(used, r['response']) == r['parse_error']
        if r['parse_error'] is None:
            row, detail = pipeline.consume(by_id[p['record_id']], used, r['response'])
            assert row == r['row'] and detail == r['details'], 'CPU consumer changed'
            assert r['model_judgment_present'] is True
        a = aggregate[p['arm']]
        a['observations'] += 1
        a['first_valid'] += first[key]['parse_error'] is None
        a['final_valid'] += r['parse_error'] is None
        a['selected_retry'] += r['attempt'] > 0
        a['output_tokens'] += len(native[key, 0]['native']['output_token_ids'])
        a['length_finishes'] += native[key, 0]['native']['finish_reason'] == 'length'
        details = r['details'] or []
        if isinstance(details, dict):
            details = [details]
        a['relationship_issues'] += sum(len(d.get('relationship_issues', [])) for d in details)
    pairs = []
    candidate_mode = freeze.get('candidate_review_intervention', False)
    context_mode = freeze.get('candidate_context_intervention', False)
    assert not (candidate_mode and context_mode)
    if candidate_mode:
        assert freeze['arms'] == ['specification_scope', 'specification_candidates']
    if context_mode:
        from submission.pps.specification_candidate_review import FORMAT, CONTEXT_ARM
        assert freeze['arms'] == [FORMAT, CONTEXT_ARM]
    for rid in by_id:
        pair = [next(p for p in primary if p['record_id'] == rid and p['arm'] == arm) for arm in freeze['arms']]
        p = pair[0]
        for q in pair[1:]:
            assert p['spans'] == q['spans']
            if candidate_mode:
                from submission.pps.specification_candidate_review import inventory_prompt
                assert q['messages'][1]['content'] == p['messages'][1]['content'] + inventory_prompt(q['specification_inventory'])
            elif context_mode:
                verify_context_pair(p, q)
            else:
                assert p['messages'][1] == q['messages'][1]
            assert p['source_search'] == q['source_search']
            assert sampler_without_schema(sampling[p['request_key'], 0]) == sampler_without_schema(sampling[q['request_key'], 0])
        pairs.append({'record_id': rid, 'same_original_source_user_and_sampling': not (candidate_mode or context_mode),
            **({'same_original_source_and_sampling': True, 'added_source_derived_candidate_metadata': True}
               if candidate_mode else {}),
            **({'same_original_source_and_sampling': True, 'added_observed_context_metadata': True}
               if context_mode else {}),
            'source_tokens': p['source_search']['source_tokens'],
            'input_tokens': {x['arm']: len(x['token_ids']) for x in pair},
            'raw_values': {x['arm']: final[x['request_key']].get('raw_values') for x in pair},
            'rows': {x['arm']: final[x['request_key']]['row'] for x in pair}})
    repeated = []
    for pair in repeats:
        x, y = pair['primary'], pair['repeat']
        assert sampling[x, 0] == sampling[y, 0]
        nx, ny = native[x, 0]['native'], native[y, 0]['native']
        repeated.append({**pair, 'arm': index[x]['arm'], 'same_input_and_sampler': True,
            'batch_numbers': [native[x, 0]['batch'], native[y, 0]['batch']],
            'cached_input_tokens': [nx['cached_input_tokens'], ny['cached_input_tokens']],
            'raw_text_equal': nx['raw_text'] == ny['raw_text'],
            'raw_values': [first[k].get('raw_values') for k in (x, y)],
            'consumed_values': [{a: b for a, b in (first[k]['row'] or {}).items() if a.startswith('v')} for k in (x, y)],
            'parse_errors': [first[k]['parse_error'] for k in (x, y)]})
    report = {'kind': 'source_matched_contract_native_audit', 'classification_labels_read': False,
        'source_sha256': freeze['source_sha256'], 'observations': len(index), 'primary_comparison_fields': len(primary),
        'native_returns': len(native), 'retry_returns': len(native) - len(index),
        'cpu_consumer_reproduced': True, 'aggregates': aggregate, 'pairs': pairs,
        'repeats': repeated, 'generation_seconds': summary['generation_seconds'],
        'engine_load_seconds': engine['load_seconds'], 'total_execution_seconds': completion['seconds'],
        'execution_context': execution,
        'checkpoint_identity': engine['checkpoint_identity'],
        'weight_shard_hashes_observed': environment['weight_shard_hashes_observed'],
        'packages': environment['packages'],
        'repeated_raw_changes': sum(not r['raw_text_equal'] for r in repeated),
        'repeated_bit_changes': sum(r['consumed_values'][0] != r['consumed_values'][1] for r in repeated),
        'limitation': 'Same engine, different batches/cache histories. Output task/schema differ between arms. Repeats never select predictions. No retrieval algorithm, full-fresh, held-out or official score claim.'}
    return freeze, primary, final, records, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--shared-root', type=Path,
        help='Explicit root containing one shared engine and the complete phase receipts.')
    parser.add_argument('--replay', type=Path, action='append', required=True)
    parser.add_argument('--baseline', type=Path, action='append', default=[])
    parser.add_argument('--labels', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    freeze, packets, final, records, report = audit(args.run, args.prepared, shared_root=args.shared_root)
    save(args.output / 'generation_audit.json', report)
    predictions, measurements, baseline_paths = {}, [], []
    for replay in args.replay:
        baseline_dir = Path(read(replay / 'freeze.json')['source_run'])
        baseline_path = verified_replay_path(baseline_dir, replay, freeze['source_sha256'])
        baseline_paths.append(baseline_path)
        source = rows(baseline_dir / 'current_inputs.jsonl.gz')
        all_by_id = unique(source, 'id')
        assert all(all_by_id[r['id']] == r for r in records)
        for arm in freeze['arms']:
            name = replay.name + '__' + arm
            assert name not in predictions
            part = [p for p in packets if p['arm'] == arm]
            joined, join = join_items(read_csv(baseline_path), part, [final[p['request_key']] for p in part])
            detail = {'name': name, 'arm': arm, 'baseline': baseline_path.as_posix(), 'join': join}
            if joined is not None:
                path = args.output / (name + '.csv')
                write_csv(path, joined, recs=source, require_positive_evidence=False)
                predictions[name] = path
                detail['prediction_sha256'] = sha(path)
            measurements.append(detail)
    save(args.output / 'prediction_freeze.json', {'kind': 'saved_baselines_plus_predeclared_fresh_v9_fields',
        'classification_labels_read': False, 'source_sha256': freeze['source_sha256'],
        'baseline_files': {p.as_posix(): sha(p) for p in baseline_paths},
        'files': {p.name: sha(p) for p in predictions.values()}, 'repeat_answer_selection': False})
    for detail in measurements:
        name = detail['name']
        if name not in predictions:
            detail['mixed_macro_f1'] = None
            continue
        refs = list(dict.fromkeys([*baseline_paths, *args.baseline,
            *[p for k, p in predictions.items() if k != name]]))
        evaluation = compare(args.labels, predictions[name], refs)
        save(args.output / (name + '_metrics.json'), evaluation)
        score = evaluation['candidate']
        detail.update(mixed_macro_f1=score['macro_f1'],
            fp=sum(r['fp'] for r in score['per_item'].values()),
            fn=sum(r['fn'] for r in score['per_item'].values()), v9=score['per_item']['v9'])
    save(args.output / 'report.json', {'kind': 'source_matched_v9_contract_mixed_response_diagnostic',
        'full_fresh_inference': False, 'official_macro_f1': None, 'default_route_adopted': False,
        'source_sha256': freeze['source_sha256'], 'notices': len(records),
        'measurements': measurements, 'labels_sha256': sha(args.labels),
        'repeated_bit_changes': report['repeated_bit_changes'], 'repeated_observations': len(report['repeats']),
        'limitations': report['limitation']})
    print(json.dumps([{k: r.get(k) for k in ('name', 'mixed_macro_f1', 'fp', 'fn', 'v9')}
        for r in measurements], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
