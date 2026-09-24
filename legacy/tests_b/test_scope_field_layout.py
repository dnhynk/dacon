"""Field delimiters may change in extracted text without changing task content."""
import pytest

from submission.pps.catalog_scope import whole_task_witnesses
from submission.pps.retrieval import Span
from tests.test_notice_search import record


@pytest.mark.parametrize('text,field', [
    ('사업명:\n국내 담수식물 표본 확보\n과업기간: 8개월', '사업명:\n국내 담수식물 표본 확보'),
    ('사업명\n국내 담수식물 표본 확보\n과업기간: 8개월', '사업명\n국내 담수식물 표본 확보'),
    ('공 고 명\n국내 담수식물 표본 확보\n수요기관\n[수요기관]', '공 고 명\n국내 담수식물 표본 확보'),
    ('과업내용\n국내 담수식물 표본 확보 100종\n과업기간\n8개월', '과업내용\n국내 담수식물 표본 확보 100종'),
    ('1. 계약명: 전세버스 임차 용역\n2. 계약기간: 1년', '1. 계약명: 전세버스 임차 용역'),
    ('안내문\n' * 350 + '입찰건명 전세버스 임차 용역\n입찰마감: 2026. 8. 1.', '입찰건명 전세버스 임차 용역'),
])
def test_identified_field_retains_its_original_delimiter_and_value(text, field):
    rec = record(text)
    result = whole_task_witnesses(rec, [Span(0, '공고문', 0, len(text), text)], [1])
    assert any(w['text'] == field for w in result)
    assert all(text[w['start']:w['end']] == w['text'] for w in result)


@pytest.mark.parametrize('text', [
    '사업명\n사업기간: 8개월\n계약금액: 1억원',
    '용 역 명\n용 역 개 요\n용 역 기 간\n계 약 금 액 (원)\nAI 비전수립 연구 용역\n제안요청서 참조\n7개월\n1억원',
    '사업명\n[공고명]\n과업기간: 8개월',
    '과업내용\n별첨 과업지시서 참조\n과업기간: 8개월',
])
def test_a_missing_value_or_ambiguous_column_order_is_not_reconstructed(text):
    assert whole_task_witnesses(record(text), [Span(0, '공고문', 0, len(text), text)], [1]) == []


def test_value_without_its_original_label_is_not_a_complete_field():
    text = '과업내용\n국내 담수식물 표본 확보 100종\n과업기간\n8개월'
    start = text.index('국내')
    end = text.index('\n과업기간')
    shown = Span(0, '공고문', start, end, text[start:end])
    assert whole_task_witnesses(record(text), [shown], [1]) == []
