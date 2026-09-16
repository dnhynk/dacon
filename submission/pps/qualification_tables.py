"""Literal certificate checklist columns; no restored rows or bidder facts.

The table can establish a submission observation, never actual possession.
Unfilled, conditional and malformed rows retain their unresolved scope.
"""
from __future__ import annotations

import re

_HEADERS = {
    '서류명':'name', '제출서류':'name', '제출서류명':'name', '구비서류':'name',
    '구비서류명':'name', '자격서류':'name', '필수':'required', '필수제출':'required',
    '해당시':'conditional', '해당시제출':'conditional', '조건부':'conditional',
    '비고':'note', '제출대상':'target', '적용대상':'target', '번호':'index',
    '연번':'index', '순번':'index',
}
_YES = {'○', '●', '◯', 'o', '✓', '✔', '√', '필수', '제출', '필수제출'}
_NO = {'x', '×', '-', '미제출', '불필요', '해당없음', '필수아님', '비대상'}
_BARRIER = re.compile(r'예시|작성예|참고용|가정|삭제|철회|필수아님|필수가아님|'
    r'계약조건|계약체결|낙찰후|계약후|계약이후|납품후|선정후|선정이후|평가기준|평가항목')
_CONDITIONAL = re.compile(r'해당시|경우|조건부|한하여|한정|구성원|하도급|협력업체|제조사')


def compact(text):
    return re.sub(r'\s+', '', text).lower()


def certificate_header(text, lo=0, hi=None):
    hi = len(text) if hi is None else hi
    if '|' not in text[lo:hi]:
        return None
    from .table_structure import pipe_cells, pipe_separators
    if not pipe_separators(text, lo, hi):
        return None
    cells = pipe_cells(text, lo, hi)
    roles = [_HEADERS.get(compact(c['text']), 'unknown') for c in cells]
    if 'name' not in roles or not {'required', 'target'}.intersection(roles):
        return None
    # Duplicate/unknown columns still identify a potentially mandatory table,
    # but cannot provide a selected, conveniently aligned interpretation.
    valid = ('unknown' not in roles and len(roles) == len(set(roles)))
    return {'start':lo, 'end':hi, 'text':text[lo:hi], 'cells':cells,
        'roles':roles, 'unique_columns':valid,
        'kind':'checklist' if 'required' in roles else 'qualification_scope',
        'submission_caption':any(roles[i] == 'name' and re.search(r'제출|구비', compact(c['text']))
                                 for i, c in enumerate(cells)),
        'fences':[text[lo:hi].lstrip().startswith('|'), text[lo:hi].rstrip().endswith('|')]}


def _mark(cell):
    value = compact(cell['text'])
    return 'yes' if value in _YES else 'no' if value in _NO else 'empty' if not value else 'unknown'


def certificate_rows(text):
    from .table_structure import pipe_cells, pipe_separators
    result, header, previous_end = {}, None, 0
    for line in re.finditer(r'[^\r\n]+', text):
        candidate = certificate_header(text, line.start(), line.end())
        if candidate:
            header, previous_end = candidate, line.end()
            continue
        if (not header or not pipe_separators(text, line.start(), line.end())
                or text[previous_end:line.start()].count('\n') > 1):
            header = None
            continue
        previous_end = line.end()
        cells = pipe_cells(text, line.start(), line.end(), fences=header['fences'])
        if cells and all(re.fullmatch(r':?-{3,}:?', c['text']) for c in cells):
            continue  # Markdown separator, never an applicant observation.
        valid = header['unique_columns'] and len(cells) == len(header['roles'])
        mapping = dict(zip(header['roles'], cells)) if valid else {}
        state = 'column_alignment_unresolved'
        if valid and header['kind'] == 'qualification_scope':
            target = compact(mapping['target']['text'])
            state = ('applicant_scope' if target in {'전체입찰자','모든입찰자','입찰참가자',
                '입찰참가업체','참가업체'} else 'target_scope_unresolved')
        if valid and header['kind'] == 'checklist':
            required = _mark(mapping['required'])
            conditional = _mark(mapping['conditional']) if 'conditional' in mapping else 'empty'
            if required == 'yes' and conditional in ('empty', 'no'):
                state = 'required_submission'
            elif conditional == 'yes' and required in ('empty', 'no'):
                state = 'conditional_submission'
            elif required == 'no' and conditional in ('empty', 'no'):
                state = 'explicitly_not_required'
            elif required == conditional == 'empty':
                state = 'unfilled_checklist'
            else:
                state = 'marks_unresolved'
        if valid:
            qualifiers = ''.join(compact(mapping[k]['text']) for k in ('target', 'note') if k in mapping)
            if _BARRIER.search(qualifiers):
                state = 'nonoperative_or_later_stage'
            elif _CONDITIONAL.search(qualifiers) and state in ('required_submission', 'applicant_scope'):
                state = 'conditional_submission'
        result[line.start()] = {'header':header, 'cells':cells, 'column_cells':mapping,
            'literal_column_alignment':valid, 'status':state,
            'bidder_possession_certified':False}
    return result


def submission_observation(record, entry):
    """Return a source-backed uncertainty, not a mandatory eligibility fact."""
    table = entry.get('certificate_table')
    if (not table or table['header']['kind'] != 'checklist'
            or entry['section_role'] == 'scoring'):
        return None
    ancestors = entry.get('heading_ancestors') or []
    if any(_BARRIER.search(compact(h['text'])) for h in ancestors):
        return None
    if table['status'] in ('explicitly_not_required', 'nonoperative_or_later_stage'):
        return None
    ev = entry['evidence']
    if _BARRIER.search(compact(table['column_cells'].get('name', ev)['text'])):
        return None
    def ref(cell):
        return {**cell, 'doc_index':ev['doc_index'], 'doc_id':ev['doc_id'],
            'document_role':ev['document_role']}
    header = table['header']
    return {'reason':'certificate_checklist_applicability_unresolved',
        'table_status':table['status'], 'governor':ref({k:header[k] for k in ('start','end','text')}),
        'evidence':ev, 'cells':[ref(c) for c in table['cells']],
        'literal_column_alignment':table['literal_column_alignment'],
        'bidder_possession_certified':False,
        'certificate_text':table['column_cells'].get('name', ev)['text']}
