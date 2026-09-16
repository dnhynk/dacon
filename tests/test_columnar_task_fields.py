"""Flattened header runs retain a bounded, source-addressed task relation."""
from submission.pps.catalog_scope import (
    complete_value_selected_task_fields, whole_task_witnesses,
)
from submission.pps.retrieval import Span
from submission.pps.source_units import unitize
from submission.pps.task_scope import candidate_fields, columnar_task_fields
from submission.pps.task_context import field_groups, render_groups


def record(text):
    return {'id': 'columnar-task', 'meta': {},
        'docs': [{'doc_id': 'D0', 'type': '공고문', 'text': text}],
        'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


def test_header_run_and_first_value_form_a_bounded_task_hypothesis():
    text = ('1. 입찰에 부치는 사항\n\n용 역 명\n\n용 역 개 요\n\n용 역 기 간\n\n'
            '계 약 금 액 (원)\n\nAI 비전수립 연구 용역\n\n제안요청서 및\n\n과업지시서\n\n참조\n\n7개월')
    rec = record(text)
    fields = columnar_task_fields(rec)
    assert len(fields) == 1
    field = fields[0]
    assert field['header_count'] == 4
    assert field['text'].startswith('용 역 명')
    assert field['text'].endswith('AI 비전수립 연구 용역')
    candidates = candidate_fields(rec)
    assert any(candidate['candidate_role'] == 'columnar_task_field_certified'
               for candidate in candidates)
    assert all('role' not in candidate for candidate in candidates)

    spans = unitize([Span(0, '공고문', 0, len(text), text)])
    value_unit = next(i for i, span in enumerate(spans, 1)
                      if span.text.strip() == 'AI 비전수립 연구 용역')
    refs, additions = complete_value_selected_task_fields(rec, spans, [value_unit])
    assert additions and additions[0]['added_units']
    witnesses = whole_task_witnesses(rec, spans, refs)
    witness = next(w for w in witnesses
                   if w['candidate_role'] == 'columnar_task_field_certified')
    assert witness['text'] == field['text']
    groups = field_groups(rec, spans)
    group = next(g for g in groups
                 if g['candidate_role'] == 'columnar_task_field_certified')
    assert group['text'] == field['text']
    assert not group['whole_contract_identity_certified']
    assert '절에 귀속된 평면화 표의 과업명 단서' in render_groups(groups)


def test_unanchored_header_run_remains_a_reading_hypothesis_only():
    text = ('용 역 명\n용 역 개 요\n용 역 기 간\n계 약 금 액 (원)\n'
            'AI 비전수립 연구 용역\n제안요청서 참조\n7개월\n1억원')
    rec = record(text)
    fields = columnar_task_fields(rec)
    assert len(fields) == 1
    assert fields[0]['role'] == 'columnar_task_field_hypothesis'
    spans = unitize([Span(0, '공고문', 0, len(text), text)])
    assert whole_task_witnesses(rec, spans, range(1, len(spans) + 1)) == []
    groups = field_groups(rec, spans)
    assert groups[0]['candidate_role'] == 'columnar_task_field_hypothesis'
    assert '평면화 표의 과업명 후보' in render_groups(groups)


def test_header_run_does_not_assign_a_document_pointer_as_the_task():
    text = '용 역 명\n\n용 역 개 요\n\n제안요청서 참조\n\nAI 연구 용역'
    assert columnar_task_fields(record(text)) == []
