"""Observed specification candidates with an exhaustive, optional review boundary.

Candidate syntax does not establish a unique name, a purchase obligation, or a
violation. The inventory covers only the offered source and the declared syntax.
It cannot prove document-wide absence.
"""
from __future__ import annotations

import dataclasses
import copy
import hashlib
import json
import re

import jsonschema

from .notice_search import merge_ranges
from .response_contract import loads
from .source_units import validate as validate_source_units

NAME = 'specification_candidate_reviews_v1'
MAX_REVIEW_CANDIDATES = 64
MAX_REVIEW_UNITS = 2048
_LABELS = {
    '모델명': 'model', '모델': 'model', '형식명': 'model', '기종명': 'model',
    '제조사': 'manufacturer', '제작사': 'manufacturer', '제조회사': 'manufacturer',
    '제조업체': 'manufacturer', '브랜드': 'brand', '상표': 'brand',
    'chipset': 'chipset', 'processor': 'processor', 'cpu': 'processor',
    'gpu/graphics': 'graphics', 'gpu': 'graphics', 'network/nic': 'network', 'os': 'software',
}
_LABEL_EXPR = '|'.join('[ \t]*'.join(map(re.escape, label)) if re.search('[가-힣]', label)
                       else re.escape(label).replace('/', r'[ \t]*/[ \t]*')
                       for label in sorted(_LABELS, key=len, reverse=True))
_PREFIX = r'[ \t]*(?:[-○●□■•ㆍ][ \t]*)?(?:(?:\d{1,3}(?:-\d{1,3})?|[가-하])[.)][ \t]*)?'
_FIELD = re.compile(_PREFIX + r'(?P<label>' + _LABEL_EXPR + r')[ \t]*(?P<colon>[:：])?(?P<tail>.*)$', re.I)
_PAREN_MODEL = re.compile(_PREFIX + r'\([ \t]*(?P<label>모델(?:명)?)[ \t]+(?P<value>[^)\r\n]+)\)[ \t]*$')
_COMPONENT = re.compile(
    r'(?<![A-Za-z0-9_-])(?P<value>[A-Za-z][A-Za-z0-9_-]*(?:[ \t]+[A-Za-z0-9][A-Za-z0-9_-]*){0,4})'
    r'[ \t]*(?:칩셋|칩|프로세서)(?:이|가|을|를)?[^\n.。]{0,20}(?:내장|탑재)', re.I)
_OTHER_HEADER = re.compile(r'(?:품[ \t]*명|수[ \t]*량|단[ \t]*위|규[ \t]*격|제[ \t]*원|비[ \t]*고|'
                           r'국[ \t]*문|영[ \t]*문|' + _LABEL_EXPR + r')[ \t]*[:：]?[ \t]*$', re.I)
_SECTION = re.compile(r'[ \t]*(?:[□■※]|(?:\d{1,3}|[가-하])[.)]|제[ \t]*\d{1,3}[ \t]*[장절조])')


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def options_for_version(version):
    """Map persisted inventory versions to their exact discovery features."""
    if type(version) is not int or version not in {1, 2, 3, 4}:
        raise ValueError('Unknown specification candidate inventory version')
    return {'include_supply': version in {2, 4},
            'include_flattened': version in {3, 4}}


