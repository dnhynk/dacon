"""Task-focused reading candidates, with literal complete-field source groups.

The existing scope consumer still decides whether a chosen relationship has a
usable witness. These helpers change offered reading context, never a model
answer, catalog identity, conditional property or absence decision.
"""
from __future__ import annotations

from types import SimpleNamespace

from .catalog_scope import ITEMS, QUERIES, covered_task_field_candidates
from .notice_search import NoticeSearch, factual_queries, merge_ranges
from .purchase_context_search import choose_reserve
from .source_units import validate
from .task_scope import candidate_fields


def source_fields(record):
    """Inventory literal task candidates across provided source, without labels."""
    return sorted(candidate_fields(record), key=lambda f: (f['doc_index'], f['start'], f['end']))


def field_groups(record, spans):
    """Complete original fields visible through the offered bounded S units.

    No field is completed from unoffered text. Multiple documents, conflicting
    values, repeated occurrences and continuation conditions remain separate.
    A group is selectable only if the whole field fits the existing 12-ref
    response contract; oversized groups remain diagnostic, never truncated.
    """
    validate(spans, record)
    # These are reading groups, not deterministic task anchors. In particular,
    # keep flattened-table hypotheses visible without letting their uncertain
    # column ownership pass ``whole_task_witnesses``.
    fields = covered_task_field_candidates(record, spans, range(1, len(spans) + 1))
    return [dict(key='T' + str(i), **field,
                 selectable=len(field['selected_units']) <= 12,
                 whole_contract_identity_certified=False)
            for i, field in enumerate(sorted(fields,
                key=lambda f: (f['doc_index'], f['start'], f['end'])), 1)]


def render_groups(groups):
    """Address hints only: don't duplicate source or turn a candidate into fact."""
    header = ('\n[과업 단서의 원문 묶음 후보]\n'
        '아래는 현재 입력에서 해당 단서의 머리글·값·이어진 조건까지 함께 볼 수 있는 원문 주소다. '
        '전체 과업이나 고시 동일성의 정답 목록이 아니다. 다른 과업·혼합대상·예외도 읽는다. '
        '해당 단서를 근거로 선택할 때는 표시된 S번호를 함께 선택한다. '
        'T번호는 출력하지 않는다. 다른 원문 S번호를 선택할 수도 있다.\n')
    role_names = {'title_or_scope_field': '과업명·내용 단서',
                  'intro_title_candidate': '도입부 과업 단서',
                  'explicit_whole_contract_body': '전체 계약 서술',
                  'explicit_task_extent_field': '과업개요·물량 필드',
                  'columnar_task_field_certified': '절에 귀속된 평면화 표의 과업명 단서',
                  'columnar_task_field_hypothesis': '평면화 표의 과업명 후보'}
    lines = [f"{g['key']}: {role_names[g['candidate_role']]} 문서{g['doc_index']} 원문{g['start']}:{g['end']} -> "
             + ','.join('S' + str(n) for n in g['selected_units'])
             for g in groups if g['selectable']]
    return header + ('\n'.join(lines) if lines else
        '이 방식으로 묶인 필드는 없다. 이것은 실제 과업이나 조건의 부재 확인이 아니다.')


class TaskContextSearch(NoticeSearch):
    """Shared lexical/dense context expansion; all raw words count in budget."""
    def __init__(self, record, tokenizer, encoder=None):
        self.fields = source_fields(record)
        super().__init__(record, tokenizer, encoder)

    def _context(self, span):
        ranges = list(super()._context(span))
        for field in self.fields:
            if (field['doc_index'] == span.doc_index
                    and span.start < field['end'] and field['start'] < span.end):
                ranges.append((span.doc_index, field['start'], field['end']))
        return merge_ranges(ranges, self.rec['docs'])

    def select(self, token_budget, *, method='lexical', reserve_fields=True):
        if type(reserve_fields) is not bool:
            raise ValueError('Task-field reservation must be explicitly boolean')
        # Reserve complete source contexts, not merely names stripped of their
        # label, table header, qualification or trailing exception.
        observations = [{'context': self._context(SimpleNamespace(**field))}
                        for field in self.fields]
        required = choose_reserve(self, observations, token_budget // 2) if reserve_fields else ()
        selected = self.search(ITEMS, token_budget=token_budget, method=method,
            query_groups={'whole_task': QUERIES[:2],
                          'task_qualification_relation': (QUERIES[2],),
                          'exclusions_and_eligibility': (QUERIES[3], *factual_queries(ITEMS))},
            required_ranges=required, selection_policy='evidence_cover')
        selected['diagnostics']['task_context'] = {
            'original_field_candidates': len(self.fields),
            'reserve_fields': reserve_fields, 'reserve_token_cap': token_budget // 2,
            'reserved_ranges': list(required), 'field_identity_certified': False,
            'absence_verified': False}
        return selected
