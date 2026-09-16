"""Require exact CPU producer identity across platforms before GPU allocation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import digest
from submission.runtime import Journal, sha256
from tools.run_runtime_revival import read, rows, verify_prepared


def compare_packets(expected, observed):
    for name, packets in (('expected', expected), ('observed', observed)):
        keys = [p['request_key'] for p in packets]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError(name + ' producer contains empty or duplicate requests')
    a = {p['request_key']: p for p in expected}
    b = {p['request_key']: p for p in observed}
    changed = []
    for key in sorted(a.keys() & b.keys()):
        if digest(a[key]) != digest(b[key]):
            fields = sorted(k for k in a[key].keys() | b[key].keys()
                            if k not in a[key] or k not in b[key]
                            or digest(a[key][k]) != digest(b[key][k]))
            changed.append({'request_key': key, 'fields': fields})
    return {
        'status': 'PASS' if digest(expected) == digest(observed) else 'FAIL',
        'expected_requests': len(expected), 'observed_requests': len(observed),
        'expected_digest': digest(expected), 'observed_digest': digest(observed),
        'request_order_equal': [p['request_key'] for p in expected] == [p['request_key'] for p in observed],
        'missing': sorted(a.keys() - b.keys()), 'extra': sorted(b.keys() - a.keys()),
        'changed_packets': changed,
        'comparison': 'Exact canonical JSON digest, including diagnostics; no float tolerance or ignored fields.',
    }


def verify(prepared, remote, output):
    frozen = verify_prepared(prepared)
    expected = rows(prepared / 'primary_packets.jsonl.gz')
    observed = rows(remote / 'result/current_packets.jsonl.gz')
    report = read(remote / 'result/report.json')
    exit_report = read(remote / 'exit.json')
    if (exit_report['status'] != 'complete' or report['source_sha256'] != frozen['source_sha256']
            or report['requests'] != len(observed) or report['records'] != frozen['records']
            or report['packets_digest'] != digest(observed)
            or any(report[k] is not False for k in ('gpu_used', 'model_loaded', 'labels_read'))
            or report['new_model_calls'] != 0):
        raise ValueError('CPU producer provenance or completion does not match the prepared source')
    result = compare_packets(expected, observed)
    result.update(prepared_sha256=sha256(prepared / 'input_freeze.json'),
                  remote_report_sha256=sha256(remote / 'result/report.json'),
                  source_fingerprint=report['source_fingerprint'],
                  remote_platform=report['platform'], remote_python=report['python'],
                  remote_packages=report['packages'], remote_seconds=report['seconds'],
                  source_sha256=frozen['source_sha256'], labels_read=False, gpu_used=False)
    Journal(output).save('report.json', result)
    return {k: v for k, v in result.items() if k != 'source_sha256'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prepared', 'remote', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.prepared, args.remote, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)
