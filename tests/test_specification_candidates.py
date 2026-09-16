import copy
import dataclasses
import json

import pytest

from submission.pps.generation_contract import validate_grammar
from submission.pps.retrieval import Span
from submission.pps.source_units import unitize, validate
from submission.pps.specification_candidates import (
    NAME, MAX_REVIEW_CANDIDATES, decode_review, inventory, review_schema,
)


def source(text, ranges=None):
    record = {'id': 'synthetic', 'docs': [{'type': '규격서', 'text': text}]}
    spans = [Span(0, '규격서', lo, hi, text[lo:hi]) for lo, hi in (ranges or [(0, len(text))])]
    return record, unitize(spans)


def answers(plan):
    return {NAME: {c['key']: {'specificity': 'unknown', 'role': 'unknown',
        'requirement': 'unknown', 'scope_sources': [], 'permission_sources': [],
        'permission_scope': 'unclear', 'exception_sources': []} for c in plan['candidates']}}


def decode(record, spans, plan, obj):
    return decode_review(json.dumps(obj, ensure_ascii=False), plan, record, spans)


def test_candidates_preserve_occurrences_coordinates_and_uncertified_semantics():
    record, spans = source('규격\n1) CPU: Intel Q7 이상\nNetwork / NIC: LinkX-7\n'
                           '(모델 WKA-167 )\n제 조 사： KGM 또는 동급 이상\nCPU: Intel Q7 이상\n')
    plan = inventory(record, spans)
    assert [c['value_source']['text'] for c in plan['candidates']] == [
        'Intel Q7 이상', 'LinkX-7', 'WKA-167', 'KGM 또는 동급 이상', 'Intel Q7 이상']
    for candidate in plan['candidates']:
        for name in ('label_source', 'value_source'):
            loc = candidate[name]
            assert record['docs'][loc['doc_index']]['text'][loc['start']:loc['end']] == loc['text']
        assert candidate['source_units']
        assert not candidate['unique_name_certified'] and not candidate['purchase_role_certified']
    assert not plan['absence_verified']


def test_source_order_changes_only_unit_reference_numbers_and_provenance():
    record, spans = source('CPU: Q7\nChipset: G10\n')
    first, second = inventory(record, spans), inventory(record, list(reversed(spans)))
    strip = lambda p: [{k: v for k, v in c.items() if k != 'source_units'} for c in p['candidates']]
    assert strip(first) == strip(second)
    assert first['units_sha256'] != second['units_sha256']


@pytest.mark.parametrize('text', ['모델명\n\nX9\n', '모델명:\n\n\nX9\n',
                                  '모델명\n수량\nX9\n', '모델명\n2. 기타 조건\n'])
def test_blank_or_header_is_not_skipped_to_invent_a_value(text):
    record, spans = source(text)
    candidates = inventory(record, spans)['candidates']
    assert len(candidates) == 1 and candidates[0]['value_source'] is None


@pytest.mark.parametrize('label', ['CPU', 'CPU:', '모 델 명：'])
def test_immediate_value_is_only_an_association_candidate(label):
    record, spans = source(label + '\nIntel Q7\n')
    plan = inventory(record, spans)
    assert plan['candidates'][0]['value_source']['text'] == 'Intel Q7'
    obj = answers(plan)
    obj[NAME]['C1'].update(specificity='named', role='new_component', requirement='mandatory',
                           scope_sources=[1], permission_scope='not_observed')
    receipt = decode(record, spans, plan, obj)
    assert receipt['unresolved_candidates'] == ['C1']
    assert receipt['model_classified_candidates'] == []


def test_pipe_headers_do_not_reconstruct_columns_or_read_the_next_row_as_one_value():
    record, spans = source('품명 | 모델명 | 제조사\n서버 | X9 | Q회사\nCPU: Q7 | Chipset: G10\n')
    candidates = inventory(record, spans)['candidates']
    assert [c['value_source']['text'] if c['value_source'] else None for c in candidates] == [None, None, 'Q7', 'G10']


FLATTENED_TABLE = '''규 격 서

COMMODITY DESCRIPTION

품목번호

Item No.

품 명

Description

모델명

세부품명번호

단위

Unit

수량

Q'ty

시험장비

Atlas X9

4111541101

System

Ⅰ. 용도
'''


