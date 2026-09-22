"""Residual qualification governor grammar, distinct from examples and forms."""
import pytest
from submission.pps.qualification import inventory, qualification_facts
from tests.test_qualification_heading_roles import notice, CLAUSE
from submission.pps.qualification import infer
from submission.pps.knowledge import Knowledge
from tests.test_service_identity import DATA


@pytest.mark.parametrize('header', [
    '입찰참가자격: 다음 사항의 자격을 모두 갖추어야 합니다.',
    '3. 입찰참가자격(해당 자격을 모두 갖춘 자만이 입찰 참가할 수 있습니다.)',
    '3. 견적참가자격(해당 조건을 모두 갖춘 업체만 견적 제출할 수 있습니다.)',
    '3. 입찰참가자격 : 아래의 모든 사항을 갖춘 자',
    '3. 입찰참가자격(아래의 조건을 동시에 충족하여야 하며, 공동계약(수급) 및 하도급 불허)',
])
def test_explicit_all_conditions_governor(header):
    rec = notice(header, CLAUSE)
    f = qualification_facts(rec, inventory(rec))
    assert f['allowed'] == ['micro', 'small']
    assert f['closed_eligibility']


@pytest.mark.parametrize('header', [
    '3. 입찰참가자격(해당 자격을 모두 갖춘 자만이 입찰 참가할 수 있습니다.) 예시',
    '3. 입찰참가자격: 다음 사항의 자격을 모두 갖추어야 합니다. (철회)',
    '3. 입찰참가자격: 다음 사항의 자격을 모두 갖추지 않아도 됩니다.',
    '3. 입찰참가자격: 다음 사항의 자격 중 하나를 갖추면 됩니다.',
    '3. 입찰참가자격(아래의 조건을 동시에 충족하여야 하며, 공동계약 허용)',
])
def test_example_withdrawal_and_alternative_do_not_prove_all_conditions(header):
    rec = notice(header, CLAUSE)
    f = qualification_facts(rec, inventory(rec))
    assert not f['allowed']


def test_new_header_does_not_turn_document_end_into_closed_absence():
    rec = notice('입찰참가자격: 다음 사항의 자격을 모두 갖추어야 합니다.', '등록 업체')
    rec['docs'][0]['text'] = rec['docs'][0]['text'].split('5. 계약조건')[0]
    f = qualification_facts(rec, inventory(rec))
    assert not f['no_size'] and not f['no_direct']


NONPROFIT = ('「중소기업제품 구매촉진 및 판로지원에 관한 법률」 시행령 '
             '제2조의3 제2호에 해당하는 비영리 법인')


def decision(clause):
    rec = notice('3. 입찰참가자격', clause)
    knowledge = Knowledge(DATA)
    knowledge.detailed_product_facts(rec)
    return infer(rec, {'v15':'0', 'e15':''}, knowledge._product_facts)


def test_explicit_nonprofit_or_preserves_ordinary_company_size_limit():
    row, facts = decision('가. 소기업·소상공인확인서를 소지한 자 또는 '+NONPROFIT)
    assert row['v15'] == '1'
    assert facts['qualification']['commercial_size_exception_review'] == []
    assert facts['qualification']['exceptions'][0]['kind'] == 'nonprofit_alternative'


@pytest.mark.parametrize('tail', [
    '다만 '+NONPROFIT,
    '또는 '+NONPROFIT+' 및 그 밖의 예외 대상',
    '또는 '+NONPROFIT.replace('제2호', '제1호'),
])
def test_ambiguous_or_broader_exception_is_still_reviewed(tail):
    row, facts = decision('가. 소기업·소상공인확인서를 소지한 자 '+tail)
    assert row['v15'] == '0'


@pytest.mark.parametrize('predicate', ['신고를 필한 업체로서', '등록을 필한 자로서'])
def test_registration_alternatives_end_before_common_production_duty(predicate):
    rec = notice('3. 입찰참가자격',
        '가. 인쇄업 또는 출판업 '+predicate+' 직접생산확인증명서(1111111111)를 소지한 업체')
    f = qualification_facts(rec, inventory(rec))
    assert f['direct_certificate_coverage']['guaranteed_codes'] == ['1111111111']


def test_registration_or_production_remains_a_real_alternative():
    rec = notice('3. 입찰참가자격',
        '가. 인쇄업 신고를 필한 업체 또는 직접생산확인증명서(1111111111)를 소지한 업체')
    f = qualification_facts(rec, inventory(rec))
    assert not f['direct_certificate_coverage']['guaranteed_codes']
