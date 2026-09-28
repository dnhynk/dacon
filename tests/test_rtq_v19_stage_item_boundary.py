import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import judge, record, switches

def notice(*lines):
    return record.build({'id': 'synthetic', 'meta': {}, 'docs': [{'type': '공고문', 'text': '\n'.join(lines)}]})

def test_next_numbered_topic_cannot_time_pledge(monkeypatch):
    n=notice('※ 공급사 기술지원 확약서 및 실적증명서 제출',
             '3) 중소기업참여 비율(5점) : 공동수급 입찰참가 시 중소기업참여 비율')
    b=SimpleNamespace(notice=n)
    monkeypatch.setattr(switches, 'C2_PLEDGE_VOCAB', True)
    monkeypatch.setattr(switches, 'V19_STAGE_ITEM_BOUNDARY', False)
    assert judge.x6_pledge_stage(b,n.lines[0]) == 'PRE'
    monkeypatch.setattr(switches, 'V19_STAGE_ITEM_BOUNDARY', True)
    assert judge.x6_pledge_stage(b,n.lines[0]) == 'OTHER'

def test_real_wrapped_timing_stays(monkeypatch):
    n=notice('※ 제조사의 물품공급 확약서는', '입찰서 제출 시 제출하여야 한다.')
    monkeypatch.setattr(switches, 'V19_STAGE_ITEM_BOUNDARY', True)
    assert judge.x6_pledge_stage(SimpleNamespace(notice=n),n.lines[0]) == 'PRE'
