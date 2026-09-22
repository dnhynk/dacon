"""Saved-response joins, production assembly parity, and restart integrity (CPU only)."""
from collections import Counter
import csv
import gzip
import json
import random
from types import SimpleNamespace

import pytest

from submission import b4_entry, prep
from submission.stream import RecordState, StreamingExecutor
from tools import cell_reconsume as replay


def write_jsonl(path, rows):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'wt', encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')


def test_assembly_matches_production_for_partial_and_overlapping_profiles():
    rng = random.Random(20260919)
    for _ in range(100):
        state = RecordState(0, {'id': 'synthetic'})
        for profile, items in {**replay.PROFILE_ITEMS, 'Q10': (10, 13, 17),
                               'S9': (9,), 'L19': (19, 20, 21), 'W20': (19, 20)}.items():
            row = {f'{field}{k}': rng.randrange(2) if field == 'v' else profile
                   for k in items for field in ('v', 'e')}
            if rng.randrange(2):
                state.rows[profile] = row if rng.randrange(3) else None
            if profile in replay.PROFILE_ITEMS and rng.randrange(2):
                state.fallback_rows[profile] = (row, [])
        actual, _ = replay.assemble('synthetic', state.rows,
                                   {key: value[0] for key, value in state.fallback_rows.items()})
        expected = StreamingExecutor._assemble(SimpleNamespace(counts=Counter()), state)
        assert actual == expected


def test_failed_consumption_uses_primary_rules_and_reports_errors_and_owners():
    saved = {'row': replay.assemble('one', {}, {})[0]}

    def consume(pipe, record, packet, response):
        return {'row': None, 'parse_error': 'bad JSON' if packet['batch'] == 'A1' else None,
                'cpu_error': 'CPU: defect' if packet['batch'] == 'Q10' else None}

    def rules(pipe, record, items):
        return ({f'v{k}': int(k == 1) for k in items}, [])

    result = replay.replay_record(None, {'id': 'one'}, saved,
                                  [({'batch': 'A1'}, {}), ({'batch': 'Q10'}, {})], consume, rules)
    assert result['counts'] == {'reconsumed_responses': 2, 'parse_errors': 1, 'cpu_errors': 1,
                                'rules_only_profiles': 3, 'records': 1, 'different_cells': 1}
    assert result['differences'] == [{'id': 'one', 'cell': 'v1', 'saved': 0,
                                      'reconsumed': 1, 'profile': 'A1'}]


@pytest.fixture
def tiny_run(tmp_path, monkeypatch):
    run_dir = tmp_path / 'saved'
    run_dir.mkdir()
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    (data_dir / 'knowledge.txt').write_text('fixed', encoding='utf-8')
    input_path = tmp_path / 'input.jsonl.gz'
    records = [{'id': name, 'meta': {}, 'docs': [{'type': '공고문', 'doc_id': name, 'text': '공고문'}],
                'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}
               for name in ('one', 'two', 'three')]
    write_jsonl(input_path, records)
    packets, consumed, saved = [], [], []
    for record in records:
        packet = {'record_id': record['id'], 'request_key': 'A:' + record['id'] + ':1', 'batch': 'A1'}
        packets.append(packet)
        consumed.extend([
            {**packet, 'row': None, 'error': 'old failed attempt', 'response': {'text': 'invalid'}},
            {**packet, 'row': {'v1': 0}, 'response': {'text': 'use current consumer'}}])
        # Stored consumed row deliberately differs: copying it would fail the test.
        saved.append({'id': record['id'], 'row': replay.assemble(record['id'], {'A1': {'v1': 1}}, {})[0]})
    write_jsonl(run_dir / 'stream_packets_00000.jsonl.gz', reversed(packets))
    write_jsonl(run_dir / 'stream_consumed_00000.jsonl.gz', consumed)
    write_jsonl(run_dir / 'stream_records_00000.jsonl.gz', reversed(saved))
    key = tmp_path / 'key.json'
    key.write_text(json.dumps({'one': {'targets': [{'item': 'v24'}]},
                               'two': {'targets': [{'item': 'v10'}]},
                               'three': {'targets': [{'item': 'v10'}, {'item': 'v24'}]}}), encoding='utf-8')
    monkeypatch.setattr(b4_entry, 'B4Pipeline', lambda *args: object())
    calls = []

    def consume(pipe, record, packet, response):
        assert record['id'] == packet['record_id']
        assert response['text'] == 'use current consumer'
        calls.append(record['id'])
        return {'row': {'v1': 1}, 'parse_error': None, 'cpu_error': None}

    monkeypatch.setattr(prep, 'consume_response', consume)
    monkeypatch.setattr(prep, 'rules_only_row', lambda pipe, record, items: ({f'v{k}': 0 for k in items}, []))
    args = SimpleNamespace(run_dir=run_dir, input=input_path, data_dir=data_dir,
                           output_dir=tmp_path / 'output', key=key, target_items=None,
                           ids_file=None, limit=None, chunk_size=1)
    return args, calls


def test_join_reconsumes_and_resumes_without_repeating_completed_parts(tiny_run, monkeypatch):
    args, calls = tiny_run
    first = replay.run(args)
    assert first['records'] == 3 and first['different_cells'] == 0
    assert first['rules_only_profiles'] == 6 and first['reconsumed_responses'] == 3
    assert first['saved_run_counts']['saved_failed_responses'] == 3
    path = args.output_dir / 'submission.csv'
    original = path.read_bytes()
    assert not original.startswith(b'\xef\xbb\xbf')
    with path.open(encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == replay.COLUMNS and len(reader.fieldnames) == 49
        assert [row['id'] for row in reader] == ['one', 'two', 'three']
    # Simulate a stop after two chunks: only the missing third is consumed again.
    (args.output_dir / 'parts/00002.json').unlink()
    second = replay.run(args)
    assert second['resumed_records'] == 2 and calls == ['one', 'two', 'three', 'three']
    assert path.read_bytes() == original
    # A completed resume does not even construct the pipeline.
    monkeypatch.setattr(b4_entry, 'B4Pipeline', lambda *a: pytest.fail('unnecessary pipeline load'))
    assert replay.run(args)['resumed_records'] == 3
    (args.data_dir / 'knowledge.txt').write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='resume inputs/code/options changed'):
        replay.run(args)


def test_filters_intersect_and_limit_preserves_input_order(tiny_run):
    args, _ = tiny_run
    args.target_items = 'v24'
    assert replay.select_ids(args) == ['one', 'three']
    args.ids_file = args.output_dir.parent / 'ids.txt'
    args.ids_file.write_text('three\ntwo\n', encoding='utf-8')
    args.limit = 1
    assert replay.select_ids(args) == ['three']
    report = replay.run(args)
    assert report['records'] == 1 and report['different_cells'] == 0
    args.key = None
    with pytest.raises(ValueError, match='requires --key'):
        replay.select_ids(args)


def test_missing_packet_fails_instead_of_silently_using_rules(tiny_run):
    args, _ = tiny_run
    write_jsonl(args.run_dir / 'stream_packets_00000.jsonl.gz', [])
    with pytest.raises(ValueError, match='missing saved packets'):
        replay.run(args)


def test_preserved_run_is_read_only(tiny_run):
    args, _ = tiny_run
    args.output_dir = args.run_dir / 'new'
    with pytest.raises(ValueError, match='preserved run'):
        replay.run(args)
    assert not args.output_dir.exists()
