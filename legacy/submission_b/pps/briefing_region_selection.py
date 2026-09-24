"""Opt-in, bounded source reservation; candidates are not legal findings."""
from __future__ import annotations

from dataclasses import asdict
import copy
import re

from .notice_search import merge_ranges
from .retrieval import NoticeIndex, Span, _CONDITION, _eligibility_units, _source_units


_EVENT = re.compile(r'(?:(?:현장|사업|과업|입찰|제안\s*요청(?:서)?)\s*)?설명회|현장\s*설명')
_ATTENDANCE = re.compile(r'참석|불참|미참가')
_ELIGIBILITY = re.compile(r'자격|한함|한하|한정|만\s*(?:입찰|참가|참여)|불가|가능|관계없|상관없')
_OFFICE = re.compile(r'본점|본사|영업소|지점|주된\s*사무소')
_LOCATION = re.compile(r'소재|위치|두고|두어|둔\s|소속')


def anchor_candidates(record, items=tuple(range(1, 25))):
    """Return complete local conditions, including an adjacent event anchor.

    Eligibility section items preserve wrapped lines and adjoining exceptions.
    Briefing attendance also occurs under an event heading, so a nearby event
    line may anchor its attendance sentence without requiring a date.
    """
    result = []
    for di, doc in enumerate(record['docs']):
        text = doc['text']
        units = _source_units(text)
        eligibility = list(_eligibility_units(text, units))
        for i, (lo, hi) in enumerate(units):
            first, last = next(((a, b) for a, b in eligibility if a <= i <= b), (i, i))
            while last + 1 < len(units) and _CONDITION.search(text[slice(*units[last + 1])]):
                last += 1
            value = text[units[first][0]:units[last][1]]
            kinds = []
            if set(items).intersection((5, 6, 7, 8)) and _OFFICE.search(value) and _LOCATION.search(value):
                # Do not reserve contact addresses or the contractor's task site.
                if any(a <= i <= b for a, b in eligibility) or re.search(r'입찰|참가\s*자격|참여\s*자격', value):
                    kinds.append('office_region_eligibility')
            if 22 in items and _ATTENDANCE.search(value) and _ELIGIBILITY.search(value):
                if _EVENT.search(value):
                    kinds.append('briefing_attendance_eligibility')
                else:
                    # A separated heading/sentence must remain in the same
                    # short local block; do not borrow an event from elsewhere.
                    for prior in range(first - 1, max(-1, first - 4), -1):
                        if units[first][0] - units[prior][0] > 500:
                            break
                        if _EVENT.search(text[slice(*units[prior])]):
                            first = prior
                            kinds.append('briefing_attendance_eligibility')
                            break
            for kind in kinds:
                candidate = {'kind': kind, 'doc_index': di,
                             'start': units[first][0], 'end': units[last][1]}
                if candidate not in result:
                    result.append(candidate)
    return result


def _covered(record, ranges, di, lo, hi):
    cursor = lo
    text = record['docs'][di]['text']
    for d, a, b in ranges:
        if d != di or b <= cursor or a >= hi:
            continue
        if a > cursor and text[cursor:a].strip():
            return False
        cursor = max(cursor, b)
    return cursor >= hi or not text[cursor:hi].strip()


def _source_atoms(record, spans):
    """Split long prior ranges only between complete operative bundles."""
    index = NoticeIndex(record)
    units, groups = index._operative_candidates()
    protected = [(c.doc_index, c.context_start, c.context_end) for group in groups for c in group]
    for di, local in enumerate(units):
        protected.extend((di, local[a][0], local[b][1])
                         for a, b in _eligibility_units(record['docs'][di]['text'], local))
    atoms = []
    for span in spans:
        cursor = span.start
        for _, end in units[span.doc_index]:
            if not cursor + 440 <= end < span.end:
                continue
            if any(di == span.doc_index and lo < end < hi for di, lo, hi in protected):
                continue
            atoms.append((span.doc_index, cursor, end))
            cursor = end
        atoms.append((span.doc_index, cursor, span.end))
    return atoms


def reserve_anchors(record, spans, tokenizer, items, source_selection=None):
    """Reserve anchors within the prior token cap, then retain prior source.

    At most 1,024 source tokens (and a quarter of the prior source allowance)
    are reserved. Oversized complete conditions are recorded, never clipped.
    Normal prompt construction separately enforces the full context limit.
    """
    if tokenizer is None:
        raise ValueError('Briefing/region selection requires a source tokenizer')
    candidates = anchor_candidates(record, items)
    old = [(s.doc_index, s.start, s.end) for s in spans]
    if not any(not _covered(record, old, c['doc_index'], c['start'], c['end']) for c in candidates):
        return spans, source_selection
    budget = sum(len(tokenizer.encode(s.text, add_special_tokens=False)) for s in spans)
    reserve_cap = min(1024, budget // 4)
    cache = {}

    def cost(ranges):
        total = 0
        for di, lo, hi in ranges:
            key = di, lo, hi
            if key not in cache:
                cache[key] = len(tokenizer.encode(record['docs'][di]['text'][lo:hi], add_special_tokens=False))
            total += cache[key]
        return total

    reserved = ()
    # Alternate roles and documents so a repeated clause cannot consume all
    # the reserve before another document's first condition is considered.
    queues = {}
    for c in candidates:
        queues.setdefault((c['kind'], c['doc_index']), []).append(c)
    while any(queues.values()):
        for queue in queues.values():
            if not queue:
                continue
            c = queue.pop(0)
            proposed = merge_ranges([*reserved, (c['doc_index'], c['start'], c['end'])], record['docs'])
            if cost(proposed) <= reserve_cap:
                reserved = proposed
    if not reserved:
        return spans, source_selection
    selected = reserved
    for atom in _source_atoms(record, spans):
        proposed = merge_ranges([*selected, atom], record['docs'])
        if cost(proposed) <= budget:
            selected = proposed
    chosen = [Span(di, record['docs'][di]['type'], lo, hi, record['docs'][di]['text'][lo:hi])
              for di, lo, hi in selected]
    if source_selection is not None:
        source_selection = copy.deepcopy(source_selection)
        source_selection.update(spans=[asdict(s) for s in chosen], source_tokens=cost(selected))
        # Regenerate search coverage/document receipts from the actual ranges.
        from .notice_search import NoticeSearch
        actual = NoticeSearch(record, tokenizer).read(selected, token_budget=budget)
        source_selection.update(documents=actual['documents'], coverage=actual['coverage'])
        source_selection.setdefault('diagnostics', {})['briefing_region_anchor_selection'] = {
            'reserved_ranges': reserved, 'reserve_token_cap': reserve_cap,
            'original_source_tokens': budget, 'selected_source_tokens': cost(selected),
            'candidates': [{**c, 'covered': _covered(record, selected, c['doc_index'], c['start'], c['end'])}
                           for c in candidates], 'legal_finding': False}
    return chosen, source_selection
