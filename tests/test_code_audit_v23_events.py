"""Event identity and date-field ownership controls; no competition labels/IDs."""
import datetime as dt
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import catalog, facts, judge, switches


@pytest.fixture(autouse=True)
def scoped_probe(monkeypatch):
    monkeypatch.setattr(switches, 'CODE_AUDIT_BASE', True)
    monkeypatch.setattr(switches, 'V23_LEGAL_COUNT', False)


def bundle(*lines):
    b = facts.build({'id': 'structural-event-example',
                     'meta': {'적용계약법': '지방계약법', '업무구분': '일반용역',
                              '계약방법': '제한경쟁', '낙찰방법': '협상에의한계약',
                              '입찰추정가격': 90_000_000, '배정예산금액': 99_000_000},
                     'docs': [{'type': '공고문', 'text': '\n'.join(lines)}]}, catalog.load())
    b.meta.posted = dt.date(2026, 1, 1)
    return b


def set_briefing(b, line):
    ln = next(x for x in b.notice.lines if x.text == line)
    b.cands['brief'] = [ln]
    # Even a mistaken positive model reading cannot create a date for an
    # explicitly absent event or for the bidder's evaluation presentation.
    b.readings['brief'] = {ln.i: {'참석': '참석해야 입찰·제안 가능'}}
    return ln


@pytest.mark.parametrize('denial', ['시행하지 않음', '시행하지 않습니다', '미시행'])
def test_absent_briefing_cannot_borrow_the_reception_date(denial):
    text = f'접수일시: 2026. 1. 20. (현장설명회는 {denial})'
    b = bundle(text, '제안서 제출 마감: 2026. 1. 20.')
    set_briefing(b, text)
    assert judge.briefing_date(b)[0] is None
    assert judge.v23(b) is None


def test_wrapped_nonholding_statement_has_the_same_subject():
    b = bundle('1. 현장설명회', '시행하지 않습니다.', '2. 제안서 제출 마감: 2026. 1. 20.')
    set_briefing(b, '1. 현장설명회')
    assert judge.briefing_date(b)[0] is None


@pytest.mark.parametrize('text', [
    '제안설명회 및 현장방문 일시가 변경될 경우 별도 통보함',
    '제안서 설명회(1차 평가)',
    '제안발표회 및 현장방문 평가',
])
def test_proposal_evaluation_is_not_the_orderers_briefing(text):
    b = bundle(text, '일시: 2026. 1. 20.', '제안서 제출 마감: 2026. 1. 20.')
    set_briefing(b, text)
    assert judge.briefing_date(b)[0] is None


@pytest.mark.parametrize('event', ['사업설명회', '제안요청서 설명회', '현장설명회'])
def test_real_briefing_on_deadline_day_keeps_zero_day_check(event):
    text = f'1. {event}: 2026. 1. 20.'
    b = bundle(text, '2. 제안서 제출 마감: 2026. 1. 20.')
    ln = set_briefing(b, text)
    assert judge.briefing_date(b)[0] == dt.date(2026, 1, 20)
    assert judge.v23(b) is ln


def test_unrelated_nonholding_statement_does_not_cancel_a_real_briefing():
    text = '1. 현장설명회: 2026. 1. 20.'
    b = bundle(text, '별도 질의응답은 시행하지 않습니다.', '2. 제안서 제출 마감: 2026. 1. 20.')
    set_briefing(b, text)
    assert judge.briefing_date(b)[0] == dt.date(2026, 1, 20)


def test_unrelated_same_line_event_denial_keeps_the_briefing():
    text = '1. 현장설명회: 2026. 1. 20.; 별도 질의응답은 시행하지 않습니다.'
    b = bundle(text, '2. 제안서 제출 마감: 2026. 1. 20.')
    set_briefing(b, text)
    assert judge.briefing_date(b)[0] == dt.date(2026, 1, 20)


def test_deadline_does_not_take_the_next_named_event_date():
    b = bundle('제안서 제출 마감', '제안설명회 일시: 2026. 2. 20.')
    assert judge.proposal_deadline(b) is None


def test_deadline_does_not_take_date_after_a_different_table_row():
    b = bundle('가격 제안서 제출기간: 2026. 1. 10. ~ 2026. 1. 20.',
               '2026. 1. 10. ~ 2026. 1. 20.', '기술 제안서 제출기간',
               '- 우편 접수 불가', '2026. 1. 21. 09:00', '제안설명회 및 제안서 평가')
    assert judge.proposal_deadline(b) == dt.date(2026, 1, 20)


def test_bare_wrapped_deadline_still_works_after_other_named_date():
    b = bundle('개찰일: 2026. 2. 20.', '제안서 제출 마감', '2026. 1. 20.')
    assert judge.proposal_deadline(b) == dt.date(2026, 1, 20)
