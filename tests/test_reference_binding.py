"""Reference availability, obligation and source-field ownership stay separate."""
import copy

import pytest

from submission.pps.reference_coverage import assess
from tests.test_comparison import record
from tests.test_reference_coverage import TEXT, facts


@pytest.mark.parametrize('statement', [
    '입찰참가자격 세부사항은 다음 문서에 따름(제안요청서는 첨부하지 않음).',
    '입찰참가자격은 제안요청서에 따르며 제안요청서는 첨부하지 않는다.',
    '입찰참가자격은 제안요청서에 따르며 제안요청서는 제공하지 않는다.',
    '제출서류의 상세 요건은 다음 문서를 확인한다(제안요청서는 작성하지 않음).',
])
def test_document_omission_does_not_waive_incorporated_qualifications(statement):
    rec = record(TEXT + statement)
    original = copy.deepcopy(rec)
    result = facts(rec)
    assert result['complete'] and result['closed_eligibility']
    assert not result['no_size'] and not result['no_direct']
    assert not result['reference_coverage']['qualification_deferrals_resolved']
    assert rec == original


@pytest.mark.parametrize('statement', [
    '입찰참가자격은\n제안요청서에서 정한 사항에 따른다.',
    '제출서류:\n과업지시서 및\n제안요청서 참조',
    '제출서류:\n과업지시서\n및 제안요청서\n참조',
    '2. 구비서류는\n\n「과업지시서」 및\n\n「제안요청서」 참조',
    '입찰참가자격:\n제 안 요 청 서 참조',
])
def test_wrapped_qualification_reference_retains_its_original_field(statement):
    rec = record(TEXT + statement)
    coverage = assess(rec)
    assert not coverage['qualification_deferrals_resolved']
    assert all(r['qualification_deferral'] for r in coverage['references'])
    for ref in coverage['references']:
        ev = ref['evidence']
        assert rec['docs'][ev['doc_index']]['text'][ev['start']:ev['end']] == ev['text']
    if '과업지시서' in statement:
        assert {r['referenced_role'] for r in coverage['references']} == {'과업지시서', '제안요청서'}


@pytest.mark.parametrize('field', ['납품규격', '설치방법', '사업내용', '과업내용'])
def test_independent_technical_field_cannot_borrow_submission_heading(field):
    rec = record(TEXT + '제출서류:\n' + field + ': 과업지시서 참조')
    coverage = assess(rec)
    assert coverage['referenced_unavailable_docs']
    assert coverage['qualification_deferrals_resolved']
    assert facts(rec)['no_size'] and facts(rec)['no_direct']
    assert all('제출서류' not in r['evidence']['text'] for r in coverage['references'])


@pytest.mark.parametrize('rule', [
    '국가종합전자조달시스템 입찰참가자격등록규정',
    '국가종합전자조달시스템 입찰참가자격 등록 규정',
    '입찰참가자격등록마감일시',
])
def test_registration_rule_or_date_is_not_a_deferral_of_qualifications(rule):
    coverage = assess(record(TEXT + f'{rule}에 따라 등록한 자로서 과업지시서에 따라 과업 수행이 가능한 업체.'))
    assert coverage['references']
    assert coverage['qualification_deferrals_resolved']


def test_genuine_reference_is_retained_alongside_registration_rule():
    coverage = assess(record(TEXT + '입찰참가자격등록규정에 따라 등록하고, '
        '제안요청서의 입찰참가자격 요건을 모두 갖춘 업체.'))
    assert not coverage['qualification_deferrals_resolved']


@pytest.mark.parametrize('ending', ['참조하지 않는다', '참조하지 않습니다',
    '참조하지 않으며 본 공고문만 따른다', '참고하지 아니한다', '따르지 않는다'])
def test_explicit_nonreference_accepts_the_document_object_particle(ending):
    coverage = assess(record(TEXT + f'입찰참가자격은 제안요청서를 {ending}.'))
    assert coverage['qualification_deferrals_resolved']
    assert not coverage['referenced_unavailable_docs']


@pytest.mark.parametrize('ending', ['참조하지 않는다는 뜻은 아니다',
    '참조하지 않아도 된다는 주장은 인정하지 않는다', '참조하지 않을 수 없다'])
def test_double_negation_is_not_repaired_into_a_nonreference(ending):
    assert not assess(record(TEXT + f'입찰참가자격은 제안요청서를 {ending}.'))['qualification_deferrals_resolved']


def test_each_document_reference_keeps_its_own_negation():
    coverage = assess(record(TEXT + '입찰참가자격은 제안요청서를 참조하지 않으며 과업지시서를 따른다.'))
    assert {r['referenced_role'] for r in coverage['references']} == {'과업지시서'}
    assert not coverage['qualification_deferrals_resolved']


def test_repeated_document_reference_does_not_erase_its_earlier_obligation():
    coverage = assess(record(TEXT + '입찰참가자격은 제안요청서에 따르며 '
        '제안요청서를 참조하지 않는다는 별도 의견은 채택하지 않는다.'))
    assert not coverage['qualification_deferrals_resolved']
    assert coverage['references'][0]['qualification_deferral']


def test_missing_second_member_of_wrapped_list_is_not_hidden_by_first_role():
    rec = record(TEXT + '제출서류:\n제안요청서 및\n과업지시서 참조', '제안요청서 본문')
    coverage = assess(rec)
    assert not coverage['qualification_deferrals_resolved']
    assert {r['referenced_role'] for r in coverage['referenced_unavailable_docs']} == {'과업지시서'}


def test_generic_nonattachment_does_not_invent_a_qualification_reference():
    assert not assess(record(TEXT + '제안요청서를 첨부하지 않는다.'))['references']


@pytest.mark.parametrize('tail', ['첨부하지 않아서는 안 된다',
    '첨부하지 않는다는 뜻이 아니다', '첨부하지 않을 수 없다'])
def test_double_negation_does_not_erase_a_generic_reference(tail):
    coverage = assess(record(TEXT + f'납품규격 확인에 사용할 제안요청서를 {tail}.'))
    assert coverage['references'] and coverage['qualification_deferrals_resolved']


def test_unavailable_reference_blocks_new_absence_positive_in_actual_consumer():
    from submission.pps.knowledge import Knowledge
    from tests.test_independent_audit import DATA, synthetic_notice
    knowledge = Knowledge(DATA)
    rec = synthetic_notice(TEXT + '입찰참가자격:\n과업지시서 및\n제안요청서 참조',
        업무구분='물품(내자)', 세부품명번호목록='서적[5510151001]', 입찰추정가격=150_000_000)
    row, details = knowledge.qualification_decisions(rec, {'v16': '0', 'e16': ''})
    assert details['product']['status'] == 'general'
    assert not details['qualification']['no_size']
    assert row['v16'] == '0' and row['e16'] == '' and 'v16' not in details['decisions']
