"""Offline-mined grammar keeps document roles, object scope and prompt facts."""
import copy

import pytest

from submission.pps.performance import performance_facts
from submission.pps.qualification import inventory, qualification_facts
from submission.pps.sme import extract_inventory


def notice(body):
    return {'id': 'grammar-test', 'meta': {
        '적용계약법': '국가계약법', '업무구분': '일반용역', '계약방법': '제한경쟁',
        '입찰추정가격': 80_000_000, '배정예산금액': 88_000_000},
        'docs': [{'doc_id': 'D0', 'type': '공고문', 'text': body}],
        'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


@pytest.mark.parametrize('verb', ['수행한', '이행했던', '납품해본', '완료한'])
def test_completed_experience_is_a_bidder_requirement(verb):
    rec = notice('2. 입찰참가자격\n가. 자료 정비 용역을\n\n'
                 + verb + ' 경험이 있는 업체\n3. 제출서류')
    original = copy.deepcopy(rec)
    before = performance_facts(rec)
    facts = performance_facts(rec, consumer=True)
    assert facts['overlays']['v2']['value'] == 1
    witness, = facts['candidates']
    assert witness['evidence']['text'] == rec['docs'][0]['text'][
        witness['evidence']['start']:witness['evidence']['end']]
    assert performance_facts(rec) == before
    assert rec == original


@pytest.mark.parametrize('heading', ['3. 평가기준', '3. 제출서류'])
def test_completed_experience_keeps_scoring_and_form_roles(heading):
    facts = performance_facts(notice('2. 입찰참가자격\n가. 일반 등록 업체\n'
        + heading + '\n가. 수행한 경험이 있는 업체'), consumer=True)
    assert facts['overlays']['v2']['value'] is None


@pytest.mark.parametrize('body', [
    '수행한 경험이 없어도 입찰할 수 있습니다.',
    '예시: 수행한 경험이 있는 업체',
    '참고용: 수행한 경험이 있는 업체',
    '완료한 경험을 가진 책임기술자를 배치하여야 한다.',
])
def test_nonasserted_or_staff_experience_is_not_a_bidder_gate(body):
    facts = performance_facts(notice('2. 입찰참가자격\n'+body), consumer=True)
    assert facts['overlays']['v2']['value'] != 1


@pytest.mark.parametrize('caption', [
    '◐ 입 찰 참 가 자 격', '□ 응찰자격', '4. 제안(입찰참가) 자격',
    '3. 입찰 참가자격 : 아래의 자격을 모두 충족한 자이어야 합니다.',
])
def test_complete_eligibility_captions_reopen_a_previous_forms_role(caption):
    rec = notice('1. 제출서류\n사본 각 1부\n'+caption+'\n'
        '가. 소기업·소상공인확인서를 소지한 업체\n'
        '나. 자료 정비 용역 실적이 있는 업체\n5. 계약조건')
    packet_inventory = extract_inventory(rec)
    packet_performance = performance_facts(rec)
    qf = qualification_facts(rec, inventory(rec))
    assert qf['active_size']
    assert performance_facts(rec, consumer=True)['overlays']['v2']['value'] == 1
    assert extract_inventory(rec) == packet_inventory
    assert performance_facts(rec) == packet_performance


@pytest.mark.parametrize('caption', [
    '응찰자격을 갖춘 업체에 제안서 작성 방법을 안내합니다.',
    '입찰참가자격 등록규정에 따라 등록한 업체',
    '입찰참가자격 제한에 관한 안내',
    '제안(입찰참가) 자격 평가자료 작성 요령',
])
def test_eligibility_mentions_cannot_reopen_a_forms_section(caption):
    rec = notice('1. 제출서류\n'+caption+'\n소기업확인서를 소지한 업체')
    assert not qualification_facts(rec, inventory(rec))['active_size']


@pytest.mark.parametrize('body', [
    '납품실적이 없는 업체의 입찰등록도 허용합니다.',
    '수행실적은 제안서 평가에서 동일하게 인정하며, 입찰참가자격 요건으로는 삼지 않습니다.',
])
def test_explicit_admission_without_experience_is_not_mandatory(body):
    facts = performance_facts(notice('2. 입찰참가자격\n'+body), consumer=True)
    assert facts['explicit_no_experience_restriction']
    assert not any(c['status'] == 'mandatory' for c in facts['candidates'])


def test_document_waiver_does_not_waive_the_experience_it_certifies():
    facts = performance_facts(notice('2. 입찰참가자격\n'
        '실적증명서가 없는 업체의 입찰등록도 허용합니다.\n'
        '가. 단일 계약 1억원 이상 수행 실적이 있는 업체'), consumer=True)
    assert facts['overlays']['v2']['value'] == 1


@pytest.mark.parametrize('body', [
    '실적이 없는 업체의 입찰등록도 허용하지 않습니다.',
    '실적을 참가자격 요건으로는 삼지 않는 것은 아닙니다.',
    '예시: 실적이 없는 업체의 입찰등록도 허용합니다.',
])
def test_nonpermission_cannot_cancel_a_separate_positive_gate(body):
    facts = performance_facts(notice('2. 입찰참가자격\n'+body+'\n'
        '가. 단일 계약 1억원 이상 수행 실적이 있는 업체'), consumer=True)
    assert facts['overlays']['v2']['value'] == 1


def test_unrestricted_size_with_nonholder_admission_is_not_a_size_gate():
    rec = notice('2. 입찰참가자격\n기업규모 제한이 없고, '
        '소기업확인서를 갖추지 않은 업체도 참가할 수 있다.')
    assert not qualification_facts(rec, inventory(rec))['active_size']


def test_size_document_waiver_does_not_waive_a_separate_entity_gate():
    rec = notice('2. 입찰참가자격\n소기업확인서는 제출하지 않아도 됩니다.\n'
        '가. 소기업확인서를 소지한 업체')
    assert qualification_facts(rec, inventory(rec))['active_size']
