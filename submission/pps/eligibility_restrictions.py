"""Source-only item-1 restrictions with a proved operative boundary.

The model remains responsible for open-ended proportionality judgments. This
module promotes only operative bidder restrictions: closed institution types,
existing company headcount, distributed facilities, and direct venue ownership.
"""
from __future__ import annotations

import re

from .assertions import clause, unresolved_assertion
from .data import clean_evidence
from .performance import compact, heading_role, item_depth, lines, span


_FACILITY_RENTAL = re.compile(
    r'(?:교육|연수|숙박|회의|행사)?시설(?:을|를|의)?(?:임차|대관)|'
    r'(?:임차|대관)(?:하는|할|대상인)?(?:교육|연수|숙박|회의|행사)?시설'
)
_DIRECT_FACILITY = re.compile(
    r'(?:교육(?:\([^)]{1,20}\))?|연수|숙박|회의|행사)시설(?:을|를)?'
    r'(?:직접)?(?:소유|보유)(?:하고)?(?:있는|한)(?:자|업체|사업자)'
)
_ACCESS_ALTERNATIVE = re.compile(
    r'(?:소유|보유).{0,20}(?:또는|및/또는|혹은).{0,20}'
    r'(?:임차|대여|사용권|운영권|협약|확보)|'
    r'(?:임차|대여|사용권|운영권|협약|확보).{0,20}'
    r'(?:또는|및/또는|혹은).{0,20}(?:소유|보유)'
)
_REGISTERED_FACILITY = re.compile(
    r'(?:법|시행령|시행규칙).{0,80}(?:등록|허가|인가|지정|인증)|'
    r'(?:등록|허가|인가|지정|인증)(?:된|받은).{0,40}(?:시설|기관)'
)


def _competitive(record, text: str) -> bool:
    method = compact(str(record.get('meta', {}).get('계약방법') or ''))
    body = compact(text)
    if '수의계약' in method or re.search(r'소액수의|수의계약', body):
        return False
    return '경쟁' in method or bool(re.search(r'(?:제한|일반)경쟁(?:입찰)?|제한총액입찰', body))


def direct_facility_ownership_check(record):
    """Return a proved item-1 positive or ``None``.

    Requiring a usable venue is not enough.  The promoted relation requires
    the bidder to already own/hold the venue.  Registered facilities,
    ownership-or-lease alternatives, proposal scoring and forms stay outside
    this rule and therefore remain available to the normal model judgment.
    """
    if record.get('meta', {}).get('업무구분') != '일반용역':
        return None
    full_text = '\n'.join(str(doc.get('text', '')) for doc in record.get('docs', []))
    if not _competitive(record, full_text):
        return None

    task_witnesses = []
    for di, doc in enumerate(record.get('docs', [])):
        for ev in lines(doc, di):
            n = compact(ev['text'])
            if _FACILITY_RENTAL.search(n) and not unresolved_assertion(ev['text']):
                task_witnesses.append(ev)
    if not task_witnesses:
        return None

    candidates = []
    for di, doc in enumerate(record.get('docs', [])):
        role = 'unknown'
        for ev in lines(doc, di):
            n = compact(ev['text'])
            new_role = heading_role(n)
            if new_role:
                role = new_role
            match = _DIRECT_FACILITY.search(n)
            if role != 'eligibility' or not match:
                continue
            lo, hi = clause(doc['text'], ev['start'], ev['end'])
            assertion = doc['text'][lo:hi]
            assertion_n = compact(assertion)
            if (unresolved_assertion(assertion)
                    or _ACCESS_ALTERNATIVE.search(assertion_n)
                    or _REGISTERED_FACILITY.search(assertion_n)):
                continue
            evidence = clean_evidence(ev['text'], record,
                                      source=(di, ev['start'], ev['end']))
            if evidence:
                candidates.append((ev, evidence))
    if len(candidates) != 1:
        return None
    ev, evidence = candidates[0]
    return {
        'item': 1,
        'value': 1,
        'evidence': evidence,
        'reason': 'competitive_facility_rental_requires_preexisting_direct_ownership',
        'source': 'supplied_item_v1_restriction_boundary',
        'relation': {
            'task': 'facility_rental',
            'qualification': 'direct_facility_ownership',
            'access_alternative_observed': False,
            'registered_facility_route': False,
            'task_witnesses': task_witnesses,
            'qualification_witness': ev,
        },
    }


