"""A preference or work-site cue cannot erase a separate explicit requirement."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import catalog, facts, judge, switches


def bundle(text):
    b = facts.build({'id': 'structural-role-example',
                     'meta': {'업무구분': '일반용역'},
                     'docs': [{'type': '공고문', 'text': '2. 입찰참가자격\n가. ' + text}]}, catalog.load())
    b.cands['perf'] = []
    return b


def test_record_requirement_survives_a_separate_preference(monkeypatch):
    monkeypatch.setattr(switches, 'PERF_UNREAD', True)
    b = bundle('유사 용역 수행실적을 보유하여야 하며, 여성기업은 우대합니다.')
    ln = b.notice.lines[-1]
    assert ln in judge.perf_lines(b)
    assert ln in judge.x2_unread_records(b)


def test_record_preference_alone_is_not_a_requirement(monkeypatch):
    monkeypatch.setattr(switches, 'PERF_UNREAD', True)
    b = bundle('유사 용역 수행실적이 있는 업체 우대')
    assert not judge.perf_lines(b)
    assert not judge.x2_unread_records(b)


@pytest.mark.parametrize('tail', ['여성기업은 우대합니다.', '납품 장소는 청사 별관입니다.'])
def test_explicit_bidder_office_requirement_survives_other_roles(tail, monkeypatch):
    monkeypatch.setattr(switches, 'V6_ORDERER_ANY', True)
    b = bundle('본점 소재지가 [수요기관(기초자치단체)] 관내에 있어야 하며, ' + tail)
    assert judge.x7_v6_extra(b) is not None


def test_preference_first_cannot_hide_a_later_required_record(monkeypatch):
    monkeypatch.setattr(switches, 'PERF_UNREAD', True)
    b = bundle('건축 실적이 있는 업체는 우대하며, 유지보수 수행실적을 보유하여야 합니다.')
    ln = b.notice.lines[-1]
    assert ln in judge.perf_lines(b)
    assert ln in judge.x2_unread_records(b)


def test_work_site_first_cannot_hide_a_later_bidder_office(monkeypatch):
    monkeypatch.setattr(switches, 'V6_ORDERER_ANY', True)
    b = bundle('납품장소: 각 사업장 ([수요기관(기초자치단체)] 관내), '
               '본점 소재지가 [수요기관(기초자치단체)] 관내에 있어야 합니다.')
    assert judge.x7_v6_extra(b) is not None
