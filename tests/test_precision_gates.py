"""Precision gates (config `precision_gates`): definitions, switch validation and executor/replay wiring (CPU only)."""
import dataclasses
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from submission.pps import precision_gates
from submission.pps.comparison import compare, positive_decision
from submission.pps.precision_gates import GATES, SET_RULES
from submission.pps.prompts import Config
from submission.stream import RecordState, StreamingExecutor
from tests.test_comparison import record as comparison_record
from tests.test_stream_executor import CONFIG, Clock, FakeRunner, SyntheticPipeline, answer, csv_rows, read_rows, record, run
from tools import cell_reconsume

SHIPPED = Path(__file__).resolve().parents[1] / 'submission/model/config.json'
FILLER = ['가' * 40] * 60            # line-length p90 40: the long-line threshold is 1.25 * 40 + 5 = 55 characters
NO_AMOUNT = '가. 최근 3년 이내 유사용역 수행실적이 있는 업체'
WITH_AMOUNT = '나. 최근 3년 이내 단일 건 1억원 이상의 유사용역 이행실적이 있는 업체'
BELOW_MILLION = '다. 최근 3년 이내 50만원 이상의 유사 실적이 있는 업체'
RATIO = '라. 추정가격의 100% 이상 유사용역 실적이 있는 업체'
WRAPPED = '마. 최근 3년 이내 유사용역 이행실적이 있는 업체로서'
WRAPPED_NEXT = '   단일 계약금액 5천만원 이상인 자'
REGION = '바. 본점 소재지가 서울특별시에 있는 업체'
LONG = '사. ' + '본점 소재지가 서울특별시 관내에 있는 업체로서 ' * 3
SECTION = [NO_AMOUNT, WITH_AMOUNT, BELOW_MILLION, RATIO, WRAPPED, WRAPPED_NEXT, REGION, LONG, '아. 기타']
# Held-briefing lines with an organiser placeholder and a participation-restricting attendance requirement.
DEFERRED = '가. 사업설명회를 개최하며(일시·장소는 추후 공지), 설명회에 참석하지 아니한 업체의 입찰 참가는 허용되지 않음'
VENUE = '나. 현장설명회: 2026. 3. 4.(수) 10:00 발주기관 대회의실, 현장설명회 참석업체에 한하여 입찰 참가 자격을 부여함'
EM_DASH = '다. 과업설명회 개최 — 미참석 업체는 입찰 참가 대상에서 제외함'
ATTENDEE = '  ○ 발주기관이 개최하는 사업설명회에 참석한 자(설명회 일정은 추후 안내)  '
NEGOTIATED = {'낙찰방법': '협상에의한계약'}


