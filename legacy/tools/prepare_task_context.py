"""Freeze five whole-Q-cohort task-context arms without reading answers/labels.

The original A10 decisions and every non-Q response remain fixed in the later
joined diagnostic. This is fresh Q inference, not standalone full inference.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import digest
from submission.pps.generation_contract import prepared_preflight, progress_preflight
from submission.pps.prompts import Config, token_ids
from submission.runtime import Journal, source_manifest, sha256
from tools.run_retrieval_contrast import verify_packet_source
from tools.run_runtime_revival import read, rows, verify_prepared

ARMS = (
    ('shared_plain', 'lexical', 'shared_source', False),
    ('shared_groups', 'lexical', 'shared_source', True),
    ('lexical_reserved_plain', 'lexical', 'task_reserved', False),
    ('lexical_reserved_groups', 'lexical', 'task_reserved', True),
    ('hybrid_reserved_groups', 'hybrid', 'task_reserved', True),
)


def prepare(base, output):
    source = source_manifest()
    reference = base / 'source_context_runtime_v2/prepared_02'
    verify_prepared(reference, require_current_source=False)
    records = rows(reference / 'current_inputs.jsonl.gz')
    controls = {p['record_id']: p for p in rows(reference / 'primary_packets.jsonl.gz') if p['family'] == 'Q'}
    assert len(records) == 160 and len(controls) == 94
    comparisons = {'lexical': base / 'task_context_v1/lexical_04',
                   'hybrid': base / 'task_context_v1/hybrid_03'}
    offered, dependencies = {}, {}
    for method, directory in comparisons.items():
        report = read(directory / 'report.json')
        assert report['source_sha256'] == source and report['method'] == method
        assert report['Q_records'] == len(controls) and report['new_model_calls'] == 0
        offered[method] = {(r['record_id'], r['arm'], r['grouped']): r['packet']
                           for r in rows(directory / 'offered_packets.jsonl.gz')}
        for name in ('report.json', 'preregistered.json', 'offered_packets.jsonl.gz'):
            path = directory / name
            dependencies[path.relative_to(ROOT).as_posix()] = sha256(path)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT / 'models/gemma-tokenizer',
        local_files_only=True, trust_remote_code=False)
    config = Config.load(ROOT / 'submission/model/config.json')
    journal = Journal(output)
    chosen = [r for r in records if r['id'] in controls]
    packets, budgets = [], []
    for ordinal, record in enumerate(chosen):
        control = controls[record['id']]
        cap = control['source_search']['source_tokens']
        ordered = ARMS[ordinal % len(ARMS):] + ARMS[:ordinal % len(ARMS)]
        for name, method, search, grouped in ordered:
            packet = copy.deepcopy(offered[method][record['id'], search, grouped])
            assert packet['record_id'] == record['id'] and packet['family'] == 'Q'
            assert packet['source_search']['source_tokens'] <= cap
            assert token_ids(tokenizer, packet['messages'], True) == packet['token_ids']
            assert digest(packet['messages']) == packet['prompt_sha256']
            assert digest(packet['token_ids']) == packet['token_ids_sha256']
            assert digest(packet['spans']) == packet['source_sha256']
            assert packet['generation'] == control['generation']
            assert len(packet['token_ids']) + 1536 + 32 <= config.max_model_len
            verify_packet_source(packet, record, tokenizer)
            if name == 'shared_plain':
                for key in ('messages', 'token_ids', 'spans', 'source_search', 'generation', 'generation_schema_sha256'):
                    assert packet[key] == control[key], (record['id'], key)
            packet.update(request_key=f'task-context:{name}:{record["id"]}',
                          case=record['id'], arm=name)
            packets.append(packet)
            budgets.append(dict(record_id=record['id'], arm=name, original_source_cap=cap,
                source_tokens=packet['source_search']['source_tokens'], prompt_tokens=len(packet['token_ids'])))
    assert len(packets) == len(chosen) * len(ARMS) == 470
    assert len({p['request_key'] for p in packets}) == len(packets)
    proof = dict(effective_schemas=prepared_preflight(packets),
                 catalog_token_progress=progress_preflight(tokenizer, response_format='catalog_scope'),
                 source_sha256=source)
    journal.save('generation_preflight.json', proof)
    journal.rows('contrast_packets.jsonl.gz', packets)
    journal.rows('contrast_inputs.jsonl.gz', chosen)
    journal.rows('full_join_inputs.jsonl.gz', records)
    journal.save('budgets.json', budgets)
    # Read hashes, never predictions, during preregistration. The join consumes
    # all saved non-Q responses under one fixed global policy, for every arm.
    scoring_paths = [
        base / 'source_questions_v1/prepared_01/input_freeze.json',
        base / 'source_questions_v1/prepared_01/recipes.json',
        base / 'gpu_runtime_v28/final_recovery_01/recovered/runtime_run/normal/resolved_responses.jsonl.gz',
        base / 'gpu_runtime_v28/final_recovery_01/recovered/runtime_run/probes/resolved_responses.jsonl.gz',
        base / 'task_context_v1/cpu_v28_01/current_questions768_catalog_explicit_v9_gated.csv',
        base / 'a10_relation_consumer_v1/scores_02/report.json',
    ]
    refs = {p.relative_to(ROOT).as_posix(): sha256(p) for p in scoring_paths}
    journal.save('scoring_references.json', dict(files=refs,
        fixed_base_policy='current_questions768_catalog_explicit_v9_gated',
        replacement='Replace only the existing Q response for all 94 originally eligible notices. Same 160 original notices, A10 full9, CPU consumer and other native responses for every arm.',
        new_fields='All 94 Q responses per arm are new, including shared_plain control.',
        comparison='Compare every globally fixed arm with fresh shared_plain and preserved current base; report all deltas, invalid results and coverage tradeoffs.',
        full_standalone_fresh_inference=False, official_score=None,
        labels_read=False, saved_response_contents_read=False))
    freeze = dict(source_sha256=source, config=asdict(config),
        packets_sha256=sha256(output / 'contrast_packets.jsonl.gz'),
        inputs_sha256=sha256(output / 'contrast_inputs.jsonl.gz'),
        call_plan=[dict(number=n // 32, request_keys=[p['request_key'] for p in packets[n:n+32]])
                   for n in range(0, len(packets), 32)],
        cases=len(chosen), notices=len(chosen), primary_requests=len(packets),
        source_token_budget='Per-notice exact shared-Q source cap; no arm may exceed it.',
        methods=[a[0] for a in ARMS],
        input_tokens_total=sum(len(p['token_ids']) for p in packets),
        input_tokens_max=max(len(p['token_ids']) for p in packets),
        dependencies=dependencies, classification_labels_read=False,
        saved_response_contents_read=False, new_full160_score=False, official_macro_f1=None,
        maximum_format_retries=2, quality_rerolls=0, maximum_engine_loads=1,
        execution_order='Rotate the five arms by source-only notice ordinal, interleaved within fixed 32-request cohorts.',
        contrasts=['shared_groups - shared_plain: group hints only',
                   'lexical_reserved_plain - shared_plain: retrieval only',
                   'lexical_reserved_groups - lexical_reserved_plain: grouping on new retrieval',
                   'hybrid_reserved_groups - lexical_reserved_groups: BGE incremental retrieval'])
    journal.save('contrast_freeze.json', freeze)
    (output / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    assert source_manifest() == source
    assert all(sha256(ROOT / p) == h for p, h in dependencies.items())
    assert all(sha256(ROOT / p) == h for p, h in refs.items())
    report = dict(status='PASS', source_sha256=source, requests=len(packets), notices=len(chosen),
        exact_control_count=len(chosen), original_source_caps_verified=True,
        actual_tokens_and_sources_verified=True, model_schema_and_sampler_fixed=True,
        generation_preflight_sha256=sha256(output / 'generation_preflight.json'),
        freeze_sha256=sha256(output / 'contrast_freeze.json'),
        classification_labels_read=False, saved_response_contents_read=False, gpu_used=False,
        input_tokens_total=freeze['input_tokens_total'], input_tokens_max=freeze['input_tokens_max'])
    journal.save('preparation_report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=ROOT / 'runs/independent_audit_20260913')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.base.resolve(), args.output.resolve()), ensure_ascii=False, indent=2))
