"""Observed qualifiers and numbering cannot become fabricated semantic links."""
import copy
import dataclasses
import json
from pathlib import Path

import jsonschema
import pytest

from submission.b4_entry import B4Pipeline, parse_error
from submission.pps import (specification_context as context,
                            specification_candidate_review as review,
                            specification_candidates as candidates)
from submission.pps.generation_contract import generation_schema, prepared_preflight
from submission.pps.retrieval import Span
from submission.pps.source_units import unitize
from tests.test_specification_candidate_review import fixture, response


def example(text='1. 데스크톱컴퓨터\n1) CPU: Atlas R7\n2) 제작사: Acme 또는 동급 이상\n2. 대체 조건\n상기 규격과 동등 이상의 제품을 납품할 수 있다.'):
    rec, units, base_plan, payload, packet = fixture(text)
    observed = context.inventory(rec, units)
    plan = {**base_plan, 'source_context': observed}
    payload = {context.NAME: {o['key']: {
        'relevance': 'unknown', 'target_candidates': [], 'target_level': 'unknown',
        'attribute': 'unknown', 'effect': 'unknown', 'target_sources': []}
        for o in observed['permission_observations']}, **payload}
    packet['specification_inventory'] = plan
    packet['generation']['specification_inventory'] = plan
    return rec, units, plan, payload, packet


def effective(packet):
    return generation_schema(review.FORMAT, len(packet['spans']), (9,),
                             specification_inventory=packet['specification_inventory'])


def test_inline_equivalence_keeps_both_exact_source_intervals():
    rec, units, plan, payload, packet = example()
    original = copy.deepcopy(rec)
    candidate = plan['source_context']['candidate_contexts'][1]
    assert candidate['core_value_source']['text'] == 'Acme'
    assert candidate['inline_qualifier_source']['text'] == '또는 동급 이상'
    for key in ('core_value_source', 'inline_qualifier_source'):
        loc = candidate[key]
        assert rec['docs'][loc['doc_index']]['text'][loc['start']:loc['end']] == loc['text']
    assert rec == original and not candidate['specificity_certified']


@pytest.mark.parametrize('value', ['"Acme 또는 동급 이상"', '(Acme 또는 동급 이상)', 'Acme (R7 또는 동급 이상', 'Acme R7'])
def test_a_quoted_or_damaged_name_is_not_split_into_a_certified_model(value):
    rec, units, plan, payload, packet = example('모델명: '+value)
    c = plan['source_context']['candidate_contexts'][0]
    assert c['inline_qualifier_source'] is None and c['core_value_source']['text'] == value


def test_cpu_numbering_retains_parent_but_does_not_certify_component_role():
    rec, units, plan, payload, packet = example()
    c = plan['source_context']['candidate_contexts'][0]
    assert [units[n-1].text.strip() for n in c['numbering_parent_sources']] == ['1. 데스크톱컴퓨터']
    assert not c['purchase_parent_certified']


def _bounded_form_case(permission):
    text = ('규 격 서\n구 분 | 품 명 | 단 위 | 수 량\n1 | 측량장비 | 식 | 1\n'
            'Ⅰ. 사양\n1. CPU: Atlas R7\nⅣ. 기타사항\n' + permission)
    rec, units, base_plan, payload, packet = fixture(text)
    observed = context.inventory(rec, units)
    plan = {**base_plan, 'source_context': observed}
    payload = {context.NAME: {o['key']: {
        'relevance': 'unknown', 'target_candidates': [], 'target_level': 'unknown',
        'attribute': 'unknown', 'effect': 'unknown', 'target_sources': []}
        for o in observed['permission_observations']}, **payload}
    packet.update(batch='S9', specification_inventory=plan)
    packet['generation']['specification_inventory'] = plan
    return rec, units, plan, payload, packet


def test_repeated_table_specification_has_exact_candidate_and_permission_section():
    rec, units, plan, payload, packet = _bounded_form_case(
        '규격서 상의 기준과 동등하거나 그 이상인 장비를 납품하여야 한다.')
    candidate = plan['source_context']['candidate_contexts'][0]
    permission = plan['source_context']['permission_observations'][0]
    assert candidate['bounded_specification_section'] == 'D0:B1'
    assert permission['bounded_specification_section'] == 'D0:B1'
    assert permission['scope_syntax'] == 'explicit_all_specification_criteria'
    assert not permission['target_link_certified']