def inventory(record, units, *, include_supply=False, include_flattened=False):
    """Preserve all observed occurrences, without joining across an omitted word."""
    if type(include_supply) is not bool or type(include_flattened) is not bool:
        raise ValueError('Specification candidate feature flags must be booleans')
    validate_source_units(units, record)
    ranges = merge_ranges([(s.doc_index, s.start, s.end) for s in units], record['docs'])
    candidates = {}

    def location(di, lo, hi):
        return {'doc_index': di, 'start': lo, 'end': hi, 'text': record['docs'][di]['text'][lo:hi]}

    def add(di, field, label, value, syntax):
        reference = value or label
        key = (di, label[0], label[1], reference[0], reference[1], field)
        addresses = [label] + ([value] if value else [])
        ids = [i for i, s in enumerate(units, 1) if s.doc_index == di
               and any(s.start < hi and lo < s.end for lo, hi in addresses)]
        if not ids:
            raise AssertionError('Observed candidate has no offered source address')
        candidates[key] = {'field': field, 'label_source': location(di, *label),
            'value_source': location(di, *value) if value else None, 'source_units': ids,
            'syntax': syntax, 'unique_name_certified': False, 'purchase_role_certified': False}

    for di, lo, hi in ranges:
        text = record['docs'][di]['text']
        lines, at = [], lo
        for line in text[lo:hi].splitlines(keepends=True):
            lines.append((at, line.rstrip('\r\n')))
            at += len(line)
        for index, (start, line) in enumerate(lines):
            # A retrieval range beginning inside a sentence must not turn its
            # suffix into a new labelled field. No hidden text is added.
            line_start_observed = start == 0 or text[start - 1] in '\r\n'
            line_end_observed = start + len(line) == len(text) or text[start + len(line)] in '\r\n'
            cell_at = start
            for cell in line.split('|'):
                cell_start_observed = line_start_observed or cell_at > start
                match = _FIELD.fullmatch(cell) if cell_start_observed else None
                paren = _PAREN_MODEL.fullmatch(cell) if cell_start_observed else None
                if match:
                    label = (cell_at + match.start('label'), cell_at + match.end('label'))
                    field = _LABELS[re.sub(r'[ \t]', '', match['label']).casefold()]
                    tail = match['tail']
                    # Unlabelled prose beginning with e.g. 제조사 is not a field.
                    if match['colon'] and tail.strip():
                        stripped = tail.strip()
                        offset = cell_at + match.start('tail') + len(tail) - len(tail.lstrip())
                        end_observed = line_end_observed or cell_at + len(cell) < start + len(line)
                        add(di, field, label, (offset, offset + len(stripped)),
                            'colon_field' if end_observed else 'partial_field_value')
                    elif not tail.strip():
                        value = None
                        syntax = 'unresolved_table_header' if '|' in line else 'isolated_field_label'
                        if '|' not in line and index + 1 < len(lines):
                            # Only the immediate physical line is a candidate;
                            # blank separators and headings do not get skipped.
                            a, t = lines[index + 1]
                            complete = a + len(t) == len(text) or text[a + len(t)] in '\r\n'
                            if t.strip() and complete and '|' not in t:
                                if not _OTHER_HEADER.fullmatch(t.strip()) and not _FIELD.fullmatch(t) and not _SECTION.match(t):
                                    offset = a + len(t) - len(t.lstrip())
                                    value = (offset, offset + len(t.strip()))
                                    syntax = 'adjacent_value_candidate'
                        add(di, field, label, value, syntax)
                elif paren and line_end_observed:
                    a = cell_at + paren.start('value')
                    tail = paren['value']
                    a += len(tail) - len(tail.lstrip())
                    add(di, 'model', (cell_at + paren.start('label'), cell_at + paren.end('label')),
                        (a, a + len(tail.strip())), 'parenthesized_model')
                cell_at += len(cell) + 1
            for match in _COMPONENT.finditer(line):
                a, b = start + match.start('value'), start + match.end('value')
                if a and re.match(r'[A-Za-z0-9_-]', text[a - 1]):
                    continue  # The offered range may begin inside a name.
                # Embedded component syntax is a candidate, not proof of its
                # actual role or a recovered relation between separate blocks.
                add(di, 'embedded_component', (a, b), (a, b), 'component_containment_phrase')
    if include_supply:
        from .supply_lists import candidates as supply_candidates
        for di, doc in enumerate(record['docs']):
            for item in supply_candidates(doc['text']):
                ref, val, qty = item['source'], item['value_source'], item['quantity_source']
                if not any(d == di and a <= ref['start'] and ref['end'] <= b for d, a, b in ranges):
                    continue  # No partial names or quantities across unread ranges.
                add(di, 'counted_supply_name', (qty['start'], qty['end']),
                    (val['start'], val['end']), item['syntax'])
    if include_flattened:
        from .specification_table_fields import observations as flattened_observations

        def fully_offered(source):
            return any(di == source['doc_index'] and lo <= source['start']
                       and source['end'] <= hi for di, lo, hi in ranges)

        for observed in flattened_observations(record):
            # This candidate may be produced only from the finite source shown
            # to the specialist.  Requiring the whole typed relation span also
            # prevents reconstruction across an omitted line or range gap.
            if not fully_offered(observed['evidence_source']):
                continue
            relation_names = ('product_source', 'model_source', 'catalog_source', 'unit_source')
            if not all(fully_offered(observed[name]) for name in relation_names):
                continue
            label, value = observed['model_label_source'], observed['model_source']
            di = label['doc_index']
            ids = [i for i, source in enumerate(units, 1) if source.doc_index == di
                   and source.start < observed['evidence_source']['end']
                   and observed['evidence_source']['start'] < source.end]
            if not ids:
                raise AssertionError('Flattened table relation has no offered source address')
            # The legacy syntax pass correctly left this physical header
            # unbound.  Under the opt-in typed layout parser, replace that
            # duplicate placeholder with the source-certified relation.
            for key, candidate in list(candidates.items()):
                source = candidate['label_source']
                if (candidate['field'] == 'model' and candidate['value_source'] is None
                        and source['doc_index'] == di and source['start'] == label['start']
                        and source['end'] == label['end']):
                    del candidates[key]
            key = (di, label['start'], label['end'], value['start'], value['end'], 'model')
            candidates[key] = {
                'field': 'model',
                'label_source': dict(label),
                'value_source': dict(value),
                'source_units': ids,
                'syntax': 'flattened_typed_model_column',
                'relation_sources': {name: dict(observed[name]) for name in relation_names},
                'typed_relation_certified': True,
                'source_text_reordered': False,
                'unique_name_certified': False,
                'purchase_role_certified': False,
            }
    items = [{'key': f'C{i}', **candidate} for i, (_, candidate) in enumerate(sorted(candidates.items()), 1)]
    version = 1 + int(include_supply) + 2 * int(include_flattened)
    return {'version': version, 'record_id': record.get('id'), 'unit_count': len(units),
        'units_sha256': _digest([dataclasses.asdict(s) for s in units]), 'candidates': items,
        'coverage_scope': 'declared_syntax_in_offered_source_only', 'absence_verified': False}


