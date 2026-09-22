"""Independent multiple-province applicability with explicit exception gates."""
from __future__ import annotations

from .legal_context import applicable_law
import re

from .assertions import clause, compact, unresolved_assertion, assertion_scope, has_withdrawal
from .data import clean_evidence
from .prices import in_band, project_prices
from .region_thresholds import below_ceiling, regional_price_bounds
from .temporal import PROVINCES
from .anonymized_tokens import basic_notice_authority, province_projection

NAMES = {v: v for v in PROVINCES.values()} | {
    '강원도': '강원특별자치도', '전라북도': '전북특별자치도', '제주도': '제주특별자치도',
    '경북': '경상북도', '경남': '경상남도', '충북': '충청북도', '충남': '충청남도',
    '전북': '전북특별자치도', '전남': '전라남도'}
NAME = re.compile('|'.join(sorted(map(re.escape, NAMES), key=len, reverse=True)))
# 국가 시행령 제21조①6호 / 지방 시행령 제20조①6호 name the anchor as the
# registered head office, "개인사업자인 경우에는 사업자등록증 ... 서류에 기재된
# 사업장의 소재지".  A notice may write that anchor as 영업장 소재지 or as the
# business-registration address instead of 영업소; the legal subject is the same.
OFFICE = re.compile(r'(?:주된\s*)?영업소|주된\s*영업\s*소재지|주된\s*사무소|본점|본사|'
                    r'(?:주된?\s*)?영업장\s*(?:의\s*)?소재지|(?:주된\s*)?사업장\s*(?:의\s*)?소재지|'
                    r'사업자등록증(?:\s*상)?[^\r\n]{0,14}소재지')
# Some notices directly say "경북에 소재한 ... 업체" without naming a
# registered office.  In an operative bidder-qualification clause this still
# restricts the bidder's location.  Requiring the bidder noun in the same line
# keeps a delivery or performance location out of this matcher.
PROVINCE_BIDDER = re.compile(
    rf'(?:{NAME.pattern})\s*(?:에|내에)?\s*소재(?:한|하고\s*있는|해\s*있는)?'
    r'(?!\s*(?:행사장|사업장|현장|납품장소|설치장소|대상시설|공공기관|발주기관|수요기관))'
    r'[^\r\n]{0,80}(?:업체|사업자|갖춘\s*자)')
LOCATION_QUALIFICATION = re.compile(rf'(?:{OFFICE.pattern})|(?:{PROVINCE_BIDDER.pattern})')
PLACE = re.compile(r'납품지|납품장소|사업현장|공사현장|운행구간|관리대상|대상시설|'
                   r'(?:캠프|행사|개최)장소')
BIDDER_LOCATION = re.compile(
    r'(?:소재|위치한|위치하고있는|둔|두고|관할구역|내에|있는).{0,100}'
    r'(?:업체|사업자(?!등록)|기업|교육기관|법인|단체|있어야|이어야|(?:한|둔|는|인)자)')
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
    for doc in rec.get('docs', []):
        if doc['type'] != '공고문':
            continue
        text = doc['text'][:3000]
        for match in QUOTE_PROCEDURE.finditer(text):
            # A qualification's reference date (견적공고일 전일) does not
            # independently announce this contract's procurement procedure.
            if re.match(r'\s*(?:\(?\s*공고\s*\)?\s*)?일', text[match.end():]):
                continue
            return False
    return True


def provinces(text):
    # An anonymized basic municipality is projected only through its explicitly
    # supplied province attribute. A city name such as 광주시 is not 광주광역시.
    text = province_projection(text, allowed_provinces=NAMES)
    return {NAMES[m[0]] for m in NAME.finditer(text)}


