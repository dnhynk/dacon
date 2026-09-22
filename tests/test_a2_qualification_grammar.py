"""Consumer grammar distinguishes eligibility from paperwork and examples."""
import pytest

from submission.pps.qualification import inventory, qualification_facts
from tests.test_qualification_heading_roles import notice


@pytest.mark.parametrize('heading', [
    '4. 입찰참가자격 및 관련사항',
    '4. 입찰참가자격 및 제출서류',
    '4. 입찰참가 자격(모두 요함)',
])
def test_qualification_heading_qualifier_keeps_observed_scope(heading):
    record = notice(heading, '가. 사업자등록을 마친 업체이어야 합니다.')
    facts = qualification_facts(record, inventory(record))
    assert facts['closed_eligibility']
    assert facts['no_size'] and facts['no_direct']


@pytest.mark.parametrize('heading', [
    '4. 입찰참가자격 및 관련사항 (작성 예시)',
    '4. 입찰참가자격 및 제출서류 적용하지 않음',
    '4. 입찰참가자격(모두 요함) 철회',
])
def test_heading_qualifier_does_not_open_nonoperative_section(heading):
    facts = qualification_facts(notice(heading), inventory(notice(heading)))
    assert not facts['active_size']
    assert not facts['no_size']


@pytest.mark.parametrize('clause', [
    '가. 소기업·소상공인 여부를 요구하지 않고 기업규모와 관계없이 신청을 받습니다.',
    '가. 중소기업확인서 보유나 기업규모를 추가 요건으로 정하지 않습니다.',
    '가. 기업의 규모는 참가자격 판단에서 제외하며 소기업 확인서는 요구하지 않습니다.',
    '가. 중소기업 여부와 기업규모는 입찰자격 심사에서 제외합니다.',
    '가. 참가범위를 기업규모로 한정하지 않으며 소기업 여부를 심사하지 않습니다.',
    '가. 기업규모에 따른 참가 제한을 두지 아니하므로 대기업도 참가할 수 있습니다.',
])
def test_explicit_size_noncondition_does_not_block_absence(clause):
    record = notice('4. 입찰참가자격', clause)
    facts = qualification_facts(record, inventory(record))
    assert facts['no_size']
    assert not facts['active_size']


@pytest.mark.parametrize('clause', [
    '가. 소기업인 업체만 참가할 수 있습니다.\n나. 소기업 확인서의 제출은 면제합니다.',
    '가. 기업규모와 관계없이 신청을 받습니다. 다만 소기업 확인서를 보유한 업체에 한합니다.',
    '가. 예시: 중소기업 여부와 기업규모에 따른 참가 제한을 두지 아니합니다.',
])
def test_certificate_waiver_restriction_and_example_are_not_unrestricted(clause):
    record = notice('4. 입찰참가자격', clause)
    facts = qualification_facts(record, inventory(record))
    assert not facts['no_size']


def test_another_size_condition_still_blocks_absence():
    record = notice('4. 입찰참가자격',
        '가. 기업규모와 관계없이 중소기업 확인서 없이 신청을 받습니다.\n'
        '나. 소기업확인서를 소지한 업체이어야 합니다.')
    facts = qualification_facts(record, inventory(record))
    assert not facts['no_size']
    assert facts['allowed'] == ['micro', 'small']


def test_mixed_heading_does_not_make_document_list_a_holding_requirement():
    record = notice('4. 입찰참가자격 및 제출서류',
        '가. 사업자등록을 마친 업체\n제출서류\n소기업확인서 1부\n직접생산확인증명서 1부')
    facts = qualification_facts(record, inventory(record))
    assert not facts['active_size'] and not facts['active_direct']


def test_unclosed_or_missing_documents_still_block_absence():
    record = notice('4. 입찰참가자격 및 관련사항', '가. 등록한 업체')
    record['docs'][0]['text'] = record['docs'][0]['text'].split('5. 계약조건')[0]
    assert not qualification_facts(record, inventory(record))['no_size']
    record = notice('4. 입찰참가자격(모두 요함)', '가. 등록한 업체')
    record['input_completeness']['완전관측'] = False
    record['dropped_doc_counts'] = {'공고문': 1}
    assert not qualification_facts(record, inventory(record))['no_size']


@pytest.mark.parametrize('clause', [
    '가. 직접생산증명서를 발급받은 업체',
    '가. 직접생산확인증명서(시험제품)를 발급받아 유효기간 내에 있어야 합니다.',
    '가. 직접생산확인증명서가 없는 업체는 제안서 접수대상에서 제외합니다.',
])
def test_certificate_issuance_or_nonholder_exclusion_is_a_prerequisite(clause):
    record = notice('4. 입찰참가자격', clause)
    facts = qualification_facts(record, inventory(record))
    assert facts['active_direct']
    assert not facts['no_direct']
    assert facts['direct_certificate_coverage']['observations'][0]['production_required_in_every_branch']
    assert not facts['direct_certificate_coverage']['guaranteed_codes']


@pytest.mark.parametrize('clause', [
    '가. 낙찰후 직접생산증명서를 발급받은 업체가 자료를 제출합니다.',
    '가. 예시: 직접생산증명서를 발급받은 업체',
    '가. 직접생산증명서 또는 일반인증서를 발급받은 업체',
    '가. 직접생산증명서 안내를 참고하고 별도의 인증서를 발급받은 업체',
])
def test_certificate_issuance_cannot_borrow_a_later_stage_or_other_object(clause):
    record = notice('4. 입찰참가자격', clause)
    facts = qualification_facts(record, inventory(record))
    assert not facts['active_direct']
