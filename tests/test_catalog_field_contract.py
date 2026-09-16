"""A per-notice schema must reach the sampler and retain its source contract."""
import copy
import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import jsonschema
import pytest

from submission.b4_entry import parse_error
from submission.pps import catalog_field_contract as fields
from submission.pps import catalog_semantics as sem
from submission.pps.catalog_condition_review import condition_plan
from submission.pps.generation_contract import generation_schema, validate_grammar
from tests.test_catalog_condition_review import setup, finding
from tests.test_catalog_semantics import obj, response


def plan():
    return condition_plan({'products': [
        {'code': '4321150102', 'name': '컴퓨터서버', 'listed': True,
         'note': 'x86 서버 CPU 1개 전체, CPU 2개 중 Clock(기본주파수) 3.2GHz 이하 제품에 한함',
         'condition': {'status': 'unknown'}},
        {'code': '2513189901', 'name': '드론', 'listed': True,
         'note': '고정익, 군사용, 수소드론 제외', 'condition': {'status': 'unknown'}}]})


def effective(contract, form=sem.FORMAT):
    return generation_schema(form, 8, tuple(range(10, 19)), catalog_fields=contract)


def claim(code='4321150102', field='cpu_count'):
    return {'code': code, 'field': field, 'value_units': [1], 'scope_units': [2],
        'condition_units': [], 'scope': 'whole_named_purchase', 'modality': 'required', 'reason': '명시한 원문 속성'}


@pytest.mark.parametrize('code,field', [('4321150102', 'military_use'), ('2513189901', 'cpu_count'),
    ('1234567890', 'cpu_count'), ('4321150102', 'building_storeys')])
def test_registered_fields_do_not_form_an_unrestricted_cross_product(code, field):
    schema = effective(fields.build(plan()))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(obj(findings=[claim(code, field)]), schema)
    jsonschema.validate(obj(findings=[claim()]), schema)
    jsonschema.validate(obj(unresolved_fields=[{'code': '2513189901', 'field': 'military_use', 'reason': '미확정'}]), schema)


def test_mask_is_per_code_and_preserves_semantic_channel_types():
    schema = effective(fields.build(plan()))
    props = schema['properties']['semantic_readings']['items']['properties']
    assert props['code']['const'] == '2513189901'
    assert set(props['field']['enum']) == {'fixed_wing', 'military_use', 'hydrogen_drone'}
    one = fields.build(plan()[1:])  # plan is sorted by code, index1 is server.
    other = effective(one)
    assert other['properties']['semantic_readings']['maxItems'] == 0
    jsonschema.validate(obj(), other)
    validate_grammar(other)


@pytest.mark.parametrize('change', ['missing_generation', 'missing_display', 'wrong_hash', 'different_field', 'duplicate'])
def test_incomplete_or_changed_prepared_contract_does_not_fall_back_to_generic(change):
    p = plan(); contract = fields.build(p)
    packet = {'generation': {'catalog_fields': copy.deepcopy(contract)},
        'catalog_conditions': {'plan': p, 'field_contract': copy.deepcopy(contract)}}
    if change == 'missing_generation':
        packet['generation'].clear()
    elif change == 'missing_display':
        del packet['catalog_conditions']['field_contract']
    elif change == 'wrong_hash':
        packet['generation']['catalog_fields']['plan_sha256'] = '0'*64
    elif change == 'different_field':
        packet['catalog_conditions']['plan'][0]['fields'][0]['field'] = 'cpu_count'
    else:
        packet['generation']['catalog_fields']['fields'].append(contract['fields'][0])
    with pytest.raises(ValueError):
        fields.validate_prepared(packet)


def test_legacy_packet_without_this_contract_is_still_readable():
    rec, packet, knowledge = setup()
    packet['generation']['response_format'] = sem.FORMAT
    packet['catalog_conditions']['interpretation_mode'] = sem.FORMAT
    assert fields.validate_prepared(packet) is None
    row, detail = sem.review(rec, response(obj(findings=[finding(packet)])), packet, knowledge)
    assert row['v10'] == 0 and detail['product']['status'] == 'general'


