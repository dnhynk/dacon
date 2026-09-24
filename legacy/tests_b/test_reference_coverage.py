"""Absence checks do not turn registration or an unread reference into text."""
import copy

import pytest

from submission.pps.qualification import inventory, qualification_facts
from submission.pps.reference_coverage import assess
from tests.test_comparison import record


TEXT = '1. 입찰참가자격\n가. 해당 업종으로 등록한 업체이어야 한다.\n2. 입찰 일정\n'


def facts(rec):
    return qualification_facts(rec, inventory(rec))


def test_registration_classification_is_retained_but_not_a_body_requirement():
    rec = record(TEXT, 조항호내용='[판로지원법 시행령] 중기업,소기업,소상공인 제한')
    before = copy.deepcopy(rec)
    f = facts(rec)
    assert f['no_size'] and f['allowed'] is None
    assert f['meta_size_restriction'] == ['medium', 'micro', 'small']
    assert rec == before


@pytest.mark.parametrize('line', [
    '제출서류: 별첨 제안요청서 참조',
    '입찰참가자격은 첨부 과업지시서에 정한 바에 따른다.',
    '구비서류는 별도 규격서를 확인한다.',
    '제출서류:\n제안요청서 참조',
])
def test_explicit_unavailable_qualification_reference_blocks_absence(line):
    rec = record(TEXT + line)
    f = facts(rec)
    assert f['complete'] and f['closed_eligibility']
    assert not f['no_size'] and not f['no_direct']
    coverage = f['reference_coverage']
    assert len(coverage['actual_provided_docs']) == 1
    assert not coverage['qualification_deferrals_resolved']
    e = coverage['referenced_unavailable_docs'][0]['evidence']
    assert rec['docs'][e['doc_index']]['text'][e['start']:e['end']] == e['text']


def test_supplied_reference_is_scanned_for_the_actual_condition():
    rec = record(TEXT+'제출서류: 제안요청서 참조',
                 '1. 입찰참가자격\n가. 중소기업 확인서를 보유한 업체이어야 한다.\n2. 계약조건')
    f = facts(rec)
    assert f['reference_coverage']['qualification_deferrals_resolved']
    assert f['allowed'] == ['medium', 'micro', 'small']
    assert not f['no_size']


def test_a_generic_technical_reference_is_reported_without_inventing_qualifications():
    rec = record(TEXT+'납품 규격은 첨부 규격서를 참조한다.')
    c = assess(rec)
    assert c['qualification_deferrals_resolved']
    assert c['referenced_unavailable_docs'] and not c['reference_contents_inferred']
    assert not c['all_referenced_document_roles_present']


def test_explicit_nonexistent_reference_is_not_an_unread_attachment():
    assert not assess(record(TEXT+'제안요청서는 첨부하지 않는다.'))['referenced_unavailable_docs']


def test_actual_procedure_restriction_still_prevents_a_false_absence():
    f = facts(record('입찰방법: 제한경쟁(소기업·소상공인)\n'+TEXT))
    assert f['allowed'] == ['micro', 'small'] and not f['no_size']


def test_postaward_sanction_does_not_turn_a_technical_reference_into_eligibility():
    rec = record(TEXT+'하도급은 과업지시서 또는 제안요청서에서 정한 승인절차를 준수하여야 한다. '
                 '무단 하도급은 계약해지 및 입찰참가자격제한을 받을 수 있다.', '1. 과업내용\n자료 조사 및 보고')
    c = assess(rec)
    assert c['referenced_unavailable_docs']
    assert c['qualification_deferrals_resolved']
    assert facts(rec)['no_size']


def test_form_pointer_before_later_exhaustive_notice_eligibility_does_not_hide_size_predicate():
    rec = record(
        '입찰참가 등록서류 : 제안요청서 참조\n'
        '제출서류 : 과업지시서 및 제안요청서 참조\n'
        '4. 입찰참가자격\n'
        '업체는 다음 각 호의 자격을 모두 갖추어야 합니다.\n'
        '가. 해당 업종으로 등록한 업체\n'
        '2. 계약조건')
    result = facts(rec)
    raw = result['reference_coverage']
    scoped = raw['eligibility_absence']
    assert not raw['qualification_deferrals_resolved']
    assert scoped['size_and_direct_predicates_resolved']
    assert len(scoped['form_references_scoped_by_later_exhaustive_notice_eligibility']) == 3
    assert result['no_size'] and result['no_direct']


