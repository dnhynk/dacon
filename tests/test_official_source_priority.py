"""Official data contract: notice law/amounts take priority; registration is preserved."""
import copy
import pytest

from submission.pps.prices import project_prices, in_band
from submission.pps.comparison import compare, positive_decision
from submission.pps.legal_context import resolve_scope
from submission.pps.rules import joint_share_check
from submission.pps.other_checks import budget_facts
from tests.test_comparison import record


def test_notice_amount_overrides_registration_only_for_applicability():
    rec = record('추정가격: 90,000,000원\n사업예산: 99,000,000원(부가세 포함)',
                 입찰추정가격=240000000, 배정예산금액=264000000)
    before = copy.deepcopy(rec)
    prices = project_prices(rec)
    assert prices['estimated_price']['value_won'] == 90000000
    assert prices['estimated_price']['source_conflict']
    assert prices['estimated_price']['all_observed_values_won'] == [90000000, 240000000]
    assert in_band(prices['estimated_price'], upper=100000000) is True
    assert budget_facts(rec)['effective_won'] == '99000000'
    assert positive_decision(rec, compare(rec))['value'] == 1
    assert rec == before


def test_conflicting_notice_amounts_remain_unresolved_even_when_meta_matches_one():
    rec = record('추정가격: 90,000,000원\n추정가격: 150,000,000원', 입찰추정가격=90000000)
    prices = project_prices(rec)['estimated_price']
    assert prices['value_won'] is None
    assert prices['status'] == 'conflict'
    assert in_band(prices, upper=100000000) is None


def test_notice_priority_does_not_relabel_base_price_as_project_budget():
    rec = record('기초금액: 40,000,000원(부가세 포함)', 배정예산금액=55000000)
    assert project_prices(rec)['budget']['value_won'] == 55000000


def test_notice_price_retains_disagreeing_attachment_for_comparison():
    rec = record('사업예산: 55,000,000원(부가세 포함)', '사업예산: 66,000,000원(부가세 포함)')
    prices = project_prices(rec)['budget']
    assert prices['value_won'] == 55000000 and prices['source_conflict']
    assert len(prices['body']) == 2
    assert next(c for c in compare(rec)['comparisons'] if c['field'] == 'budget')['status'] == 'documents_conflict'


def test_explicit_notice_law_controls_actual_rule_and_keeps_meta_disagreement():
    rec = record('본 계약은 지방계약법을 적용한다.\n공동이행 구성원별 최소 지분율은 7% 이상이어야 한다.',
                 적용계약법='국가계약법', 업무구분='일반용역', 공동도급구성방식='공동이행')
    before = copy.deepcopy(rec)
    scope = resolve_scope(rec)
    assert scope['status'] == 'local' and scope['source_conflict']
    assert joint_share_check(rec)['value'] == 0
    assert rec == before


@pytest.mark.parametrize('text', ['참고: 본 계약은 지방계약법을 적용한다.',
    '지방계약법 시행령을 참고하여 제출 서류를 작성한다.'])
def test_legal_reference_is_not_an_operative_override(text):
    assert resolve_scope(record(text, 적용계약법='국가계약법'))['status'] == 'national'


def test_conflicting_operative_notice_laws_do_not_select_a_convenient_one():
    rec = record('본 계약은 지방계약법을 적용한다.\n본 계약은 국가계약법을 적용한다.')
    assert resolve_scope(rec)['status'] == 'conflict'
    assert joint_share_check(rec) is None
