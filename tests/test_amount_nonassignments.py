"""A price's use in a rule is not a failed observation of its numeric value."""
import copy

import pytest

from submission.pps.comparison import amount_facts, compare
from submission.pps.prices import project_prices
from tests.test_comparison import record


@pytest.mark.parametrize('mention', [
    '사업예산 및 입찰금액은 부가가치세를 포함한 가격입니다.',
    '본 사업예산은 부가가치세가 포함된 금액이므로 부가가치세를 포함하여 투찰하여야 한다.',
    '입찰가격은 사업예산 범위 내여야 한다.',
    '입찰가격이 해당 사업예산 이하인 자를 협상적격자로 선정한다.',
    '입찰가격이 사업예산의 100분의 70 미만인 경우 가격점수 배점한도의 30%를 부여한다.',
    '사업금액은 전체 사업기간을 기준으로 산출되었으며 전체 기간의 금액으로 입찰한다.',
    '사업예산, 기초금액 및 입찰금액은 부가가치세를 포함한 가격이다.',
])
def test_known_explanatory_use_cannot_veto_an_independent_exact_budget(mention):
    text = '사업예산: 88,000,000원 (부가세 포함)\n\n' + mention
    rec = record(text, 배정예산금액=77_000_000)
    original = copy.deepcopy(rec)
    packet = compare(rec)
    comparison = next(c for c in packet['comparisons'] if c['field'] == 'budget')
    assert comparison['status'] == 'different'
    assert project_prices(rec)['budget']['value_won'] == 88_000_000
    # Classifying the use does not delete its original source observation.
    facts = amount_facts(rec)
    assert any(f['value'] is None and f['start'] >= text.index(mention) for f in facts)
    assert all(rec['docs'][f['doc_index']]['text'][f['start']:f['end']] for f in facts)
    assert rec == original


@pytest.mark.parametrize('statement', [
    '사업예산: 55,00원 (부가세 포함)',
    '사업예산: 2억1조원 (부가세 포함)',
    '사업예산: (99,000,000원 (부가세 포함)',
    '사업예산: 금액은 별도 제안요청서에서 정한다.',
    '사업예산: 부가가치세가 포함된 99,000,000원',
    '사업예산(원) | 구분\n99,000,000 | 1차\n110,000,000 | 2차',
])
def test_unresolved_numeric_or_external_assignment_still_blocks_comparison(statement):
    rec = record('사업예산: 88,000,000원 (부가세 포함)', statement,
                 배정예산금액=77_000_000)
    comparison = next(c for c in compare(rec)['comparisons'] if c['field'] == 'budget')
    assert comparison['status'] not in {'same', 'different'}


def test_a_vat_sentence_does_not_hide_its_own_conflicting_literal():
    rec = record('사업예산: 88,000,000원 (부가세 포함)',
                 '사업예산: 99,000,000원. 부가가치세를 포함한 금액이다.',
                 배정예산금액=77_000_000)
    comparison = next(c for c in compare(rec)['comparisons'] if c['field'] == 'budget')
    assert comparison['status'] not in {'same', 'different'}


def test_a_definition_alone_does_not_create_a_comparable_number():
    rec = record('사업예산은 부가가치세가 포함된 금액이다.')
    assert not any(f['value'] is not None for f in amount_facts(rec))
    assert next(c for c in compare(rec)['comparisons'] if c['field'] == 'budget')['status'] not in {'same', 'different'}


def test_unique_same_field_tax_description_resolves_budget_basis_for_consumers():
    from submission.pps.other_checks import budget_facts
    text = ('사업금액: 500,000,000원\n'
            '본 사업금액은 부가가치세가 포함된 금액이므로 부가가치세를 포함하여 투찰한다.')
    rec = record(text, 배정예산금액=500_000_000)
    price = project_prices(rec)['budget']
    numeric = next(f for f in price['body'] if f['won'] == 500_000_000)
    assert numeric['vat'] == 'included'
    assert numeric['basis_relation'] == 'same_document_unique_field_tax_description'
    assert budget_facts(rec)['effective_won'] == '500000000'


def test_tax_description_does_not_guess_between_repeated_same_field_amounts():
    from submission.pps.other_checks import budget_facts
    rec = record('사업금액: 500,000,000원\n사업금액: 600,000,000원\n'
                 '본 사업금액은 부가가치세가 포함된 금액이다.')
    assert all(f['vat'] == 'unspecified' for f in project_prices(rec)['budget']['body'] if f['won'])
    assert budget_facts(rec)['effective_won'] is None


def test_tax_description_cannot_cross_field_labels():
    from submission.pps.other_checks import budget_facts
    rec = record('사업예산: 500,000,000원\n본 사업금액은 부가가치세가 포함된 금액이다.')
    assert budget_facts(rec)['effective_won'] is None