def test_well_formed_bad_field_remains_a_semantic_error_not_a_quality_reroll():
    rec, packet, knowledge = setup()
    contract = fields.build(packet['catalog_conditions']['plan'])
    packet['generation'] = {'response_format': sem.FORMAT, 'catalog_fields': contract}
    packet['catalog_conditions'].update(interpretation_mode=sem.FORMAT, field_contract=contract)
    raw = response(obj(findings=[finding(packet, field='military_use')]))
    assert parse_error(packet, raw) is None
    row, detail = sem.review(rec, raw, packet, knowledge)
    assert row is None and detail['gate'] == 'model_field_selection_outside_prepared_plan'


def test_source_role_contract_cannot_disagree_with_the_field_plan():
    from tests.test_catalog_source_roles import attach
    rec, packet, _ = setup()
    contract = fields.build(packet['catalog_conditions']['plan'])
    attach(rec, packet)
    schema = generation_schema(sem.FORMAT, len(packet['spans']), packet['items'],
        catalog_fields=contract, catalog_roles=packet['generation']['catalog_roles'])
    assert schema['properties']['findings']['prefixItems']
    contract['fields'].append(['4321150102', 'military_use'])
    contract['fields'].sort()
    with pytest.raises(ValueError, match='disagree'):
        generation_schema(sem.FORMAT, len(packet['spans']), packet['items'],
            catalog_fields=contract, catalog_roles=packet['generation']['catalog_roles'])


def test_actual_sampler_receives_the_same_per_notice_schema(monkeypatch):
    from submission.pps.pipeline import VLLMRunner
    class Params:
        def __init__(self, **kw):
            self.__dict__.update(kw)
    monkeypatch.setitem(sys.modules, 'vllm', SimpleNamespace(SamplingParams=Params))
    monkeypatch.setitem(sys.modules, 'vllm.sampling_params', SimpleNamespace(StructuredOutputsParams=Params))
    contract = fields.build(plan())
    packet = {'spans': [None]*8, 'items': list(range(10, 19)), 'token_ids': [5],
        'generation': {'response_format': sem.FORMAT, 'catalog_fields': contract}}
    class Model:
        def generate(self, prompts, sampling_params, **kw):
            assert sampling_params[0].structured_outputs.json == effective(contract)
            return [SimpleNamespace(outputs=[SimpleNamespace(text='{}', finish_reason='stop', token_ids=[])])]
    runner = VLLMRunner.__new__(VLLMRunner)
    runner.config = SimpleNamespace(seed=0, max_output_tokens=128, enable_thinking=False,
        response_format='factored', thinking_budget_for=lambda items: 0)
    runner.llm = Model()
    runner.generate([packet])


@pytest.fixture(scope='module')
def tokenizer():
    path = Path(__file__).resolve().parents[1]/'models/gemma-tokenizer'
    if not path.is_dir():
        pytest.skip('Fixed local tokenizer required')
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)


def test_actual_token_mask_stops_a_code_field_mismatch(tokenizer):
    import llguidance
    import llguidance.hf
    schema = effective(fields.build(plan()))
    validate_grammar(schema)
    grammar = llguidance.LLMatcher.grammar_from_json_schema(schema, defaults={'whitespace_flexible': False})
    tok = llguidance.hf.from_tokenizer(tokenizer)
    for payload, valid in [(obj(findings=[claim()]), True),
                           (obj(findings=[claim(field='military_use')]), False), (obj(), True)]:
        matcher = llguidance.LLMatcher(tok, grammar)
        ids = tokenizer.encode(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), add_special_tokens=False)
        assert bool(matcher.consume_tokens(ids)) is valid
        if valid:
            assert matcher.is_accepting()


