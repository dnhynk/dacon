"""Reconsume every frozen SW relation and expose object/role boundary changes.

No inference or label reads. Native responses, packets and original CPU decisions
remain immutable; a new output directory records current-source decisions.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import restored
from submission.pps.software_facts import decide
from submission.runtime import Journal, sha256
from tools.run_runtime_revival import rows


def run(prepared, runtime, consumed, output):
    journal = Journal(output)
    records = {r['id']: r for r in rows(prepared/'current_inputs.jsonl.gz')}
    packets = {p['request_key']: p for p in rows(prepared/'primary_packets.jsonl.gz') if p['family'] == 'W'}
    selected = {r['request_key']: r for r in rows(runtime/'normal/resolved_responses.jsonl.gz')}
    original = {r['request_key']: r for r in rows(consumed/'consumed.jsonl.gz')}
    results, counts = [], Counter()
    for key, packet in packets.items():
        before = original[key]['details'][0]['decision']
        result = decide(records[packet['record_id']], selected[key]['response']['text'], restored(packet)['spans'],
                        expected_format='software_refs', absence_scope='source_scan')
        counts[str(before['value'])+'->'+str(result['value'])] += 1
        results.append({'request_key': key, 'record_id': packet['record_id'],
                        'before_value': before['value'], 'before_reason': before['reason'], 'decision': result})
    journal.save('decisions.json', results)
    changed = [{'id': r['record_id'], 'before': r['before_value'], 'after': r['decision']['value'],
                'reason': r['decision']['reason']} for r in results if r['before_value'] != r['decision']['value']]
    remaining = [{'id': r['record_id'], 'value': r['decision']['value'],
        'reason': r['decision']['reason'], 'relations': [{'action': a['action'], 'object': a['object'],
            'witnesses': [w['quote'] for w in a['witnesses']]} for a in r['decision']['actual_software_relations']]}
        for r in results if r['decision']['value'] == 1]
    report = {'records': len(results), 'transitions': dict(counts), 'changed': changed, 'positive': remaining,
              'prepared_sha256': sha256(prepared/'input_freeze.json'),
              'original_consumed_sha256': sha256(consumed/'consumed.jsonl.gz'),
              'software_source_sha256': {p.name: sha256(p) for p in (ROOT/'submission/pps').glob('software*.py')},
              'new_model_calls': 0, 'labels_read': False}
    journal.save('report.json', report)
    return {k: v for k, v in report.items() if k not in {'software_source_sha256', 'prepared_sha256', 'original_consumed_sha256'}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prepared', 'runtime', 'consumed', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    print(json.dumps(run(**vars(parser.parse_args())), ensure_ascii=False, indent=2))