# These are relation slots, not complete notice sentences: the bidder, its
# institutional identity / existing workforce / geographic facilities, and a
# closed qualification predicate must belong to the same operative clause.
_BIDDER = re.compile(r'입찰참가자|참가업체|참여업체|견적제출자|공급업체')
_INSTITUTION = re.compile(
    r'대학(?:교)?(?!원?생)|산학협력단|(?:국[·ㆍ]?공립|정부출연|공공)연구기관|'
    r'(?:재단|사단)법인|비영리법인'
)
_CLOSED_TYPE = re.compile(
    r'(?:만|에한(?:하여|해|함)|으로한정|으로제한)(?:입찰에?|참여|참가|가능|$)|'
    r'이어야|이여야'
)
_WORKFORCE = re.compile(
    r'상시(?:근로자|고용|근무)|상근(?:하는)?(?:정규)?직원|상근인원|'
    r'정규(?:직|고용)(?:인원|직원|기술인력)?'
)
_HEADCOUNT = re.compile(r'\d+(?:,\d{3})*(?:명|인)(?:이상|을|의|임|에|보유)')
_FACILITY = re.compile(r'(?:서비스|A/S|유지보수|수리|고객서비스)센터|지사|영업소|물류창고|교육장|처리시설')
_POSSESSION = re.compile(r'보유|갖추|갖춘|설치[·ㆍ]?운영|설치하여운영|운영하고|운영중|센터가있는')
_QUALIFICATION_END = re.compile(
    r'(?:한|는|인|춘|을)(?:업체|사업자|자)(?:이어야|이여야|에한|만|[).,]|$)|'
    r'(?:있어야|갖추어야|확보하여야|보유하여야)|'
    r'(?:입찰|견적|참가|제안서).{0,35}(?:허용|가능|참여|참가|접수|인정|제외)'
)
_NON_GATE = re.compile(
    r'배점|가점|우대|평가위원|평가인력|참고모델|'
    r'(?:계약체결|낙찰|선정)(?:후|이후)|계약상대자|낙찰자(?:는|가)|'
    r'(?:필요|요구|참가요건|참가조건).{0,12}(?:없|아닙|아님|않)|'
    r'(?:보유|고용|설치|인원).{0,30}(?:무관|관계없이)|'
    r'법정(?:인력|인원|기준)|(?:면허|허가|등록)(?:유지)?기준'
)


def _qualification_units(doc, di):
    """Keep wrapped predicates together without crossing bullets or headings."""
    source = list(lines(doc, di))
    role, index = 'unknown', 0
    eligibility_depth, nested_depth = None, None
    while index < len(source):
        first = source[index]
        n = compact(first['text'])
        depth = item_depth(first['text'])
        new_role = heading_role(n)
        if re.match(r'^(?:\d+(?:[-.]\d+)*[.)]?|[가-하][.)]|[□■])?'
                    r'(?:입찰|견적(?:서)?제출)?(?:참가|참여)?자격[:：]', n):
            new_role = 'eligibility'
        # A mention of an assessment/submission in a qualification sentence
        # is not a new heading. Nested document lists end at their next sibling.
        if new_role in {'scoring', 'forms'} and re.search(
                r'하여야|이어야|합니다|따라|한합니다|가능합니다', n):
            new_role = None
        if eligibility_depth is not None and depth is not None:
            if nested_depth is not None and depth <= nested_depth:
                role, nested_depth = 'eligibility', None
            if new_role == 'other' and depth > eligibility_depth:
                new_role = None
            if new_role and new_role != 'eligibility':
                if depth > eligibility_depth:
                    nested_depth = depth
                else:
                    eligibility_depth, nested_depth = None, None
        if new_role:
            role = new_role
            if new_role == 'eligibility':
                eligibility_depth, nested_depth = depth if depth is not None else 0, None
        end = index + 1
        if not new_role:
            while end < min(index + 6, len(source)):
                previous, nxt = source[end-1], source[end]
                if (item_depth(nxt['text']) is not None
                        or heading_role(compact(nxt['text']))
                        or re.search(r'[.!?。;]\s*$', previous['text'])
                        or doc['text'][previous['end']:nxt['start']].count('\n') > 1
                        or nxt['end'] - first['start'] > 500):
                    break
                end += 1
        yield role, span(doc, di, first['start'], source[end-1]['end'])
        index = end


