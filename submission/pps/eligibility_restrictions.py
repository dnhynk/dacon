"""Source-only item-1 restrictions with a proved operative boundary.

The model remains responsible for open-ended proportionality judgments.  This
module only promotes a positive when the notice itself establishes all parts
of a narrow relation: a competitive facility-rental task, an operative bidder
qualification, and pre-existing direct ownership with no access alternative.
"""
from __future__ import annotations

import re

from .assertions import clause, unresolved_assertion
from .data import clean_evidence
from .performance import compact, heading_role, lines


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
