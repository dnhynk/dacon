"""Exhaustive observed-candidate answers remain fallible source-addressed judgments."""
import copy
import dataclasses
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import jsonschema
import pytest

from submission.b4_entry import B4Pipeline, parse_error
from submission.pps import specification_candidate_review as review, specification_candidates as candidates
from submission.pps.generation_contract import generation_schema, validate_grammar, prepared_preflight
from submission.pps.pipeline import VLLMRunner
from submission.pps.retrieval import Span
from submission.pps.source_units import unitize
from tools.audit_specification_candidates import unknown_response


def fixture(text='품명: 시험 장비\n모델명: Atlas R7\n제조사: Atlas\n동등 이상의 수량을 납품한다.'):
    rec = {'id': 'synthetic', 'docs': [{'doc_id': 'D1', 'type': '규격서', 'text': text}]}
    units = unitize([Span(0, '규격서', 0, len(text), text)])
    plan = candidates.inventory(rec, units)
    payload = unknown_response(plan)
    for answer in payload[review.NAME].values():
        answer.update(permission_attribute='unknown', permission_effect='unknown')
    payload.update(unresolved='범위 미확정', judgment={'reason': '현재 근거와 미확정 범위를 검토', 'v': 0, 'e': 0})
    packet = {'items': [9], 'family': 'A', 'spans': units, 'specification_inventory': plan,
        'generation': {'response_format': review.FORMAT, 'specification_inventory': plan}}
    return rec, units, plan, payload, packet


def response(payload):
    return {'text': json.dumps(payload, ensure_ascii=False), 'finish_reason': 'stop'}


def effective(packet):
    return generation_schema(review.FORMAT, len(packet['spans']), packet['items'],
                             specification_inventory=packet['specification_inventory'])


def test_every_candidate_requires_an_answer_even_when_unknown():
    rec, units, plan, payload, packet = fixture()
    assert len(plan['candidates']) == 2
    assert parse_error(packet, response(payload)) is None
    facts = review.decode(response(payload)['text'], units, plan, rec)
    assert facts['declared_candidates_answered'] == 2 and facts['unresolved_candidates'] == ['C1', 'C2']
    assert facts['unlisted_source_candidates_possible'] and not facts['semantic_truth_certified']
    for key in list(payload[review.NAME]):
        missing = copy.deepcopy(payload)
        del missing[review.NAME][key]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(missing, effective(packet))
        assert parse_error(packet, response(missing)).startswith('ValueError:')


@pytest.mark.parametrize('value', [0, 1])
def test_complete_reviews_preserve_the_model_bit_without_certifying_it(value):
    rec, units, plan, payload, packet = fixture()
    payload['judgment'].update(v=value, e=2 if value else 0)
    raw = response(payload)
    original = copy.deepcopy((rec, packet, payload))
    row, details = B4Pipeline.__new__(B4Pipeline).consume(rec, packet, raw)
    assert row['v9'] == value and row['e9'] == (units[1].text if value else '')
    assert not details[0]['semantic_validation_complete']
    assert not details[0]['facts']['absence_verified']
    assert (rec, packet, payload) == original


def test_empty_syntax_inventory_does_not_force_a_negative_judgment():
    rec, units, plan, payload, packet = fixture('계약업체는 Atlas사의 센서를 납품하여야 한다.')
    assert not plan['candidates']
    payload['judgment'].update(v=1, e=1)
    row, details = review.review(rec, response(payload), packet)
    assert row['v9'] == 1 and details[0]['facts']['unlisted_source_candidates_possible']


def test_an_unbound_table_header_cannot_be_named_by_the_model():
    rec, units, plan, payload, packet = fixture('모델명 | 수량 | 단위\nAtlas R7 | 2 | 식')
    assert plan['candidates'][0]['value_source'] is None
    payload[review.NAME]['C1']['specificity'] = 'named'
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, effective(packet))
    assert parse_error(packet, response(payload))


@pytest.mark.parametrize('change', ['float_ref', 'duplicate_float', 'float_e', 'float_v', 'bool_ref', 'source_zero'])
def test_invalid_native_numeric_types_are_rejected_before_source_access(change):
    rec, units, plan, payload, packet = fixture()
    answer = payload[review.NAME]['C1']
    if change == 'float_ref': answer['scope_sources'] = [1.0]
    if change == 'duplicate_float': answer['scope_sources'] = [1, 1.0]
    if change == 'bool_ref': answer['scope_sources'] = [True]
    if change == 'source_zero': answer['scope_sources'] = [0]
    if change == 'float_e': payload['judgment']['e'] = 1.0
    if change == 'float_v': payload['judgment']['v'] = 1.0
    assert parse_error(packet, response(payload)).startswith('ValueError:')


@pytest.mark.parametrize('change', ['missing_role_source', 'missing_permission_source', 'absence_with_effect'])
def test_a_known_relationship_needs_its_own_observed_source(change):
    rec, units, plan, payload, packet = fixture()
    answer = payload[review.NAME]['C1']
    if change == 'missing_role_source': answer['role'] = 'new_whole_product'
    if change == 'missing_permission_source': answer['permission_effect'] = 'allowed'
    if change == 'absence_with_effect': answer.update(permission_scope='not_observed', permission_effect='prohibited')
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, effective(packet))
    assert parse_error(packet, response(payload))


