import copy
import dataclasses
import json

import pytest

from submission.b4_entry import parse_error
from submission.pps.generation_contract import generation_schema, validate_grammar
from submission.pps.retrieval import Span
from submission.pps.specification_scope import NAME, decode, relationship_diagnostics, review, schema


def sources():
    texts = ['신규 납품 장비의 모델명: Atlas GX900', '액세서리: 동등 이상 수량 공급을 허용한다.',
             '라이선스 사용권을 다음 연도까지 갱신 구매한다.']
    rec = {'id': 'synthetic', 'docs': [{'type': '규격서', 'text': '\n'.join(texts)}]}
    spans, at = [], 0
    for text in texts:
        spans.append(Span(0, '규격서', at, at + len(text), text))
        at += len(text) + 1
    return rec, spans


def payload():
    return {NAME: {'products': [{'sources': [1], 'role_sources': [1], 'role': 'new_supply',
        'specificity': 'named', 'requirement': 'mandatory'}],
        'permissions': [{'sources': [2], 'target_sources': [2], 'product': 1, 'target': 'component',
                         'attribute': 'quantity', 'effect': 'allowed'}],
        'exemption_sources': [], 'unresolved': '', 'judgment': {'reason': '신규 납품의 모델 지정', 'v': 1, 'e': 1}}}


def encoded(obj=None):
    return json.dumps(payload() if obj is None else obj, ensure_ascii=False)


def test_source_coordinates_and_distinct_quantity_permission_survive_consumption():
    rec, spans = sources()
    facts = decode(encoded(), spans, rec)
    assert relationship_diagnostics(facts) == []
    row, details = review(rec, {'text': encoded()}, {'spans': spans, 'items': [9]})
    assert row == {'v9': 1, 'e9': spans[0].text}
    assert details[0]['judgment_is_model_output'] and not details[0]['semantic_validation_complete']
    for section, loc in (('products', 'source_locations'), ('permissions', 'target_locations')):
        source = facts[section][0][loc][0]
        assert rec['docs'][source['doc_index']]['text'][source['start']:source['end']] == source['text']


def test_quantity_to_identity_generalization_is_a_review_flag_not_a_legal_override():
    rec, spans = sources()
    obj = payload()
    obj[NAME]['permissions'][0].update(target='whole_product', attribute='brand_or_model')
    obj[NAME]['judgment'].update(v=0, e=0)
    row, details = review(rec, {'text': encoded(obj)}, {'spans': spans, 'items': [9]})
    assert row == {'v9': 0, 'e9': ''}
    assert details[0]['relationship_issues'][0]['kind'] == 'quantity_permission_used_as_identity_substitution'


def test_explicit_separate_model_substitution_is_not_rejected_by_quantity_phrase():
    _, spans = sources()
    text = spans[1].text + ' 다른 제조사 제품으로 대체할 수 있다.'
    spans[1] = dataclasses.replace(spans[1], text=text, end=spans[1].start + len(text))
    obj = payload()
    obj[NAME]['permissions'][0]['attribute'] = 'brand_or_model'
    assert relationship_diagnostics(decode(encoded(obj), spans)) == []


@pytest.mark.parametrize('change', ['unknown_source', 'zero_source', 'missing_product', 'wrong_type', 'extra_key'])
def test_invalid_wire_and_references_are_rejected(change):
    rec, spans = sources()
    obj = payload()
    if change == 'unknown_source': obj[NAME]['products'][0]['sources'] = [4]
    if change == 'zero_source': obj[NAME]['products'][0]['sources'] = [0]
    if change == 'missing_product': obj[NAME]['permissions'][0]['product'] = 2
    if change == 'wrong_type': obj[NAME]['judgment']['v'] = True
    if change == 'extra_key': obj[NAME]['gold'] = 1
    with pytest.raises(ValueError):
        decode(encoded(obj), spans, rec)


def test_duplicate_addresses_normalize_without_changing_relation_or_value():
    rec, spans = sources()
    obj = payload()
    obj[NAME]['products'][0]['sources'] = [1, 1]
    facts = decode(encoded(obj), spans, rec)
    assert len(facts['products'][0]['source_locations']) == 1
    assert facts['products'][0]['sources'] == [1, 1] and facts['judgment']['v'] == 1


def test_unreferenced_forged_source_and_oversized_units_are_rejected():
    rec, spans = sources()
    spans[2] = dataclasses.replace(spans[2], text='잘못된 내용', end=spans[2].start + 7)
    with pytest.raises(ValueError):
        decode(encoded(), spans, rec)
    spans = [Span(0, '규격서', 0, 221, '가' * 221)]
    with pytest.raises(ValueError):
        decode(encoded(), spans)


def test_renewal_role_is_not_collapsed_to_existing_reference():
    rec, spans = sources()
    obj = payload()
    obj[NAME]['products'][0].update(role='license_renewal', role_sources=[3])
    facts = decode(encoded(obj), spans, rec)
    assert facts['products'][0]['role'] == 'license_renewal'
    assert '갱신' in facts['products'][0]['role_locations'][0]['text']


def test_no_document_does_not_create_a_product_or_permission():
    obj = {NAME: {'products': [], 'permissions': [], 'exemption_sources': [],
                 'unresolved': '규격서 미제공', 'judgment': {'reason': '특정 모델을 확인할 수 없음', 'v': 0, 'e': 0}}}
    assert decode(encoded(obj), [], {'docs': []})['products'] == []
    with pytest.raises(ValueError):
        schema(3, (10,))


@pytest.mark.parametrize('count', [0, 3, 512])
def test_actual_engine_grammar_accepts_optional_contract(count):
    validate_grammar(generation_schema('specification_scope', count, (9,)))


def test_native_format_gate_checks_product_links_and_family_consumer_is_item_scoped():
    from submission.b4_entry import B4Pipeline
    rec, spans = sources()
    packet = {'spans': [dataclasses.asdict(s) for s in spans], 'items': [9], 'family': 'A',
              'generation': {'response_format': 'specification_scope'}}
    response = {'text': encoded(), 'finish_reason': 'stop'}
    assert parse_error(packet, response) is None
    pipeline = B4Pipeline.__new__(B4Pipeline)
    row, _ = pipeline.consume(rec, packet, response)
    assert set(row) == {'v9', 'e9'}
    invalid = copy.deepcopy(payload())
    invalid[NAME]['permissions'][0]['product'] = 3
    assert parse_error(packet, {**response, 'text': encoded(invalid)}) is not None
