"""Notice amount typography must not change applicability or erase ambiguity."""
import copy
from decimal import Decimal

import pytest

from submission.pps.amounts import won_value
from submission.pps.comparison import amount_facts, compare
from submission.pps.prices import project_prices, in_band
from tests.test_comparison import record


@pytest.mark.parametrize('text', [
    '추정가격(80,000,000원)',
    '- 추정가격(80,000,000원)/부가가치세(8,000,000원)',
    '추정가격（80,000,000원）',
    '추정가격: (금80,000,000원)',
    '추정가격(부가세 제외): (80,000,000원)',
    '추정가격(80,000,000원(금팔천만원정))',
    '추정가격(금팔천만원)',
    '추정가격(금 팔 천 만 원)',
    '추정가격: 금팔천만원정',
    '추정가격: (팔천만원)',
    '추정가격((80,000,000원))',
    '추정가격（(금팔천만원)）',
    '추정가격(80,000,000원)\n납품기한: 계약 후 3개월',
])
def test_parenthesized_notice_literal_governs_without_mutating_registration(text):
    rec = record(text, 입찰추정가격=150_000_000)
    raw = copy.deepcopy(rec)
    price = project_prices(rec)['estimated_price']
    assert price['status'] == 'known' and price['value_won'] == 80_000_000
    assert price['effective_source'] == 'notice' and price['source_conflict']
    assert price['all_observed_values_won'] == [80_000_000, 150_000_000]
    assert in_band(price, upper=100_000_000) is True
    assert next(c for c in compare(rec)['comparisons'] if c['field'] == 'estimated_price')['status'] == 'different'
    assert rec == raw
    for observation in price['body']:
        ev = observation['evidence']
        assert ev['text'] == text[ev['start']:ev['end']]
        assert ev['text'].startswith(text.splitlines()[0])


@pytest.mark.parametrize('text', [
    '추정가격(80,00,000원)',
    '추정가격(80,000,000원',
    '추정가격(80,000,000원）',
    '추정가격(80,000,000원(금구천만원))',
    '추정가격(80,000,000원): 90,000,000원',
    '추정가격(금팔백천만원)',
    '추정가격: 금팔오만원',
    '추정가격(금팔천만원(￦90,000,000원))',
    '추정가격: 금팔천만원(90,000,000원)',
    '추정가격: 금팔천만원(80,00,000원)',
    '추정가격: 금팔천만원(90,000,000원',
    '추정가격: 금팔천만원정(80,00,000원)',
    '추정가격: 금팔천만원정(80,000,000원',
    '추정가격: 80,000,000원정(팔천만원',
    '추정가격: 80,000,000원정(팔천만원）',
    '추정가격(-80,000,000원)',
    '추정가격: -80,000,000원',
    '추정가격((80,000,000원)',
    '추정가격: 0원',
])
def test_broken_or_contradictory_notice_literal_does_not_fall_back_to_meta(text):
    rec = record(text, '추정가격: 150,000,000원', 입찰추정가격=150_000_000)
    price = project_prices(rec)['estimated_price']
    assert price['status'] == 'unknown' and price['value_won'] is None
    assert price['unresolved_literal'] and price['effective_source'] == 'notice'
    assert in_band(price, upper=100_000_000) is None


@pytest.mark.parametrize('text', [
    '예시: 추정가격(80,000,000원)',
    '예시:\n추정가격(80,000,000원)',
    '1차 추정가격(80,000,000원)',
    '추정가격(80,000,000원) 이상인 사업은 별도 절차에 따른다.',
    '추정가격(80,000,000원)은 적용하지 않는다.',
    '추정가격(부가세 제외)에 대한 산정 방법은 별도로 안내한다.',
    '추정가격(예시: 80,000,000원)은 작성용 참고입니다.',
    '예시: 추정가격(80,00,000원)',
    '1차 추정가격(80,00,000원)',
    '추정가격(80,00,000원) 미만인 사업에 한한다.',
    '추정가격(80,00,000원)은 적용하지 않는다.',
])
def test_qualifiers_thresholds_examples_and_phases_are_not_whole_assignments(text):
    rec = record(text, 입찰추정가격=150_000_000)
    price = project_prices(rec)['estimated_price']
    assert price['value_won'] == 150_000_000 and not price['unresolved_literal']


