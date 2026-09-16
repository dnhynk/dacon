"""Source binding failures must not turn valid model JSON into false certainty."""
import copy
import dataclasses
import json

import pytest

from submission.b4_entry import B4Pipeline, parse_error
from submission.pps.catalog_condition_review import ITEMS, condition_plan, decode, prompt, review, schema
from submission.pps.generation_contract import generation_schema, validate_grammar
from submission.pps.knowledge import Knowledge
from submission.pps.retrieval import Span
from submission.pps.source_units import unitize
from tests.test_catalog_predicate_consumption import purchase
from tests.test_service_identity import DATA


def setup(properties='CPU 아키텍처: ARM', *, name='컴퓨터서버', code='4321150102', extra=''):
    rec = purchase(name, code, [])
    rec['docs'][0]['text'] = f'1. 구매내역\n품명: {name}\n{properties}\n{extra}\n2. 입찰참가자격\n일반 업체\n3. 계약조건'
    knowledge = Knowledge(DATA)
    packet = packet_for(rec, knowledge)
    return rec, packet, knowledge


def packet_for(rec, knowledge):
    _, source = knowledge.qualification_decisions(rec, {})
    spans = unitize([Span(i, d['type'], 0, len(d['text']), d['text']) for i, d in enumerate(rec['docs'])])
    return {'spans': spans, 'items': list(ITEMS), 'family': 'C',
        'catalog_conditions': {'plan': condition_plan(source['product'])},
        'generation': {'response_format': 'catalog_conditions'}}


def units(packet, text):
    return [i for i, span in enumerate(packet['spans'], 1) if text in span.text]


def finding(packet, text='CPU 아키텍처:', *, field='cpu_architecture', code='4321150102', name='컴퓨터서버'):
    return {'code': code, 'field': field, 'value_units': units(packet, text),
        'scope_units': units(packet, '품명: '+name), 'condition_units': [],
        'scope': 'whole_named_purchase', 'modality': 'required', 'reason': '현재 구매 품목의 필수 규격'}


def response(findings=(), unresolved=()):
    return {'text': json.dumps({'findings': list(findings), 'unresolved_fields': list(unresolved)}, ensure_ascii=False),
            'finish_reason': 'stop'}


def test_original_values_are_consumed_after_local_named_scope_selection():
    rec, packet, knowledge = setup()
    before = copy.deepcopy((rec, packet))
    result, log = review(rec, response([finding(packet)]), packet, knowledge)
    assert result['v10'] == 0 and log['product']['status'] == 'general'
    assert log['conditions'][0]['observations'][0]['value'] == 'arm'
    assert log['conditions'][0]['model_scope_is_fallible']
    assert (rec, packet) == before
    assert set(result) < {f'{prefix}{k}' for prefix in ('v', 'e') for k in ITEMS}


def test_whole_nonmandatory_or_ambiguous_relationships_stay_unknown():
    rec, packet, knowledge = setup()
    for field, choices in [('scope', ['component', 'other', 'unclear']),
                           ('modality', ['optional', 'example', 'negated', 'unclear'])]:
        for value in choices:
            claim = finding(packet)
            claim[field] = value
            raw = response([claim])
            assert parse_error(packet, raw) is None
            result, log = review(rec, raw, packet, knowledge)
            assert result is None and log['conditions'][0]['status'] == 'unknown'


@pytest.mark.parametrize('bad_value', ['3.2GHz 이상', '3 200MHz', '3.2TiB', '미정', '3.2GHz 또는 3.6GHz'])
def test_model_cannot_create_a_definite_numeric_value_from_an_ambiguous_source(bad_value):
    rec, packet, knowledge = setup('CPU 아키텍처: x86\nCPU 개수: 2개\nCPU 기본주파수: '+bad_value)
    claims = [finding(packet), finding(packet, 'CPU 개수:', field='cpu_count'),
              finding(packet, 'CPU 기본주파수:', field='cpu_base_ghz')]
    result, log = review(rec, response(claims), packet, knowledge)
    assert result is None and log['conditions'][0]['status'] == 'unknown'


