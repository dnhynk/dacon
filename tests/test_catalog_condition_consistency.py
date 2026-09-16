"""A fallible catalog summary cannot erase a decisive supplied condition conflict."""
import copy
import json

import pytest

from submission.pps.fact_consistency import PRODUCT_FIELD, apply


EXPRESSION = {'all': [
    {'field': 'cpu_architecture', 'operator': 'eq', 'value': 'x86'},
    {'any': [
        {'field': 'cpu_count', 'operator': 'eq', 'value': '1'},
        {'all': [
            {'field': 'cpu_count', 'operator': 'eq', 'value': '2'},
            {'field': 'cpu_base_ghz', 'operator': 'le', 'value': '3.2'},
        ]},
    ]},
]}


def product(*, architecture='arm', scope='unbound_property_mention', status='unknown',
            paired=True, uncertainty=None):
    return {
        'status': 'unknown', 'catalog_scope': 'supplied_catalog_only',
        'uncertainty': [] if uncertainty is None else uncertainty,
        'paired_meta_purchase_codes': ['4321150102'] if paired else [],
        'products': [{
            'code': '4321150102', 'name': '컴퓨터서버', 'listed': True,
            'condition': {
                'status': status, 'expression': EXPRESSION,
                'source_facts': {
                    'whole_purchase_certified': False,
                    'observations': [{
                        'field': 'cpu_architecture', 'type': 'enum', 'value': architecture,
                        'scope': scope, 'issue': None,
                        'evidence': {'doc_index': 1, 'start': 10, 'end': 29,
                                     'text': 'Processor: 20-core Arm'},
                    }],
                    # An equivalence permission keeps purchase scope unresolved;
                    # it cannot make an unconditional positive trustworthy.
                    'scope_issues': [{'kind': 'unresolved_scope_or_modality', 'text': '동등'}],
                },
            },
        }],
    }


def response(claim='컴퓨터서버(4321150102)는 중소기업자간 경쟁제품이며 고시 조건을 충족한다.'):
    return {'text': json.dumps({'facts': {PRODUCT_FIELD: claim}}, ensure_ascii=False)}


def test_decisive_unbound_condition_conflict_suppresses_only_unconditional_competition_bits():
    source = product()
    before = copy.deepcopy(source)
    row = {f'v{i}': '1' for i in (10, 11, 13)} | {f'e{i}': '모델 근거' for i in (10, 11, 13)}
    row.update(v18='1', e18='다른 항목')
    out, flags = apply(row, response(), {10: 1, 11: 1, 13: 1, 18: 1},
                       product=source, qualification={'complete': True})
    assert [out[f'v{i}'] for i in (10, 11, 13, 18)] == ['0', '0', '0', '1']
    assert [out[f'e{i}'] for i in (10, 11, 13, 18)] == ['', '', '', '다른 항목']
    assert {f['item'] for f in flags} == {10, 11, 13}
    assert all(f['reason'] == 'model_competition_claim_omits_decisive_conditional_conflict'
               and f['condition_result_if_observed_properties_apply'] is False
               and f['scope_promoted'] is False for f in flags)
    assert source == before and source['status'] == 'unknown'


@pytest.mark.parametrize('source,complete,claim', [
    (product(), False, '컴퓨터서버(4321150102)는 경쟁제품이다.'),
    (product(paired=False), True, '컴퓨터서버(4321150102)는 경쟁제품이다.'),
    (product(architecture='x86'), True, '컴퓨터서버(4321150102)는 경쟁제품이다.'),
    (product(scope='entire_named_purchase'), True, '컴퓨터서버(4321150102)는 경쟁제품이다.'),
    (product(status='met'), True, '컴퓨터서버(4321150102)는 경쟁제품이다.'),
    (product(uncertainty=['additional_declared_purchase_components']), True,
     '컴퓨터서버(4321150102)는 경쟁제품이다.'),
    (product(), True, '복수 구매품목 중 컴퓨터서버(4321150102)만 경쟁제품이다.'),
    (product(), True, '다른 장비가 경쟁제품이다.'),
])
def test_incomplete_identity_scope_nondecisive_or_qualified_cases_preserve_the_model(source, complete, claim):
    out, flags = apply({'v10': '1', 'e10': '모델 근거'}, response(claim), {10: 1},
                       product=source, qualification={'complete': complete})
    assert out == {'v10': '1', 'e10': '모델 근거'} and not flags
