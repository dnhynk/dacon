"""Observed permission syntax and numbering around supplied product candidates.

Every address is in the already offered source. A numbering parent is not a
certified purchase parent; an equivalence cue is not a legal permission. Gaps
and bounded continuations remain explicit rather than reconstructed as prose.
"""
from __future__ import annotations

import re

import jsonschema

from . import specification_candidates as candidates
from .notice_search import heading_ancestry, is_heading, merge_ranges, numbered_heading
from .source_units import validate as validate_source_units
from .specification_blocks import form_blocks


MAX_CONTEXT_UNITS = 6
MAX_OBSERVATIONS = 64
NAME = 'permission_context_reviews_v1'
_CUES = (
    ('equivalence', re.compile(r'동\s*등|동\s*급')),
    ('replacement', re.compile(r'대\s*체')),
    ('manufacturer_consistency', re.compile(
        r'동일[^.。\r\n]{0,24}제조사|제조사[^.。\r\n]{0,16}보증')),
)
_INLINE = re.compile(r'(?:또는|혹은|(?<![A-Za-z])or(?![A-Za-z]))[ \t]*(?:동[ \t]*등|동[ \t]*급)', re.I)
_TERMINAL = re.compile(r'(?:[.。;；]|한다|합니다|함|불가|불허|없음|가능하다)[)）\]”’]*$')
_FIELD_BOUNDARY = re.compile(r'^[ \t]*(?:[-○●□■•ㆍ][ \t]*)?[가-힣A-Za-z][가-힣A-Za-z /_-]{0,22}[:：]')
_ALL_SPECIFICATION_SCOPE = re.compile(
    r'(?:본)?규격서(?:상|의)?(?:의)?(?:기준|규격|사양|내용|조건|사항).{0,50}'
    r'(?:동등|동급).{0,50}(?:장비|제품|물품).{0,30}납품')
_DEICTIC_SCOPE = re.compile(r'(?:상기|위|해당)(?:의)?(?:사항|규격|조건).{0,50}(?:동등|동급)')


def _location(record, di, lo, hi):
    return {'doc_index': di, 'start': lo, 'end': hi, 'text': record['docs'][di]['text'][lo:hi]}


def _numbers(units, di, lo, hi):
    return [n for n, s in enumerate(units, 1) if s.doc_index == di and s.start < hi and lo < s.end]


def _open_group(text):
    pairs = {'(': ')', '（': '）', '[': ']', '【': '】', '「': '」', '『': '』',
             '“': '”', '‘': '’', '"': '"', "'": "'"}
    stack = []
    for char in text:
        if stack and char == stack[-1]:
            stack.pop()
        elif char in pairs:
            stack.append(pairs[char])
        elif char in pairs.values():
            return True
    return bool(stack)


def _inline_value(record, candidate):
    value = candidate['value_source']
    if value is None or candidate['syntax'] == 'partial_field_value':
        return {'core_value_source': None, 'inline_qualifier_source': None,
                'split_status': 'value_unbound_or_partial'}
    for match in _INLINE.finditer(value['text']):
        prefix = value['text'][:match.start()].rstrip()
        # Do not split an operator that may be part of a quoted/bracketed name,
        # or repair a damaged parenthesis to manufacture a bare model name.
        if not prefix or _open_group(prefix):
            continue
        a, b = value['start'], value['start'] + len(prefix)
        return {'core_value_source': _location(record, value['doc_index'], a, b),
                'inline_qualifier_source': _location(record, value['doc_index'],
                    value['start'] + match.start(), value['end']),
                'split_status': 'before_top_level_equivalence_operator'}
    return {'core_value_source': value, 'inline_qualifier_source': None,
            'split_status': 'no_top_level_equivalence_operator'}


def _observed_line_groups(record, ranges):
    groups = []
    for di, lo, hi in ranges:
        text, lines, at = record['docs'][di]['text'], [], lo
        for line in text[lo:hi].splitlines(keepends=True):
            raw = line.rstrip('\r\n')
            start, end = at + len(raw) - len(raw.lstrip()), at + len(raw.rstrip())
            complete = (at == 0 or text[at-1] in '\r\n') and (
                at + len(raw) == len(text) or text[at+len(raw)] in '\r\n')
            if raw.strip() and complete:
                lines.append((start, end))
            at += len(line)
        groups.append((di, lines, heading_ancestry(text, lines)))
    return groups


def _bounded_section(blocks, location):
    matches = [i for i, block in enumerate(blocks) if block['doc_index'] == location['doc_index']
               and block['start'] <= location['start'] and location['end'] <= block['end']]
    return f"D{location['doc_index']}:B{matches[0]+1}" if len(matches) == 1 else None


