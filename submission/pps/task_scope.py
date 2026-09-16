"""Literal task fields for the scope consumer, independent of search ranking.

Discovery deliberately retains useful administrative context. These checks say
which complete original fields may witness a purchase task. They neither infer
a catalog identity nor reconstruct columns or an unobserved continuation.
"""
from __future__ import annotations

import re

from .products import (
    _FIELD_START, _OTHER_FIELDS, _SCOPE_NAMES, _TABLE_COLUMNS, compact,
    document_reading_instruction, has_scope_content, scope_table_header,
)


_EXTENT_NAMES = ('용역개요', '용역의개요', '용역량', '과업개요', '과업의개요')
_EXTENT_FIELD = re.compile(
    r'^[ \t○◯❍□■ㆍ·ㅇ-]*(?:(?:\d+(?:\.\d+)*[.)]|[가-하][.)])[ \t]*)?'
    r'(?P<label>' + '|'.join(r'[ \t]*'.join(name) for name in _EXTENT_NAMES) + r')'
    r'[ \t]*[:：][ \t]*(?P<value>[^\r\n]+)$')
_DOCUMENT_TITLES = frozenset((
    '과업내용서', '과업지시서', '과업설명서', '제안요청서', '설계서', '설계설명서',
    '규격서', '구매규격서', '시방서', '용역계약특수조건',
))
_HEADER_CELLS = _TABLE_COLUMNS | frozenset((
    '위치', '용역위치', '사업내용', '설계금액', '기초금액', '추정금액', '추정가격',
    '예정금액', '용역기간', '용역기한', '용역개요', '용역량', '용역내역', '계약방법',
    '사업개요', '과업개요', '구분', '기간', '참여기간', '지정기관', '지정일자',
    '제한기간', '처분사유', '지체일수', '계약액',
))
_TASK_FIELD = re.compile('(?:' + '|'.join(r'[ \t]*'.join(name)
    for name in (*_SCOPE_NAMES, *_EXTENT_NAMES)) + r')'
    r'(?:[ \t]*[:：|][ \t]*|[ \t]+|$)')
_EMPTY_SUBHEADINGS = frozenset((
    '추진배경', '추진방향', '과업개요', '사업개요', '용역개요',
    '용역배경', '과업배경', '사업배경', '과업내용', '용역내용',
    '관련', '및범위', '등기술관련사항', '및평가관련',
))


def _header_cell(text):
    value = compact(re.sub(r'\([^)]*\)', '', text)).strip(':：')
    if value in _HEADER_CELLS:
        return True
    # A form name followed by its empty purchase-name cell is still a header.
    return bool(re.fullmatch(r'(?:안전보건관리준수서약서|청렴계약서약서)사업명', value))


def unusable_scope_role(text):
    """Reject a document name/empty field or a row of column headings."""
    visible = re.sub(r'\[[^\]]*\]', '', text)
    letters = re.sub(r'[^가-힣a-zA-Z]', '', visible)
    if letters in _DOCUMENT_TITLES:
        return 'task_document_title'
    if re.fullmatch(r'(?:조달물자|물자|물품|용역|구매|전자|입찰|공고서?|대행|긴급|정정|변경|'
                    r'수의|견적|제출|안내|일반|제한|경쟁|계약|재공고)+', letters):
        return 'generic_procurement_document_title'
    first = text.splitlines()[0] if text else ''
    field = _FIELD_START.match(first) or _TASK_FIELD.search(first)
    if field:
        value = re.sub(r'\[[^\]]*\]', '', text[field.end():])
        value = re.sub(r'\d{2,4}\s*(?:학년도|년도|년)', '', value)
        words = re.sub(r'[^가-힣a-zA-Z]', '', value)
        if not words or words in _DOCUMENT_TITLES or words == '서':
            return 'task_field_without_named_task'
        if words in _EMPTY_SUBHEADINGS or words in _HEADER_CELLS:
            return 'task_field_contains_only_another_heading'
        headings = '|'.join(re.escape(cell) for cell in sorted(_HEADER_CELLS, key=len, reverse=True))
        if re.fullmatch(r'(?:' + headings + r'){2,}', words):
            return 'flattened_task_column_headings'
        # An empty purchase-name cell followed by the signer's declaration is
        # not a label/value pair. Keep an actual contract to author such forms.
        if (re.match(r'\s*(?:우리는|저희는|본인은|당사는)', value)
                and re.search(r'(?:위의|위|본|해당)\s*입찰', value)
                and re.search(r'서약|승낙|합의각서|동의합니다', value)):
            return 'bidder_form_declaration_in_empty_task_field'
    elif (re.search(r'입찰\s*금액|투찰\s*금액|입찰\s*가격', text)
            and re.search(r'가격\s*제안서', text)
            and re.search(r'동일하여야|일치하여야|불일치하는|인정합니다', text)):
        return 'bid_price_consistency_instruction'
    cells = [cell for cell in first.split('|') if cell.strip()]
    if len(cells) >= 2 and all(_header_cell(cell) for cell in cells):
        return 'task_table_column_headings'
    return None


def _open_parenthesis(text):
    depth = 0
    for char in text:
        if char in '(（':
            depth += 1
        elif char in ')）':
            depth -= 1
            if depth < 0:
                return None
    return depth


