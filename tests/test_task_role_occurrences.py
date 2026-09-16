"""A source role requires a real task and the correct original occurrence."""
import copy

import pytest

from submission.pps.catalog_scope import whole_task_witnesses
from submission.pps.catalog_condition_review import _local_task_witnesses
from submission.pps.retrieval import Span
from submission.pps.source_units import unitize
from tests.test_notice_search import record


@pytest.mark.parametrize('text', [
    '품 명 | 수량 | 납품조건 및 규격 | 납품기한 | 납품장소',
    '품명 | 인도조건 | 제조국 | 규격 | 수량',
    '- 장비는 국내 수리 및 유지보수가 가능하여야 한다.',
    '납품 장비는 다른 시스템과 연동하여 운용 가능해야 합니다.',
    '다. 적격심사대상 물품 구매입니다.',
    '- 조달청 물품구매적격심사 세부기준(제4조 제1항 제3호)',
])
def test_table_columns_equipment_properties_and_procedure_are_not_task_names(text):
    rec = record(text)
    assert whole_task_witnesses(rec, [Span(0, '공고문', 0, len(text), text)], [1]) == []


@pytest.mark.parametrize('text', [
    '품명 | 연동 가능한 연구장비 유지보수 서비스',
    '사업명: 물품구매 적격심사 세부기준 관리 시스템 개발',
    '상수도 시설 운영관리 위탁 용역 입찰공고',
    '과업내용: 장비를 수리하고 다른 시스템과 연동하도록 개발하여야 한다.',
])
def test_explicit_task_fields_and_actual_titles_survive_the_role_filter(text):
    rec = record(text)
    assert whole_task_witnesses(rec, [Span(0, '공고문', 0, len(text), text)], [1])


def test_same_task_text_in_another_document_is_a_distinct_source_witness():
    text = '품명: 컴퓨터서버\nCPU 아키텍처: ARM'
    rec = record(text)
    rec['docs'].append({'doc_id': 'second', 'type': '규격서', 'text': text})
    spans = unitize([Span(i, d['type'], 0, len(text), text) for i, d in enumerate(rec['docs'])])
    original = copy.deepcopy(rec)
    refs = [i for i, s in enumerate(spans, 1) if s.doc_index == 1 and '품명' in s.text]
    witnesses = whole_task_witnesses(rec, spans, refs)
    assert len(witnesses) == 1 and witnesses[0]['doc_index'] == 1
    ev = {'doc_index': 1, 'start': text.index('CPU'), 'end': len(text), 'text': text[text.index('CPU'):]}
    assert _local_task_witnesses(rec, spans, {'scope_units': refs}, {'evidence': ev})
    assert rec == original


def test_repeated_name_after_an_intervening_item_binds_at_its_own_location():
    text = ('품명: 컴퓨터서버\nCPU 아키텍처: ARM\n'
            '품명: 데이터수집장치\nCPU 아키텍처: x86\n'
            '품명: 컴퓨터서버\nCPU 아키텍처: ARM')
    rec = record(text)
    spans = unitize([Span(0, '공고문', 0, len(text), text)])
    refs = [i for i, s in enumerate(spans, 1) if s.start >= text.rfind('품명:')]
    ev = {'doc_index': 0, 'start': text.rfind('CPU'), 'end': len(text), 'text': 'CPU 아키텍처: ARM'}
    witnesses = _local_task_witnesses(rec, spans, {'scope_units': refs}, {'evidence': ev})
    assert len(witnesses) == 1 and witnesses[0]['start'] == text.rfind('품명:')
    assert not _local_task_witnesses(rec, spans, {'scope_units': [1]}, {'evidence': ev})