def _scope_syntax(record, units, refs):
    text = ''.join(units[n-1].text for n in refs)
    compact = re.sub(r'\s+', '', text)
    if _ALL_SPECIFICATION_SCOPE.search(compact):
        return 'explicit_all_specification_criteria'
    if _DEICTIC_SCOPE.search(compact):
        return 'deictic_reference'
    return 'local_or_unknown'


def _candidate_context(record, units, candidate, groups, blocks):
    label = candidate['label_source']
    result = {'candidate': candidate['key'], **_inline_value(record, candidate),
              'numbering_parent_sources': [], 'nearby_heading_sources': [],
              'bounded_specification_section': _bounded_section(blocks, label),
              'purchase_parent_certified': False, 'specificity_certified': False}
    for di, lines, ancestors in groups:
        if di != label['doc_index']:
            continue
        for index, (lo, hi) in enumerate(lines):
            if not lo <= label['start'] < label['end'] <= hi:
                continue
            parents = [i for i in ancestors[index] if i != index]
            field = ('numbering_parent_sources' if numbered_heading(record['docs'][di]['text'][lo:hi])
                     else 'nearby_heading_sources')
            result[field] = list(dict.fromkeys(n for i in parents for n in _numbers(units, di, *lines[i])))
            return result
    return result


def _cue_context(record, units, cue):
    di, lo, hi = cue['source']['doc_index'], cue['source']['start'], cue['source']['end']
    refs = _numbers(units, di, lo, hi)
    ordered = sorted((n for n, s in enumerate(units, 1) if s.doc_index == di),
                     key=lambda n: (units[n-1].start, units[n-1].end))
    if not refs:
        raise AssertionError('An observed permission cue has no supplied source')
    # Source order is independent of the order of S references or model output.
    tail = max(refs, key=lambda n: units[n-1].end)
    cursor = ordered.index(tail)
    stop = 'end_of_offered_source'
    while True:
        current = units[ordered[cursor]-1]
        if _TERMINAL.search(current.text.strip()):
            stop = 'sentence_end'
            break
        if cursor + 1 >= len(ordered):
            break
        following_number = ordered[cursor+1]
        following = units[following_number-1]
        if following.start < current.end:
            stop = 'overlapping_source_units'
            break
        gap = record['docs'][di]['text'][current.end:following.start]
        if gap.strip():
            stop = 'unobserved_text_gap'
            break
        if (numbered_heading(following.text.strip()) or is_heading(following.text.strip())
                or _FIELD_BOUNDARY.match(following.text.strip())):
            stop = 'next_structural_field'
            break
        if len(refs) >= MAX_CONTEXT_UNITS:
            stop = 'context_unit_limit'
            break
        refs.append(following_number)
        cursor += 1
    return sorted(set(refs)), stop


def inventory(record, units, *, include_supply=False, include_flattened=False):
    """Provide source candidates only; never silently expand the source budget."""
    validate_source_units(units, record)
    base = candidates.inventory(record, units, include_supply=include_supply,
                                include_flattened=include_flattened)
    ranges = merge_ranges([(s.doc_index, s.start, s.end) for s in units], record['docs'])
    groups = _observed_line_groups(record, ranges)
    blocks = form_blocks(record)
    contexts = [_candidate_context(record, units, c, groups, blocks) for c in base['candidates']]
    observations = {}
    for di, lo, hi in ranges:
        text = record['docs'][di]['text']
        for kind, pattern in _CUES:
            for match in pattern.finditer(text, lo, hi):
                cue = {'kind': kind, 'source': _location(record, di, match.start(), match.end())}
                refs, stop = _cue_context(record, units, cue)
                # Several cue words within the same supplied line constitute
                # one observation; distinct occurrences elsewhere stay distinct.
                seed = min(_numbers(units, di, match.start(), match.end()), key=lambda n: units[n-1].start)
                key = (di, units[seed-1].start)
                entry = observations.setdefault(key, {'source_units': [], 'cues': [], 'continuation_stops': []})
                entry['source_units'] = sorted(set(entry['source_units']) | set(refs))
                entry['cues'].append(cue)
                if stop not in entry['continuation_stops']:
                    entry['continuation_stops'].append(stop)
    observed = [{'key': f'P{i}', **value,
                 'bounded_specification_section': _bounded_section(blocks, value['cues'][0]['source']),
                 'scope_syntax': _scope_syntax(record, units, value['source_units']),
                 'meaning_certified': False, 'target_link_certified': False}
                for i, (_, value) in enumerate(sorted(observations.items()), 1)]
    for permission in observed:
        section=permission['bounded_specification_section']
        permission['same_section_candidates']=[c['candidate'] for c in contexts
            if section is not None and c['bounded_specification_section']==section]
    return {'version': 1, 'candidates_sha256': candidates._digest(base),
            'units_sha256': base['units_sha256'], 'unit_count': len(units),
            'candidate_contexts': contexts, 'permission_observations': observed,
            'source_text_added': False, 'coverage_scope': 'cue_syntax_in_offered_source_only',
            'absence_verified': False, 'semantic_truth_certified': False}


