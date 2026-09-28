"""Consumer-only typed amount comparisons; no model calls or request changes."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import switches, judge
from pps_c.dedicated import input_mismatch as st
from test_dedicated_input_mismatch import bundle, reading, line, LengthEngine, Req, model_output


def sample():
    b = bundle('사업예산: 110,000,000원', '추정가격: 80,000,000원', 배정예산금액=110000000, 입찰추정가격=100000000)
    r = reading(b, budget=[('사업예산', '사업예산', 110000000, '포함', '총액'),
                           ('추정가격', '추정가격', 80000000, '미포함', '총액')])
    return b, r


def test_masked_estimate_and_exact_evidence(monkeypatch):
    b, r = sample()
    monkeypatch.setattr(switches, 'V24_TYPED_AMOUNT', False)
    assert st.decide(b, r) is None
    monkeypatch.setattr(switches, 'V24_TYPED_AMOUNT', True)
    assert st.decide(b, r) is line(b, '추정가격')
    b.d_input_mismatch = r
    assert st.verdict(b).text == '추정가격: 80,000,000원'


@pytest.mark.parametrize('vat', ['포함', '미포함', '불명'])
def test_estimate_is_compared_as_written_even_if_vat_or_cross_field_matches(monkeypatch, vat):
    b = bundle('추정가격: 110,000,000원', 배정예산금액=110000000, 입찰추정가격=100000000)
    r = reading(b, budget=[('추정가격', '추정가격', 110000000, vat, '총액')])
    monkeypatch.setattr(switches, 'V24_TYPED_AMOUNT', False)
    assert st.decide(b, r) is None
    monkeypatch.setattr(switches, 'V24_TYPED_AMOUNT', True)
    assert st.decide(b, r) is line(b, '추정가격')


@pytest.mark.parametrize('kind', ['예산', '배정예산', '사업예산'])
def test_budget_uses_B_and_cannot_be_cancelled_by_P(monkeypatch, kind):
    b = bundle('사업예산: 100,000,000원', 배정예산금액=110000000, 입찰추정가격=100000000)
    r = reading(b, budget=[('사업예산', kind, 100000000, '불명', '총액')])
    monkeypatch.setattr(switches, 'V24_TYPED_AMOUNT', True)
    assert st.decide(b, r) is line(b, '사업예산')


@pytest.mark.parametrize('scope', ['단가', '금차분', '항목별', '불명'])
def test_new_comparison_ignores_non_total_scope(monkeypatch, scope):
    b, r = sample()
    r['budget'][1] = (*r['budget'][1][:4], scope)
    monkeypatch.setattr(switches, 'V24_TYPED_AMOUNT', True)
    assert st.typed_amount_mismatch(b, r) is None
    assert st.decide(b, r) is None  # the budget agrees in the unchanged legacy path


def test_keep_legacy_unit_reading_exclusion(monkeypatch):
    b, r = sample()
    r['budget'][0] = (*r['budget'][0][:4], '단가')
    monkeypatch.setattr(switches, 'V24_TYPED_AMOUNT', True)
    assert st.decide(b, r) is None


def test_same_field_agreement_and_sum_do_not_cancel_another_total(monkeypatch):
    b = bundle('첫 추정가격: 100,000,000원', '둘째 추정가격: 80,000,000원', 배정예산금액=180000000, 입찰추정가격=100000000)
    r = reading(b, budget=[('첫', '추정가격', 100000000, '미포함', '총액'), ('둘째', '추정가격', 80000000, '미포함', '총액')])
    monkeypatch.setattr(switches, 'V24_TYPED_AMOUNT', True)
    assert st.decide(b, r) is line(b, '둘째')


@pytest.mark.parametrize('amount,expected', [(99000000, False), (101000000, False), (98999999, True), (101000001, True)])
def test_existing_one_percent_tolerance(amount, expected):
    b = bundle(f'추정가격: {amount:,}원', 배정예산금액=110000000, 입찰추정가격=100000000)
    r = reading(b, budget=[('추정가격', '추정가격', amount, '미포함', '총액')])
    assert (st.typed_amount_mismatch(b, r) is not None) == expected


def test_missing_registered_P_is_not_replaced_by_derived_P():
    b = bundle('추정가격: 80,000,000원', 배정예산금액=110000000, 입찰추정가격=None)
    assert b.meta.P_source != 'meta'
    r = reading(b, budget=[('추정가격', '추정가격', 80000000, '미포함', '총액')])
    assert st.typed_amount_mismatch(b, r) is None


@pytest.mark.parametrize('kind', ['기초금액', '사업금액', '기타', '불명'])
def test_unmapped_kind_not_compared_by_new_rule(kind):
    b, r = sample()
    r['budget'][1] = (r['budget'][1][0], kind, *r['budget'][1][2:])
    assert st.typed_amount_mismatch(b, r) is None


def test_no_saved_reading_means_no_new_verdict(monkeypatch):
    b, _ = sample()
    monkeypatch.setattr(switches, 'V24_TYPED_AMOUNT', True)
    assert st.verdict(b) is None


def test_selector_and_model_request_identical_across_switch(monkeypatch):
    quiet, _ = sample()
    requested = bundle('사업예산: 70,000,000원', 배정예산금액=110000000, 입찰추정가격=100000000)
    def signature(b):
        req = st.request(LengthEngine(), b, 0, Req)
        return st.select(b), None if req is None else (req.fam, [ln.i for ln in req.cands], req.token_ids, req.schema, req.max_tokens)
    monkeypatch.setattr(switches, 'V24_TYPED_AMOUNT', False)
    before = [signature(b) for b in (quiet, requested)]
    assert before[0][1] is None and before[1][1] is not None
    monkeypatch.setattr(switches, 'V24_TYPED_AMOUNT', True)
    assert [signature(b) for b in (quiet, requested)] == before


def test_grounded_consume_and_other_judge_items_unchanged(monkeypatch):
    b, _ = sample()
    monkeypatch.setattr(switches, 'DEDICATED', ('v24',))
    output = model_output(budget={'stated': '있음', 'items': [
        {'kind': '사업예산', 'amount': '110,000,000원', 'vat': '포함', 'scope': '총액', 'quote': '사업예산: 110,000,000원'},
        {'kind': '추정가격', 'amount': '80,000,000원', 'vat': '미포함', 'scope': '총액', 'quote': '추정가격: 80,000,000원'}]})
    assert st.consume(b, [line(b, '사업예산'), line(b, '추정가격')], output)
    monkeypatch.setattr(switches, 'V24_TYPED_AMOUNT', False)
    before = judge.judge(b)
    assert before['v24'] == (0, '')
    monkeypatch.setattr(switches, 'V24_TYPED_AMOUNT', True)
    after = judge.judge(b)
    assert after['v24'] == (1, '추정가격: 80,000,000원')
    assert {k:v for k,v in before.items() if k != 'v24'} == {k:v for k,v in after.items() if k != 'v24'}


def test_annual_total_not_compared_to_registered_unit_price(monkeypatch):
    b = bundle('본 입찰은 단가계약입니다.', '사업예산: 13,250,000원', 배정예산금액=530, 입찰추정가격=482)
    r = reading(b, budget=[('사업예산', '사업예산', 13250000, '불명', '총액')])
    monkeypatch.setattr(switches, 'V24_TYPED_AMOUNT', True)
    assert st.typed_amount_mismatch(b, r) is None
    assert st.decide(b, r) is None