def review_schema(plan):
    """Require a separate answer for every declared candidate, including unknowns."""
    if not isinstance(plan, dict):
        raise ValueError('Candidate review requires a prepared inventory')
    count = plan.get('unit_count')
    if (type(count) is not int or not 0 <= count <= MAX_REVIEW_UNITS
            or not isinstance(plan.get('candidates'), list)
            or len(plan['candidates']) > MAX_REVIEW_CANDIDATES
            or any(not isinstance(c, dict) for c in plan['candidates'])):
        raise ValueError('Candidate review requires bounded units and candidates; split an oversized inventory explicitly')
    keys = [c.get('key') for c in plan['candidates']]
    if keys != [f'C{i}' for i in range(1, len(keys) + 1)]:
        raise ValueError('Candidate review requires the complete ordered inventory')
    refs = {'type': 'array', 'maxItems': min(6, count),
            'items': {'type': 'integer', 'enum': list(range(1, count + 1)) or [1]}}
    enum = lambda values: {'type': 'string', 'enum': values}
    def obj(properties):
        return {'type': 'object', 'additionalProperties': False, 'required': list(properties), 'properties': properties}
    answer = obj({'specificity': enum(['named', 'generic', 'unknown']),
        'role': enum(['new_whole_product', 'new_component', 'replacement_component', 'license_renewal',
                      'maintenance_target', 'existing_reference', 'not_procurement', 'unknown']),
        'requirement': enum(['mandatory', 'example', 'unknown']),
        'scope_sources': refs, 'permission_sources': refs,
        'permission_scope': enum(['this_candidate', 'whole_product_only', 'other_product', 'not_observed', 'unclear']),
        'exception_sources': refs})
    return obj({NAME: obj({key: copy.deepcopy(answer) for key in keys})})


def decode_review(text, plan, record, units):
    options = options_for_version(plan.get('version'))
    expected = inventory(record, units, **options)
    if _digest(expected) != _digest(plan):
        raise ValueError('Candidate review plan differs from the current source inventory')
    return validate_review(text, plan, units)


def validate_review(text, plan, units):
    """Validate offered-address answers; original inventory verification is separate."""
    validate_source_units(units)
    if (not isinstance(plan, dict) or type(plan.get('unit_count')) is not int or plan['unit_count'] != len(units)
            or plan.get('units_sha256') != _digest([dataclasses.asdict(s) for s in units])):
        raise ValueError('Candidate review differs from the prepared source units')
    obj = loads(text)
    try:
        jsonschema.validate(obj, review_schema(plan))
    except jsonschema.ValidationError as exc:
        raise ValueError('Incomplete or invalid candidate review: ' + exc.message) from exc
    answers = obj[NAME]
    classified, unresolved = [], []
    for candidate in plan['candidates']:
        answer = answers[candidate['key']]
        references = answer['scope_sources'] + answer['permission_sources'] + answer['exception_sources']
        if any(type(n) is not int or not 1 <= n <= len(units) for n in references):
            raise ValueError('Candidate review requires exact integer source references')
        references = sorted(set(references))
        if any(not units[n - 1].text.strip() for n in references):
            raise ValueError('Candidate review cites an empty source')
        if any(answer[k] != 'unknown' for k in ('role', 'requirement')) and not answer['scope_sources']:
            raise ValueError('A classified purchase role or requirement needs a cited scope')
        if answer['permission_scope'] in ('this_candidate', 'whole_product_only', 'other_product') and not answer['permission_sources']:
            raise ValueError('A classified permission scope needs a cited permission')
        if answer['permission_scope'] == 'not_observed' and answer['permission_sources']:
            raise ValueError('An unobserved permission cannot cite an observed permission')
        if candidate['value_source'] is None and answer['specificity'] != 'unknown':
            raise ValueError('An unbound field has no value to classify as a name')
        (unresolved if any(answer[k] == 'unknown' for k in ('specificity', 'role', 'requirement'))
         or answer['permission_scope'] == 'unclear' or candidate['value_source'] is None
         or candidate['syntax'] in ('partial_field_value', 'adjacent_value_candidate')
         else classified).append(candidate['key'])
    return {'reviews': answers, 'declared_candidates_answered': len(answers),
        'inventory_sha256': _digest(plan),
        'model_classified_candidates': classified, 'unresolved_candidates': unresolved,
        'absence_verified': False, 'semantic_truth_certified': False, 'legal_judgment_inferred': False}
