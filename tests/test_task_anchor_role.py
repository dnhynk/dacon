"""Discovery relevance is insufficient to authenticate a whole-task witness."""
import copy

import pytest

from submission.b4_entry import B4Pipeline, parse_error
from submission.pps.catalog_scope import whole_task_witnesses
from submission.pps.products import scope_spans
from submission.pps.retrieval import Span
from tests.test_catalog_scope_contract import DATA, response, setup
from tests.test_notice_search import record


NON_TASKS = (
    '공공구매정보망에 등록하여 소기업 확인서를 소지한 업체로 유효기간 내에 있어야 합니다.',
    '확인서는 마감일 전일까지 발급되어 유효기간 내에 있고 공공구매정보망에서 확인되어야 한다.',
    '사업예산: 금480,000,000원(부가가치세 및 대행수수료 포함)',
    '예정금액: 360,000,000원(구축비 및 운영비 포함)',
    '재해예방에 필요한 인력·예산·점검 등 안전보건관리체계의 구축 및 그 이행에 관한 조치',
    '안전·보건 관계 법령에 따른 의무이행에 필요한 관리체계 구축에 관한 조치',
    '충분한 장비와 인력 및 기술을 보유하여 기획, 제작, 시공, 운영 등 종합적인 업무가 가능한 업체',
    '조달물자(용역) 구매입찰 공고',
    '용역 전자입찰 공고(대행)',
)


def witnesses(text):
    return whole_task_witnesses(record(text), [Span(0, '공고문', 0, len(text), text)], [1])


@pytest.mark.parametrize('text', NON_TASKS)
def test_an_original_complete_but_administrative_sentence_is_not_a_task(text):
    assert witnesses(text) == []


@pytest.mark.parametrize('text', (
    '사업명: 안전보건관리체계 구축 및 현장 재해예방 컨설팅',
    '사업명: 중소기업 확인서 발급지원 시스템 개발',
    '과업내용: 신청서류를 검증하고 전담 상담센터를 운영한다.',
    '사업명: 사업예산 편성자료 분석 및 교육운영지원',
    'ㅇ 여신심사 시 요구되는 신용분석 현장실태조사에 대한 업무 위탁',
    'ㅇ 신청자 자격 검증을 위한 서류 심사 업무를 수행하고 민원을 응대하는 전문업체를 선정·운영',
    '상수도 시설 운영관리 위탁 용역 입찰공고',
    '본 과업은 신청서와 확인서를 검증하고 발급지원 시스템을 운영하는 용역이다.',
    '본 과업의 목적은 안전보건관리체계 구축 및 재해예방 컨설팅이다.',
))
def test_same_topic_words_can_describe_the_actual_purchased_work(text):
    assert witnesses(text)[0]['text'] == text


@pytest.mark.parametrize('text', NON_TASKS)
def test_format_valid_model_claim_cannot_promote_an_administrative_anchor(text):
    rec, packet, obj, _ = setup()
    # Preserve a real task elsewhere, so rejection is about the selected role.
    original = rec['docs'][0]['text']
    rec['docs'][0]['text'] = text + '\n' + original
    end = original.index('\n')
    packet['spans'] = [Span(0, '공고문', 0, len(text), text),
        Span(0, '공고문', len(text)+1, len(text)+1+end, original[:end])]
    snapshot = copy.deepcopy((rec, packet, obj))
    pipeline = B4Pipeline(DATA, None)
    assert parse_error(packet, response(obj)) is None
    row, log = pipeline.consume(rec, packet, response(obj))
    assert row is None and log['gate'] == 'no_original_whole_task_anchor'
    assert not log['source_scope_promoted']
    assert (rec, packet, obj) == snapshot
    # Reading the independent operative task permits the original decision.
    obj['whole_task_units'] = [2]
    row, log = pipeline.consume(rec, packet, response(obj))
    assert row['v14'] == 1 and log['source_scope_promoted']


def test_discovery_still_retains_relevant_budget_context():
    text = NON_TASKS[2]
    assert any(s['text'] == text for s in scope_spans(record(text)))
    assert witnesses(text) == []
