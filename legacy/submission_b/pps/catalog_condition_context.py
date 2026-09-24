"""Read complete condition dependencies within the same original-token cap.

The prior retrieval (including BGE candidates) supplies fill order. Property,
task, requirement-header and permission ranges are atomic dependencies. If they
do not fit, the reader reports that limit; it never clips them into false proof.
"""
from __future__ import annotations

import re

from .catalog_permissions import occurrences
from .catalog_condition_review import condition_plan, property_readings, _local_task_witnesses
from .catalog_semantics import candidates, statement
from .notice_search import NoticeSearch, merge_ranges
from .prompts import verified_search_spans
from .requirement_frames import containing
from .retrieval import Span
from .source_units import unitize


def window(record, ev, neighbors=1):
    text = record['docs'][ev['doc_index']]['text']
    lines = list(re.finditer(r'[^\r\n]+', text))
    selected = [i for i, m in enumerate(lines) if m.start() < ev['end'] and m.end() > ev['start']]
    if not selected:
        return (ev['doc_index'], ev['start'], ev['end'])
    lo, hi = max(0, selected[0]-neighbors), min(len(lines)-1, selected[-1]+neighbors)
    return ev['doc_index'], lines[lo].start(), lines[hi].end()


def expand(record, prior, tokenizer, knowledge):
    old = verified_search_spans(record, prior, tokenizer)
    reader = NoticeSearch(record, tokenizer)
    _, facts = knowledge.qualification_decisions(record, {})
    plan = condition_plan(facts['product'])
    all_units = unitize([Span(i, d['type'], 0, len(d['text']), d['text'])
                        for i, d in enumerate(record['docs']) if d['text']])
    all_refs = list(range(1, len(all_units)+1))
    dependencies, observations = [], []
    for product in plan:
        fields = [f['field'] for f in product['fields']]
        items = property_readings(record, product['name'], fields)['observations'] + candidates(record, fields)
        seen = set()
        for item in items:
            ev = item['evidence']
            key = item['field'], ev['doc_index'], ev['start'], ev['end']
            if key in seen:
                continue
            seen.add(key)
            anchors = _local_task_witnesses(record, all_units, {'scope_units': all_refs}, item)
            locations = [window(record, ev)]
            locations += [(a['doc_index'], a['start'], a['end']) for a in anchors]
            frames = containing(record, ev)
            locations += [(f['doc_index'], f['heading']['start'], f['heading']['end']) for f in frames]
            dependencies.extend(locations)
            observations.append({'code': product['code'], 'field': item['field'],
                'property': ev, 'task_witnesses': anchors, 'requirement_frames': frames,
                'dependency_ranges': locations, 'semantics_certified': False})
    permission_sources = []
    if observations:
        for issue in occurrences(record, {o['field'] for o in observations}):
            local = record['docs'][issue['doc_index']]['text'][issue['start']:issue['start']+40]
            if issue['text'] == '가정' and re.match(r'가정(?:보호|회복|복지|용|에서|의\s*아동)', local):
                continue
            ev = statement(record, issue)
            permission_sources.append(ev)
            dependencies.append(window(record, ev))
    required = merge_ranges(dependencies, record['docs'])
    budget = prior['source_token_budget']
    cost = reader.token_cost(required)
    audit = {'method': 'complete_condition_dependencies_then_prior_retrieval_units',
        'prior_method': prior['method'], 'prior_source_tokens': prior['source_tokens'],
        'required_source_tokens': cost, 'observations': observations,
        'permission_sources': permission_sources, 'required_ranges': required,
        'source_budget': budget, 'new_model_calls': 0, 'semantics_certified': False}
    if cost > budget:
        audit['status'] = 'required_dependencies_exceed_budget'
        return None, audit
    selected, chosen = required, []
    # Fill from the prior candidate selection without another embedding pass.
    # Units remain exact original ranges; this is not paraphrase compression.
    for unit in unitize(old):
        candidate = merge_ranges([*selected, (unit.doc_index, unit.start, unit.end)], record['docs'])
        if reader.token_cost(candidate) <= budget:
            selected = candidate
            chosen.append((unit.doc_index, unit.start, unit.end))
    result = reader.read(selected, token_budget=budget)
    audit.update(status='complete_dependencies_fit', final_source_tokens=result['source_tokens'],
        prior_units_retained=chosen, final_ranges=selected,
        cumulative_unique_source_tokens=reader.token_cost([*selected, *[(s.doc_index, s.start, s.end) for s in old]]))
    result['diagnostics']['condition_dependency_reader'] = {'status': audit['status'],
        'required_source_tokens': cost, 'prior_method': prior['method'],
        'cumulative_unique_source_tokens': audit['cumulative_unique_source_tokens'],
        'note': 'Same final cap, changed source selection. Compare fresh control/semantic inference on this same selection;do not attribute the effect to BGE alone.'}
    return result, audit
