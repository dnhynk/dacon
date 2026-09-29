"""RT-P2 source-reading regressions, private CPU tree only."""
from types import SimpleNamespace
import pytest
from pps_c import judge, record, switches, families
from pps_c.x2_v13_forms import small_only_any2


def bundle(text):
    n = record.build({'id': 'T', 'meta': {}, 'docs': [{'type': '공고문', 'text': text}]})
    return SimpleNamespace(notice=n, cands={}, read=lambda *a: {}, meta=SimpleNamespace(P=1e8))


@pytest.fixture
def active(monkeypatch):
    monkeypatch.setattr(switches, 'RTP2_V13_NOTE_BOUNDARY', True)
    monkeypatch.setattr(switches, 'RTP2_V13_CONDITIONAL_HEADER', True)
    monkeypatch.setattr(switches, 'AUDIT_FIXES2', True)
    monkeypatch.setattr(switches, 'AUDIT_FIXES', True)
    monkeypatch.setattr(families, 'SMALL_LIMIT_ACTIVE', [True])


def test_default_off():
    assert switches.RTP2_V13_NOTE_BOUNDARY is False
    assert switches.RTP2_V13_CONDITIONAL_HEADER is False


def test_starred_unified_certificate_does_not_borrow_partial_class(active, monkeypatch):
    b = bundle('2. 입찰참가자격\n법률 제 조에 따른 소상공인으로서 중소기업 범위 및 확인에 관한 규정 에 따라 발급된\n'
               '」 2 「 」\n\n중 소기업 및 소상공인 확인서를 소지한 자 용도 공공기관 입찰용\n· ( : )\n\n'
               '* ‘중·소기업및소상공인확인서’ 및‘직접생산확인증명서’가 종합정보망에서확인(전자입찰서제출\n'
               '마감일전일까지발급된것으로서전자입찰서제출마감일까지유지되어야함)되지않을경우입찰참가자격이없습니다.')
    note = next(x for x in b.notice.lines if x.text.startswith('*'))
    monkeypatch.setattr(switches, 'RTP2_V13_NOTE_BOUNDARY', False)
    assert judge.size_class(b, note) == 'small'
    monkeypatch.setattr(switches, 'RTP2_V13_NOTE_BOUNDARY', True)
    assert judge.size_class(b, note) == 'sme'
    monkeypatch.setattr(families, 'SMALL_LIMIT_ACTIVE', [False])
    assert judge.size_class(b, note) == 'small'  # other items keep their reading


@pytest.mark.parametrize('reader', [judge.small_only_text, judge.small_only_any, small_only_any2])
def test_class_condition_is_not_unconditional_and_later_clause_is_kept(active, monkeypatch, reader):
    clause = '다. 소기업·소상공인의 경우: 「중소기업기본법」 제2조에 따른 소기업 또는 소상공인으로서 소기업·소상공인확인서를 소지한 자'
    b = bundle('2. 입찰참가자격\n' + clause)
    monkeypatch.setattr(switches, 'RTP2_V13_CONDITIONAL_HEADER', False)
    assert reader(b) is not None
    monkeypatch.setattr(switches, 'RTP2_V13_CONDITIONAL_HEADER', True)
    assert reader(b) is None
    independent = bundle('2. 입찰참가자격\n' + clause + '\n라. 소기업 또는 소상공인으로서 소기업·소상공인확인서를 소지한 자')
    assert reader(independent).text.startswith('라.')


def test_explicit_small_requirement_is_retained(active):
    b = bundle('2. 입찰참가자격\n* 소기업 또는 소상공인으로서 소기업·소상공인확인서를 소지한 자')
    assert judge.size_class(b, b.notice.lines[-1]) == 'small'


def test_conditional_model_reading_is_filtered_only_for_v13(active, monkeypatch):
    b = bundle('2. 입찰참가자격\n다. 소기업·소상공인의 경우: 소기업·소상공인확인서를 소지한 자')
    ln = b.notice.lines[-1]
    monkeypatch.setattr(judge, 'lines_where', lambda *a, **kw: [ln] if kw.get('역할') == '참가자격 제한' else [])
    monkeypatch.setattr(switches, 'AUDIT_FIXES2', False)
    assert judge.size_state(b, positive=True)[0] is None
    assert judge.size_state(b, positive=False)[0] == 'small'
    monkeypatch.setattr(families, 'SMALL_LIMIT_ACTIVE', [False])
    assert judge.size_state(b, positive=True)[0] == 'small'