def notice(lines=FILLER + SECTION, rid='gate-case', **meta):
    return {'id': rid, 'meta': {'업무구분': '일반용역', **meta},
            'docs': [{'type': '공고문', 'doc_id': rid + '-0', 'text': '\n'.join(lines)}],
            'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


def row_with(**cells):
    row = {'id': 'gate-case', **{f'v{k}': 0 for k in range(1, 25)}, **{f'e{k}': '' for k in range(1, 25)}}
    for name, quote in cells.items():
        row['v' + name[1:]], row['e' + name[1:]] = 1, quote
    return row


def gated(rec, item, quote, gate):
    row = row_with(**{f'v{item}': quote})
    cleared = precision_gates.apply(rec, row, (gate,))
    assert (row[f'v{item}'], row[f'e{item}']) == ((0, '') if cleared else (1, quote))
    return cleared


def test_size_registration_gates_read_meta_clause_content():
    kept16 = ('중소기업자간 경쟁제품', '중기업 제한', '지정·고시한 제품', '중소벤처기업부장관이 지정 공고한 물품')
    kept18 = ('소기업 또는 소상공인', '소상공인 제한', '중소기업자')          # 중소기업자 contains 소기업
    for value in kept16:
        assert gated(notice(조항호내용=value), 16, '', 'v16_size_registration') == []
    for value in kept18:
        assert gated(notice(조항호내용=value), 18, '', 'v18_size_registration') == []
    for value in ('경쟁은 입찰의 방법으로 행함', None, '미입력'):
        assert gated(notice(조항호내용=value), 16, '', 'v16_size_registration') == [(16, 'v16_size_registration')]
    for value in ('중기업 제한', '지정·고시한 제품', None):
        assert gated(notice(조항호내용=value), 18, '', 'v18_size_registration') == [(18, 'v18_size_registration')]


def test_v24_keeps_only_a_non_method_witness_and_its_evidence():
    budget = comparison_record('사업예산: 1억원(부가세 포함)\n입찰대상금액: 3천만원(부가세 포함)', 배정예산금액=40000000)
    title = comparison_record('장비 운영(일반경쟁·1천만원미만)\n입찰방법: 제한경쟁', 계약방법='일반경쟁')
    method = comparison_record('배정예산금액: 55,000,000원\n계약방법: 일반경쟁')
    nothing = comparison_record('입찰방법: 제한경쟁')
    for rec, field, kept in ((budget, 'budget', True), (title, 'title_tag', True),
                             (method, 'competition_method', False), (nothing, None, False)):
        decision = positive_decision(rec, compare(rec))
        assert (decision['comparison']['field'] if decision else None) == field
        # The gate never replaces e24; with 24 in source_rule_items it is the witness's own evidence.
        assert gated(rec, 24, 'model quote', 'v24_non_method_witness') == ([] if kept else [(24, 'v24_non_method_witness')])


def test_v2_needs_an_amount_or_price_ratio_in_the_overlapped_performance_clause():
    rec = notice()
    for quote in (WITH_AMOUNT[3:], RATIO[3:], WRAPPED[3:],
                  WRAPPED_NEXT.strip()):              # the wrapped clause joins its marker line and continuation
        assert gated(rec, 2, quote, 'v2_amount') == []
    for quote in (NO_AMOUNT[3:], BELOW_MILLION[3:],   # 50만원 is below the 1,000,000 floor
                  REGION[3:],                         # no 실적/경험 clause is overlapped
                  '최근3년 이내 유사용역 수행실적'):    # located without whitespace
        assert gated(rec, 2, quote, 'v2_amount') == [(2, 'v2_amount')]
    for quote in ('공고 어디에도 없는 문장입니다', ''):   # unlocated quote or no quote: unchanged
        assert gated(rec, 2, quote, 'v2_amount') == []


def test_long_line_gate_clears_quotes_anchored_on_an_overlong_line():
    rec = notice()
    assert len(LONG) > 55 >= max(len(x) for x in SECTION if x != LONG)
    for k in precision_gates.LONG_LINE_ITEMS:
        assert gated(rec, k, LONG[3:30], 'long_line') == [(k, 'long_line')]
        assert gated(rec, k, WITH_AMOUNT[3:], 'long_line') == []
    for k in set(range(1, 25)) - set(precision_gates.LONG_LINE_ITEMS):
        assert gated(rec, k, LONG[3:30], 'long_line') == []
    # The anchor is the quoted line with the largest overlap.
    assert gated(rec, 3, REGION[-6:] + '\n' + LONG[:40], 'long_line') == [(3, 'long_line')]
    assert gated(rec, 3, REGION[3:] + '\n' + LONG[:3], 'long_line') == []
    # Exactly at the threshold is not overlong; fewer than 30 lines of 10+ characters give no width.
    assert gated(notice(FILLER + ['사. ' + '가' * 52]), 3, '가' * 52, 'long_line') == []
    assert gated(notice(FILLER + ['사. ' + '가' * 53]), 3, '가' * 53, 'long_line') == [(3, 'long_line')]
    assert gated(notice(FILLER[:28] + [LONG]), 3, LONG[3:30], 'long_line') == []


def test_gates_touch_enabled_positives_only_and_report_in_gate_order():
    rec = notice(조항호내용='경쟁은 입찰의 방법으로 행함')
    row = row_with(v2=NO_AMOUNT[3:], v3=LONG[3:30], v16='', v18='', v24='model quote')
    row['e5'] = LONG[3:30]                        # a quote without a positive is not a target
    before = dict(row)
    assert precision_gates.apply(rec, row, ()) == [] and row == before
    assert precision_gates.apply(rec, row, ('long_line',)) == [(3, 'long_line')] and row['v2'] == 1
    assert precision_gates.apply(rec, row, tuple(reversed(GATES))) == [
        (16, 'v16_size_registration'), (18, 'v18_size_registration'), (24, 'v24_non_method_witness'), (2, 'v2_amount')]
    assert row['e5'] == LONG[3:30] and all(row[f'v{k}'] == 0 for k in range(1, 25))


def test_config_lists_distinct_known_gate_names_only():
    assert Config().precision_gates == ()
    assert dataclasses.replace(Config(), precision_gates=['long_line', 'v2_amount']).precision_gates == ['long_line', 'v2_amount']
    for bad in (['long_line', 'long_line'], ['v20_amount'], [1], 'long_line', [['long_line']], None):
        with pytest.raises(ValueError):
            dataclasses.replace(Config(), precision_gates=bad)


def test_v20_applicability_clears_v20_in_a_private_contract_notice_only():
    for method, cleared in (('수의계약', True), ('제한경쟁', False), ('일반경쟁', False), (None, False)):
        assert gated(notice(계약방법=method), 20, '', 'v20_applicability') == ([(20, 'v20_applicability')] if cleared else [])
    assert gated(notice(), 20, '', 'v20_applicability') == []                     # no 계약방법 in meta
    row = row_with()
    assert precision_gates.apply(notice(계약방법='수의계약'), row, ('v20_applicability',)) == [] and row['v20'] == 0
    assert 'v20_applicability' not in SET_RULES


def placeholder(lines, meta=NEGOTIATED, **cells):
    """Run briefing_placeholder alone on a notice with `lines`; return (changes, v22, e22, v23)."""
    row = row_with(**cells)
    changes = precision_gates.apply(notice(FILLER + list(lines), **meta), row, ('briefing_placeholder',))
    return changes, row['v22'], row['e22'], row['v23']


def test_briefing_placeholder_sets_v22_with_the_placeholder_clause():
    for line in (DEFERRED, VENUE, EM_DASH, ATTENDEE):
        assert placeholder([NO_AMOUNT, line, REGION]) == ([(22, 'briefing_placeholder')], 1, line.strip(), 0)
    # The first qualifying line in document order is the evidence.
    assert placeholder([EM_DASH, DEFERRED])[2] == EM_DASH
    assert 'briefing_placeholder' in SET_RULES


def test_briefing_placeholder_needs_the_items_own_applicability():
    for meta in ({'낙찰방법': '적격심사제'}, {'낙찰방법': '소액수의견적'}, {}):
        assert placeholder([DEFERRED], meta) == ([], 0, '', 0)


def test_briefing_placeholder_without_a_placeholder_or_attendance_restriction_is_a_no_op():
    for line in ('라. 사업설명회에 참석하지 아니한 업체의 입찰 참가는 허용되지 않습니다.',        # no placeholder
                 '마. 사업설명회 개최(일시·장소는 개별 통보)',                                   # no attendance requirement
                 '바. 사업설명회 개최(일정은 추후 안내), 설명회 참석 여부와 상관없이 입찰 참가 자격을 부여함',  # negated
                 '사. 현장설명회: 생략(일시·장소 별도 안내), 미참석 업체는 입찰 참가 불가',     # briefing omitted
                 '아. 제안서 설명회 개최(일정은 개별 통보), 불참 업체는 평가에서 제외',        # proposal presentation
                 # Template heading: C's ATTEND matched 참가자격 here on six untouched corpus notices.
                 '6. 제안요청설명의 일시·장소·참가자격 및 참가의무 유무에 관한 사항',
                 '자. 사업설명회 개최(일정은 추후 안내), 설명회 참석업체에 한하여 참가 허용' + '.' * 500):  # over 500
        assert placeholder([line]) == ([], 0, '', 0)
    assert placeholder([]) == ([], 0, '', 0)


def test_briefing_placeholder_leaves_an_existing_v22_and_v23_alone():
    assert placeholder([DEFERRED], v22='model quote') == ([], 1, 'model quote', 0)
    # A dated placeholder line in a local negotiated notice does not set v23 (no schedule shortfall proven).
    assert placeholder([VENUE], {**NEGOTIATED, '적용계약법': '지방계약법'})[3] == 0


def test_set_and_cleared_pairs_report_in_gate_order():
    rec = notice(FILLER + [DEFERRED], 조항호내용='경쟁은 입찰의 방법으로 행함', 계약방법='수의계약', **NEGOTIATED)
    row = row_with(v16='', v20='')
    changes = precision_gates.apply(rec, row, tuple(reversed(GATES)))
    assert changes == [(16, 'v16_size_registration'), (20, 'v20_applicability'), (22, 'briefing_placeholder')]
    assert [name in SET_RULES for _, name in changes] == [False, False, True]
    assert (row['v16'], row['v20'], row['v22'], row['e22']) == (0, 0, 1, DEFERRED)


def test_shipped_config_enables_all_gates():
    assert tuple(Config.load(SHIPPED).precision_gates) == GATES
    assert {'v20_applicability', 'briefing_placeholder'} <= set(GATES) and SET_RULES <= set(GATES)


def stream_one(tmp_path, monkeypatch, config):
    monkeypatch.setenv('PPS_STREAM_JOURNAL', '1')
    rec = record('r1')
    rec['meta']['조항호내용'] = '중기업 제한'
    pipeline = SyntheticPipeline()
    pipeline.config = config
    clock = Clock()
    runner = FakeRunner(answer, clock)
    runner.config = config
    run(tmp_path, [rec], pipeline, answer, clock=clock, runner=runner)
    return (csv_rows(tmp_path / 'output/submission.csv')['r1'],
            read_rows(tmp_path / 'output/stream_records_00000.jsonl.gz')[0], csv_rows(tmp_path / 'output/B3.csv')['r1'])


def test_the_executor_gates_the_final_row_only(tmp_path, monkeypatch):
    # The synthetic model answers 1 everywhere without quotes: the registration and v24 gates act.
    row, saved, b3 = stream_one(tmp_path, monkeypatch, SimpleNamespace(**vars(CONFIG), precision_gates=GATES))
    assert (row['v16'], row['v18'], row['v24']) == ('1', '0', '0')
    assert all(row[f'v{k}'] == '1' for k in range(1, 25) if k not in (18, 24))
    assert saved['sources']['_precision_gates'] == [[18, 'v18_size_registration'], [24, 'v24_non_method_witness']]
    assert saved['row']['v18'] == 0 and all(b3[f'v{k}'] == '1' for k in range(1, 25))


def test_without_gates_the_executor_changes_nothing(tmp_path, monkeypatch):
    for name, config in (('absent', CONFIG), ('empty', SimpleNamespace(**vars(CONFIG), precision_gates=()))):
        row, saved, _ = stream_one(tmp_path / name, monkeypatch, config)
        assert all(row[f'v{k}'] == '1' for k in range(1, 25))
        assert '_precision_gates' not in saved['sources']


def test_replay_assembly_matches_the_executor_with_gates_on():
    rec = notice(조항호내용='소기업 또는 소상공인')
    state = RecordState(0, rec)
    state.rows['A1'] = {'v2': 1, 'e2': NO_AMOUNT[3:], 'v3': 1, 'e3': LONG[3:30], 'v4': 1, 'e4': WITH_AMOUNT[3:]}
    state.rows['A10'] = {'v16': 1, 'e16': '', 'v18': 1, 'e18': ''}
    state.fallback_rows['A19'] = ({'v22': 1, 'e22': REGION[3:], 'v24': 1, 'e24': REGION[3:]}, [])
    expected = StreamingExecutor._assemble(SimpleNamespace(counts=Counter(), precision_gates=GATES), state)
    actual, owners = cell_reconsume.assemble(rec['id'], state.rows, {'A19': state.fallback_rows['A19'][0]}, rec,
                                             None, (), GATES)
    assert actual == expected
    assert state.sources['_precision_gates'] == [(24, 'v24_non_method_witness'), (2, 'v2_amount'), (3, 'long_line')]
    assert [actual[f'v{k}'] for k in (2, 3, 4, 16, 18, 22, 24)] == [0, 0, 1, 1, 1, 1, 0]
    assert owners['v2'] == owners['e2'] == 'precision_gate:v2_amount' and owners['v3'] == 'precision_gate:long_line'
    assert owners['v24'] == 'precision_gate:v24_non_method_witness' and owners['v4'] == 'A1' and owners['v22'] == 'A19'
    # Off, the replay leaves the assembled row alone.
    assert cell_reconsume.assemble(rec['id'], state.rows, {'A19': state.fallback_rows['A19'][0]}, rec)[0]['v2'] == 1


def test_replay_and_executor_record_the_set_rule_like_a_gate():
    rec = notice(FILLER + [DEFERRED], 계약방법='수의계약', **NEGOTIATED)
    state = RecordState(0, rec)
    state.rows['A19'] = {'v20': 1, 'e20': '', 'v22': 0, 'e22': '', 'v23': 0, 'e23': ''}
    expected = StreamingExecutor._assemble(SimpleNamespace(counts=Counter(), precision_gates=GATES), state)
    actual, owners = cell_reconsume.assemble(rec['id'], state.rows, {}, rec, None, (), GATES)
    assert actual == expected
    assert state.sources['_precision_gates'] == [(20, 'v20_applicability'), (22, 'briefing_placeholder')]
    assert (actual['v20'], actual['v22'], actual['e22'], actual['v23']) == (0, 1, DEFERRED, 0)
    assert owners['v22'] == owners['e22'] == 'precision_gate:briefing_placeholder'
    assert owners['v20'] == 'precision_gate:v20_applicability'
