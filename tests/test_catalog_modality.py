"""A valid model citation cannot turn permission or capability into a duty."""
import copy

import pytest

from submission.pps import catalog_semantics as sem
from tests.test_catalog_condition_review import setup, finding
from tests.test_catalog_semantics import video, reading, response, obj, semantic_packet


@pytest.mark.parametrize('tail', [
    '삽입할 수 있다.', '삽입할 수 있어야 한다.', '삽입해도 된다.',
    '삽입하여도 무방하다.', '삽입하는 것을 권장한다.', '삽입을 권장한다.',
    '삽입 가능하다.', '삽입하는 기능을 제공하여야 한다.',
    '삽입할 수 있는 기능을 제공하여야 한다.',
    '삽입하도록 권장한다.', '삽입을 허용한다.', '삽입은 선택사항이다.',
])
def test_optional_action_or_mandatory_capability_is_not_actual_output_inclusion(tail):
    text = '모든 납품영상에는 발주기관의 로고를 '+tail
    rec, packet, knowledge = video(text)
    claim = reading(packet, text)
    original = copy.deepcopy((rec, packet, claim))
    row, log = sem.review(rec, response(obj(semantic_readings=[claim])), packet, knowledge)
    c = log['conditions'][0]
    assert row is None and c['status'] == 'unknown'
    interpretation = c['semantic_candidates'][0]['interpretations'][0]
    check = interpretation['original_modality_review']
    assert check['issue'] == 'original_property_does_not_establish_delivery_obligation'
    for r in check['relations']:
        assert text[r['start']:r['end']] == r['text']
    assert (rec, packet, claim) == original


@pytest.mark.parametrize('caption', ['권장 사항', '선택사항', '참고 내용', '예시'])
def test_explicit_weak_caption_cannot_be_overridden_by_required(caption):
    text = caption+': 모든 납품영상에는 발주기관의 로고를 삽입하여야 한다.'
    rec, packet, knowledge = video(text)
    row, log = sem.review(rec, response(obj(semantic_readings=[reading(packet, text)])), packet, knowledge)
    assert row is None and log['conditions'][0]['status'] == 'unknown'


@pytest.mark.parametrize('text', [
    '모든 납품영상에는 기관의 로고를 포함하여야 한다.',
    '모든 납품영상에는 기관의 로고를 삽입하고 자막은 변경할 수 있다.',
    '모든 납품영상에는 기관의 로고를 삽입하여야 한다. 자막은 변경할 수 있다.',
    '자막은 변경할 수 있다. 모든 납품영상에는 기관의 로고를 삽입하여야 한다.',
    '모든 납품영상에는 기관의 로고를 포함한다. 별도 편집기능 제공을 권장한다.',
    '모든 납품영상에는 기관을 식별할 수 있는 로고를 포함하여야 한다.',
])
def test_actual_inclusion_is_distinct_from_other_optional_action_or_identifiability(text):
    rec, packet, knowledge = video(text)
    row, log = sem.review(rec, response(obj(semantic_readings=[reading(packet, text)])), packet, knowledge)
    assert row is not None and log['conditions'][0]['status'] == 'met'


def test_another_clause_caption_does_not_supply_this_property_modality():
    text = '권장 사항: 색상 편집기능 제공. 모든 납품영상에는 기관의 로고를 삽입하여야 한다.'
    check = sem.modality_review('commissioning_public_agency_identified', text, 'required')
    assert check['issue'] is None
    assert {r['kind'] for r in check['relations']} == {'explicit_requirement'}


@pytest.mark.parametrize('modality', ['optional', 'example', 'negated'])
def test_model_contradiction_is_reported_without_silently_rewriting_claim(modality):
    text = '모든 납품영상에는 발주기관의 로고를 삽입하여야 한다.'
    rec, packet, knowledge = video(text)
    claim = reading(packet, text)
    claim['modality'] = modality
    row, log = sem.review(rec, response(obj(semantic_readings=[claim])), packet, knowledge)
    interpretation = log['conditions'][0]['semantic_candidates'][0]['interpretations'][0]
    assert interpretation['original_modality_review']['issue'] == 'model_modality_conflicts_with_original_requirement'
    assert row is None and interpretation['reading'] == claim


def test_rotary_wing_literal_optional_conflict_is_also_visible_to_consumer():
    text = '회전익이여야 한다.'
    rec, _, knowledge = setup(text, name='드론', code='2513189901')
    packet = semantic_packet(rec, knowledge)
    claim = finding(packet, text, field='fixed_wing', code='2513189901', name='드론')
    claim['modality'] = 'optional'
    row, log = sem.review(rec, response(obj(findings=[claim])), packet, knowledge)
    c = log['conditions'][0]
    assert row is None
    assert c['links'][0]['original_modality_review']['issue'] == 'model_modality_conflicts_with_original_requirement'
    assert not c['links'][0]['accepted_scope_link']
    assert c['observations'][0]['value'] is False
