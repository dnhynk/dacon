"""Resumable, single-process census of canonical model-free rule firings.

No labels, model weights, network, or generation are used. Each atomic part has
one envelope per input record (including zeros/errors), enabling exact coverage
and restart verification. `summarize` streams parts without loading notices.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import itertools
import json
import os
from pathlib import Path
import sys
import time
import traceback
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
META_KEYS = ('적용계약법', '업무구분', '계약방법', '낙찰방법', '입찰추정가격', '지역제한여부')
DEFAULT_OUTPUT = ROOT / 'runs/harness_improve_20260919/T3_natural_census'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def fingerprint(directory, prefix=None):
    directory = Path(directory)
    files = {(Path(prefix) / p.relative_to(directory)).as_posix() if prefix else p.relative_to(ROOT).as_posix(): sha(p)
             for p in sorted(Path(directory).rglob('*'))
             if p.is_file() and '__pycache__' not in p.parts
             and p.suffix in {'.py', '.json', '.csv', '.txt'}}
    return {'sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
            'files': files}


def dump(path, obj):
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def read_part(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        for line in f:
            yield json.loads(line)


def cell_origin(details, item, evidence):
    """Follow application order, including qualification's final overrides."""
    chain = []
    for detail in details:
        if detail.get('item') == item:
            chain.append(detail)
        facts = detail.get('facts')
        decisions = facts.get('decisions') if isinstance(facts, dict) else None
        decision = decisions.get(f'v{item}') if isinstance(decisions, dict) else None
        if decision is not None:
            chain.append({'item': item, 'source': detail['source'], **decision})
    if not chain or int(chain[-1]['value']) != 1 or chain[-1]['evidence'] != evidence:
        raise ValueError(f'Cannot attribute final v{item} to details')
    return {'source': chain[-1].get('source', 'unspecified'),
            'reason': chain[-1].get('reason'), 'decision_chain': chain}


def process_record(pipe, raw, index):
    from submission.prep import PROFILE_ITEMS, rules_only_row
    from submission.pps.input_contract import load_record_json
    result = {'index': index, 'record_id': None,
              'input_sha256': hashlib.sha256(raw.encode()).hexdigest(), 'firings': []}
    try:
        record = load_record_json(raw)
        result['record_id'] = record['id']
        for doc in record['docs']:
            doc['text'] = unicodedata.normalize('NFC', doc['text'])
        meta = {key: record['meta'].get(key) for key in META_KEYS}
        result['meta'] = meta
        for profile, items in PROFILE_ITEMS.items():
            row, details = rules_only_row(pipe, record, items)
            for item in items:
                if int(row[f'v{item}']) == 1:
                    evidence = row[f'e{item}']
                    result['firings'].append({'record_id': record['id'], 'item': item,
                        'profile': profile, 'evidence': evidence, 'meta': meta,
                        **cell_origin(details, item, evidence)})
        result['status'] = 'ok'
    except Exception as exc:
        result.update(status='error', error=f'{type(exc).__name__}: {exc}',
                      traceback=traceback.format_exc())
        # A record error must not masquerade as a complete 24-item result.
        result['partial_firings'] = result.pop('firings')
        result['firings'] = []
    return result


