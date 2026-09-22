"""Consumer facts retain document function, bidder scope and event ownership."""
import copy

import pytest

from submission.pps.qualification import inventory, qualification_facts
from submission.pps.temporal import v23
from tests.test_qualification_heading_roles import notice
from tests.test_temporal import rec


@pytest.mark.parametrize('admission', [
    '중소기업자가 아닌 업체도 등록요건을 갖추면 입찰에 참가할 수 있습니다.',
    '중소기업이 아닌 기업도 입찰 참가가 가능합니다.',
    '중소기업에 해당하지 않는 업체도 제안서를 제출할 수 있습니다.',
])
def test_non_sme_admission_is_not_a_size_restriction(admission):
    record = notice('3. 입찰참가자격',
                    '※ 중소기업확인서는 해당 업체에 한하여 제출하는 확인용 서류이며, '+admission)
    before = copy.deepcopy(record)
    facts = qualification_facts(record, inventory(record))
    assert not facts['active_size']
    assert not facts['common_size_bound']
    assert record == before


@pytest.mark.parametrize('clause', [
    '가. 중소기업확인서를 보유한 업체만 참가할 수 있습니다.',
    '가. 중소기업자가 아닌 업체는 입찰에 참가할 수 없습니다.',
    '가. 중소기업확인서를 소지한 자\n나. 다른 입찰에서는 중소기업자가 아닌 업체도 참가할 수 있다는 예시입니다.',
])
def test_mandatory_or_nonasserted_size_wording_is_not_unrestricted(clause):
    entries, *_ = inventory(notice('3. 입찰참가자격', clause))
    assert not any(e.get('postprocessing_repair') == 'explicit_unrestricted_bidder_size' for e in entries)


@pytest.mark.parametrize('action', [
    '입찰등록 때 제출한 업체만 제안할 수 있습니다.',
    '견적제출 전에 첨부한 자에 한하여 입찰할 수 있다.',
])
def test_prebid_exclusive_certificate_submitter_is_a_duty(action):
    record = notice('3. 입찰참가자격', '나. 직접생산확인증명서를 '+action)
    facts = qualification_facts(record, inventory(record))
    assert facts['active_direct']
    assert not facts['no_direct']


@pytest.mark.parametrize('tail', [
    '입찰등록 때 제출할 수 있는 업체는 참고하시기 바랍니다.',
    '낙찰 후 제출한 업체만 대금을 청구할 수 있습니다.',
    '입찰등록 때 제출한 업체만 제안할 수 있다는 예시는 삭제한다.',
    '입찰등록 때 제출한 업체에 가점을 부여한다.',
    '면제하며 사업자등록증을 입찰등록 때 제출한 업체만 제안할 수 있다.',
])
def test_nonoperative_certificate_mention_is_not_a_duty(tail):
    record = notice('3. 입찰참가자격', '나. 직접생산확인증명서는 '+tail)
    assert not qualification_facts(record, inventory(record))['active_direct']


@pytest.mark.parametrize('citation', [
    '행정안전부 예규 제299호(2025. 10. 2.)',
    '조달청 고시 제17호(2024. 4. 8.)',
    '시행규칙(2025. 6. 3.)',
])
def test_statute_edition_date_is_not_a_proposal_deadline(citation):
    record = rec('제안서 제출 : 방문제출\n- '+citation+'에 따름\n'
                 '사업설명회 : 2026. 1. 15.\n'
                 '제안서 제출 마감 : 2026. 1. 20.')
    decision = v23(record)
    assert decision['value'] == 1
    deadlines = [f['value'] for f in decision['facts'] if f['kind']=='proposal_deadline']
    assert deadlines == ['2026-01-20']


def test_two_actual_proposal_deadlines_still_conflict():
    record = rec('사업설명회 : 2026. 1. 15.\n'
                 '제안서 제출 마감 : 2026. 1. 20.\n'
                 '제안서 접수 마감 : 2026. 1. 21.')
    assert v23(record)['reason'] == 'conflicting_proposal_deadlines'
