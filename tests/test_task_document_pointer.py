import copy

import pytest

from submission.b4_entry import B4Pipeline, parse_error
from submission.pps.catalog_scope import whole_task_witnesses
from submission.pps.products import document_reading_instruction, non_task_scope_role, scope_spans
from submission.pps.retrieval import Span
from tests.test_catalog_scope_contract import DATA, setup, response
from tests.test_notice_search import record


POINTERS = [
    '마. 사양설명 : 게시 용도설명서 및 구매사양서 열람 및 구매요구자에게 문의',
    '사업내용: 게시된 구매사양서 열람 및 수요기관에 문의한다.',
    '구매 규격서는 게시된 규격서를 열람하여 확인한다.',
    '과업내용: 별첨 제안요청서 및 과업지시서 열람',
    '용역내용: 「용도설명서」 및\n「구매사양서」 확인하고 구매요구자에게 문의',
    '사업내용: 첨부 제안요청서 참고 및 담당자에게 문의',
]


@pytest.mark.parametrize('text', POINTERS)
def test_only_reading_and_contact_is_not_an_actual_purchase_anchor(text):
    assert document_reading_instruction(text)
    assert non_task_scope_role(text) == 'procurement_document_reading_instruction'
    rec = record(text)
    assert whole_task_witnesses(rec, [Span(0, '공고문', 0, len(text), text)], [1]) == []


@pytest.mark.parametrize('text', [
    '사업내용: 구매사양서 작성 및 기술검토 용역',
    '과업내용: 문서 열람 서비스를 개발하고 이용자 문의를 응대한다.',
    '용역명: 계약용 제안요청서 및 규격서 작성',
    '과업내용: 해양환경 영향조사 및 평가를 수행한다. 세부 내용은 과업지시서 참조',
    '사업내용: 게시된 규격서를 열람하여 장비를 설계하고 제작한다.',
    '과업내용: 용도설명서 확인 후 분석시스템을 구축한다.',
    '사업내용: 현황 자료를 열람하고 인허가 기관에 문의하여 설계도서를 작성한다.',
])
def test_actual_deliverables_survive_document_and_reading_words(text):
    assert not document_reading_instruction(text)
    rec = record(text)
    assert whole_task_witnesses(rec, [Span(0, '공고문', 0, len(text), text)], [1])


@pytest.mark.parametrize('text', POINTERS[:5])
def test_model_claim_cannot_promote_a_document_direction_but_a_real_anchor_still_works(text):
    rec, packet, obj, _ = setup()
    original = rec['docs'][0]['text']
    rec['docs'][0]['text'] = text + '\n' + original
    end = original.index('\n')
    packet['spans'] = [Span(0, '공고문', 0, len(text), text),
        Span(0, '공고문', len(text) + 1, len(text) + 1 + end, original[:end])]
    before = copy.deepcopy((rec, packet, obj))
    pipeline = B4Pipeline(DATA, None)
    assert parse_error(packet, response(obj)) is None
    row, log = pipeline.consume(rec, packet, response(obj))
    assert row is None and log['gate'] == 'no_original_whole_task_anchor'
    assert (rec, packet, obj) == before
    obj['whole_task_units'] = [2]
    row, log = pipeline.consume(rec, packet, response(obj))
    assert row['v14'] == 1 and log['source_scope_promoted']


def test_discovery_preserves_the_original_reading_instruction():
    text = POINTERS[0]
    rec = record(text)
    assert any(s['text'] == text for s in scope_spans(rec))
    assert rec['docs'][0]['text'] == text


def test_other_task_in_the_same_selected_line_is_not_erased():
    text = POINTERS[0] + '. 별도의 환경영향조사 용역을 수행한다.'
    assert not document_reading_instruction(text)


def test_long_nonmatching_list_is_bounded_by_visible_document_tokens():
    assert not document_reading_instruction('제안요청서및' * 1000 + '정보시스템구축')


def test_wrapped_real_task_requires_the_complete_continuation_in_model_input():
    text = '사업내용: 제안요청서 및\n구매사양서 작성'
    rec = record(text)
    cut = text.index('\n')
    first = Span(0, '공고문', 0, cut, text[:cut])
    assert whole_task_witnesses(rec, [first], [1]) == []
    second = Span(0, '공고문', cut, len(text), text[cut:])
    witnesses = whole_task_witnesses(rec, [first, second], [1, 2])
    assert witnesses[0]['text'] == text
    assert rec['docs'][0]['text'][witnesses[0]['start']:witnesses[0]['end']] == text


@pytest.mark.parametrize('suffix', ['', '\n\n구매사양서 작성', '\n입찰방법: 협상', '\n2. 계약조건'])
def test_connector_cannot_create_a_complete_field_from_missing_or_independent_text(suffix):
    text = '사업내용: 제안요청서 및' + suffix
    rec = record(text)
    assert whole_task_witnesses(rec, [Span(0, '공고문', 0, len(text), text)], [1]) == []
