"""Source-bounded task completion and compatible catalog roles."""
import copy

import pytest

from submission.pps.catalog_scope import review
from submission.pps.retrieval import Span
from submission.pps.source_units import unitize
from tests.test_catalog_scope_contract import setup, response


@pytest.mark.parametrize('label', ['용역명:', '용 역 명', '가. 용 역 명 :'])
def test_value_only_task_and_whole_link_complete_same_original_field(label):
    rec, packet, obj, knowledge = setup('초등학교 통학버스 임차')
    text = rec['docs'][0]['text'].replace('용역명:', label + '\n')
    rec['docs'][0]['text'] = text
    end = text.index('\n1.')
    packet['spans'] = unitize([Span(0, '공고문', 0, end, text[:end])])
    obj.update(whole_task_units=[2], catalog_relation='listed_category', relationships=[
        {'code': '7811189902', 'role': 'whole', 'source_units': [2]}])
    before = copy.deepcopy(obj)
    row, log = review(rec, response(obj), packet, knowledge)
    assert row is not None and log['gate'] == 'source_predicates_joined_to_fallible_scope'
    assert log['task_reference_completion'][0]['added_units'] == [1]
    assert log['catalog_link_reference_completion'][0]['completion'][0]['added_units'] == [1]
    assert log['model_scope'] == before


def paired_roles(certificate='직접생산확인증명서 통학운송서비스(7811189902) 소지'):
    rec, packet, obj, knowledge = setup('초등학교 통학버스 임차')
    start = len(rec['docs'][0]['text']) + 1
    rec['docs'][0]['text'] += '\n' + certificate
    packet['spans'].append(Span(0, '공고문', start, start+len(certificate), certificate))
    obj.update(catalog_relation='listed_category', relationships=[
        {'code': '7811189902', 'role': 'whole', 'source_units': [1]},
        {'code': '7811189902', 'role': 'certificate_only', 'source_units': [2]}])
    return rec, packet, obj, knowledge


def test_duplicate_role_relaxation_remains_withheld_until_qualification_is_safe():
    rec, packet, obj, knowledge = paired_roles()
    row, log = review(rec, response(obj), packet, knowledge)
    assert row is None and log['gate'] == 'unrecognized_or_conflicting_catalog_links'


@pytest.mark.parametrize('variant', ['component', 'same_units', 'no_certificate', 'wrong_code'])
def test_true_conflicts_and_unverified_certificate_still_abstain(variant):
    certificate = ('관련 서류를 제출한다' if variant == 'no_certificate' else
                   '직접생산확인증명서 기타도로여객운송서비스(7811189904)' if variant == 'wrong_code' else
                   '직접생산확인증명서 통학운송서비스(7811189902) 소지')
    rec, packet, obj, knowledge = paired_roles(certificate)
    if variant == 'component': obj['relationships'][1]['role'] = 'component'
    if variant == 'same_units': obj['relationships'][1]['source_units'] = [1]
    row, log = review(rec, response(obj), packet, knowledge)
    assert row is None and log['gate'] == 'unrecognized_or_conflicting_catalog_links'


def test_certificate_whole_link_cannot_borrow_separate_task_field():
    rec, packet, obj, knowledge = paired_roles()
    obj['relationships'] = [{'code': '7811189902', 'role': 'whole', 'source_units': [2]}]
    row, log = review(rec, response(obj), packet, knowledge)
    assert row is None and log['gate'] == 'whole_catalog_link_without_task_anchor'
