"""Original purchase-table reading ranges; never reconstructed PDF cells.

A detected header and following text are structural candidates, not proof that
the purchase list is complete or that adjacent values belong to the same row.
"""
from __future__ import annotations

import re

from .retrieval import _source_units


_NAMES = {'품명', '품목', '물품명', '품목명', '제품명', '명칭', '분류명'}
_ATTRS = {'규격', '사양', '단위', '수량', '단가', '금액', '비고', '규격및단위',
          '납품조건및규격', '납품기한', '납품장소', '사용골재의최대치수'}
_OTHER = {'번호', '순번', '연번', '구분', '회사명', '제조사', '유효성분'}
_CLOSE = re.compile(r'^(?:(?:\d+(?:[.-]\d+)*[.)]|[가-하][.)]|[ⅠⅡⅢⅣⅤⅥ]+[.)]?|'
    r'[①-⑳])|(?:붙임|별첨|별지)\d|※공통)')
_SECTION = re.compile(r'납품|계약|하자|공통|일반|기타|참가|제출|품질|필요조건|적용|특징|사양|용도|운송|배송|준수')
_ATTACHMENT = re.compile(r'^(?:붙임|별첨|별지)(?:제)?\d+(?:호)?[.：:]?$')


def _cell(text):
    return re.sub(r'\s+', '', re.sub(r'\([^)]*\)', '', text)).strip('：:')


def purchase_tables(text):
    """Find pipe or vertical header sequences without inventing missing rows."""
    units = _source_units(text)
    headers = []
    i = 0
    while i < len(units):
        line = text[slice(*units[i])]
        if '|' in line:
            cells = {_cell(c) for c in line.split('|') if c.strip()}
            if cells & _NAMES and cells & _ATTRS:
                headers.append((i, i, 'pipe'))
            i += 1
            continue
        j, cells = i, set()
        while j < len(units):
            value = _cell(text[slice(*units[j])])
            if value not in _NAMES | _ATTRS | _OTHER:
                break
            cells.add(value)
            j += 1
        if len(cells) >= 3 and cells & _NAMES and cells & _ATTRS:
            headers.append((i, j-1, 'vertical'))
            i = j
        else:
            i += 1
    tables = []
    for n, (first, last, layout) in enumerate(headers):
        stop, boundary = len(units), 'document_end'
        next_header = headers[n+1][0] if n+1 < len(headers) else len(units)
        for j in range(last+1, len(units)):
            line = text[slice(*units[j])]
            value = re.sub(r'\s+', '', line)
            if j == next_header:
                stop, boundary = j, 'next_table_header'
                break
            if layout == 'pipe' and '|' not in line:
                stop, boundary = j, 'end_of_pipe_lines'
                break
            if _ATTACHMENT.fullmatch(value):
                stop, boundary = j, 'following_attachment'
                break
            if len(value) <= 100 and _CLOSE.match(value) and _SECTION.search(value):
                stop, boundary = j, 'following_section_candidate'
                break
        if stop <= last+1:
            continue  # Empty form; only a header was supplied.
        lo, hi = units[first][0], units[stop-1][1]
        parent = None
        if first:
            a, b = units[first-1]
            value = re.sub(r'\s+', '', text[a:b])
            if len(value) <= 120 and re.search(r'내역|목록|규격|사양|품목|구입|구매', value):
                parent = [a, b]
        tables.append({'start': lo, 'end': hi, 'text': text[lo:hi],
            'header_start': lo, 'header_end': units[last][1], 'parent_range': parent,
            'layout': layout, 'end_basis': boundary,
            'cells_reconstructed': False, 'whole_purchase_certified': False})
    return tables
