"""Run one canonical cohort from CPU-frozen packets without loading BGE on GPU.

Packet preparation remains part of the evidence: the current source, original
document coordinates, tokenizer output, effective schemas and call plan are
sealed before allocation.  The GPU process loads Gemma once, preserves every
native answer, and applies the same source-only S9 call gate as
``submission.runtime.execute``.  CPU consumption happens after recovery.

This measures fresh model behavior for the whole frozen cohort.  It does not
measure end-to-end live retrieval time and is never described as an official
submission-server run.
"""
from __future__ import annotations

import argparse
import dataclasses
import gzip
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from submission.b4_entry import B4Pipeline, assemble, digest, parse_error
from submission.pps.data import missing_evidence_items, write_csv
from submission.pps.generation_contract import prepared_preflight
from submission.pps.prompts import token_ids
from submission.runtime import (Journal, NativeRecorder, format_recovery_packet,
                                require_generation_progress, sha256, source_manifest)
from tools.run_integrated_comparison import verify_resolved_native
from tools.run_runtime_revival import verify_prepared


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))


def load(prepared, data_dir, tokenizer=None, *, require_current_source=True):
    """Verify the single-arm preparation and return its immutable call graph."""
    prepared = Path(prepared)
    frozen = verify_prepared(prepared, require_current_source=require_current_source)
    registration = read(prepared / 'preregistered.json')
    if registration.get('arms') != ['purchase_context']:
        raise ValueError('Frozen canonical execution requires exactly one normal source arm')
    if registration.get('cache_repeat_notices') != 0:
        raise ValueError('Frozen canonical execution cannot contain repeat probes')
    recs = rows(prepared / 'current_inputs.jsonl.gz')
    packets = rows(prepared / 'primary_packets.jsonl.gz')
    probes = rows(prepared / 'probe_packets.jsonl.gz')
    plan = read(prepared / 'normal_call_plan.json')
    if probes:
        raise ValueError('Frozen canonical execution cannot contain diagnostic probes')
    keys = [p['request_key'] for p in packets]
    planned = [key for batch in plan for key in batch['request_keys']]
    if (len(recs) != frozen['records'] or len(keys) != len(set(keys))
            or planned != keys and sorted(planned) != sorted(keys)
            or len(planned) != len(set(planned))):
        raise ValueError('Frozen records, packets or call plan are incomplete')
    pipe = B4Pipeline(data_dir, tokenizer)
    if dataclasses.asdict(pipe.config) != registration['config']:
        raise ValueError('Current canonical configuration differs from frozen preparation')
    if tokenizer is not None:
        by_id = {r['id']: r for r in recs}
        for packet in packets:
            if packet['record_id'] not in by_id:
                raise ValueError('Packet refers to a record outside the frozen cohort')
            actual = token_ids(tokenizer, packet['messages'], pipe.config.enable_thinking)
            if actual != packet['token_ids'] or digest(actual) != packet['token_ids_sha256']:
                raise ValueError('Frozen prompt tokens differ from the actual fixed tokenizer')
            for span in packet['spans']:
                doc = by_id[packet['record_id']]['docs'][span['doc_index']]
                if (span['text'] != doc['text'][span['start']:span['end']]
                        or span['doc_type'] != doc['type']):
                    raise ValueError('Frozen packet span differs from its original document range')
    proof = prepared_preflight(packets)
    return frozen, registration, recs, packets, plan, pipe, proof


def _s9_gate(packet, primary_a1_row, parent_key):
    count = len(packet['specification_inventory']['candidates'])
    value = primary_a1_row.get('v9') if primary_a1_row is not None else None
    needed = value == 1 or bool(count)
    if needed:
        return None
    return {'rule': 'shared CPU v9 positive OR nonempty original-source candidate inventory',
            'parent_request_key': parent_key, 'shared_v9': value,
            'candidate_count': count, 'model_called': False,
            'prediction_inferred': False}


