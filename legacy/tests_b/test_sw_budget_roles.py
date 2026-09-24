"""SW applicability must consume typed whole-budget facts, not a second regex."""
from decimal import Decimal
import pytest

from submission.pps.other_checks import budget_facts
from tests.test_other_checks import notice


@pytest.mark.parametrize('text',[
    '사업예산: 20억원 (부가세 포함)이 아니다.',
    '예시 사업예산: 20억원 (부가세 포함)',
    '1차 사업예산: 20억원 (부가세 포함)',
    '연간 사업예산: 20억원 (부가세 포함)',
    '평균 사업예산: 20억원 (부가세 포함)',
    '사업예산: 2,388원/ℓ (부가세 포함, 단가)',
])
def test_nonwhole_or_nonasserted_amount_cannot_become_the_sw_project_budget(text):
    result=budget_facts(notice(text))
    assert result['project_won'] is None
    assert result['effective_won'] is None and result['band'] is None


def test_explicit_budget_alias_with_included_vat_is_the_same_assigned_amount():
    result=budget_facts(notice('기초금액(사업예산): 20억원 (부가세 포함)'))
    assert Decimal(result['project_won'])==2_000_000_000


def test_unrelated_certificate_tax_comment_does_not_erase_the_actual_budget():
    text='사업예산: 20억원 (부가세 포함)\n면세사업자는 입찰 시 부가세 제외 여부를 확인한다.'
    result=budget_facts(notice(text))
    assert Decimal(result['project_won'])==2_000_000_000


def test_whole_budget_and_later_partial_amount_are_not_conflicting_totals():
    text='총 사업예산: 40억원 (부가세 포함)\n\n1차 사업예산: 20억원 (부가세 포함)'
    result=budget_facts(notice(text))
    assert Decimal(result['project_won'])==4_000_000_000
    assert not result['conflict']


def test_withdrawn_prior_amount_does_not_conflict_with_current_budget():
    text='기존 사업예산: 40억원 (부가세 포함)이라는 금액은 철회한다.\n\n사업예산: 20억원 (부가세 포함)'
    result=budget_facts(notice(text))
    assert Decimal(result['project_won'])==2_000_000_000
    assert not result['conflict']


def test_full_total_keeps_its_role_when_the_next_line_names_a_stage_budget():
    from submission.pps.comparison import amount_facts
    rec=notice('총 사업예산: 40억원 (부가세 포함)\n1차 사업예산: 20억원 (부가세 포함)')
    observations=amount_facts(rec)
    assert [(o['value'],o['scope']) for o in observations]==[('4000000000','whole'),('2000000000','partial')]
    assert budget_facts(rec)['effective_won']=='4000000000'


def test_metadata_only_or_unstated_vat_never_supplies_the_sw_tax_basis():
    assert budget_facts(notice('',budget=2_000_000_000))['project_won'] is None
    assert budget_facts(notice('사업예산: 20억원'))['project_won'] is None


@pytest.mark.parametrize('bullet',['○ ','④ ','가. ','2) ','○ | '])
def test_new_named_contract_fields_do_not_negate_the_budget(bullet):
    text='사업예산: 20억원 (부가세 포함)\n'+bullet+'입찰 방식: 전자입찰\n'+bullet+'공동계약: 불가'
    result=budget_facts(notice(text))
    assert result['effective_won']=='2000000000'
    assert result['typed_budget_observations'][0]['scope']=='whole'


@pytest.mark.parametrize('wording,included',[
    ('부가가치세 및 대행수수료 포함',True),
    ('부가세, 이윤 및 운송비 포함',True),
    ('VAT 및 수수료 제외',False),
    ('부가세 제외, 수수료 포함',False),
    ('부가세 및 수수료 미포함',False),
])
def test_vat_coordinated_with_costs_keeps_its_own_shared_predicate(wording,included):
    result=budget_facts(notice('사업예산: 20억원 ('+wording+')'))
    assert (result['project_won']=='2000000000') is included
