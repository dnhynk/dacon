"""E2: the default-off corpus_seen_filter switch, its line index and the executor/replay wiring (CPU only)."""
import dataclasses
import gzip
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from submission.pps import corpus_lines
from submission.pps.data import ABSENCE
from submission.pps.prompts import Config
from submission.stream import RecordState, StreamOptions, StreamingExecutor, execute_stream
from tests.test_stream_executor import CONFIG, Clock, ClockedPool, FakeRunner, SyntheticPipeline, answer, csv_rows, read_rows
from tools import cell_reconsume
from tools.build_corpus_line_index import build

STOCK = '본점 소재지가 서울특별시 관내에 있는 업체로 제한합니다.'
STOCK_NEXT = '최근 3년 이내 단일 건 1억원 이상의 유사용역 이행실적이 있는 업체'
EDITED = '본점 소재지가 [수요기관(초등학교)] 관할 자치구 안에 있는 업체만 참가할 수 있습니다.'
SHORT = '가. 참가자격'
EXCLUDED = (5, 10, 11, 13, 14, 15, 16, 18, 20, 24)


def index_of(*lines):
    return np.array(sorted({int.from_bytes(corpus_lines.line_digest(corpus_lines.normalize(x)), 'big')
                            for x in lines}), dtype='<u8')


