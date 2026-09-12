"""Independent multiple-province applicability with explicit exception gates."""
from __future__ import annotations

import re

from .assertions import clause, compact, unresolved_assertion, assertion_scope, has_withdrawal
from .data import clean_evidence
from .prices import in_band, project_prices
from .temporal import PROVINCES

NAMES = {v: v for v in PROVINCES.values()} | {
    '강원도': '강원특별자치도', '전라북도': '전북특별자치도', '제주도': '제주특별자치도'}
NAME = re.compile('|'.join(sorted(map(re.escape, NAMES), key=len, reverse=True)))
OFFICE = re.compile(r'주된\s*(?:영업소|사무소)|본점|본사')
PLACE = re.compile(r'납품지|납품장소|사업현장|공사현장|운행구간|관리대상|대상시설')


def provinces(text):
    # An anonymized basic municipality is projected only through its explicitly
    # supplied province attribute. A city name such as 광주시 is not 광주광역시.
    text = re.sub(r'\[지역:[^\]]+\]',
        lambda m: (re.search(r'광역=([^|\]]+)', m[0])[1]
                   if re.search(r'광역=([^|\]]+)', m[0]) else ''), text)
    return {NAMES[m[0]] for m in NAME.finditer(text)}


def multiple_region_check(rec):
    meta = rec.get('meta', {})
    if meta.get('적용계약법') not in {'국가계약법', '지방계약법'}:
        return None
    if meta.get('업무구분') not in {'물품(내자)', '일반용역'} or meta.get('계약방법') != '제한경쟁':
        return None
    if rec.get('input_completeness', {}).get('완전관측') is not True or any(rec.get('dropped_doc_counts', {}).values()):
        return None
    if has_withdrawal(rec, 'region'):
        return None
    notices = '\n'.join(d['text'] for d in rec['docs'] if d['type']=='공고문')
    # 230m is a sufficient lower bound, not an invented universal ceiling.
    # A higher local band needs an explicitly identified basic authority.
    ceiling = 230_000_000
    if meta['적용계약법']=='지방계약법' and '[수요기관(기초자치단체)]' in notices:
        ceiling = 500_000_000
    if in_band(project_prices(rec)['estimated_price'], lower=1, upper=ceiling) is not True:
        return None
    if re.search(r'수의\s*(?:계약|견적)\s*(?:안내|공고)|견적\s*제출\s*(?:안내|공고)', notices[:3000]):
        return None
    full = '\n'.join(d['text'] for d in rec['docs'])
    if re.search(r'자격.{0,80}(?:10인|10개|십인)미만|시[·ㆍ]*도.{0,40}(?:신설|통합)|관할구역.{0,30}분리하지', compact(full)):
        return None  # Statutory exception or transition needs individual review.
    for doc in rec['docs']:
        if doc['type'] != '공고문':
            continue
        text = doc['text']
        for match in OFFICE.finditer(text):
            lo, hi = clause(text, match.start(), match.end())
            context = text[lo:hi]
            target = assertion_scope(text[match.start():hi], 0, match.end()-match.start(), 'region')
            n = compact(target)
            names = provinces(target)
            if len(names)<2 or unresolved_assertion(target):
                continue
            if not re.search(r'(?:소재|둔|두고|관할구역|내에).{0,100}(?:업체|사업자|있어야|이어야|자로제한)', n):
                continue
            # A multi-province place of performance can independently establish
            # an exception. Never convert its unparsed overlap into absence.
            possible_exception = False
            for supplied in rec['docs']:
                body = supplied['text']
                for place in PLACE.finditer(body):
                    # Include wrapped field values and their table header, but
                    # stop at a new numbered section or another named duty.
                    tail = body[place.start():place.end()+600]
                    boundary = re.search(r'\n\s*(?:\d+[.)]|[가-하][.)]|입찰\s*참가자격|참가자격|본점|주된\s*영업소)', tail)
                    if boundary:
                        tail = tail[:boundary.start()]
                    if len(provinces(tail) & names)>1 or re.search(r'인접.{0,15}시[·ㆍ\s]*도|걸쳐|걸친', tail):
                        possible_exception = True
            if possible_exception:
                continue
            evidence = clean_evidence(context, rec)
            if evidence:
                return {'item': 7, 'value': 1, 'evidence': evidence,
                        'reason': 'operative_multiple_province_restriction_without_observed_exception',
                        'provinces': sorted(names), 'sufficient_price_upper_bound': ceiling,
                        'source': 'supplied_state_and_local_rule_article25_3',
                        'complete_input_scanned': True}
    return None
