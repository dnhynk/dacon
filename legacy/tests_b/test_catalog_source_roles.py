"""Complete role choices constrain source selection, never certify semantics."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import jsonschema
import pytest

from submission.b4_entry import parse_error
from submission.pps import catalog_semantics as sem, catalog_source_roles as roles, catalog_condition_review as literal
from submission.pps.generation_contract import generation_schema, validate_grammar
from submission.pps.pipeline import VLLMRunner
from submission.pps.retrieval import Span
from submission.pps.source_units import unitize
from tests.test_catalog_condition_review import setup, finding, packet_for, units
from tests.test_catalog_semantics import obj, response


def attach(rec, packet):
    built = roles.build(rec, packet['spans'], packet['catalog_conditions']['plan'])
    packet['catalog_conditions'].update(interpretation_mode=sem.FORMAT, source_roles=built)
    packet['generation'] = {'response_format': sem.FORMAT, 'catalog_roles': built['generation']}
    return packet


def effective(packet):
    return generation_schema(sem.FORMAT, len(packet['spans']), packet['items'],
                             catalog_roles=packet['generation']['catalog_roles'])


def test_role_choices_are_original_complete_and_do_not_change_meaning_fields():
    rec, packet, knowledge = setup(extra='동등 이상의 제품으로 납품할 수 있다.')
    original = copy.deepcopy((rec, packet['spans']))
    attach(rec, packet)
    claim = finding(packet)
    payload = obj(findings=[claim])
    for modality in ('required', 'optional', 'example', 'negated', 'unclear'):
        claim['modality'] = modality
        jsonschema.validate(payload, effective(packet))
    assert (rec, packet['spans']) == original
    assert not packet['catalog_conditions']['source_roles']['semantic_truth_certified']
    # The unresolved permission remains unresolved even after a valid role choice.
    claim['modality'] = 'required'
    row, _ = sem.review(rec, response(payload), packet, knowledge)
    assert row is None


def test_property_as_task_reference_is_blocked_and_is_not_a_quality_retry():
    rec, packet, knowledge = setup()
    attach(rec, packet)
    claim = finding(packet)
    claim['scope_units'] = claim['value_units'][:]
    payload = obj(findings=[claim])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, effective(packet))
    assert parse_error(packet, response(payload)) is None
    row, log = sem.review(rec, response(payload), packet, knowledge)
    assert row is None and log['gate'] == 'model_role_selection_outside_prepared_candidates'
    assert log['model_readings'] == payload


def test_a_multiline_name_must_be_returned_as_its_complete_array():
    rec, _, knowledge = setup()
    rec['docs'][0]['text'] = ('1. 품 명\n영 문\nCompute Mini Box\n국 문\n연산 장치\n'
                             '2. 규격\nCPU 아키텍처: ARM')
    packet = attach(rec, packet_for(rec, knowledge))
    candidate = packet['generation']['catalog_roles']['properties'][0]
    refs = candidate['scope_options'][0]
    assert len(refs) > 1
    claim = {**finding(packet), 'scope_units': refs, 'value_units': candidate['value_units']}
    jsonschema.validate(obj(findings=[claim]), effective(packet))
    claim['scope_units'] = refs[:-1]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(obj(findings=[claim]), effective(packet))


def test_no_local_named_source_leaves_an_unresolved_field_path():
    rec, packet, _ = setup()
    rec['docs'][0]['text'] = 'CPU 아키텍처: ARM'
    packet['spans'] = [Span(0, '공고문', 0, len(rec['docs'][0]['text']), rec['docs'][0]['text'])]
    attach(rec, packet)
    graph = packet['catalog_conditions']['source_roles']
    assert graph['unavailable'][0]['reason'] == 'no_complete_local_named_task'
    assert effective(packet)['properties']['findings']['maxItems'] == 0
    jsonschema.validate(obj(unresolved_fields=[{'code': '4321150102', 'field': 'cpu_architecture',
        'reason': '속성은 있으나 실제 구매 범위가 없다'}]), effective(packet))
    validate_grammar(effective(packet))


def test_unselected_original_permission_is_reported_without_becoming_absent():
    rec, packet, knowledge = setup()
    rec['docs'].append({'doc_id': 'conditions', 'type': '규격서',
                        'text': '동등한 다른 CPU 아키텍처도 납품할 수 있다.'})
    attach(rec, packet)
    graph = packet['catalog_conditions']['source_roles']
    assert any(x['reason'] == 'complete_permission_not_selected' for x in graph['unavailable'])
    assert graph['generation']['permission_options'] == []
    row, detail = sem.review(rec, response(obj(findings=[finding(packet)])), packet, knowledge)
    assert row is None
    assert detail['conditions'][0]['scope_issues']


@pytest.mark.parametrize('mutation', ['source', 'role', 'missing_half'])
def test_prepared_roles_are_recomputed_against_the_current_notice(mutation):
    rec, packet, _ = setup()
    attach(rec, packet)
    if mutation == 'source':
        rec['docs'][0]['text'] = rec['docs'][0]['text'].replace('ARM', 'x86')
    elif mutation == 'role':
        packet['generation']['catalog_roles'] = copy.deepcopy(packet['generation']['catalog_roles'])
        packet['generation']['catalog_roles']['properties'][0]['scope_options'] = [[3]]
    else:
        del packet['catalog_conditions']['source_roles']
    with pytest.raises(ValueError):
        roles.validate_prepared(rec, packet)


def test_sampler_receives_prepared_complete_role_constraints(monkeypatch):
    rec, packet, _ = setup()
    attach(rec, packet)
    packet['token_ids'] = [1, 2]
    captured = []
    def generate(inputs, sampling_params, use_tqdm):
        captured.extend(sampling_params)
        return [SimpleNamespace(outputs=[SimpleNamespace(text='{}', finish_reason='stop', token_ids=[1])])]
    monkeypatch.setitem(sys.modules, 'vllm', SimpleNamespace(SamplingParams=lambda **kwargs: kwargs))
    monkeypatch.setitem(sys.modules, 'vllm.sampling_params',
                        SimpleNamespace(StructuredOutputsParams=lambda **kwargs: kwargs))
    runner = VLLMRunner.__new__(VLLMRunner)
    runner.config = SimpleNamespace(seed=0, max_output_tokens=128, enable_thinking=False,
        response_format=sem.FORMAT, thinking_budget_for=lambda items: 0)
    runner.llm = SimpleNamespace(generate=generate)
    runner.generate([packet])
    assert captured[0]['structured_outputs']['json'] == effective(packet)
    assert captured[0]['temperature'] == 0 and captured[0]['max_tokens'] == 128


def test_actual_token_mask_accepts_valid_and_unresolved_but_blocks_wrong_roles():
    from transformers import AutoTokenizer
    import llguidance
    import llguidance.hf
    path = Path(__file__).resolve().parents[1] / 'models/gemma-tokenizer'
    if not path.is_dir():
        pytest.skip('Fixed local tokenizer required')
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    rec, packet, _ = setup()
    attach(rec, packet)
    grammar = llguidance.LLMatcher.grammar_from_json_schema(effective(packet),
        defaults={'whitespace_flexible': False})
    ll_tokenizer = llguidance.hf.from_tokenizer(tokenizer)
    good = obj(findings=[finding(packet)])
    bad = copy.deepcopy(good)
    bad['findings'][0]['scope_units'] = bad['findings'][0]['value_units']
    for payload, expected in [(good, True), (obj(), True), (bad, False)]:
        matcher = llguidance.LLMatcher(ll_tokenizer, grammar)
        ids = tokenizer.encode(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), add_special_tokens=False)
        accepted = matcher.consume_tokens(ids) and matcher.is_accepting()
        assert accepted == expected


def test_a_literal_property_in_one_requirement_is_not_the_entire_purchase():
    rec, packet, knowledge = setup('요구사항 ID: HWR-001\n요구사항명: 보조 연산 장치\nCPU 아키텍처: ARM')
    attach(rec, packet)
    candidate = packet['generation']['catalog_roles']['properties'][0]
    claim = finding(packet)
    claim['scope_units'] = candidate['scope_options'][0]
    row, details = sem.review(rec, response(obj(findings=[claim])), packet, knowledge)
    assert row is None and details['conditions'][0]['status'] == 'unknown'


def test_an_explicit_contract_wide_named_fact_survives_a_requirement_frame():
    rec, packet, _ = setup('요구사항 ID: HWR-001\n요구사항명: 구매 규격\n'
        '본 계약의 전체 납품 컴퓨터서버의 CPU 아키텍처: ARM')
    conditions, bad = literal.evaluate_readings(rec, packet['spans'],
        {'findings': [], 'unresolved_fields': []}, packet['catalog_conditions']['plan'])
    assert not bad and conditions[0]['status'] == 'not_met'


def test_a_long_permission_is_preserved_as_capacity_unknown_without_clipping():
    rec, packet, _ = setup(extra='동등 규격은 ' + '상세 검토를 거친다 ' * 400)
    graph = roles.build(rec, packet['spans'], packet['catalog_conditions']['plan'])
    assert any(e['reason'] == 'complete_permission_reference_capacity_exceeded' for e in graph['unavailable'])
    assert graph['generation']['permission_options'] == []
    validate_grammar(sem.generation_schema(len(packet['spans']), source_roles=graph['generation']))
