"""Source-backed applicant locations, preserving roles and exception uncertainty."""
from __future__ import annotations
import re
from .assertions import compact, unresolved_assertion, assertion_scope, has_withdrawal, CONNECTIVE
from .data import clean_evidence
from .prices import in_band, project_prices
from .temporal import PROVINCES

NAMES = {v: v for v in PROVINCES.values()} | {
    '강원도': '강원특별자치도', '전라북도': '전북특별자치도', '제주도': '제주특별자치도'}
NAME = re.compile('|'.join(sorted(map(re.escape, NAMES), key=len, reverse=True)))
OFFICE = re.compile(r'주된\s*(?:영업\s*(?:소(?:재지)?)?|사무\s*(?:소|실))|본\s*점|본사')
PLACE = re.compile(r'납품\s*(?:지(?!시)|장소)|(?:사업|공사|용역)\s*현장|운행\s*구간|관리\s*대상|대상\s*시설|'
                   r'(?:과업|용역|수행)(?:의)?\s*(?:장소|위치|지역|대상위치)|전국\s*\d+\s*개\s*권역')
BOUNDARY = re.compile(r'[.。;；](?=\s|$)|\n\s*\n|\n[ \t]*(?:\d+[.)]|[가-하][.)]|[①-⑳※□○]|\*(?=\s))|'
                      r'\n[ \t]*(?=공동\s*도급사|공동\s*수급체|대표사|대표자)')


def locations(text, *, allow_abbreviations=False):
    """A basic-region token stays basic; its parent is a separate projection."""
    found, masked = [], list(text)
    for m in re.finditer(r'\[지역:([^|\]]+)([^\]]*)\]', text):
        attrs = dict(re.findall(r'([가-힣]+)=([^|\]]+)', m[2]))
        found.append({'raw': m[0], 'id': m[1], 'unit': attrs.get('단위', '미상'),
                      'province': NAMES.get(attrs.get('광역')), 'start': m.start(), 'end': m.end()})
        masked[m.start():m.end()] = ' ' * (m.end() - m.start())
    for m in NAME.finditer(''.join(masked)):
        if any(x['province'] == NAMES[m[0]] and 0 <= x['start'] - m.end() <= 3 for x in found):
            continue
        found.append({'raw': m[0], 'id': None, 'unit': '광역', 'province': NAMES[m[0]],
                      'start': m.start(), 'end': m.end()})
    if allow_abbreviations:
        aliases = {'서울': '서울특별시', '인천': '인천광역시', '경기': '경기도', '강원': '강원특별자치도',
                   '대구': '대구광역시', '경북': '경상북도', '경남': '경상남도', '부산': '부산광역시',
                   '울산': '울산광역시', '광주': '광주광역시', '전남': '전라남도', '전북': '전북특별자치도',
                   '충남': '충청남도', '충북': '충청북도', '대전': '대전광역시', '세종': '세종특별자치시', '제주': '제주특별자치도'}
        for m in re.finditer(r'(?<![가-힣])(?:' + '|'.join(aliases) + r')(?![가-힣])', ''.join(masked)):
            if not any(x['start'] <= m.start() < x['end'] for x in found):
                found.append({'raw': m[0], 'id': None, 'unit': '광역약칭', 'province': aliases[m[0]],
                              'start': m.start(), 'end': m.end()})
    return sorted(found, key=lambda x: x['start'])


def provinces(text):
    return {x['province'] for x in locations(text) if x['province']}


def span(text, start, end):
    boundaries = list(BOUNDARY.finditer(text))
    left = max((m.end() for m in boundaries if m.end() <= start), default=0)
    right = min((m.start() for m in boundaries if m.start() >= end), default=len(text))
    return max(left, start - 500), min(right, end + 900)


def evidence(di, doc, start, end):
    return {'doc_index': di, 'doc_id': doc.get('doc_id'), 'doc_type': doc['type'],
            'start': start, 'end': end, 'text': doc['text'][start:end]}


