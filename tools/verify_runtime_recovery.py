"""Verify recovered normal/probe native evidence before releasing the GPU."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.data import read_csv
from submission.runtime import Journal, parse_error, sha256
from tools.run_runtime_revival import rows, read, verify_prepared, digest
from tools.run_integrated_comparison import verify_resolved_native


def verify(prepared, run, output):
    frozen = verify_prepared(prepared)
    journal = Journal(output)
    records = rows(prepared/'current_inputs.jsonl.gz')
    primary = rows(prepared/'primary_packets.jsonl.gz')
    probes = rows(prepared/'probe_packets.jsonl.gz')
    if (digest(rows(run/'normal/current_inputs.jsonl.gz')) != digest(records)
            or digest(rows(run/'normal/current_packets.jsonl.gz')) != digest(primary)):
        raise ValueError('Recovered runtime inputs differ from local preparation')
    normal_done = read(run/'normal_completed.json')
    primary_report = read(run/'normal/run_report.json')
    complete = read(run/'complete.json')
    shutdown = read(run/'engine_shutdown.json')
    if (normal_done['report'] != primary_report or complete['normal_report'] != primary_report
            or not normal_done['diagnostic_probes_not_started']
            or primary_report['source_unchanged'] is not True
            or complete['source_unchanged'] is not True
            or complete['engine_loads'] != 1 or shutdown != {'status': 'shutdown_returned', 'engine_loads': 1}):
        raise ValueError('Incomplete normal/probe lifecycle receipts')
    csv_sha = sha256(run/'normal/submission.csv')
    if (normal_done['prediction_sha256'] != csv_sha
            or read(run/'normal/prediction_freeze.json')['files']['submission.csv'] != csv_sha):
        raise ValueError('Normal CSV differs from its pre-probe freeze')
    ids = [r['id'] for r in read_csv(run/'normal/submission.csv')]
    if len(ids) != len(records) or set(ids) != {r['id'] for r in records}:
        raise ValueError('Normal CSV does not cover the full cohort exactly once')
    lookup = {p['request_key']: p for p in primary+probes}
    skips = read(run/'normal/skipped_requests.json')
    for key, entry in skips.items():
        from submission.skips import validate
        if key not in lookup:
            raise ValueError('Skipped request is absent from prepared packets')
        validate(lookup[key], entry)
    selected = rows(run/'normal/resolved_responses.jsonl.gz')
    if len({r['request_key'] for r in selected}) != len(selected):
        raise ValueError('Duplicate selected normal response')
    if {r['request_key'] for r in selected} != {p['request_key'] for p in primary}-skips.keys():
        raise ValueError('Incomplete normal response and skip accounting')
    valid = [{k: r[k] for k in ('request_key', 'attempt', 'response')} for r in selected
             if parse_error(lookup[r['request_key']], r['response']) is None]
    normal_verification = verify_resolved_native(run/'normal', [p for p in primary if p['request_key'] not in skips], valid)
    resolved_probes = rows(run/'probes/resolved_responses.jsonl.gz')
    if len({r['request_key'] for r in resolved_probes}) != len(resolved_probes):
        raise ValueError('Duplicate selected probe response')
    probe_verification = verify_resolved_native(run/'probes', probes, resolved_probes)
    summary = read(run/'probes/generation_summary.json')
    if (summary['valid_responses'] != len(resolved_probes)
            or complete['diagnostic_valid'] != len(resolved_probes)
            or complete['diagnostic_primary_requests'] != len(probes)
            or primary_report['primary_requests'] != len(primary)-len(skips)
            or primary_report['returned'] != normal_verification['all_parsed_attempts']):
        raise ValueError('Native counts differ from completion receipts')
    for value in (normal_verification, probe_verification):
        if value['unreturned_requests'] or value['unparsed_native_attempts'] or value['unrequested_primary_keys']:
            raise ValueError('Completed round has unaccounted native attempts')
    result = {'status': 'PASS', 'prepared_sha256': sha256(prepared/'input_freeze.json'),
        'source_sha256': frozen['source_sha256'], 'normal_csv_sha256': csv_sha, 'records': len(records),
        'normal': normal_verification, 'probes': probe_verification, 'skipped_source_reviews': len(skips),
        'normal_selected_valid': len(valid), 'probe_selected_valid': len(resolved_probes),
        'normal_format_retries': primary_report['parse_retries'], 'probe_format_retries': summary['format_retries'],
        'single_engine_shutdown_verified': True, 'labels_read': False, 'normal_csv_before_probes_verified': True}
    journal.save('report.json', result)
    return {k: v for k, v in result.items() if k != 'source_sha256'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prepared', 'run', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.prepared, args.run, args.output), ensure_ascii=False, indent=2))
