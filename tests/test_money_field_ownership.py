"""Independent labelled fields cannot change a preceding amount's modality."""
import copy

import pytest

from submission.pps.comparison import amount_facts, compare, positive_decision
from submission.pps.prices import project_prices
from submission.pps.other_checks import budget_facts
from submission.pps.performance import performance_facts
from submission.pps.regions import above_ceiling_region_check, multiple_region_check
from submission.pps.temporal import v23
from tests.test_comparison import record
from tests.test_temporal import rec as temporal_record


@pytest.mark.parametrize('other', [
    '분할납품: 불가능',
    '○ | 분 할 납 품 : | 불가능',
    '○ | 분할납품 | 불가능',
    '| 장비대여: 미적용 |',
    '시험계약: 단가계약',
    '추가옵션: 본 사업에는 적용하지 않음',
])
@pytest.mark.parametrize('position', ['before','after'])
def test_independent_field_negation_or_unit_scope_stays_with_that_field(other, position):
    amount = '○ | 추 정 가 격 : | 금80,000,000원(금팔천만원, 부가가치세 제외)'
    text = '\n'.join((other,amount) if position=='before' else (amount,other))
    r = record(text, 입찰추정가격=150000000)
    original = copy.deepcopy(r)
    price = project_prices(r)['estimated_price']
    assert price['value_won']==80000000 and price['effective_source']=='notice'
    assert price['source_conflict'] and price['meta']['won']==150000000
    facts = amount_facts(r)
    assert next(f for f in facts if f['field']=='estimated_price')['scope']=='whole'
    comparison = next(c for c in compare(r)['comparisons'] if c['field']=='estimated_price')
    assert comparison['status']=='different' and comparison['metadata']==150000000
    assert r==original


@pytest.mark.parametrize('suffix', [
    '\n수요기관: [기관(공공기관)]\n분할납품: 불가',
    '\n○ | 수 요 기 관 : | [기관(공공기관)]\n○ | 분 할 납 품 : | 불가',
    ' | 분할납품: 불가',
    ' ; 분할납품: 불가',
])
def test_budget_consumers_keep_the_notice_amount_and_its_own_tax_basis(suffix):
    r = record('사업예산: 88,000,000원(부가세 포함)'+suffix, 배정예산금액=165000000)
    prices = project_prices(r)
    assert prices['budget']['value_won']==88000000
    assert budget_facts(r)['effective_won']=='88000000'
    assert positive_decision(r,compare(r))['value']==1


@pytest.mark.parametrize('text', [
    '예시:\n사업예산: 88,000,000원(부가세 포함)\n분할납품: 불가',
    '사업예산: 88,000,000원(부가세 포함)\n위 금액: 적용하지 않는다.',
    '사업예산: 88,000,000원(부가세 포함)\n비고: 위 금액은 본 계약에 적용하지 않는다.',
    '사업예산: 88,000,000원(부가세 포함)\n금액조건: 월별 단가',
    '사업예산: 88,000,000원(부가세 포함)\n참고사항: 예시 금액이다.',
    '사업예산: 88,000,000원(부가세 포함)\n적용조건: 위 금액은 작성 예시이다.',
    '사업예산: 88,000,000원(부가세 포함; 효력: 본 계약에는 적용하지 않음)',
    '사업예산: 88,000,000원(부가세 포함\n효력: 본 계약에는 적용하지 않음)',
])
def test_governing_amount_conditions_and_references_cannot_be_cut_away(text):
    r = record(text, 배정예산금액=None)
    assert project_prices(r)['budget']['value_won'] is None
    assert budget_facts(r)['effective_won'] is None


def test_a_vat_continuation_stays_with_its_amount_before_the_next_field():
    r = record('사업예산: 88,000,000원\n(부가세 포함)\n분할납품: 불가')
    assert budget_facts(r)['effective_won']=='88000000'