@pytest.mark.parametrize('role_mode', [False, True])
def test_prompt_only_names_the_current_fields_and_keeps_the_original_source(tokenizer, role_mode):
    rec, _, knowledge = setup('CPU 아키텍처: ARM\nCPU 개수: 1개')
    body = sem.prompt(rec, selection_for(rec, tokenizer), tokenizer, knowledge, source_roles=role_mode)
    assert fields.validate_prepared(body) == fields.build(body['catalog_conditions']['plan'])
    shown = json.loads(body['messages'][1]['content'].split('[출력 JSON Schema]\n')[1])
    encoded = json.dumps(shown)
    assert 'building_storeys' not in encoded and 'military_use' not in encoded
    assert 'cpu_architecture' in encoded and '4321150102' in encoded
    for span in body['spans']:
        assert rec['docs'][span.doc_index]['text'][span.start:span.end] == span.text


def selection_for(rec, tokenizer):
    selection = {'record_id': rec['id'], 'source_token_budget': 4096,
        'spans': [{'doc_index': i, 'start': 0, 'end': len(d['text']), 'text': d['text'],
                   'doc_type': d['type']} for i, d in enumerate(rec['docs'])],
        'source_tokens': sum(len(tokenizer.encode(d['text'], add_special_tokens=False)) for d in rec['docs']),
        'documents': [{'doc_index': i, 'doc_id': d['doc_id'],
                       'doc_sha256': hashlib.sha256(d['text'].encode()).hexdigest()}
                      for i, d in enumerate(rec['docs'])],
        'coverage': {}}
    return selection


@pytest.mark.parametrize('failure', ['missing_digest', 'unsupported_grammar'])
def test_effective_packet_grammar_fails_before_engine_loading(tmp_path, monkeypatch, tokenizer, failure):
    from submission import engine
    from submission.b4_entry import digest
    from submission.pps import generation_contract
    from submission.runtime import source_manifest
    from tools.prepare_retrieval_contrast import write_rows
    from tools.run_retrieval_contrast import run
    rec, _, knowledge = setup()
    body = sem.prompt(rec, selection_for(rec, tokenizer), tokenizer, knowledge)
    packet = {**body, 'record_id': rec['id'],
        'spans': [dataclasses.asdict(s) for s in body['spans']],
        'prompt_sha256': digest(body['messages']), 'token_ids_sha256': digest(body['token_ids'])}
    expected = 'effective generation schema digest'
    if failure == 'unsupported_grammar':
        schema = generation_schema(sem.FORMAT, len(body['spans']), body['items'],
            catalog_fields=body['generation']['catalog_fields'])
        schema['properties']['findings']['uniqueItems'] = True
        packet['generation_schema_sha256'] = digest(schema)
        monkeypatch.setattr(generation_contract, 'generation_schema', lambda *a, **k: schema)
        expected = 'uniqueItems'
    folder = tmp_path/'input'
    folder.mkdir()
    write_rows(folder/'contrast_packets.jsonl.gz', [packet])
    write_rows(folder/'contrast_inputs.jsonl.gz', [rec])
    sha = lambda name: hashlib.sha256((folder/name).read_bytes()).hexdigest()
    (folder/'contrast_freeze.json').write_text(json.dumps({'source_sha256': source_manifest(),
        'packets_sha256': sha('contrast_packets.jsonl.gz'),
        'inputs_sha256': sha('contrast_inputs.jsonl.gz')}), encoding='utf8')
    monkeypatch.setattr(engine, 'configure_environment', lambda: None)
    def unexpected(*a, **k):
        pytest.fail('Engine constructed before effective grammar validation')
    monkeypatch.setattr(engine, 'CanonicalRunner', unexpected)
    with pytest.raises(ValueError, match=expected):
        run(folder, 'unused', Path(__file__).resolve().parents[1]/'models/gemma-tokenizer', tmp_path/'output')
    assert not (tmp_path/'output').exists()