def office_span(text, start, end, lo, hi):
    """Keep the office's connected clause as an exact source interval.

    Contact offices, certificate issuers and other explicit subjects in a
    following clause cannot supply applicant locations. Polarity is checked
    separately on the broader assertion so a later withdrawal still applies.
    """
    cuts = [(lo, lo)] + [(m.start(), m.end()) for m in CONNECTIVE.finditer(text, lo, hi)] + [(hi, hi)]
    for left, right in zip(cuts, cuts[1:]):
        if left[1] <= start and end <= right[0]:
            return left[1], right[0]
    return lo, hi


def region_facts(rec):
    offices, places, exceptions, roles = [], [], [], []
    seen = set()
    for di, doc in enumerate(rec.get('docs', [])):
        text = doc['text']
        for m in OFFICE.finditer(text):
            outer_lo, outer_hi = span(text, m.start(), m.end())
            lo, hi = office_span(text, m.start(), m.end(), outer_lo, outer_hi)
            q = text[lo:hi]
            key = (di, lo, hi)
            if key in seen:
                continue
            seen.add(key)
            n = compact(q)
            geographic = locations(q)
            if not geographic:
                continue
            operative = bool(re.search(r'(?:소재|두고|두어|둔|위치|내에|관할구역).{0,180}'
                                       r'(?:업체|사업자|자여야|있는자|있어야|이어야|하여야|해야)', n))
            if re.search(r'문의|접수처|제출장소|전화', n) and not re.search(r'참가자격|소재.{0,120}업체|있어야', n):
                operative = False
            # A representative/member may be named in the preceding connected
            # clause; keep that role without importing its locations.
            prefix = text[outer_lo:m.start()]
            role = ('joint_member' if re.search(r'공동도급사|공동수급체.{0,20}구성원', prefix)
                    else 'joint_representative' if re.search(r'대표(?:사|자|\[담당자\])', prefix)
                    else 'bidder')
            if role != 'bidder' and re.search(r'소재(?:하여야|해야|$)', n):
                operative = True
            polarity_scope = assertion_scope(text[outer_lo:outer_hi], m.start() - outer_lo,
                                             m.end() - outer_lo, 'region')
            offices.append({'role': role, 'operator': 'OR' if '또는' in q else 'ENUMERATED',
                            'branch_office_included': bool(re.search(r'(?:지점|지사|대리점).{0,15}포함', q)),
                            'locations': geographic, 'provinces': sorted({x['province'] for x in geographic if x['province']}),
                            'operative': operative and not unresolved_assertion(polarity_scope),
                            'unresolved_assertion': unresolved_assertion(polarity_scope),
                            'anchor': m[0], 'evidence': evidence(di, doc, lo, hi)})
        seen_places = set()
        for m in PLACE.finditer(text):
            lo, hi = span(text, m.start(), m.end())
            if not locations(text[lo:hi], allow_abbreviations=True) and re.search(r'(?:위치|장소|지역)\s*[:：]?\s*$', text[lo:hi]):
                value = re.match(r'\s*([^\r\n]{1,220})', text[hi:])
                if value and locations(value[1], allow_abbreviations=True):
                    hi += value.end()
            if (lo, hi) in seen_places:
                continue
            seen_places.add((lo, hi))
            q = text[lo:hi]
            geographic = locations(q, allow_abbreviations=True)
            names = sorted({x['province'] for x in geographic if x['province']})
            nationwide = bool(re.search(r'전국\s*\d+\s*개\s*권역|전국.{0,50}(?:공연|순회|운행)', compact(q)))
            adjacent = bool(re.search(r'인접.{0,15}시[·ㆍ\s]*도|인근\s*일대', q))
            unknown = any(x['province'] is None for x in geographic)
            places.append({'provinces': names, 'locations': geographic, 'nationwide': nationwide,
                           'adjacent_extent_unresolved': adjacent, 'province_unresolved': unknown,
                           'possible_multi_province': len(names) > 1 or nationwide or adjacent,
                           'evidence': evidence(di, doc, lo, hi)})
        for m in re.finditer(r'공동\s*수급체|공동\s*도급사|대표사|서점\s*매장', text):
            lo, hi = span(text, m.start(), m.end())
            q = text[lo:hi]
            geographic = locations(q)
            if geographic:
                roles.append({'role': 'local_shop' if '서점' in m[0] else 'joint_representative' if '대표사' in m[0] else 'joint_participant',
                              'locations': geographic, 'evidence': evidence(di, doc, lo, hi)})
        seen_exceptions = set()
        for m in re.finditer(r'자격|시[·ㆍ\s]*도|관할구역', text):
            lo, hi = span(text, m.start(), m.end())
            # Whitespace can wrap a supplier count or a table value; numbered
            # sections and sentence boundaries must still stop the match.
            tail = compact(text[m.start():hi])
            if re.match(r'자격.{0,100}(?:10인|10개|십인)미만|'
                        r'시[·ㆍ]*도.{0,40}(?:신설|통합)|관할구역.{0,30}분리하지', tail):
                if (lo, hi) not in seen_exceptions:
                    seen_exceptions.add((lo, hi))
                    exceptions.append(evidence(di, doc, lo, hi))
    return {'office_requirements': offices, 'places_of_performance': places,
            'separate_role_conditions': roles,
            'explicit_exception_candidates': exceptions,
            'provided_input_complete': rec.get('input_completeness', {}).get('완전관측') is True
                                      and not any(rec.get('dropped_doc_counts', {}).values()),
            'withdrawal_observed': has_withdrawal(rec, 'region'),
            'legal_exception_absence_certified': False}


