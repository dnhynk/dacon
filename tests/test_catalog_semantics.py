"""Semantic interpretation is fallible, but its source/coverage cannot be fabricated."""
import copy
import dataclasses
import json

import pytest

from submission.b4_entry import B4Pipeline, parse_error
from submission.pps import catalog_semantics as semantics
from submission.pps.generation_contract import generation_schema, validate_grammar
from submission.pps.requirement_frames import frames
from tests.test_catalog_condition_review import setup, packet_for, finding, units


def semantic_packet(rec, knowledge):
    packet = packet_for(rec, knowledge)
    packet['generation']['response_format'] = semantics.FORMAT
    packet['catalog_conditions']['interpretation_mode'] = semantics.FORMAT
    return packet


def obj(**kwargs):
    return {'findings': [], 'unresolved_fields': [], 'semantic_readings': [], 'permissions': [], **kwargs}


def response(payload):
    return {'text': json.dumps(payload, ensure_ascii=False), 'finish_reason': 'stop'}


def video(text):
    rec, _, knowledge = setup(text, name='동영상제작서비스', code='8213160301')
    rec['meta']['업무구분'] = '일반용역'
    return rec, semantic_packet(rec, knowledge), knowledge


def reading(packet, text, field='commissioning_public_agency_identified', polarity='affirmed'):
    return {'code': '8213160301', 'field': field, 'value_units': units(packet, text),
        'scope_units': units(packet, '품명: 동영상제작서비스'), 'condition_units': [],
        'scope': 'whole_named_purchase', 'modality': 'required', 'quantifier': 'all_named_targets',
        'reason': '실제 납품 영상의 필수 포함 내용과 전체 적용 범위를 해석', 'polarity': polarity}


def test_natural_identifiable_video_fact_is_consumable_and_explicitly_fallible():
    rec, packet, knowledge = video('모든 납품영상에는 발주기관의 로고를 삽입하여야 한다.')
    original = copy.deepcopy(rec)
    raw = response(obj(semantic_readings=[reading(packet, '모든 납품영상')]))
    assert parse_error(packet, raw) is None
    row, details = B4Pipeline(knowledge.data_dir if hasattr(knowledge, 'data_dir') else
        'data_open/data', None).consume(rec, packet, raw)
    assert row['v12'] == 0 and details['product']['status'] == 'competition'
    condition = details['conditions'][0]
    assert not condition['source_values_only'] and not condition['semantic_truth_certified']
    assert condition['semantic_candidates'][0]['value_origin'] == 'fallible_source_addressed_model_interpretation'
    assert rec == original


def test_explicit_negative_natural_facts_can_short_circuit_the_supplied_program():
    rec, packet, knowledge = video('모든 납품영상은 공공기관 홍보용이 아니다.\n모든 납품영상에 기관의 로고 등 식별정보를 포함하지 않는다.')
    payload = obj(semantic_readings=[reading(packet, '홍보용이', 'public_agency_promotion', 'denied'),
        reading(packet, '식별정보를', polarity='denied')])
    row, details = semantics.review(rec, response(payload), packet, knowledge)
    assert details['product']['status'] == 'general' and row['v10'] == 0


@pytest.mark.parametrize(('key', 'value'), [('scope', 'component'), ('scope', 'other'),
    ('quantifier', 'some_targets'), ('quantifier', 'unspecified'), ('modality', 'optional'),
    ('modality', 'example'), ('polarity', 'unknown')])
def test_partial_conditional_or_unknown_statement_is_not_a_whole_purchase_fact(key, value):
    rec, packet, knowledge = video('납품영상에는 발주기관의 로고를 삽입한다.')
    claim = reading(packet, '납품영상에는')
    claim[key] = value
    row, details = semantics.review(rec, response(obj(semantic_readings=[claim])), packet, knowledge)
    assert row is None and details['conditions'][0]['status'] == 'unknown'


@pytest.mark.parametrize('text', ['교육용 영상만 제작한다.', '학습자의 역량을 높일 교육콘텐츠를 제작한다.'])
def test_education_is_not_a_negative_promotion_fact(text):
    rec, packet, knowledge = video(text)
    claim = reading(packet, text, field='public_agency_promotion', polarity='denied')
    row, details = semantics.review(rec, response(obj(semantic_readings=[claim])), packet, knowledge)
    assert row is None and details['conditions'][0]['unmatched_semantic_readings'] == [claim]


