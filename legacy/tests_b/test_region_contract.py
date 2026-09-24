"""The operative office qualification controls scope, not a registration flag."""
import pytest

from submission.pps.regions import above_ceiling_region_check, multiple_region_check
from submission.pps.rules import apply_rules, narrow_region_check


def notice(text, price=300_000_000, method='일반경쟁'):
    return {'id': 'synthetic-region-contract',
            'meta': {'적용계약법': '국가계약법', '업무구분': '일반용역',
                     '계약방법': method, '입찰추정가격': price, '지역제한여부': 'N'},
            'docs': [{'type': '공고문', 'text': text}],
            'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


OFFICE = '입찰참가자는 주된 사무소 소재지가 서울특별시로 기재되어 있는 업체이어야 한다.'


@pytest.mark.parametrize('method', ['일반경쟁', '제한경쟁'])
def test_actual_office_restriction_overrides_general_competition_registration(method):
    rec = notice('2. 입찰참가자격\n' + OFFICE, method=method)
    check = above_ceiling_region_check(rec)
    assert check['value'] == 1
    assert check['evidence'] in rec['docs'][0]['text']
    for raw in (0, 1):
        after, _ = apply_rules(rec, {'v5': raw, 'e5': ''}, items=(5,))
        assert after['v5'] == 1


@pytest.mark.parametrize('method', ['일반경쟁', '제한경쟁'])
@pytest.mark.parametrize('text', [
    '2. 납품장소\n서울특별시 소재 본사로 납품한다.',
    '2. 입찰참가자격\n예시: ' + OFFICE,
    '2. 입찰참가자격\n' + OFFICE + '\n지역 제한 조건은 철회한다.',
    '소액수의 견적제출 안내공고\n2. 입찰참가자격\n' + OFFICE,
    '계약방법: 수의계약\n2. 입찰참가자격\n' + OFFICE,
])
def test_location_example_withdrawal_and_quote_do_not_force_region_violation(text, method):
    assert above_ceiling_region_check(notice(text, method=method)) is None


@pytest.mark.parametrize('price', [0, 229_999_999, None])
def test_price_boundary_remains_independent_of_method_metadata(price):
    assert above_ceiling_region_check(notice(OFFICE, price)) is None


def test_general_competition_is_not_itself_a_region_violation():
    assert above_ceiling_region_check(notice('입찰참가자는 해당 업종을 등록한 업체이어야 한다.')) is None
    assert above_ceiling_region_check(notice(OFFICE, method='수의계약')) is None


def test_local_above_ceiling_bidder_location_does_not_require_the_word_office():
    rec = notice('2. 입찰참가자격\n경북에 소재한 실적이 우수한 업체이어야 한다.',
                 price=545_454_545)
    rec['meta']['적용계약법'] = '지방계약법'
    check = above_ceiling_region_check(rec)
    assert check['value'] == 1 and check['ceiling'] == 500_000_000
    assert check['evidence'] in rec['docs'][0]['text']


def test_local_below_ceiling_and_performance_place_do_not_trigger_v5():
    rec = notice('2. 입찰참가자격\n경북에 소재한 실적이 우수한 업체이어야 한다.',
                 price=499_999_999)
    rec['meta']['적용계약법'] = '지방계약법'
    assert above_ceiling_region_check(rec) is None


def test_dated_local_notice_uses_common_ordinary_lower_bound_for_v7():
    rec = notice(
        '용역명: 지역축제 행사대행 용역\n2. 입찰참가자격\n'
        '주된 영업소를 경상남도, 부산광역시 내에 둔 사업자로 제한한다.',
        price=327_272_727, method='제한경쟁')
    rec['meta'].update(적용계약법='지방계약법', 공고게시일자='20260312')
    check = multiple_region_check(rec)
    assert check['value'] == 1
    assert check['sufficient_price_upper_bound'] == 350_000_000
    assert check['regional_price_bounds']['status'] == 'interval_authority_unresolved'


def test_local_interval_does_not_choose_v5_or_v7_between_possible_ceilings():
    text = ('용역명: 지역축제 행사대행 용역\n2. 입찰참가자격\n'
            '주된 영업소를 경상남도, 부산광역시 내에 둔 사업자로 제한한다.')
    rec = notice(text, price=350_000_000, method='제한경쟁')
    rec['meta'].update(적용계약법='지방계약법', 공고게시일자='20260312')
    assert multiple_region_check(rec) is None
    assert above_ceiling_region_check(rec) is None


def test_local_safety_service_does_not_inherit_ordinary_notice_amount():
    text = ('용역명: 시설물 정밀안전진단 용역\n'
            '시설물의 안전 및 유지관리에 관한 특별법에 따른 용역이다.\n'
            '2. 입찰참가자격\n본점 소재지가 경상남도와 부산광역시 내에 있는 업체이어야 한다.')
    rec = notice(text, price=200_000_000, method='제한경쟁')
    rec['meta'].update(적용계약법='지방계약법', 공고게시일자='20260312')
    assert multiple_region_check(rec) is None
    check = above_ceiling_region_check(rec)
    assert check['value'] == 1 and check['ceiling'] == 150_000_000
    rec = notice('2. 납품장소\n경북에 소재한 행사장에서 용역을 수행할 업체',
                 price=600_000_000)
    rec['meta']['적용계약법'] = '지방계약법'
    assert above_ceiling_region_check(rec) is None


def test_other_region_items_share_the_actual_competition_gate():
    text = '주된 영업소가 서울특별시 [지역:r1|단위=기초|광역=서울특별시] 내에 소재하고 있는 업체이어야 한다.'
    assert narrow_region_check(notice(text, 100_000_000))['value'] == 1
    text = '주된 영업소를 경상남도, 부산광역시 내에 둔 사업자로 제한한다.'
    assert multiple_region_check(notice(text, 100_000_000))['value'] == 1


@pytest.mark.parametrize('method', ['일반경쟁', '제한경쟁'])
@pytest.mark.parametrize('check,text,price', [
    (above_ceiling_region_check, OFFICE, 300_000_000),
    (multiple_region_check, '본점 소재지가 경상남도와 부산광역시 내에 있는 업체이어야 한다.', 100_000_000),
    (narrow_region_check, '본점 소재지가 [지역:r1|단위=기초|광역=경기도] 내에 있는 업체이어야 한다.', 100_000_000),
])
def test_actual_quote_in_second_notice_is_not_hidden_by_document_order(check, text, price, method):
    rec = notice('2. 입찰참가자격\n' + text, price, method)
    rec['docs'].append({'type': '공고문', 'text': '계약방법: 수의계약\n견적제출 안내공고'})
    assert check(rec) is None