def test_two_distinct_notice_assignments_remain_conflicting():
    rec = record('추정가격(80,000,000원)\n추정가격(150,000,000원)', 입찰추정가격=80_000_000)
    price = project_prices(rec)['estimated_price']
    assert price['status'] == 'conflict' and price['value_won'] is None
    assert price['candidate_values_won'] == [80_000_000, 150_000_000]
    assert in_band(price, upper=100_000_000) is None


def test_tax_basis_stays_with_its_own_parenthesized_field():
    rec = record('사업예산(88,000,000원, 부가세 포함)\n추정가격(80,000,000원)\n기초금액(77,000,000원, 부가세 포함)',
                 배정예산금액=165_000_000, 입찰추정가격=150_000_000)
    prices = project_prices(rec)
    assert prices['budget']['value_won'] == 88_000_000
    assert prices['estimated_price']['value_won'] == 80_000_000
    assert [f['basis'] for f in amount_facts(rec)] == ['including_vat', 'excluding_vat', 'including_vat']


def test_parenthesized_estimate_with_included_tax_is_not_a_comparable_estimate():
    price = project_prices(record('추정가격(88,000,000원, 부가세 포함)'))['estimated_price']
    assert price['status'] == 'unknown' and price['unresolved_tax_basis']


def test_outer_wrapper_does_not_hide_a_bound():
    fact = amount_facts(record('추정가격(80,000,000원) 미만'))[0]
    assert fact['scope'] == 'bounded' and fact['value_relation'] == '미만'


@pytest.mark.parametrize('text,value', [
    ('금팔천만원정', 80_000_000), ('삼억이천오백만원', 325_000_000),
    ('금일천사백오십팔원구십전', Decimal('1458.90')),
])
def test_spelled_scalar_is_exact_not_a_digit_concatenation(text, value):
    assert won_value(text) == Decimal(value)


def test_spelled_fractional_project_amount_cannot_be_rounded_into_a_band():
    price = project_prices(record('추정가격(금일천사백오십팔원구십전)'))['estimated_price']
    assert price['value_won'] is None and price['unresolved_literal']


def test_spelled_and_numeric_literal_duplicate_must_still_agree():
    assert won_value('80,000,000원(금구천만원)') is None
    assert won_value('80,000,000원(금팔천만원)') == Decimal(80_000_000)


@pytest.mark.parametrize('text', [
    '금팔천만원(￦80,000,000, 부가세 포함)',
    '금팔천만원( ￦ 80,000,000원, 부가가치세, 배송비 등\n부대비용 포함)',
    '금팔천만원정(80,000,000원)',
    '금팔천만원（80,000,000원）',
])
def test_korean_first_numeric_duplicate_must_agree(text):
    assert won_value(text) == 80_000_000
    assert won_value(text.replace('80,000,000', '90,000,000')) is None
    assert project_prices(record('사업예산: '+text))['budget']['value_won'] == 80_000_000


def test_readable_spelled_notice_and_second_notice_are_both_retained():
    rec = record('사업예산: 금 오천오백만원(부가세 포함)', '사업예산: 66,000,000원(부가세 포함)')
    rec['docs'][1]['type'] = '공고문'
    assert compare(rec)['comparisons'][0]['status'] == 'documents_conflict'
    assert project_prices(rec)['budget']['status'] == 'conflict'


def test_plain_spelled_thousand_after_colon_is_a_value_in_a_pipe_row():
    rec = record('| 추정가격: 천원 |')
    assert project_prices(rec)['estimated_price']['value_won'] == 1000


@pytest.mark.parametrize('unit', ['천원', '만원', '백만원'])
def test_unit_annotation_without_a_numeric_value_cannot_invent_a_price(unit):
    rec = record('사업예산('+unit+')', 배정예산금액=None)
    assert project_prices(rec)['budget']['value_won'] is None