def test_a_denied_polarity_needs_a_source_denial_not_just_a_valid_quote():
    rec, packet, knowledge = video('모든 납품영상은 공공기관 홍보를 목적으로 한다.')
    claim = reading(packet, '모든 납품영상은', field='public_agency_promotion', polarity='denied')
    row, details = semantics.review(rec, response(obj(semantic_readings=[claim])), packet, knowledge)
    assert row is None
    assert details['conditions'][0]['semantic_candidates'][0]['interpretations'][0]['issue'] == 'negative_property_has_no_original_denial'


def test_absence_reason_and_explicit_unresolved_field_are_not_overridden_by_polarity():
    rec, packet, knowledge = video('모든 납품영상에는 기관의 로고를 포함한다.')
    claim = reading(packet, '모든 납품영상')
    for payload in [obj(semantic_readings=[{**claim, 'reason': '해당 내용이 없으므로 추론'}]),
        obj(semantic_readings=[claim], unresolved_fields=[{'code': claim['code'],
            'field': claim['field'], 'reason': '대상 범위 미확정'}])]:
        row, details = semantics.review(rec, response(payload), packet, knowledge)
        assert row is None and details['conditions'][0]['status'] == 'unknown'


def test_a_capability_to_use_character_assets_is_not_the_delivered_property():
    rec, packet, knowledge = video('기관 캐릭터를 AI화하여 사용할 계획이기 때문에 가능하도록 준비 필수')
    row, details = semantics.review(rec, response(obj(semantic_readings=[reading(packet, '기관 캐릭터')])), packet, knowledge)
    assert row is None
    assert details['conditions'][0]['semantic_candidates'][0]['interpretations'][0]['issue'] == 'asset_capability_is_not_identifiable_deliverable'


def test_omitted_or_conflicting_narrative_observations_remain_unknown():
    rec, packet, knowledge = video('모든 납품영상에는 기관의 로고를 포함한다.\n다른 납품영상에는 기관의 로고를 포함하지 않는다.')
    claim = reading(packet, '모든 납품영상')
    row, details = semantics.review(rec, response(obj(semantic_readings=[claim])), packet, knowledge)
    assert row is None and len(details['conditions'][0]['semantic_candidates']) == 2
    contradictory = {**claim, 'scope': 'component'}
    row, details = semantics.review(rec, response(obj(semantic_readings=[claim, contradictory])), packet, knowledge)
    assert row is None


def test_nonnumeric_semantic_claim_cannot_create_a_numeric_value():
    rec, _, knowledge = setup('CPU 개수: 1개')
    packet = semantic_packet(rec, knowledge)
    claim = {**reading(packet, 'CPU 개수', field='cpu_count'), 'code': '4321150102',
             'scope_units': units(packet, '품명: 컴퓨터서버')}
    raw = response(obj(semantic_readings=[claim]))
    assert parse_error(packet, raw) is None  # Typed JSON; wrong semantic field is not a reroll.
    row, details = semantics.review(rec, raw, packet, knowledge)
    assert row is None and details['gate'] == 'model_used_undeclared_condition_field'


def permission(packet, text, **changes):
    return {'code': '4321150102', 'field': 'cpu_architecture', 'source_units': units(packet, text),
        'value_units': units(packet, 'CPU 아키텍처:'), 'scope_units': units(packet, '품명: 컴퓨터서버'),
        'condition_scope_units': [], 'reason': '원문 허용과 속성 제약의 관계를 해석',
        'effect': 'preserves_field', **changes}


def test_per_field_permission_allows_decisive_branch_without_certifying_semantics():
    rec, _, knowledge = setup(extra='상기 사항과 동등 이상의 제품을 납품할 수 있다.')
    packet = semantic_packet(rec, knowledge)
    payload = obj(findings=[finding(packet)], permissions=[permission(packet, '동등 이상의')])
    row, details = semantics.review(rec, response(payload), packet, knowledge)
    assert row['v10'] == 0 and details['product']['status'] == 'general'
    condition = details['conditions'][0]
    assert condition['scope_issues'] and condition['permission_interpretations']
    assert any(not p['resolved_by_fallible_interpretation'] for p in condition['permission_interpretations'])
    assert condition['status'] == 'not_met' and not condition['semantic_truth_certified']


@pytest.mark.parametrize('effect', ['relaxes_field', 'unclear', 'other_subject'])
def test_unresolved_permission_and_unsupported_other_subject_are_not_dismissed(effect):
    rec, _, knowledge = setup(extra='상기 사항과 동등 이상의 제품을 납품할 수 있다.')
    packet = semantic_packet(rec, knowledge)
    row, details = semantics.review(rec, response(obj(findings=[finding(packet)],
        permissions=[permission(packet, '동등 이상의', effect=effect)])), packet, knowledge)
    assert row is None and details['conditions'][0]['status'] == 'unknown'


