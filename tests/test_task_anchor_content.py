"""An addressable field still needs actual task content, not an identifier."""
import pytest

from submission.pps.catalog_scope import whole_task_witnesses
from submission.pps.retrieval import Span
from tests.test_notice_search import record


@pytest.mark.parametrize('text', [
    '○ 구매관리번호 : [부서]-1005',
    '사업명: [공고명]',
    '라. 사업내용: 세부내용은 과업지시서 등 참조',
    '사업내용: 세부내용은 과업지시서(제안요청서 포함)에 따름',
    '과업내용: 별첨 ‘과업지시서’ 및 ‘제안요청서’ 참조',
    '행사장소: 부산 국제컨벤션센터',
    '1) 입찰 공고일 전일까지 행사기획업, 이벤트 및 행사대행업 사업자 등록을 마친 업체(행사대행업 : 9901)',
    '❍ 강좌운영: 2026. 7. ~ 10.',
    '본 용역의 과업은 과업지시서에 따름',
])
def test_an_administrative_identifier_or_reference_is_not_actual_scope(text):
    rec = record(text)
    assert whole_task_witnesses(rec, [Span(0, '공고문', 0, len(text), text)], [1]) == []


@pytest.mark.parametrize('text', [
    '사업명: [수요기관(공기업)] 연구 자료 분석 및 운영 자문',
    '사업내용: 역사 참고자료를 편집하고 신규 조사 결과를 분석한다.',
    '사업내용: 해양환경 영향조사 및 평가를 수행한다. 세부 내용은 과업지시서 참조',
    '사업내용 | 64개소*3회 악취측정 용역 ※과업지시서 참조',
    '사 업 명: [수요기관] 자문 및 운영 분석',
])
def test_actual_task_content_survives_anonymization_and_reference_words(text):
    rec = record(text)
    result = whole_task_witnesses(rec, [Span(0, '공고문', 0, len(text), text)], [1])
    assert result and result[0]['text'] == text


def test_a_long_whole_task_line_cannot_be_accepted_by_a_truncated_prefix():
    text = '본 계약의 범위는 ' + '연구자료 분석 및 통계자문을 수행한다. ' * 20 + '단, 해외조사는 제외한다.'
    rec = record(text)
    shown = text[:400]
    assert whole_task_witnesses(rec, [Span(0, '공고문', 0, len(shown), shown)], [1]) == []