def test_known_permission_scope_does_not_hide_unknown_attribute_or_effect():
    rec, units, plan, payload, packet = fixture()
    answer = payload[review.NAME]['C1']
    answer.update(specificity='named', role='new_whole_product', requirement='mandatory',
                  scope_sources=[1, 2], permission_scope='this_candidate', permission_sources=[4])
    facts = review.decode(response(payload)['text'], units, plan, rec)
    assert 'C1' in facts['unresolved_candidates'] and 'C1' not in facts['model_classified_candidates']


@pytest.mark.parametrize('change', ['inventory_subset', 'both_inventory_subset', 'unit_value', 'extra_candidate'])
def test_prepared_inventory_is_recomputed_from_the_offered_original_source(change):
    rec, units, plan, payload, packet = fixture()
    packet = copy.deepcopy(packet)
    if change == 'inventory_subset':
        packet['specification_inventory'] = copy.deepcopy(plan)
        packet['specification_inventory']['candidates'].pop()
    if change == 'both_inventory_subset':
        packet['specification_inventory']['candidates'].pop()
    if change == 'unit_value':
        packet['spans'][0] = dataclasses.replace(units[0], text=units[0].text.replace('시험', '실험'))
    if change == 'extra_candidate':
        packet['specification_inventory']['candidates'].append(copy.deepcopy(plan['candidates'][0]))
    with pytest.raises(ValueError):
        review.review(rec, response(payload), packet)


def test_generator_requires_inventory_and_rejects_wrong_format():
    rec, units, plan, payload, packet = fixture()
    with pytest.raises(ValueError, match='complete prepared'):
        generation_schema(review.FORMAT, len(units), (9,))
    with pytest.raises(ValueError, match='candidate review format'):
        generation_schema('specification_scope', len(units), (9,), specification_inventory=plan)
    validate_grammar(effective(packet))


def test_actual_token_mask_requires_all_observed_candidates_and_accepts_unknowns():
    from transformers import AutoTokenizer
    import llguidance
    import llguidance.hf
    path = Path(__file__).resolve().parents[1] / 'models/gemma-tokenizer'
    if not path.is_dir():
        pytest.skip('Fixed local tokenizer required')
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    rec, units, plan, payload, packet = fixture()
    grammar = llguidance.LLMatcher.grammar_from_json_schema(effective(packet), defaults={'whitespace_flexible': False})
    ll_tokenizer = llguidance.hf.from_tokenizer(tokenizer)
    missing = copy.deepcopy(payload)
    del missing[review.NAME]['C2']
    no_source = copy.deepcopy(payload)
    no_source[review.NAME]['C1']['role'] = 'new_whole_product'
    known = copy.deepcopy(payload)
    known[review.NAME]['C1'].update(specificity='named', role='new_whole_product', requirement='mandatory',
        scope_sources=[1, 2], permission_scope='this_candidate', permission_sources=[4],
        permission_attribute='quantity', permission_effect='allowed')
    for obj, expected in [(payload, True), (missing, False), (no_source, False), (known, True)]:
        matcher = llguidance.LLMatcher(ll_tokenizer, grammar)
        ids = tokenizer.encode(json.dumps(obj, ensure_ascii=False, separators=(',', ':')), add_special_tokens=False)
        assert bool(matcher.consume_tokens(ids) and matcher.is_accepting()) == expected


def test_sampler_receives_the_exact_per_notice_inventory_schema(monkeypatch):
    rec, units, plan, payload, packet = fixture()
    packet['token_ids'] = [1, 2]
    seen = []
    def generate(inputs, sampling_params, use_tqdm):
        seen.extend(sampling_params)
        return [SimpleNamespace(outputs=[SimpleNamespace(text='{}', finish_reason='stop', token_ids=[1])])]
    monkeypatch.setitem(sys.modules, 'vllm', SimpleNamespace(SamplingParams=lambda **kwargs: kwargs))
    monkeypatch.setitem(sys.modules, 'vllm.sampling_params',
                        SimpleNamespace(StructuredOutputsParams=lambda **kwargs: kwargs))
    runner = VLLMRunner.__new__(VLLMRunner)
    runner.config = SimpleNamespace(seed=0, max_output_tokens=2048, enable_thinking=False,
        response_format=review.FORMAT, thinking_budget_for=lambda _: 0)
    runner.llm = SimpleNamespace(generate=generate)
    runner.generate([packet])
    assert seen[0]['structured_outputs']['json'] == effective(packet)
    assert seen[0]['temperature'] == 0


def test_preflight_compiles_the_required_inventory_instead_of_all_optional_keys():
    rec, units, plan, payload, packet = fixture()
    packet['request_key'] = 'candidate:synthetic'
    report = prepared_preflight([packet])
    assert report['status'] == 'PASS' and report['requests'] == 1
    assert report['unique_effective_schemas'] == 1 and not report['model_loaded']
    assert report['original_source_verification_separate']
    packet['generation_schema_sha256'] = 'tampered'
    with pytest.raises(ValueError, match='digest changed'):
        prepared_preflight([packet])


def test_preflight_cannot_bypass_a_missing_inventory_or_duplicate_request():
    rec, units, plan, payload, packet = fixture()
    packet['request_key'] = 'candidate:synthetic'
    with pytest.raises(ValueError, match='unique identities'):
        prepared_preflight([packet, packet])
    del packet['generation']['specification_inventory']
    with pytest.raises(ValueError, match='complete prepared'):
        prepared_preflight([packet])