def test_flattened_table_discovery_is_opt_in_and_preserves_legacy_versions():
    record, spans = source(FLATTENED_TABLE)
    legacy = inventory(record, spans)
    assert legacy['version'] == 1
    assert len(legacy['candidates']) == 1
    assert legacy['candidates'][0]['value_source'] is None
    assert decode(record, spans, legacy, answers(legacy))['declared_candidates_answered'] == 1

    typed = inventory(record, spans, include_flattened=True)
    assert typed['version'] == 3
    candidate, = typed['candidates']
    assert candidate['syntax'] == 'flattened_typed_model_column'
    assert candidate['value_source']['text'] == 'Atlas X9'
    assert candidate['typed_relation_certified']
    assert set(candidate['relation_sources']) == {
        'product_source', 'model_source', 'catalog_source', 'unit_source'}
    for source_ref in candidate['relation_sources'].values():
        text = record['docs'][source_ref['doc_index']]['text']
        assert text[source_ref['start']:source_ref['end']] == source_ref['text']
    assert decode(record, spans, typed, answers(typed))['declared_candidates_answered'] == 1
    assert inventory(record, spans, include_supply=True, include_flattened=True)['version'] == 4


@pytest.mark.parametrize('flag', [0, 1, None, 'yes'])
def test_inventory_feature_flags_reject_truthy_or_falsy_non_booleans(flag):
    record, spans = source('모델명: X100\n')
    with pytest.raises(ValueError, match='feature flags must be booleans'):
        inventory(record, spans, include_supply=flag)
    with pytest.raises(ValueError, match='feature flags must be booleans'):
        inventory(record, spans, include_flattened=flag)


def test_flattened_relation_is_not_reconstructed_across_unoffered_source():
    end = FLATTENED_TABLE.index('Atlas X9')
    record, spans = source(FLATTENED_TABLE, [(0, end)])
    plan = inventory(record, spans, include_flattened=True)
    assert plan['version'] == 3
    assert all(candidate['syntax'] != 'flattened_typed_model_column'
               for candidate in plan['candidates'])


def test_omitted_words_and_different_documents_cannot_supply_a_label_value():
    text = '모델명\n확인되지 않음\nX9\n'
    record, spans = source(text, [(0, len('모델명\n')), (text.index('X9'), len(text))])
    assert inventory(record, spans)['candidates'][0]['value_source'] is None
    record['docs'].append({'type': '규격서', 'text': 'X9\n'})
    spans = [spans[0], Span(1, '규격서', 0, 3, 'X9\n')]
    assert inventory(record, spans)['candidates'][0]['value_source'] is None


def test_partial_line_cannot_become_a_label_and_truncated_value_stays_unresolved():
    text = '참고로 사용하는 CPU: Q7\nCPU: Q7-900\n'
    record, spans = source(text, [(text.index('CPU'), text.index('\n')), (text.rindex('CPU'), len(text) - 5)])
    plan = inventory(record, spans)
    assert len(plan['candidates']) == 1
    assert plan['candidates'][0]['syntax'] == 'partial_field_value'
    obj = answers(plan)
    obj[NAME]['C1'].update(specificity='named', role='new_component', requirement='mandatory',
                           scope_sources=[2], permission_scope='not_observed')
    assert decode(record, spans, plan, obj)['unresolved_candidates'] == ['C1']


def test_containment_phrase_is_discovered_without_certifying_purchase_relation():
    record, spans = source('수신기는 Maxwell 7칩이 내장된 제품이며 기존 참고 모델이다.\n')
    candidate, = inventory(record, spans)['candidates']
    assert candidate['value_source']['text'] == 'Maxwell 7'
    assert not candidate['purchase_role_certified']


def test_empty_declared_inventory_is_not_absence_and_empty_response_still_has_contract():
    record, spans = source('DJI 아바타2 1개\n')
    plan = inventory(record, spans)
    assert plan['candidates'] == []  # This declared field syntax cannot discover every product name.
    receipt = decode(record, spans, plan, answers(plan))
    assert not receipt['absence_verified'] and receipt['declared_candidates_answered'] == 0
    with pytest.raises(ValueError):
        decode(record, spans, plan, {})


@pytest.mark.parametrize('change', ['missing', 'extra', 'bool_ref', 'float_ref', 'mixed_float_ref', 'unknown_ref', 'double_key', 'bad_value'])
def test_exhaustive_response_boundary_rejects_missing_extra_and_invalid_answers(change):
    record, spans = source('CPU: Q7\nChipset: G10\n')
    plan, obj = inventory(record, spans), None
    obj = answers(plan)
    if change == 'missing': del obj[NAME]['C2']
    if change == 'extra': obj[NAME]['C3'] = copy.deepcopy(obj[NAME]['C1'])
    if change == 'bool_ref': obj[NAME]['C1']['scope_sources'] = [True]
    if change == 'float_ref': obj[NAME]['C1']['scope_sources'] = [1.0]
    if change == 'mixed_float_ref': obj[NAME]['C1']['scope_sources'] = [1, 1.0]
    if change == 'unknown_ref': obj[NAME]['C1']['scope_sources'] = [len(spans) + 1]
    if change == 'bad_value': obj[NAME]['C1']['role'] = 'definitely_legal'
    if change == 'double_key':
        value = json.dumps(obj)
        value = value.replace('"specificity": "unknown"', '"specificity": "named", "specificity": "unknown"', 1)
        with pytest.raises(ValueError): decode_review(value, plan, record, spans)
    else:
        with pytest.raises(ValueError): decode(record, spans, plan, obj)