@pytest.mark.parametrize('permission,accepted', [
    ('규격서 상의 기준과 동등하거나 그 이상인 장비를 납품하여야 한다.', True),
    ('상기 사항과 동등 이상의 조건을 충족하는 제품으로 납품 가능함.', False),
])
def test_optional_negative_uses_only_reviewed_explicit_whole_specification_scope(permission, accepted):
    rec, units, plan, payload, packet = _bounded_form_case(permission)
    candidate = plan['candidates'][0]
    observed = plan['source_context']['permission_observations'][0]
    key = candidate['key']
    payload[context.NAME][observed['key']].update(
        relevance='permission_or_requirement', target_candidates=[key],
        target_level='whole_product', attribute='performance', effect='allowed',
        target_sources=candidate['source_units'])
    payload[review.NAME][key].update(
        specificity='named', role='new_component', requirement='mandatory',
        scope_sources=candidate['source_units'])
    payload['unresolved'] = ''
    payload['judgment'] = {'reason':'구조와 허용 범위를 함께 검토함','v':0,'e':0}
    row, details = B4Pipeline.__new__(B4Pipeline).consume(rec, packet, response(payload))
    assert bool(row) is accepted
    assert details[-1]['deferred'] is (not accepted)
    assert details[0]['facts']['context_relations_applied'][0]['candidate'] == key
    assert key not in details[0]['facts']['unresolved_candidates']


def test_a_cpu_sold_as_a_separate_top_level_item_has_no_computer_parent():
    rec, units, plan, payload, packet = example('1. 컴퓨터\n보증 1년\n2. CPU: Atlas R7\n수량 3개')
    assert plan['source_context']['candidate_contexts'][0]['numbering_parent_sources'] == []


def test_missing_source_between_title_and_candidate_cannot_prove_a_parent():
    text = '1. 컴퓨터\n이 사이에는 별도의 2번 품목이 기재돼 있으나 발췌에서 생략됐다.\n1) CPU: Atlas R7'
    rec = {'id': 'gapped', 'docs': [{'doc_id': 'D0', 'type': '규격서', 'text': text}]}
    start = text.index('1) CPU')
    units = unitize([Span(0, '규격서', 0, text.index('\n')+1, text[:text.index('\n')+1]),
                     Span(0, '규격서', start, len(text), text[start:])])
    result = context.inventory(rec, units)
    assert result['candidate_contexts'][0]['numbering_parent_sources'] == []
    assert not result['source_text_added']


def test_an_unbound_model_header_does_not_get_an_invented_core_value():
    _, _, plan, _, _ = example('모델명 | 수량 | 단위\nAtlas R7 | 2 | 개')
    assert plan['source_context']['candidate_contexts'][0]['core_value_source'] is None


def test_a_wrapped_permission_retains_its_actual_obligation_tail():
    rec, units, plan, payload, packet = example('1. 본 규격과 동등 이상의 규격의\n신품이어야 함\n2. 별도의 조건')
    p = plan['source_context']['permission_observations'][0]
    assert [units[n-1].text.strip() for n in p['source_units']] == ['1. 본 규격과 동등 이상의 규격의', '신품이어야 함']
    assert p['continuation_stops'] == ['sentence_end']


def test_another_numbered_operative_clause_is_not_a_continuation():
    rec, units, plan, payload, packet = example('모델명: Atlas 또는 동급 이상\n2. 납품 후 공동수급은 허용하지 않습니다.')
    assert plan['source_context']['permission_observations'][0]['source_units'] == [1]


def test_an_unobserved_continuation_cannot_be_read_through_a_gap():
    text = '동등 이상의 제품의\n필수 허용 조건은 이 발췌에서 빠졌다.\n보증을 확인한다.'
    rec = {'id': 'gap', 'docs': [{'doc_id': 'D0', 'type': '규격서', 'text': text}]}
    first, last = text.index('\n')+1, text.index('보증')
    units = unitize([Span(0, '규격서', 0, first, text[:first]), Span(0, '규격서', last, len(text), text[last:])])
    result = context.inventory(rec, units)
    assert result['permission_observations'][0]['source_units'] == [1]
    assert result['permission_observations'][0]['continuation_stops'] == ['unobserved_text_gap']