def test_unit_conversion_and_short_circuit_use_source_fields_only():
    rec, packet, knowledge = setup('CPU 아키텍처: x86\nCPU 개수: 2개\nCPU 기본주파수: 3200MHz')
    claims = [finding(packet), finding(packet, 'CPU 개수:', field='cpu_count'),
              finding(packet, 'CPU 기본주파수:', field='cpu_base_ghz')]
    result, log = review(rec, response(claims), packet, knowledge)
    assert log['product']['status'] == 'competition' and result['v10'] == 1
    assert log['conditions'][0]['observations'][2]['value']['upper'] == '3.200'
    rec, packet, knowledge = setup('CPU 아키텍처: x86\nCPU 개수: 1개')
    result, log = review(rec, response([finding(packet), finding(packet, 'CPU 개수:', field='cpu_count')]), packet, knowledge)
    assert log['product']['status'] == 'competition'
    assert log['conditions'][0]['missing_fields'] == ['cpu_base_ghz']


@pytest.mark.parametrize('extra', ['동등한 타 아키텍처 제품도 납품 가능하다.',
    '다른 CPU를 선택할 수 있다.', '참고 규격을 적용한다.'])
def test_unselected_original_permission_cannot_be_silently_resolved(extra):
    rec, packet, knowledge = setup(extra=extra)
    result, log = review(rec, response([finding(packet)]), packet, knowledge)
    assert result is None and log['conditions'][0]['scope_issues']
    assert log['conditions'][0]['status'] == 'unknown'


def test_permission_in_an_unselected_other_document_is_preserved():
    rec, _, knowledge = setup()
    rec['docs'].append({'doc_id': 'elsewhere', 'type': '규격서', 'text': '동등 규격으로 대체 가능'})
    packet = packet_for(rec, knowledge)
    packet['spans'] = [s for s in packet['spans'] if s.doc_index == 0]
    result, log = review(rec, response([finding(packet)]), packet, knowledge)
    assert result is None and log['conditions'][0]['scope_issues'][0]['doc_index'] == 1


def test_omitting_one_conflicting_source_observation_does_not_remove_it():
    rec, packet, knowledge = setup('CPU 아키텍처: ARM\nCPU 아키텍처: x86')
    claim = finding(packet, 'CPU 아키텍처: ARM')
    result, log = review(rec, response([claim]), packet, knowledge)
    assert result is None
    assert [f['value'] for f in log['conditions'][0]['observations']] == ['arm', 'x86']
    assert len(log['conditions'][0]['unbound_original_properties']) == 1


def test_conflicting_model_scope_claims_are_not_majority_voted():
    rec, packet, knowledge = setup()
    good = finding(packet)
    bad = {**good, 'scope': 'component'}
    result, log = review(rec, response([good, good, bad]), packet, knowledge)
    assert result is None and log['conditions'][0]['status'] == 'unknown'


def test_missing_reading_and_explicit_unknown_do_not_default_to_a_negative_fact():
    rec, packet, knowledge = setup()
    for raw in (response(), response([finding(packet)], [{'code': '4321150102', 'field': 'cpu_architecture', 'reason': '범위 불명확'}])):
        assert parse_error(packet, raw) is None
        row, log = review(rec, raw, packet, knowledge)
        assert row is None and log['conditions'][0]['status'] == 'unknown'


def test_model_cannot_rename_takeoff_weight_to_self_weight():
    rec, packet, knowledge = setup('이륙무게: 1kg', name='드론', code='2513189901')
    claim = finding(packet, '이륙무게:', field='self_weight_kg', code='2513189901', name='드론')
    result, log = review(rec, response([claim]), packet, knowledge)
    assert result is None
    assert log['conditions'][0]['observations'] == []
    assert log['conditions'][0]['unmatched_model_readings'] == [claim]