@pytest.mark.parametrize('change', ['plan_value', 'plan_key', 'plan_count', 'plan_bool', 'version_bool', 'source', 'unselected_source'])
def test_plan_cannot_be_reused_with_forged_or_different_current_sources(change):
    record, spans = source('CPU: Q7\n별도 원문\n')
    plan, obj = inventory(record, spans), None
    obj = answers(plan)
    if change == 'plan_value': plan['candidates'][0]['value_source']['text'] = 'X9'
    if change == 'plan_key': plan['candidates'][0]['key'] = 'C9'
    if change == 'plan_count': plan['unit_count'] += 1
    if change == 'plan_bool': plan['unit_count'] = True
    if change == 'version_bool': plan['version'] = True
    if change == 'source': record['docs'][0]['text'] = record['docs'][0]['text'].replace('Q7', 'X9')
    if change == 'unselected_source': spans[-1] = dataclasses.replace(spans[-1], text='가' * len(spans[-1].text))
    with pytest.raises(ValueError): decode(record, spans, plan, obj)


@pytest.mark.parametrize('patch', [
    {'role': 'new_whole_product'}, {'requirement': 'mandatory'},
    {'permission_scope': 'this_candidate'},
    {'permission_scope': 'not_observed', 'permission_sources': [1]},
])
def test_classified_relationship_requires_its_own_observed_source(patch):
    record, spans = source('CPU: Q7\n')
    plan = inventory(record, spans)
    obj = answers(plan)
    obj[NAME]['C1'].update(patch)
    with pytest.raises(ValueError): decode(record, spans, plan, obj)


def test_complete_model_answers_are_reported_as_assertions_not_semantic_truth():
    record, spans = source('CPU: Q7\n동등 제품 허용 여부를 별도로 검토한다.\n')
    plan = inventory(record, spans)
    obj = answers(plan)
    obj[NAME]['C1'].update(specificity='named', role='new_component', requirement='mandatory',
                           scope_sources=[1, 1], permission_scope='not_observed')
    receipt = decode(record, spans, plan, obj)
    assert receipt['model_classified_candidates'] == ['C1']
    assert not receipt['semantic_truth_certified'] and not receipt['legal_judgment_inferred']
    assert receipt['reviews']['C1']['scope_sources'] == [1, 1]


def test_unbound_header_value_cannot_be_classified_as_named():
    record, spans = source('모델명 | 수량\n')
    plan = inventory(record, spans)
    obj = answers(plan)
    obj[NAME]['C1']['specificity'] = 'named'
    with pytest.raises(ValueError): decode(record, spans, plan, obj)


@pytest.mark.parametrize('count', [0, 1, 8])
def test_actual_engine_grammar_accepts_exhaustive_review_without_enabling_default(count):
    record = {'id': 'synthetic', 'docs': []}
    spans = []
    if count: record, spans = source('CPU: Q7\n' * count)
    plan = inventory(record, spans)
    validate_grammar(review_schema(plan))


def test_schema_refuses_unbounded_plans_instead_of_silently_truncating():
    record, spans = source('CPU: Q7\n' * (MAX_REVIEW_CANDIDATES + 1))
    plan = inventory(record, spans)
    assert len(plan['candidates']) == MAX_REVIEW_CANDIDATES + 1
    with pytest.raises(ValueError): review_schema(plan)


def test_very_long_numbering_is_not_a_slow_or_invented_heading():
    record, spans = source('1' * 20000 + ') CPU: Q7\n')
    assert inventory(record, spans)['candidates'] == []


@pytest.mark.parametrize('patch', [{'doc_index': True}, {'start': False}, {'end': 9.0}, {'text': None}])
def test_common_source_validation_rejects_malformed_even_unselected_units(patch):
    record, spans = source('CPU: Q7\n')
    spans[0] = dataclasses.replace(spans[0], **patch)
    with pytest.raises(ValueError): validate(spans, record)
