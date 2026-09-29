"""RT-T: optional records, field negation, and evaluation witnesses."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import facts, judge, record, switches
from pps_c import rt_t_v2_reading as rt


def bundle(*lines):
    notice = record.build({'id': 'SYNTHETIC', 'meta': {}, 'docs': [
        {'type': '공고문', 'text': '\n'.join(lines)}]})
    return facts.Bundle(notice, SimpleNamespace(P=5e7, B=5.5e7, local_private=False), None, [],
                        cands={'perf': [ln for ln in notice.lines if rt.RECORD.search(ln.text)]},
                        readings={'perf': {ln.i: {'역할': '참가자격'} for ln in notice.lines}})


def first_record(b):
    return next((ln for ln in b.cands['perf'] if rt.RECORD.search(ln.text)
                 and b.read('perf', ln).get('역할') == '참가자격'), None)


@pytest.mark.parametrize('text', ['실적 : 없음', '실적 - 없음 (단, 기술인증서 제출 필수)',
                                  '실적 \xad 없음 (단, 전문회사 신고확인서 제출 필수)',
                                  '실적 | 해당 없음', '실적 요건 : 미요구'])
def test_explicit_none_is_not_a_positive_requirement(text):
    b = bundle(text)
    assert rt.none_value(b, b.notice.lines[0])
    assert rt.filter_hit(b, first_record(b), first_record, 'NONE') is None
    assert first_record(b) is b.notice.lines[0]  # original readings/candidates untouched


@pytest.mark.parametrize('text', ['실적 없는 업체는 참여 불가', '실적 : 없음. 단, 납품 실적 1건은 필수',
                                  '실적 1건 이상인 업체'])
def test_none_keeps_bans_and_other_record_conditions(text):
    b = bundle(text)
    assert not rt.none_value(b, b.notice.lines[0])


@pytest.mark.parametrize('text', [
    '가. 용역수행실적 또는 당해 용역 수행에 필요한 능력과 장비를 갖춘 기관, 단체 또는 사업자',
    '가. 3년 내 납품실적(동일 물품) 또는 사전품질인증을 통해 품질적격판정을 받은 업체이어야 합니다.',
])
def test_complete_nonrecord_alternative(text):
    b = bundle(text)
    assert rt.nonrecord_alternative(b, b.notice.lines[0])
    assert rt.filter_hit(b, first_record(b), first_record, 'OR') is None


@pytest.mark.parametrize('text', [
    '가. 납품실적 또는 유지보수 실적을 보유한 업체',
    '가. 공공기관 또는 민간기업 납품실적을 보유한 업체',
    '가. 납품실적이 있는 업체. 또는 사전품질인증을 받은 업체는 제외합니다.',
    '가. 납품실적 또는 사전품질인증을 받은 업체. 별도로 유지보수 실적이 있어야 합니다.',
    '가. 납품실적과 장비를 갖춘 업체',
    '가. 납품실적을 보유하고, 인증서 또는 필요한 능력과 장비를 갖춘 업체',
])
def test_or_keeps_record_alternatives_negation_and_independent_clauses(text):
    b = bundle(text)
    assert not rt.nonrecord_alternative(b, b.notice.lines[0])


def test_later_real_witness_survives_and_maps_to_original_line():
    b = bundle('가. 납품실적 또는 사전품질인증을 받은 업체',
               '나. 별도로 장비 유지보수 실적이 있는 업체')
    got = rt.filter_hit(b, first_record(b), first_record, 'OR')
    assert got is b.notice.lines[1]
    assert b.notice.lines[0].text.startswith('가. 납품실적')
    assert b.read('perf', b.notice.lines[0])['역할'] == '참가자격'


def test_alternative_in_next_item_does_not_waive_record():
    b = bundle('가. 납품실적이 있는 업체', '나. 또는 사전품질인증을 받은 업체')
    assert not rt.nonrecord_alternative(b, b.notice.lines[0])


def test_evaluation_heading_with_wrapped_text():
    b = bundle('바. 적격심사 시 적용할 이행실적 평가기준은 다음과 같습니다.',
               *['평가기준 안내'] * 7, '이행실적 : 최근 5년간 운행 실적만 인정')
    assert rt.evaluation_context(b, b.notice.lines[-1])


def test_evaluation_does_not_hide_independent_bidder_requirement():
    b = bundle('바. 적격심사 시 적용할 이행실적 평가기준은 다음과 같습니다.',
               '가. 별도로 납품실적을 보유한 업체')
    assert not rt.evaluation_context(b, b.notice.lines[-1])


def test_qualification_heading_ends_evaluation_scope():
    b = bundle('바. 적격심사 시 적용할 이행실적 평가기준은 다음과 같습니다.',
               '2. 입찰참가자격', '가. 실적 1건 이상 필수')
    assert not rt.evaluation_context(b, b.notice.lines[-1])


def test_pipeline_switch_off_and_on(monkeypatch):
    b = bundle('2. 입찰참가자격', '실적 : 없음 (단, 신고확인서 제출 필수)')
    monkeypatch.setattr(switches, 'RTT_V2_NONE', False)
    baseline = judge.v2(b)
    assert baseline is b.notice.lines[1]
    monkeypatch.setattr(switches, 'RTT_V2_NONE', True)
    assert judge.v2(b) is None