@pytest.mark.parametrize('statement', [
    '입찰참가자격은 제안요청서에 따른다.',
    '입찰참가 등록서류 : 제안요청서 참조',
])
def test_exhaustive_clause_only_scopes_earlier_pure_form_fields(statement):
    if statement.startswith('입찰참가 등록'):
        text = ('4. 입찰참가자격\n업체는 다음 각 호의 자격을 모두 갖추어야 합니다.\n'
                '가. 해당 업종으로 등록한 업체\n2. 계약조건\n' + statement)
    else:
        text = (statement + '\n4. 입찰참가자격\n'
                '업체는 다음 각 호의 자격을 모두 갖추어야 합니다.\n'
                '가. 해당 업종으로 등록한 업체\n2. 계약조건')
    result = facts(record(text))
    assert not result['reference_coverage']['eligibility_absence']['size_and_direct_predicates_resolved']
    assert not result['no_size'] and not result['no_direct']


def test_generic_form_pointer_after_exhaustive_clause_is_scoped_but_bid_registration_is_not():
    prefix = ('4. 입찰참가자격\n'
              '다음 각 호의 자격을 모두 갖춘 업체이어야 합니다.\n'
              '가. 해당 업종으로 등록한 업체\n'
              '5. 제출 안내\n')
    generic = facts(record(prefix+'제출서류 : 제안요청서 참조'))
    scoped = generic['reference_coverage']['eligibility_absence']
    assert scoped['size_and_direct_predicates_resolved']
    assert len(scoped['form_references_scoped_by_later_exhaustive_notice_eligibility']) == 1
    assert generic['no_size'] and generic['no_direct']

    registration = facts(record(prefix+'입찰참가 등록서류 : 제안요청서 참조'))
    scoped = registration['reference_coverage']['eligibility_absence']
    assert not scoped['size_and_direct_predicates_resolved']
    assert not registration['no_size'] and not registration['no_direct']


@pytest.mark.parametrize('pointer', [
    '제안서 제출서류: 제안요청서 입찰 참가 서류 목록 및 양식 참조',
    '제출서류: 기술제안서, 발표자료, 기타자료 등(제안요청서 참고)',
    '제안서 제출 방법, 구비서류 서식 및 작성요령은 제안요청서를 참고하시기 바랍니다.',
])
def test_form_list_wording_after_closed_eligibility_does_not_delegate_new_predicates(pointer):
    prefix = ('4. 입찰참가자격\n'
              '다음 각 호의 자격을 모두 갖춘 업체이어야 합니다.\n'
              '가. 해당 업종으로 등록한 업체\n'
              '5. 제안서 제출 안내\n')
    result = facts(record(prefix + pointer))
    scoped = result['reference_coverage']['eligibility_absence']
    assert scoped['size_and_direct_predicates_resolved']
    assert result['no_size'] and result['no_direct']


def test_below_qualification_governor_closes_later_form_list_references():
    text = ('3. 입찰 참가자격\n'
            '관계 법령상 유자격 사업자로 아래의 자격을 갖춘 자이어야 합니다.\n'
            '- 학술연구용역으로 등록한 자\n'
            '4. 제출서류\n'
            '제안서 제출서류: 제안요청서 입찰 참가 서류 목록 및 양식 참조')
    result = facts(record(text))
    scoped = result['reference_coverage']['eligibility_absence']
    assert scoped['size_and_direct_predicates_resolved']
    assert result['no_size'] and result['no_direct']


def test_optional_size_confirmation_document_does_not_become_a_bidder_restriction():
    text = ('3. 입찰 참가자격\n'
            '사업자로 아래의 자격을 갖춘 자이어야 합니다.\n'
            '- 학술연구용역으로 등록한 자\n'
            '4. 제출서류\n'
            '6) 중소기업 및 소상공인 확인 서류 1부(해당시)')
    result = facts(record(text))
    assert result['no_size']
    assert len(result['optional_size_documents']) == 1
    assert result['optional_size_documents'][0]['evidence']['text'].endswith('(해당시)')


def test_dropped_form_attachment_does_not_erase_closed_notice_eligibility_coverage():
    rec = record('4. 입찰참가자격\n'
                 '다음 각 호의 자격을 모두 갖춘 업체이어야 합니다.\n'
                 '가. 해당 업종으로 등록한 업체\n'
                 '5. 제출서류\n기타 양식은 제안요청서 제출서식 참조')
    rec['input_completeness'] = {
        '공고문_실재': True, '추출_성공': True, '무탈락': False, '완전관측': False}
    rec['dropped_doc_counts'] = {'제안요청서': 1}
    result = facts(rec)
    scoped = result['reference_coverage']['eligibility_absence']
    assert not result['complete'] and scoped['eligibility_source_complete']
    assert scoped['dropped_roles_scoped_outside_qualification'] == ['제안요청서']
    assert result['no_size'] and result['no_direct']


def test_unrepresented_dropped_attachment_cannot_certify_domain_absence():
    rec = record(TEXT)
    rec['input_completeness'] = {
        '공고문_실재': True, '추출_성공': True, '무탈락': False, '완전관측': False}
    rec['dropped_doc_counts'] = {'제안요청서': 1}
    result = facts(rec)
    assert not result['reference_coverage']['eligibility_absence']['eligibility_source_complete']
    assert not result['no_size'] and not result['no_direct']
