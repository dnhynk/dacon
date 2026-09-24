"""Money assignments retain aliases and their own assertion boundaries."""
import pytest

from submission.pps.comparison import amount_facts
from submission.pps.prices import project_prices
from tests.test_comparison import record


@pytest.mark.parametrize('key', ['기초금액(사업예산)', '사업예산(기초금액)', '기초금액（사업예산）'])
def test_explicit_parenthesized_field_aliases_share_the_original_value(key):
    text = key+': 350,000,000원(부가세 포함)'
    rec = record(text)
    facts = amount_facts(rec)
    assert {f['label'] for f in facts if f['value'] == '350000000' and f['scope'] == 'whole'} == {'기초금액','사업예산'}
    assert project_prices(rec)['budget']['value_won'] == 350000000
    assert all(rec['docs'][0]['text'][f['start']:f['end']] == text for f in facts)


@pytest.mark.parametrize('other_duty', ['입찰보증금은 납부하지 않는다.', '계약방법: 단가계약'])
def test_another_named_duty_does_not_change_the_project_amount_scope(other_duty):
    rec = record('사업예산: 88,000,000원 (부가세 포함)\n'+other_duty)
    fact = amount_facts(rec)[0]
    assert fact['scope'] == 'whole'
    assert project_prices(rec)['budget']['value_won'] == 88000000


@pytest.mark.parametrize('ending', ['아니다.', '아니며 최종 금액은 별도 공지한다.', '아님.'])
def test_negated_amount_is_not_an_active_assignment(ending):
    rec = record('사업예산은 88,000,000원이 '+ending, 배정예산금액=None)
    assert not any(f['scope'] == 'whole' for f in amount_facts(rec))
    assert project_prices(rec)['budget']['value_won'] is None


@pytest.mark.parametrize('text', [
    '예시:\n사업예산: 88,000,000원 (부가세 포함)',
    '사업예산: 88,000,000원 (부가세 포함)\n위 금액은 본 계약에 적용하지 않는다.',
    '사업예산: 88,000,000원 (부가세 포함)\n이 금액은 월별 단가이다.',
    '기초금액(사업예산 88,000,000원',
    '기초금액(사업예산): 2,388원/ℓ(부가세 포함, 단가)',
])
def test_governing_exception_or_broken_field_is_not_promoted(text):
    rec = record(text, 배정예산금액=None)
    assert project_prices(rec)['budget']['value_won'] is None
