import copy
import dataclasses
import json

import pytest

from submission.b4_entry import B4Pipeline, parse_error
from submission.pps.generation_contract import generation_schema, validate_grammar
from submission.pps.retrieval import Span
from submission.pps.specification_relations import NAME, FORMAT, decode, review, schema
from submission.pps import specification_scope as control


def case():
    lines = ['신규 납품: 위치 측정기\n', '수신보드 모델: Atlas R7\n',
             '이 수신보드는 측정기 내부에 장착한다.\n', '측정기는 동등 성능 이상의 완제품을 허용한다.\n',
             '현재 설치된 구형 보드: Atlas R6\n', '새 보드로 기존 보드를 교체한다.\n']
    rec = {'id': 'synthetic', 'docs': [{'type': '규격서', 'text': ''.join(lines)}]}
    spans, at = [], 0
    for line in lines:
        spans.append(Span(0, '규격서', at, at + len(line), line))
        at += len(line)
    product = lambda source, specificity: {
        'sources': [source], 'role_sources': [source], 'role': 'new_supply',
        'specificity': specificity, 'requirement': 'mandatory',
        'relation': 'none', 'related_product': 0, 'relation_sources': []}
    products = [product(1, 'generic'), product(2, 'named')]
    products[1].update(relation='part_of', related_product=1, relation_sources=[3])
    obj = {NAME: {'products': products, 'permissions': [{
        'sources': [4], 'target_sources': [1, 4], 'product': 1, 'target': 'whole_product',
        'attribute': 'performance', 'effect': 'allowed'}], 'exemption_sources': [],
        'unresolved': '', 'product_inventory': 'complete',
        'judgment': {'reason': '구성품의 필수 명칭과 본체 대체 허용을 구별', 'v': 1, 'e': 2}}}
    return rec, spans, obj


@pytest.mark.parametrize('value', [0, 1])
def test_whole_permission_and_named_component_are_separate_without_automatic_inheritance(value):
    rec, spans, obj = case()
    obj[NAME]['judgment'].update(v=value, e=2 if value else 0)
    frozen = copy.deepcopy((rec, obj))
    row, details = review(rec, {'text': json.dumps(obj)}, {'items': [9], 'spans': spans})
    facts = details[0]['facts']
    assert facts['products'][1]['related_product'] == 1
    assert facts['products'][1]['relation_locations'][0]['text'] == spans[2].text
    assert facts['permissions'][0]['product'] == 1
    assert row['v9'] == value and not details[0]['semantic_validation_complete']
    assert (rec, obj) == frozen


def test_new_replacement_and_existing_target_do_not_become_a_legal_exemption():
    rec, spans, obj = case()
    products = obj[NAME]['products']
    old = {**products[0], 'sources': [5], 'role_sources': [5],
           'specificity': 'named', 'role': 'existing_reference'}
    products.append(old)
    products[1].update(role='replacement_component', relation='replacement_for',
                       related_product=3, relation_sources=[6, 6], role_sources=[6])
    row, details = review(rec, {'text': json.dumps(obj)}, {'items': [9], 'spans': spans})
    assert len(details[0]['facts']['products'][1]['relation_locations']) == 1
    assert row['v9'] == 1 and not details[0]['relations_imply_legal_exemption']


@pytest.mark.parametrize('change', ['nonexistent', 'self', 'empty_link', 'missing_target',
                                   'source_zero', 'source_bool', 'unclaimed_link', 'unknown_source'])
def test_invalid_relations_are_rejected_without_silent_rewiring(change):
    rec, spans, obj = case()
    p = obj[NAME]['products'][1]
    if change == 'nonexistent': p['related_product'] = 6
    if change == 'self': p['related_product'] = 2
    if change == 'empty_link': p['relation_sources'] = []
    if change == 'missing_target': p['related_product'] = 0
    if change == 'source_zero': p['relation_sources'] = [0]
    if change == 'source_bool': p['relation_sources'] = [True]
    if change == 'unclaimed_link': p['relation'] = 'none'
    if change == 'unknown_source': p['relation_sources'] = [99]
    wire = json.dumps(obj)
    with pytest.raises(ValueError):
        decode(wire, spans, rec)
    packet = {'items': [9], 'spans': [dataclasses.asdict(s) for s in spans],
              'generation': {'response_format': FORMAT}, 'family': 'A'}
    assert parse_error(packet, {'text': wire, 'finish_reason': 'stop'}) is not None