def relative_office_scope(text):
    """Read administrative scope without inventing the authority's place name.

    This proves only a bidder-location predicate. Price, procedure, completeness
    and exception gates remain with the individual item consumers.
    """
    n = compact(text)
    if (unresolved_assertion(text) or not OFFICE.search(text)
            or re.search(r'납품|배송|행사장|사업현장|배치|동점|가점|우선순위|우선으로|'
                         r'낙찰자|계약체결후|권장|선택|두지않아도|소재하지않아도|'
                         r'전국|어느지역이든|지역(?:에|과)관계없이|업체도참가|사업자도참가', n)):
        return None
    authority = r'(?:(?:발주|수요|공고)기관(?:\([^)]*\)\])?)'
    # 국가 시행규칙 제25조③ and 지방 시행규칙 제25조③ abbreviate the province
    # level as "시ㆍ도"; a notice using that name states the same unit as 광역.
    # The basic level is also written as 자치구, 구·군, 시·군 or 시(市): the same
    # 시·군·구 unit of the authority's own locality.
    province_unit = r'(?:광역(?:자치단체|시[·ㆍ]?도)?|시[·ㆍ]도)'
    unit = (r'(?P<unit>시[·ㆍ]?군[·ㆍ]?구|자치구|구[·ㆍ]?군|군[·ㆍ]?구|시[·ㆍ]?군|시\(市\)|'
            r'기초자치단체|' + province_unit + ')')
    relation = re.search(authority + r'[^.;。]{0,35}(?:소재|위치|주소|동일|같은)[^.;。]{0,25}?' + unit, n)
    if not relation:
        return None
    # A location belongs to a bidder office, rather than the buyer's separate
    # address. Accept both nominal holder qualifications and exclusive scope
    # declarations; a mere ability to move or an optional address proves none.
    holder = re.search(
        r'(?:본점|본사|영업소)[^.;。]{0,110}(?:둔|두고있는|소재한|있는)'
        r'(?:업체|사업자|기업|자)(?:만|이어야|여야|[.(]|$|에한(?:하여|해|함|한다|합니다|정))', n)
    duty = re.search(r'(?:본점|본사|영업소)[^.;。]{0,110}(?:두어야|있어야)', n)
    exclusive = re.search(r'(?:본점|본사|영업소)(?:사업자|업체)(?:에게만|만|로한정)|'
                          r'(?:본점|본사|영업소)[^.;。]{0,110}관내(?:로|으로)한정', n)
    designation = re.search(r'본점(?:참가지역|소재지)(?:은|는).{0,120}'
                            r'중(?:어느)?(?:한곳|하나)(?:으로|로)(?:정|인정)', n)
    if not (holder or duty or exclusive or designation):
        return None
    basic = not re.fullmatch(province_unit, relation['unit'])
    multiple = not basic and bool(re.search(
        province_unit + r'(?:또는|이나|와|과).{0,25}인접.{0,10}' + province_unit + r'|'
        + province_unit + r'.{0,25}인접.{0,10}' + province_unit + r'.{0,25}중(?:어느)?(?:한곳|하나)', n))
    return {'unit': 'basic' if basic else 'province',
            'minimum_provinces': 2 if multiple else 1,
            'place_name_inferred': False}


def distributed_performance_review(rec):
    """Multiple venues plus national transport duties leave geography open.

    Names of venues are not geocoded. This is an observed exception premise
    requiring review, not a finding that the statutory exception is satisfied.
    """
    multiple_venues = national_transport = False
    for doc in rec['docs']:
        if doc['type'] not in {'과업지시서', '제안요청서'}:
            continue
        for raw in doc['text'].splitlines():
            n = compact(raw)
            venue = re.search(r'(?:캠프|행사|개최)장소[:：]([^()]+)', n)
            if (venue and re.search(r'[,、]|및', venue[1])
                    and not re.search(r'또는|예시|추후|미정', venue[1])):
                multiple_venues = True
            if (re.search(r'전국(?:단위|각지|지역).{0,60}(?:이동|운송|수송)', n)
                    and re.search(r'예약|인솔|관리|제공|수행', n)
                    and not re.search(r'예시|참고용|가정|수행하지|제외', n)):
                national_transport = True
    return multiple_venues and national_transport


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
    if below_ceiling(project_prices(rec)['estimated_price'], ceiling) is not True:
        return None
    full = '\n'.join(d['text'] for d in rec['docs'])
    if re.search(r'자격.{0,80}(?:10인|10개|십인)미만|'
                 r'(?:자격(?:업체|자)|적격업체|자격을갖춘(?:업체|자))'
                 r'(?:의?수)?(?:가|는|이)?(?:10(?:인|개|명)|십인)(?:미만|에미달)|'
                 r'시[·ㆍ]*도.{0,40}(?:신설|통합)|관할구역.{0,30}분리하지', compact(full)):
        return None  # Statutory exception or transition needs individual review.
    if distributed_performance_review(rec):
        return None
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
            relative = relative_office_scope(target)
            relative_multiple = relative and relative['minimum_provinces'] >= 2
            if (len(names)<2 and not relative_multiple) or unresolved_assertion(target):
                continue
            if not relative_multiple and not BIDDER_LOCATION.search(n):
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
                    if (len(provinces(tail) & names)>1
                            or relative_multiple and len(provinces(tail))>1
                            or re.search(r'인접.{0,15}시[·ㆍ\s]*도|걸쳐|걸친', tail)):
                        possible_exception = True
            if possible_exception:
                continue
            evidence = clean_evidence(context, rec)
            if evidence:
                return {'item': 7, 'value': 1, 'evidence': evidence,
                        'reason': 'operative_multiple_province_restriction_without_observed_exception',
                        'provinces': sorted(names), 'sufficient_price_upper_bound': ceiling,
                        'relative_scope': relative,
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
            relative = relative_office_scope(target)
            if (not provinces(target) and not relative) or unresolved_assertion(target):
                continue
            if re.search(r'소재(?:한|하고있는|해있는)?(?:행사장|사업장|현장|납품장소|설치장소|대상시설|공공기관|발주기관|수요기관)', n):
                continue
            if not relative and not BIDDER_LOCATION.search(n):
                continue
            evidence = clean_evidence(text[lo:hi], rec)
            if evidence:
                return {'item': 5, 'value': 1, 'evidence': evidence,
                        'reason': 'operative_bidder_location_restriction_at_or_above_applicable_ceiling',
                        'estimated_price': price, 'ceiling': ceiling,
                        'regional_price_bounds': bounds,
                        'relative_scope': relative,
                        'source': ('supplied_national_decree21_1_6_rule24_2_and_notice_amount'
                                   if law == '국가계약법'
                                   else 'supplied_local_rule24_general_goods_service_ceiling')}
    return None
