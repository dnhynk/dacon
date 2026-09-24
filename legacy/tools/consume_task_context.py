"""Verify native Q contrasts and reassemble each arm over fixed full9 responses.

An abstaining new Q must preserve the original A10 decision, not an old Q
overlay. Predictions are frozen before the separate score command reads labels.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import B4Pipeline, assemble, digest, parse_error
from submission.pps.data import read_csv, write_csv
from submission.runtime import Journal, sha256, source_manifest
from tools.run_integrated_comparison import verify_resolved_native
from tools.run_runtime_revival import read, rows, verify_prepared


def projected(resolved):
    return [{k: r[k] for k in ('request_key', 'attempt', 'response')} for r in resolved]


def replacement_packets(base_packets, fresh_q):
    old_ids = [p['record_id'] for p in base_packets if p['family'] == 'Q']
    new_ids = [p['record_id'] for p in fresh_q]
    if (len(set(old_ids)) != len(old_ids) or len(set(new_ids)) != len(new_ids)
            or set(old_ids) != set(new_ids) or any(p['family'] != 'Q' for p in fresh_q)):
        raise ValueError('Q replacement requires the complete identical cohort exactly once')
    return [p for p in base_packets if p['family'] != 'Q'] + list(fresh_q)


def consume(prepared, run, base, output):
    frozen = read(prepared / 'contrast_freeze.json')
    refs = read(prepared / 'scoring_references.json')
    source = source_manifest()
    assert frozen['source_sha256'] == source
    assert all(sha256(ROOT / p) == h for p, h in refs['files'].items())
    assert sha256(prepared / 'contrast_packets.jsonl.gz') == frozen['packets_sha256']
    assert sha256(prepared / 'contrast_inputs.jsonl.gz') == frozen['inputs_sha256']
    packets = rows(prepared / 'contrast_packets.jsonl.gz')
    original = rows(prepared / 'full_join_inputs.jsonl.gz')
    by_id = {r['id']: r for r in original}
    assert len(by_id) == len(original) == 160
    assert rows(run / 'current_packets.jsonl.gz') == packets
    assert rows(run / 'current_inputs.jsonl.gz') == rows(prepared / 'contrast_inputs.jsonl.gz')
    assert read(run / 'input_freeze.json') == frozen
    for rec in rows(run / 'current_inputs.jsonl.gz'): assert rec == by_id[rec['id']]
    resolved = rows(run / 'resolved_responses.jsonl.gz')
    observed = rows(run / 'contrast_results.jsonl.gz')
    assert {r['request_key'] for r in observed} == {p['request_key'] for p in packets}
    assert len(observed) == len(packets) == 470
    native = verify_resolved_native(run, packets, projected(resolved))
    assert not native['unreturned_requests'] and not native['unparsed_native_attempts'] and not native['unrequested_primary_keys']
    assert native['primary_attempts'] == 470
    summary = read(run / 'contrast_summary.json')
    assert summary['source_unchanged'] and summary['quality_rerolls'] == 0
    assert summary['valid_results'] == len(resolved)
    assert len({r['request_key'] for r in resolved}) == len(resolved)
    fresh = {r['request_key']: r for r in resolved}
    remote = {r['request_key']: r for r in observed}
    journal = Journal(output)
    old_prepared = base / 'source_questions_v1/prepared_01'
    old_run = base / 'gpu_runtime_v28/final_recovery_01/recovered/runtime_run'
    verify_prepared(old_prepared, require_current_source=False)
    assert rows(old_prepared / 'current_inputs.jsonl.gz') == original
    old_primary = rows(old_prepared / 'primary_packets.jsonl.gz')
    old_probes = rows(old_prepared / 'probe_packets.jsonl.gz')
    lookup = {p['request_key']: p for p in old_primary + old_probes}
    keys = read(old_prepared / 'recipes.json')[refs['fixed_base_policy']]
    skipped = read(old_run / 'normal/skipped_requests.json')
    old_normal = rows(old_run / 'normal/resolved_responses.jsonl.gz')
    old_probe_resolved = rows(old_run / 'probes/resolved_responses.jsonl.gz')
    native_base = dict(normal=verify_resolved_native(old_run / 'normal',
        [p for p in old_primary if p['request_key'] not in skipped], projected(old_normal)),
        probes=verify_resolved_native(old_run / 'probes', old_probes, projected(old_probe_resolved)))
    old_responses = {r['request_key']: r for r in old_normal + old_probe_resolved}
    assert all(k in old_responses or k in skipped for k in keys)
    pipe = B4Pipeline(ROOT / 'data_open/data', None)
    consumed, details = {}, []
    for key in keys:
        p = lookup[key]
        if key in skipped:
            from submission.skips import validate, consume as consume_skip
            validate(p, skipped[key])
            row, trace = consume_skip(by_id[p['record_id']], p, skipped[key], pipe.knowledge)
        else:
            response = old_responses[key]['response']
            error = parse_error(p, response)
            if error:
                assert p['batch'] in {'Q10', 'S9', 'W20'}
                row, trace = None, dict(recorded_optional_format_failure=error)
            else:
                row, trace = pipe.consume(by_id[p['record_id']], p, response)
        consumed[key] = row
        details.append(dict(request_key=key, record_id=p['record_id'], row=row, details=trace, origin='fixed_saved_response'))
    _, baseline = assemble(original, [lookup[k] for k in keys], consumed)
    reference = base / 'task_context_v1/cpu_v28_01/current_questions768_catalog_explicit_v9_gated.csv'
    baseline_path = output / 'preserved_current.csv'
    write_csv(baseline_path, [baseline[r['id']] for r in original], recs=original, require_positive_evidence=False)
    assert baseline_path.read_bytes() == reference.read_bytes(), 'Frozen base reassembly differs'
    q_ids = {lookup[k]['record_id'] for k in keys if lookup[k]['family'] == 'Q'}
    assert q_ids == {p['record_id'] for p in packets} and len(q_ids) == 94
    # Crucial: remove every old Q overlay before joining a new arm. Leaving an
    # old Q decision under a new abstention would mix two policies by notice.
    base_packets = [lookup[k] for k in keys]
    predictions, generation = {}, []
    for arm in frozen['methods']:
        part = [p for p in packets if p['arm'] == arm]
        assert len(part) == 94 and {p['record_id'] for p in part} == q_ids
        invalid = []
        gates = Counter()
        for p in part:
            key = p['request_key']
            if key not in fresh:
                assert remote[key]['parse_error'] is not None
                invalid.append(key)
                continue
            response = fresh[key]['response']
            assert parse_error(p, response) is None
            row, trace = pipe.consume(by_id[p['record_id']], p, response)
            assert row == remote[key]['row'] and trace == remote[key]['details'], 'Local/remote Q consumption differs'
            consumed[key] = row
            gates[trace['gate']] += 1
            details.append(dict(request_key=key, record_id=p['record_id'], arm=arm,
                                row=row, details=trace, origin='fresh_Q_response'))
            generation.append(dict(request_key=key, arm=arm, attempt=fresh[key]['attempt'],
                output_tokens=response['output_tokens'], finish_reason=response['finish_reason'],
                response_text_sha256=digest(response['text'])))
        entry = dict(fresh_Q_requests=94, valid=94-len(invalid), invalid=invalid, gates=dict(gates))
        if invalid:
            entry.update(status='withheld_unresolved_format')
        else:
            _, values = assemble(original, replacement_packets(base_packets, part), consumed)
            path = output / (arm + '.csv')
            write_csv(path, [values[r['id']] for r in original], recs=original, require_positive_evidence=False)
            entry.update(status='complete', path=path.name, sha256=sha256(path), records=len(values))
        predictions[arm] = entry
    journal.rows('decisions.jsonl.gz', details)
    journal.save('native_verification.json', dict(fresh_Q=native, fixed_base=native_base))
    journal.save('generation.json', generation)
    immutable = {p.as_posix(): sha256(p) for p in [prepared / 'contrast_freeze.json',
        prepared / 'scoring_references.json', prepared / 'full_join_inputs.jsonl.gz',
        run / 'resolved_responses.jsonl.gz', run / 'contrast_results.jsonl.gz', run / 'contrast_summary.json']}
    assert source_manifest() == source
    assert all(sha256(ROOT / p) == h for p, h in refs['files'].items())
    result = dict(kind='fresh_Q_fixed_saved_non_Q_join', source_sha256=source,
        input_sha256=immutable, predictions=predictions, records=160, Q_notices=94,
        preserved_current_sha256=sha256(baseline_path), source_and_consumer_fixed_before_generation=True,
        labels_read=False, old_Q_overlays_removed_before_join=True,
        new_Q_responses=native['all_parsed_attempts'], official_score=None,
        full_standalone_fresh_inference=False, per_notice_arm_selection=False,
        limitation='Exposed development sources.470 fresh Q requests joined to fixed earlier native responses; not whole fresh execution or official performance.')
    journal.save('prediction_freeze.json', result)
    return {k: v for k, v in result.items() if k not in ('source_sha256', 'input_sha256')}


def score(consumed, prepared, labels, output):
    from tools.evaluate import compare
    frozen = read(consumed / 'prediction_freeze.json')
    declared = read(prepared / 'contrast_freeze.json')
    assert frozen['source_sha256'] == declared['source_sha256'] == source_manifest()
    assert set(frozen['predictions']) == set(declared['methods'])
    assert frozen['old_Q_overlays_removed_before_join'] and not frozen['labels_read']
    assert all(sha256(Path(p)) == h for p, h in frozen['input_sha256'].items())
    paths = {}
    for arm, info in frozen['predictions'].items():
        if info['status'] == 'complete':
            path = consumed / info['path']
            assert sha256(path) == info['sha256'] and len(read_csv(path)) == 160
            paths[arm] = path
        else:
            assert info['invalid'] and not (consumed / (arm + '.csv')).exists()
    baseline = consumed / 'preserved_current.csv'
    assert sha256(baseline) == frozen['preserved_current_sha256']
    assert sha256(labels) == '9e277dd7d72bd4c9cdca386dbac2d4d2ce4d26b9997f0f46907a1237a09c06ce'
    journal = Journal(output)
    metrics, comparisons = {}, []
    for arm, path in paths.items():
        references = [baseline, *([paths['shared_plain']] if arm != 'shared_plain' and 'shared_plain' in paths else [])]
        result = compare(labels, path, references)
        journal.save(arm + '_metrics.json', result)
        candidate = result['candidate']
        metrics[arm] = dict(macro_f1=candidate['macro_f1'],
            fp=sum(v['fp'] for v in candidate['per_item'].values()),
            fn=sum(v['fn'] for v in candidate['per_item'].values()),
            path=path.as_posix(), sha256=sha256(path))
    for before, after in [('shared_plain', 'shared_groups'), ('shared_plain', 'lexical_reserved_plain'),
                          ('lexical_reserved_plain', 'lexical_reserved_groups'),
                          ('lexical_reserved_groups', 'hybrid_reserved_groups')]:
        if before in paths and after in paths:
            delta = compare(labels, paths[after], [paths[before]])['comparisons'][str(paths[before])]
            comparisons.append(dict(control=before, candidate=after, **delta))
    journal.save('paired_contrasts.json', comparisons)
    report = dict(kind=frozen['kind'], measurements=metrics,
        predictions_sha256=sha256(consumed / 'prediction_freeze.json'), labels_sha256=sha256(labels),
        full_standalone_fresh_inference=False, official_score=None,
        incomplete=[arm for arm in declared['methods'] if arm not in paths],
        limitation=frozen['limitation'])
    journal.save('report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    cp = sub.add_parser('consume')
    for name in ('prepared', 'run', 'output'): cp.add_argument('--'+name, type=Path, required=True)
    cp.add_argument('--base', type=Path, default=ROOT / 'runs/independent_audit_20260913')
    sp = sub.add_parser('score')
    for name in ('consumed', 'prepared', 'labels', 'output'): sp.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    result = (consume(args.prepared, args.run, args.base, args.output) if args.operation == 'consume'
              else score(args.consumed, args.prepared, args.labels, args.output))
    print(json.dumps(result, ensure_ascii=False, indent=2))
