"""Replay preserved scope responses through the current consumer, without labels.

The first response accepted by this boundary is used, including a primary that
only needed lossless reference-set canonicalization. No semantic best-of choice
or new generation occurs. Original fresh measurements remain unchanged.
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
from submission.b4_entry import B4Pipeline, digest, parse_error, restored
from submission.pps.data import read_csv, write_csv
from submission.runtime import source_manifest
from tools.score_catalog_scope import join_arm, verified_replay_path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))


def first_accepted(packet, attempts):
    ordered = sorted(attempts, key=lambda r: r['attempt'])
    if not ordered or len({r['attempt'] for r in ordered}) != len(ordered):
        raise ValueError('Missing or duplicate response attempt')
    for row in ordered:
        if parse_error(packet, row['response']) is None:
            return row
    return ordered[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True, help='Preserved contrast_output')
    parser.add_argument('--baseline-run', type=Path, required=True)
    parser.add_argument('--baseline-replay', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Use a new replay directory')
    code = source_manifest()
    baseline_path = verified_replay_path(args.baseline_run, args.baseline_replay, code)
    baseline = read_csv(baseline_path)
    original = rows(args.baseline_run / 'current_inputs.jsonl.gz')
    by_id = {r['id']: r for r in original}
    inputs = rows(args.run / 'current_inputs.jsonl.gz')
    assert all(by_id[r['id']] == r for r in inputs)
    selected_ids = {r['id'] for r in inputs}
    assert len(selected_ids) == len(inputs)
    packets = rows(args.run / 'current_packets.jsonl.gz')
    index = {p['request_key']: p for p in packets}
    assert len(index) == len(packets)
    paths = [*sorted(args.run.glob('primary_*.jsonl.gz')), *sorted(args.run.glob('recovery_*.jsonl.gz'))]
    attempts = {key: [] for key in index}
    for path in paths:
        for row in rows(path):
            p = index[row['request_key']]
            assert row['record_id'] == p['record_id'] and row['arm'] == p['arm']
            attempts[row['request_key']].append(row)
    hashes = {p.as_posix(): sha(p) for p in [*paths, args.run / 'current_inputs.jsonl.gz',
        args.run / 'current_packets.jsonl.gz', args.run / 'input_freeze.json', baseline_path]}
    native = {(r['request_key'], r['attempt']): r for p in args.run.glob('*_native.jsonl.gz') for r in rows(p)}
    pipeline = B4Pipeline(ROOT / 'data_open/data', None)
    output, normalizations = [], []
    args.output.mkdir(parents=True, exist_ok=False)
    for p in packets:
        assert digest(p['messages']) == p['prompt_sha256'] and digest(p['token_ids']) == p['token_ids_sha256']
        record = by_id[p['record_id']]
        assert all(record['docs'][s.doc_index]['text'][s.start:s.end] == s.text for s in restored(p)['spans'])
        chosen = first_accepted(p, attempts[p['request_key']])
        actual = native[(p['request_key'], chosen['attempt'])]
        assert actual['prompt_sha256'] == p['prompt_sha256'] and actual['token_ids_sha256'] == p['token_ids_sha256']
        error = parse_error(p, chosen['response'])
        row, log = (None, None) if error else pipeline.consume(record, p, chosen['response'])
        result = {'request_key': p['request_key'], 'record_id': p['record_id'], 'arm': p['arm'],
            'attempt': chosen['attempt'], 'parse_error': error, 'row': row, 'details': log,
            'raw_values': None, 'model_judgment_present': False,
            'response_sha256': hashlib.sha256(chosen['response']['text'].encode()).hexdigest()}
        output.append(result)
        if log and log['reference_normalization']['changes']:
            normalizations.append({'request_key': p['request_key'], **log['reference_normalization']})
    measurements = []
    for arm in sorted({p['arm'] for p in packets}):
        part = [r for r in output if r['arm'] == arm]
        joined, join = join_arm(baseline, part, selected_ids)
        result = {'arm': arm, 'join': join, 'valid': sum(r['parse_error'] is None for r in part),
            'gates': dict(Counter((r['details'] or {}).get('gate', 'invalid_format') for r in part))}
        if joined is not None:
            path = args.output / (arm + '.csv')
            write_csv(path, joined, recs=original, require_positive_evidence=False)
            result.update(prediction=path.as_posix(), prediction_sha256=sha(path))
        measurements.append(result)
    assert source_manifest() == code and all(sha(Path(p)) == h for p, h in hashes.items())
    with gzip.open(args.output / 'decisions.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for row in output:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    report = {'kind': 'saved_baseline_and_scope_current_CPU_replay', 'new_model_calls': 0,
        'labels_read': False, 'source_sha256': code, 'input_sha256': hashes,
        'first_accepted_response_policy': 'Earliest valid boundary representation; never select by semantic outcome.',
        'baseline_prediction': baseline_path.as_posix(), 'records': len(baseline),
        'scope_notices': len(inputs), 'responses_consumed': len(output), 'measurements': measurements,
        'normalizations': normalizations, 'official_score': None, 'full_fresh_inference': False,
        'limitation': 'Current CPU on frozen old prompts, sources and responses. Retrieval and input-generation changes are not evaluated.'}
    (args.output / 'freeze.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'measurements': measurements, 'normalized_responses': len(normalizations)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