def test_property_must_include_its_complete_original_field_and_unit():
    rec, packet, knowledge = setup()
    claim = finding(packet)
    n = claim['value_units'][0]
    old = packet['spans'][n-1]
    offset = old.text.index('ARM')
    packet['spans'][n-1] = dataclasses.replace(old, start=old.start+offset, text=old.text[offset:])
    result, log = review(rec, response([claim]), packet, knowledge)
    assert result is None and log['conditions'][0]['unbound_original_properties']


def test_task_anchor_must_be_local_and_cannot_jump_over_another_purchase():
    rec, _, knowledge = setup()
    rec['docs'][0]['text'] = rec['docs'][0]['text'].replace('CPU 아키텍처:', '품명: 태블릿컴퓨터\nCPU 아키텍처:')
    packet = packet_for(rec, knowledge)
    result, log = review(rec, response([finding(packet)]), packet, knowledge)
    assert result is None
    assert not any(l['accepted_scope_link'] for l in log['conditions'][0]['links'])
    rec, _, knowledge = setup('')
    rec['docs'].append({'type': '규격서', 'doc_id': 'other', 'text': 'CPU 아키텍처: ARM'})
    packet = packet_for(rec, knowledge)
    result, log = review(rec, response([finding(packet)]), packet, knowledge)
    assert result is None and not any(l['accepted_scope_link'] for l in log['conditions'][0]['links'])


def test_source_task_name_need_not_literally_repeat_the_catalog_label():
    rec, _, knowledge = setup()
    rec['docs'][0]['text'] = rec['docs'][0]['text'].replace('품명: 컴퓨터서버', '품명: 고성능 GPU 컴퓨팅 미니 박스')
    packet = packet_for(rec, knowledge)
    claim = finding(packet)
    claim['scope_units'] = units(packet, '품명: 고성능')
    row, log = review(rec, response([claim]), packet, knowledge)
    assert row['v10'] == 0 and log['source_scope_promoted']
    assert log['model_scope_is_fallible']  # This is not an independent identity proof.


def test_bilingual_vertical_name_field_preserves_both_original_names():
    from submission.pps.catalog_condition_review import _subject_candidates
    rec, _, knowledge = setup()
    text = ('1. 품 명\n\n영 문\nHigh-Performance GPU Mini Box\n국 문\n고성능 GPU 컴퓨팅 미니 박스\n'
            '2. 규격(성능 및 사양)\nProcessor: 20-core Arm (10 Cortex-X925 + 10 Cortex-A725)')
    rec['docs'].append({'type': '규격서', 'doc_id': 'specification', 'text': text})
    rec['docs'][0]['text'] = rec['docs'][0]['text'].replace('CPU 아키텍처: ARM', '')
    packet = packet_for(rec, knowledge)
    candidate = _subject_candidates(rec)[0]
    assert candidate['text'] == text[:text.index('\n2.')]
    claim = finding(packet, 'Processor:')
    claim['scope_units'] = [i for i, s in enumerate(packet['spans'], 1)
                           if s.doc_index == 1 and s.start < candidate['end']]
    result, log = review(rec, response([claim]), packet, knowledge)
    assert result['v10'] == 0 and log['conditions'][0]['status'] == 'not_met'
    claim['scope_units'] = units(packet, '영 문')
    assert review(rec, response([claim]), packet, knowledge)[0] is None


def test_literal_item_row_can_bound_a_property_without_certifying_all_purchases():
    from submission.pps.catalog_condition_review import property_readings
    rec, _, knowledge = setup('', name='드론', code='2513189901')
    rec['docs'].append({'type': '규격서', 'doc_id': 'spec', 'text':
        '4 | 드론 | 식 | 1\nⅠ. 특징 및 사양\n1. 저고도 매핑 솔루션에 최적화 된 회전익이여야 한다.'})
    packet = packet_for(rec, knowledge)
    claim = finding(packet, '회전익', field='fixed_wing', code='2513189901', name='드론')
    claim['scope_units'] = units(packet, '4 | 드론')
    row, log = review(rec, response([claim]), packet, knowledge)
    assert row is None  # Weight, altitude and other exclusions are still unknown.
    observed = log['conditions'][0]['observations'][0]
    assert observed['field'] == 'fixed_wing' and observed['value'] is False
    assert observed['scope'] == 'entire_named_purchase'
    assert 'source_assertion' in property_readings(rec, '드론', ['fixed_wing'])['observations'][0]