def run(args):
    output = args.output
    snapshot = output / 'source_snapshot'
    source_dir = snapshot / 'submission' if (snapshot / 'submission').is_dir() else ROOT / 'submission'
    if source_dir.parent == snapshot:
        sys.path.insert(0, str(snapshot.resolve()))
        if 'submission' in sys.modules and Path(sys.modules['submission'].__file__).resolve().parent != source_dir.resolve():
            raise RuntimeError('A different submission package was already imported; use a fresh process')
    from submission.b4_entry import B4Pipeline
    (output / 'parts').mkdir(parents=True, exist_ok=True)
    identity = {'schema_version': 1, 'input': str(args.input.resolve()),
                'input_sha256': sha(args.input), 'chunk_size': args.chunk_size,
                'source': fingerprint(source_dir, 'submission'),
                'catalog': fingerprint(args.data_dir), 'expected_records': args.expected_records}
    manifest_path = output / 'manifest.json'
    if manifest_path.exists():
        if json.loads(manifest_path.read_text(encoding='utf-8')) != identity:
            raise ValueError('Input, source, catalog or chunk configuration changed; use a fresh output')
    else:
        if list((output / 'parts').glob('part_*.jsonl.gz')):
            raise ValueError('Existing parts without manifest')
        dump(manifest_path, identity)
    pipe = B4Pipeline(str(args.data_dir), None)
    dump(output / 'runtime_receipt.json', {'submission_import_root': str(source_dir.resolve()),
         'source_sha256': identity['source']['sha256'], 'script_sha256': sha(Path(__file__)),
         'pid': os.getpid(), 'started_utc': datetime.now(timezone.utc).isoformat()})
    began, completed, new_parts = time.monotonic(), 0, 0
    with gzip.open(args.input, 'rt', encoding='utf-8') as f:
        lines = (line for line in f if line.strip())
        for part_no in itertools.count():
            batch = list(itertools.islice(lines, args.chunk_size))
            if not batch:
                break
            target = output / 'parts' / f'part_{part_no:04d}.jsonl.gz'
            if target.exists():
                saved = list(read_part(target))
                if len(saved) != len(batch) or any(
                    row['index'] != completed + j or row['input_sha256'] != hashlib.sha256(raw.encode()).hexdigest()
                    for j, (row, raw) in enumerate(zip(saved, batch))):
                    raise ValueError(f'Invalid checkpoint: {target}')
                print(f'skip {target.name}: {len(batch)} records', flush=True)
            else:
                tmp = target.with_name(target.name + '.tmp')
                with gzip.open(tmp, 'wt', encoding='utf-8') as dst:
                    for j, raw in enumerate(batch):
                        result = process_record(pipe, raw, completed + j)
                        dst.write(json.dumps(result, ensure_ascii=False) + '\n')
                        if (j + 1) % 50 == 0:
                            print(f'processed {completed+j+1} elapsed={time.monotonic()-began:.1f}s', flush=True)
                if fingerprint(source_dir, 'submission') != identity['source']:
                    raise ValueError('Bound source changed during chunk; uncommitted part retained as .tmp')
                os.replace(tmp, target)
                new_parts += 1
            completed += len(batch)
            dump(output / 'progress.json', {'records': completed, 'last_part': part_no,
                 'updated_utc': datetime.now(timezone.utc).isoformat(),
                 'invocation_seconds': time.monotonic() - began})
            if args.max_new_parts and new_parts >= args.max_new_parts:
                print('Stopped at requested checkpoint boundary', flush=True)
                return
    if completed != args.expected_records:
        raise ValueError(f'Expected {args.expected_records} records, observed {completed}')
    dump(output / 'completed.json', {'records': completed, 'parts': part_no,
         'input_sha256': identity['input_sha256'], 'source_sha256': identity['source']['sha256']})
    print(f'complete: {completed} records, {part_no} parts', flush=True)


def read_retries(output, manifest):
    retries = {}
    for path in sorted((output / 'retries').glob('retry_*.jsonl.gz')):
        for row in read_part(path):
            if row.get('retry_source_sha256') != manifest['source']['sha256']:
                raise ValueError(f'Retry source mismatch: {path}')
            retries[row['index']] = row
    return retries


