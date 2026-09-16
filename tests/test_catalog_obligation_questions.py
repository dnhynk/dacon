"""The obligation operand is a source value, never a forced yes/no answer."""
import copy
import hashlib
from pathlib import Path

import jsonschema
import pytest

from submission.pps import catalog_source_roles as roles, catalog_semantics as sem
from tests.test_catalog_condition_review import setup, finding
from tests.test_catalog_observed_facts import install, observe
from tests.test_catalog_source_roles import effective
from tests.test_catalog_semantics import obj, response


def add_questions(rec, packet):
    install(rec, packet)
    questions = roles.obligation_questions(packet['catalog_conditions']['source_observations'])
    packet['catalog_conditions']['obligation_questions'] = questions
    return questions


def test_false_value_is_the_operand_and_no_obligation_answer_is_provided():
    rec, packet, _ = setup('회전익이어야 한다.', name='드론', code='2513189901')
    before = copy.deepcopy((rec, packet))
    questions = roles.obligation_questions(observe(rec, packet))
    question = questions['questions'][0]
    assert question['value_constraint'] == {'type': 'boolean', 'value': False}
    assert question['modality_operand'] == 'value_constraint' and not question['answer_supplied']
    assert set(questions['read_modality_as']) == {'required','optional','example','negated','unclear'}
    assert questions['no_scope_or_permission_decision_supplied']
    assert (rec, packet) == before


def test_numeric_open_bound_is_kept_as_a_range_not_rounded_to_a_favorable_value():
    rec, packet, _ = setup('CPU 기본주파수: 3200MHz 미만')
    observed = observe(rec, packet)
    questions = roles.obligation_questions(observed)
    assert questions['questions'][0]['value_constraint']['value'] == {
        'lower': '0', 'upper': '3.200', 'lower_closed': True, 'upper_closed': False}
    questions['questions'][0]['value_constraint']['value']['upper_closed'] = True
    assert not observed['observations'][0]['value']['upper_closed']


@pytest.mark.parametrize('source', ['CPU 아키텍처: 미정', 'CPU 아키텍처: ARM 또는 x86'])
def test_no_question_invents_a_value_for_an_unparsed_source(source):
    rec, packet, _ = setup(source)
    observed = observe(rec, packet)
    assert observed['unresolved_values']
    assert roles.obligation_questions(observed)['questions'] == []


@pytest.mark.parametrize('mutation', ['value', 'boolean_as_number', 'float_coordinate', 'source_unit', 'omit', 'version', 'null'])
def test_changed_prepared_questions_are_rejected_before_consumption(mutation):
    rec, packet, knowledge = setup('회전익이어야 한다.', name='드론', code='2513189901')
    questions = add_questions(rec, packet)
    if mutation == 'value':
        questions['questions'][0]['value_constraint']['value'] = True
    elif mutation == 'boolean_as_number':
        questions['questions'][0]['value_constraint']['value'] = 0
    elif mutation == 'float_coordinate':
        ev = questions['questions'][0]['source_range']
        ev['start'] = float(ev['start'])
    elif mutation == 'source_unit':
        questions['questions'][0]['source_units'] = []
    elif mutation == 'omit':
        questions['questions'] = []
    elif mutation == 'version':
        questions['version'] = True
    else:
        packet['catalog_conditions']['obligation_questions'] = None
    with pytest.raises(ValueError, match='obligation questions'):
        sem.review(rec, response(obj()), packet, knowledge)


def test_questions_without_observed_source_contract_are_rejected():
    rec, packet, knowledge = setup()
    add_questions(rec, packet)
    del packet['catalog_conditions']['source_observations']
    with pytest.raises(ValueError, match='require source observations'):
        sem.review(rec, response(obj()), packet, knowledge)


def test_optional_parent_keeps_every_model_modality_choice_available():
    rec, packet, knowledge = setup('선택 규격\n회전익이어야 한다.', name='드론', code='2513189901')
    add_questions(rec, packet)
    for modality in ('required','optional','example','negated','unclear'):
        claim = finding(packet, '회전익이어야 한다.', field='fixed_wing', code='2513189901', name='드론')
        claim['modality'] = modality
        jsonschema.validate(obj(findings=[claim]), effective(packet))
    claim['modality'] = 'required'
    row, details = sem.review(rec, response(obj(findings=[claim])), packet, knowledge)
    assert row is None and details['conditions'][0]['status'] == 'unknown'


def test_additional_questions_cannot_repair_a_saved_negated_model_claim():
    rec, packet, knowledge = setup('회전익이어야 한다.', name='드론', code='2513189901')
    install(rec, packet)
    claim = finding(packet, '회전익이어야 한다.', field='fixed_wing', code='2513189901', name='드론')
    claim['modality'] = 'negated'
    prior = sem.review(rec, response(obj(findings=[claim])), packet, knowledge)
    add_questions(rec, packet)
    after = sem.review(rec, response(obj(findings=[claim])), packet, knowledge)
    assert after == prior and after[0] is None


def test_question_occurrences_keep_distinct_original_addresses_and_order():
    rec, packet, _ = setup('CPU 아키텍처: ARM\nCPU 아키텍처: x86\nCPU 아키텍처: ARM')
    questions = add_questions(rec, packet)['questions']
    assert [q['key'] for q in questions] == ['O1','O2','O3']
    assert [q['value_constraint']['value'] for q in questions] == ['arm','x86','arm']
    assert len({q['source_range']['start'] for q in questions}) == 3


def test_prompt_addition_preserves_source_values_schema_and_consumer_contract():
    from transformers import AutoTokenizer
    folder = Path(__file__).resolve().parents[1]/'models/gemma-tokenizer'
    if not folder.is_dir():
        pytest.skip('Fixed local tokenizer required')
    tokenizer = AutoTokenizer.from_pretrained(folder, local_files_only=True, trust_remote_code=False)
    rec, _, knowledge = setup()
    selection = {'record_id': rec['id'], 'source_token_budget': 4096, 'coverage': {},
        'source_tokens': sum(len(tokenizer.encode(d['text'], add_special_tokens=False)) for d in rec['docs']),
        'documents': [{'doc_index': i, 'doc_id': d['doc_id'], 'doc_sha256': hashlib.sha256(d['text'].encode()).hexdigest()}
            for i, d in enumerate(rec['docs'])],
        'spans': [{'doc_index': i, 'doc_type': d['type'], 'start': 0, 'end': len(d['text']), 'text': d['text']}
            for i, d in enumerate(rec['docs'])]}
    control = sem.prompt(rec, selection, tokenizer, knowledge, source_roles=True, source_observations=True)
    candidate = sem.prompt(rec, selection, tokenizer, knowledge,
        source_roles=True, source_observations=True, source_obligations=True)
    assert candidate['spans'] == control['spans'] and candidate['generation'] == control['generation']
    assert candidate['catalog_conditions']['source_observations'] == control['catalog_conditions']['source_observations']
    assert effective(candidate) == effective(control) and candidate['token_ids'] != control['token_ids']
    roles.validate_prepared(rec, candidate)
    with pytest.raises(ValueError, match='require observed source facts'):
        sem.prompt(rec, selection, tokenizer, knowledge, source_roles=True, source_obligations=True)
