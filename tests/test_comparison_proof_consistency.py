"""Typed source relations reject only demonstrably non-comparable v24 proofs."""
import json

import pytest

from submission.pps.comparison import compare, reject_unsupported_comparison_claim
from tests.test_comparison import record


def response(claim):
    return {'text': json.dumps({'facts': {'본문과메타의동일필드차이': claim}}, ensure_ascii=False)}


def guard(rec, claim, evidence='선택된 모델 근거'):
    return reject_unsupported_comparison_claim(
        rec, {'v24': 1, 'e24': evidence}, response(claim), compare(rec))


def test_region_flag_is_not_a_registered_region_value():
    rec = record('주된 영업소 소재지가 제주특별자치도에 있는 업체',
                 지역제한여부='N', 제한지역코드목록=None,
                 면허업종제한목록='기타자유업(행사대행업)(9901)')
    claim = ('지역제한은 본문에서 제주특별자치도로 제한하나 메타에는 지역제한여부 N으로 표시됨, '
             '업종은 본문과 메타가 일치함')
    result = guard(rec, claim)
    assert result['value'] == 0 and result['absence_verified'] is False
    assert result['reason'] == 'model_compared_region_flag_as_registered_region'


def test_compact_region_flag_wording_is_still_a_flag_not_a_region_value():
    rec = record('주된 영업소 소재지가 서울특별시에 있는 업체',
                 지역제한여부='N', 제한지역코드목록=None)
    result = guard(rec, '지역=상이(메타 N, 본문 서울특별시 제한)')
    assert result['reason'] == 'model_compared_region_flag_as_registered_region'


def test_industry_flag_is_not_a_registered_industry_value():
    rec = record('기타자유업(업종코드:9901)으로 등록한 자',
                 업종제한여부='N', 면허업종제한목록=None)
    result = guard(rec, '업종=상이(메타는 업종제한 N이나 본문은 9901 등록을 요구)')
    assert result['reason'] == 'model_compared_industry_flag_as_registered_industry'


def test_missing_metadata_is_not_a_conflicting_industry_value():
    rec = record('학술연구용역(업종코드:1169)으로 등록한 자',
                 업종제한여부='N', 면허업종제한목록=None)
    result = guard(rec, '업종=상이(메타는 미입력이나 본문은 학술연구용역 1169 요구)')
    assert result['reason'] == 'model_treated_missing_metadata_as_conflicting_value'


def test_project_total_is_not_the_component_tender_budget():
    rec = record('사업예산: 100,000,000원(부가세 포함)\n'
                 '입찰대상금액: 30,000,000원(부가세 포함)', 배정예산금액=30_000_000)
    claim = ('배정예산금액(메타 30,000,000원 vs 제안요청서 100,000,000원/'
             '30,000,000원)이 상이함')
    result = guard(rec, claim)
    assert result['reason'] == 'model_compared_project_total_as_tender_budget'


@pytest.mark.parametrize('claim', [
    '예산=상이(본문 8억/메타 8억이나 부가가치세 기준 차이 가능)',
    '예산=상이(본문 61,823,000원/메타 61,823,000원)',
    '예산=상이(본문 5,450,000,000원/메타 5,450,000,001원)',
])
def test_equal_or_one_won_rounded_amounts_do_not_prove_a_difference(claim):
    rec = record('사업예산: 800,000,000원(부가세 포함)', 배정예산금액=800_000_000)
    result = guard(rec, claim)
    assert result['reason'] == 'model_asserted_difference_between_equal_amounts'


def test_contract_method_is_not_the_award_method():
    rec = record('계약방법: 일반경쟁\n낙찰방법: 협상에 의한 계약', 계약방법='일반경쟁')
    result = guard(rec, '계약방법=상이(일반경쟁 vs 협상에 의한 계약)')
    assert result['reason'] == 'model_compared_contract_method_to_nonmetadata_method'


