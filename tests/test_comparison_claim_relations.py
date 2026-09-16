"""Explicit matches do not form an additional positive mismatch claim."""
import pytest
from submission.pps.comparison import compare, reject_bounded_amount_witness
from tests.test_comparison import record
from tests.test_comparison_witness import response

QUOTE='적격심사 세부심사기준 별표4(추정가격 5억원 미만의 용역)를 적용한다.'


@pytest.mark.parametrize('same', [
    '지역제한(메타 경기도/본문 경기도) 일치',
    '업종제한(메타 운송업/본문 운송업) 일치',
    '지역제한(메타 경기도/본문 경기도) 일치, 업종제한(메타 운송업/본문 운송업) 동일',
])
@pytest.mark.parametrize('order', [0,1])
def test_same_other_fields_do_not_hide_a_demonstrably_wrong_price_proof(same,order):
    wrong='추정가격(메타 50,000,000원/본문 5억원) 불일치'
    claim=', '.join((same,wrong) if order else (wrong,same))
    rec=record(QUOTE)
    result=reject_bounded_amount_witness(rec,{'v24':1,'e24':QUOTE},response(claim),compare(rec))
    assert result and result['value']==0 and result['absence_verified'] is False


@pytest.mark.parametrize('other', [
    '지역제한(메타 경기도/본문 서울특별시) 불일치',
    '지역제한은 일치하지 않는다',
    '지역제한은 확인할 수 없음',
    '지역제한(메타 경기도/본문 경기도) 일치하지만 업종은 다름',
    '지역제한과 추정가격 모두 메타와 다르다',
    '지역제한(메타 경기도/본문 서울특별시) 일치',
])
def test_other_differences_and_unknown_claims_are_not_dropped(other):
    rec=record(QUOTE)
    claim='추정가격(메타 5천만원/본문 5억원) 불일치, '+other
    assert reject_bounded_amount_witness(rec,{'v24':1,'e24':QUOTE},response(claim),compare(rec)) is None


def test_literal_amount_assignment_is_kept_even_with_other_fields_explicitly_matching():
    quote='추정가격: 80,000,000원'
    rec=record(quote)
    claim='추정가격(메타 5천만원/본문 8천만원) 불일치, 지역제한(메타 경기도/본문 경기도) 일치'
    assert reject_bounded_amount_witness(rec,{'v24':1,'e24':quote},response(claim),compare(rec)) is None


def test_a_denied_price_difference_is_not_parsed_as_an_asserted_difference():
    rec=record(QUOTE)
    assert reject_bounded_amount_witness(rec,{'v24':1,'e24':QUOTE},
        response('본문 추정가격은 메타와 다르지 않음.'),compare(rec)) is None
