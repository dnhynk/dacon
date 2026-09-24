"""All decision modules must agree on compound Korean monetary units."""
from decimal import Decimal
import pytest

from submission.pps.comparison import won_value
from submission.pps.other_checks import won, budget_facts, sw_check
from submission.pps.performance import won as performance_won
from submission.pps.temporal import extract_amounts
from tests.test_other_checks import notice


@pytest.mark.parametrize('text,value', [('2천3백만원', 23000000), ('20억5천만원', 2050000000),
    ('1조2천3백억원', 1230000000000), ('120,000천원', 120000000)])
def test_compound_amount_is_identical_in_all_consumers(text, value):
    assert won(text) == performance_won(text) == won_value(text) == value
    rec = notice('사업예산: '+text+' (부가세 포함)')
    assert Decimal(budget_facts(rec)['project_won']) == value
    assert Decimal(extract_amounts(rec)[0]['value']) == value


def test_compound_budget_cannot_hide_an_inconsistent_sw_floor_disclosure():
    rec = notice('사업예산: 20억5천만원 (부가세 포함)\n본 사업은 소프트웨어 사업이다.\n'
                 '본 사업은 20억원 미만으로 소프트웨어진흥법 제48조의 하한제도를 적용한다.')
    decision = sw_check(rec)
    assert decision['facts']['budget']['effective_won'] == '2050000000'
    assert decision['value'] is None
    assert decision['reason'] == 'disclosure_amount_conflict'


def test_invalid_unit_order_is_never_salvaged_as_a_smaller_amount():
    text = '2억1조원'
    assert won(text) is won_value(text) is None
    with pytest.raises(ValueError):
        performance_won(text)
    rec = notice('사업예산: '+text+' (부가세 포함)')
    assert budget_facts(rec)['project_won'] is None
    assert extract_amounts(rec) == []
