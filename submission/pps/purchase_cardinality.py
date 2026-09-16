"""Source-local purchase counts and explicit item-list witnesses.

A classification code is not an item identifier. Counts can expose missing
identities, while a literal name/code table can account for distinct variants
sharing a code. Neither mechanism certifies a catalog condition or fills a
missing cell. Unstructured or partly observed lists remain unresolved.
"""
from __future__ import annotations

import re

from . import sme
from .law_declarations import _reference_reason
from .products import _FIELD_START, compact, normalized_map, non_task_scope_role, scope_spans
from .table_structure import pipe_cells, pipe_separators

_FIELDS = {'구매품목', '구입품목', '구매내역', '구입내역', '품명'}
_TITLES = {'공고명', '공고건명', '입찰건명', '사업명', '건명', '계약명'}
_COUNT = re.compile(r'(?<![\d.,+\-제])(?P<connective>외|등)?(?P<open>\()?(?:총)?'
    r'(?P<number>[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d*)(?:종|품목)(?!류|별)')
_UNRESOLVED = re.compile(r'예시|작성예|기재예|일부|발췌|가칭|철회|삭제|취소|제외|'
    r'선택|옵션|예정|변경가능|경우|않|아니|미정|미확정')
_COMPONENT = re.compile(r'부속품|구성품|소모품|부품|옵션|예비품')
_NAME_HEADERS = {'품명', '품목', '물품명', '품목명', '제품명', '세부품명'}
_CODE_HEADERS = {'세부품명번호', '세부품명분류번호'}
_SPEC_HEADERS = {'규격', '사양', '모델명'}
_OTHER_HEADERS = {'번호', '순번', '연번', '수량', '단위', '단가', '금액', '비고'}
_ATTACHMENT_REFERENCE = re.compile(r'(?:붙임|별첨|별지)\s*(?:제\s*)?(\d+)(?:\s*호)?')
_ATTACHMENT_HEADING = re.compile(
    r'(?m)^[ \t]*(?:붙임|별첨|별지)\s*(?:제\s*)?(\d+)(?:\s*호)?[ \t]*[.：:]?[ \t]*$')


def _count_sources(record):
    # Repeated text can have different owners; do not lexical-deduplicate it.
    scopes = scope_spans(record, max_spans=1000, char_limit=1_000_000,
                         preserve_occurrences=True)
    result = []
    for span in scopes:
        raw = span['text']
        if non_task_scope_role(raw):
            continue
        field = _FIELD_START.match(raw.splitlines()[0])
        label = compact(field['label']) if field else None
        n, positions = normalized_map(raw)
        purchase_field = label in _FIELDS
        purchase_parent = None
        if label == '사업내용' and field:
            # A project-content field may describe species, deliverable files,
            # existing assets or bought parts. Admit its count only when the
            # very same literal object is named in an earlier purchase title
            # of this document. Do not borrow a purchase role across documents.
            value=compact(raw[field.end():])
            count=_COUNT.search(value)
            object_name=value[:count.start()] if count else ''
            if len(object_name)>=4:
                for parent in scopes:
                    if (parent['doc_index']!=span['doc_index'] or parent['end']>span['start']
                            or span['start']-parent['end']>1500):
                        continue
                    parent_field=_FIELD_START.match(parent['text'].splitlines()[0])
                    if not parent_field or compact(parent_field['label']) not in _TITLES:
                        continue
                    title=compact(parent['text'][parent_field.end():])
                    if object_name in title and re.search(r'구매|구입',title) and not _UNRESOLVED.search(title):
                        purchase_field=True
                        purchase_parent=parent
                        break
        purchase_title = (label in _TITLES or span['role'] == 'intro_title_candidate') and bool(
            re.search(r'구매|구입', n))
        if not (purchase_field or purchase_title):
            continue
        matches = list(_COUNT.finditer(n))
        if not matches:
            continue
        text = record['docs'][span['doc_index']]['text']
        reference = _reference_reason(text, span['start'])
        for match in matches:
            # A number inside an accessory/option clause is not the count of
            # the enclosing equipment purchase. Standalone parts purchases
            # remain discoverable in their own purchase fields/titles.
            opening = n.rfind('(', 0, match.start())
            closing = n.rfind(')', 0, match.start())
            parent = n[opening+1:match.start()] if opening > closing else ''
            if _COMPONENT.search(parent) or re.search(r'종(?:보통|대형)?면허', n[match.start():]):
                continue
            count = int(match['number'].replace(',', ''))
            connective = match['connective'] or 'total'
            end = match.end()
            unresolved = []
            if match['open']:
                if end < len(n) and n[end] == ')':
                    end += 1
                else:
                    unresolved.append('unclosed_count_parenthesis')
            if reference:
                unresolved.append(reference)
            if _UNRESOLVED.search(n):
                unresolved.append('non_exhaustive_or_non_operative_scope')
            lo, hi = span['start'] + positions[match.start()], span['start'] + positions[end-1] + 1
            result.append({'stated_count': count, 'connective': connective,
                'minimum_total': count + (connective == '외'),
                'evidence': sme.evidence(record, span['doc_index'], lo, hi),
                'scope_evidence': sme.evidence(record, span['doc_index'], span['start'], span['end']),
                'purchase_parent_evidence':sme.evidence(record,purchase_parent['doc_index'],purchase_parent['start'],purchase_parent['end']) if purchase_parent else None,
                'purchase_field': purchase_field, 'reference_only': bool(reference),
                'unresolved': unresolved})
    return result


