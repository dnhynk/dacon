"""Consumer-only recovery for nonoperative quotation-procedure references.

Keep the shared performance facts unchanged: they are also model inputs.
Every extracted quotation mention must be explained, and current procedure
evidence must not contradict the registered competitive method.
"""
from __future__ import annotations

import re

from .data import clean_evidence
from .performance import NOTICE_WON, compact, item_depth
from .small_quote import procedure
from .performance_predicates import mandatory as affirmative_predicate


_QUOTE = re.compile(r'수의(?:계약)?(?:견적|계약)|소액수의|'
                    r'견적(?:서)?제출(?:안내공고|및계약방법|대상용역)')
_SANCTION = re.compile(r'수의계약(?:배제|결격)(?:업체|$)|수의계약추가제한')
# A pledge naming every procurement route, and the conflict-of-interest rule
# restricting private contracts with officials' relatives (이해충돌 방지법
# 제12조), mention 수의계약 as a category; neither declares this procedure.
_ALL_ROUTES = re.compile(r'입찰(?:\([^)]{0,20}\))?(?:및|과|또는|[,·ㆍ])수의계약')
_CONFLICT_OF_INTEREST = re.compile(r'(?:이해충돌방지|방지제도운영규정)[^。]{0,60}?수의계약|'
                                   r'수의계약체결제한(?:대상자?|여부(?:확인서)?)|수의계약(?:을)?체결하려는경우')
_BIDDER = re.compile(r'(?:보유한|있는|갖춘|수행한|완료한)(?:업체|자|사업자|기관|법인|단체)'
                     r'|있어야|보유(?:하여야|해야)')


def nonoperative_quote_reference(text):
    """Account for each quote mention without erasing a current method."""
    n = compact(text)
    n = re.sub(r'수의계약시(?:배제|결격)사유에해당하는자와체결하는계약', '', n)
    if (re.search(r'직계|배우자|계열회사', n)
            and re.search(r'(?:지방계약법|법)제33조', n)
            and not re.search(r'(?:본|이|해당)(?:입찰|계약|공고)', n)):
        n = re.sub(r'체결하는수의계약\((?:지방계약법|법)제33조\)', '', n)
    n = re.sub(r'(?:경쟁)?입찰[–—\-:：][^,，;。]{0,30}(?:제출|접수)'
               r'(?:일)?(?:이전|전|까지)[,，]수의계약[–—\-:：]'
               r'계약체결(?:일)?(?:이전|전|까지)', '', n)
    n = _CONFLICT_OF_INTEREST.sub('', _ALL_ROUTES.sub('', n))
    return not _QUOTE.search(_SANCTION.sub('', n))


def _with_wrapped_line(record, evidence):
    """Read a mention cut by a line wrap together with its next line."""
    text = record['docs'][evidence['doc_index']]['text']
    if re.search(r'[.。!?]\s*$|다\s*$', evidence['text']):
        return evidence['text']
    following = re.match(r'\s*\n\s*([^\n]+)', text[evidence['end']:evidence['end'] + 300])
    if not following or item_depth(following[1]) is not None:
        return evidence['text']
    return evidence['text'] + '\n' + following[1]


def checks(record, facts, items):
    """Restore service obligations blocked by nonoperative method references."""
    wanted = set(items) & {2, 8}
    references = facts['procedure']['actual_quote_evidence']
    if (not wanted or not references
            or facts['law'] not in {'국가계약법', '지방계약법'}
            or facts['work'] != '일반용역'
            or record.get('meta', {}).get('계약방법') not in {'일반경쟁', '제한경쟁', '지명경쟁'}
            or facts['explicit_no_experience_restriction']):
        return []
    # Remove only the noun phrases describing exclusion/penalty history.
    # Another quote mention in the same unit remains a blocker.
    if not all(nonoperative_quote_reference(_with_wrapped_line(record, e)) for e in references):
        return []
    method = procedure(record)
    # An uncertain browser/submission instruction is not evidence of an
    # uncertain quotation route. Keep actual route and withdrawal conflicts.
    if method['affirmative'] or any(
            _QUOTE.search(compact(e['text']))
            or re.search(r'취소|철회|전환|변경', e['text'])
            for e in method['unresolved']):
        return []
    # The shared candidate grammar also accepts the noun "qualification".
    # A scoring-only/permission sentence can contain that noun, so restoring a
    # suppressed positive additionally needs an affirmative bidder predicate.
    mandatory = [c for c in facts['candidates'] if c['status'] == 'mandatory'
                 and (_BIDDER.search(compact(c['evidence']['text']))
                      or affirmative_predicate(compact(c['evidence']['text'])))]
    if not mandatory:
        return []
    evidence = next((q for c in mandatory if (q := clean_evidence(c['evidence']['text'], record))), '')
    if not evidence:
        return []
    price = facts['prices']['estimated_price']
    eligible = set()
    if price['status'] == 'known' and price['value_won'] is not None and 0 < price['value_won'] < NOTICE_WON:
        eligible.add(2)
    if facts['operative_regions']:
        eligible.add(8)
    return [{'item': item, 'value': 1, 'evidence': evidence,
             'source': 'competitive_service_performance_consumer',
             'reason': 'quotation_mentions_only_nonoperative_procedural_references',
             'nonoperative_quote_references': references}
            for item in sorted(wanted & eligible)]
