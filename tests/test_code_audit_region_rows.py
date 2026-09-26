"""Location field boundaries with ordinary and anonymised wrapped controls."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import catalog, facts, judge, switches


@pytest.fixture(autouse=True)
def regional_probe(monkeypatch):
    monkeypatch.setattr(switches, 'AUDIT_FIXES', True)
    monkeypatch.setattr(switches, 'AUDIT_FIXES2', True)
    monkeypatch.setattr(switches, 'V7_CLAUSE', True)


def bundle(*lines, docs=None):
    return facts.build({'id': 'structural-region-example',
                        'meta': {'적용계약법': '국가계약법', '업무구분': '일반용역',
                                 '계약방법': '제한경쟁', '낙찰방법': '적격심사',
                                 '입찰추정가격': 90_000_000, '배정예산금액': 99_000_000,
                                 '지역제한여부': 'Y', '제한지역코드목록': '경기도'},
                        'docs': docs or [{'type': '공고문', 'text': '\n'.join(lines)}]}, catalog.load())


@pytest.mark.parametrize('separator', [' | ', ': ', '： '])
def test_delivery_field_is_not_part_of_the_bidders_location(separator):
    b = bundle('2. 입찰참가자격', '입찰참가자격' + separator + '주된 영업소 소재지가 경기도인 업체',
               '납품장소' + separator + '서울특별시 종로구 청사')
    assert judge.v7(b) is None
    assert not judge.v7_clause_lines(b)


def test_wrapped_two_regions_are_still_one_bidder_clause():
    b = bundle('2. 입찰참가자격', '입찰참가자격 | 주된 영업소 소재지가',
               '경기도 또는 서울특별시에 있는 업체', '납품장소 | 충청남도')
    got = judge.v7(b)
    assert got is not None
    clause = judge.region_clause_text(b.notice, got)
    assert '경기도' in clause and '서울특별시' in clause and '충청남도' not in clause


def test_anonymised_values_are_not_labels_because_of_their_colons():
    b = bundle('2. 입찰참가자격', '주된 영업소 소재지가',
               '[지역:r1|단위=광역|광역=경기도] 또는 [지역:r2|단위=광역|광역=서울특별시]인 업체')
    assert judge.v7(b) is not None


def test_continuation_can_read_its_own_preceding_field_label():
    b = bundle('2. 입찰참가자격', '입찰참가자격: 주된 영업소 소재지가', '경기도인 업체',
               '납품장소: 서울특별시')
    ln = next(x for x in b.notice.lines if x.text == '경기도인 업체')
    clause = judge.region_clause_text(b.notice, ln)
    assert '주된 영업소' in clause and '서울특별시' not in clause


def test_region_clause_cannot_cross_attachment_boundary():
    b = bundle(docs=[{'type': '공고문', 'text': '2. 입찰참가자격\n주된 영업소 소재지가 경기도인 업체'},
                     {'type': '규격서', 'text': '서울특별시 납품장소'}])
    ln = b.notice.lines[1]
    assert '서울특별시' not in judge.region_clause_text(b.notice, ln)


@pytest.mark.parametrize('third_pass', [False, True])
def test_v24_reuses_the_same_owned_region_set(third_pass, monkeypatch):
    monkeypatch.setattr(switches, 'AUDIT_FIXES3', third_pass)
    b = bundle('2. 입찰참가자격', '입찰참가자격 | 주된 영업소 소재지가 경기도인 업체',
               '납품장소 | 서울특별시 종로구 청사')
    ln = b.notice.lines[1]
    b.cands['region'] = [ln]
    b.readings['region'] = {ln.i: {'역할': '참가자격', '대상': '입찰자 소재지 제한'}}
    assert judge.v24_region(b) is None
    b.meta.region_sido = {'서울특별시'}
    assert judge.v24_region(b) is ln
