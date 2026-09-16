"""Audit saved optional software relations with the current CPU consumer.

Reuses every frozen final attempt, including its unresolved format failures.
No prompts, model outputs, labels or retrieval selections are regenerated.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import B4Pipeline, parse_error
from submission.runtime import source_manifest


def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))


def keyed(path):
    values = rows(path)
    result = {r['request_key']: r for r in values}
    if len(result) != len(values):
        raise ValueError('Duplicate request identities: ' + str(path))
    return result


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def replay(diagnostic, prior, output):
    if output.exists():
        raise ValueError('Choose a fresh output directory')
    paths = [diagnostic / name for name in (
        'current_inputs.jsonl.gz', 'current_packets.jsonl.gz',
        'resolved_responses.jsonl.gz', 'contrast_results.jsonl.gz')]
    paths.append(prior / 'decisions.jsonl.gz')
    frozen = {str(p.resolve()): sha(p) for p in paths}
    code = source_manifest()
    output.mkdir(parents=True, exist_ok=False)
    save(output / 'preregistered.json', {
        'kind': 'saved_software_responses_current_CPU_replay',
        'source_sha256': code, 'inputs_sha256': frozen,
        'tool_sha256': sha(Path(__file__)), 'new_model_calls': 0, 'labels_read': False,
        'cohort': 'All saved final attempts; invalid responses retained and unscored.',
        'hypothesis': 'Exact source may negate, qualify, or fail to express a model-claimed required action.',
        'limitation': 'Exposed development witnesses; no new input or fresh inference effect measured.'})
    packets = keyed(paths[1])
    selected = keyed(paths[2])
    observed = keyed(paths[3])
    previous = keyed(paths[4])
    if set(packets) != set(observed) or set(observed) != set(previous):
        raise ValueError('Comparison must cover exactly the same requests')
    if set(selected) != {k for k, r in observed.items() if r['parse_error'] is None}:
        raise ValueError('Frozen selected responses differ from format-valid final attempts')
    recs = rows(paths[0])
    by_id = {r['id']: r for r in recs}
    if len(by_id) != len(recs):
        raise ValueError('Duplicate notice identities')
    pipeline = B4Pipeline(ROOT / 'data_open/data', None)
    changed, relation_changes, errors = [], [], []
    started = time.monotonic()
    with gzip.open(output / 'decisions.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for key, row in observed.items():
            packet = packets[key]
            if row['parse_error'] is not None:
                if parse_error(packet, row['response']) is None:
                    raise ValueError('Formerly invalid format now parses; audit recovery separately')
                errors.append({'request_key': key, 'parse_error': row['parse_error']})
                result = {k: v for k, v in row.items() if k != 'response'}
                assert result['row'] is None
            else:
                chosen = selected[key]
                if chosen['response'] != row['response'] or chosen['attempt'] != row['attempt']:
                    raise ValueError('Selected raw response differs from the frozen final attempt')
                for field, value in packet.items():
                    if field != 'generation' and chosen['packet'].get(field) != value:
                        raise ValueError('Recovery altered a fixed input field: ' + field)
                packet = chosen['packet']
                if packet['generation']['response_format'] not in {'software_facts', 'software_refs'}:
                    raise ValueError('Expected optional software relation schema')
                if error := parse_error(packet, row['response']):
                    raise ValueError(key + ': ' + error)
                pred, details = pipeline.consume(by_id[packet['record_id']], packet, row['response'])
                result = {**{k: v for k, v in row.items() if k != 'response'}, 'row': pred, 'details': details}
                old = previous[key]['details'][0]['decision']
                new = details[0]['decision']
                if old['facts'] != new['facts']:
                    raise ValueError('Raw model claims or exact source locations changed')
                if previous[key]['row'] != pred:
                    changed.append({'request_key': key, 'old': previous[key]['row'], 'new': pred,
                                    'old_semantic_value': old['value'], 'new_semantic_value': new['value']})
                for before, after in zip(old['semantic_audit']['relations'], new['semantic_audit']['relations']):
                    if before != after:
                        index = after['index']
                        relation_changes.append({'request_key': key, 'index': index,
                            'old_issues': before['issues'], 'new_issues': after['issues'],
                            'claim': new['facts']['relations'][index]})
            stream.write(json.dumps(result, ensure_ascii=False) + '\n')
    if source_manifest() != code or any(sha(Path(p)) != h for p, h in frozen.items()):
        raise RuntimeError('Source or inputs changed during replay')
    report = {'kind': 'saved_software_responses_current_CPU_replay', 'source_sha256': code,
        'requests': len(observed), 'format_valid': len(selected), 'format_unresolved': errors,
        'changed_bits': changed, 'changed_relation_reviews': relation_changes,
        'decisions_sha256': sha(output / 'decisions.jsonl.gz'),
        'new_model_calls': 0, 'labels_read': False, 'source_unchanged': True,
        'seconds': time.monotonic() - started, 'fresh_score': None}
    save(output / 'summary.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnostic', type=Path, required=True)
    parser.add_argument('--prior-replay', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = replay(args.diagnostic.resolve(), args.prior_replay.resolve(), args.output.resolve())
    print(json.dumps({k: v for k, v in report.items() if k not in {'source_sha256', 'changed_relation_reviews'}}, ensure_ascii=False))
    print('Changed relation reviews:', len(report['changed_relation_reviews']))
