"""The actual statutory contract route and whole amount govern production checks."""
import copy

import pytest

from submission.pps.knowledge import Knowledge
from submission.pps.production_exceptions import exception_observations
from submission.pps.production_scope import exception_claims, quote_requirement
from tests.test_catalog_predicate_consumption import purchase
from tests.test_service_identity import DATA


def notice(price=10_000_000, law='국가계약법', *, basis=True, extra='', method='수의계약'):
    rec = purchase('책상', '5610170301', [])
    rec['meta'].update(적용계약법=law, 계약방법=method, 입찰추정가격=price)
    cite = '제26조제1항제5호가목' if law == '국가계약법' else '제25조제1항제5호'
    route = f'본 계약은 {law} 시행령 {cite}에 따라 수의계약으로 체결합니다.' if basis and method == '수의계약' else ''
    title = '소액수의 견적제출 안내공고' if method == '수의계약' else '제한경쟁 입찰공고'
    rec['docs'][0]['text'] = '\n'.join([title, route, '1. 구매내역', '품명: 책상',
        f'추정가격: {price:,}원 (부가세 별도)', '2. 입찰참가자격', '일반 업체', extra, '3. 계약조건'])
    return rec


def consume(rec, value='0'):
    original = copy.deepcopy(rec)
    row, facts = Knowledge(DATA).qualification_decisions(rec, {'v10': value, 'e10': ''})
    assert rec == original
    return row, facts


@pytest.mark.parametrize('law', ['국가계약법', '지방계약법'])
@pytest.mark.parametrize('price', [9_999_999, 10_000_000, 50_000_000])
@pytest.mark.parametrize('baseline', ['0', '1'])
def test_specified_private_contract_threshold_is_applied_at_the_inclusive_boundary(law, price, baseline):
    rec = notice(price, law)
    row, facts = consume(rec, baseline)
    review = facts['qualification']['direct_production_quote_review']
    assert facts['product']['status'] == 'competition' and facts['qualification']['no_direct']
    assert row['v10'] == ('1' if price >= 10_000_000 else '0')
    assert review['basis_evidence'] and not review['waiver_certified']
    assert not review['catalog_designation_changed']


def test_low_amount_does_not_exempt_the_competitive_procurement_route():
    row, facts = consume(notice(9_999_999, method='제한경쟁'))
    assert row['v10'] == '1'
    assert 'direct_production_quote_review' not in facts['qualification']


@pytest.mark.parametrize('baseline', ['0', '1'])
def test_quote_title_and_metadata_alone_do_not_certify_the_statutory_subparagraph(baseline):
    row, facts = consume(notice(basis=False), baseline)
    assert row['v10'] == baseline and 'v10' not in facts['decisions']
    assert facts['deferred_decisions']['v10']['waiver_certified'] is False


@pytest.mark.parametrize('change', ['wrong_article', 'wrong_subparagraph', 'wrong_law',
    'conditional', 'reference_heading', 'quoted_multiline', 'contradictory_method'])
def test_unverified_legal_or_source_scope_cannot_create_a_new_positive(change):
    rec = notice()
    text = rec['docs'][0]['text']
    if change == 'wrong_article':
        text = text.replace('제26조', '제25조')
    elif change == 'wrong_subparagraph':
        text = text.replace('제5호가목', '제5호나목')
    elif change == 'wrong_law':
        rec['meta']['적용계약법'] = '지방계약법'
    elif change == 'conditional':
        text = text.replace('본 계약은', '낙찰자가 없는 경우 본 계약은')
    elif change == 'reference_heading':
        text = text.replace('본 계약은', '참고자료\n본 계약은')
    elif change == 'quoted_multiline':
        text = text.replace('본 계약은', '“\n본 계약은').replace('체결합니다.', '체결합니다.\n”')
    else:
        text += '\n계약방법: 제한경쟁'
    rec['docs'][0]['text'] = text
    row, facts = consume(rec)
    assert row['v10'] == '0' and 'v10' not in facts['decisions']
    assert facts['deferred_decisions']['v10']['reason'].endswith('unresolved')


