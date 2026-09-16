"""Independent multiple-province applicability with explicit exception gates."""
from __future__ import annotations

from .legal_context import applicable_law
import re

from .assertions import clause, compact, unresolved_assertion, assertion_scope, has_withdrawal
from .data import clean_evidence
from .prices import in_band, project_prices
from .region_thresholds import regional_price_bounds
from .temporal import PROVINCES
from .anonymized_tokens import basic_notice_authority, province_projection

NAMES = {v: v for v in PROVINCES.values()} | {
    '강원도': '강원특별자치도', '전라북도': '전북특별자치도', '제주도': '제주특별자치도',
    '경북': '경상북도', '경남': '경상남도', '충북': '충청북도', '충남': '충청남도',
    '전북': '전북특별자치도', '전남': '전라남도'}
NAME = re.compile('|'.join(sorted(map(re.escape, NAMES), key=len, reverse=True)))
OFFICE = re.compile(r'주된\s*(?:영업소|사무소)|본점|본사')
# Some notices directly say "경북에 소재한 ... 업체" without naming a
# registered office.  In an operative bidder-qualification clause this still
# restricts the bidder's location.  Requiring the bidder noun in the same line
# keeps a delivery or performance location out of this matcher.
PROVINCE_BIDDER = re.compile(
    rf'(?:{NAME.pattern})\s*(?:에|내에)?\s*소재(?:한|하고\s*있는|해\s*있는)?'
    r'(?!\s*(?:행사장|사업장|현장|납품장소|설치장소|대상시설|공공기관|발주기관|수요기관))'
    r'[^\r\n]{0,80}(?:업체|사업자|갖춘\s*자)')
LOCATION_QUALIFICATION = re.compile(rf'(?:{OFFICE.pattern})|(?:{PROVINCE_BIDDER.pattern})')
PLACE = re.compile(r'납품지|납품장소|사업현장|공사현장|운행구간|관리대상|대상시설')
QUOTE_PROCEDURE = re.compile(
    r'수의\s*(?:계약|견적)\s*(?:안내|공고)|견적\s*(?:제출)?\s*(?:안내|공고)|'
    r'계\s*약\s*방\s*법[^\n]{0,25}수의')


def regional_competition_scope(rec):
    """A general-competition label cannot erase an actual qualification.

    Check every supplied notice before a positive early return. A contradictory
    quote procedure needs separate review, even when a different notice comes
    first. This gate alone supplies no proof of an office restriction.
    """
    if rec.get('meta', {}).get('계약방법') not in {'일반경쟁', '제한경쟁'}:
        return False
    return not any(QUOTE_PROCEDURE.search(d['text'][:3000])
                   for d in rec.get('docs', []) if d['type'] == '공고문')


def provinces(text):
    # An anonymized basic municipality is projected only through its explicitly
    # supplied province attribute. A city name such as 광주시 is not 광주광역시.
    text = province_projection(text, allowed_provinces=NAMES)
    return {NAMES[m[0]] for m in NAME.finditer(text)}


def multiple_region_check(rec):
    meta = rec.get('meta', {})
    if applicable_law(rec) not in {'국가계약법', '지방계약법'}:
        return None
    if meta.get('업무구분') not in {'물품(내자)', '일반용역'} or not regional_competition_scope(rec):
        return None
    from .input_contract import provided_complete
    if not provided_complete(rec):
        return None
    if has_withdrawal(rec, 'region'):
        return None
    bounds = regional_price_bounds(rec)
    ceiling = bounds['below_ceiling']
    if ceiling is None:
        return None
    if in_band(project_prices(rec)['estimated_price'], lower=1, upper=ceiling) is not True:
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
            target = assertion_scope(text, match.start(), match.end(), 'region')
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
                        'regional_price_bounds': bounds,
                        'source': 'supplied_state_and_local_rule_article25_3',
                        'complete_input_scanned': True}
    return None


def above_ceiling_region_check(rec):
    """Apply the supplied national/local ceiling to an actual bidder location.

    This is separate from a delivery or performance location.  The supplied
    local general-goods/service ceiling is 500 million won; construction and
    technical-service categories are outside this consumer.
    """
    meta = rec.get('meta', {})
    law = applicable_law(rec)
    price = project_prices(rec)['estimated_price']['value_won']
    bounds = regional_price_bounds(rec)
    ceiling = bounds['above_ceiling']
    if (law not in {'국가계약법', '지방계약법'} or not regional_competition_scope(rec)
            or meta.get('업무구분') not in {'일반용역', '물품(내자)'}
            or ceiling is None or price is None or price < ceiling
            or has_withdrawal(rec, 'region')):
        return None
    for doc in rec['docs']:
        if doc['type'] != '공고문':
            continue
        text = doc['text']
        for match in LOCATION_QUALIFICATION.finditer(text):
            lo, hi = clause(text, match.start(), match.end())
            target = assertion_scope(text, match.start(), match.end(), 'region')
            n = compact(target)
            if not provinces(target) or unresolved_assertion(target):
                continue
            if re.search(r'소재(?:한|하고있는|해있는)?(?:행사장|사업장|현장|납품장소|설치장소|대상시설|공공기관|발주기관|수요기관)', n):
                continue
            if not re.search(r'(?:소재|둔|두고|관할구역|내에).{0,100}(?:업체|사업자|있어야|이어야|자로제한)', n):
                continue
            evidence = clean_evidence(text[lo:hi], rec)
            if evidence:
                return {'item': 5, 'value': 1, 'evidence': evidence,
                        'reason': 'operative_bidder_location_restriction_at_or_above_applicable_ceiling',
                        'estimated_price': price, 'ceiling': ceiling,
                        'regional_price_bounds': bounds,
                        'source': ('supplied_national_decree21_1_6_rule24_2_and_notice_amount'
                                   if law == '국가계약법'
                                   else 'supplied_local_rule24_general_goods_service_ceiling')}
    return None