def _multiple_facility_regions(n, tail):
    if re.search(r'전국|(?:모든|각)(?:광역|시[·ㆍ]?도)', n):
        return True
    count = re.search(r'(\d+)개(?:이상(?:의)?)?(?:시[·ㆍ]?도|자치구|지역)', n)
    if count and int(count[1]) > 1 and re.search(r'각각|모두|각\d', n + tail):
        return True
    regions = re.findall(r'[가-힣]{2,}(?:특별시|광역시|자치도|자치시)', n)
    return len(set(regions)) > 1 and bool(re.search(r'각각|모두', n + tail))


def _distributed_facility(n):
    for facility in _FACILITY.finditer(n):
        # The geographic modifier belongs to the facility, not to a separate
        # nationwide delivery duty followed by ownership of one local centre.
        prefix = re.split(r'하며|이고|있고|이며|[.;]', n[:facility.start()])[-1][-100:]
        tail = n[facility.end():facility.end()+100]
        if (not re.search(r'납품|배송|공급하|제공하', prefix)
                and _multiple_facility_regions(prefix, tail)
                and _POSSESSION.search(facility.group() + tail)):
            return True
    return False


def eligibility_restriction_check(record):
    """Positive-only recovery for closed bidder identity/capacity conditions.

    A required licence, project staffing plan, scoring row or post-award duty
    does not establish this relation. Institutional SME exceptions that add an
    alternative route cannot prove a closed class of eligible bidders either.
    """
    existing = direct_facility_ownership_check(record)
    if existing is not None:
        return existing
    for di, doc in enumerate(record.get('docs', [])):
        for role, ev in _qualification_units(doc, di):
            n = compact(ev['text'])
            if (role in {'scoring', 'forms'}
                    or not (role == 'eligibility' or _BIDDER.search(n))
                    or unresolved_assertion(ev['text']) or _NON_GATE.search(n)):
                continue
            relation = None
            institutions = list(_INSTITUTION.finditer(n))
            if institutions:
                tail = n[institutions[-1].end():]
                # The limitation must attach to the bidder's institution type,
                # not to its past clients, employees or certification issuer.
                closed = re.match(r'(?:등)?(?:에)?(?:만|한하여|한해|한함|으로한정|으로제한|이어야|이여야)', tail)
                if (closed and _CLOSED_TYPE.search(tail)
                        and not re.search(r'실적|납품한|발주한|발급|출신|졸업', tail)
                        and not re.search(r'비영리법인.{0,20}(?:경우|해당)|예외|참가가능하며|또는일반|또는기업', n)
                        and not re.search(r'(?:중소기업|소기업|소상공인|일반기업).{0,350}(?:또는|혹은)',
                                          n[:institutions[-1].start()])):
                    relation = 'closed_bidder_institution_type'
            if (relation is None and _WORKFORCE.search(n) and _HEADCOUNT.search(n)
                    and _QUALIFICATION_END.search(n)
                    and not (re.search(r'투입|배치|수행에필요', n) and '별개' not in n)):
                relation = 'preexisting_company_headcount'
            if (relation is None and _distributed_facility(n) and _QUALIFICATION_END.search(n)
                    and not _ACCESS_ALTERNATIVE.search(n)
                    and not re.search(r'협력업체|협약|위탁가능|임차가능|서비스제공능력', n)):
                relation = 'preexisting_multi_region_facilities'
            if relation is None or len(ev['text']) > 500:
                continue
            evidence = clean_evidence(ev['text'], record, source=(di, ev['start'], ev['end']))
            if evidence:
                return {'item': 1, 'value': 1, 'evidence': evidence,
                        'reason': relation, 'source': 'supplied_item_v1_restriction_boundary',
                        'relation': {'qualification': relation, 'section': role,
                                     'qualification_witness': ev}}
    return None
