"""Permission omissions and visible line breaks cannot certify fixed attributes."""
import copy

import jsonschema
import pytest

from submission.b4_entry import parse_error
from submission.pps import catalog_permissions as perms, catalog_semantics as sem, catalog_source_roles as roles
from tests.test_catalog_condition_review import setup, finding, units
from tests.test_catalog_semantics import obj, response, permission, semantic_packet
from tests.test_catalog_source_roles import effective, attach


RELEASES = ['CPU 아키텍처는 변경해도 무방하다.',
    'CPU 아키텍처는 ARM 이외의 구조도 허용한다.',
    'CPU 아키텍처에 제한을 두지 않는다.',
    'CPU 아키텍처는 납품 시 협의하여 변경할 수 있다.']


@pytest.mark.parametrize('text', RELEASES)
@pytest.mark.parametrize('effect', ['relaxes_field', 'unclear', 'preserves_field'])
def test_previously_ignored_release_is_consumed_and_cannot_settle_a_fixed_attribute(text, effect):
    rec, _, knowledge = setup(extra=text)
    packet = semantic_packet(rec, knowledge)
    before = copy.deepcopy((rec, packet))
    payload = obj(findings=[finding(packet)], permissions=[permission(packet, text, effect=effect)])
    assert parse_error(packet, response(payload)) is None
    row, detail = sem.review(rec, response(payload), packet, knowledge)
    assert row is None and detail['conditions'][0]['status'] == 'unknown'
    assert any(p['checks'] for p in detail['conditions'][0]['permission_interpretations'])
    assert (rec, packet) == before


@pytest.mark.parametrize('text', RELEASES)
def test_missing_model_permission_does_not_hide_an_original_release(text):
    rec, _, knowledge = setup(extra=text)
    packet = semantic_packet(rec, knowledge)
    row, detail = sem.review(rec, response(obj(findings=[finding(packet)])), packet, knowledge)
    assert row is None and detail['conditions'][0]['scope_issues']


@pytest.mark.parametrize('effect', ['relaxes_field', 'unclear'])
def test_model_permission_outside_lexical_inventory_is_accounted_for(effect):
    text = '해당 항목은 반드시 맞출 필요가 없습니다.'
    rec, _, knowledge = setup(extra=text)
    assert not perms.occurrences(rec)
    packet = semantic_packet(rec, knowledge)
    payload = obj(findings=[finding(packet)], permissions=[permission(packet, text, effect=effect)])
    row, detail = sem.review(rec, response(payload), packet, knowledge)
    condition = detail['conditions'][0]
    assert row is None and condition['status'] == 'unknown'
    assert any(p['source_issue']['kind'] == 'model_proposed_permission_scope'
        and p['field'] == 'cpu_architecture' and p['checks'] for p in condition['permission_interpretations'])


def test_permission_for_one_field_does_not_suppress_unrelated_decisive_fields():
    text = '해당 항목은 반드시 맞출 필요가 없습니다.'
    rec, _, knowledge = setup('CPU 아키텍처: ARM\nCPU 개수: 1개', extra=text)
    packet = semantic_packet(rec, knowledge)
    claim = permission(packet, text, effect='relaxes_field', field='cpu_count',
        value_units=units(packet, 'CPU 개수:'))
    row, detail = sem.review(rec, response(obj(findings=[finding(packet)], permissions=[claim])), packet, knowledge)
    assert row['v10'] == 0 and detail['conditions'][0]['status'] == 'not_met'


def test_attribute_preservation_and_another_clause_release_are_kept_separate():
    text = 'CPU 아키텍처는 유지하고 메모리 용량은 변경해도 무방하다.'
    assert not perms.preservation_conflict('cpu_architecture', text)
    rec, _, knowledge = setup(extra=text)
    packet = semantic_packet(rec, knowledge)
    row, detail = sem.review(rec, response(obj(findings=[finding(packet)],
        permissions=[permission(packet, text)])), packet, knowledge)
    assert row['v10'] == 0 and detail['conditions'][0]['status'] == 'not_met'


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_wrapped_permission_retains_exact_source_and_both_sides(newline):
    text = newline.join(['1. 규격과 동등하거나 그 이상인 장비를', '납품하여야 한다.', '2. 계약기간'])
    rec = {'docs': [{'text': text}]}
    ev = {'doc_index': 0, 'start': text.index('동등'), 'end': text.index('동등')+2}
    result = perms.reading_extent(rec, ev)
    full = result['evidence']
    assert full['text'] == newline.join(text.split(newline)[:2])
    assert text[full['start']:full['end']] == full['text']
    assert not result['unclosed_continuation'] and not result['source_reordered']
    assert '2. 계약기간' not in full['text']


def test_permission_trigger_on_second_line_retains_its_subject():
    text = 'CPU 아키텍처는\n변경해도 무방하다.'
    rec = {'docs': [{'text': text}]}
    found = perms.occurrences(rec, ['cpu_architecture'])
    assert found and perms.reading_extent(rec, found[0])['evidence']['text'] == text


