"""Identify explicit uses of an amount name that assign no scalar value.

These are source observations, not fabricated amounts or legal conclusions.
Unknown syntax, missing values, external definitions and malformed monetary
literals remain unresolved in the caller. A matched phrase supplies its own
predicate; a nearby VAT word alone cannot make an assignment disappear.
"""
from __future__ import annotations

import re


_VAT = r'(?:부가가치세|부가세|vat)'
_PRICE = r'(?:사업예산|사업금액|기초금액|배정예산금액|예산액|입찰금액|입찰가격)(?:\(가격입찰서금액\))?'
_BID = r'(?:입찰|투찰|견적)(?:가격|금액)'
_END = r'(?=\s|[.。;,；，|]|$)'


def nonassignment(text, start, end):
    """Return the positive syntax witness, keeping original coordinates."""
    # Keep the entire visible line so another field inside a coordinated list
    # or a definition's parentheses cannot truncate the relevant predicate.
    lo = text.rfind('\n', 0, start) + 1
    hi = text.find('\n', end)
    hi = len(text) if hi < 0 else hi
    # Do not cross a completed sentence into a different field's explanation.
    endings = list(re.finditer(r'[.。;；](?=\s|$)', text[lo:start]))
    if endings:
        lo += endings[-1].end()
    stop = re.search(r'[.。;；](?=\s|$)', text[end:hi])
    if stop:
        hi = end + stop.end()
    if hi-lo > 1200:
        return None
    left = re.sub(r'\s+', '', text[lo:start]).lower()
    tail = re.sub(r'\s+', '', text[end:hi]).lower()
    # A digit/currency assignment remains the numeric parser's responsibility.
    # Descriptive amount annotations before numbers are not scalar-free uses.
    if re.search(r'(?:\d|[일이삼사오육칠팔구영공조억만천백십])\s*원|[₩￦]|\d[\d,.]*\s*(?:조|억|만|천|백만)\s*원', text[start:hi]):
        return None
    matches = [
        ('tax_basis_description', re.match(
            r'(?:(?:및|[,·ㆍ/])' + _PRICE + r')*(?:은|는|에는)?' + _VAT +
            r'(?:를포함한|가포함된)(?:가격|금액)(?:(?:입니다|이다|임)' + _END + r'|이오니|이므로|이며)', tail)),
        ('whole_period_basis_description', re.match(
            r'(?:은|는)전체(?:사업)?기간을기준으로산출되었(?:으며|(?:다|습니다)' + _END + ')', tail)),
    ]
    # A price cap belongs to the bidder's offer. It uses the budget variable;
    # it does not provide a second numeric observation of the budget itself.
    bidder_subject = re.search(_BID + r'(?:은|는|이|가)(?:해당|당해|본)?$', left)
    if bidder_subject:
        matches.extend([
            ('bid_price_ceiling_rule', re.match(
                r'(?:\(예정가격을작성한(?:경우|경우에는)예정가격\))?'
                r'(?:범위내(?:여야|이어야)(?:한다|함)|이하인자를협상적격자로선정(?:한다|함)|'
                r'이하인자로서)', tail)),
            ('bid_price_scoring_rule', re.match(
                r'의(?:100분의\d+(?:\.\d+)?|\d+(?:\.\d+)?%)(?:미만|이하|초과|이상)인경우'
                r'.{0,120}(?:점수|평점).{0,80}(?:부여|산정|계산)', tail)),
        ])
    for reason, match in matches:
        if match:
            # A colon/equality directly attached to the anchor expresses a
            # field assignment. None of the descriptive grammars bypass it.
            return {'kind': reason, 'start': lo, 'end': hi}
    return None
