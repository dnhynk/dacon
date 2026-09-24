"""A submission obligation must have a current, correctly enclosing governor."""
import pytest

from submission.pps.pledge_reference import pledge_check
from tests.test_pledge_modality import notice


@pytest.mark.parametrize('governor', [
    '가. 낙찰자는 낙찰일로부터 10일 이내에 다음의 서류를 제출하여 계약을 체결하여야 합니다.',
    '가. 낙찰자는 다음의 서류를 제출하여 계약을 체결하여야 합니다.',
])
def test_awarded_party_contract_submission_has_later_stage(governor):
    record = notice('1. 계약체결\n' + governor + '\n- 제조사의 기술지원확약서')
    result = pledge_check(record)
    assert result['value'] == 0
    link = result['facts']['pledges'][0]['structural_links'][0]
    assert link['governor']['quote'] == governor


@pytest.mark.parametrize('statement', [
    '기존 입찰 참가 제출 서류 양식은 참고자료이다.',
    '이전 공고의 입찰 참가 제출 서류 목록은 효력이 없다.',
])
def test_reference_to_an_inoperative_list_is_not_a_governor(statement):
    result = pledge_check(notice('1. 검토 자료\n' + statement + '\n- 제조사의 기술지원확약서'))
    assert result['value'] is None
    assert result['facts']['structure']['governors'] == []


def test_decimal_child_stays_within_its_actual_submission_parent():
    text = ('7.2 계약상대자는 납품 전 아래의 서류를 제출한다.\n'
            '7.2.1 제조사의 기술지원확약서\n7.3 기타 조건')
    result = pledge_check(notice(text))
    assert result['value'] == 0
    governor = result['facts']['structure']['governors'][0]
    assert governor['end'] == text.index('7.3')


def test_decimal_sibling_is_outside_the_submission_parent():
    text = ('7.2 계약상대자는 납품 전 아래의 서류를 제출한다.\n'
            '7.2.1 사업자등록증\n7.3 제조사의 기술지원확약서')
    assert pledge_check(notice(text))['value'] is None


def test_contract_purpose_does_not_replace_explicit_pre_bid_deadline():
    text = ('1. 입찰참가자격\n낙찰예정자는 계약 체결에 필요한 제조사의 '
            '기술지원확약서를 입찰 전에 제출하여야 한다.')
    assert pledge_check(notice(text))['value'] == 1


def test_early_possession_survives_the_later_contract_submission_list():
    text = ('1. 계약체결\n낙찰자는 다음의 서류를 제출하여 계약을 체결하여야 합니다.\n'
            '- 제조사의 기술지원확약서: 입찰 전까지 보유하여야 한다.')
    assert pledge_check(notice(text))['value'] == 1


def test_operative_current_bid_list_remains_positive():
    text = '1. 입찰 참가 제출 서류\n- 제조사의 기술지원확약서\n2. 계약조건'
    assert pledge_check(notice(text))['value'] == 1


def test_bid_related_documents_heading_binds_its_pledge_member():
    text = ('다. 제출서류\n1) 입찰관련서류\n1-1) 사업자등록증 1부\n'
            '1-4) 제조회사 공급 증명원 및 A/S 확약서 각 1부\n'
            '2) 제안서\n2-1) 규격 제안서 1부')
    result = pledge_check(notice(text))
    assert result['value'] == 1
    pledge = result['facts']['pledges'][0]
    assert pledge['timing'] == 'explicit_pre_bid'
    assert pledge['structural_links'][0]['governor']['quote'] == '1) 입찰관련서류'


def test_relationship_with_manufacturer_does_not_prove_manufacturer_issued_pledge():
    text = ('3. 입찰 참가 제출 서류\n'
            '- 판매업자의 경우 제조(수입)사와의 공고물량 이상의 공급확약서 1부')
    result = pledge_check(notice(text))
    assert result['value'] is None
    assert result['facts']['pledges'][0]['issuer_relation_only'] is True


def test_explicit_manufacturer_issuance_remains_third_party_pledge():
    text = ('1. 입찰 참가 제출 서류\n'
            '- 제조사와의 계약에 따라 제조사가 발급한 물품공급확약서 1부')
    assert pledge_check(notice(text))['value'] == 1


def test_exact_attached_bidder_form_is_not_a_third_party_pledge():
    text = ('1. 입찰 참가 제출 서류\n- 물품 공급 및 기술지원 확약서[첨부3] 1부\n'
            '2. 계약조건\n[첨부 3]\n장비별 공급 및 기술지원 확약서\n'
            '입찰공고명:\n해당품목:\n당사는 발주기관에 물품 공급 및 기술지원을 원활히 제공하도록 한다.\n'
            '당사가 납품 완료해야 하는 날짜는 계약서의 납기일까지로 한다.\n'
            '업체명:\n사업자 번호:\n대표이사: (인)')
    result = pledge_check(notice(text))
    assert result['value'] == 0
    assert result['reason'] == 'all_pledges_explicitly_later_or_bidder_written'
    assert all(p['bidder_written'] for p in result['facts']['pledges'])
    assert all(p['form_authorship']['kind'] == 'referenced_bidder_authored_form'
               for p in result['facts']['pledges'])


@pytest.mark.parametrize('form', [
    # A first-person phrase without the closed bidder signature role is still
    # ambiguous: it could be a supplied third-party template.
    '당사는 기술지원을 제공하도록 한다.\n대표이사: (인)',
    # An explicitly external issuer remains a possible prohibited pledge.
    ('당사는 기술지원을 제공하도록 한다.\n업체명:\n사업자 번호:\n대표이사: (인)\n'
     '제조사명: 제조사 대표: (인)'),
])
def test_attached_form_without_closed_bidder_authorship_remains_unresolved(form):
    text = ('1. 입찰 참가 제출 서류\n- 물품 공급 및 기술지원 확약서[첨부3] 1부\n'
            '2. 계약조건\n[첨부 3]\n장비별 공급 및 기술지원 확약서\n' + form)
    assert pledge_check(notice(text))['value'] is None