@pytest.mark.parametrize('following', ['', '\n납품하여야 한다.', '2. 다음 절', '품명: 다른 장비', '항목 | 값'])
def test_dangling_clause_is_not_completed_across_an_unrelated_block(following):
    text = '1. 동등 제품의 경우\n'+following
    rec = {'docs': [{'text': text}]}
    extent = perms.reading_extent(rec, {'doc_index': 0, 'start': 3, 'end': 5})
    assert extent['unclosed_continuation']
    assert extent['evidence']['text'] == '1. 동등 제품의 경우'


def test_complete_permission_reference_is_required_to_discharge_a_wrapped_issue():
    text = '동등한 장비를\n납품할 수 있다.'
    rec, _, knowledge = setup(extra=text)
    packet = semantic_packet(rec, knowledge)
    p = permission(packet, '동등한 장비를')
    row, detail = sem.review(rec, response(obj(findings=[finding(packet)], permissions=[p])), packet, knowledge)
    assert row is None
    p['source_units'] += units(packet, '납품할 수 있다.')
    row, detail = sem.review(rec, response(obj(findings=[finding(packet)], permissions=[p])), packet, knowledge)
    assert row['v10'] == 0
    assert any(p['source_statement']['text'] == text for p in detail['conditions'][0]['permission_interpretations'])


def test_current_role_mask_includes_complete_release_and_legacy_masks_remain_readable():
    rec, packet, _ = setup(extra='CPU 아키텍처는\n변경해도 무방하다.')
    plan = packet['catalog_conditions']['plan']
    old = roles.build(rec, packet['spans'], plan, version=2)
    assert old['generation']['permission_options'] == []
    packet['catalog_conditions'].update(source_roles=old, interpretation_mode=sem.FORMAT)
    packet['generation'].update(catalog_roles=old['generation'], response_format=sem.FORMAT)
    assert roles.validate_prepared(rec, packet) == old['generation']
    jsonschema.validate(obj(findings=[None]), effective(packet))
    assert parse_error(packet, response(obj(findings=[None]))) is None
    attach(rec, packet)
    assert packet['generation']['catalog_roles']['version'] == 3
    refs = units(packet, 'CPU 아키텍처는') + units(packet, '변경해도 무방하다.')
    assert packet['generation']['catalog_roles']['permission_options'] == [refs]
    p = permission(packet, 'CPU 아키텍처는', effect='relaxes_field', source_units=refs)
    jsonschema.validate(obj(permissions=[p]), effective(packet))


def test_unclosed_permission_cannot_be_resolved_by_a_model_preserves_claim():
    rec, _, knowledge = setup(extra='동등한 제품의 경우\n2. 다른 조건')
    packet = semantic_packet(rec, knowledge)
    row, _ = sem.review(rec, response(obj(findings=[finding(packet)],
        permissions=[permission(packet, '동등한 제품의 경우')])), packet, knowledge)
    assert row is None


def test_bounded_backward_search_reports_the_missing_condition_prefix():
    text = '동등 제품의 경우\n조건에 따라\nCPU 아키텍처는\n변경해도 무방하다.'
    rec = {'docs': [{'text': text}]}
    extent = perms.reading_extent(rec, {'doc_index': 0, 'start': text.index('변경'), 'end': len(text)}, max_lines=2)
    assert extent['unclosed_continuation'] and extent['line_count'] == 2


def test_explicit_denial_of_alternative_architectures_does_not_relax_the_field():
    text = 'CPU 아키텍처는 ARM 이외의 구조도 허용하지 않는다.'
    assert not perms.preservation_conflict('cpu_architecture', text)
    rec, _, knowledge = setup(extra=text)
    packet = semantic_packet(rec, knowledge)
    row, _ = sem.review(rec, response(obj(findings=[finding(packet)],
        permissions=[permission(packet, text)])), packet, knowledge)
    assert row['v10'] == 0


def test_wrapped_subject_release_cannot_be_overruled_by_preserves_field():
    text = 'CPU 아키텍처는\n변경해도 무방하다.'
    rec, _, knowledge = setup(extra=text)
    packet = semantic_packet(rec, knowledge)
    claim = permission(packet, 'CPU 아키텍처는', source_units=
        units(packet, 'CPU 아키텍처는')+units(packet, '변경해도 무방하다.'))
    row, _ = sem.review(rec, response(obj(findings=[finding(packet)], permissions=[claim])), packet, knowledge)
    assert row is None


def test_a_qualification_penalty_exception_is_not_a_product_attribute_release_candidate():
    text = '직접생산 확인기준을 위반하지 않은 경우 입찰참가자격 제한조치는 제외할 수 있습니다.'
    rec = {'docs': [{'text': text}]}
    assert not perms.occurrences(rec, ['public_agency_promotion', 'commissioning_public_agency_identified'])
