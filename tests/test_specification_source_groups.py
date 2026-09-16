"""A reference set must not invent proximity or borrow an unrelated exception."""
import copy
import json

import pytest

from submission.pps.retrieval import Span
from submission.pps.specification_scope import NAME, decode, relationship_diagnostics, review


QUANTITY = 'quantity_permission_used_as_identity_substitution'
COHERENCE = 'set_manufacturer_consistency_used_as_model_prohibition'


def packet(texts, selections, *, effect='allowed', attribute='brand_or_model', value=0):
    rec = {'id': 'synthetic', 'docs': [{'type': '규격서', 'text': text} for text in texts]}
    spans = [Span(di, '규격서', lo, hi, texts[di][lo:hi]) for di, lo, hi in selections]
    refs = list(range(1, len(spans) + 1))
    obj = {NAME: {'products': [], 'permissions': [{
        'sources': refs, 'target_sources': refs, 'product': 0, 'target': 'whole_product',
        'attribute': attribute, 'effect': effect}], 'exemption_sources': [], 'unresolved': '',
        'judgment': {'reason': '관계의 적용 범위는 별도 확인 필요', 'v': value, 'e': 1 if value else 0}}}
    return rec, spans, json.dumps(obj, ensure_ascii=False)


@pytest.mark.parametrize('gap', ['', '\n', '\n \t\n'])
@pytest.mark.parametrize('reverse', [False, True])
def test_real_connected_phrase_is_read_in_original_order(gap, reverse):
    text = '동등' + gap + '수량 공급을 허용한다.'
    ranges = [(0, 0, 2), (0, 2 + len(gap), len(text))]
    if reverse:
        ranges.reverse()
    rec, spans, wire = packet([text], ranges)
    facts = decode(wire, spans, rec)
    frozen = copy.deepcopy(facts)
    issues = relationship_diagnostics(facts, rec)
    assert [i['kind'] for i in issues] == [QUANTITY]
    assert issues[0]['source_group'] == {'doc_index': 0, 'start': 0, 'end': len(text), 'text': text}
    assert facts == frozen


@pytest.mark.parametrize('original_available', [False, True])
@pytest.mark.parametrize('separation', ['document', 'omitted_word'])
def test_disconnected_sources_cannot_form_a_quantity_phrase(original_available, separation):
    if separation == 'document':
        texts, ranges = ['동등', '수량 공급'], [(0, 0, 2), (1, 0, 5)]
    else:
        texts = ['동등 성능. 2. 수량 공급']
        at = texts[0].index('수량')
        ranges = [(0, 0, 2), (0, at, len(texts[0]))]
    rec, spans, wire = packet(texts, ranges)
    facts = decode(wire, spans, rec)
    assert relationship_diagnostics(facts, rec if original_available else None) == []


def test_unobserved_gap_cannot_be_assumed_to_be_whitespace():
    rec, spans, wire = packet(['동등\n수량 공급'], [(0, 0, 2), (0, 3, 8)])
    facts = decode(wire, spans, rec)
    assert relationship_diagnostics(facts) == []
    assert relationship_diagnostics(facts, rec)[0]['kind'] == QUANTITY


def test_overlapping_original_ranges_do_not_duplicate_or_reorder_the_phrase():
    text = '동등 이상 수량 공급'
    rec, spans, wire = packet([text], [(0, 3, len(text)), (0, 0, 7)])
    issues = relationship_diagnostics(decode(wire, spans, rec), rec)
    assert len(issues) == 1 and issues[0]['source_group']['text'] == text


@pytest.mark.parametrize('separation', ['document', 'omitted_word'])
def test_unrelated_model_permission_cannot_suppress_a_quantity_scope_warning(separation):
    quantity = '동등 이상 수량 공급을 허용한다.'
    identity = '다른 제조사로 대체할 수 있다.'
    if separation == 'document':
        texts, ranges = [quantity, identity], [(0, 0, len(quantity)), (1, 0, len(identity))]
    else:
        text = quantity + '\n두 번째 별도 품목의 조건\n' + identity
        texts, ranges = [text], [(0, 0, len(quantity)), (0, text.index(identity), len(text))]
    rec, spans, wire = packet(texts, ranges)
    issues = relationship_diagnostics(decode(wire, spans, rec), rec)
    assert [i['kind'] for i in issues] == [QUANTITY]
    assert issues[0]['source_group']['text'] == quantity


@pytest.mark.parametrize('value', [0, 1])
@pytest.mark.parametrize('wrap', [' ', '\n'])
def test_internal_manufacturer_consistency_does_not_certify_a_named_brand(value, wrap):
    text = '기체 및 조종기는 동일회사' + wrap + '완제품형태로 세트화 하여 납품하여야 한다.'
    rec, spans, wire = packet([text], [(0, 0, len(text))], effect='prohibited', value=value)
    frozen = copy.deepcopy(rec)
    row, details = review(rec, {'text': wire}, {'spans': spans, 'items': [9]})
    issues = details[0]['relationship_issues']
    assert [i['kind'] for i in issues] == [COHERENCE]
    assert issues[0]['source_group']['text'] == text
    assert not issues[0]['semantic_truth_certified'] and row['v9'] == value
    assert rec == frozen


@pytest.mark.parametrize('suffix,attribute,effect', [
    (' 지정 모델의 변경은 불가하다.', 'brand_or_model', 'prohibited'),
    ('', 'warranty', 'prohibited'),
    ('', 'brand_or_model', 'conditional'),
])
def test_explicit_model_condition_and_distinct_relations_are_not_reinterpreted(suffix, attribute, effect):
    text = '동일 제조사 제품을 세트로 납품한다.' + suffix
    rec, spans, wire = packet([text], [(0, 0, len(text))], effect=effect, attribute=attribute)
    assert relationship_diagnostics(decode(wire, spans, rec), rec) == []
