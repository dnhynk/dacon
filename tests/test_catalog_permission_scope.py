"""A model's other-subject claim cannot erase the clause's explicit scope."""
import copy

import pytest

from submission.pps import catalog_semantics as sem
from submission.pps.catalog_permissions import other_subject_scope
from tests.test_catalog_condition_review import setup, finding, units
from tests.test_catalog_semantics import semantic_packet, obj, response, permission


def reviewed(text, *, owner='모니터', caption='품명: '):
    rec, _, knowledge = setup(extra=caption+owner+'\n'+text)
    packet = semantic_packet(rec, knowledge)
    payload = obj(findings=[finding(packet)], permissions=[permission(packet,text,
        effect='other_subject', condition_scope_units=units(packet,caption+owner),
        source_units=list(dict.fromkeys(n for line in text.splitlines() for n in units(packet,line))))])
    saved = copy.deepcopy((rec,packet,payload))
    row, detail = sem.review(rec,response(payload),packet,knowledge)
    assert (rec,packet,payload) == saved
    condition = detail['conditions'][0]
    return row, condition


@pytest.mark.parametrize('text,issue', [
    ('컴퓨터서버의 CPU 아키텍처는 다른 구조를 선택할 수 있다.', 'permission_mentions_current_named_target'),
    ('컴퓨터서버와 모니터의 CPU 아키텍처는 다른 구조를 선택할 수 있다.', 'permission_mentions_current_named_target'),
    ('본 계약의 전체 납품대상은 다른 CPU 아키텍처를 선택할 수 있다.', 'contract_wide_permission_cannot_be_other_subject'),
    ('이 규격서의 모든 품목은 다른 CPU 아키텍처를 선택할 수 있다.', 'contract_wide_permission_cannot_be_other_subject'),
    ('모니터를 포함한 모든 구매품목은 다른 CPU 아키텍처를 선택할 수 있다.', 'contract_wide_permission_cannot_be_other_subject'),
    ('CPU 아키텍처는 다른 구조를 선택할 수 있다.', 'different_header_does_not_prove_exclusive_permission_scope'),
])
def test_different_heading_cannot_erase_shared_or_current_target_permission(text, issue):
    row, condition = reviewed(text)
    assert row is None and condition['status'] == 'unknown'
    checks = [c for p in condition['permission_interpretations'] for c in p['checks']]
    assert checks and all(not c['accepted_interpretation'] for c in checks)
    assert checks[0]['original_subject_scope_check']['issue'] == issue


@pytest.mark.parametrize('text', ['모니터의 CPU 아키텍처는 다른 구조를 선택할 수 있다.',
    '해당 품목의 CPU 아키텍처는 다른 구조를 선택할 수 있다.',
    '본 품목에만 다른 CPU 아키텍처를 선택할 수 있다.'])
def test_explicit_other_item_still_has_a_fallible_resolvable_path(text):
    _, condition = reviewed(text)
    assert condition['status'] == 'not_met'
    checks = [c for p in condition['permission_interpretations'] for c in p['checks']]
    assert checks[0]['accepted_interpretation']
    assert not checks[0]['original_subject_scope_check']['semantic_truth_certified']


def test_same_name_under_different_field_labels_is_not_a_different_target():
    target = {'text':'품명: 컴퓨터서버'}
    owner = {'text':'물품명: 컴퓨터서버'}
    full = {'doc_index':0,'start':100,'end':130,'text':'해당 품목의 다른 아키텍처를 선택할 수 있다.'}
    result = other_subject_scope(full,[owner],[target])
    assert result['issue'] == 'same_original_name_is_not_other_subject'


def test_literal_item_row_name_is_used_without_recovering_catalog_identity():
    target = {'role':'literal_item_row_candidate','text':'4 | 드론 | 식 | 1'}
    owner = {'role':'literal_item_row_candidate','text':'3 | 카메라 | 식 | 2'}
    full = {'doc_index':0,'start':100,'end':130,'text':'드론과 카메라는 다른 규격을 선택할 수 있다.'}
    result = other_subject_scope(full,[owner],[target])
    assert result['issue'] == 'permission_mentions_current_named_target'
    assert result['target_names'] == ['드론']


def test_wrapped_global_scope_is_kept_when_assessing_another_subject():
    _, condition = reviewed('본 계약의 전체 납품대상은\n다른 CPU 아키텍처를 선택할 수 있다.')
    assert condition['status'] == 'unknown'
    assert any(p.get('source_reading_extent',{}).get('line_count') == 2
        for p in condition['permission_interpretations'])
