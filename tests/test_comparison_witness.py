"""An eligibility price threshold is not a literal notice/metadata difference."""
import json
import pytest

from submission.pps.comparison import compare, reject_bounded_amount_witness
from submission.pps.rules import apply_rules
from tests.test_comparison import record


def response(claim='메타의 추정가격 5천만원과 본문 추정가격 5억원이 상이하다.'):
    return {'text':json.dumps({'facts':{'본문과메타의동일필드차이':claim}},ensure_ascii=False)}


def test_quoted_statutory_range_is_rejected_without_an_absence_claim():
    quote='적격심사 세부심사기준 별표4(추정가격 5억원 미만의 폐기물처리용역)를 적용한다.'
    rec=record(quote)
    original={'v24':1,'e24':quote}
    guard=reject_bounded_amount_witness(rec,original,response(),compare(rec))
    assert guard['value']==0 and guard['semantic_value'] is None
    assert guard['absence_verified'] is False and guard['rejected_witness']==quote
    assert original=={'v24':1,'e24':quote}


def test_a_literal_price_difference_is_never_rejected_as_a_threshold():
    quote='추정가격: 90,000,000원'
    rec=record(quote)
    assert reject_bounded_amount_witness(rec,{'v24':1,'e24':quote},response(),compare(rec)) is None


def test_other_literal_price_difference_preserves_the_positive_observation():
    quote='적격심사 세부심사기준 별표4(추정가격 5억원 미만의 폐기물처리용역)를 적용한다.'
    rec=record('추정가격: 90,000,000원\n\n'+quote)
    assert reject_bounded_amount_witness(rec,{'v24':1,'e24':quote},response(),compare(rec)) is None


def test_a_multi_field_claim_is_not_reduced_to_its_price_fragment():
    quote='적격심사 세부심사기준 별표4(추정가격 5억원 미만의 폐기물처리용역)를 적용한다.'
    rec=record(quote)
    claim='메타와 본문 추정가격이 상이하며, 본점 지역 제한도 다르다.'
    assert reject_bounded_amount_witness(rec,{'v24':1,'e24':quote},response(claim),compare(rec)) is None


def test_same_quote_with_a_different_source_role_remains_unresolved():
    quote='추정가격 5억원'
    rec=record('적격심사 기준: '+quote+' 미만의 용역\n\n'+quote+'으로 공고한다.')
    assert reject_bounded_amount_witness(rec,{'v24':1,'e24':quote},response(),compare(rec)) is None


def test_missing_quote_or_unparsed_other_price_never_invents_a_disproof():
    quote='적격심사 기준 별표4(추정가격 5억원 미만의 용역)를 적용한다.'
    rec=record('추정가격: 금 구천만원\n\n'+quote)
    assert reject_bounded_amount_witness(rec,{'v24':1,'e24':quote},response(),compare(rec)) is None
    assert reject_bounded_amount_witness(rec,{'v24':1,'e24':''},response(),compare(rec)) is None


def test_independent_source_difference_is_applied_after_rejecting_bad_model_proof():
    quote='적격심사 기준 별표4(추정가격 5억원 미만의 용역)를 적용한다.'
    rec=record('사업예산: 99,000,000원 (부가세 포함)\n\n'+quote)
    packet=compare(rec)
    guard=reject_bounded_amount_witness(rec,{'v24':1,'e24':quote},response(),packet)
    assert guard['value']==0
    row,trace=apply_rules(rec,{'v24':0,'e24':''},comparison=packet,items=(24,))
    assert row['v24']==1 and '99,000,000원' in row['e24']


@pytest.mark.parametrize('actual_difference', [False,True])
def test_normal_response_consumer_checks_the_claim_then_keeps_independent_positive(actual_difference):
    from submission.pps.pipeline import _response_row
    from submission.pps.prompts import Config, fact_fields
    from submission.pps.retrieval import Span
    quote='적격심사 기준 별표4(추정가격 5억원 미만의 용역)를 적용한다.'
    prefix='사업예산: 99,000,000원 (부가세 포함)\n\n' if actual_difference else ''
    rec=record(prefix+quote)
    facts=dict.fromkeys(fact_fields((24,)),'확인 불가')
    facts['본문과메타의동일필드차이']='메타 추정가격과 본문 추정가격 5억원이 상이하다.'
    observed={'text':json.dumps({'facts':facts,'judgments':{'v24':{'reason':'금액이 다름','v':1,'e':1}}},ensure_ascii=False)}
    prompt={'spans':[Span(0,'공고문',len(prefix),len(prefix)+len(quote),quote)]}
    row,details=_response_row(rec,observed,prompt,(24,),
        Config(mode='evidence_first',rule_checks=True,cross_source_facts=True),None,(24,))
    assert row['v24']==int(actual_difference)
    assert any(d.get('reason')=='model_compared_statutory_bound_as_literal_price' for d in details)
