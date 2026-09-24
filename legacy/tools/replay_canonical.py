"""Replay a frozen canonical journal through the current CPU consumer, without labels.

This deliberately does not regenerate packets or claim a fresh inference score.
Use evaluate.py separately after the predictions and source manifest are frozen.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import B4Pipeline, assemble, parse_error
from submission.pps.data import write_csv
from submission.runtime import source_manifest, call_plan


def read_rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replay(source_run, output, data_dir):
    source_run, output = Path(source_run).resolve(), Path(output).resolve()
    if output.exists() or output == source_run or output.is_relative_to(source_run):
        raise ValueError('Choose a new output directory outside the preserved source run')
    recs = read_rows(source_run / 'current_inputs.jsonl.gz')
    packets = read_rows(source_run / 'current_packets.jsonl.gz')
    by_id = {rec['id']: rec for rec in recs}
    keys = {p['request_key'] for p in packets}
    if not recs or len(by_id) != len(recs) or len(keys) != len(packets):
        raise ValueError('Empty or duplicated input identities')
    call_plan(recs, packets)
    responses, inputs = {}, [source_run / 'current_inputs.jsonl.gz', source_run / 'current_packets.jsonl.gz']
    selected_packets = {p['request_key']: p for p in packets}
    skip_path=source_run/'skipped_requests.json'
    skipped=json.loads(skip_path.read_text(encoding='utf8')) if skip_path.is_file() else {}
    if skipped:
        inputs.append(skip_path)
        for key,entry in skipped.items():
            from submission.skips import validate
            if key not in selected_packets:
                raise ValueError('Skipped request is absent from prepared packets')
            validate(selected_packets[key], entry)
    resolved_path = source_run / 'resolved_responses.jsonl.gz'
    response_paths = [resolved_path] if resolved_path.is_file() else sorted(source_run.glob('first_part_*.jsonl.gz'))
    for path in response_paths:
        inputs.append(path)
        for row in read_rows(path):
            key = row['request_key']
            if key in responses:
                raise ValueError('Duplicate response key: ' + key)
            if path == resolved_path:
                if row.get('error') is not None or key not in selected_packets:
                    raise ValueError('Unresolved or unexpected selected response: ' + key)
                chosen, primary = row['packet'], selected_packets[key]
                # Format recovery can alter generation settings, never silently
                # replace the notice, evidence, schema or input token sequence.
                for field, value in primary.items():
                    if field != 'generation' and chosen.get(field) != value:
                        raise ValueError('Recovery changed primary field: ' + field)
                selected_packets[key] = chosen
            responses[key] = row['response']
    if responses.keys() != keys-skipped.keys():
        raise ValueError('Stored responses and packets must have exactly the same keys')
    code = source_manifest()
    frozen_inputs = {str(p): file_hash(p) for p in inputs}
    pipeline = B4Pipeline(data_dir, None)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    consumed = {}
    with gzip.open(output / 'decisions.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for packet in packets:
            packet = selected_packets[packet['request_key']]
            if packet['request_key'] in skipped:
                from submission.skips import consume
                row,details=consume(by_id[packet['record_id']], packet,
                                    skipped[packet['request_key']], pipeline.knowledge)
            else:
                response = responses[packet['request_key']]
                error = parse_error(packet, response)
                if error:
                    raise ValueError(packet['request_key'] + ': ' + error)
                row, details = pipeline.consume(by_id[packet['record_id']], packet, response)
            consumed[packet['request_key']] = row
            stream.write(json.dumps({'request_key': packet['request_key'], 'row': row,
                                     'details': details}, ensure_ascii=False) + '\n')
    b3, b4 = assemble(recs, packets, consumed)
    for name, rows in (('B3', b3), ('B4', b4)):
        write_csv(output / (name + '.csv'), list(rows.values()), recs=recs,
                  require_positive_evidence=pipeline.config.require_positive_evidence)
    if source_manifest() != code or any(file_hash(Path(p)) != h for p, h in frozen_inputs.items()):
        raise RuntimeError('Source or replay inputs changed during execution')
    report = {'kind': 'saved_response_current_cpu_replay', 'new_model_calls': 0,
              'labels_read': False, 'records': len(recs), 'responses': len(responses),
              'skipped_requests':len(skipped),
              'source_run': str(source_run), 'source_sha256': code, 'input_sha256': frozen_inputs,
              'prediction_sha256': {p.name: file_hash(p) for p in output.glob('*.csv')},
              'seconds': time.monotonic() - started, 'official_score': None,
              'limitation': 'Stored prompts and responses; changes to input generation are not evaluated.'}
    (output / 'freeze.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data_open/data')
    args = parser.parse_args()
    report = replay(args.source_run, args.output, args.data_dir)
    print(json.dumps({k: v for k, v in report.items() if not k.endswith('sha256')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
