"""Known local values help reading but do not discharge scope or permission gates."""
import copy
import dataclasses
import hashlib
from pathlib import Path

import jsonschema
import pytest

from submission.pps import catalog_source_roles as roles, catalog_semantics as sem
from submission.pps.retrieval import Span
from tests.test_catalog_condition_review import setup, packet_for, finding
from tests.test_catalog_source_roles import attach, effective
from tests.test_catalog_semantics import obj, response


def observe(rec, packet):
    return roles.observed_facts(rec, packet['spans'], packet['catalog_conditions']['plan'])


def install(rec, packet):
    attach(rec, packet)
    packet['catalog_conditions']['source_observations'] = observe(rec, packet)
    return packet


def test_observed_false_and_local_must_are_not_absence_or_scope_certification():
    rec, packet, _ = setup('회전익이여야 한다.', name='드론', code='2513189901')
    original = copy.deepcopy((rec, packet))
    facts = observe(rec, packet)
    fact = facts['observations'][0]
    assert fact['type'] == 'boolean' and fact['value'] is False
    assert fact['method'] == 'explicit_airframe_requirement'
    ev = fact['source_range']
    text = rec['docs'][ev['doc_index']]['text']
    assert text[ev['start']:ev['end']] == '회전익이여야 한다.'
    local = fact['local_modality_expressions'][0]
    assert text[local['start']:local['end']] == local['text'] == '이여야 한다'
    assert text[local['property_start']:local['property_end']] == '회전익'
    assert not any(facts[k] for k in ('whole_purchase_certified', 'delivery_obligation_certified',
                                    'permissions_resolved', 'absence_certified'))
    assert (rec, packet) == original


@pytest.mark.parametrize('prefix', ['선택 규격\n', '예시 규격\n', '다음 선택 장비를 납품하는 경우:\n'])
def test_parent_conditional_scope_never_becomes_a_global_required_mask(prefix):
    rec, packet, knowledge = setup(prefix+'회전익이어야 한다.', name='드론', code='2513189901')
    install(rec, packet)
    fact = observe(rec, packet)['observations'][0]
    assert fact['value'] is False and fact['local_modality_expressions']
    for modality in ('required', 'optional', 'example', 'negated', 'unclear'):
        claim = finding(packet, '회전익이어야 한다.', field='fixed_wing', code='2513189901', name='드론')
        claim['modality'] = modality
        jsonschema.validate(obj(findings=[claim]), effective(packet))
    # A fallible model claim still cannot erase a disclosed parent condition.
    claim['modality'] = 'required'
    row, detail = sem.review(rec, response(obj(findings=[claim])), packet, knowledge)
    assert row is None and detail['conditions'][0]['status'] == 'unknown'


def test_cross_document_alternative_is_not_resolved_by_an_observed_value():
    rec, _, knowledge = setup()
    rec['docs'].append({'doc_id': 'other', 'type': '규격서', 'text': '다른 CPU 아키텍처로 대체할 수 있다.'})
    packet = install(rec, packet_for(rec, knowledge))
    packet['spans'] = [s for s in packet['spans'] if s.doc_index == 0]
    install(rec, packet)
    assert observe(rec, packet)['observations'][0]['value'] == 'arm'
    assert not observe(rec, packet)['permissions_resolved']
    row, detail = sem.review(rec, response(obj(findings=[finding(packet)])), packet, knowledge)
    assert row is None and detail['conditions'][0]['scope_issues']


@pytest.mark.parametrize('text', ['CPU 아키텍처: 미정', 'CPU 아키텍처: ARM 또는 x86',
                                 'CPU 기본주파수: 3.2GHz 또는 3.6GHz'])
def test_unparsed_values_are_visible_without_inventing_a_typed_value(text):
    rec, packet, _ = setup(text)
    facts = observe(rec, packet)
    assert not facts['observations'] and len(facts['unresolved_values']) == 1
    assert 'value' not in facts['unresolved_values'][0]


def test_ambiguous_airframe_is_not_shown_as_a_known_false():
    rec, packet, _ = setup('고정익 또는 회전익이어야 한다.', name='드론', code='2513189901')
    facts = observe(rec, packet)
    assert not facts['observations']
    assert facts['unresolved_values'][0]['issue'] == 'airframe_definition_or_modality_unresolved'


def test_conflicting_and_duplicate_occurrences_keep_their_original_addresses():
    rec, packet, _ = setup('CPU 아키텍처: ARM\nCPU 아키텍처: x86\nCPU 아키텍처: ARM')
    facts = observe(rec, packet)['observations']
    assert [f['value'] for f in facts] == ['arm', 'x86', 'arm']
    assert len({f['source_range']['start'] for f in facts}) == 3


