"""Synthetic contracts for the optional literal base-budget/estimate hypothesis."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import catalog, facts, judge, switches


def bundle(*lines, price=227_272_727, award='협상에의한계약', attachments=()):
    return facts.build({'id': 'synthetic-budget-case', 'meta': {
        '적용계약법': '국가계약법', '업무구분': '일반용역', '계약방법': '제한경쟁',
        '낙찰방법': award, '입찰추정가격': price, '배정예산금액': 250_000_000},
        'docs': [{'type': '공고문', 'text': '\n'.join(lines)}] +
                [{'type': '과업지시서', 'text': text} for text in attachments]}, catalog.load())


@pytest.mark.parametrize('mode', ['base', 'nego'])
def test_literal_vat_inclusive_base_and_verbatim_evidence(monkeypatch, mode):
    monkeypatch.setattr(switches, 'V24_BUDGET_VS_P', mode)
    text = '다. 기초금액 : 금250,000,000원(부가가치세 포함)'
    b = bundle(text)
    assert judge.v24_budget_vs_p(b).text == text
    # Even a separately enabled VAT-agreement fix cannot undo this hypothesis.
    monkeypatch.setattr(switches, 'V24_FIXES', True)
    assert judge.v24_budget_vs_p(b).text == text


@pytest.mark.parametrize('label', ['기초금액', '추정가격', '추정금액', '배정예산', '사업예산', '사업금액',
                                  '용역금액', '계약금액', '총사업비', '소요예산', '예산액', '구매예산'])
@pytest.mark.parametrize('mode', ['base', 'nego'])
def test_any_label_stating_registered_estimate_suppresses(monkeypatch, label, mode):
    monkeypatch.setattr(switches, 'V24_BUDGET_VS_P', mode)
    b = bundle('기초금액: 250,000,000원', f'{label}: 227,272,727원')
    assert judge.v24_budget_vs_p(b) is None


@pytest.mark.parametrize('mode,expected', [('base', True), ('nego', False), (False, False)])
def test_award_scope_and_default_off(monkeypatch, mode, expected):
    monkeypatch.setattr(switches, 'V24_BUDGET_VS_P', mode)
    b = bundle('기초금액: 250,000,000원', award='적격심사')
    assert (judge.v24_budget_vs_p(b) is not None) == expected


@pytest.mark.parametrize('text', ['금액은 추후 공지', '기초금액: 추후 공지', '사업예산: 250,000,000원',
                                  '기초금액: 999,999원', '기초금액: 250000000',
                                  '기초금액: 2억 이상 실적 보유'])
def test_requires_stated_base_amount(monkeypatch, text):
    monkeypatch.setattr(switches, 'V24_BUDGET_VS_P', 'base')
    assert judge.v24_budget_vs_p(bundle(text)) is None


@pytest.mark.parametrize('price', [None, 0, -1, float('inf'), float('nan')])
def test_requires_registered_finite_positive_estimate(monkeypatch, price):
    monkeypatch.setattr(switches, 'V24_BUDGET_VS_P', 'base')
    assert judge.v24_budget_vs_p(bundle('기초금액: 250,000,000원', price=price)) is None


def test_thousand_won_units_and_spaced_base_label(monkeypatch):
    monkeypatch.setattr(switches, 'V24_BUDGET_VS_P', 'base')
    text = '기 초 금 액 : 250,000천원'
    assert judge.v24_budget_vs_p(bundle(text)).text == text
    assert judge.v24_budget_vs_p(bundle(text, '추정가격: 227,272.727천원')) is None
    assert judge.v24_budget_vs_p(bundle('기초금액: 227,272.727천원')) is None


def test_equal_estimate_after_line_150_is_seen(monkeypatch):
    monkeypatch.setattr(switches, 'V24_BUDGET_VS_P', 'base')
    assert judge.v24_budget_vs_p(bundle('기초금액: 250,000,000원', *['안내사항'] * 155,
                                      '추정가격: 227,272,727원')) is None


def test_only_notice_and_first_base_evidence(monkeypatch):
    monkeypatch.setattr(switches, 'V24_BUDGET_VS_P', 'base')
    first = '기초금액: 250,000,000원'
    b = bundle(first, '기초금액: 249,000,000원', attachments=['추정가격: 227,272,727원'])
    assert judge.v24_budget_vs_p(b).text == first
    assert judge.v24_budget_vs_p(bundle('본문 안내', attachments=[first])) is None


def test_existing_won_rounding_tolerance(monkeypatch):
    monkeypatch.setattr(switches, 'V24_BUDGET_VS_P', 'base')
    assert judge.v24_budget_vs_p(bundle('기초금액: 227,272,728원')) is None
    monkeypatch.setattr(switches, 'V24P_GUARDS', True)
    assert judge.v24_budget_vs_p(bundle('기초금액: 227,272,700원')) is None


def test_extra_axis_is_additive_and_default_off(monkeypatch):
    monkeypatch.setattr(judge, 'V24_AXES', ())
    b = bundle('기초금액: 250,000,000원')
    monkeypatch.setattr(switches, 'V24_BUDGET_VS_P', False)
    assert judge.v24_axes(b) is None
    monkeypatch.setattr(switches, 'V24_BUDGET_VS_P', 'nego')
    assert judge.v24_axes(b).text == b.notice.notice_lines()[0].text


@pytest.mark.parametrize('text', ['마. 기초금액: 전체 250,000,000원(부가세 포함)',
                                  '계약기초금액: 연간 금250,000,000원',
                                  '금250,000,000원(기초금액: 추정가격 + 부가가치세)',
                                  '기초금액: 金250,000,000원'])
def test_line_level_budget_forms(monkeypatch, text):
    monkeypatch.setattr(switches, 'V24_BUDGET_VS_P', 'base')
    assert judge.v24_budget_vs_p(bundle(text)).text == text


def test_unit_conversion_does_not_infer_vat_agreement(monkeypatch):
    monkeypatch.setattr(switches, 'V24_BUDGET_VS_P', 'base')
    assert judge.v24_budget_vs_p(bundle('기초금액: 60,000천원', price=54_545_455)) is not None
