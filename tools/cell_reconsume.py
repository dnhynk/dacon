"""Replay saved stream responses through the current CPU consumer, without inference.

The SQLite join index and atomic JSON parts keep memory bounded and permit restart.
Use a new output directory after changing code, data, inputs, or selection options.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

COLUMNS = ['id', *(f'v{k}' for k in range(1, 25)), *(f'e{k}' for k in range(1, 25))]
PROFILE_ITEMS = {'A1': tuple(range(1, 10)), 'A10': tuple(range(10, 19)), 'A19': tuple(range(19, 25))}
RULE_PROFILE = {k: profile for profile, items in PROFILE_ITEMS.items() for k in items}


def json_rows(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf-8')
    temporary.replace(path)


def peak_memory_bytes():
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                        *[(name, ctypes.c_size_t) for name in
                          ('PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                           'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                           'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        query = ctypes.windll.psapi.GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not query(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError()
        return counters.PeakWorkingSetSize
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)


def assemble(record_id, rows, fallback_rows, record=None, corpus_index=None, source_rule_items=(), precision_gates=(),
             corpus_filter_items=()):
    """Mirror StreamingExecutor._assemble; also record the last writer of each cell."""
    result = {'id': record_id, **{f'v{k}': None for k in range(1, 25)},
              **{f'e{k}': '' for k in range(1, 25)}}
    owners = {}

    def update(profile, row):
        result.update(row)
        owners.update({key: profile for key in row if key != 'id'})

    for profile in PROFILE_ITEMS:
        row = rows.get(profile)
        if row is None:
            row = fallback_rows.get(profile)
        if row is not None:
            update(profile, row)
    for profile in ('Q10', 'S9'):
        if rows.get(profile):
            update(profile, rows[profile])
    for profile in ('L19', 'W20'):
        if rows.get(profile):
            update(profile, {key: rows[profile][key] for key in ('v20', 'e20') if key in rows[profile]})
    for k in source_rule_items:
        source = fallback_rows.get(RULE_PROFILE[k])
        if source is not None:
            update('source_rules', {f'v{k}': source[f'v{k}'], f'e{k}': source[f'e{k}']})
    for k in range(1, 25):
        if result[f'v{k}'] is None:
            update('filled_zero', {f'v{k}': 0, f'e{k}': ''})
    if corpus_index is not None:
        from submission.pps import corpus_lines
        for k in corpus_lines.apply(record, result, corpus_index, corpus_filter_items):
            owners.update({f'v{k}': 'corpus_seen_filter', f'e{k}': 'corpus_seen_filter'})
    if precision_gates:
        from submission.pps.precision_gates import apply as apply_gates
        for k, gate in apply_gates(record, result, precision_gates):
            owners.update({f'v{k}': 'precision_gate:' + gate, f'e{k}': 'precision_gate:' + gate})
    return result, owners


def select_ids(args):
    allowed = None
    if args.ids_file:
        allowed = set(args.ids_file.read_text(encoding='utf-8-sig').splitlines()) - {''}
    if args.target_items:
        if not args.key:
            raise ValueError('--target-items requires --key')
        items = set(args.target_items.split(','))
        if not items or not items <= {f'v{k}' for k in range(1, 25)}:
            raise ValueError('--target-items must be comma-separated v1..v24')
        key = json.loads(args.key.read_text(encoding='utf-8'))
        targets = {record_id for record_id, entry in key.items()
                   if items.intersection(target['item'] for target in entry['targets'])}
        allowed = targets if allowed is None else allowed & targets
    ids, seen = [], set()
    for record in json_rows(args.input):
        record_id = record['id']
        if record_id in seen:
            raise ValueError(f'duplicate input id: {record_id}')
        seen.add(record_id)
        if allowed is None or record_id in allowed:
            ids.append(record_id)
    if args.ids_file:
        requested = set(args.ids_file.read_text(encoding='utf-8-sig').splitlines()) - {''}
        if requested - seen:
            raise ValueError(f'ids-file contains unknown ids: {sorted(requested - seen)[:3]}')
    if args.limit:
        ids = ids[:args.limit]
    if not ids:
        raise ValueError('selection contains no records')
    return ids


def manifest(args, ids):
    paths = [args.input, Path(__file__), *sorted((ROOT / 'submission').rglob('*.py')),
             *sorted((ROOT / 'submission').rglob('*.json')), *sorted((ROOT / 'submission').rglob('*.u64')),
             *sorted(path for path in args.data_dir.rglob('*') if path.is_file())]
    for prefix in ('stream_packets', 'stream_consumed', 'stream_records'):
        files = sorted(args.run_dir.glob(prefix + '_*.jsonl.gz'))
        if not files:
            raise ValueError(f'no {prefix} files in {args.run_dir}')
        paths.extend(files)
    paths.extend(path for path in (args.key, args.ids_file) if path)
    result = {'version': 1, 'ids': ids, 'chunk_size': args.chunk_size,
              'files': {str(path.resolve()): sha256(path) for path in paths}}
    if getattr(args, 'drop_profiles', ''):
        result['drop_profiles'] = args.drop_profiles
    return result


def build_index(path, args, ids):
    """Only selected records and successful response packets are retained on disk."""
    temporary = path.with_suffix('.tmp')
    if temporary.exists():
        temporary.unlink()
    selected = set(ids)
    counts = Counter()
    with closing(sqlite3.connect(temporary)) as db:
        db.executescript('''
            CREATE TABLE records (id TEXT PRIMARY KEY, input TEXT, saved TEXT);
            CREATE TABLE responses (request_key TEXT PRIMARY KEY, record_id TEXT, payload TEXT);
            CREATE TABLE packets (request_key TEXT PRIMARY KEY, payload TEXT);
            CREATE TABLE metadata (payload TEXT);
            CREATE INDEX response_record ON responses(record_id);
        ''')
        # Match production input normalization and contract validation.
        from submission.pps.data import records
        for record in records(args.input):
            if record['id'] in selected:
                db.execute('INSERT INTO records VALUES (?, ?, NULL)',
                           (record['id'], json.dumps(record, ensure_ascii=False)))
        for file in sorted(args.run_dir.glob('stream_records_*.jsonl.gz')):
            for saved in json_rows(file):
                counts['saved_record_rows'] += 1
                if saved['id'] in selected:
                    current = db.execute('SELECT saved FROM records WHERE id=?', (saved['id'],)).fetchone()
                    if current[0] is not None:
                        raise ValueError(f'duplicate saved record: {saved["id"]}')
                    db.execute('UPDATE records SET saved=? WHERE id=?',
                               (json.dumps(saved, ensure_ascii=False), saved['id']))
        for file in sorted(args.run_dir.glob('stream_consumed_*.jsonl.gz')):
            for consumed in json_rows(file):
                counts['saved_consumed_rows'] += 1
                if consumed['record_id'] not in selected:
                    continue
                if consumed.get('error'):
                    counts['saved_failed_responses'] += 1
                    continue
                if consumed.get('row') is None:
                    # A consumer that withheld its overlay (e.g. a Q10 gate) still
                    # carries a valid response; current code must see it again.
                    counts['saved_withheld_responses'] += 1
                if consumed.get('response') is None:
                    counts['saved_without_response'] += 1
                    continue
                db.execute('INSERT INTO responses VALUES (?, ?, ?)',
                           (consumed['request_key'], consumed['record_id'], json.dumps(consumed, ensure_ascii=False)))
        needed = {row[0] for row in db.execute('SELECT request_key FROM responses')}
        for file in sorted(args.run_dir.glob('stream_packets_*.jsonl.gz')):
            for packet in json_rows(file):
                counts['saved_packet_rows'] += 1
                if packet['request_key'] in needed:
                    db.execute('INSERT INTO packets VALUES (?, ?)',
                               (packet['request_key'], json.dumps(packet, ensure_ascii=False)))
        missing = db.execute('SELECT request_key FROM responses LEFT JOIN packets USING(request_key) '
                             'WHERE packets.payload IS NULL').fetchall()
        if missing:
            raise ValueError(f'missing saved packets: {missing[:3]}')
        if db.execute('SELECT count(*) FROM records WHERE saved IS NULL').fetchone()[0]:
            raise ValueError('selected input lacks a saved final row')
        db.execute('INSERT INTO metadata VALUES (?)', (json.dumps(dict(counts)),))
        db.commit()
    temporary.replace(path)


def replay_record(pipe, record, saved, responses, consume, rules, corpus_index=None, drop=()):
    rows, fallback, errors = {}, {}, []
    counts = Counter()
    for packet, response in responses:
        profile = packet['batch']
        if profile in drop:          # tier simulation: the executor never sent this packet
            continue
        if profile.startswith('A10_t'):     # a kept A10 thinking-budget profile answers the A10 group
            profile = 'A10'
        if profile in rows:
            raise ValueError(f'duplicate successful profile: {record["id"]} {profile}')
        result = consume(pipe, record, packet, response)
        counts['reconsumed_responses'] += 1
        for kind in ('parse_error', 'cpu_error'):
            if result.get(kind):
                counts[kind + 's'] += 1
                errors.append({'id': record['id'], 'profile': profile, 'kind': kind, 'error': result[kind]})
        rows[profile] = result['row']
    source_items = tuple(getattr(getattr(pipe, 'config', None), 'source_rule_items', ()) or ())
    gates = tuple(getattr(getattr(pipe, 'config', None), 'precision_gates', ()) or ())
    filter_items = tuple(getattr(getattr(pipe, 'config', None), 'corpus_seen_filter_items', ()) or ())
    for profile, items in PROFILE_ITEMS.items():
        if rows.get(profile) is None or set(items) & set(source_items):
            fallback[profile], _ = rules(pipe, record, items)
            counts['rules_only_profiles'] += rows.get(profile) is None
    row, owners = assemble(record['id'], rows, fallback, record, corpus_index, source_items, gates, filter_items)
    differences = [{'id': record['id'], 'cell': cell, 'saved': saved['row'][cell],
                    'reconsumed': row[cell], 'profile': owners.get(cell)}
                   for cell in COLUMNS[1:] if row[cell] != saved['row'][cell]]
    counts['records'] += 1
    counts['different_cells'] += len(differences)
    return {'row': row, 'counts': dict(counts), 'differences': differences, 'errors': errors}


def run(args):
    began = time.perf_counter()
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    for field in ('chunk_size', 'limit'):
        if getattr(args, field) is not None and getattr(args, field) < 1:
            raise ValueError(f'--{field.replace("_", "-")} must be positive')
    output = args.output_dir.resolve()
    run_dir = args.run_dir.resolve()
    if output == run_dir or run_dir in output.parents:
        raise ValueError('output must not be inside the preserved run')
    ids = select_ids(args)
    provenance = manifest(args, ids)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / 'manifest.json'
    if manifest_path.exists():
        if json.loads(manifest_path.read_text(encoding='utf-8')) != provenance:
            raise ValueError('resume inputs/code/options changed; use a fresh --output-dir')
    else:
        if list(output.iterdir()):
            raise ValueError('output directory is nonempty without a replay manifest')
        atomic_json(manifest_path, provenance)
    signature = sha256(manifest_path)
    parts = output / 'parts'
    parts.mkdir(exist_ok=True)
    index_path = output / 'join.sqlite3'
    if not index_path.exists():
        build_index(index_path, args, ids)
    from submission.b4_entry import B4Pipeline
    from submission.pps import corpus_lines
    from submission.prep import consume_response, rules_only_row
    pipe = corpus_index = None
    resumed = 0
    counts = Counter({key: 0 for key in ('records', 'reconsumed_responses', 'rules_only_profiles',
                                        'parse_errors', 'cpu_errors', 'different_cells')})
    differences, errors, part_timings = [], [], []
    csv_path = output / 'submission.csv'
    csv_tmp = output / 'submission.csv.tmp'
    with closing(sqlite3.connect(index_path)) as db, csv_tmp.open('w', encoding='utf-8', newline='') as handle:
        index_counts = json.loads(db.execute('SELECT payload FROM metadata').fetchone()[0])
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for start in range(0, len(ids), args.chunk_size):
            chunk_ids = ids[start:start + args.chunk_size]
            part_path = parts / f'{start // args.chunk_size:05d}.json'
            if part_path.exists():
                part = json.loads(part_path.read_text(encoding='utf-8'))
                if part['manifest_sha256'] != signature or [result['row']['id'] for result in part['results']] != chunk_ids:
                    raise ValueError(f'invalid resume part: {part_path}')
                resumed += len(chunk_ids)
            else:
                chunk_began = time.perf_counter()
                if pipe is None:
                    pipe = B4Pipeline(str(args.data_dir), None)
                    corpus_index = corpus_lines.load_for(pipe)
                results = []
                for record_id in chunk_ids:
                    record, saved = map(json.loads, db.execute('SELECT input, saved FROM records WHERE id=?', (record_id,)).fetchone())
                    responses = ((json.loads(packet), json.loads(consumed)['response'])
                                 for packet, consumed in db.execute(
                                     'SELECT packets.payload, responses.payload FROM responses JOIN packets USING(request_key) '
                                     'WHERE record_id=? ORDER BY request_key', (record_id,)))
                    results.append(replay_record(pipe, record, saved, responses, consume_response, rules_only_row,
                                                 corpus_index, tuple(p for p in getattr(args, 'drop_profiles', '').split(',') if p)))
                part = {'manifest_sha256': signature, 'seconds': time.perf_counter() - chunk_began,
                        'peak_memory_bytes': peak_memory_bytes(), 'results': results}
                atomic_json(part_path, part)
                print(json.dumps({'completed': start + len(chunk_ids), 'total': len(ids),
                                  'part_seconds': round(part['seconds'], 3)}), flush=True)
            part_timings.append(part['seconds'])
            for result in part['results']:
                writer.writerow(result['row'])
                counts.update(result['counts'])
                differences.extend(result['differences'])
                errors.extend(result['errors'])
    csv_tmp.replace(csv_path)
    report = {**dict(counts), 'model_calls': 0, 'measurement': 'saved_response_current_cpu_replay',
              'resumed_records': resumed, 'elapsed_seconds': time.perf_counter() - began,
              'peak_memory_bytes': peak_memory_bytes(), 'part_seconds': part_timings,
              'saved_run_counts': index_counts, 'differences': differences, 'errors': errors,
              'submission_sha256': sha256(csv_path), 'manifest_sha256': signature}
    atomic_json(output / 'reconsume_report.json', report)
    print(json.dumps({key: value for key, value in report.items() if key not in ('differences', 'errors', 'part_seconds')}, ensure_ascii=False))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('run-dir', 'input', 'data-dir', 'output-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--key', type=Path)
    parser.add_argument('--target-items')
    parser.add_argument('--ids-file', type=Path, help='UTF-8 text, one exact record ID per line')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--chunk-size', type=int, default=200)
    parser.add_argument('--drop-profiles', default='',
                        help='comma-separated packet profiles (A10, A19, Q10, L19, S9, W20, A10_t<budget>) whose saved '
                             'responses are ignored, as if the executor had not sent them; dropped A groups fall back to '
                             'source rules; at most one A10 budget profile may remain')
    args = parser.parse_args(argv)
    try:
        run(args)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f'cell_reconsume: {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