def test_context_capacity_is_visible_instead_of_fabricated_completion():
    rec, units, plan, payload, packet = example('동등 이상의 제품의\n'+'미완결된 조건과\n'*8)
    p = plan['source_context']['permission_observations'][0]
    assert len(p['source_units']) == context.MAX_CONTEXT_UNITS
    assert p['continuation_stops'] == ['context_unit_limit']


def test_repeated_permission_occurrences_in_different_documents_stay_separate():
    text = '동등 이상의 물품을 납품한다.'
    rec = {'id': 'repeat', 'docs': [{'doc_id': 'D0', 'type': '규격서', 'text': text},
                                   {'doc_id': 'D1', 'type': '규격서', 'text': text}]}
    units = [Span(i, '규격서', 0, len(text), text) for i in (1, 0)]
    plan = context.inventory(rec, units)
    assert len(plan['permission_observations']) == 2
    assert [p['source_units'] for p in plan['permission_observations']] == [[2], [1]]


def test_every_observed_permission_requires_a_response_and_unknown_remains_valid():
    rec, units, plan, payload, packet = example()
    assert parse_error(packet, response(payload)) is None
    assert list(effective(packet)['properties'])[0] == context.NAME
    for key in list(payload[context.NAME]):
        omitted = copy.deepcopy(payload)
        del omitted[context.NAME][key]
        assert parse_error(packet, response(omitted)).startswith('ValueError:')
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(omitted, effective(packet))


@pytest.mark.parametrize('target', [0, 1.0, True, -1, 9999])
def test_target_addresses_require_strict_valid_integers(target):
    rec, units, plan, payload, packet = example()
    payload[context.NAME]['P1']['target_sources'] = [target]
    assert parse_error(packet, response(payload)).startswith('ValueError:')


def test_a_float_cannot_be_hidden_by_an_equal_integer_target_address():
    rec, units, plan, payload, packet = example()
    payload[context.NAME]['P1']['target_sources'] = [1, 1.0]
    assert parse_error(packet, response(payload)).startswith('ValueError:')


@pytest.mark.parametrize('change', ['missing_target_source', 'nonexistent_candidate', 'other_context_with_link', 'omitted_plan_observation'])
def test_unaddressed_or_inconsistent_context_links_do_not_survive_validation(change):
    rec, units, plan, payload, packet = example()
    p = payload[context.NAME]['P1']
    if change == 'missing_target_source': p['target_candidates'] = ['C1']
    if change == 'nonexistent_candidate': p.update(target_candidates=['C999'], target_sources=[1])
    if change == 'other_context_with_link': p.update(relevance='other_context', target_candidates=['C1'], target_sources=[1])
    if change == 'omitted_plan_observation':
        packet['specification_inventory']['source_context']['permission_observations'].pop()
    assert parse_error(packet, response(payload)).startswith('ValueError:')


def test_hidden_modifications_to_both_prepared_plan_copies_are_checked_against_source():
    rec, units, plan, payload, packet = example()
    packet['specification_inventory']['source_context']['candidate_contexts'][0]['numbering_parent_sources'] = [5]
    with pytest.raises(ValueError, match='selected original source'):
        review.decode(response(payload)['text'], units, plan, rec)
    with pytest.raises(ValueError, match='selected original source'):
        review.validate_prepared(rec, packet)


@pytest.mark.parametrize('value', [0, 1])
def test_explicit_permission_review_fills_the_redundant_candidate_omission(value):
    rec, units, plan, payload, packet = example()
    payload[context.NAME]['P1'].update(relevance='permission_or_requirement', target_candidates=['C1'],
        target_level='candidate', attribute='performance', effect='allowed', target_sources=[1, 2])
    payload[review.NAME]['C1'].update(specificity='named', role='new_component', requirement='mandatory',
        scope_sources=[1, 2], permission_scope='not_observed')
    payload['judgment'].update(v=value, e=2 if value else 0)
    assert parse_error(packet, response(payload)) is None
    row, details = B4Pipeline.__new__(B4Pipeline).consume(rec, packet, response(payload))
    facts = details[0]['facts']
    assert row['v9'] == value and 'C1' not in facts['unresolved_candidates']
    assert facts['effective_reviews']['C1']['permission_scope'] == 'this_candidate'
    assert facts['context_relations_applied'][0]['candidate'] == 'C1'
    assert facts['context_link_issues'][0]['kind'] == 'observed_target_link_but_candidate_permission_unobserved'
    assert facts['unresolved_context_link_issues'] == []
    assert not facts['permission_context_review']['semantic_truth_certified']


