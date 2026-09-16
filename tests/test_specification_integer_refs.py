"""JSON Schema accepts integral floats; source decoders must reject them first."""
import copy
import dataclasses
import json

import pytest

from submission.b4_entry import B4Pipeline, parse_error
from submission.pps import specification_scope as base, specification_relations as graph
from tests.test_specification_relations import case


def example(module):
    rec, spans, obj = case()
    if module is base:
        body = obj.pop(graph.NAME)
        body.pop('product_inventory')
        for product in body['products']:
            for key in ('relation', 'related_product', 'relation_sources'):
                product.pop(key)
        obj[base.NAME] = body
    return rec, spans, obj


@pytest.mark.parametrize('module', [base, graph])
@pytest.mark.parametrize('location,notation', [
    (location, notation) for location in ('product_source', 'product_scope', 'permission_source',
        'permission_scope', 'exemption', 'permission_product', 'judgment_e', 'judgment_v')
    for notation in ('float', 'exponent', 'duplicate')
    if notation != 'duplicate' or location not in ('permission_product', 'judgment_e', 'judgment_v')])
def test_integral_float_reference_never_reaches_the_consumer(module, location, notation):
    rec, spans, obj = example(module)
    body = obj[module.NAME]
    value = 1.0
    refs = [1, value] if notation == 'duplicate' else [value]
    if location == 'product_source': body['products'][0]['sources'] = refs
    if location == 'product_scope': body['products'][0]['role_sources'] = refs
    if location == 'permission_source': body['permissions'][0]['sources'] = refs
    if location == 'permission_scope': body['permissions'][0]['target_sources'] = refs
    if location == 'exemption': body['exemption_sources'] = refs
    if location == 'permission_product': body['permissions'][0]['product'] = value
    if location == 'judgment_e': body['judgment']['e'] = value
    if location == 'judgment_v': body['judgment']['v'] = value
    wire = json.dumps(obj)
    if notation == 'exponent':
        wire = wire.replace('1.0', '1e0')
    with pytest.raises(ValueError, match='integer|address'):
        module.decode(wire, spans, rec)
    packet = {'items': [9], 'spans': [dataclasses.asdict(s) for s in spans], 'family': 'A',
              'generation': {'response_format': 'specification_scope' if module is base else graph.FORMAT}}
    response = {'text': wire, 'finish_reason': 'stop'}
    assert parse_error(packet, response).startswith('ValueError:')
    pipeline = B4Pipeline.__new__(B4Pipeline)
    with pytest.raises(ValueError):
        pipeline.consume(rec, packet, response)


@pytest.mark.parametrize('location', ['relation_source', 'relation_target'])
@pytest.mark.parametrize('value', [1.0, True, -1, 99])
def test_product_edges_have_the_same_exact_type_boundary(location, value):
    rec, spans, obj = example(graph)
    product = obj[graph.NAME]['products'][1]
    if location == 'relation_source':
        product['relation_sources'] = [1, value]
    else:
        product['related_product'] = value
    with pytest.raises(ValueError):
        graph.decode(json.dumps(obj), spans, rec)


@pytest.mark.parametrize('module', [base, graph])
def test_valid_duplicate_ids_preserve_raw_payload_and_original_locations(module):
    rec, spans, obj = example(module)
    body = obj[module.NAME]
    body['products'][0]['sources'] = [1, 1]
    body['products'][0]['role_sources'] = [1, 1]
    body['permissions'][0].update(sources=[4, 4], target_sources=[1, 4, 1])
    body['exemption_sources'] = [6, 6]
    if module is graph:
        body['products'][1]['relation_sources'] = [3, 3]
    before = copy.deepcopy(obj)
    facts = module.decode(json.dumps(obj), spans, rec)
    assert obj == before
    assert facts['products'][0]['sources'] == [1, 1]
    assert [s['s'] for s in facts['products'][0]['source_locations']] == [1]
    assert [s['s'] for s in facts['permissions'][0]['target_locations']] == [1, 4]
    assert [s['s'] for s in facts['exemption_locations']] == [6]


@pytest.mark.parametrize('module', [base, graph])
def test_unknown_product_zero_and_negative_judgment_zero_stay_valid(module):
    rec, spans, obj = example(module)
    obj[module.NAME]['permissions'][0]['product'] = 0
    obj[module.NAME]['judgment'].update(v=0, e=0)
    facts = module.decode(json.dumps(obj), spans, rec)
    assert facts['permissions'][0]['product'] == 0 and facts['judgment']['v'] == 0


def test_decoding_an_unused_float_evidence_cannot_defer_failure_to_positive_indexing():
    rec, spans, obj = example(graph)
    obj[graph.NAME]['judgment'].update(v=0, e=0.0)
    with pytest.raises(ValueError):
        graph.decode(json.dumps(obj), spans, rec)