def infer(prepared, data_dir, model_dir, output, *, wall_seconds=5400):
    """Generate every normal call required by the frozen source-only gate."""
    from transformers import AutoTokenizer
    from submission.engine import CanonicalRunner, configure_environment

    if type(wall_seconds) is not int or wall_seconds < 300:
        raise ValueError('A bounded integer wall budget of at least 300 seconds is required')
    began = time.monotonic()
    journal = Journal(output)
    configure_environment()
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True,
                                              trust_remote_code=False)
    frozen, registration, recs, packets, plan, pipe, proof = load(
        prepared, data_dir, tokenizer)
    journal.save('input_freeze.json', frozen)
    journal.save('generation_preflight.json', proof)
    journal.save('execution_policy.json', {
        'kind': 'fresh_whole_cohort_from_cpu_frozen_canonical_packets',
        'packet_preparation_included_in_score_but_excluded_from_gpu_wall_time': True,
        'live_retrieval_runtime_measured': False,
        'maximum_format_retries': registration['maximum_format_retries'],
        'quality_rerolls': 0, 'wall_seconds': wall_seconds,
        'labels_read': False, 'one_engine_maximum': True})
    by_id = {r['id']: r for r in recs}
    index = {p['request_key']: p for p in packets}
    primary_a1 = {p['record_id']: p['request_key'] for p in packets if p['batch'] == 'A1'}
    primary_rows = {}
    resolved = {}
    errors = {}
    skipped = {}
    called = set()
    primary_returned = format_retries = 0
    runner = recorder = None
    try:
        runner = CanonicalRunner(model_dir, pipe.config, journal)
        runner.deadline = min(getattr(runner, 'deadline', float('inf')),
                              began + wall_seconds - 30)
        recorder = NativeRecorder(runner, journal)

        def capture(batch, number, attempt):
            nonlocal primary_returned, format_retries
            if not batch:
                return
            if time.monotonic() >= runner.deadline:
                raise TimeoutError('Frozen canonical generation deadline reached')
            responses = recorder.generate(batch, number, attempt)
            if len(responses) != len(batch):
                raise RuntimeError('Missing frozen canonical native response')
            require_generation_progress(responses)
            for packet, response in zip(batch, responses):
                key = packet['request_key']
                error = parse_error(packet, response)
                errors[key] = error
                if attempt == 0:
                    called.add(key)
                if error is None:
                    if key in resolved:
                        raise ValueError('A valid native response may not be replaced')
                    resolved[key] = {'request_key': key, 'attempt': attempt,
                                     'response': response}
                    if packet['batch'] == 'A1' and attempt == 0:
                        primary_rows[key] = pipe.consume(
                            by_id[packet['record_id']], packet, response)[0]
                elif packet['batch'] == 'A1' and attempt == 0:
                    primary_rows[key] = None
            if attempt:
                format_retries += len(batch)
            else:
                primary_returned += len(batch)
            journal.save('response_errors.json', errors)
            journal.progress(phase='frozen_canonical_generation', batch=number,
                profile=batch[0]['batch'], attempt=attempt,
                primary_returned=primary_returned, primary_prepared=len(packets),
                skipped=len(skipped), valid=len(resolved),
                format_retries=format_retries,
                elapsed_seconds=round(time.monotonic() - began, 3))

        for planned in plan:
            batch = [index[key] for key in planned['request_keys']]
            if planned['profile'] == 'S9':
                selected = []
                for packet in batch:
                    parent = primary_a1[packet['record_id']]
                    decision = _s9_gate(packet, primary_rows.get(parent), parent)
                    if decision is None:
                        selected.append(packet)
                    else:
                        skipped[packet['request_key']] = decision
                journal.save('skipped_requests.json', skipped)
                batch = selected
            capture(batch, planned['number'], 0)

        number = len(plan)
        for attempt in range(1, registration['maximum_format_retries'] + 1):
            pending = [p for p in packets
                       if p['request_key'] in called and p['request_key'] not in resolved]
            if not pending:
                break
            for offset in range(0, len(pending), 32):
                capture([format_recovery_packet(p, attempt)
                         for p in pending[offset:offset + 32]], number, attempt)
                number += 1
        journal.rows('resolved_responses.jsonl.gz', list(resolved.values()))
        unresolved = {key: errors.get(key) for key in sorted(called - resolved.keys())}
        summary = {
            'records': len(recs), 'prepared_requests': len(packets),
            'primary_requests': primary_returned, 'skipped_requests': len(skipped),
            'format_retries': format_retries, 'valid_responses': len(resolved),
            'unresolved_formats': unresolved,
            'fresh_model_responses_for_all_called_packets': not unresolved,
            'single_engine': True, 'engine_load_seconds': runner.load_seconds,
            'elapsed_seconds': time.monotonic() - began,
            'source_unchanged': source_manifest() == frozen['source_sha256'],
            'labels_read': False, 'quality_rerolls': 0,
            'whole_fresh_predictions_assembled': False,
            'official_score': None, 'live_retrieval_runtime_measured': False}
        journal.save('generation_summary.json', summary)
        if primary_returned + len(skipped) != len(packets):
            raise ValueError('Every prepared primary request must be called or explicitly skipped')
        if unresolved:
            raise ValueError('Required frozen canonical responses remain invalid')
        if not summary['source_unchanged']:
            raise RuntimeError('Canonical source changed during frozen generation')
        return summary
    except BaseException:
        journal.rows('resolved_responses_partial.jsonl.gz', list(resolved.values()))
        journal.save('failure.json', {'traceback': traceback.format_exc(),
            'primary_returned': primary_returned, 'skipped_requests': len(skipped),
            'valid_responses': len(resolved), 'format_retries': format_retries})
        raise
    finally:
        if recorder is not None:
            recorder.close()
        if runner is not None:
            runner.close()
            journal.save('engine_shutdown.json', {
                'status': 'shutdown_returned', 'engine_loads': 1})


