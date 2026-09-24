"""Project stored A10 judgments to test consumer equivalence, never fresh F1.

The projection drops source-fixed judgment entries from a diagnostic copy.
It is not a native response, cannot be used as a new-model measurement, and
never changes or replaces the preserved text/token/response artifacts.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import gzip
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from submission.b4_entry import B4Pipeline, digest, parse_error
from submission.pps.source_questions import ITEMS, plan, code_only
from submission.runtime import Journal, sha256, source_manifest
from tools.run_runtime_revival import rows

AUDIT = ROOT / 'runs/independent_audit_20260913'


def sources():
    result = []
    for name in ('v3', 'v5'):
        directory = AUDIT / f'gpu_{name}_checkpoint/recovered/output'
        result.append((name, directory / 'current_inputs.jsonl.gz', directory / 'current_packets.jsonl.gz',
                       directory / 'resolved_responses.jsonl.gz'))
    for name, prepared, run in (
        ('v24', AUDIT / 'runtime_revival_v1/prepared_01', AUDIT / 'gpu_runtime_v24/recovery_01/recovered/runtime_run'),
        ('v27', AUDIT / 'source_context_runtime_v2/prepared_02', AUDIT / 'gpu_runtime_v27/final_recovery_01/recovered/runtime_run')):
        for kind in ('normal', 'probes'):
            result.append((name + '_' + kind, prepared / 'current_inputs.jsonl.gz',
                           prepared / ('primary_packets.jsonl.gz' if kind == 'normal' else 'probe_packets.jsonl.gz'),
                           run / kind / 'resolved_responses.jsonl.gz'))
    return result


def audit(output, data_dir):
    journal = Journal(output)
    code = source_manifest()
    began = time.monotonic()
    pipe = B4Pipeline(data_dir, None)
    counters = Counter()
    mismatches, observations, frozen = [], [], {}
    plans = {}
    for name, input_path, packet_path, response_path in sources():
        if not response_path.is_file():
            # Old runs may keep only first_part journals, but never silently
            # assume one valid response if no preserved selection is available.
            raise ValueError('Missing preserved selection: ' + str(response_path))
        for path in (input_path, packet_path, response_path):
            frozen[str(path.relative_to(ROOT))] = sha256(path)
        recs = {r['id']: r for r in rows(input_path)}
        packets = {p['request_key']: p for p in rows(packet_path)}
        for stored in rows(response_path):
            original = stored.get('packet', packets[stored['request_key']])
            if original['family'] != 'A' or tuple(original['items']) != ITEMS:
                continue
            if original['generation']['response_format'] not in {'fact_compact', 'factored'}:
                continue
            rec = recs[original['record_id']]
            response = stored['response']
            if parse_error(original, response):
                raise ValueError('Invalid preserved A10 response')
            before, _ = pipe.consume(rec, original, response)
            identity = digest(rec)
            if identity not in plans:
                plans[identity] = plan(rec, pipe.knowledge)
            question_plan = plans[identity]
            packet = copy.deepcopy(original)
            # This packet is for CPU projection only. It is never submitted as
            # a model input and no token/input-effect claim is made about it.
            packet['batch'] = 'A10'
            packet['source_questions'] = question_plan
            packet['source_questions_sha256'] = digest(question_plan)
            packet['items'] = question_plan['model_items'] or list(ITEMS)
            if not question_plan['model_items']:
                after, _ = code_only(rec, packet, pipe.knowledge)
                counters['code_only_projections'] += 1
            else:
                obj = json.loads(response['text'])
                judgments = obj['judgments']
                if set(judgments) == {'v', 'e'}:
                    obj['judgments'] = {key: [judgments[key][i - 10] for i in packet['items']]
                                        for key in ('v', 'e')}
                else:
                    obj['judgments'] = {f'v{i}': judgments[f'v{i}'] for i in packet['items']}
                diagnostic = {'text': json.dumps(obj, ensure_ascii=False),
                              'finish_reason': response['finish_reason']}
                after, _ = pipe.consume(rec, packet, diagnostic)
            differences = {key: {'before': value, 'after': after.get(key)}
                           for key, value in before.items() if after.get(key) != value}
            if differences:
                mismatches.append({'source': name, 'request_key': original['request_key'], 'differences': differences})
            counters[name] += 1
            counters['projected_A10_responses'] += 1
            observations.append({'source': name, 'request_key': original['request_key'],
                'record_sha256': identity, 'fixed_items': list(question_plan['fixed']),
                'model_items': question_plan['model_items'], 'consumption_equal': not differences})
        journal.progress(phase='projected_source_question_consumption', source=name,
                         comparisons=counters['projected_A10_responses'], mismatches=len(mismatches))
    journal.rows('observations.jsonl.gz', observations)
    journal.save('mismatches.json', mismatches)
    if code != source_manifest() or any(sha256(ROOT / path) != value for path, value in frozen.items()):
        raise RuntimeError('Source or preserved evidence changed during projection')
    report = {'version': 'source_question_cpu_projection_v1', 'counts': dict(counters),
        'mismatches': len(mismatches), 'exact_bits_and_evidence': not mismatches,
        'source_sha256': code, 'frozen_input_hashes': frozen,
        'new_model_calls': 0, 'labels_read': False, 'fresh_inference_score': None,
        'seconds': time.monotonic() - began,
        'limitation': 'Diagnostic projection verifies source/consumer partition only; changed prompts and schedule require new inference.'}
    journal.save('report.json', report)
    if mismatches:
        raise ValueError('Source-question partition changes stored consumption; inspect all mismatches')
    return {k: report[k] for k in ('counts', 'mismatches', 'exact_bits_and_evidence', 'seconds')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data_open/data')
    args = parser.parse_args()
    print(json.dumps(audit(args.output, args.data_dir), ensure_ascii=False, indent=2))