def test_explicit_notice_price_controls_threshold_and_original_meta_remains_available():
    rec = notice(9_999_999)
    rec['meta']['입찰추정가격'] = 50_000_000
    row, facts = consume(rec, '1')
    price = facts['qualification']['direct_production_quote_review']['project_estimated_price']
    assert row['v10'] == '0'
    assert price['value_won'] == 9_999_999 and price['source_conflict']
    assert rec['meta']['입찰추정가격'] == 50_000_000


def test_conflicting_notice_totals_do_not_resolve_from_the_registered_value():
    rec = notice(9_999_999)
    rec['docs'][0]['text'] += '\n추정가격: 50,000,000원 (부가세 별도)'
    row, facts = consume(rec, '1')
    assert row['v10'] == '1' and 'v10' not in facts['decisions']
    assert facts['qualification']['direct_production_quote_review']['reason'] == 'whole_contract_estimated_price_unresolved'


def test_an_item_amount_assertion_never_replaces_the_whole_contract_amount():
    rec = notice(50_000_000, extra='본 사업의 책상은 추정가격 1천만 원 미만으로 직접생산확인증명서를 별도로 요구하지 않습니다.')
    row, facts = consume(rec)
    review = facts['qualification']['direct_production_quote_review']
    assert row['v10'] == '1' and review['project_estimated_price']['value_won'] == 50_000_000
    claims = review['exception_claims']
    assert claims[0]['named_item_candidates'] == [{'code': '5610170301', 'name': '책상'}]
    assert claims[0]['claimed_amounts'][0]['operator'] == '미만'
    assert not claims[0]['claimed_amounts'][0]['project_total_certified']


def test_other_item_exception_does_not_rename_the_current_purchase_or_its_total():
    rec = notice(extra='드론은 추정가격 1천만원 미만으로 직접생산확인증명서를 별도로 요구하지 않습니다.')
    row, facts = consume(rec)
    claim = facts['qualification']['direct_production_quote_review']['exception_claims'][0]
    assert row['v10'] == '1' and facts['product']['status'] == 'competition'
    assert not claim['named_item_candidates'] and not claim['item_identity_certified']


def test_a_special_procurement_regime_claim_is_preserved_for_further_review():
    rec = notice(extra='판로지원법 시행령 제7조제1항제4호를 적용하여 직접생산확인품목에서 제외합니다.')
    row, facts = consume(rec)
    assert row['v10'] == '0' and 'v10' not in facts['decisions']
    review = facts['qualification']['direct_production_quote_review']
    assert review['reason'] == 'disclosed_procurement_regime_exception_unresolved'
    assert not review['catalog_designation_changed'] and not review['waiver_certified']


def test_provided_exception_notice_document_is_not_dropped_by_its_type():
    rec = notice()
    rec['docs'].append({'doc_id': 'notice-exception', 'type': '예외공표서',
        'text': '판로지원법 시행령 제7조제1항제4호를 적용하여 직접생산확인품목에서 제외합니다.'})
    assert exception_observations(rec)[0]['evidence']['doc_type'] == '예외공표서'
    row, facts = consume(rec)
    assert row['v10'] == '0' and facts['deferred_decisions']['v10']


def test_incomplete_input_still_cannot_establish_absence_after_applicability_is_known():
    rec = notice()
    rec['input_completeness']['완전관측'] = False
    rec['dropped_doc_counts'] = {'규격서': 1}
    row, facts = consume(rec)
    assert row['v10'] == '0' and not facts['qualification']['no_direct']
    assert 'v10' not in facts['decisions']


def test_source_claim_amount_keeps_fractional_won_as_an_unresolved_literal():
    rec = notice(extra='책상 추정가격 0.5원으로 직접생산확인증명서를 면제합니다.')
    claim = exception_claims(rec, {'products': [{'name': '책상', 'code': '5610170301'}]})[0]
    assert claim['claimed_amounts'][0]['value_won'] is None
    assert claim['claimed_amounts'][0]['literal_error']
