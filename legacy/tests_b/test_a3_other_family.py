"""Consumer proof boundaries and equivalent briefing-eligibility grammar."""
import pytest

from submission.pps.other_checks import briefing_check
from tests.test_other_checks import notice


@pytest.mark.parametrize('clause', [
    '사업설명회 미참석 업체는 제안 참가 자격 없음.',
    '현장설명회 불참 업체의 입찰은 무효로 합니다.',
    '제안요청 설명회 참석확인을 받지 못한 업체의 입찰등록은 허용하지 않습니다.',
    '제안요청 설명회에 참석하여 출석 확인을 받은 업체만 제안서를 낼 수 있습니다.',
    '사업설명회에 참석하여 참석확인서를 교부받은 업체에 한하여 제안서를 제출할 수 있습니다.',
])
def test_attendance_confirmation_and_negative_eligibility(clause):
    result = briefing_check(notice(clause))
    assert result['value'] == 1
    assert result['evidence'] == clause


@pytest.mark.parametrize('clause', [
    '사업설명회에 참석하여 출석 확인을 받은 업체만 주차권을 받을 수 있습니다.',
    '사업설명회 참석확인을 받지 못한 업체에도 입찰등록을 허용합니다.',
    '사업설명회 참석은 선택사항이며, 불참하더라도 입찰참가 및 제안서 제출에 아무런 제한이 없음.',
    '사업설명회 불참 업체의 입찰이 무효인 것은 아닙니다.',
    '사업설명회 불참 업체의 입찰은 무효라는 조건은 철회한다.',
    '사업설명회 불참 업체에게 제안 참가 자격이 없는 것은 아닙니다.',
    '계약 체결 후 사업설명회 불참 업체의 입찰은 무효로 합니다.',
    '제안서 평가위원 대상 설명회 불참 업체의 입찰은 무효로 합니다.',
    '사업설명회에 참석하여 출석 확인을 받습니다. 안전교육 불참 업체는 입찰 불가.',
])
def test_attendance_boundaries(clause):
    assert briefing_check(notice(clause))['value'] != 1