def notice(*texts, rid='e2-case'):
    return {'id': rid, 'meta': {'업무구분': '일반용역'},
            'docs': [{'type': '공고문' if i == 0 else '제안요청서', 'doc_id': f'{rid}-{i}', 'text': text}
                     for i, text in enumerate(texts)],
            'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


def row_with(**cells):
    row = {'id': 'e2-case', **{f'v{k}': 0 for k in range(1, 25)}, **{f'e{k}': '' for k in range(1, 25)}}
    for name, quote in cells.items():
        row['v' + name[1:]], row['e' + name[1:]] = 1, quote
    return row


def write_corpus(path, texts):
    with gzip.open(path, 'wt', encoding='utf-8') as handle:
        for n, text in enumerate(texts):
            handle.write(json.dumps({'id': f'PPS-D-{n:06d}', 'docs': [{'text': text}]}, ensure_ascii=False) + '\n')
    return path


def test_a_quote_whose_lines_are_all_in_the_corpus_is_cleared_with_its_evidence():
    rec = notice(f'{SHORT}\n{STOCK}\n{STOCK_NEXT}\n{EDITED}')
    row = row_with(v6=STOCK, v2='서울특별시 관내에 있는 업체로 제한합니다.\n최근 3년 이내 단일 건')
    assert corpus_lines.apply(rec, row, index_of(STOCK, STOCK_NEXT)) == [2, 6]
    assert (row['v6'], row['e6'], row['v2'], row['e2']) == (0, '', 0, '')
    # Whitespace and compatibility forms do not matter; the line is NFKC without spaces.
    spaced = notice('본점  소재지가 서울특별시　관내에 있는 업체로 제한합니다．')
    assert corpus_lines.apply(spaced, row_with(v6='서울특별시　관내'), index_of(STOCK)) == [6]


def test_one_unseen_line_keeps_the_cell():
    rec = notice(f'{STOCK}\n{EDITED}\n{STOCK_NEXT}')
    quote = f'제한합니다.\n{EDITED}\n최근 3년'
    row = row_with(v6=quote, v7=EDITED)
    assert corpus_lines.apply(rec, row, index_of(STOCK, STOCK_NEXT)) == []
    assert (row['v6'], row['e6'], row['v7'], row['e7']) == (1, quote, 1, EDITED)
    # Digits are not normalized: the same clause with another amount is a new line.
    other_amount = notice(STOCK_NEXT.replace('1억원', '2억원'))
    assert corpus_lines.apply(other_amount, row_with(v2='유사용역 이행실적'), index_of(STOCK_NEXT)) == []


def test_a_quote_on_short_lines_only_or_without_a_source_keeps_the_cell():
    rec = notice(f'{SHORT}\n{STOCK}')
    short_only = row_with(v1=SHORT)
    assert corpus_lines.apply(rec, short_only, index_of(SHORT, STOCK)) == [] and short_only['v1'] == 1
    unlocated = row_with(v1='공고 어디에도 없는 문장입니다 가나다라마바사')
    assert corpus_lines.apply(rec, unlocated, index_of(SHORT, STOCK)) == [] and unlocated['v1'] == 1
    # A short line beside a seen long line is ignored, not counted as unseen.
    both = row_with(v1=f'{SHORT}\n{STOCK}')
    assert corpus_lines.apply(rec, both, index_of(STOCK)) == [1]
    assert corpus_lines.apply(rec, row_with(v1=STOCK), np.array([], dtype='<u8')) == []


def test_the_first_document_that_contains_the_quote_decides():
    unseen = '참가자격: 서울특별시 관내에 있는 업체로서 [수요기관(초등학교)]이 정한 요건을 갖춘 자'
    quote = '서울특별시 관내에 있는 업체로'
    assert corpus_lines.apply(notice(unseen, STOCK), row_with(v6=quote), index_of(STOCK)) == []
    assert corpus_lines.apply(notice(STOCK, unseen), row_with(v6=quote), index_of(STOCK)) == [6]


def test_absence_v24_and_price_shift_items_are_never_touched():
    rec = notice(STOCK)
    row = row_with(**{f'v{k}': STOCK for k in range(1, 25)})
    before = dict(row)
    dropped = corpus_lines.apply(rec, row, index_of(STOCK))
    assert dropped == [k for k in range(1, 25) if k not in EXCLUDED]
    assert all(row[f'{f}{k}'] == before[f'{f}{k}'] for k in EXCLUDED for f in 've')
    negatives = row_with()
    negatives['e3'] = STOCK          # a quote without a positive is not a target
    assert corpus_lines.apply(rec, negatives, index_of(STOCK)) == [] and negatives['e3'] == STOCK


def test_the_switch_is_off_in_code_and_on_in_the_shipped_config():
    shipped = Config.load(Path(__file__).resolve().parents[1] / 'submission/model/config.json')
    assert Config().corpus_seen_filter is False and shipped.corpus_seen_filter is True
    assert corpus_lines.load_for(SimpleNamespace(config=Config())) is None
    assert len(corpus_lines.load_for(SimpleNamespace(config=shipped))) > 0
    with pytest.raises(ValueError):
        dataclasses.replace(Config(), corpus_seen_filter='yes')


class QuotingPipeline(SyntheticPipeline):
    """Every model positive quotes the stock clause; absence items carry no quote."""

    def __init__(self, config):
        super().__init__()
        self.config = config

    def consume(self, rec, pkt, response):
        row, details = super().consume(rec, pkt, response)
        for name in row:
            if name[0] == 'e' and int(name[1:]) not in ABSENCE and row['v' + name[1:]]:
                row[name] = STOCK
        return row, details


def stream(tmp_path, config, *, factory=None):
    clock = Clock()
    pipeline = QuotingPipeline(config)
    runner = FakeRunner(answer, clock)
    runner.config = config
    return execute_stream('unused', 'unused', tmp_path / 'output', tokenizer_dir='unused',
                          runner_factory=factory or (lambda config, journal: runner),
                          options=StreamOptions(total_runtime_seconds=100000, tier_ceiling=0),
                          records_override=[notice(f'{SHORT}\n{STOCK}\n{EDITED}', rid='r1')],
                          pool=ClockedPool(pipeline, clock), pipeline=pipeline, clock=clock, started_at=clock())


@pytest.fixture
def shipped_index(tmp_path, monkeypatch):
    monkeypatch.setenv('PPS_STREAM_JOURNAL', '1')
    build(write_corpus(tmp_path / 'corpus.jsonl.gz', [STOCK]), tmp_path / 'index.u64')
    monkeypatch.setattr(corpus_lines, 'INDEX_PATH', tmp_path / 'index.u64')


def test_the_executor_leaves_rows_unchanged_while_the_switch_is_off(tmp_path, shipped_index, monkeypatch):
    for name, config in (('absent', CONFIG), ('false', SimpleNamespace(**vars(CONFIG), corpus_seen_filter=False))):
        stream(tmp_path / name, config)
        row = csv_rows(tmp_path / name / 'output/submission.csv')['r1']
        assert all(row[f'v{k}'] == '1' for k in range(1, 25))
        assert all(row[f'e{k}'] == ('' if k in ABSENCE else STOCK) for k in range(1, 25))
        saved = read_rows(tmp_path / name / 'output/stream_records_00000.jsonl.gz')[0]
        assert '_corpus_seen_dropped' not in saved['sources']
    # Off needs no index file at all.
    monkeypatch.setattr(corpus_lines, 'INDEX_PATH', tmp_path / 'missing.u64')
    stream(tmp_path / 'no_index', CONFIG)


def test_the_executor_applies_the_filter_to_the_final_row_only(tmp_path, shipped_index):
    stream(tmp_path, SimpleNamespace(**vars(CONFIG), corpus_seen_filter=True))
    dropped = [k for k in range(1, 25) if k not in EXCLUDED]
    row = csv_rows(tmp_path / 'output/submission.csv')['r1']
    assert all((row[f'v{k}'], row[f'e{k}']) == ('0', '') for k in dropped)
    assert all(row[f'v{k}'] == '1' for k in EXCLUDED) and row['e24'] == STOCK
    saved = read_rows(tmp_path / 'output/stream_records_00000.jsonl.gz')[0]
    assert saved['sources']['_corpus_seen_dropped'] == dropped and saved['row']['v6'] == 0
    b3 = csv_rows(tmp_path / 'output/B3.csv')['r1']
    assert all((b3[f'v{k}'], b3[f'e{k}']) == ('1', STOCK) for k in dropped)


def test_the_switch_without_its_index_fails_before_the_engine_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(corpus_lines, 'INDEX_PATH', tmp_path / 'missing.u64')
    loaded = []
    with pytest.raises(FileNotFoundError):
        stream(tmp_path, SimpleNamespace(**vars(CONFIG), corpus_seen_filter=True),
               factory=lambda config, journal: loaded.append(config))
    assert not loaded and not (tmp_path / 'output/submission.csv').exists()
    assert (tmp_path / 'output/failure.json').is_file()


def test_a_damaged_index_is_rejected(tmp_path):
    good = index_of(STOCK, STOCK_NEXT, EDITED)
    for name, data in (('empty', b''), ('truncated', good.tobytes()[:-3]), ('unsorted', good[::-1].tobytes()),
                       ('repeated', np.repeat(good, 2).tobytes())):
        (tmp_path / name).write_bytes(data)
        with pytest.raises(ValueError):
            corpus_lines.load(tmp_path / name)
    (tmp_path / 'good').write_bytes(good.tobytes())
    assert corpus_lines.load(tmp_path / 'good').tolist() == good.tolist()


def test_replay_assembly_matches_the_executor_with_the_filter_on():
    rec, index = notice(f'{STOCK}\n{EDITED}'), index_of(STOCK)
    state = RecordState(0, rec)
    state.rows['A1'] = {'v6': 1, 'e6': STOCK, 'v7': 1, 'e7': EDITED}
    state.fallback_rows['A19'] = ({'v22': 1, 'e22': STOCK, 'v24': 1, 'e24': STOCK}, [])
    expected = StreamingExecutor._assemble(SimpleNamespace(counts=Counter(), corpus_index=index), state)
    actual, owners = cell_reconsume.assemble(rec['id'], state.rows, {'A19': state.fallback_rows['A19'][0]}, rec, index)
    assert actual == expected and state.sources['_corpus_seen_dropped'] == [6, 22]
    assert (actual['v6'], actual['e6'], actual['v7'], actual['v22'], actual['v24']) == (0, '', 1, 0, 1)
    assert owners['v6'] == owners['e22'] == 'corpus_seen_filter' and owners['v7'] == 'A1'


def test_index_build_is_deterministic_sorted_and_distinct(tmp_path):
    texts = [f'{SHORT}\n{STOCK}\n  {STOCK_NEXT}  ', f'{STOCK}\n{EDITED}\n\n{SHORT}']
    first = build(write_corpus(tmp_path / 'a.jsonl.gz', texts), tmp_path / 'a.u64')
    second = build(write_corpus(tmp_path / 'b.jsonl.gz', texts[::-1] + texts), tmp_path / 'b.u64')
    assert (tmp_path / 'a.u64').read_bytes() == (tmp_path / 'b.u64').read_bytes()
    assert first['output_sha256'] == second['output_sha256'] and first['hashes'] == 3 and first['output_bytes'] == 24
    assert first['records'] == 2 and len(first['corpus_sha256']) == 64
    # Lines under 12 normalized characters are not indexed; the rest are exactly the normalized long lines.
    assert corpus_lines.load(tmp_path / 'a.u64').tolist() == index_of(STOCK, STOCK_NEXT, EDITED).tolist()
