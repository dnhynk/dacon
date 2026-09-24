"""Consumer repairs keep permission, operative duties and reference roles apart."""
import pytest
from submission.pps.qualification import inventory, qualification_facts
from submission.pps.performance import performance_facts
from tests.test_qualification_heading_roles import notice


def facts(clause):
    rec = notice('3. 입찰참가자격', clause)
    return qualification_facts(rec, inventory(rec))


@pytest.mark.parametrize('clause', [
    '기업규모에 관계없이 신청 가능하며 중소기업확인서를 요구하지 않습니다.',
    '참가업체를 중소기업자로 한정하지 않습니다.',
    '업체 규모에 따른 참가 제한을 두지 않습니다.',
])
def test_explicit_size_permission_is_not_an_unresolved_size_duty(clause):
    f = facts(clause)
    assert not f['raw_size']
    assert not f['active_size']


def test_document_waiver_and_separate_restriction_still_block_absence():
    f = facts('중소기업확인서는 제출하지 않아도 됩니다.\n소기업·소상공인 확인서를 소지한 업체')
    assert f['active_size'] and not f['no_size']


def test_nonprofit_exception_does_not_waive_commercial_size_bound():
    f = facts('소기업·소상공인 확인서를 소지한 업체\n'
              '※ 단, ｢중소기업제품 구매촉진 및 판로지원에 관한 법률 시행령｣ 제2조의 3에 따라 ‘비영리법인’의 경우 예외 적용함')
    assert f['exceptions'][0]['kind'] == 'nonprofit_alternative'


@pytest.mark.parametrize('prefix', ['예시: ', '참고용: ', '가정: '])
def test_nonasserted_exclusion_never_establishes_size_bound(prefix):
    f = facts(prefix+'소기업 또는 소상공인으로 한정하며 중기업은 참가할 수 없습니다.')
    assert not f['ordinary_commercial_size_bound']


def test_explicit_medium_exclusion_preserves_a_small_commercial_bound():
    f = facts('중소기업 확인서를 소지한 업체\n'
              '소기업 또는 소상공인으로 한정하며 중기업은 참가할 수 없습니다.')
    assert f['ordinary_commercial_size_bound']['ordinary_medium_enterprises_excluded']
    assert f['allowed'] == ['micro', 'small']


def test_prebid_direct_verification_with_exclusion_is_a_duty():
    f = facts('입찰서 제출 마감일 전일까지 직접생산확인증명서가 중소기업제품 공공구매 종합정보망에서 조회되는 자(조회되지 않는 자의 입찰은 무효로 함)')
    assert f['direct_certificate_coverage']['observations'][0]['production_required_in_every_branch']
    assert not f['direct_certificate_coverage']['guaranteed_codes']


@pytest.mark.parametrize('clause', [
    '계약체결 후 직접생산확인증명서를 제출하여야 합니다.',
    '입찰등록 전 직접생산확인증명서를 제출하면 됩니다.',
    '직접생산확인증명서 또는 판매업 등록증을 제출하여야 합니다.',
])
def test_submission_alone_does_not_prove_holding(clause):
    assert not facts(clause)['active_direct']


def test_contract_article_closes_then_new_eligibility_reopens():
    rec = notice('3. 입찰참가자격', '일반 등록 업체\n운송 용역 특수계약조건\n제1조 (목적) 계약상 의무\n'
                 '4. 입찰참가자격\n소기업 확인서를 소지한 업체')
    f = qualification_facts(rec, inventory(rec))
    assert all(section['closed'] for section in f['eligibility_sections'])
    assert f['active_size']


def test_consumer_region_wrap_does_not_change_default_prompt_facts():
    from tests.test_performance_units import record
    rec=record('가. 본점 소재지(개인사업자인 경우 사업장 소재지)는\n'
               '세종특별자치시에 두고 있는 업체\n'
               '나. 최근 3년 운영 실적을 보유한 업체')
    before=performance_facts(rec)
    consumed=performance_facts(rec,consumer=True)
    assert not before['operative_regions']
    assert consumed['operative_regions']
    assert performance_facts(rec)==before


def test_entity_noun_or_is_not_an_experienceless_branch():
    from tests.test_performance_units import record
    rec=record('최근 3년 운영 실적을 1건 이상 보유한 업체 또는 단체')
    assert performance_facts(rec,consumer=True)['overlays']['v2']['value']==1
    alternative=record('최근 3년 운영 실적을 보유한 업체 또는 일반 업종 등록을 마친 단체')
    assert performance_facts(alternative,consumer=True)['overlays']['v2']['value'] is None


def test_ten_digit_wrapped_certificate_code_retains_possession():
    f=facts('직접생산확인증명서[세부품명: 시험물품(세부품명번호:\n1234567890)]를 소지한 업체이어야 합니다.')
    assert f['direct_certificate_coverage']['guaranteed_codes']==['1234567890']


def test_consumer_heading_role_survives_a_nested_submission_heading():
    from tests.test_performance_units import record
    rec=record('가. 최근 3년 실적을 보유한 업체\n나. 제출서류\n실적증명서 1부\n다. 일반 등록 업체')
    f=performance_facts(rec,consumer=True)
    assert f['candidates'][0]['status']=='mandatory'
    assert any(c['status']=='forms_or_submission' for c in f['candidates'])


def test_another_statutes_article_number_is_not_a_priority_exception():
    f=facts('중소기업 확인서를 소지한 자\n4. 계약조건\n｢인지세법 시행령｣ 제2조의3에 따라 인지세를 납부합니다.')
    assert f['exceptions'][0]['kind']=='unrelated_statutory_reference'


def test_each_or_branch_still_requires_its_own_certificate_with_wrapped_qualifiers():
    f=facts('직접생산확인증명서[세부품명: 시험물품(세부품명번호 1234567890) 또는 '
            '직접생산확인증명서[세부품명: 대체물품(세부품명번호 1234567891)를 소지한 자')
    coverage=f['direct_certificate_coverage']
    assert coverage['observations'][0]['production_required_in_every_branch']
    assert coverage['guaranteed_codes']==[]
