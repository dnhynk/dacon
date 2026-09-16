"""Recover explicit model fields from one narrow flattened specification form.

PDF extraction can serialize a table as a run of column headers followed by a
run of values.  This module does not try to reconstruct arbitrary tables.  It
accepts only a repeated procurement form whose Korean headers, ten-digit
catalog code, unit, product name, and model-shaped value agree in one bounded
source block.  Every returned relationship retains literal document offsets.
"""
from __future__ import annotations

import re

from .data import clean_evidence


_SPEC_TITLE = {'규격서'}
_REQUIRED_HEADERS = ('품명', '모델명', '세부품명번호', '단위', '수량')
_MIRROR_HEADERS = {
    'commoditydescription', '품목번호', 'itemno', 'description', 'unit', 'qty',
}
_UNITS = {
    'system', 'set', 'unit', 'ea', 'lot', '식', '대', '개', '세트', '조', '식',
    '병', '팩', '박스', '본', '권', '매', '장', '통', '건', '회', '명', '개소',
}
_GENERIC_MODEL_WORDS = {
    'and', 'arm', 'base', 'basic', 'chip', 'chipset', 'computer', 'core', 'cpu',
    'desktop', 'device', 'equipment', 'faster', 'gb', 'ghz', 'gpu', 'hardware',
    'hz', 'laptop', 'linux', 'memory', 'mhz', 'model', 'network', 'nic', 'notebook',
    'or', 'processor', 'quad', 'ram', 'server', 'software', 'ssd', 'storage', 'system',
    'tb', 'usb', 'windows', 'workstation',
}
_SECTION = re.compile(
    r'^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.．]|[A-Z][.．]|제\s*\d+\s*[장절]|'
    r'\d{1,3}(?:[.-]\d{1,3})*[.)．]|[가-하][.)．])(?:\s|$)'
)
_CATALOG = re.compile(r'\d{10}')
_MODEL_PERMISSION = re.compile(
    r'(?:동등|동급)(?:\s*이상)?\s*(?:모델|제품|물품|기종|장비)'
    r'[^\n.!?。]{0,24}(?:허용|가능|인정|제안|납품|공급)|'
    r'(?:모델|제품|물품|기종|장비)[^\n.!?。]{0,24}(?:또는|혹은)'
    r'[^\n.!?。]{0,16}(?:동등|동급)',
    re.I,
)


def _normalized(value: str) -> str:
    return re.sub(r"[\s:：.'’`\-_/]", '', value).casefold()


def _source(doc_index, text, start, end):
    return {'doc_index': doc_index, 'start': start, 'end': end, 'text': text[start:end]}


def _lines(text):
    result = []
    at = 0
    for raw in text.splitlines(keepends=True):
        body = raw.rstrip('\r\n')
        stripped = body.strip()
        if stripped:
            left = len(body) - len(body.lstrip())
            start = at + left
            result.append({'start': start, 'end': start + len(stripped), 'text': stripped,
                           'normalized': _normalized(stripped)})
        at += len(raw)
    # splitlines() omits the final empty terminator but retains a final nonempty
    # line.  A no-newline one-line document therefore needs no special case.
    return result


def _model_shaped(value):
    if not 4 <= len(value) <= 100 or not re.search(r'[A-Za-z]', value):
        return False
    if re.search(r'[:：=<>]|\b(?:or|또는|이상|이하|최소|최대)\b', value, re.I):
        return False
    words = re.findall(r'[A-Za-z][A-Za-z0-9._+/-]*', value)
    if not words:
        return False
    meaningful = [word for word in words
                  if word.casefold().strip('._+/-') not in _GENERIC_MODEL_WORDS]
    return bool(meaningful) and (bool(re.search(r'\d', value)) or len(words) >= 2)


def _product_shaped(value):
    return (2 <= len(value) <= 100 and bool(re.search(r'[가-힣]', value))
            and not _SECTION.match(value) and _normalized(value) not in _REQUIRED_HEADERS)


def _unit_shaped(value):
    return _normalized(value) in _UNITS


def _ordered_headers(lines, model_index):
    """Return the bounded header indices or None.

    English mirrors and the item-number column may be interleaved.  The five
    semantic Korean headers themselves must be present, ordered, and close.
    """
    start = max(0, model_index - 12)
    end = min(len(lines), model_index + 12)
    window = lines[start:end]
    indices = []
    cursor = 0
    for header in _REQUIRED_HEADERS:
        for offset in range(cursor, len(window)):
            if window[offset]['normalized'] == header:
                indices.append(start + offset)
                cursor = offset + 1
                break
        else:
            return None
    if indices[1] != model_index or indices[-1] - indices[0] > 10:
        return None
    prefix = lines[max(0, indices[0] - 8):indices[0]]
    if not any(line['normalized'] in _SPEC_TITLE for line in prefix):
        return None
    # The form identifies itself as a commodity description or item-number
    # table.  This prevents an ordinary prose list of similar words from being
    # treated as a procurement table.
    if not any(line['normalized'] in _MIRROR_HEADERS for line in prefix):
        return None
    return indices


