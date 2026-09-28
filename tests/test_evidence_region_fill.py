"""Evidence-only region fill: preserve decisions and exact source substrings."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import catalog, facts, judge, switches, csvout
from pps_c.evidence_region import fill


def bundle(text, heading='1. 입찰참가자격', dtype='공고문'):
    return facts.build({'id': 'region-evidence-control', 'meta': {'업무구분': '물품'},
                        'docs': [{'type': dtype, 'text': heading + '\n' + text}]}, catalog.load())


@pytest.mark.parametrize('text', [
    '가. 법인등기부상 본점 소재지가 서울특별시인 업체이어야 한다.',
    '나. 주된 영업소가 충청남도에 위치해 있는 수련원으로 등록한 업체',
    '다. 업체 위치 : 서울,경기',
    '가. 입찰공고일 기준, 부산・경남 지역 소재(본점 또는 지점 포함) 업체에 한함',
    '나. 주소지(법인인 경우 지사포함)가 서울 및 인천 지역에 사업자가 위치한 수련시설이어야 한다.',
    '다. 서울특별시에 소재한 업체는 납품실적도 보유하여야 한다.',
])
def test_explicit_qualification_is_quoted_verbatim(text):
    b = bundle(text)
    assert fill(b) == text
    assert fill(b) in b.notice.full_text()


@pytest.mark.parametrize('text', [
    '가. 전남지역 순천, 여수, 광양의 어린이집 납품 실적이 있는 업체',
    '가. 납품장소 : 서울특별시에 소재한 발주기관 본점',
    '나. 행사장소 : 서울특별시에 소재한 전시장',
    '가. 발주기관 본점 소재지: 서울특별시',
    '가. 본점 소재지가 서울특별시인 업체도 가능하며 지역제한 없음',
    '가. 본점 소재지는 무관하며 서울특별시 납품이 가능하여야 함',
    '나. 낙찰 후 서울특별시에 영업소를 개설하여야 한다.',
    '다. 공동수급 구성원은 서울특별시에 본점을 두어야 한다.',
    '가. 본점 소재지: 서울특별시 [상세주소]',
])
def test_other_roles_and_nonrestrictions_do_not_fill(text):
    assert fill(bundle(text)) == ''


@pytest.mark.parametrize('heading', ['2. 평가기준', '2. 제출서류', '2. 기타사항'])
def test_excluded_sections_do_not_become_qualification(heading):
    assert fill(bundle('서울특별시에 본점을 둔 업체', heading)) == ''


def test_default_off():
    assert switches.EVIDENCE_REGION_FILL is False


def test_overlay_only_fills_empty_positive_v6_v7(monkeypatch):
    text = '가. 본점 소재지가 서울특별시인 업체에 한한다.'
    b = bundle(text)
    for it in judge.ITEMS:
        monkeypatch.setitem(judge.RULES, it, lambda b: None)
    monkeypatch.setattr(switches, 'DEDICATED', ())
    monkeypatch.setattr(switches, 'DEDICATED_OR', ())
    monkeypatch.setattr(switches, 'SEGMENT_OFF', ())
    monkeypatch.setitem(judge.RULES, 'v6', lambda b: True)
    monkeypatch.setitem(judge.RULES, 'v7', lambda b: True)
    monkeypatch.setitem(judge.RULES, 'v8', lambda b: True)
    monkeypatch.setattr(switches, 'EVIDENCE_REGION_FILL', False)
    off = judge.judge(b)
    monkeypatch.setattr(switches, 'EVIDENCE_REGION_FILL', True)
    on = judge.judge(b)
    assert {it: v[0] for it, v in off.items()} == {it: v[0] for it, v in on.items()}
    assert off['v6'] == off['v7'] == (1, '')
    assert on['v6'] == on['v7'] == (1, text)
    assert on['v8'] == (1, '')
    assert all(on[it] == off[it] for it in judge.ITEMS if it not in ('v6', 'v7'))
    row = csvout.row(b.notice.id, on, b.notice.full_text())
    assert row['e6'] == row['e7'] == text
    monkeypatch.setitem(judge.RULES, 'v6', lambda b: b.notice.lines[1])
    assert judge.judge(b)['v6'] == (1, text)
    monkeypatch.setitem(judge.RULES, 'v7', lambda b: None)
    assert judge.judge(b)['v7'] == (0, '')