def review_schema(plan):
    """Require every observed cue, allowing unrelated and unresolved readings."""
    if not isinstance(plan, dict):
        raise ValueError('Permission context requires a prepared source inventory')
    count = plan.get('unit_count')
    observations, contexts = plan.get('permission_observations'), plan.get('candidate_contexts')
    if (type(count) is not int or not 0 <= count <= candidates.MAX_REVIEW_UNITS
            or not isinstance(observations, list) or len(observations) > MAX_OBSERVATIONS
            or not isinstance(contexts, list) or len(contexts) > candidates.MAX_REVIEW_CANDIDATES):
        raise ValueError('Permission context requires bounded original units and observations')
    keys = [o.get('key') for o in observations if isinstance(o, dict)]
    targets = [c.get('candidate') for c in contexts if isinstance(c, dict)]
    if keys != [f'P{i}' for i in range(1, len(observations)+1)] or targets != [f'C{i}' for i in range(1, len(contexts)+1)]:
        raise ValueError('Permission context must preserve all ordered observation and candidate keys')
    def enum(values):
        return {'type': 'string', 'enum': values}
    def obj(properties):
        return {'type': 'object', 'additionalProperties': False, 'required': list(properties), 'properties': properties}
    def entry():
        props = {
            'relevance': enum(['permission_or_requirement', 'other_context', 'unknown']),
            'target_candidates': {'type': 'array', 'maxItems': len(targets),
                                  'items': enum(targets or ['C1'])},
            'target_level': enum(['candidate', 'whole_product', 'component', 'other_product', 'unknown']),
            'attribute': enum(['brand_or_model', 'performance', 'quantity', 'warranty', 'manufacturer_consistency', 'unknown']),
            'effect': enum(['allowed', 'prohibited', 'conditional', 'required', 'unknown']),
            'target_sources': {'type': 'array', 'maxItems': min(6, count),
                               'items': {'type': 'integer', 'enum': list(range(1, count+1)) or [1]}},
        }
        result = obj(props)
        result['allOf'] = [
            {'anyOf': [{'properties': {'target_candidates': {'maxItems': 0}}},
                       {'properties': {'target_sources': {'minItems': 1}}}]},
            {'anyOf': [{'properties': {'relevance': {'enum': ['permission_or_requirement', 'unknown']}}},
                       {'properties': {'target_candidates': {'maxItems': 0},
                           'target_level': {'const': 'unknown'}, 'attribute': {'const': 'unknown'}, 'effect': {'const': 'unknown'}}}]},
        ]
        return result
    return obj({key: entry() for key in keys})


def validate_reviews(answers, plan, units, candidate_plan):
    """Validate the observation coverage and addresses, not semantic truth."""
    if (plan.get('units_sha256') != candidate_plan['units_sha256']
            or plan.get('candidates_sha256') != candidates._digest(candidate_plan)
            or plan.get('unit_count') != len(units)):
        raise ValueError('Permission context differs from its candidate/source inventory')
    try:
        jsonschema.validate(answers, review_schema(plan))
    except jsonschema.ValidationError as exc:
        raise ValueError('Incomplete or invalid permission observation review: '+exc.message) from exc
    for answer in answers.values():
        if any(type(n) is not int or not 1 <= n <= len(units) or not units[n-1].text.strip()
               for n in answer['target_sources']):
            raise ValueError('Permission target requires exact integer original source references')
    return {'observed_contexts_answered': len(answers), 'reviews': answers,
            'observation_inventory_sha256': candidates._digest(plan),
            'semantic_truth_certified': False, 'absence_verified': False}


def candidate_link_issues(answers, candidate_reviews):
    """Expose two incompatible model claims, without choosing a new legal bit."""
    issues = []
    for key, answer in answers.items():
        if answer['relevance'] != 'permission_or_requirement':
            continue
        for target in answer['target_candidates']:
            candidate = candidate_reviews[target]
            if candidate['permission_scope'] == 'not_observed':
                issues.append({'kind': 'observed_target_link_but_candidate_permission_unobserved',
                               'observation': key, 'candidate': target})
            elif (candidate['permission_scope'] == 'this_candidate' and answer['target_level'] == 'candidate'
                    and candidate['permission_attribute'] == answer['attribute'] != 'unknown'
                    and {candidate['permission_effect'], answer['effect']} == {'allowed', 'prohibited'}):
                issues.append({'kind': 'opposite_effect_for_same_model_target_attribute',
                               'observation': key, 'candidate': target})
    return issues
