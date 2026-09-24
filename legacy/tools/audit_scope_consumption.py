"""Label-free audit of task anchors available to, and selected by, scope review.

These are structural field-coverage observations, not semantic gold or proof
that every condition needed for a legal judgment has been read.
"""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import restored
from submission.pps.catalog_scope import whole_task_witnesses
from submission.runtime import source_manifest


def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True, help='Recovered contrast_output directory')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Use a new audit output path')
    freeze = json.loads((args.run/'input_freeze.json').read_text(encoding='utf-8'))
    assert freeze['source_sha256'] == source_manifest(), 'Audit with the frozen consumer first'
    recs = {r['id']: r for r in rows(args.run/'current_inputs.jsonl.gz')}
    packets = {p['request_key']: restored(p) for p in rows(args.run/'current_packets.jsonl.gz')}
    observed = rows(args.run/'contrast_results.jsonl.gz')
    assert len(observed) == len(packets) == freeze['primary_requests']
    assert {r['request_key'] for r in observed} == packets.keys()
    aggregates, details = {}, []
    for result in observed:
        packet = packets[result['request_key']]
        assert packet['record_id'] == result['record_id'] and packet['arm'] == result['arm']
        record = recs[result['record_id']]
        spans = packet['spans']
        available = whole_task_witnesses(record, spans, range(1, len(spans)+1))
        log = result['details'] or {}
        model = log.get('model_scope')
        selected = whole_task_witnesses(record, spans, model['whole_task_units']) if model else []
        gate = log.get('gate', 'invalid_format')
        counters = aggregates.setdefault(result['arm'], Counter())
        counters['records'] += 1
        counters['complete_task_field_available'] += bool(available)
        counters['complete_task_field_selected'] += bool(selected)
        counters['available_but_not_selected'] += bool(available) and not selected
        counters['consumer_scope_promoted'] += bool(log.get('source_scope_promoted'))
        counters['gate:'+gate] += 1
        details.append({'id': result['record_id'], 'arm': result['arm'], 'gate': gate,
            'parse_error': result['parse_error'], 'model_scope': model,
            'available_whole_task_fields': available, 'selected_whole_task_fields': selected,
            'decisions': log.get('decisions', {}), 'row': result['row']})
    report = {'kind': 'same_frozen_consumer_structural_scope_audit', 'labels_read': False,
        'new_model_calls': 0, 'source_sha256': freeze['source_sha256'],
        'input_sha256': {n: hashlib.sha256((args.run/n).read_bytes()).hexdigest() for n in
            ('current_inputs.jsonl.gz', 'current_packets.jsonl.gz', 'contrast_results.jsonl.gz')},
        'interpretation': 'Complete original task-field coverage is structural, not semantic task identity or full legal evidence-bundle recall. No absence conclusion is inferred.',
        'aggregates': aggregates, 'details': details}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(aggregates, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