@pytest.mark.parametrize('relation', ['part_of', 'replacement_for'])
def test_directed_cycle_is_invalid(relation):
    rec, spans, obj = case()
    obj[NAME]['products'][0].update(relation=relation, related_product=2, relation_sources=[3])
    with pytest.raises(ValueError, match='cycle'):
        decode(json.dumps(obj), spans, rec)


def test_reciprocal_compatibility_is_not_a_containment_cycle():
    rec, spans, obj = case()
    for i, product in enumerate(obj[NAME]['products']):
        product.update(relation='compatibility_with', related_product=2 - i, relation_sources=[3])
    assert len(decode(json.dumps(obj), spans, rec)['products']) == 2


@pytest.mark.parametrize('change', ['forged', 'whitespace'])
def test_relation_only_source_is_validated_against_original_record(change):
    rec, spans, obj = case()
    old = spans[2]
    text = ' ' * len(old.text) if change == 'whitespace' else '가' * len(old.text)
    spans[2] = dataclasses.replace(old, text=text)
    if change == 'whitespace':
        rec['docs'][0]['text'] = rec['docs'][0]['text'][:old.start] + text + rec['docs'][0]['text'][old.end:]
    with pytest.raises(ValueError):
        decode(json.dumps(obj), spans, rec)


def test_consistency_condition_does_not_have_to_be_encoded_as_a_brand_prohibition():
    rec, spans, obj = case()
    obj[NAME]['permissions'][0].update(attribute='manufacturer_consistency', effect='required')
    facts = decode(json.dumps(obj), spans, rec)
    assert facts['permissions'][0]['attribute'] == 'manufacturer_consistency'


def test_inventory_incomplete_and_missing_parent_remain_uncertainty():
    rec, spans, obj = case()
    obj[NAME]['products'][1].update(role='replacement_component', relation='unknown',
                                   related_product=0, relation_sources=[])
    obj[NAME]['product_inventory'] = 'partial'
    row, details = review(rec, {'text': json.dumps(obj)}, {'items': [9], 'spans': spans})
    assert row['v9'] == 1 and details[0]['facts']['product_inventory'] == 'partial'
    assert details[0]['relationship_issues'][0]['kind'] == 'replacement_target_unresolved'


@pytest.mark.parametrize('count', [0, 6, 512])
def test_finite_grammar_compiles_without_mutating_control_schema(count):
    before = control.schema(count)
    validate_grammar(generation_schema(FORMAT, count, (9,)))
    assert control.schema(count) == before
    fields = schema(count)['properties'][NAME]['properties']
    assert list(fields)[-1] == 'judgment'
    assert fields['products']['maxItems'] == (6 if count else 0)


def test_empty_source_does_not_create_products_and_pipeline_is_v9_only():
    obj = {NAME: {'products': [], 'permissions': [], 'exemption_sources': [],
                 'unresolved': '첨부 미제공', 'product_inventory': 'unknown',
                 'judgment': {'reason': '실제 모델 원문을 확인할 수 없음', 'v': 0, 'e': 0}}}
    packet = {'items': [9], 'spans': [], 'generation': {'response_format': FORMAT}, 'family': 'A'}
    response = {'text': json.dumps(obj), 'finish_reason': 'stop'}
    assert parse_error(packet, response) is None
    pipeline = B4Pipeline.__new__(B4Pipeline)
    row, _ = pipeline.consume({'docs': []}, packet, response)
    assert row == {'v9': 0, 'e9': ''}
    with pytest.raises(ValueError):
        pipeline.consume({'docs': []}, {**packet, 'family': 'C'}, response)