@pytest.mark.parametrize('statement', ['고정익 또는 회전익이어야 한다.', '기존 기체는 회전익이어야 한다.',
    'VTOL 복합 회전익이어야 한다.', '예시로 회전익이어야 한다.'])
def test_airframe_wording_with_other_layouts_or_modalities_is_not_a_negative_fact(statement):
    from submission.pps.catalog_condition_review import property_readings
    rec, packet, knowledge = setup(statement, name='드론', code='2513189901')
    facts = property_readings(rec, '드론', ['fixed_wing'])
    assert facts['observations'][0]['issue']


def test_numeric_requirement_suffix_is_removed_only_in_the_value_interpretation():
    rec, packet, knowledge = setup('CPU 아키텍처: x86\nCPU 개수: 2개일 것\nCPU 기본주파수: 3200MHz이어야 한다.')
    claims = [finding(packet), finding(packet, 'CPU 개수:', field='cpu_count'),
              finding(packet, 'CPU 기본주파수:', field='cpu_base_ghz')]
    _, log = review(rec, response(claims), packet, knowledge)
    assert log['product']['status'] == 'competition'
    obs = log['conditions'][0]['observations'][-1]
    assert obs['literal_requirement_suffix'] and obs['evidence']['text'].endswith('이어야 한다.')


def test_explicit_multiple_purchase_identity_is_not_certified_by_one_property():
    rec, packet, knowledge = setup(name='컴퓨터서버 외 3종')
    # The static catalog name remains 컴퓨터서버, but the declared list is incomplete.
    result, log = review(rec, response([finding(packet)]), packet, knowledge)
    assert result is None and log['gate'] == 'purchase_identity_or_mixed_scope_unresolved'


@pytest.mark.parametrize('change', [{'code': '9999999999'}, {'field': 'self_weight_kg'}])
def test_undeclared_fields_are_semantic_abstentions_without_retry(change):
    rec, packet, knowledge = setup()
    claim = {**finding(packet), **change}
    raw = response([claim])
    assert parse_error(packet, raw) is None
    row, log = review(rec, raw, packet, knowledge)
    assert row is None and log['gate'] == 'model_used_undeclared_condition_field'


def test_catalog_or_original_source_tampering_is_not_accepted():
    rec, packet, knowledge = setup()
    raw = response([finding(packet)])
    packet['catalog_conditions']['plan'][0]['note'] = '무조건 포함'
    assert review(rec, raw, packet, knowledge)[1]['gate'] == 'supplied_condition_plan_missing_or_changed'
    packet = packet_for(rec, knowledge)
    packet['spans'][0] = dataclasses.replace(packet['spans'][0], text='위조 원문')
    with pytest.raises(ValueError, match='original source'):
        review(rec, raw, packet, knowledge)


@pytest.mark.parametrize('number', [0, 100000, 1.0, True])
def test_bad_references_fail_before_indexing(number):
    rec, packet, knowledge = setup()
    claim = finding(packet)
    claim['value_units'] = [number]
    assert parse_error(packet, response([claim])) is not None


def test_duplicate_reference_sets_are_idempotent_without_changing_semantics():
    rec, packet, knowledge = setup()
    claim = finding(packet)
    claim['value_units'] *= 2
    raw = response([claim])
    decoded, audit = decode(raw['text'], packet['spans'])
    assert len(decoded['findings'][0]['value_units']) == 1
    assert audit['changes'] and not audit['semantic_fields_changed']
    assert parse_error(packet, raw) is None
    assert review(rec, raw, packet, knowledge)[0]['v10'] == 0