def test_explicit_alternative_architecture_cannot_be_erased_by_a_preserves_claim():
    rec, _, knowledge = setup(extra='다른 CPU 아키텍처를 선택할 수 있다.')
    packet = semantic_packet(rec, knowledge)
    row, _ = semantics.review(rec, response(obj(findings=[finding(packet)],
        permissions=[permission(packet, '다른 CPU')])), packet, knowledge)
    assert row is None


def test_permission_reference_must_cover_the_whole_original_statement():
    rec, _, knowledge = setup(extra='상기 사항과 동등 이상의 제품을 납품할 수 있다.')
    packet = semantic_packet(rec, knowledge)
    claim = permission(packet, '동등 이상의')
    n = claim['source_units'][0]
    span = packet['spans'][n-1]
    offset = span.text.index('동등')
    packet['spans'][n-1] = dataclasses.replace(span, start=span.start+offset,
        end=span.start+offset+2, text='동등')
    row, _ = semantics.review(rec, response(obj(findings=[finding(packet)], permissions=[claim])), packet, knowledge)
    assert row is None


def test_unselected_other_document_permission_is_still_unresolved():
    rec, _, knowledge = setup(extra='동등한 제품으로 납품할 수 있다.')
    rec['docs'].append({'doc_id': 'extra', 'type': '규격서', 'text': '다른 아키텍처를 선택할 수 있다.'})
    packet = semantic_packet(rec, knowledge)
    packet['spans'] = [s for s in packet['spans'] if s.doc_index == 0]
    row, details = semantics.review(rec, response(obj(findings=[finding(packet)],
        permissions=[permission(packet, '동등한 제품')])), packet, knowledge)
    assert row is None and any(p['source_issue']['doc_index'] == 1 for p in details['conditions'][0]['permission_interpretations'])


def test_a_requirement_component_needs_its_header_and_an_explicit_whole_contract_bridge():
    text = '요구사항ID\nDER-001\n요구사항명\n신규 영상 제작\n모든 영상에 기관의 로고를 포함한다.\n요구사항ID\nDER-002\n요구사항명\n기존 영상 편집\n편집 15건'
    rec, packet, knowledge = video(text)
    claim = reading(packet, '모든 영상에')
    row, details = semantics.review(rec, response(obj(semantic_readings=[claim])), packet, knowledge)
    assert row is None
    candidate = details['conditions'][0]['semantic_candidates'][0]
    assert candidate['interpretations'][0]['issue'] == 'requirement_frame_header_not_read'
    header = frames(rec)[0]['heading']
    claim['scope_units'] += [n for n, s in enumerate(packet['spans'], 1) if s.start < header['end'] and s.end > header['start']]
    row, details = semantics.review(rec, response(obj(semantic_readings=[claim])), packet, knowledge)
    assert row is None
    assert details['conditions'][0]['semantic_candidates'][0]['interpretations'][0]['issue'] == 'requirement_component_not_whole_contract'


def test_explicit_contract_wide_property_inside_a_read_requirement_is_not_always_rejected():
    text = '요구사항ID\nABC-001\n요구사항명\n영상 제작\n본 과업 전체의 모든 영상에 발주기관의 로고를 포함한다.\n요구사항ID\nABC-002\n요구사항명\n기존 영상 편집\n편집 업무'
    rec, packet, knowledge = video(text)
    claim = reading(packet, '본 과업 전체')
    header = frames(rec)[0]['heading']
    claim['scope_units'] += [n for n, s in enumerate(packet['spans'], 1) if s.start < header['end'] and s.end > header['start']]
    row, details = semantics.review(rec, response(obj(semantic_readings=[claim])), packet, knowledge)
    assert row['v12'] == 0 and details['conditions'][0]['status'] == 'met'


@pytest.mark.parametrize('text', ['발주기관의 로고뿐만 아니라 연락처도 포함한다.',
    '발주기관의 로고를 넣는 데 그치지 않고 명칭과 연락처를 추가한다.'])
def test_additive_negation_does_not_support_a_negative_property(text):
    rec, packet, knowledge = video(text)
    row, details = semantics.review(rec, response(obj(semantic_readings=[reading(packet, text, polarity='denied')])), packet, knowledge)
    assert row is None
    assert details['conditions'][0]['semantic_candidates'][0]['interpretations'][0]['issue'] == 'additive_negation_is_not_property_denial'


def test_household_word_and_real_hypothesis_in_same_line_are_distinct_source_occurrences():
    text = '모든 납품영상에 기관 로고를 포함한다.\n가정보호 교육으로 변경한다고 가정하면 로고를 삭제한다.'
    rec, packet, knowledge = video(text)
    row, details = semantics.review(rec, response(obj(semantic_readings=[reading(packet, '모든 납품영상')])), packet, knowledge)
    assert row is None
    result = details['conditions'][0]
    assert len(result['lexical_noncondition_triggers']) == 1
    assert any(p['text'] == '가정' for p in result['scope_issues'])