def region_analysis(rec):
    facts = region_facts(rec)
    def stop(reason):
        return {'decision': None, 'reason': reason, 'facts': facts}
    meta = rec.get('meta', {})
    if meta.get('적용계약법') not in {'국가계약법', '지방계약법'}:
        return stop('contract_law_unresolved')
    if meta.get('업무구분') not in {'물품(내자)', '일반용역'} or meta.get('계약방법') != '제한경쟁':
        return stop('outside_restricted_competitive_goods_or_services')
    if not facts['provided_input_complete'] or facts['withdrawal_observed']:
        return stop('incomplete_or_withdrawn_requirement')
    notices = '\n'.join(d['text'] for d in rec['docs'] if d['type'] == '공고문')
    ceiling = 230_000_000
    if meta['적용계약법'] == '지방계약법' and re.search(r'\[수요기관\(기초자치단체\)(?:\||\])', notices):
        ceiling = 500_000_000
    facts['estimated_price'] = project_prices(rec)['estimated_price']
    if in_band(facts['estimated_price'], lower=1, upper=ceiling) is not True:
        return stop('price_band_unresolved_or_outside_scope')
    if re.search(r'수의\s*(?:계약|견적)\s*(?:안내|공고)|견적\s*제출\s*(?:안내|공고)', notices[:3000]):
        return stop('observed_quotation_procedure')
    if facts['explicit_exception_candidates']:
        return stop('explicit_exception_requires_review')
    eligible = [x for x in facts['office_requirements'] if x['operative'] and len(x['provinces']) > 1
                and x['evidence']['doc_type'] == '공고문']
    if not eligible:
        return stop('no_operative_multiple_province_bidder_requirement')
    if (any(x['role'] != 'bidder' for x in facts['office_requirements'])
            or any(x['role'].startswith('joint_') for x in facts['separate_role_conditions'])):
        return stop('joint_roles_require_separate_applicability_review')
    if any(x['possible_multi_province'] for x in facts['places_of_performance']):
        return stop('multi_province_or_unresolved_adjacent_performance')
    for requirement in eligible:
        context = requirement['evidence']['text']
        quote = clean_evidence(context, rec)
        if not quote or provinces(quote) != set(requirement['provinces']):
            continue
        decision = {'item': 7, 'value': 1, 'evidence': quote,
                    'reason': 'operative_multiple_province_restriction_without_observed_exception',
                    'provinces': requirement['provinces'], 'locations': requirement['locations'],
                    'role': requirement['role'], 'sufficient_price_upper_bound': ceiling,
                    'source': 'supplied_state_and_local_rule_article25_3', 'complete_input_scanned': True,
                    'exception_review_scope': 'provided_source_patterns_only',
                    'legal_exception_absence_certified': False}
        return {'decision': decision, 'reason': decision['reason'], 'facts': facts}
    return stop('supported_requirement_but_evidence_window_unavailable')


def multiple_region_check(rec):
    return region_analysis(rec)['decision']
