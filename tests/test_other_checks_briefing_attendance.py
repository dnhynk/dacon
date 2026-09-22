"""Attendance must govern bidding eligibility, within the briefing item."""
import pytest
from submission.pps.other_checks import briefing_check


def notice(text, award='협상에의한계약'):
    return {'meta': {'적용계약법': '국가계약법', '소관구분': '국가기관', '낙찰방법': award},
            'docs': [{'type': '공고문', 'text': text}],
            'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


@pytest.mark.parametrize('text', [
    '입찰참가자격\n현장설명회에 참석한 경우에 한함.',
    '입찰 참가자격은 사업설명회에 참석한 경우에 한함.',
    '사업설명회 참석업체에 한하여 입찰할 수 있다.',
    '제안요청서 설명회에 참석한 자만 제안서 제출 가능.',
    '제안요청설명회 불참자는 입찰 참가자격이 주어지지 않음.',
    '사업설명회 미참석 업체의 제안서는 접수하지 않음.',
    '현장설명회에 참석하지 아니한 업체의 입찰 참가는 허용되지 않음.',
    '사업설명회 불참 업체는 참가 불가.',
    '현장설명회는 다음 주 개최한다. 해당 설명회 미참석자의 제안서는 접수하지 않음.',
])
def test_attendance_eligibility_relation(text):
    assert briefing_check(notice(text))['value'] == 1


@pytest.mark.parametrize('text', [
    '사업설명회에 참석 바랍니다. 입찰 참가자격을 안내합니다.',
    '사업설명회 참석한 자만 주차권을 받는다.',
    '사업설명회 안내. 안전교육 참석업체만 입찰 가능.',
    '사업설명회 불참자는 기념품이 주어지지 않음.',
    '현장설명회 안내\n교육 불참자는 입찰 참가자격이 주어지지 않음.',
    '계약 후 사업설명회 불참자는 입찰 참가자격이 주어지지 않음.',
    '계약 체결 후 사업설명회 불참자는 입찰 참가자격이 주어지지 않음.',
    '착수보고 사업설명회 불참자는 입찰 참가자격이 주어지지 않음.',
    '제안서 발표회 및 사업설명회 불참자는 입찰 참가자격이 주어지지 않음.',
    '사업설명회 불참자는 입찰 참가자격이 주어지지 않음이라는 조건은 철회한다.',
    '사업설명회 참석 여부와 상관없이 입찰 가능하며 불참 업체를 대상에서 제외하지 않는다.',
    '사업설명회 미참석 업체의 제안서는 접수하지 않는 것은 아니다.',
])
def test_exclusions(text):
    assert briefing_check(notice(text))['value'] != 1


def test_negotiated_contract_required():
    assert briefing_check(notice('현장설명회 불참자는 입찰 참가자격이 주어지지 않음.', '적격심사'))['value'] is None
