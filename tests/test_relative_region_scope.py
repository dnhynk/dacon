"""Relative administrative scope must bind the bidder, not the buyer's address."""
import pytest

from submission.pps.regions import above_ceiling_region_check, multiple_region_check
from submission.pps.rules import narrow_region_check
from tests.test_region_contract import notice


PROVINCE = [
    '입찰참가자는 수요기관이 소재한 광역자치단체 안에 본점을 둔 업체만 가능합니다.',
    '참가자격: 본점 소재지가 발주기관 본청과 동일한 광역시·도 관할구역 안에 있는 사업자.',
    '발주기관 소재 광역자치단체 안의 본점 사업자에게만 참가를 허용합니다.',
    '[수요기관(행정기관)] 본청이 소재한 광역시·도 관할구역 안에 본점을 계속 두고 있는 업체.',
]
BASIC = [
    '응찰자의 본점은 발주기관 소재 시·군·구의 행정구역 안에 있어야 합니다.',
    '수요기관 주소가 속한 단일 시·군·구에 본점을 둔 업체만 신청할 수 있습니다.',
]
MULTIPLE = [
    '본점 참가지역은 수요기관 소재 광역자치단체 또는 그와 인접한 다른 광역자치단체 중 한 곳으로 정합니다.',
    '응찰자는 발주기관 본사 소재 광역이나 인접 광역 중 하나에 본점을 두어야 합니다.',
    '지역자격은 발주기관이 소재한 광역 또는 인접 광역의 본점 사업자로 한정합니다.',
]


@pytest.mark.parametrize('text', PROVINCE)
def test_relative_province_above_ceiling(text):
    r = notice('2. 입찰참가자격\n' + text, 600_000_000)
    check = above_ceiling_region_check(r)
    assert check and check['value'] == 1
    assert check['evidence'] in r['docs'][0]['text']


@pytest.mark.parametrize('text', BASIC)
def test_relative_basic_municipality_below_ceiling(text):
    r = notice('2. 입찰참가자격\n' + text, 80_000_000)
    check = narrow_region_check(r)
    assert check and check['value'] == 1
    assert check['evidence'] in r['docs'][0]['text']


@pytest.mark.parametrize('text', MULTIPLE)
def test_relative_multiple_provinces_below_ceiling(text):
    r = notice('2. 입찰참가자격\n' + text, 80_000_000)
    check = multiple_region_check(r)
    assert check and check['value'] == 1
    assert check['evidence'] in r['docs'][0]['text']


@pytest.mark.parametrize('text', [
    '수요기관 본점은 같은 광역자치단체 안에 소재하며, 납품업체는 그 주소로 배송해야 합니다.',
    '발주기관 소재 광역자치단체 안에 있는 본사에 물품을 납품할 업체.',
    '발주기관 소재 광역자치단체에 본점을 둔 업체를 동점일 때 우선으로 한다.',
    '본점이 발주기관 소재 광역에 있으면 가점을 부여한다.',
    '예시: 본점이 발주기관 소재 광역자치단체에 있는 업체만 참가 가능.',
    '본점이 발주기관 소재 광역자치단체에 있어야 한다는 조건은 철회한다.',
    '발주기관 소재 광역에 본점을 두지 않아도 모든 업체가 참가할 수 있다.',
    '발주기관 소재 광역에 본점을 둔 업체도 참가할 수 있다.',
    '낙찰자는 계약 체결 후 발주기관 소재 광역자치단체에 본점을 설치하여야 한다.',
    '공급업체의 물품을 발주기관 본점 소재 광역자치단체로 배송한다.',
    '입찰참가자는 발주기관 소재 광역자치단체의 행사장에 인력을 배치할 업체.',
    '본점은 발주기관 소재 광역 또는 전국 어느 곳에 있어야 한다.',
])
def test_relative_address_does_not_establish_bidder_qualification(text):
    assert above_ceiling_region_check(notice(text, 600_000_000)) is None


@pytest.mark.parametrize('check,text,price', [
    (above_ceiling_region_check, PROVINCE[0], 600_000_000),
    (narrow_region_check, BASIC[0], 80_000_000),
    (multiple_region_check, MULTIPLE[0], 80_000_000),
])
def test_relative_scope_preserves_quote_gate(check, text, price):
    assert check(notice(text, price, method='수의계약')) is None


def test_relative_scope_preserves_unit_and_price_boundaries():
    assert narrow_region_check(notice(PROVINCE[0], 80_000_000)) is None
    assert multiple_region_check(notice(PROVINCE[0], 80_000_000)) is None
    assert above_ceiling_region_check(notice(PROVINCE[0], 80_000_000)) is None
    assert narrow_region_check(notice(BASIC[0], 600_000_000)) is None
    assert multiple_region_check(notice(MULTIPLE[0], 600_000_000)) is None


@pytest.mark.parametrize('extra', [
    '납품장소: 인접 시·도에 걸쳐 있는 대상시설.',
    '해당 시도의 자격을 갖춘 업체 수가 10인 미만이다.',
])
def test_relative_multiple_scope_keeps_exception_open(extra):
    r = notice(MULTIPLE[0] + '\n\n' + extra, 80_000_000)
    assert multiple_region_check(r) is None


def test_relative_multiple_scope_requires_complete_input():
    r = notice(MULTIPLE[0], 80_000_000)
    r['input_completeness']['완전관측'] = False
    assert multiple_region_check(r) is None
