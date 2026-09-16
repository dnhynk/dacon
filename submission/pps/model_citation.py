"""Repair a V9 locator using a named source anchor in the model's own citation.

A valid source substring need not support the model's reason. This pass only
relocates an empty/unrelated quote when the model explicitly cited another S
span and a non-generic name in that span. It does not infer product identity,
new procurement, equivalence scope, or the truth of the violation bit.
"""
from __future__ import annotations

import re

from .data import clean_evidence
from .response_contract import loads

FIELD = '기관시설인력제한_특정모델'
_REF = re.compile(r'(?<![A-Za-z0-9])S([1-9]\d*)(?:\s*[-~–]\s*S?([1-9]\d*))?(?![A-Za-z0-9])', re.I)
_LATIN = re.compile(r'(?<![A-Za-z0-9-])[A-Za-z][A-Za-z0-9™®._+/-]*(?:[ \t]+[A-Za-z0-9][A-Za-z0-9™®._+/-]*)*')
_QUOTED = re.compile(r'["\'“‘]([^"\'”’\n]{3,80})["\'”’]')
_GENERIC = frozenset(('processor chipset gpu cpu os memory storage system model manufacturer brand '
    'network nic software hardware windows linux arm intel amd usb ssd hdd ram cpu gpu '
    'operating system main system included in the box gb mb ghz mhz').split())


def cited_indices(summary, count):
    """Bound ranges by the actual packet; never allocate from untrusted digits."""
    result = set()
    for match in _REF.finditer(summary):
        first = int(match[1])
        last = int(match[2]) if match[2] else first
        if 1 <= first <= last <= count:
            result.update(range(first - 1, last))
    return sorted(result)


def named_anchors(summary):
    result = set()
    for match in _LATIN.finditer(summary):
        text = match[0].strip(' .,/+-')
        words = re.findall(r'[A-Za-z0-9]+', text)
        if (len(text) < 5 or re.fullmatch(r'S\d+(?:-S?\d+)?', text, re.I)
                or not words or all(w.casefold() in _GENERIC or w.isdecimal() for w in words)
                or re.fullmatch(r'\d*(?:GB|MB|GHz|MHz|TB)', text, re.I)):
            continue
        result.add(text)
    # Quoted Korean product names can serve as exact anchors, but sentences
    # and generic specification/permission wording cannot.
    for match in _QUOTED.finditer(summary):
        text = match[1].strip()
        if (re.search(r'[가-힣]', text) and not re.search(r'\s|[.!?:;]|동등|이상|허용|규격|제조사|모델명', text)
                and text not in {'제품명', '브랜드', '소프트웨어', '하드웨어'}):
            result.add(text)
    return sorted(result, key=lambda s: (-len(s), s))


def _matches(text, anchors):
    result = []
    for anchor in anchors:
        for match in re.finditer(r'(?<![A-Za-z0-9])' + re.escape(anchor) + r'(?![A-Za-z0-9])', text):
            result.append((match.start(), match.end(), anchor))
    return sorted(result)


def _span_source(rec, span):
    index, start, end = span.doc_index, span.start, span.end
    if (type(index) is not int or not 0 <= index < len(rec['docs'])
            or type(start) is not int or type(end) is not int):
        return None
    doc = rec['docs'][index]
    if (doc['type'] != span.doc_type or not 0 <= start < end <= len(doc['text'])
            or doc['text'][start:end] != span.text):
        return None
    return doc['text']


def _excerpt(text, hit, limit=500):
    """Keep the paragraph prefix and end at a paragraph/line boundary."""
    start, end, _ = hit
    line_start = text.rfind('\n', 0, start) + 1
    # A source paragraph may contain the actor, object, or exception. Do not
    # start at the name simply because it maximizes string-overlap density.
    paragraph = text.rfind('\n\n', 0, start)
    paragraph = paragraph + 2 if paragraph >= 0 else 0
    lo = paragraph if end - paragraph <= limit else line_start
    if end - lo > limit:
        return None  # No safe local boundary preserves the complete anchor.
    hi = min(len(text), lo + limit)
    boundary = max(text.rfind('\n\n', end, hi), text.rfind('\n', end, hi))
    if hi < len(text):
        if boundary < end:
            return None  # Do not clip a continuing clause at the size cap.
        hi = boundary
    return lo, hi


def repair_v9(rec, row, response, spans, *, items):
    result = dict(row)
    if 9 not in items or row.get('v9') not in (1, '1'):
        return result, None
    obj = loads(response['text'])
    facts = obj.get('facts', {}) if isinstance(obj, dict) else {}
    summary = facts.get(FIELD, '') if isinstance(facts, dict) else ''
    if not isinstance(summary, str) or not summary:
        return result, None
    anchors = named_anchors(summary)
    indices = cited_indices(summary, len(spans))
    # Keep a quote already grounded in a named assertion; missing another
    # name is not evidence that this selected quote is incorrect.
    if not anchors or not indices or _matches(row.get('e9', ''), anchors):
        return result, None
    candidates, contexts = [], []
    for index in indices:
        span = spans[index]
        if _span_source(rec, span) is None:
            continue
        contexts.append({'span_number': index + 1, 'doc_index': span.doc_index,
                         'start': span.start, 'end': span.end, 'text': span.text})
        matches = _matches(span.text, anchors)
        for hit in matches:
            window = _excerpt(span.text, hit)
            if window is None:
                continue
            lo, hi = window
            proposed = span.text[lo:hi]
            source = (span.doc_index, span.start + lo, span.start + hi)
            quote = clean_evidence(proposed, rec, source=source)
            matched = sorted({m[2] for m in _matches(quote, anchors)})
            if not quote or hit[2] not in matched:
                continue
            # clean_evidence may extend left to preserve an initial operator.
            # Locate that exact extension without attributing it to the old lo.
            quote_start = source[1] if quote == proposed[:len(quote)] else source[2] - len(quote)
            doc_text = rec['docs'][span.doc_index]['text']
            if doc_text[quote_start:quote_start + len(quote)] != quote:
                continue
            candidates.append({'span_number': index + 1, 'doc_index': span.doc_index,
                'start': quote_start, 'end': quote_start + len(quote), 'quote': quote,
                'matched_anchors': matched, 'cited_span': {'doc_index': span.doc_index,
                    'start': span.start, 'end': span.end, 'text': span.text}})
    if not candidates:
        return result, None
    # All candidates have an explicit source locator and literal named anchor.
    # Tie order is stable and never uses labels, another notice, or a new LLM.
    candidates.sort(key=lambda c: (-len(c['matched_anchors']), c['span_number'], c['start'], c['end']))
    candidates = list({(c['span_number'], c['start'], c['end']): c for c in candidates}.values())
    chosen = candidates[0]
    result['e9'] = chosen['quote']
    detail = {'source': 'source_named_citation_repair', 'item': 9,
        'reason': 'explicit_fact_citation_and_named_source_anchor_replace_unrelated_quote',
        'model_fact': summary, 'model_fact_is_fallible': True,
        'previous_evidence': row.get('e9', ''), 'public_evidence': chosen['quote'],
        'selected': chosen, 'all_cited_source_candidates': candidates,
        'verified_cited_contexts': contexts,
        'judgment_preserved': True, 'product_identity_certified': False,
        'equivalence_scope_certified': False,
        'limitation': 'Literal citation repair, not a semantic or legal verdict. Full cited source context is retained.'}
    return result, detail
