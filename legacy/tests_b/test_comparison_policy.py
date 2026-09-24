"""Comparison grounds remain independent across methods, amounts and licenses."""
import pytest

from submission.pps.comparison import compare, positive_decision
from tests.test_comparison import record, comparison
from tests.test_comparison_proof_consistency import guard


def test_rounding_claim_can_also_mention_the_equal_gross_pair():
    rec = record('추정가격: 19,999,999원\n기초금액: 22,000,000원(부가세 포함)',
                 입찰추정가격=20_000_000, 배정예산금액=22_000_000)
    claim = ('예산=상이(기초금액 22,000,000원 vs 예산 22,000,000원 동일하나 '
             '추정가격 19,999,999원 vs 20,000,000원 상이)')
    result = guard(rec, claim, '')
    assert result['value'] == 0 and result['absence_verified'] is False


def test_net_gross_claim_with_compound_units_needs_the_source_gross():
    rec = record('기초금액: 22,000,000원(부가세 포함)',
                 입찰추정가격=20_000_000, 배정예산금액=22_000_000)
    assert guard(rec, '예산=상이(추정가격2천만/배정예산2천2백만)', '')['value'] == 0
    assert guard(record('금액은 별도 규격서 참조', 입찰추정가격=20_000_000,
                        배정예산금액=22_000_000),
                 '예산=상이(추정가격2천만/배정예산2천2백만)', '') is None


def test_invalid_amount_claim_does_not_discard_an_independent_industry_claim():
    rec = record('기초금액: 22,000,000원(부가세 포함)\n'
                 '운송업(업종코드: 3456)으로 등록한 업체',
                 입찰추정가격=20_000_000, 배정예산금액=22_000_000,
                 면허업종제한목록='운송업(7890)')
    assert guard(rec, '예산=상이(추정가격2천만/배정예산2천2백만);'
                      '업종=상이(본문3456, 메타7890)', '') is None


@pytest.mark.parametrize('modifier', ['단가', '총액'])
def test_price_form_is_not_a_different_competition_method(modifier):
    rec = record(f'입찰방법: 제한({modifier})경쟁 / 전자입찰')
    assert comparison(rec, 'competition_method')['status'] == 'same'
    assert guard(rec, f'계약방법=상이(제한({modifier})경쟁 vs 제한경쟁)', '')['value'] == 0


def test_modifier_normalization_keeps_a_real_method_difference():
    rec = record('입찰방법: 일반(단가)경쟁입찰 / 전자입찰\n'
                 '시장조사용 견적서는 입찰 참가와 관계없는 참고 자료입니다.', 계약방법='제한경쟁')
    assert comparison(rec, 'competition_method')['status'] == 'different'
    assert positive_decision(rec, compare(rec))['value'] == 1
    assert guard(rec, '계약방법=상이(본문 일반(단가)경쟁, 메타 제한경쟁)') is None


@pytest.mark.parametrize('text', [
    '소액수의 견적제출 안내공고\n입찰방법: 제한경쟁입찰 방식을 준용합니다.',
    '입찰방법: 제한/일반경쟁입찰(총액제)',
    '견적제출 안내공고\n입찰방법: 제한경쟁(여성기업), 전자견적',
    '입찰방법: 제한경쟁\n예정가격 대비 제출\n된 견적가격 중 최저가격으로 결정합니다.',
])
def test_quotation_or_alternative_is_not_a_unique_competition_method(text):
    rec = record(text, 계약방법='수의계약')
    assert comparison(rec, 'competition_method')['status'] == 'method_relation_unresolved'
    assert positive_decision(rec, compare(rec)) is None
    assert guard(rec, '계약방법=상이(본문 제한경쟁, 메타 수의계약)', '')['value'] == 0


def test_title_method_conflict_does_not_suppress_its_independent_amount_band():
    rec = record('장비 운영(일반경쟁·1천만원미만)\n입찰방법: 제한경쟁', 계약방법='일반경쟁')
    assert comparison(rec, 'competition_method')['status'] == 'method_relation_unresolved'
    result = positive_decision(rec, compare(rec))
    assert result['reason'] == 'title_tag_metadata_difference'
    assert result['comparison']['band_difference'] is True


def test_method_only_title_fallback_does_not_restore_an_unresolved_method():
    rec = record('장비 운영(일반경쟁·1억원미만)\n입찰방법: 제한경쟁', 계약방법='제한경쟁')
    assert comparison(rec, 'competition_method')['status'] == 'method_relation_unresolved'
    assert positive_decision(rec, compare(rec)) is None


def test_wrapped_license_parenthesis_keeps_all_and_or_codes():
    rec = record('다음 각호 중 어느 하나에 해당하는 업체\n'
                 '1) 처리업(업종코드: 1234,\n\n처리시설을 갖춘 경우)과 운반업(업종코드: 5678) 허가를 받은 업체\n'
                 '2) 처리업(업종코드: 1234,\n\n장비를 갖춘 경우) 허가를 받은 업체',
                 면허업종제한목록='처리업(1234)')
    packet = compare(rec)
    assert comparison(rec, 'industry')['status'] == 'and_or_scope_unresolved'
    assert {f['value'] for f in packet['facts'] if f['field'] == 'industry'} == {'1234', '5678'}
    assert positive_decision(rec, packet) is None
    assert guard(rec, '업종=상이(본문5678, 메타1234)')['value'] == 0
    rec['meta']['면허업종제한목록'] = '검정기관(7890)'
    assert guard(rec, '업종=상이(메타는 검정기관이나 본문은 처리업 요구)') is None


def test_registered_unit_value_does_not_compare_to_project_total():
    rec = record('단가기초금액: 12,500원(부가세 포함)\n사업금액: 25,000,000원(부가세 포함)',
                 배정예산금액=12_500)
    assert comparison(rec)['status'] == 'amount_scope_unresolved'
    assert positive_decision(rec, compare(rec)) is None
    assert guard(rec, '예산=상이(본문25,000,000원, 메타12,500원)')['value'] == 0
    # A real changed total remains comparable even on a unit-price tender.
    rec['meta']['배정예산금액'] = 26_000_000
    assert comparison(rec)['status'] == 'different'


def test_other_named_project_matching_registered_budget_blocks_cross_project_comparison():
    rec = record('사업명: 도서관 교육\n사업예산: 80,000,000원(부가세 포함)',
                 '「박물관 교육」 수행기관 선정 입찰\n위탁규모) 총 120백만원(부가세 포함)',
                 배정예산금액=120_000_000)
    assert comparison(rec)['status'] == 'amount_scope_unresolved'
    rec['meta']['배정예산금액'] = 130_000_000
    assert comparison(rec)['status'] == 'different'
