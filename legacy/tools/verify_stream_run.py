"""Compare a streaming run with a frozen cohort baseline: inputs, calls, cells, scores.

Input equivalence (same token IDs per request key) is the proof that parallel
preparation and the GPU encoder produced the canonical prompts. Cell equality
against the baseline CSV shows what the scheduler changed; labels, when given
locally, add both scores. A run over a record subset is compared on that
subset only. None of this is an official measurement.
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.data import read_csv

CRLF = '\r\n'


def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def subset_csv(path, ids, name):
    with open(path, encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        kept = [row for row in reader if row['id'] in ids]
        fields = reader.fieldnames
    target = Path(tempfile.mkdtemp()) / name
    with target.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator=CRLF)
        writer.writeheader()
        writer.writerows(kept)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True, help='stream executor output directory')
    parser.add_argument('--baseline-packets', type=Path, help='frozen primary_packets.jsonl.gz')
    parser.add_argument('--baseline-responses', type=Path, help='frozen run directory with call_*_responses.jsonl.gz')
    parser.add_argument('--baseline-csv', type=Path, help='frozen submission.csv to compare cells against')
    parser.add_argument('--labels', type=Path, help='local development labels; never uploaded')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Use a new verification output path')
    report = {'run': str(args.run), 'official_measurement': False}
    report_json = json.loads((args.run / 'run_report.json').read_text(encoding='utf-8'))
    report['run_report'] = {k: report_json.get(k) for k in ('records', 'requests_submitted', 'responses',
        'unresolved_requests', 'source_rule_fallbacks', 'tier_counts', 'tier_timeline', 'seconds',
        'records_without_model_call', 'records_after_submission_deadline', 'aborted_requests')}
    packets = [row for name in report_json['journal_files']['packets'] for row in rows(args.run / name)]
    by_key = {p['request_key']: p for p in packets}
    run_ids = {p['record_id'] for p in packets}
    if args.baseline_packets:
        baseline = {p['request_key']: p for p in rows(args.baseline_packets) if p['record_id'] in run_ids}
        per_profile = collections.defaultdict(lambda: collections.Counter())
        mismatches = []
        for key, packet in by_key.items():
            profile = packet['batch']
            base = baseline.get(key)
            if base is None:
                per_profile[profile]['extra'] += 1
            elif base['token_ids_sha256'] == packet['token_ids_sha256']:
                per_profile[profile]['identical_token_ids'] += 1
            else:
                per_profile[profile]['different_token_ids'] += 1
                mismatches.append(key)
        for key, base in baseline.items():
            if key not in by_key:
                per_profile[base['batch']]['missing'] += 1
        report['packets'] = {'by_profile': {k: dict(v) for k, v in per_profile.items()},
                             'different_token_ids': mismatches[:50],
                             'all_identical': not mismatches and not any(v['missing'] or v['extra'] for v in per_profile.values())}
    natives = [row for name in report_json['journal_files']['native'] for row in rows(args.run / name)]
    called = {row['request_key'] for row in natives if row['attempt'] == 0}
    if args.baseline_responses:
        baseline_called = set()
        for path in sorted(args.baseline_responses.glob('call_*_responses.jsonl.gz')):
            baseline_called.update(r['request_key'] for r in rows(path)
                                   if r.get('attempt', 0) == 0 and r['request_key'].split(':')[1] in run_ids)
        report['calls'] = {'run': len(called), 'baseline': len(baseline_called),
                           'only_in_run': sorted(called - baseline_called)[:50],
                           'only_in_baseline': sorted(baseline_called - called)[:50],
                           'identical_call_set': called == baseline_called}
    if args.baseline_csv:
        candidate = {r['id']: r for r in read_csv(args.run / 'submission.csv')}
        base = {r['id']: r for r in read_csv(args.baseline_csv)}
        if not candidate.keys() <= base.keys():
            raise ValueError('Run CSV contains ids absent from the baseline')
        base = {rid: base[rid] for rid in candidate}
        value_changes = collections.Counter()
        evidence_changes = collections.Counter()
        changed = []
        for rid in candidate:
            for k in range(1, 25):
                if candidate[rid][f'v{k}'] != base[rid][f'v{k}']:
                    value_changes[f'v{k}'] += 1
                    changed.append({'id': rid, 'item': k, 'baseline': base[rid][f'v{k}'], 'run': candidate[rid][f'v{k}']})
                if candidate[rid][f'e{k}'] != base[rid][f'e{k}']:
                    evidence_changes[f'e{k}'] += 1
        report['cells'] = {'records': len(candidate), 'value_changes': dict(value_changes),
                           'value_changes_total': sum(value_changes.values()),
                           'evidence_changes_total': sum(evidence_changes.values()),
                           'changed': changed[:200], 'identical': not value_changes and not evidence_changes}
    if args.labels:
        sys.path.insert(0, str(ROOT / 'tools'))
        from evaluate import evaluate
        labels = subset_csv(args.labels, run_ids, 'labels_subset.csv')
        scored = {'run': evaluate(labels, args.run / 'submission.csv')}
        if args.baseline_csv:
            scored['baseline'] = evaluate(labels, subset_csv(args.baseline_csv, run_ids, 'baseline_subset.csv'))
        report['scores'] = {name: {'macro_f1': m['macro_f1'],
                                   'fp': sum(e['prediction'] == 1 for e in m['errors']),
                                   'fn': sum(e['prediction'] == 0 for e in m['errors'])} for name, m in scored.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    summary = {k: report[k] for k in ('packets', 'calls', 'cells', 'scores') if k in report}
    for key in ('packets', 'cells'):
        if key in summary:
            summary[key] = {k: v for k, v in summary[key].items() if k not in ('changed', 'different_token_ids')}
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
