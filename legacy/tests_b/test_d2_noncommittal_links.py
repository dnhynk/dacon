"""Non-committal relations whose own citation cannot concern their code."""
import copy

import pytest

from submission.pps.catalog_scope import review
from submission.pps.retrieval import Span
from tests.test_catalog_scope_contract import setup, response


def with_certificate(clause='직접생산확인증명서[세부품명: 통학운송서비스(7811189902)]를 소지한 업체'):
    rec, packet, obj, knowledge = setup('슬레이트 운반 및 처리 용역')
    start = len(rec['docs'][0]['text']) + 1
    rec['docs'][0]['text'] += '\n' + clause
    packet['spans'].append(Span(0, '공고문', start, start + len(clause), clause))
    return rec, packet, obj, knowledge


@pytest.mark.parametrize('role', ['uncertain', 'certificate_only'])
def test_uncited_noncommittal_link_is_set_aside_under_anchored_outside_task(role):
    rec, packet, obj, knowledge = setup('슬레이트 운반 및 처리 용역')
    obj['relationships'] = [{'code': '7215401001', 'role': role, 'source_units': []},
                            {'code': '8214150201', 'role': role, 'source_units': []}]
    before = copy.deepcopy(obj)
    row, log = review(rec, response(obj), packet, knowledge)
    assert row is not None and log['product']['status'] == 'general'
    assert [e['reason'] for e in log['unsupported_noncommittal_catalog_links']] == [
        'no_cited_source_unit', 'no_cited_source_unit']
    assert log['model_scope'] == before


def test_doubt_cited_to_another_codes_certificate_is_set_aside():
    rec, packet, obj, knowledge = with_certificate()
    obj['relationships'] = [{'code': '7215409902', 'role': 'uncertain', 'source_units': [2]}]
    row, log = review(rec, response(obj), packet, knowledge)
    assert row is not None and log['product']['status'] == 'general'
    entry = log['unsupported_noncommittal_catalog_links'][0]
    assert entry['reason'] == 'cited_certificate_clause_names_only_other_codes'
    assert entry['cited_codes'] == ['7811189902']


@pytest.mark.parametrize('variant', ['own_code', 'own_name', 'task_unit', 'task_and_certificate'])
def test_doubt_that_can_concern_its_code_still_stops(variant):
    clause = {'own_code': '직접생산확인증명서[세부품명: 기타(7215409902)]를 소지한 업체',
              'own_name': '직접생산확인증명서[세부품명: 전시홍보관설치 및 디자인서비스(7811189902)]를 소지한 업체',
              }.get(variant, '직접생산확인증명서[세부품명: 통학운송서비스(7811189902)]를 소지한 업체')
    rec, packet, obj, knowledge = with_certificate(clause)
    units = {'task_unit': [1], 'task_and_certificate': [1, 2]}.get(variant, [2])
    obj['relationships'] = [{'code': '7215409902', 'role': 'uncertain', 'source_units': units}]
    row, log = review(rec, response(obj), packet, knowledge)
    assert row is None and log['gate'] == 'uncertain_identity_link'
    assert 'unsupported_noncommittal_catalog_links' not in log


def test_uncited_identity_bearing_link_still_stops():
    rec, packet, obj, knowledge = setup('초등학교 통학버스 임차')
    obj.update(catalog_relation='listed_category', relationships=[
        {'code': '7811189902', 'role': 'whole', 'source_units': []}])
    row, log = review(rec, response(obj), packet, knowledge)
    assert row is None and log['gate'] == 'catalog_link_without_source'
