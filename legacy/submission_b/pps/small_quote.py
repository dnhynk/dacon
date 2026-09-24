"""A bounded v13 exception proof, separate from direct-production waivers.

Supplied SME decree7(1)1/7(2), national decree26(1)5(a)2/3 and local
decree25(1)5(b)/(d). An observed small-quotation procedure, disclosed amount
route and ordinary small/micro eligibility must agree. No model facts or I/O.
"""
from __future__ import annotations

import re

from .assertions import unresolved_assertion
from .legal_context import applicable_law


def compact(text):
    return re.sub(r'\s+', '', str(text))


_QUOTE = re.compile(r'소액(?:\(총액\))?수의|수의(?:계약|견적)')
# A bounded numbering grammar must not repartition a long identifier into an
# unbounded repeated digit group; account/product IDs occur in real notices.
_PREFIX = r'^[○●□■❍•·ㆍ※-]*(?:(?:\d{1,3}(?:\.\d{1,3}){0,4})[.)]?|[가-하][.)])?'
_FIELD = re.compile(_PREFIX+r'(?:입찰방법|입찰방식|계약방법)[:：|]?(?P<value>.+)$')
_CURRENT = re.compile(_PREFIX+r'(?:본|이|해당)(?:입찰|계약|견적|공고)(?:은|는|의계약방법은)')
_COMPETITIVE = re.compile(r'(?:일반|제한|지명)경쟁(?:입찰)?')
_UNSAFE = re.compile(r'예시|작성예|참고|가정|법률|시행령|준용|유찰|경우|가능|할수|'
                     r'하도급|수급인|수급자|계약상대자|지난|이전|종전|당초|철회|취소|삭제|'
                     r'아니|아닌|아님|아닙|아닐|하지않|미적용|불확실|미확정|별도계약|일부품목')
_TITLE_END = re.compile(r'(?:견적(?:서)?(?:제출)?(?:안내)?|안내|입찰)?공고(?:문)?'
                        r'(?:\([^\n]{1,15}\)|\[[^\n]{1,120}\])?$')
_HEADING = re.compile(r'^\d{1,2}[.)](?!\d).{1,60}$')


def procedure(record):
    """Keep exact evidence; a keyword in a rule, form or sanction is no proof."""
    affirmative, conflicts, unresolved = [], [], []
    for di, doc in enumerate(record.get('docs') or []):
        text = doc.get('text') or ''
        before_section = True
        prior = []
        for line in re.finditer(r'[^\r\n]+', text):
            n = compact(line[0])
            ev = {'doc_index':di, 'doc_id':doc.get('doc_id'), 'document_role':doc.get('type'),
                  'start':line.start(), 'end':line.end(), 'text':line[0]}
            field, current = _FIELD.fullmatch(n), _CURRENT.match(n)
            # A preceding example/reference caption cannot lend an operative
            # role to its next line. Broader damaged hierarchies stay unresolved.
            parent_uncertain = any(re.search(r'예시|작성예|참고|가정|서식|종전공고', p)
                                   for p in prior[-2:])
            withdrawal = re.match(r'^(?:본|이|해당)(?:입찰|계약|공고)(?:을|를|은|는)'
                r'.{0,40}(?:취소|철회|전환|변경)',n) or re.match(
                r'^수의(?:계약|견적)(?:안내)?공고(?:문)?(?:을|를|은|는).{0,20}(?:취소|철회)',n)
            if withdrawal and not parent_uncertain:
                unresolved.append(ev)
            elif field or current:
                if parent_uncertain:
                    unresolved.append(ev)
                elif _QUOTE.search(n):
                    if (_UNSAFE.search(n) or unresolved_assertion(n)
                            or re.search(r'또는|혹은|선택|일반경쟁|지명경쟁|제한경쟁입찰',n)):
                        unresolved.append(ev)
                    elif doc.get('type') == '공고문':
                        affirmative.append({**ev, 'basis':'current_method_declaration'})
                elif _COMPETITIVE.search(n):
                    if not _UNSAFE.search(n) and not unresolved_assertion(n):
                        conflicts.append(ev)
            elif (doc.get('type') == '공고문' and before_section and line.start() < 2000
                  and len(n) < 220 and _QUOTE.search(n) and _TITLE_END.search(n)):
                if not parent_uncertain and not _UNSAFE.search(n) and not unresolved_assertion(n):
                    affirmative.append({**ev, 'basis':'notice_title'})
            if _HEADING.fullmatch(n):
                before_section = False
            prior = [*prior[-1:],n]
    return {'affirmative':affirmative, 'conflicts':conflicts, 'unresolved':unresolved,
            'source_order_changed':False}


def review(record, estimate, allowed):
    """Only the standard small+micro route can clear v13, never another item.

The estimate is the shared, whole-contract estimated-price resolution already
used by the caller. An unknown/conflicting/invalid amount cannot enable a rule.
Registration of an exception is corroboration, never the sole source proof.
"""
    result = {'status':'unresolved', 'reason':'small_quote_route_not_proven',
              'item':13, 'evidence':[], 'direct_production_waiver_certified':False}
    meta = record.get('meta') or {}
    law = applicable_law(record)
    if law not in {'국가계약법','지방계약법'} or meta.get('업무구분') not in {'물품(내자)','일반용역'}:
        return {**result, 'reason':'contract_law_or_purchase_kind_unresolved'}
    if isinstance(estimate, bool) or estimate is None or not 0 < estimate <= 100_000_000:
        return {**result, 'reason':'outside_known_small_quote_amount'}
    if set(allowed or []) != {'small','micro'}:
        return {**result, 'reason':'standard_small_and_micro_eligibility_not_proven'}
    if meta.get('계약방법') != '수의계약':
        return {**result, 'reason':'actual_and_registered_method_not_corroborated'}
    method = procedure(record)
    result['procedure'] = method
    if not method['affirmative'] or method['conflicts'] or method['unresolved']:
        return {**result, 'reason':'operative_quote_method_missing_or_conflicting'}
    # The supplied SME decree requires the reason in the notice or electronic
    # procurement system. Do not invent disclosure from eligibility alone.
    reason = compact(meta.get('조항호내용') or '')
    below = estimate <= 20_000_000
    disclosed = (bool(re.match(r'추정가격(?:이)?(?:2천만원|2000만원|20,?000,?000원)이하', reason)) if below
                 else bool(re.match(r'추정가격(?:이)?(?:2천만원|2000만원)초과(?:1억원|10000만원)이하', reason)
                           and '소기업' in reason and '소상공인' in reason))
    if (not disclosed or unresolved_assertion(reason)
            or re.search(r'미적용|아니|없음|경우|가능|취소|철회|종전|이전|참고',reason)):
        return {**result, 'reason':'matching_small_quote_reason_not_disclosed'}
    reference = ('국가계약법 시행령 제26조제1항제5호가목'+('2)' if below else '3)')
                 if law == '국가계약법' else '지방계약법 시행령 제25조제1항제5호'+('나목' if below else '라목'))
    return {**result, 'status':'permitted_small_size_route',
            'reason':'documented_small_quote_with_standard_size_eligibility',
            'law':law, 'estimated_price_won':estimate, 'amount_route':'at_most20m' if below else 'over20m_at_most100m',
            'disclosure':{'source':'meta.조항호내용','text':meta['조항호내용']},
            'references':['판로지원법 시행령 제7조제1항제1호 및 제2항',reference],
            'evidence':method['affirmative']}