def _header_label(value):
    # Only descriptive code-width annotations are stripped. An unfamiliar
    # header remains unfamiliar, rather than shifting the remaining columns.
    return re.sub(r'\((?:10자리|10단위)\)', '', normalized_map(value)[0]).strip(':')


def _list_witness(record, count):
    """Account for one total in the immediately following literal pipe table."""
    if not count['purchase_field'] or count['connective'] == '외' or count['unresolved']:
        return None
    scope = count['scope_evidence']
    di = scope['doc_index']
    text = record['docs'][di]['text']
    following = list(re.finditer(r'[^\r\n]+', text[scope['end']:]))
    if not following:
        return None
    header = following[0]
    hlo, hhi = scope['end'] + header.start(), scope['end'] + header.end()
    if not pipe_separators(text, hlo, hhi):
        return None
    cells = pipe_cells(text, hlo, hhi)
    labels = [_header_label(c['text']) for c in cells]
    names = [i for i, label in enumerate(labels) if label in _NAME_HEADERS]
    codes = [i for i, label in enumerate(labels) if label in _CODE_HEADERS]
    specs = [i for i, label in enumerate(labels) if label in _SPEC_HEADERS]
    if (len(names) != 1 or len(codes) != 1 or len(specs) > 1
            or len(labels) != len(set(labels))
            or any(l not in _NAME_HEADERS | _CODE_HEADERS | _SPEC_HEADERS | _OTHER_HEADERS for l in labels)):
        return None
    fences = (text[hlo:hhi].lstrip().startswith('|'), text[hlo:hhi].rstrip().endswith('|'))
    rows = []
    identities = set()
    for line in following[1:]:
        lo, hi = scope['end'] + line.start(), scope['end'] + line.end()
        if not text[lo:hi].strip():
            continue
        if not pipe_separators(text, lo, hi):
            # A wrapped/missing row cannot be silently ignored as a boundary.
            if not re.match(r'\s*(?:(?:\d+[.)]|[가-하][.)])\s*)?'
                            r'(?:납품기한|납품장소|입찰참가자격|계약조건|입찰일정)\s*[:：]?', text[lo:hi]):
                return None
            break
        values = pipe_cells(text, lo, hi, fences=fences)
        if len(values) != len(labels) or _UNRESOLVED.search(compact(text[lo:hi])):
            return None
        if all(re.fullmatch(r':?-{2,}:?', c['text']) for c in values) and not rows:
            continue  # Explicit Markdown header separator, not an item.
        name, code = values[names[0]], values[codes[0]]
        spec = values[specs[0]] if specs else None
        if (not re.fullmatch(r'\d{10}', code['text'])
                or not re.search(r'[가-힣A-Za-z]{2}', name['text'])
                or re.search(r'합계|총계|소계|품명|입력|기재|동일|상동', name['text'])):
            return None
        identity = (normalized_map(name['text'])[0], normalized_map(spec['text'])[0] if spec else '')
        if identity in identities:
            return None  # Repeated printing/locations do not prove new items.
        identities.add(identity)
        rows.append({'name': sme.evidence(record, di, name['start'], name['end']),
            'code': code['text'], 'code_evidence': sme.evidence(record, di, code['start'], code['end']),
            'specification': sme.evidence(record, di, spec['start'], spec['end']) if spec else None})
        if len(rows) > 256:
            return None
    if len(rows) != count['minimum_total']:
        return None
    return {'scope_evidence': scope, 'header_evidence': sme.evidence(record, di, hlo, hhi),
        'rows': rows, 'observed_item_count': len(rows), 'distinct_codes': sorted({r['code'] for r in rows}),
        'basis': 'explicit_total_and_adjacent_literal_name_code_rows',
        'missing_cells_inferred': False, 'catalog_identity_complete': True,
        'whole_purchase_certified': True, 'catalog_conditions_certified': False}


