"""Actual task fields keep their values; blank bid forms do not set identity."""
import pytest

from submission.pps.products import scope_spans
from submission.pps.qualification import whole_task_support, EVENT
from tests.test_comparison import record


@pytest.mark.parametrize('form', [
    '| ○ 사업건명:\n|',
    '사업명 | 사업기간 (년월) | 계약금액 | 발주처 | 비고',
    '계약건명 : 계약기간 : 업체명 : (대표자) 위 업체는 본 계약 조건을 준수할 것을 서약합니다.',
])
def test_empty_form_does_not_veto_an_actual_event_purchase_title(form):
    rec = record('용역명: 국제포럼 기획 및 행사대행\n'+form)
    spans = scope_spans(rec, 1000, 1000000)
    assert len(spans) == 1
    assert whole_task_support(spans, EVENT)
    assert spans[0]['text'] == '용역명: 국제포럼 기획 및 행사대행'


def test_wrapped_actual_title_keeps_its_original_source_position():
    rec = record('용역명:\n국제포럼 행사대행\n계약기간: 6개월')
    spans = scope_spans(rec)
    assert spans[0]['text'] == '용역명:\n국제포럼 행사대행'
    assert rec['docs'][0]['text'][spans[0]['start']:spans[0]['end']] == spans[0]['text']


def test_different_populated_contract_title_still_blocks_whole_family_identity():
    rec = record('용역명: 국제포럼 행사대행\n사업명: 정책 연구 개발')
    spans = scope_spans(rec)
    assert len(spans) == 2
    assert not whole_task_support(spans, EVENT)
