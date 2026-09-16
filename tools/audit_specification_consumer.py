"""Consume every frozen contract response with new diagnostics, without labels."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import B4Pipeline, parse_error
from submission.runtime import source_manifest
from tools.score_catalog_scope import rows, sha
from tools.score_specification_contrast import save, unique


def audit(run, output):
    output.mkdir(parents=True, exist_ok=False)
    source = source_manifest()
    folder = run / 'contrast_output'
    input_paths = [folder / name for name in ('current_inputs.jsonl.gz', 'current_packets.jsonl.gz',
        'contrast_results.jsonl.gz')]
    hashes = {str(p): sha(p) for p in input_paths}
    records = unique(rows(input_paths[0]), 'id')
    packets = unique(rows(input_paths[1]))
    final = unique(rows(input_paths[2]))
    assert packets.keys() == final.keys()
    pipeline = B4Pipeline(ROOT / 'data_open/data', None)
    flagged, counts, row_changes = [], Counter(), []
    with gzip.open(output / 'decisions.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for key, packet in packets.items():
            result = final[key]
            assert parse_error(packet, result['response']) is None
            row, details = pipeline.consume(records[packet['record_id']], packet, result['response'])
            if row != result['row']:
                row_changes.append(key)
            issues = [issue for d in details for issue in d.get('relationship_issues', [])]
            counts.update(issue['kind'] for issue in issues)
            if issues:
                flagged.append({'request_key': key, 'record_id': packet['record_id'],
                    'arm': packet['arm'], 'issues': issues})
            stream.write(json.dumps({'request_key': key, 'row': row, 'details': details}, ensure_ascii=False) + '\n')
    assert source_manifest() == source and all(sha(Path(p)) == h for p, h in hashes.items())
    report = {'kind': 'saved_specification_responses_current_diagnostics',
        'responses': len(final), 'row_changes': row_changes,
        'flagged_observations': len(flagged), 'issue_counts': dict(counts), 'flagged': flagged,
        'source_sha256': source, 'input_sha256': hashes, 'labels_read': False, 'new_model_calls': 0,
        'legal_bits_overridden_by_diagnostics': False, 'default_contract_promoted': False,
        'limitation': 'Diagnostics expose relations needing review. No semantic truth or F1 improvement is inferred from a flag.'}
    save(output / 'report.json', report)
    print(json.dumps({k: v for k, v in report.items() if k not in ('source_sha256', 'input_sha256', 'flagged')}, indent=2))
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    audit(a.run, a.output)