def _specification_list_witness(record, count):
    """Bind an exact total to repeated original specification subject blocks.

    PDF extraction often flattens a wide summary table, but repeats each item
    under the referenced specification attachment.  This route accepts only a
    literal attachment reference, an exact attachment boundary, one observed
    name in every repeated ``번호/품명/단위/비고`` block, and an exact count
    match.  It does not reconstruct the damaged summary-table columns or infer
    a catalog code from a nearby name.
    """
    if (not count['purchase_field'] or count['connective'] == '외'
            or count['unresolved'] or count['minimum_total'] > 256):
        return None
    scope = count['scope_evidence']
    references = list(_ATTACHMENT_REFERENCE.finditer(scope['text']))
    if len(references) != 1:
        return None
    attachment_number = int(references[0][1])
    di = scope['doc_index']
    text = record['docs'][di]['text']
    headings = list(_ATTACHMENT_HEADING.finditer(text))
    matching = [i for i, heading in enumerate(headings)
                if int(heading[1]) == attachment_number and heading.start() >= scope['end']]
    if len(matching) != 1:
        return None
    position = matching[0]
    heading = headings[position]
    section_end = headings[position + 1].start() if position + 1 < len(headings) else len(text)

    # Import lazily: table_structure itself imports this module's low-level
    # pipe helpers.  The public purchase_cardinality call occurs after module
    # initialization, so this avoids an import cycle without duplicating the
    # table hypothesis logic.
    from .table_structure import table_structures
    blocks = []
    expected_roles = ('index', 'name', 'unit', 'note')
    for table in table_structures(text):
        if not (heading.end() <= table['start'] < table['end'] <= section_end
                and table['layout'] == 'vertical'
                and tuple(header['role'] for header in table['headers']) == expected_roles
                and len(table['rows']) == 1
                and not table['candidate_search_truncated']):
            continue
        row = table['rows'][0]
        names = {(candidate['start'], candidate['end'], candidate['text'])
                 for candidate in row['name_candidates']}
        if len(names) != 1:
            return None
        start, end, name = next(iter(names))
        normalized = normalized_map(name)[0]
        if (not re.search(r'[가-힣A-Za-z]{2}', normalized)
                or re.search(r'^(?:품명|물품명|제품명|합계|총계|소계)$', normalized)):
            return None
        blocks.append({'name': sme.evidence(record, di, start, end),
            'code': None, 'code_evidence': None, 'specification': None,
            'block_header_evidence': sme.evidence(record, di, table['header_start'], table['header_end']),
            'block_range': sme.evidence(record, di, table['start'], table['end']),
            '_table_start': table['start']})
    if len(blocks) != count['minimum_total']:
        return None
    identities = [normalized_map(row['name']['text'])[0] for row in blocks]
    if len(set(identities)) != len(identities):
        return None
    for index, row in enumerate(blocks):
        detail_end = blocks[index + 1]['_table_start'] if index + 1 < len(blocks) else section_end
        row['detail_range'] = sme.evidence(record, di, row['_table_start'], detail_end)
        del row['_table_start']
    return {'scope_evidence': scope,
        'header_evidence': sme.evidence(record, di, heading.start(), heading.end()),
        'attachment_range': sme.evidence(record, di, heading.start(), section_end),
        'rows': blocks, 'observed_item_count': len(blocks), 'distinct_codes': [],
        'basis': 'exact_total_and_referenced_repeated_specification_subject_blocks',
        'missing_cells_inferred': False, 'catalog_identity_complete': False,
        'whole_purchase_certified': True, 'catalog_conditions_certified': False,
        'summary_table_layout_reconstructed': False}


def purchase_cardinality(record):
    counts = _count_sources(record)
    lists = []
    for count in counts:
        # Multiple counts in the same title may describe subgroups. A locally
        # matching row count does not make any one of those the overall total.
        scope = count['scope_evidence']
        siblings = [c for c in counts if c['scope_evidence'] == scope]
        witness = (_list_witness(record, count) or _specification_list_witness(record, count)) \
            if len(siblings) == 1 else None
        count['item_list_index'] = len(lists) if witness else None
        if witness:
            lists.append(witness)
    # A notice and its attachment may repeat the same exact whole-purchase
    # total.  Reuse the one source-certified list only when every operative
    # multi-item count agrees; different totals remain distinct and unresolved.
    whole = [(i, witness) for i, witness in enumerate(lists)
             if witness.get('whole_purchase_certified')]
    operative = [count for count in counts if count['minimum_total'] > 1
                 and not count['reference_only'] and not count['unresolved']
                 and count['connective'] != '외']
    if len(whole) == 1 and operative and len({c['minimum_total'] for c in operative}) == 1:
        index, witness = whole[0]
        if witness['observed_item_count'] == operative[0]['minimum_total']:
            for count in operative:
                if count['item_list_index'] is None:
                    count['item_list_index'] = index
                    count['item_list_reconciliation'] = (
                        'same_record_same_exact_total_as_referenced_whole_purchase_list')
    return {'counts': counts, 'item_lists': lists}
