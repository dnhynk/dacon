"""Inventory explicit applicability uncertainty followed by a positive raw bit.

This is a frozen-response diagnostic, not a production correction. Its declared
counterfactual emits the competition's unresolved output0 for narrowly flagged
items; it never certifies normality or reads labels to choose a flag.
"""
import argparse
import copy
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import parse_error, restored
from submission.pps.data import read_csv, write_csv
from submission.pps.pipeline import parse_output
from submission.pps.response_contract import loads
from submission.runtime import source_manifest

from submission.pps.fact_consistency import PRODUCT_FIELD, SW_FIELDS, review, uncertain_statement


def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def audit(run, replay, output):
    if output.exists():
        raise ValueError('Choose a new output directory')
    code = source_manifest()
    frozen = json.loads((replay / 'freeze.json').read_text(encoding='utf-8'))
    if frozen['source_sha256'] != code:
        raise ValueError('Audit requires a replay of exactly the current source')
    paths = [run / 'resolved_responses.jsonl.gz', run / 'current_inputs.jsonl.gz',
             replay / 'decisions.jsonl.gz', replay / 'B4.csv', replay / 'freeze.json']
    fingerprints = {str(p.resolve()): sha(p) for p in paths}
    if frozen['prediction_sha256']['B4.csv'] != sha(paths[3]):
        raise ValueError('Replay prediction differs from its freeze')
    output.mkdir(parents=True, exist_ok=False)
    save(output / 'preregistered.json', {'source_sha256': code,
        'audit_tool_sha256': sha(Path(__file__)), 'inputs_sha256': fingerprints,
        'cohort': 'All final responses and all notices in this complete native run.',
        'flag': 'Explicit model uncertainty about a necessary applicability fact, raw positive, independent source unresolved.',
        'counterfactual': 'Only flagged still-positive items become unresolved output0, with empty evidence. No other changes.',
        'labels_read': False, 'new_model_calls': 0, 'production_change': False})
    observations = rows(paths[0])
    records = {r['id']: r for r in rows(paths[1])}
    decisions = {r['request_key']: r for r in rows(paths[2])}
    if len(observations) != len(records)*4 or len(decisions) != len(observations):
        raise ValueError('Expected all four native responses per current notice')
    keys = {r['request_key'] for r in observations}
    if len(keys) != len(observations) or keys != decisions.keys():
        raise ValueError('Duplicate or mismatched selected response identities')
    predictions = read_csv(paths[3])
    candidate = copy.deepcopy({r['id']: r for r in predictions})
    if len(candidate) != len(predictions) or candidate.keys() != records.keys():
        raise ValueError('Predictions and source notices must align exactly')
    flags, summary_statements = [], []
    for row in observations:
        p, response = row['packet'], row['response']
        if error := parse_error(p, response):
            raise ValueError(row['request_key'] + ': ' + error)
        obj = loads(response['text'])
        facts = obj.get('facts', {})
        values, _ = parse_output(response['text'], restored(p)['spans'], tuple(p['items']), rec=records[p['record_id']])
        values = {k: values[k-1] for k in p['items']}
        details = decisions[row['request_key']]['details']
        product = sw = None
        if p['family'] == 'A' and 10 in p['items']:
            product = next(x['facts']['product'] for x in details if x.get('source') == 'supplied_catalog_qualification_v2')
        elif p['family'] == 'L':
            sw = next(x['decision'] for x in details if x.get('source') == 'canonical_SW_rule_on_L_response')
        else:
            continue
        for name in ([PRODUCT_FIELD] if product is not None else SW_FIELDS):
            if statement := uncertain_statement(facts.get(name), 'catalog' if product is not None else 'software'):
                summary_statements.append({'request_key': row['request_key'], 'field': name,
                    **statement, 'values': values,
                    'source_status': product['status'] if product is not None else sw['reason']})
        for flag in review(facts, values, product, sw):
            rid, item = p['record_id'], flag['item']
            old = candidate[rid]['v'+str(item)]
            changed = old == '1'
            flags.append({'request_key': row['request_key'], 'record_id': rid, **flag,
                'current_value': int(old), 'counterfactual_changed': changed,
                'raw_facts': facts, 'raw_judgments': obj['judgments'],
                'raw_response_sha256': hashlib.sha256(response['text'].encode()).hexdigest()})
            if changed:
                candidate[rid]['v'+str(item)] = '0'
                candidate[rid]['e'+str(item)] = ''
    if source_manifest() != code or any(sha(Path(p)) != h for p, h in fingerprints.items()):
        raise RuntimeError('Source or evidence changed during audit')
    write_csv(output / 'counterfactual.csv', list(candidate.values()))
    result = {'source_sha256': code, 'requests': len(observations), 'records': len(records),
        'model_uncertain_statements': summary_statements, 'review_flags': flags,
        'changed_bits': sum(f['counterfactual_changed'] for f in flags),
        'prediction_sha256': sha(output / 'counterfactual.csv'),
        'new_model_calls': 0, 'labels_read': False, 'production_changed': False,
        'interpretation': 'A review trigger and an explicit unresolved-output counterfactual; neither proves the legal truth.'}
    save(output / 'report.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--replay', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.run.resolve(), args.replay.resolve(), args.output.resolve())
    print(json.dumps({k: v for k, v in result.items() if k not in {'source_sha256', 'model_uncertain_statements', 'review_flags'}}, ensure_ascii=False))
    for flag in result['review_flags']:
        print(flag['record_id'], flag['item'], flag['counterfactual_changed'], flag['matched_declaration'])
