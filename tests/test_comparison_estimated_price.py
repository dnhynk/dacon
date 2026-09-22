"""The registered estimate is a comparable field only as a whole-contract price."""
import pytest

from submission.pps.comparison import compare, positive_decision
from tests.test_comparison import comparison, record


def test_estimated_price_difference_is_an_independent_positive():
    rec = record('추정가격 : 45,000,000원', 입찰추정가격=50_000_000)
    assert comparison(rec, 'estimated_price')['status'] == 'different'
    decision = positive_decision(rec, compare(rec))
    assert decision['value'] == 1
    assert decision['comparison']['field'] == 'estimated_price'
    assert '45,000,000' in decision['evidence']


def test_matching_or_one_won_estimate_is_never_a_positive():
    for text in ('추정가격 : 50,000,000원', '추정가격 : 49,999,999원'):
        rec = record(text, 입찰추정가격=50_000_000)
        assert positive_decision(rec, compare(rec)) is None


@pytest.mark.parametrize('notice', [
    '나. 계약방법 : 전자입찰, 단가계약, 소액수의\n다. 기초금액 : 금500,000원(추정가격 금454,545원, 부가세 45,455원)',
    '기초금액 : 금500,000원/톤(단가)\n추정가격 : 454,545원',
    '기초금액 금500,000원 - 1일 단가\n추정가격 : 454,545원',
    '단가로 견적서를 제출하여야 합니다.(단가계약)\n추정가격 : 454,545원',
])
def test_unit_priced_notice_states_no_whole_contract_estimate(notice):
    rec = record(notice, 입찰추정가격=50_000_000)
    assert comparison(rec, 'estimated_price')['status'] == 'different'
    assert positive_decision(rec, compare(rec)) is None


def test_registered_estimate_written_in_the_notice_is_agreement_not_conflict():
    rec = record('예비가격기초금액(추정가격) : 금55,000,000원 (금50,000,000원)',
                 입찰추정가격=50_000_000)
    assert positive_decision(rec, compare(rec)) is None


def test_named_threshold_reference_does_not_block_an_assigned_estimate():
    rec = record('※ 추정가격: 45,000,000원 부가가치세: 4,500,000원\n'
                 '[별표 3]추정가격이 고시금액 미만인 물품 제조 또는 구매 입찰 평가기준을 적용함.',
                 입찰추정가격=50_000_000)
    assert comparison(rec, 'estimated_price')['status'] == 'extraction_unresolved'
    decision = positive_decision(rec, compare(rec))
    assert decision['value'] == 1 and decision['comparison']['criterion_reference_only']
    assert '45,000,000' in decision['evidence']


def test_unitless_estimate_is_read_only_when_its_vat_fixes_the_unit():
    rec = record('※ 추정가격: 45,000,000원 부가가치세: 4,500,000원\n'
                 '라. 추정금액 : 금49,500,000원(추정가격45,000,000 / 부가가치세4,500,000)',
                 입찰추정가격=50_000_000)
    assert comparison(rec, 'estimated_price')['status'] == 'extraction_unresolved'
    decision = positive_decision(rec, compare(rec))
    assert decision['value'] == 1 and decision['comparison']['vat_paired_estimate']


@pytest.mark.parametrize('assignment', [
    '라. 추정금액 : 금49,500,000원(추정가격45,000,000)',
    '라. 추정금액 : 금49,500,000원(추정가격45,000,000 / 부가가치세4,000,000)',
    '라. 추정금액 : 금49,500,000원(추정가격45,000 / 부가가치세4,500)천원',
])
def test_unreadable_assignment_still_abstains(assignment):
    rec = record('※ 추정가격: 45,000,000원 부가가치세: 4,500,000원\n' + assignment,
                 입찰추정가격=50_000_000)
    assert comparison(rec, 'estimated_price')['status'] == 'extraction_unresolved'
    assert positive_decision(rec, compare(rec)) is None


def test_unit_written_after_the_vat_pair_is_not_read_as_won():
    rec = record('라. 추정금액(추정가격45,000 / 부가가치세4,500)천원', 입찰추정가격=50_000_000)
    assert positive_decision(rec, compare(rec)) is None


def test_evaluation_basis_gloss_assigns_no_estimate():
    rec = record('라. 추정금액 : 금49,500,000원(추정가격45,000,000 / 부가가치세4,500,000)\n'
                 '가. 용역수행금액 평가기준(부가가치세 제외 추정가격 기준)\n'
                 '- 수집운반업 : 22,500,000원', 입찰추정가격=50_000_000)
    decision = positive_decision(rec, compare(rec))
    assert decision['value'] == 1 and '45,000,000' in decision['evidence']


def test_vat_marked_estimate_keeps_the_basis_abstention():
    rec = record('추정가격 : 45,000,000원(부가가치세 포함)\n'
                 '추정가격이 고시금액 미만인 입찰에 참여하는 소기업에 가점을 부여한다.',
                 입찰추정가격=50_000_000)
    assert positive_decision(rec, compare(rec)) is None


def test_attachment_only_estimate_stays_with_the_model():
    rec = record('금액은 제안요청서를 참고한다.', '추정가격 : 45,000,000원',
                 입찰추정가격=50_000_000)
    assert comparison(rec, 'estimated_price')['status'] == 'different'
    assert positive_decision(rec, compare(rec)) is None
