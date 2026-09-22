"""Semantic controls for bidder location, schedule and joint-share roles."""
import pytest

from submission.pps.regions import above_ceiling_region_check, multiple_region_check, regional_competition_scope
from submission.pps.rules import narrow_region_check, joint_share_check
from tests.test_region_contract import notice
from tests.test_temporal import rec
from tests.test_rules import notice as share_notice
from submission.pps.temporal import v23


@pytest.mark.parametrize('ending', ['에 둔 자이어야 한다.', '에 소재하고 있는 자.', '에 소재인 기업이어야 한다.'])
def test_basic_region_binds_individual_or_enterprise_bidder(ending):
    r=notice('입찰참가자의 주된 영업소가 [지역:r8|단위=기초|광역=경기도]'+ending,100_000_000)
    assert narrow_region_check(r)['value']==1


@pytest.mark.parametrize('text', [
    '주된 영업소의 소재지를 확인할 수 있는 사업자등록증을 제출해야 합니다.',
    '동일 점수인 경우 본점 소재지가 [지역:r8|단위=기초|광역=경기도] 내에 있는 업체를 우선으로 한다.',
    '참가업체는 경기도에 본점을 두어야 하고, [수요기관(기초자치단체)]에서 제시하는 규격에 따라 납품할 수 있는 업체여야 한다.',
])
def test_document_priority_and_buyer_are_not_basic_region_eligibility(text):
    r=notice(text,100_000_000)
    r['meta'].update(지역제한여부='Y',제한지역코드목록='[등록지역:r8|단위=기초|광역=경기도]')
    # The buyer example has an independent province condition; omit registered
    # basic metadata so this test isolates the institution-token path.
    if '제시하는' in text:r['meta']['지역제한여부']='N'
    assert narrow_region_check(r) is None


@pytest.mark.parametrize('check,price,place', [
    (above_ceiling_region_check,600_000_000,'경기도'),
    (multiple_region_check,100_000_000,'경기도 또는 강원특별자치도'),
])
def test_location_qualification_accepts_bare_person(check,price,place):
    r=notice('입찰참가자는 본점 소재지를 '+place+'에 둔 자이어야 한다.',price)
    assert check(r)['value']==1


def test_joint_or_divided_permission_includes_joint_performance():
    r=share_notice('공동도급(공동 또는 분담이행방식)이 가능하며 공동수급체 구성원별 최소지분율은 2% 이상이어야 한다.',law='지방계약법')
    assert joint_share_check(r)['value']==1


def test_past_qualification_negation_does_not_cancel_a_dated_briefing():
    r=rec('입찰참가 자격정지 사유가 없는 업체이어야 한다.\n마. 사업설명회: 2026. 1. 4.\n\n제안서 접수: 2026. 1. 20.')
    assert v23(r)['value']==1


def test_insurance_in_previous_list_item_does_not_cancel_briefing():
    r=rec('가. 생산물 배상책임 보험에 가입한 사업자\n나. 사업설명회: 2026. 1. 4.\n\n제안서 접수: 2026. 1. 20.')
    assert v23(r)['value']==1


def test_announcement_date_is_not_proposal_deadline():
    r=rec('제안서 제출 안내사항을 다음과 같이 공고합니다.\n2026년 1월 1일\n\n'
          '사업설명회: 2026. 1. 4.\n\n제안서 제출: 2026. 1. 20.')
    decision=v23(r)
    assert decision['value']==1
    assert {f['value'] for f in decision['facts'] if f['kind']=='proposal_deadline'}=={'2026-01-20'}


def test_submission_time_range_does_not_extend_to_evaluation_date():
    r=rec('사업설명회: 2026. 1. 15.\n\n제안서 제출: 2026. 1. 20. 10:00 ~ 17:00\n'
          '제안서 평가: 2026. 2. 2.')
    decision=v23(r)
    assert decision['value']==1
    assert {f['value'] for f in decision['facts'] if f['kind']=='proposal_deadline'}=={'2026-01-20'}


def test_explicit_cancellation_still_blocks_inserted_date():
    r=rec('사업설명회: 2026. 1. 4.\n\n과업설명은 제안요청서로 갈음합니다.')
    assert v23(r)['value'] != 1


def test_basic_region_with_named_alternative_keeps_location_binding():
    r=notice('본점 소재지를 [지역:r8|단위=기초|광역=경기도] 또는 제주도에 둔 업체',100_000_000)
    assert narrow_region_check(r)['value']==1


def test_office_after_parenthesized_basic_region_binds_the_same_duty():
    r=notice('권역(경기도 [지역:r8|단위=기초|광역=경기도])에 사무소를 둔 업체만 참여 가능',100_000_000)
    assert narrow_region_check(r)['value']==1


def test_office_located_predicate_is_not_lost_by_tighter_binding():
    r=notice('주된 영업소가 [지역:r8|단위=기초|광역=경기도]에 위치해 있는 업체이어야 한다.',100_000_000)
    assert narrow_region_check(r)['value']==1


@pytest.mark.parametrize('anchor',['견적공고일','견적제출 공고일','견적 제출 안내(공고)일'])
def test_qualification_reference_day_does_not_announce_quote_procedure(anchor):
    r=notice(anchor+' 전일부터 본점 소재지가 경기도인 업체이어야 한다.')
    assert regional_competition_scope(r)
    r['docs'][0]['text']='견적제출 안내공고\n'+r['docs'][0]['text']
    assert not regional_competition_scope(r)