def retry_errors(args):
    """Retry only failed records; original parts and attempts remain immutable."""
    output = args.output
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    snapshot = output / 'source_snapshot'
    source_dir = snapshot / 'submission' if (snapshot / 'submission').is_dir() else ROOT / 'submission'
    if (sha(args.input) != manifest['input_sha256']
            or fingerprint(source_dir, 'submission') != manifest['source']
            or fingerprint(args.data_dir) != manifest['catalog']):
        raise ValueError('Retry input, source or catalog mismatch')
    retries = read_retries(output, manifest)
    failed = {}
    for path in sorted((output / 'parts').glob('part_*.jsonl.gz')):
        for row in read_part(path):
            if row['status'] == 'error':
                retry = retries.get(row['index'])
                if retry is not None and (retry['input_sha256'], retry['record_id']) != (row['input_sha256'], row['record_id']):
                    raise ValueError('Retry record identity mismatch')
                if retry is None or retry['status'] == 'error':
                    failed[row['index']] = row
    if not failed:
        print('No unresolved error records to retry')
        return
    if source_dir.parent == snapshot:
        sys.path.insert(0, str(snapshot.resolve()))
        if 'submission' in sys.modules and Path(sys.modules['submission'].__file__).resolve().parent != source_dir.resolve():
            raise RuntimeError('Retry requires a fresh process for the bound snapshot')
    from submission.b4_entry import B4Pipeline
    pipe = B4Pipeline(str(args.data_dir), None)
    retry_dir = output / 'retries'
    retry_dir.mkdir(exist_ok=True)
    target = retry_dir / f'retry_{len(list(retry_dir.glob("retry_*.jsonl.gz"))):04d}.jsonl.gz'
    if target.exists():
        raise ValueError('Retry sequence has a gap; refusing to overwrite an attempt')
    tmp = target.with_name(target.name + '.tmp')
    count = 0
    with gzip.open(args.input, 'rt', encoding='utf-8') as src, gzip.open(tmp, 'wt', encoding='utf-8') as dst:
        for index, raw in enumerate(line for line in src if line.strip()):
            if index not in failed:
                continue
            result = process_record(pipe, raw, index)
            original = failed[index]
            if (result['input_sha256'], result['record_id']) != (original['input_sha256'], original['record_id']):
                raise ValueError('Retry record differs from original input')
            result.update(retry_source_sha256=manifest['source']['sha256'],
                          retry_script_sha256=sha(Path(__file__)), previous_error=original['error'])
            dst.write(json.dumps(result, ensure_ascii=False) + '\n')
            count += 1
            print(f"retry {result['record_id']}: {result['status']}", flush=True)
            if count == len(failed):
                break
    if count != len(failed) or fingerprint(source_dir, 'submission') != manifest['source']:
        raise ValueError('Incomplete retry or changed source; attempt remains temporary')
    os.replace(tmp, target)
    print(f'Retried {count} failed records; original parts unchanged')


