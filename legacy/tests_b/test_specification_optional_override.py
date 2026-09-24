"""Specialist uncertainty must not silently erase an independent judgment."""
import pytest

from submission.b4_entry import B4Pipeline, assemble
from submission.pps import specification_candidate_review as review
from tests.test_specification_candidate_review import fixture, response


@pytest.mark.parametrize('case', ['unknown','unbound_permission','quantity','brand_allowed','exception'])
def test_negative_optional_override_needs_consistent_candidate_relations(case):
    rec, units, plan, payload, packet = fixture('品目\n모델명: Atlas R7\n동등 모델 허용\n법정 예외 적용 주장')
    answer = payload[review.NAME]['C1']
    if case != 'unknown':
        answer.update(specificity='named',role='new_whole_product',requirement='mandatory',scope_sources=[2])
    if case in {'quantity','brand_allowed'}:
        answer.update(permission_sources=[3],permission_scope='this_candidate',permission_effect='allowed',
                      permission_attribute='quantity' if case == 'quantity' else 'brand_or_model')
    if case == 'exception':
        answer['exception_sources'] = [4]
    packet.update(batch='S9',request_key='review',record_id=rec['id'])
    original = response(payload)
    row, details = B4Pipeline.__new__(B4Pipeline).consume(rec, packet, original)
    deferred = case in {'unknown','unbound_permission','quantity','exception'}
    assert row == ({} if deferred else {'v9':0,'e9':''})
    assert details[-1]['deferred'] is deferred
    assert details[-1]['model_judgment_preserved']['v'] == 0
    base = {'family':'A','record_id':rec['id'],'request_key':'base'}
    b3, b4 = assemble([rec],[base,packet],{'base':{'v9':1,'e9':'모델명: Atlas R7'},'review':row})
    assert b4[rec['id']]['v9'] == (1 if deferred else 0)
    assert original == response(payload)


def test_empty_discovery_still_uses_fallible_model_judgment():
    rec, units, plan, payload, packet = fixture('기존 설비의 정기 점검을 수행한다.')
    assert not plan['candidates']
    packet['batch'] = 'S9'
    row, details = B4Pipeline.__new__(B4Pipeline).consume(rec,packet,response(payload))
    assert row['v9'] == 0 and not details[-1]['deferred']
    assert details[0]['facts']['unlisted_source_candidates_possible']


@pytest.mark.parametrize('permission,accepted', [
    ('규격서 상의 기준과 동등하거나 그 이상인 장비를 납품하여야 한다.', True),
    ('상기 사항과 동등 이상의 제품으로 납품할 수 있다.', False),
])
def test_same_form_explicit_whole_specification_permission_can_unblock_an_old_response(
        permission, accepted):
    text = ('규 격 서\n구 분 | 품 명 | 단 위 | 수 량\n1 | 측량장비 | 식 | 1\n'
            'Ⅰ. 사양\n1. CPU: Atlas R7\nⅣ. 기타사항\n' + permission)
    rec, units, plan, payload, packet = fixture(text)
    answer = payload[review.NAME]['C1']
    answer.update(specificity='named', role='new_component', requirement='mandatory',
                  scope_sources=plan['candidates'][0]['source_units'], permission_sources=[],
                  permission_scope='not_observed', permission_attribute='unknown',
                  permission_effect='unknown', exception_sources=[])
    payload.update(unresolved='', judgment={'reason':'후보와 전체 규격 허용을 검토함','v':0,'e':0})
    packet.update(batch='S9', request_key='review', record_id=rec['id'])
    row, details = B4Pipeline.__new__(B4Pipeline).consume(rec, packet, response(payload))
    assert bool(row) is accepted
    assert details[-1]['deferred'] is (not accepted)
    assert bool(details[-1]['deterministic_whole_specification_permissions']) is accepted


def test_an_explicit_permission_in_another_repeated_form_does_not_unblock_the_candidate():
    text = ('규 격 서\n구 분 | 품 명 | 단 위 | 수 량\n1 | 측량장비 | 식 | 1\n'
            'Ⅰ. 사양\n1. CPU: Atlas R7\n'
            '규 격 서\n구 분 | 품 명 | 단 위 | 수 량\n2 | 다른장비 | 식 | 1\n'
            'Ⅳ. 기타사항\n규격서 상의 기준과 동등하거나 그 이상인 장비를 납품하여야 한다.')
    rec, units, plan, payload, packet = fixture(text)
    answer = payload[review.NAME]['C1']
    answer.update(specificity='named', role='new_component', requirement='mandatory',
                  scope_sources=plan['candidates'][0]['source_units'], permission_sources=[],
                  permission_scope='not_observed', permission_attribute='unknown',
                  permission_effect='unknown', exception_sources=[])
    payload.update(unresolved='', judgment={'reason':'서로 다른 규격서 블록을 검토함','v':0,'e':0})
    packet.update(batch='S9', request_key='review', record_id=rec['id'])
    row, details = B4Pipeline.__new__(B4Pipeline).consume(rec, packet, response(payload))
    assert row == {} and details[-1]['deferred']
    assert details[-1]['deterministic_whole_specification_permissions'] == {}
