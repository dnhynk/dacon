"""Out-of-service-list codes remain certificate facts, never purchase identity."""
import copy

import pytest

from submission.pps.catalog_scope import review
from submission.pps.retrieval import Span
from tests.test_catalog_scope_contract import setup, response


def fixture(code='3912180101', name='빌딩자동제어장치'):
    rec, packet, obj, knowledge = setup('운영현황 조사 및 분석 용역')
    clause = f'직접생산확인증명서[세부품명: {name}, 세부품명번호: {code}]를 소지한 업체'
    start = len(rec['docs'][0]['text']) + 1
    rec['docs'][0]['text'] += '\n' + clause
    packet['spans'].append(Span(0, '공고문', start, start + len(clause), clause))
    obj['relationships'] = [{'code': code, 'role': 'certificate_only', 'source_units': [2]}]
    return rec, packet, obj, knowledge


@pytest.mark.parametrize('code,name', [('3912180101', '빌딩자동제어장치'),
                                      ('6010989901', '실물모형및전시물')])
def test_exact_original_goods_certificate_does_not_invalidate_service_scope(code, name):
    rec, packet, obj, knowledge = fixture(code, name)
    before = copy.deepcopy(obj)
    row, log = review(rec, response(obj), packet, knowledge)
    assert row is not None and log['product']['status'] == 'general'
    assert log['verified_nonservice_certificate_links'][0]['code'] == code
    assert log['verified_nonservice_certificate_links'][0]['purchase_identity_certified'] is False
    assert log['model_scope'] == before


@pytest.mark.parametrize('variant', ['invented_code', 'wrong_name', 'wrong_code', 'no_certificate',
                                    'whole', 'component', 'uncertain', 'duplicate', 'disjoint_units'])
def test_unverified_or_identity_bearing_goods_links_remain_unresolved(variant):
    rec, packet, obj, knowledge = fixture()
    if variant == 'invented_code':
        rec, packet, obj, knowledge = fixture('9999999999', '가상물품')
    elif variant == 'wrong_name':
        rec, packet, obj, knowledge = fixture(name='실물모형및전시물')
    elif variant == 'wrong_code':
        obj['relationships'][0]['code'] = '6010989901'
    elif variant == 'no_certificate':
        s = packet['spans'][1]
        text = s.text.replace('직접생산확인증명서', '참고사항')
        rec['docs'][0]['text'] = rec['docs'][0]['text'][:s.start] + text
        packet['spans'][1] = Span(0, '공고문', s.start, s.start + len(text), text)
    elif variant in {'whole', 'component', 'uncertain'}:
        obj['relationships'][0]['role'] = variant
    elif variant == 'duplicate':
        obj['relationships'].append(copy.deepcopy(obj['relationships'][0]))
    else:
        obj['relationships'][0]['source_units'] = [1, 2]
    row, log = review(rec, response(obj), packet, knowledge)
    assert row is None
    assert 'verified_nonservice_certificate_links' not in log
    assert log['gate'] == ('strong_existing_purchase_scope_preserved' if variant == 'no_certificate'
                           else 'unrecognized_or_conflicting_catalog_links')


def test_verified_certificate_does_not_resolve_unknown_purchase_or_missing_task():
    rec, packet, obj, knowledge = fixture()
    obj['catalog_relation'] = 'unknown'
    row, log = review(rec, response(obj), packet, knowledge)
    assert row is None and log['gate'] == 'whole_category_not_resolved'
    obj['whole_task_units'] = [2]
    row, log = review(rec, response(obj), packet, knowledge)
    assert row is None and log['gate'] == 'no_original_whole_task_anchor'
