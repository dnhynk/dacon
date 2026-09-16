"""Condition truth can be invariant even when the exact source price conflicts."""
import pytest

from submission.pps.qualification import catalog_condition, infer, software_catalog_prices
from submission.pps.knowledge import Knowledge
from tests.test_service_identity import DATA
from tests.test_other_checks import notice
from submission.pps.prices import project_prices

SPATIAL = ('1. 소프트웨어 진흥법 제48조 적용 2. 토지, 도시계획, 지하 시설물 등 지리정보를 전자매체로 '
           '제공하기 위한 측량, 탐사, 수치지도, 정사 영상 지도 제작 등의 기초 활동 포함')


@pytest.mark.parametrize('values,expected', [([5000000000, 5000000001], 'not_met'),
    ([700000000, 700000001], 'met'), ([1999999999, 2000000000], 'unknown')])
def test_software_band_uses_all_conflicting_amounts_without_selecting_one(values, expected):
    prices = {'candidate_values_won': values, 'unresolved_tax_basis': False, 'value_won': None, 'status': 'conflict'}
    result = catalog_condition('소프트웨어 진흥법 제48조 적용', None, None, budget_prices=prices)
    assert result['status'] == expected
    assert result['value_won'] is None
    assert result['candidate_values_won'] == values


def test_price_ceiling_uses_invariant_band_but_not_unknown_tax_basis():
    prices = {'candidate_values_won': [200000000, 200000001], 'unresolved_tax_basis': False}
    assert catalog_condition('추정가격 10억원 미만에 한함', None, None, estimate_prices=prices)['status'] == 'met'
    prices['unresolved_tax_basis'] = True
    assert catalog_condition('추정가격 10억원 미만에 한함', None, None, estimate_prices=prices)['status'] == 'unknown'


def test_known_scope_definition_is_not_an_additional_unresolved_condition():
    result = catalog_condition(SPATIAL, 700000000, 770000000)
    assert result['status'] == 'met'
    assert result['scope_definition'] == SPATIAL.split('2. ', 1)[1]
    assert not result['purchase_identity_certified']
    assert catalog_condition(SPATIAL+' 다만 특정 자격이 있어야 함', 700000000, 770000000)['status'] == 'not_evaluated'


def test_whole_software_family_does_not_turn_one_won_conflict_into_unknown_applicability():
    text = ('용역명: 경영정보시스템 구축 용역\n소요예산: 5,000,000,000원 (부가세 포함)\n'
            '1. 입찰참가자격\n소프트웨어사업자(컴퓨터관련서비스)로 등록한 업체\n2. 계약조건')
    rec = {'meta': {'업무구분': '일반용역', '적용계약법': '국가계약법', '배정예산금액': 5000000001,
                    '입찰추정가격': 4500000000}, 'docs': [{'doc_id': 'test', 'type': '공고문', 'text': text}],
            'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}
    knowledge = Knowledge(DATA); knowledge.detailed_product_facts(rec)
    row, facts = infer(rec, {'v10': '1', 'v11': '1'}, knowledge._product_facts)
    assert facts['product']['project_prices']['budget']['source_conflict']
    assert facts['product']['budget_won'] == 5000000000
    assert facts['product']['status'] == 'general'
    assert row['v10'] == row['v11'] == '0'


def test_software_catalog_uses_annual_average_for_long_maintenance():
    rec = notice('사업예산: 50억원 (부가세 포함)\n장기계속계약 소프트웨어 유지보수\n계약기간: 36개월')
    prices = software_catalog_prices(rec, project_prices(rec)['budget'])
    condition = catalog_condition('소프트웨어 진흥법 제48조 적용', None, 5000000000, budget_prices=prices)
    assert condition['status'] == 'met'
    assert condition['effective_scope'] == 'annual_average_SW_maintenance'
    assert condition['derived_band']['annualized']


@pytest.mark.parametrize('scope', [
    '소프트웨어사업과 다른 사업을 분리하여 발주한다.',
    '둘 이상의 소프트웨어사업을 일괄 발주한다.',
    '장기계속계약 소프트웨어 유지보수. 계약기간은 미정이다.',
])
def test_unresolved_component_or_annual_scope_blocks_catalog_band(scope):
    rec = notice('사업예산: 50억원 (부가세 포함)\n'+scope)
    prices = software_catalog_prices(rec, project_prices(rec)['budget'])
    assert catalog_condition('소프트웨어 진흥법 제48조 적용', None, 5000000000, budget_prices=prices)['status'] == 'unknown'
