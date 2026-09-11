"""Model/specification roles from this notice, without a brand lookup table."""
from __future__ import annotations
import re

FIELD = re.compile(r'(?:모델명|모델|제조사|제조업체|브랜드|상표|기종|Chipset|Model)\s*[:：|]\s*[^\r\n]+', re.I)
EXISTING = re.compile(r'기존.{0,70}(?:동일|호환|운영|보유)|라이선스\s*갱신|유지\s*보수\s*대상|서비스\s*연장', re.S)
ALTERNATIVE = re.compile(r'동등|동급|대체\s*(?:가능|허용)|타\s*제조사')
RESTRICTED = re.compile(r'(?:동등|동급|대체|타\s*제조사).{0,35}(?:불가|불허|금지|허용하지)|반드시.{0,80}(?:모델|제품)|모델.{0,80}만\s*납품')
GENERIC_VALUE = re.compile(r'^(?:\d+(?:\.\d+)?\s*(?:GB|TB|MB|GHz|MHz|Gbps|W|mm|인치)|DDR\d|PCIe|HDMI|USB|SSD|HDD|C-ARM)(?:\W|$)', re.I)


def facts(rec):
    candidates, alternatives, existing, references = [], [], [], []
    for di, doc in enumerate(rec['docs']):
        text = doc['text']
        for match in re.finditer(r'[^\r\n]+', text):
            line = match[0]
            ev = {'doc_index': di, 'start': match.start(), 'end': match.end(), 'text': line}
            if FIELD.search(line):
                candidates.append({'role': 'existing_or_compatibility' if EXISTING.search(line) else 'role_requires_review',
                                   'evidence': ev})
            if ALTERNATIVE.search(line):
                alternatives.append({'scope': 'referenced_scope_requires_review', 'evidence': ev})
            if EXISTING.search(line):
                existing.append(ev)
            if re.search(r'(?:규격서|물품내역서|구매내역서).{0,35}(?:참조|참고)', line):
                references.append(ev)
    return {'model_field_candidates': candidates, 'alternative_clauses': alternatives,
            'existing_or_renewal_clauses': existing, 'specification_references': references,
            'specification_document_observed': any(d['type'] == '규격서' for d in rec['docs']),
            'candidate_name_is_not_a_restriction': True, 'equivalence_scope_requires_review': True}


def supports_new_specific_quote(rec, source):
    """Reject source roles that directly contradict a new-model restriction.

    Passing this guard still relies on the model to interpret product identity
    and the scope of alternatives. It is not an independent legal conclusion.
    """
    quote = source['text']
    if EXISTING.search(quote) and not RESTRICTED.search(quote):
        return False
    if ALTERNATIVE.search(quote) and not RESTRICTED.search(quote):
        return False
    field = FIELD.search(quote)
    if field:
        value = re.split(r'[:：|]', field[0], maxsplit=1)[1].strip()
        return not bool(GENERIC_VALUE.match(value))
    # Named items in a delivery bundle can identify a model without a field
    # label. Capacity/port numbers alone are not such a bundle.
    return bool(re.search(r'[A-Za-z][A-Za-z0-9 -]{1,70}(?:본체|조종기|배터리|모델|시리즈|기종)', quote)
                and re.search(r'\d|™|®', quote))