def observations(record):
    """Return typed, source-addressed model-column observations.

    A result certifies the observed table relation only.  It does not claim
    document-wide completeness and never repairs or reorders the source text.
    """
    result = []
    for doc_index, doc in enumerate(record.get('docs', [])):
        text = doc.get('text')
        if not isinstance(text, str) or not text:
            continue
        lines = _lines(text)
        for model_index, label in enumerate(lines):
            if label['normalized'] != '모델명':
                continue
            headers = _ordered_headers(lines, model_index)
            if headers is None:
                continue
            values = []
            for line in lines[headers[-1] + 1:headers[-1] + 9]:
                if (_SECTION.match(line['text'])
                        or line['normalized'] in _REQUIRED_HEADERS
                        or line['normalized'] in _SPEC_TITLE):
                    break
                values.append(line)
            catalog_positions = [i for i, line in enumerate(values)
                                 if _CATALOG.fullmatch(line['text'])]
            for catalog_position in catalog_positions:
                if catalog_position < 2 or catalog_position + 1 >= len(values):
                    continue
                product, model = values[catalog_position - 2:catalog_position]
                catalog, unit = values[catalog_position:catalog_position + 2]
                if not (_product_shaped(product['text']) and _model_shaped(model['text'])
                        and _unit_shaped(unit['text'])):
                    continue
                # At most one optional item number may precede the aligned
                # product/model/code/unit tuple.  Extra values make the layout
                # ambiguous and are left unresolved.
                if catalog_position - 2 > 1:
                    continue
                next_titles = [line['start'] for line in lines[headers[-1] + 1:]
                               if line['normalized'] in _SPEC_TITLE]
                block_end = min(next_titles) if next_titles else len(text)
                permission_hits = []
                for match in _MODEL_PERMISSION.finditer(text, unit['end'], block_end):
                    permission_hits.append(_source(doc_index, text, match.start(), match.end()))
                evidence_start, evidence_end = lines[headers[0]]['start'], unit['end']
                if evidence_end - evidence_start > 500:
                    continue
                result.append({
                    'doc_index': doc_index,
                    'syntax': 'flattened_typed_model_column',
                    'form_source': _source(doc_index, text, lines[max(0, headers[0] - 2)]['start'],
                                           evidence_end),
                    'model_label_source': _source(doc_index, text, label['start'], label['end']),
                    'product_source': _source(doc_index, text, product['start'], product['end']),
                    'model_source': _source(doc_index, text, model['start'], model['end']),
                    'catalog_source': _source(doc_index, text, catalog['start'], catalog['end']),
                    'unit_source': _source(doc_index, text, unit['start'], unit['end']),
                    'evidence_source': _source(doc_index, text, evidence_start, evidence_end),
                    'model_alternative_sources': permission_hits,
                    'source_text_reordered': False,
                    'absence_verified': False,
                })
                break
    result.sort(key=lambda item: (item['doc_index'], item['model_source']['start']))
    return result


def apply_v9(record, row, *, items):
    """Promote only a certified explicit model column for the requested v9."""
    result = dict(row)
    if 9 not in items or result.get('v9') not in (0, '0'):
        return result, None
    found = observations(record)
    applicable = [item for item in found if not item['model_alternative_sources']]
    if not applicable:
        return result, None
    chosen = applicable[0]
    source = chosen['evidence_source']
    quote = clean_evidence(source['text'], record,
                           source=(source['doc_index'], source['start'], source['end']))
    if not quote:
        return result, None
    result['v9'], result['e9'] = 1, quote
    return result, {
        'source': 'flattened_typed_model_column_v1',
        'item': 9,
        'reason': 'explicit_model_field_aligned_with_product_catalog_code_and_unit',
        'previous_value': row.get('v9'),
        'previous_evidence': row.get('e9', ''),
        'public_evidence': quote,
        'selected': chosen,
        'all_observations': found,
        'typed_relation_certified': True,
        'new_procurement_relation_certified': True,
        'legal_exemption_inferred': False,
        'document_wide_absence_inferred': False,
    }
