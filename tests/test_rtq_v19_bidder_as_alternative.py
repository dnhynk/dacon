import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import judge, record, switches

def notice(*lines):
    return record.build({'id': 'synthetic', 'meta': {}, 'docs': [{'type': '공고문', 'text': '\n'.join(lines)}]})

from pps_c import rtq_v19_alternative as alt, x3_v19

DEMAND='1) 제조사 정품공급 및 A/S확약서 1부'
WAIVER='※ 단, 제조사의 정품공급확약서 제출이 불가한 경우 납품사의 A/S확약서 1부'
READ={'발급 주체':'제3자(제조사·공급사·기술지원사)', '시점':'입찰 전 발급·보유 또는 입찰서와 함께 제출'}

def test_local_alternative_and_separate_demand(monkeypatch):
    n=notice(DEMAND,WAIVER,'2) 입찰 시 다른 장비 제조사의 기술지원확약서를 제출하여야 한다.')
    b=SimpleNamespace(notice=n,meta=SimpleNamespace(private=False),cands={'pledge':n.lines},read=lambda *a:READ)
    monkeypatch.setattr(switches,'V19_BIDDER_AS_ALTERNATIVE',False)
    assert judge.v19(b).i==0
    monkeypatch.setattr(switches,'V19_BIDDER_AS_ALTERNATIVE',True)
    assert alt.exempt(n,n.lines[0]) and alt.exempt(n,n.lines[1])
    assert not x3_v19._passes(b,n.lines[0],READ)
    assert judge.v19(b).i==2

@pytest.mark.parametrize('second',[
    '※ 제조사 확약서 제출이 불가한 경우 입찰 참가 불가',
    '※ 단, 제조사의 정품공급확약서 제출이 불가한 경우 총판사의 A/S확약서 1부',
    '2) 별도 서류 제출',
])
def test_no_self_alternative_no_waiver(second):
    n=notice(DEMAND,second,WAIVER)
    assert not alt.exempt(n,n.lines[0])

def test_no_cross_document_exception():
    n=record.build({'id':'synthetic','meta':{},'docs':[
        {'type':'공고문','text':DEMAND},{'type':'규격서','text':WAIVER}]})
    assert not alt.exempt(n,n.lines[0])
