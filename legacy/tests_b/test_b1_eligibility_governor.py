"""B1: a base date or an enumerated-clause governor still closes the section.

Neither qualifier restricts the listed qualifications, so absence checks may use
the section. Examples, withdrawals, form lists and a bare base date must not.
"""
import pytest

from submission.pps.qualification import inventory, qualification_facts
from tests.test_qualification_heading_roles import notice


@pytest.mark.parametrize('header', [
    '5. 입찰 참가 자격 : 입찰공고일 현재 아래 조건을 모두 충족 하는 업체',
    '4. 입찰참가자격 : 입찰등록마감일 현재 다음 각 호의 자격을 모두 갖춘 자',
    '4. 견적제출 참가자격 : 견적제출마감일까지 다음의 요건을 모두 충족하여야 함',
    '3. 입찰참가 자격 (각 호의 자격을 모두 갖춘 자이어야 합니다)',
    '4. 입찰참가자격 : 각 항의 요건을 모두 충족하여야 함',
])
def test_dated_or_enumerated_governor_closes_the_eligibility_section(header):
    record = notice(header, '가. 사업자등록을 마친 업체이어야 합니다.')
    facts = qualification_facts(record, inventory(record))
    assert facts['closed_eligibility']
    assert facts['no_size'] and facts['no_direct']


@pytest.mark.parametrize('header', [
    '5. 입찰 참가 자격 : 입찰공고일 현재 아래 조건을 모두 충족 하는 업체 (작성 예시)',
    '4. 입찰참가자격 : 입찰등록마감일 현재 다음 각 호의 자격을 모두 갖춘 자 - 적용하지 않음',
    '4. 입찰참가자격 : 입찰공고일 현재 유효한 등록증을 보유한 업체',
    '4. 입찰참가자격 제출서류 목록',
])
def test_examples_withdrawals_and_a_bare_base_date_do_not_close_it(header):
    record = notice(header, '가. 사업자등록을 마친 업체이어야 합니다.')
    facts = qualification_facts(record, inventory(record))
    assert not facts['no_size']


def test_a_size_clause_inside_the_newly_closed_section_is_still_operative():
    record = notice('5. 입찰 참가 자격 : 입찰공고일 현재 아래 조건을 모두 충족 하는 업체',
                    '라. 중소기업확인서(중기업·소기업·소상공인 구분 불문)를 소지한 중소기업자일 것')
    facts = qualification_facts(record, inventory(record))
    assert facts['allowed'] == ['medium', 'micro', 'small']
    assert not facts['no_size']