def test_real_notice_amount_conflict_is_not_erased_by_table_field_boundaries():
    r = record('추정가격: 80,000,000원\n분할납품: 불가\n추정가격: 150,000,000원',
               입찰추정가격=150000000)
    p = project_prices(r)['estimated_price']
    assert p['status']=='conflict' and p['value_won'] is None


def test_unreadable_assignment_does_not_inherit_the_next_fields_negation():
    r = record('추정가격: 8,00,000원\n분할납품: 불가', 입찰추정가격=150000000)
    p = project_prices(r)['estimated_price']
    assert p['value_won'] is None and p['unresolved_literal']


def test_actual_briefing_interval_consumer_uses_notice_band_with_disagreeing_meta():
    r = temporal_record('추정가격: 80,000,000원\n분할납품: 불가\n\n'
        '사업설명회: 2026. 1. 15.\n\n제안서 제출 마감: 2026. 1. 31.', 입찰추정가격=150000000)
    d = v23(r)
    assert d['value']==0
    assert next(f for f in d['facts'] if f['kind']=='calculation')['required_days']==10


def test_actual_performance_consumer_does_not_take_the_larger_registered_amount():
    r = record('추정가격: 80,000,000원\n분할납품: 불가\n'
        '사업예산: 88,000,000원(부가세 포함)\n장비대여: 미적용\n\n'
        '2. 입찰 참가자격\n가. 단일 용역 수행 실적 2억원 이상이 있는 업체',
        입찰추정가격=300000000,배정예산금액=330000000,업무구분='일반용역',적용계약법='국가계약법',소관구분='국가기관')
    d = performance_facts(r)['overlays']
    assert d['v2']['value']==1 and d['v3']['value']==1


def test_actual_region_consumers_switch_using_the_notice_price():
    r = record('추정가격: 80,000,000원\n분할납품: 불가\n\n'
        '2. 입찰참가자격\n법인등기부상 본점 소재지를 경기도 또는 충청남도에 둔 업체이어야 한다.',
        입찰추정가격=300000000,업무구분='일반용역',적용계약법='국가계약법',계약방법='제한경쟁')
    r.update(input_completeness={'완전관측':True},dropped_doc_counts={})
    assert above_ceiling_region_check(r) is None
    assert multiple_region_check(r)['value']==1


def test_sme_and_qualification_consumers_share_the_notice_band():
    from submission.pps.knowledge import Knowledge
    from submission.pps.qualification import infer
    from submission.pps.sme import extract_sme_facts
    from tests.test_service_identity import DATA
    r = record('용역명: 농림수산연구조사서비스\n추정가격: 80,000,000원\n분할납품: 불가\n\n'
        '2. 입찰참가자격\n가. 중소기업확인서를 소지한 업체이어야 합니다.\n3. 기타사항',
        입찰추정가격=150000000,업무구분='일반용역',적용계약법='국가계약법',계약방법='제한경쟁',
        세부품명번호목록='농림수산연구조사서비스[7010150001]')
    r.update(input_completeness={'완전관측':True},dropped_doc_counts={})
    knowledge = Knowledge(DATA); knowledge.detailed_product_facts(r)
    row, facts = infer(r,{'v17':'0','e17':''},knowledge._product_facts)
    assert facts['product']['estimate_won']==80000000
    assert row['v17']=='1'
    legacy = extract_sme_facts(r,knowledge._product_facts)
    assert legacy['price']['effective_won']==80000000
    assert legacy['price']['meta_value_krw']==150000000


def test_catalog_price_condition_uses_the_same_notice_value():
    from submission.pps.qualification import catalog_condition
    r = record('추정가격: 800,000,000원\n분할납품: 불가',입찰추정가격=1500000000)
    price = project_prices(r)['estimated_price']
    d = catalog_condition('추정가격 10억원 미만에 한함',price['value_won'],None,estimate_prices=price)
    assert d['status']=='met'
    assert d['candidate_values_won']==[800000000]