@pytest.mark.parametrize('text', [
    'DER-001\n영상 제작\nDER-002\n영상 편집',
    '요구사항ID\n요구사항명\nDER-001\n영상 제작',
    '요구사항ID\nDER-001\n요구사항명\n요구사항 분류\n개발 요구사항',
])
def test_requirement_frames_do_not_repair_toc_or_mixed_column_order(text):
    assert frames({'docs': [{'type': '제안요청서', 'text': text}]}) == []


def test_frame_source_locations_preserve_id_name_and_close_at_next_requirement():
    text = '요구사항ID: ABC-001\n요구사항명: 새 영상 제작\n원문\n요구사항ID\nDEF-002\n요구사항명\n편집\n원문'
    result = frames({'docs': [{'type': '제안요청서', 'text': text}]})
    assert [r['id'] for r in result] == ['ABC-001', 'DEF-002']
    assert result[0]['end'] == result[1]['start']
    assert all(text[r['heading']['start']:r['heading']['end']] == r['heading']['text'] for r in result)


def test_short_english_property_cue_is_not_a_substring_of_another_word():
    assert not semantics.cue('commissioning_public_agency_identified', 'special basic video')
    assert semantics.cue('commissioning_public_agency_identified', '기관 CI를 표시')


@pytest.mark.parametrize('line', ['서약자 기관명 ○○○○○ 대표 ○○○ (인)', '기 관 명 : (인) (전화번호 : )'])
def test_blank_signature_name_is_recorded_without_becoming_a_video_property(line):
    rec, packet, knowledge = video('모든 납품영상에는 발주기관의 로고를 포함한다.\n'+line)
    before = copy.deepcopy(rec)
    row, details = semantics.review(rec, response(obj(semantic_readings=[reading(packet, '모든 납품영상')])), packet, knowledge)
    condition = details['conditions'][0]
    assert row['v12'] == 0 and condition['status'] == 'met'
    assert [o['evidence']['text'] for o in condition['non_property_source_observations']] == [line]
    assert rec == before


def test_nonblank_video_agency_name_cannot_be_discarded_as_an_empty_form():
    rec, packet, knowledge = video('모든 납품영상에는 발주기관의 로고를 포함한다.\n기관명: 영상에는 기관명을 포함하지 않는다.')
    row, details = semantics.review(rec, response(obj(semantic_readings=[reading(packet, '모든 납품영상')])), packet, knowledge)
    assert row is None and not details['conditions'][0]['non_property_source_observations']


@pytest.mark.parametrize('ref', [0, 999, 1.0, True])
def test_semantic_refs_are_bounded_exact_integers(ref):
    rec, packet, _ = video('기관 로고를 표시한다.')
    claim = reading(packet, '기관 로고')
    claim['value_units'] = [ref]
    assert parse_error(packet, response(obj(semantic_readings=[claim]))) is not None


def test_duplicate_semantic_reference_sets_are_idempotent_without_polarity_repair():
    rec, packet, _ = video('기관 로고를 표시한다.')
    claim = reading(packet, '기관 로고')
    claim['value_units'] *= 2
    parsed, audit = semantics.decode(response(obj(semantic_readings=[claim]))['text'], packet['spans'])
    assert len(parsed['semantic_readings'][0]['value_units']) == 1
    assert audit['changes'] and not audit['semantic_fields_changed']
    assert parsed['semantic_readings'][0]['polarity'] == 'affirmed'


@pytest.mark.parametrize('count', [1, 512])
def test_semantic_wire_grammar_compiles(count):
    validate_grammar(generation_schema(semantics.FORMAT, count, tuple(range(10, 19))))


def test_generation_blocks_numeric_and_enum_fields_in_boolean_channel():
    import jsonschema
    rec, packet, _ = video('기관의 로고를 표시한다.')
    claim = reading(packet, '기관의 로고')
    effective = generation_schema(semantics.FORMAT, len(packet['spans']), tuple(range(10, 19)))
    for field in ('cpu_architecture', 'self_weight_kg', 'operating_altitude_m'):
        payload = obj(semantic_readings=[{**claim, 'field': field}])
        # Readable legacy observations remain auditable, but a new constrained
        # generation cannot emit this category error in the first place.
        semantics.decode(json.dumps(payload), packet['spans'])
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, effective)
    jsonschema.validate(obj(semantic_readings=[claim]), effective)