def test_numeric_unit_and_open_boundary_are_kept_from_existing_parser():
    rec, packet, _ = setup('CPU 기본주파수: 3200MHz 미만')
    fact = observe(rec, packet)['observations'][0]
    assert fact['field'] == 'cpu_base_ghz'
    assert fact['value'] == {'lower': '0', 'upper': '3.200', 'lower_closed': True, 'upper_closed': False}


@pytest.mark.parametrize('hole', ['value', 'label', 'middle'])
def test_missing_original_characters_cannot_be_filled_from_full_record(hole):
    rec, packet, _ = setup()
    text = rec['docs'][0]['text']
    start = text.index('CPU 아키텍처: ARM')
    end = start + len('CPU 아키텍처: ARM')
    pieces = [(start, end-3)] if hole == 'value' else [(end-3, end)] if hole == 'label' else [
        (start, end-3), (end-2, end)]
    packet['spans'] = [Span(0, '공고문', lo, hi, text[lo:hi]) for lo, hi in pieces]
    facts = observe(rec, packet)
    assert not facts['observations'] and not facts['unresolved_values']
    assert not facts['absence_certified']


def test_malformed_even_unselected_source_unit_is_rejected():
    rec, packet, _ = setup()
    packet['spans'][0] = dataclasses.replace(packet['spans'][0], start=True)
    with pytest.raises(ValueError):
        observe(rec, packet)


@pytest.mark.parametrize('mutation', ['boolean_as_number', 'float_coordinate', 'value', 'null'])
def test_prepared_facts_are_checked_before_consuming_any_answer(mutation):
    rec, packet, knowledge = setup('회전익이어야 한다.', name='드론', code='2513189901')
    install(rec, packet)
    facts = packet['catalog_conditions']['source_observations']
    if mutation == 'null':
        packet['catalog_conditions']['source_observations'] = None
    elif mutation == 'float_coordinate':
        ev = facts['observations'][0]['source_range']
        ev['start'] = float(ev['start'])
    else:
        facts['observations'][0]['value'] = 0 if mutation == 'boolean_as_number' else True
    with pytest.raises(ValueError):
        sem.review(rec, response(obj()), packet, knowledge)


def test_legacy_packet_does_not_require_or_use_observations():
    rec, packet, knowledge = setup()
    attach(rec, packet)
    payload = response(obj(findings=[finding(packet)]))
    baseline = sem.review(rec, payload, packet, knowledge)
    install(rec, packet)
    assert sem.review(rec, payload, packet, knowledge) == baseline


def test_prompt_facts_change_no_selected_source_role_schema_or_consumer():
    from transformers import AutoTokenizer
    tokenizer_dir = Path(__file__).resolve().parents[1] / 'models/gemma-tokenizer'
    if not tokenizer_dir.is_dir():
        pytest.skip('Fixed local tokenizer required')
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_dir, local_files_only=True, trust_remote_code=False)
    rec, _, knowledge = setup()
    selection = {'record_id': rec['id'], 'source_token_budget': 4096, 'coverage': {},
        'source_tokens': sum(len(tokenizer.encode(d['text'], add_special_tokens=False)) for d in rec['docs']),
        'documents': [{'doc_index': i, 'doc_id': d['doc_id'],
            'doc_sha256': hashlib.sha256(d['text'].encode()).hexdigest()} for i, d in enumerate(rec['docs'])],
        'spans': [{'doc_index': i, 'doc_type': d['type'], 'start': 0, 'end': len(d['text']), 'text': d['text']}
                 for i, d in enumerate(rec['docs'])]}
    control = sem.prompt(rec, selection, tokenizer, knowledge, source_roles=True)
    candidate = sem.prompt(rec, selection, tokenizer, knowledge, source_roles=True, source_observations=True)
    assert candidate['spans'] == control['spans'] and candidate['generation'] == control['generation']
    assert effective(candidate) == effective(control)
    assert candidate['token_ids'] != control['token_ids']
    assert candidate['catalog_conditions']['source_observations']['observations'][0]['value'] == 'arm'
    assert '"source_observations"' in candidate['messages'][1]['content']
    roles.validate_observed_facts(rec, candidate)
    with pytest.raises(ValueError, match='require source roles'):
        sem.prompt(rec, selection, tokenizer, knowledge, source_observations=True)