def extent_fields(record):
    """Read explicit scope/quantity fields with their real label and full value.

    A quantity alone, following field, table row or document pointer supplies
    no task identity. An open parenthesis permits only a bounded literal line
    continuation, and the consumer still requires all its source units.
    """
    result = []
    for di, doc in enumerate(record['docs']):
        source = doc['text']
        for line in re.finditer(r'[^\r\n]+', source):
            field = _EXTENT_FIELD.fullmatch(line[0])
            if field is None:
                continue
            start, end = line.start(), line.end()
            for _ in range(5):
                content = source[start:end]
                balance = _open_parenthesis(content)
                if balance is None:
                    break
                needs_next = balance > 0 or bool(re.search(r'(?:및|또는|혹은)\s*$', content))
                if not needs_next:
                    value = content[field.start('value'):]
                    # Unit words and punctuation cannot be a purchased task.
                    named = re.sub(r'[\d,./%㎡㎥㎞\s]+(?:톤|ton|건|회|식|개소|개|종|점|명|개월|일)?', '', value)
                    if (has_scope_content(value) and re.search(r'[가-힣a-zA-Z]{2,}', named)
                            and not document_reading_instruction(value)
                            and not unusable_scope_role(content)
                            and not scope_table_header(value)):
                        result.append({'doc_index': di, 'start': start, 'end': end,
                            'text': content, 'role': 'explicit_task_extent_field'})
                    break
                continuation = re.match(r'[ \t]*\r?\n(?P<line>[^\r\n]+)', source[end:])
                if continuation is None:
                    break
                following = continuation['line']
                if (not following.strip() or _FIELD_START.match(following)
                        or _EXTENT_FIELD.match(following) or _OTHER_FIELDS.search(compact(following))
                        or scope_table_header(following)
                        or re.match(r'\s*(?:\d{1,3}[.)]|[가-하][.)]|[□■※])', following)):
                    break
                end += continuation.end()
                if end - start > 1200:
                    break
    return result


def columnar_task_fields(record):
    """Return bounded task fields from a flattened header-run/value table.

    Some PDF tables are emitted column-major: all headers first, then all cell
    values. Preserve the complete original range and expose only the first
    header/value relation when a task-name header starts at least two recognized
    headers and the immediate first value names an actual task. An immediately
    preceding numbered ``입찰에 부치는 사항`` heading certifies that the header
    run belongs to the notice's subject table. Without that independent anchor,
    retain the range as a reading hypothesis. No cell is synthesized and the
    source text is never reordered.
    """
    task_headers = frozenset(compact(name) for name in _SCOPE_NAMES)
    result = []
    for di, doc in enumerate(record['docs']):
        source = doc['text']
        cells = list(re.finditer(r'[^\r\n]+', source))
        index = 0
        while index < len(cells):
            first = cells[index]
            if compact(first[0]).strip(':：|') not in task_headers:
                index += 1
                continue
            end = index + 1
            while end < len(cells) and _header_cell(cells[end][0]):
                end += 1
            if end - index < 2 or end >= len(cells):
                index += 1
                continue
            value = cells[end]
            value_text = value[0]
            if (not has_scope_content(value_text)
                    or document_reading_instruction(value_text)
                    or _header_cell(value_text)
                    or unusable_scope_role(value_text)
                    or not re.search(r'(?:용역|사업|과업|구매|임차|납품|설치)',
                                     compact(value_text))):
                index += 1
                continue
            previous = compact(cells[index-1][0]) if index else ''
            certified = bool(re.fullmatch(
                r'(?:\d+(?:\.\d+)*[.)]?)?(?:입찰|견적)에부치는사항', previous))
            role = ('columnar_task_field_certified' if certified
                    else 'columnar_task_field_hypothesis')
            result.append({
                'doc_index': di, 'start': first.start(), 'end': value.end(),
                'text': source[first.start():value.end()],
                'document_role': doc['type'],
                'role': role,
                'header_start': first.start(), 'header_end': cells[end-1].end(),
                'value_start': value.start(), 'value_end': value.end(),
                'header_count': end-index,
                'source_modified': False,
                'layout_certainty': (
                    'section_bound_ordered_header_run_and_first_value'
                    if certified else 'ordered_header_run_and_first_value_hypothesis'),
            })
            index = end + 1
    return result


def candidate_fields(record):
    """Shared source-role check for discovery hints and the scope consumer.

    These are candidates for a model to read, not a certification that a short
    title or one task paragraph describes the entire contract. Preserve the
    discovery role so a reading hint cannot call every fragment a named field.
    """
    from .products import complete_scope_range, non_task_scope_role, scope_spans
    from .service_identity import whole_contract_scope
    raw = (scope_spans(record, 1000, 1_000_000, preserve_occurrences=True)
           + [dict(s, role='explicit_whole_contract_body') for s in whole_contract_scope(record)]
           + extent_fields(record))
    result, seen = [], set()
    for item in raw:
        item = complete_scope_range(record, item)
        if item is None:
            continue
        di, start, end = item['doc_index'], item['start'], item['end']
        text = record['docs'][di]['text'][start:end]
        if (non_task_scope_role(text) or unusable_scope_role(text)
                or not has_scope_content(re.split(r'[:：]', text, maxsplit=1)[-1])
                or (di, start, end) in seen):
            continue
        seen.add((di, start, end))
        result.append(dict(doc_index=di, start=start, end=end, text=text,
            document_role=record['docs'][di]['type'], candidate_role=item['role']))
    for item in columnar_task_fields(record):
        key = (item['doc_index'], item['start'], item['end'])
        if key in seen:
            continue
        seen.add(key)
        candidate = {name: value for name, value in item.items() if name != 'role'}
        result.append({**candidate, 'candidate_role': item['role']})
    return result
