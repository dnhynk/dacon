import copy
import json

import pytest

from submission.pps.retrieval import Span
from submission.pps.specification_blocks import form_blocks, permission_link_issues
from submission.pps.specification_scope import NAME, decode, review


def case(second_name='Beta B200'):
    header = '규 격 서\nCOMMODITY DESCRIPTION\n품 명\n모델명\n수량\n'
    lines = [header, 'Alpha A100\n', '첫 장비 신규 납품\n', header, second_name + '\n',
             '두 번째 장비 신규 납품\n', '상기 사양 이상의 제품을 공급할 수 있다.\n']
    text = ''.join(lines)
    rec = {'id': 'synthetic', 'docs': [{'type': '규격서', 'text': text}]}
    spans, at = [], 0
    for line in lines:
        spans.append(Span(0, '규격서', at, at+len(line), line))
        at += len(line)
    p = lambda n, r: {'sources': [n], 'role_sources': [r], 'role': 'new_supply',
        'specificity': 'named', 'requirement': 'mandatory'}
    value = {NAME: {'products': [p(2, 3), p(5, 6)], 'permissions': [
        {'sources': [7], 'target_sources': [7], 'product': 1,
         'target': 'whole_product', 'attribute': 'performance', 'effect': 'allowed'}],
        'exemption_sources': [], 'unresolved': '',
        'judgment': {'reason': '모델이 제안한 판정', 'v': 0, 'e': 0}}}
    return rec, spans, value


def test_cross_form_link_requires_scope_bridge_and_preserves_judgment():
    rec, spans, value = case()
    wire = json.dumps(value, ensure_ascii=False)
    row, details = review(rec, {'text': wire}, {'spans': spans, 'items': [9]})
    issue = details[0]['relationship_issues'][0]
    assert issue['kind'] == 'cross_form_permission_without_scope_bridge'
    assert issue['other_products_in_permission_form'] == [2]
    assert row == {'v9': 0, 'e9': ''}
    for heading in issue['product_form_headers'] + issue['permission_form_headers']:
        assert rec['docs'][heading['doc_index']]['text'][heading['start']:heading['end']] == heading['text']


@pytest.mark.parametrize('variant', ['correct_product', 'explicit_bridge', 'repeated_same_product', 'unstructured_title'])
def test_do_not_infer_wrong_scope_from_distance_alone(variant):
    rec, spans, value = case('Alpha A100' if variant == 'repeated_same_product' else 'Beta B200')
    permission = value[NAME]['permissions'][0]
    if variant == 'correct_product':
        permission['product'] = 2
    if variant == 'explicit_bridge':
        permission['target_sources'] = [2, 5, 7]
    facts = decode(json.dumps(value), spans, rec)
    if variant == 'unstructured_title':
        rec = copy.deepcopy(rec)
        rec['docs'][0]['text'] = rec['docs'][0]['text'].replace('모델명', '제원표')
        assert form_blocks(rec) == []
    assert permission_link_issues(facts, rec) == []


def test_warranty_constraint_is_flagged_without_reinterpreting_model_judgment():
    rec, spans, value = case()
    old = spans[-1]
    text = '데스크톱은 완제품으로 모든 부품이 동일 제조사 보증을 보장해야 함'
    rec['docs'][0]['text'] = rec['docs'][0]['text'][:old.start] + text
    spans[-1] = Span(0, '규격서', old.start, old.start+len(text), text)
    value[NAME]['permissions'][0].update(product=2, attribute='brand_or_model', effect='prohibited')
    value[NAME]['judgment'].update(v=1, e=5)
    row, details = review(rec, {'text': json.dumps(value)}, {'spans': spans, 'items': [9]})
    assert row['v9'] == 1
    assert details[0]['relationship_issues'][0]['kind'] == 'warranty_consistency_used_as_model_prohibition'


def test_length_limit_is_observed_without_claiming_truncation_or_changing_value():
    rec, spans, value = case()
    value[NAME]['permissions'] = []
    value[NAME]['judgment']['reason'] = '가' * 110
    row, details = review(rec, {'text': json.dumps(value)}, {'spans': spans, 'items': [9]})
    assert row['v9'] == 0
    assert details[0]['relationship_issues'][0]['kind'] == 'reason_at_schema_length_limit'
