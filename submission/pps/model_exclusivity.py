"""Positive-only v9 witnesses for a named identity whose alternatives are closed.

Two source relations, each bound inside one requirement line:

1. exclusive identity: a model/brand name bound to its identity noun, and the
   same sentence refuses other makers/models/brands (including equivalents);
2. parenthesised designation: a labelled specification row describes its
   product and pins it with ``(모델: <designation>)``.

Reference, compatibility, example and equivalence-allowing contexts withhold
the witness. Unknown roles remain model decisions; this never returns v9=0.
"""
from __future__ import annotations

import re

from .data import clean_evidence

_NAME = r'[A-Za-z0-9가-힣][A-Za-z0-9가-힣._/-]{1,30}'
_BOUND = re.compile(
    r'(?:모델(?:명)?|브랜드|상표|기종)\s*(?:은|는|을|를|[:：])?\s*(?P<a>' + _NAME + r')'
    r'|(?P<b>' + _NAME + r')\s*(?:브랜드|상표)')
_REFUSED = re.compile(
    r'(?:타|다른)\s*(?:제조사|제작사|브랜드|상표|모델|기종|메이커|회사)'
    r'[^.。;\n]{0,40}?'
    r'(?:(?:승인|인정|허용|인수)하지\s*않|할\s*수\s*없|불가|불허|금지|미허용)')
_DESIGNATION = re.compile(
    r'(?P<product>[가-힣][가-힣 ]{1,30}?)\s*\(\s*모델(?:명)?\s*[:：]\s*'
    r'(?P<name>[A-Za-z0-9][A-Za-z0-9 ._/-]{1,38}?)\s*\)')
_ROW_LABEL = re.compile(
    r'^\s*[-○●•▪·]?\s*(?:차\s*종|차\s*량\s*명|품\s*명|물품명|제품명|장비명|규\s*격|사\s*양|기\s*종)\s*[|:：]')
_GENERIC = re.compile(r'^(?:Windows|Linux|Ubuntu|Android|iOS|OS|USB|HDMI|ISO|KS|IEC|IEEE|LED|LCD|'
                      r'CPU|GPU|SSD|HDD|RAM|PC|A/S|AS|IP|KC|G2B)\b', re.I)
_REFERENCE = re.compile(r'예시|참고|추천|가정|기존|보유|사용\s*중|호환|적용\s*(?:기종|모델)|'
                        r'소모품|부품|토너|카트리지|매뉴얼|설명서')
_EQUIVALENT = re.compile(r'동\s*(?:등|급)(?:\s*(?:또는|혹은))?(?:\s*이상)?|\bequivalent\b', re.I)
_ALLOWED = re.compile(r'허용|가능|인정|무방|포함')


def _proper(name):
    """A designation carries Latin script; bare Korean nouns are never names."""
    name = name.strip(' ._/-')
    return bool(re.search(r'[A-Za-z]', name)) and not _GENERIC.match(name)


def _allows_equivalent(text):
    for match in _EQUIVALENT.finditer(text):
        tail = text[match.end():match.end() + 40]
        if re.search(r'(?:승인|인정|허용|인수)하지\s*않|할\s*수\s*없|불가|불허|금지|미허용', tail):
            continue
        if _ALLOWED.search(tail):
            return True
    return False


def _lines(text):
    at = 0
    for raw in text.splitlines(keepends=True):
        line = raw.rstrip('\r\n')
        if line.strip():
            yield at, at + len(line), line
        at += len(raw)


def _witness(record, di, lo, hi, line, reason, identity):
    if hi - lo > 500:
        return None  # Never clip the requirement or its exception.
    quote = clean_evidence(line, record, source=(di, lo, hi))
    if not quote:
        return None
    return {'item': 9, 'value': 1, 'evidence': quote, 'source': 'named_identity_alternatives_closed',
            'reason': reason, 'identity': identity,
            'requirement_source': {'doc_index': di, 'start': lo, 'end': hi},
            'legal_exemption_inferred': False}


def exclusive_identity_check(record):
    for di, doc in enumerate(record.get('docs', [])):
        lines = list(_lines(doc['text']))
        for index, (lo, hi, line) in enumerate(lines):
            for sentence in re.finditer(r'[^.。;]+[.。;]?', line):
                unit = sentence[0]
                refused = _REFUSED.search(unit)
                if not refused or _REFERENCE.search(unit) or _allows_equivalent(unit):
                    continue
                names = [re.sub(r'(?:으로만|로만|으로|로|에|은|는|을|를|이|가|만)$', '', m['a'] or m['b'])
                         for m in _BOUND.finditer(unit[:refused.start()])]
                names = [n for n in names if _proper(n)]
                if names:
                    found = _witness(record, di, lo, hi, line,
                                     'named_identity_with_explicitly_refused_alternatives', names[-1])
                    if found:
                        return found
            match = _DESIGNATION.search(line)
            if (match and _ROW_LABEL.match(line) and _proper(match['name'])
                    and re.search(r'\d|-', match['name'])
                    and not _REFERENCE.search(line)
                    and not any(_allows_equivalent(x[2]) for x in lines[index:index + 3])):
                found = _witness(record, di, lo, hi, line,
                                 'specification_row_pins_product_to_parenthesised_model',
                                 match['name'].strip())
                if found:
                    return found
    return None


def named_or_exclusive_check(record):
    """The existing named-purchase witness first, then the closed-identity ones."""
    from .specification_purchase import named_purchase_check
    return named_purchase_check(record) or exclusive_identity_check(record)