def consume(prepared, run_dir, data_dir, output, *, require_current_source=True):
    """Assemble the fresh frozen responses with the canonical CPU consumer."""
    prepared, run_dir = Path(prepared), Path(run_dir)
    frozen, _, recs, packets, _, pipe, _ = load(
        prepared, data_dir, require_current_source=require_current_source)
    journal = Journal(output)
    summary = read(run_dir / 'generation_summary.json')
    resolved = rows(run_dir / 'resolved_responses.jsonl.gz')
    responses = {r['request_key']: r for r in resolved}
    skipped = (read(run_dir / 'skipped_requests.json')
               if (run_dir / 'skipped_requests.json').is_file() else {})
    if len(responses) != len(resolved) or len(responses) != summary['valid_responses']:
        raise ValueError('Resolved response population differs from the generation summary')
    expected = {p['request_key'] for p in packets}
    if set(responses) | set(skipped) != expected or set(responses) & set(skipped):
        raise ValueError('Each frozen packet must have one response or one explicit skip')
    native = verify_resolved_native(run_dir, packets, resolved)
    if set(native['unrequested_primary_keys']) != set(skipped):
        raise ValueError('Native unrequested packets differ from explicit deterministic skips')
    journal.save('native_verification.json', native)
    by_id = {r['id']: r for r in recs}
    consumed = {}
    details = []
    for packet in packets:
        key = packet['request_key']
        if key in skipped:
            from submission.skips import consume as consume_skip
            row, trace = consume_skip(by_id[packet['record_id']], packet,
                                      skipped[key], pipe.knowledge)
        else:
            response = responses[key]['response']
            error = parse_error(packet, response)
            if error:
                raise ValueError('Selected frozen response is invalid: ' + error)
            row, trace = pipe.consume(by_id[packet['record_id']], packet, response)
        consumed[key] = row
        details.append({'request_key': key, 'record_id': packet['record_id'],
                        'attempt': responses[key]['attempt'] if key in responses else None,
                        'row': row, 'details': trace})
    b3, b4 = assemble(recs, packets, consumed)
    missing = {'final_B3': sum(row[f'v{k}'] is None for row in b3.values()
                              for k in range(1, 25)),
               'final_B4': sum(row[f'v{k}'] is None for row in b4.values()
                              for k in range(1, 25))}
    journal.rows('final_B3.jsonl.gz', list(b3.values()))
    journal.rows('final_B4.jsonl.gz', list(b4.values()))
    journal.rows('consumed.jsonl.gz', details)
    path = journal.root / 'submission.csv'
    write_csv(path, [b4[r['id']] for r in recs], recs=recs,
              require_positive_evidence=pipe.config.require_positive_evidence)
    report = {
        'status': 'PASS', 'records': len(recs),
        'fresh_model_responses': len(responses),
        'deterministic_skips': len(skipped), 'missing_bits': missing,
        'missing_required_evidence': sum(len(missing_evidence_items(r)) for r in b4.values()),
        'prediction_sha256': sha256(path),
        'source_sha256': source_manifest(),
        'generation_source_sha256': frozen['source_sha256'],
        'source_matches_generation': source_manifest() == frozen['source_sha256'],
        'labels_read': False, 'new_model_calls': 0,
        'measurement': 'whole development cohort, fresh frozen canonical packet inference',
        'live_retrieval_runtime_measured': False, 'official_score': None}
    journal.save('consumption_report.json', report)
    if missing['final_B4']:
        raise ValueError('Unresolved required predictions; CSV is not scoreable')
    if require_current_source and not report['source_matches_generation']:
        raise RuntimeError('Current consumer source differs from generation source')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    for name in ('infer', 'consume', 'verify'):
        p = sub.add_parser(name)
        p.add_argument('--prepared', type=Path, required=True)
        p.add_argument('--data-dir', type=Path, required=True)
        p.add_argument('--output', type=Path, required=True)
        if name in {'infer', 'verify'}:
            p.add_argument('--model-dir', type=Path, required=True)
        if name == 'infer':
            p.add_argument('--wall-seconds', type=int, default=5400)
        if name == 'consume':
            p.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    if args.operation == 'infer':
        result = infer(args.prepared, args.data_dir, args.model_dir,
                       args.output, wall_seconds=args.wall_seconds)
    elif args.operation == 'consume':
        result = consume(args.prepared, args.run_dir, args.data_dir, args.output)
    else:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(
            args.model_dir, local_files_only=True, trust_remote_code=False)
        result = {'status': 'PASS',
                  'effective_schemas': load(args.prepared, args.data_dir,
                                            tokenizer)[-1],
                  'labels_read': False, 'gpu_used': False}
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