def test_empty_candidate_inventory_still_reviews_other_observed_permissions():
    rec, units, plan, payload, packet = example('기준과 동등한 물품을 납품한다.')
    assert not plan['candidates'] and len(payload[context.NAME]) == 1
    assert parse_error(packet, response(payload)) is None
    row, details = review.review(rec, response(payload), packet)
    assert row['v9'] == 0 and not details[0]['facts']['absence_verified']


def test_context_inventory_keeps_the_same_supply_candidate_version_end_to_end():
    text = ('규 격 서\n구 분 | 품 명 | 단 위 | 수 량\nGNSS 위성측량장비 : 3식\n'
            'CPU: Atlas R7\n규격서 상의 기준과 동등한 장비를 납품한다.')
    rec, units, _, payload, packet = fixture(text)
    base = candidates.inventory(rec, units, include_supply=True)
    observed = context.inventory(rec, units, include_supply=True)
    plan = {**base, 'source_context': observed}
    assert plan['version'] == 2
    assert observed['candidates_sha256'] == candidates._digest(base)
    packet['specification_inventory'] = plan
    packet['generation']['specification_inventory'] = plan
    assert review.validate_prepared(rec, packet) == plan


def test_actual_token_mask_requires_all_observations_and_preserves_unknown_option():
    from transformers import AutoTokenizer
    import llguidance
    import llguidance.hf
    tokenizer = AutoTokenizer.from_pretrained(Path(__file__).resolve().parents[1]/'models/gemma-tokenizer',
                                              local_files_only=True, trust_remote_code=False)
    rec, units, plan, payload, packet = example()
    grammar = llguidance.LLMatcher.grammar_from_json_schema(effective(packet), defaults={'whitespace_flexible': False})
    ll_tokenizer = llguidance.hf.from_tokenizer(tokenizer)
    missing = copy.deepcopy(payload)
    missing[context.NAME].pop('P1')
    for obj, expected in ((payload, True), (missing, False)):
        matcher = llguidance.LLMatcher(ll_tokenizer, grammar)
        ids = tokenizer.encode(json.dumps(obj, ensure_ascii=False, separators=(',', ':')), add_special_tokens=False)
        assert bool(matcher.consume_tokens(ids) and matcher.is_accepting()) == expected
    packet['request_key'] = 'observed_context:synthetic'
    assert prepared_preflight([packet])['status'] == 'PASS'


@pytest.mark.parametrize('change', ['none', 'source', 'search', 'role', 'extra_message', 'system', 'user', 'base_candidate'])
def test_contrast_audit_rejects_every_undeclared_control_change(change):
    from tools.score_specification_contrast import verify_context_pair
    rec, units, plan, payload, candidate = example()
    control = copy.deepcopy(candidate)
    control['specification_inventory'] = review._candidate_plan(plan)
    control['generation']['specification_inventory'] = control['specification_inventory']
    control['messages'] = [{'role': 'system', 'content': 'fixed task'}, {'role': 'user', 'content': 'fixed original source'}]
    candidate['messages'] = [{'role': 'system', 'content': 'fixed task'+review.CONTEXT_SYSTEM},
        {'role': 'user', 'content': 'fixed original source'+review.source_context_prompt(plan['source_context'])}]
    control['source_search'] = candidate['source_search'] = {'source_tokens': 123}
    if change == 'source': candidate['spans'] = []
    if change == 'search': candidate['source_search'] = {'source_tokens': 456}
    if change == 'role': candidate['messages'][0]['role'] = 'user'
    if change == 'extra_message': candidate['messages'].append({'role': 'user', 'content': 'extra hints'})
    if change == 'system': candidate['messages'][0]['content'] += ' undeclared task'
    if change == 'user': candidate['messages'][1]['content'] += ' extra source'
    if change == 'base_candidate': candidate['specification_inventory']['candidates'] = []
    if change == 'none':
        verify_context_pair(control, candidate)
    else:
        with pytest.raises(ValueError):
            verify_context_pair(control, candidate)