def test_existing_canonical_consumer_routes_condition_reading_without_default_calls():
    rec, packet, knowledge = setup()
    raw = response([finding(packet)])
    row, log = B4Pipeline(DATA, None).consume(rec, packet, raw)
    assert row['v10'] == 0 and log['source_scope_promoted']
    packet['family'] = 'A'
    with pytest.raises(ValueError, match='C family'):
        B4Pipeline(DATA, None).consume(rec, packet, raw)


def test_supplied_unknown_clause_is_kept_and_not_partially_compiled():
    rec, packet, knowledge = setup()
    row = copy.deepcopy(knowledge.products['4321150102'])
    row['특이사항'] += ' 3. 확인되지 않은 추가 적용조건'
    knowledge.products['4321150102'] = row
    knowledge._product_facts.products['4321150102'] = row
    packet = packet_for(rec, knowledge)
    plan = packet['catalog_conditions']['plan']
    assert plan[0]['unsupported_note_preserved'] and plan[0]['program'] is None
    assert plan[0]['note'].endswith('추가 적용조건')


@pytest.mark.parametrize('count', [1, 512])
def test_wire_schema_compiles_in_the_actual_cpu_guidance_backend(count):
    validate_grammar(generation_schema('catalog_conditions', count, ITEMS))
    assert schema(count)['properties']['findings']['items']['properties']['value_units']['uniqueItems']


@pytest.mark.parametrize('statement', [
    '만약 새로 납품하면 회전익이어야 한다.', '필요시 회전익이어야 한다.',
    '교체할 때에는 회전익이어야 한다.', '발주자가 희망하면 회전익이어야 한다.',
    '일부 기체는 회전익이어야 한다.', '선택 사양은 회전익이어야 한다.',
])
def test_conditional_airframe_requirements_cannot_become_whole_purchase_facts(statement):
    from submission.pps.catalog_condition_review import property_readings
    rec, _, _ = setup(statement, name='드론', code='2513189901')
    facts = property_readings(rec, '드론', ['fixed_wing'])
    assert facts['observations'] and all(o['issue'] for o in facts['observations'])


def test_new_condition_based_positive_defers_a_disclosed_requirement_waiver():
    rec, _, knowledge = setup('CPU 아키텍처: x86\nCPU 개수: 1개',
        extra='본 사업의 컴퓨터서버는 추정가격 1천만 원 미만으로 직접생산확인증명서를 별도로 요구하지 않습니다.')
    packet = packet_for(rec, knowledge)
    row, log = review(rec, response([finding(packet), finding(packet, 'CPU 개수:', field='cpu_count')]), packet, knowledge)
    assert log['product']['status'] == 'competition'
    assert 'v10' not in row and 'e10' not in row
    assert log['deferred_decisions']['v10']['proposed_decision']['value'] == 1
    assert not log['deferred_decisions']['v10']['waiver_certified']


def test_submission_waiver_is_not_a_possession_waiver():
    rec, _, knowledge = setup('CPU 아키텍처: x86\nCPU 개수: 1개',
        extra='직접생산확인서 제출은 생략합니다.')
    packet = packet_for(rec, knowledge)
    row, log = review(rec, response([finding(packet), finding(packet, 'CPU 개수:', field='cpu_count')]), packet, knowledge)
    assert row['v10'] == 1 and 'v10' not in log['deferred_decisions']
    assert log['production_exception_observations'][0]['action'] == 'submission'


def test_condition_references_keep_original_text_and_do_not_certify_permissions():
    rec, packet, knowledge = setup(extra='동등한 타 아키텍처도 가능하다.')
    claim = finding(packet)
    claim['condition_units'] = units(packet, '동등한')
    row, log = review(rec, response([claim]), packet, knowledge)
    assert row is None
    evidence = log['conditions'][0]['links'][0]['condition_evidence']
    assert evidence and all(rec['docs'][e['doc_index']]['text'][e['start']:e['end']] == e['text'] for e in evidence)
