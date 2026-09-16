"""Analyze a complete fresh software-witness contrast after local recovery.

Format failures never become predicted zero. Unresolved semantic decisions are
reported separately from the competition's zero output policy. Review cases are
exposed development examples, not independent validation or a full24-item score.
"""
import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path


def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))


def score(folder, labels):
    freeze = json.loads((folder/'input_freeze.json').read_text(encoding='utf-8'))
    summary = json.loads((folder/'contrast_summary.json').read_text(encoding='utf-8'))
    packets = {p['request_key']: p for p in rows(folder/'current_packets.jsonl.gz')}
    final = rows(folder/'contrast_results.jsonl.gz')
    primary = [r for p in sorted(folder.glob('primary_*.jsonl.gz')) for r in rows(p)]
    attempts = primary + [r for p in sorted(folder.glob('recovery_*.jsonl.gz')) for r in rows(p)]
    assert len(packets) == len(final) == len(primary) == freeze['primary_requests'] == 36
    assert {r['request_key'] for r in primary} == {r['request_key'] for r in final} == set(packets)
    assert summary['source_unchanged'] and not summary['labels_read']
    assert all(p['items'] == [20] for p in packets.values())
    formats = {k: p['generation']['response_format'] for k, p in packets.items()}
    assert set(formats.values()) == {'software_facts', 'software_refs'}
    with labels.open(encoding='utf-8-sig', newline='') as stream:
        gold = {r['id']: r for r in csv.DictReader(stream)}
    validity, measurements, observations = {}, [], []
    for fmt in sorted(set(formats.values())):
        part = [r for r in final if formats[r['request_key']] == fmt]
        first = [r for r in primary if formats[r['request_key']] == fmt]
        all_attempts = [r for r in attempts if formats[r['request_key']] == fmt]
        assert len(part) == len(first) == 18
        invalid = [r for r in part if r['parse_error'] or r['row'] is None]
        validity[fmt] = {'primary': 18, 'first_valid': sum(r['parse_error'] is None for r in first),
            'retry_requests': len(all_attempts)-18, 'final_valid': 18-len(invalid),
            'unresolved': [{'request_key': r['request_key'], 'error': r['parse_error']} for r in invalid],
            'input_tokens': freeze['per_format'][fmt]['input_tokens'],
            'score_status': 'withheld_unresolved_format' if invalid else 'complete_format_cohort'}
        for r in part:
            d = next((x['decision'] for x in (r['details'] or []) if 'decision' in x), None)
            observations.append({'request_key': r['request_key'], 'case': r['case'], 'arm': r['arm'],
                'format': fmt, 'attempt': r['attempt'], 'parse_error': r['parse_error'],
                'value': None if r['row'] is None else r['row']['v20'],
                'semantic_value': None if d is None else d['value'],
                'reason': None if d is None else d['reason'], 'decision': d})
        if invalid:
            continue
        assert all(r['raw_values'] is None and not r['model_judgment_present'] for r in part)
        for arm in sorted({r['arm'] for r in part}):
            arm_rows = [r for r in part if r['arm'] == arm]
            assert len(arm_rows) == len({r['record_id'] for r in arm_rows}) == 6
            bits = [{'case': r['case'], 'id': r['record_id'], 'truth': int(gold[r['record_id']]['v20']),
                     'prediction': int(r['row']['v20'])} for r in arm_rows]
            tp = sum(b['truth'] == b['prediction'] == 1 for b in bits)
            fp = sum(b['truth'] == 0 and b['prediction'] == 1 for b in bits)
            fn = sum(b['truth'] == 1 and b['prediction'] == 0 for b in bits)
            measurements.append({'format': fmt, 'arm': arm, 'cases': 6, 'tp': tp, 'fp': fp, 'fn': fn,
                'v20_f1': 2*tp/max(1, 2*tp+fp+fn), 'bits': bits,
                'unknown': sum(o['semantic_value'] is None for o in observations if o['arm'] == arm and o['format'] == fmt)})
    return {'kind': 'fresh_software_witness_contract_comparison', 'validity': validity,
        'measurements': measurements, 'observations': observations,
        'paired_final_effect_available': all(v['final_valid'] == 18 for v in validity.values()),
        'format_error_counts': dict(Counter(str(r['parse_error']) for r in attempts if r['parse_error'])),
        'generation_seconds': summary['generation_seconds'], 'official_macro_f1': None,
        'full160_macro_f1': None, 'labels_sha256': hashlib.sha256(labels.read_bytes()).hexdigest(),
        'scope': 'Six exposed cases and three fixed source selections. Original source ranges match. '
            'Schema and source unit presentation differ; this is a harness comparison, not a retrieval gain. '
            'Exact source references prove provenance, not the model\'s interpretation. No partial or zero-filled format score.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--labels', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Use a new output path; preserve existing measurements')
    result = score(args.folder, args.labels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k not in {'observations', 'measurements'}}, ensure_ascii=False, indent=2))
    print(json.dumps([{k: v for k, v in m.items() if k != 'bits'} for m in result['measurements']], ensure_ascii=False))
