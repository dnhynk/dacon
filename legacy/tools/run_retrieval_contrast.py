"""Run frozen retrieval arms with the canonical engine/consumer; label-free."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import B4Pipeline, digest, parse_error, restored
from submission.pps.pipeline import parse_output
from submission.pps.prompts import token_ids, verified_search_spans
from submission.runtime import Journal, NativeRecorder, format_recovery_packet, source_manifest, require_generation_progress


def read_rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))


def verify_packet_source(packet, record, tokenizer):
    from submission.pps.catalog_field_contract import validate_prepared as validate_fields
    validate_fields(packet)
    selected = verified_search_spans(record, packet['source_search'], tokenizer)
    if packet['generation']['response_format'] in {'software_refs', 'catalog_scope', 'catalog_conditions', 'catalog_semantics', 'specification_scope', 'specification_relations'} or packet.get('source_layout') == 'finite_units':
        from submission.pps.source_units import unitize
        selected = unitize(selected)
    if selected != restored(packet)['spans']:
        raise ValueError('Packet source differs from its verified retrieval and unitization')
    if (packet['generation']['response_format'] == 'specification_candidates'
            or 'specification_inventory' in packet or 'specification_inventory' in packet['generation']):
        from submission.pps.specification_candidate_review import validate_prepared
        if packet['generation']['response_format'] != 'specification_candidates':
            raise ValueError('Candidate inventory differs from the prepared response format')
        validate_prepared(record, restored(packet))
    if packet.get('generation', {}).get('catalog_roles') is not None or packet.get('catalog_conditions', {}).get('source_roles') is not None:
        from submission.pps.catalog_source_roles import validate_prepared
        validate_prepared(record, restored(packet))


def first_format_gate(rows, journal=None):
    """A label-free pilot gate; semantic unknowns remain valid observations."""
    valid = bool(rows) and all(r['parse_error'] is None for r in rows)
    if journal is not None:
        journal.save('first_batch_format_gate.json', {'status': 'PASS' if valid else 'FAIL',
            'primary_requests': len(rows), 'all_valid': valid,
            'invalid': [{'request_key': r.get('request_key'), 'parse_error': r['parse_error']}
                        for r in rows if r['parse_error'] is not None],
            'labels_read': False, 'semantic_abstention_is_not_failure': True})
    if not valid:
        raise RuntimeError('Initial format pilot failed; saved responses retained, remaining requests not submitted')


def run(packet_dir, data_dir, model_dir, output_dir, *, runner=None,
        first_batch_format_gate=False, maximum_format_retries=2):
    if type(maximum_format_retries) is not int or not 0 <= maximum_format_retries <= 2:
        raise ValueError('Format recovery rounds must be an integer between zero and two')
    from submission.engine import configure_environment, CanonicalRunner
    configure_environment()
    from transformers import AutoTokenizer
    packet_dir = Path(packet_dir)
    freeze = json.loads((packet_dir / 'contrast_freeze.json').read_text(encoding='utf-8'))
    assert source_manifest() == freeze['source_sha256'], 'Canonical source changed after preparing contrast'
    for name, key in [('contrast_packets.jsonl.gz', 'packets_sha256'), ('contrast_inputs.jsonl.gz', 'inputs_sha256')]:
        assert hashlib.sha256((packet_dir / name).read_bytes()).hexdigest() == freeze[key]
    packets = read_rows(packet_dir / 'contrast_packets.jsonl.gz')
    recs = {r['id']: r for r in read_rows(packet_dir / 'contrast_inputs.jsonl.gz')}
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False)
    legal_knowledge = None
    if any(p.get('legal_control') is not None or p.get('legal_reading') is not None for p in packets):
        from submission.pps.knowledge import Knowledge
        legal_knowledge = Knowledge(data_dir)
    checked_grammars = set()
    for p in packets:
        assert digest(p['messages']) == p['prompt_sha256'] and digest(p['token_ids']) == p['token_ids_sha256']
        assert token_ids(tokenizer, p['messages'], True) == p['token_ids'], 'Rendered tokens differ from prepared input'
        verify_packet_source(p, recs[p['record_id']], tokenizer)
        if legal_knowledge is not None:
            from submission.pps.legal_query_contract import verify_prepared
            verify_prepared(p, recs[p['record_id']], legal_knowledge, tokenizer)
        if (p['generation'].get('catalog_fields') is not None or p['generation'].get('specification_inventory') is not None) and 'generation_schema_sha256' not in p:
            raise ValueError('Prepared catalog fields require an effective generation schema digest')
        if 'generation_schema_sha256' in p:
            from submission.pps.generation_contract import generation_schema, validate_grammar
            effective = generation_schema(
                p['generation']['response_format'], len(p['spans']), p['items'],
                schema_order=p['generation'].get('schema_order'),
                catalog_roles=p['generation'].get('catalog_roles'),
                catalog_fields=p['generation'].get('catalog_fields'),
                specification_inventory=p['generation'].get('specification_inventory'))
            fingerprint = digest(effective)
            assert p['generation_schema_sha256'] == fingerprint, 'Effective generation schema changed'
            if fingerprint not in checked_grammars:
                validate_grammar(effective)
                checked_grammars.add(fingerprint)
    journal = Journal(output_dir)
    journal.save('input_freeze.json', freeze)
    journal.rows('current_packets.jsonl.gz', packets)
    journal.rows('current_inputs.jsonl.gz', list(recs.values()))
    pipeline = B4Pipeline(data_dir, tokenizer, input_strategy='audited')
    owns_runner = runner is None
    if owns_runner:
        runner = CanonicalRunner(model_dir, pipeline.config, journal)
    else:
        from dataclasses import asdict
        for field in ('max_model_len', 'quantization', 'gpu_memory_utilization', 'seed', 'text_only'):
            assert getattr(runner.config, field) == getattr(pipeline.config, field), 'Shared engine config mismatch: '+field
        journal.save('shared_engine.json', {'load_seconds': runner.load_seconds,
            'config': asdict(runner.config), 'engine_loads_here': 0,
            'history': 'Caller-owned engine; prior call history is recorded by the caller.'})
    recorder = NativeRecorder(runner, journal)
    index = {p['request_key']: p for p in packets}
    results, selected = {}, {}
    started = time.monotonic()
    def consume(p, response, attempt):
        error = parse_error(p, response)
        row = {'request_key': p['request_key'], 'record_id': p['record_id'], 'case': p['case'],
               'arm': p['arm'], 'items': p['items'], 'attempt': attempt, 'parse_error': error,
               'response': response, 'row': None, 'details': None}
        if error is None:
            if p['generation']['response_format'] == 'specification_candidates':
                from submission.pps.specification_candidate_review import decode
                facts = decode(response['text'], restored(p)['spans'], p['specification_inventory'], recs[p['record_id']])
                row['raw_values'] = {'v9': facts['judgment']['v']}
                row['model_judgment_present'] = True
            elif p['generation']['response_format'] in {'specification_scope', 'specification_relations'}:
                if p['generation']['response_format'] == 'specification_relations':
                    from submission.pps.specification_relations import decode
                else:
                    from submission.pps.specification_scope import decode
                facts = decode(response['text'], restored(p)['spans'], recs[p['record_id']])
                row['raw_values'] = {'v9': facts['judgment']['v']}
                row['model_judgment_present'] = True
            elif p['generation']['response_format'] in {'software_facts', 'software_refs', 'catalog_scope', 'catalog_conditions', 'catalog_semantics'}:
                row['raw_values'] = None
                row['model_judgment_present'] = False
            else:
                values, _ = parse_output(response['text'], restored(p)['spans'], tuple(p['items']), rec=recs[p['record_id']])
                row['raw_values'] = {f'v{k}': values[k-1] for k in p['items']}
                row['model_judgment_present'] = True
            # Deterministic consumer failure is not a model retry condition.
            row['row'], row['details'] = pipeline.consume(recs[p['record_id']], p, response)
            selected[p['request_key']] = {'request_key': p['request_key'], 'packet': p,
                                          'response': response, 'attempt': attempt}
        return row
    try:
        for planned in freeze['call_plan']:
            batch = [index[k] for k in planned['request_keys']]
            responses = recorder.generate(batch, planned['number'], 0)
            rows = [consume(p, r, 0) for p, r in zip(batch, responses)]
            results.update({r['request_key']: r for r in rows})
            journal.rows(f"primary_{planned['number']:03d}.jsonl.gz", rows)
            journal.progress(phase='retrieval_contrast', returned=len(results), primary_total=len(packets),
                invalid=sum(r['parse_error'] is not None for r in results.values()))
            require_generation_progress(responses)
            if first_batch_format_gate and planned['number'] == 0:
                first_format_gate(rows, journal)
        recovery_number = len(freeze['call_plan'])
        for attempt in range(1, maximum_format_retries + 1):
            pending = [p for p in packets if results[p['request_key']]['parse_error'] is not None]
            if not pending:
                break
            # Preserve each bounded recovery batch before starting another.
            # One giant queued call hid progress and delayed access to results.
            for offset in range(0, len(pending), 32):
                recovery = [format_recovery_packet(p, attempt) for p in pending[offset:offset+32]]
                responses = recorder.generate(recovery, recovery_number, attempt)
                recovery_number += 1
                rows = [consume(p, r, attempt) for p, r in zip(recovery, responses)]
                journal.rows(f'recovery_{attempt}_{offset//32:03d}.jsonl.gz', rows)
                results.update({r['request_key']: r for r in rows})
                journal.progress(phase='retrieval_format_recovery', attempt=attempt,
                    recovered_in_round=min(offset+32, len(pending)), pending_in_round=len(pending),
                    returned=len(results), primary_total=len(packets),
                    invalid=sum(r['parse_error'] is not None for r in results.values()))
                require_generation_progress(responses)
        journal.rows('resolved_responses.jsonl.gz', list(selected.values()))
        journal.rows('contrast_results.jsonl.gz', [results[p['request_key']] for p in packets])
        summary = {'primary_requests': len(packets), 'valid_results': len(selected),
            'unresolved_formats': [key for key, row in results.items() if row['parse_error']],
            'generation_seconds': time.monotonic() - started, 'labels_read': False,
            'quality_rerolls': 0, 'full160': False, 'official_score': None,
            'first_batch_format_gate': first_batch_format_gate, 'maximum_format_retries': maximum_format_retries,
            'source_unchanged': source_manifest() == freeze['source_sha256']}
        journal.save('contrast_summary.json', summary)
        assert summary['source_unchanged']
        return runner, summary
    except BaseException:
        import traceback
        journal.save('failure.json', {'traceback': traceback.format_exc(), 'returned': len(results)})
        if owns_runner:
            runner.close()
        raise
    finally:
        recorder.close()