def test_missing_citation_does_not_bypass_a_demonstrably_invalid_claim():
    rec = record('계약방법: 일반경쟁\n낙찰방법: 협상에 의한 계약', 계약방법='일반경쟁')
    result = guard(rec, '계약방법=상이(일반경쟁 vs 협상에 의한 계약)', evidence='')
    assert result['reason'] == 'model_compared_contract_method_to_nonmetadata_method'
    assert result['rejected_witness'] == ''


def test_source_to_source_method_conflict_is_not_a_metadata_comparison():
    rec = record('계약방법: 제한경쟁', 계약방법='제한경쟁')
    result = guard(rec, '계약방법=상이(본문 제한경쟁 vs 제안요청서 일반경쟁)')
    assert result['reason'] == 'model_compared_contract_method_to_nonmetadata_method'


def test_vague_industry_list_wording_does_not_resolve_and_or_scope():
    rec = record('업종코드 5210 또는 업종코드 5246으로 등록한 자',
                 면허업종제한목록='식품판매업(5210)', 업종제한여부='Y')
    result = guard(rec, '업종=상이(메타는 특정 업종 조합이나 본문은 개별 업종을 나열)')
    assert result['reason'] == 'model_asserted_unresolved_industry_scope_as_difference'


@pytest.mark.parametrize('notice,claim', [
    ('적격심사 세부심사기준 별표4(추정가격 5억원 미만의 용역)를 적용한다.',
     '추정가격은 공고문에 5억원 미만이나 메타 및 기초금액 6,600만원과 불일치한다.'),
    ('추정가격 1억원 이하 업체의 수의계약\n추정가격: 60,000,000원',
     '추정가격(메타 6천만원, 본문 6천만원)은 동일함. '
     '단 메타 입찰추정가격 1억원과 본문 6천만원이 상이함.'),
])
def test_statutory_band_is_not_a_literal_notice_price(notice, claim):
    rec = record(notice, 입찰추정가격=60_000_000)
    result = guard(rec, claim, notice)
    assert result['reason'] == 'model_compared_statutory_bound_as_literal_price'


@pytest.mark.parametrize('notice,meta,claim', [
    ('계약방법: 일반경쟁', {'계약방법': '제한경쟁'},
     '계약방법이 메타 제한경쟁과 본문 일반경쟁으로 상이하다.'),
    ('사업예산: 66,000,000원(부가세 포함)', {'배정예산금액': 55_000_000},
     '배정예산이 메타 55,000,000원과 본문 66,000,000원으로 다르다.'),
    ('본점 소재지를 경기도 또는 제주도에 둔 업체', {'제한지역코드목록': '경기도', '지역제한여부': 'Y'},
     '지역 범위가 메타 경기도와 본문 경기도 또는 제주도로 상이하다.'),
    ('식품판매업(업종코드:5210)으로 등록한 자', {'면허업종제한목록': '식품판매업(5246)'},
     '면허업종이 메타 5246과 본문 5210으로 상이하다.'),
])
def test_independent_typed_source_difference_always_preserves_the_positive(notice, meta, claim):
    rec = record(notice, **meta)
    assert any(x['status'] == 'different' for x in compare(rec)['comparisons'])
    assert guard(rec, claim, notice) is None


def test_an_additional_unresolved_mismatch_claim_is_not_silently_dropped():
    rec = record('사업예산: 100,000,000원(부가세 포함)\n'
                 '입찰대상금액: 30,000,000원(부가세 포함)', 배정예산금액=30_000_000)
    claim = ('배정예산금액(메타 30,000,000원/사업 전체 100,000,000원)이 상이하며, '
             '수량도 메타와 본문이 다르다.')
    assert guard(rec, claim) is None


def test_matching_fields_without_a_demonstrably_invalid_difference_do_not_certify_absence():
    rec = record('추정가격: 60,000,000원', 입찰추정가격=60_000_000)
    assert guard(rec, '추정가격은 메타와 본문 모두 60,000,000원으로 동일함.') is None