def summarize(args):
    output = args.output
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    retries = read_retries(output, manifest)
    initial_errors, used_retries = [], set()
    counts, clusters, errors, ids = Counter(), {}, [], set()
    total = fired_records = 0
    sample_records = sample_fired_records = sample_errors = 0
    sample_counts = Counter()
    paths = sorted((output / 'parts').glob('part_*.jsonl.gz'))
    tmp = output / 'firings.jsonl.gz.tmp'
    with gzip.open(tmp, 'wt', encoding='utf-8') as dst:
        for number, path in enumerate(paths):
            if path.name != f'part_{number:04d}.jsonl.gz':
                raise ValueError('Non-contiguous part sequence')
            part_rows = 0
            for row in read_part(path):
                if row['status'] == 'error':
                    initial_errors.append({k: row[k] for k in ('index', 'record_id', 'error')})
                retry = retries.get(row['index'])
                if retry is not None:
                    if row['status'] != 'error' or (retry['input_sha256'], retry['record_id']) != (row['input_sha256'], row['record_id']):
                        raise ValueError('Retry does not match an original error record')
                    used_retries.add(row['index'])
                    row = retry
                if row['index'] != total or row['record_id'] in ids:
                    raise ValueError('Duplicate ID or non-contiguous record sequence')
                ids.add(row['record_id'])
                total += 1
                part_rows += 1
                if row['status'] == 'error':
                    errors.append({k: row[k] for k in ('index', 'record_id', 'error')})
                if row['index'] % 50 == 0:
                    sample_records += 1
                    sample_fired_records += bool(row['firings'])
                    sample_errors += row['status'] == 'error'
                    sample_counts.update(cell['item'] for cell in row['firings'])
                fired_records += bool(row['firings'])
                for cell in row['firings']:
                    counts[cell['item']] += 1
                    dst.write(json.dumps(cell, ensure_ascii=False) + '\n')
                    key = (cell['item'], cell['source'], cell['reason'])
                    cluster = clusters.setdefault(key, {'item': key[0], 'source': key[1],
                        'reason': key[2], 'count': 0, 'examples': []})
                    cluster['count'] += 1
                    if len(cluster['examples']) < 3:
                        cluster['examples'].append({'record_id': cell['record_id'],
                            'evidence': cell['evidence'][:200], 'meta': cell['meta']})
            if part_rows != min(manifest['chunk_size'], manifest['expected_records'] - number * manifest['chunk_size']):
                raise ValueError(f'Wrong part length: {path}')
    if total != manifest['expected_records'] and not args.allow_partial:
        raise ValueError(f'Incomplete census: {total}/{manifest["expected_records"]}')
    if used_retries != set(retries):
        raise ValueError('Orphan retry records')
    os.replace(tmp, output / 'firings.jsonl.gz')
    summary = {'records': total, 'successful_records': total - len(errors), 'errors': errors,
        'initial_errors': initial_errors, 'retried_records': len(used_retries),
        'error_records': len(errors), 'fired_records': fired_records,
        'fired_record_rate': fired_records / total if total else 0,
        'fired_cells': sum(counts.values()), 'cells_per_record': sum(counts.values()) / total if total else 0,
        'parts': len(paths), 'complete': total == manifest['expected_records'],
        'every_50_reference_check': {'index_rule': 'zero-based index % 50 == 0',
            'records': sample_records, 'error_records': sample_errors,
            'fired_records': sample_fired_records, 'fired_cells': sum(sample_counts.values()),
            'items': {str(k): sample_counts[k] for k in range(1, 25)}},
        'rate_denominator': 'all processed records; errors listed separately',
        'items': {str(k): {'count': counts[k], 'rate': counts[k] / total if total else 0} for k in range(1,25)},
        'clusters': sorted(clusters.values(), key=lambda c: (c['item'], -c['count'], c['source'], str(c['reason'])))}
    dump(output / 'summary.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in {'clusters', 'items', 'errors'}}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run_parser = sub.add_parser('run')
    run_parser.add_argument('--input', type=Path, default=ROOT / 'data_open/train_unlabeled.jsonl.gz')
    run_parser.add_argument('--data-dir', type=Path, default=ROOT / 'data_open/data')
    run_parser.add_argument('--chunk-size', type=int, default=500)
    run_parser.add_argument('--expected-records', type=int, default=20000)
    run_parser.add_argument('--max-new-parts', type=int)
    summary_parser = sub.add_parser('summarize')
    summary_parser.add_argument('--allow-partial', action='store_true')
    retry_parser = sub.add_parser('retry-errors')
    retry_parser.add_argument('--input', type=Path, default=ROOT / 'data_open/train_unlabeled.jsonl.gz')
    retry_parser.add_argument('--data-dir', type=Path, default=ROOT / 'data_open/data')
    for child in (run_parser, summary_parser, retry_parser):
        child.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.command == 'run':
        if args.chunk_size < 1 or args.expected_records < 1 or (args.max_new_parts is not None and args.max_new_parts < 1):
            parser.error('Counts must be positive')
        run(args)
    elif args.command == 'retry-errors':
        retry_errors(args)
    else:
        summarize(args)


if __name__ == '__main__':
    main()
