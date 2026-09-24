"""An explicit body definition is task evidence without a title-field match."""
import pytest

from submission.pps.catalog_scope import whole_task_witnesses
from submission.pps.retrieval import Span
from tests.test_notice_search import record


@pytest.mark.parametrize('text', [
    '1. 본 과업은 조사대상지를 현장 조사하고 자료를 분석하는 용역으로 수행한다.',
    '본 용역은 가연성 생활폐기물을 지정 시설까지 운반하는 용역이다.',
    '본 과업의 전체 내용은 조사계획 수립과 환경영향 검토를 수행하는 것이다.',
])
def test_whole_task_definition_in_the_body_is_not_blocked_by_its_surface_form(text):
    spans = [Span(0, '공고문', 0, len(text), text)]
    witnesses = whole_task_witnesses(record(text), spans, [1])
    assert witnesses and witnesses[0]['text'] == text


@pytest.mark.parametrize('text', [
    '본 계약은 일반용역 계약규정을 적용한다.',
    '본 과업은 추가 운송용역을 검토할 수 있다.',
    '본 과업은 중소기업자만 참여할 수 있다.',
    '본 과업의 일부 내용은 조사대상지에서 현장조사를 수행하는 것이다.',
    '본 과업은 현장조사를 수행한다는 예시이다.',
    '나. 입찰자 중 예정가격의 88% 이상 금액의 견적을 제출한 자 중에서 수의계약 운영요령에 따라 계약상대자를 결정합니다.',
])
def test_procedure_hypothetical_and_partial_tasks_do_not_establish_whole_scope(text):
    spans = [Span(0, '공고문', 0, len(text), text)]
    assert whole_task_witnesses(record(text), spans, [1]) == []
