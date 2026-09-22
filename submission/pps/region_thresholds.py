"""Shared, source-scoped price bounds for regional qualification items.

The local rule has more than one possible ceiling.  When the anonymized input
does not identify whether an ordinary local issuer is covered by the delegated
international-procurement notice, consumers can still use an interval:

* below the notice amount, the contract is below every possible ordinary
  local ceiling;
* at or above 500 million won, it is at or above every possible ordinary
  local ceiling;
* values between those bounds need the issuer type before v5 versus v6/v7 can
  be selected.

This keeps an unknown authority from falling back to the unrelated national
230-million-won amount.  The 2025 delegated notice is effective for the 2026
notices in this task and sets ordinary local goods/services at 350 million won.
"""
from __future__ import annotations

import re

from .anonymized_tokens import anonymous_tokens, basic_notice_authority
from .legal_context import applicable_law


NATIONAL_GOODS_SERVICES = 230_000_000
LOCAL_ORDINARY_NONCOVERED = 500_000_000
LOCAL_TECHNICAL_SERVICE = 330_000_000
LOCAL_SAFETY_SERVICE = 150_000_000


def _stated_project_amounts(price):
    """Whole-project amounts a notice states, before the tax-basis filter.

    ``project_prices`` keeps only amounts whose tax basis matches the field, so
    a single stated amount with an unresolved basis leaves no candidate at all.
    The same observations still exist; this returns them with the notice
    priority the shared policy already uses.
    """
    notice, other = [], []
    for body in price['body']:
        if body['scope'] != 'whole' or body['won'] is None or body['literal_error']:
            continue
        (notice if body['evidence']['document_role'] == '공고문' else other).append(body['won'])
    if notice:
        return notice
    meta = price['meta']['won']
    return other + ([meta] if meta is not None else [])


def below_ceiling(price, ceiling):
    """One-directional applicability: is the project amount under ``ceiling``?

    부가가치세 is an additive component of a price (국가 시행규칙 제11조), so a
    stated amount whose basis the notice leaves unresolved is still an upper
    bound of the tax-excluded 추정가격. That proves the below-ceiling side of
    the band without resolving the exact price. The at-or-above side needs a
    lower bound, which an unresolved basis does not supply, so it is reported
    only when the shared band predicate already resolves it.
    """
    from .prices import in_band
    if ceiling is None:
        return None
    decided = in_band(price, lower=1, upper=ceiling)
    if decided is not None:
        return decided
    if price.get('unresolved_literal'):
        return None
    amounts = _stated_project_amounts(price)
    if not amounts:
        return None
    return True if all(0 < won < ceiling for won in amounts) else None


def _publication_year(record):
    raw = record.get('meta', {}).get('공고게시일자')
    digits = re.sub(r'\D', '', str(raw or ''))
    if len(digits) < 4:
        return None
    year = int(digits[:4])
    return year if 2000 <= year <= 2100 else None


def _delegated_local_notice_amount(record):
    """Return the dated ordinary local notice amount supplied as static law.

    Older/undated inputs retain the previous conservative floor.  The runtime
    data for this competition is dated 2026; keeping the fallback explicit
    prevents a synthetic or malformed record from silently inheriting a law
    revision whose effective date cannot be established.
    """
    year = _publication_year(record)
    if year is not None and year >= 2025:
        return 350_000_000, 'local_delegated_notice_2025_goods_services'
    if year is not None and year >= 2021:
        return 330_000_000, 'local_delegated_notice_2021_2024_goods_services'
    return NATIONAL_GOODS_SERVICES, 'undated_legacy_sufficient_lower_bound'


def _notice_text(record):
    return '\n'.join(doc.get('text', '') for doc in record.get('docs', [])
                     if doc.get('type') == '공고문')


def _local_service_scope(record):
    """Separate the two statutory special-service bands from ordinary work."""
    if record.get('meta', {}).get('업무구분') == '물품(내자)':
        return 'ordinary'
    text = _notice_text(record)
    # A stray registration option is not enough.  Require the project term and
    # its governing statute in the supplied notice before using a lower band.
    if (re.search(r'안전점검|정밀안전진단', text)
            and re.search(r'시설물의\s*안전\s*및\s*유지관리에\s*관한\s*특별법', text)):
        return 'safety'
    if (re.search(r'건설기술|건축설계|공사감리|엔지니어링(?:기술)?', text)
            and re.search(r'건설기술\s*진흥법|건축사법|엔지니어링산업\s*진흥법', text)):
        return 'technical'
    return 'ordinary'


def _single_notice_authority(record):
    values = []
    for doc in record.get('docs', []):
        if doc.get('type') != '공고문':
            continue
        values.extend(token.value for token in anonymous_tokens(doc.get('text', ''))
                      if token.kind == 'institution' and not token.errors)
    return values[0] if values and len(set(values)) == 1 else None


def regional_price_bounds(record):
    """Return sufficient below/above bounds without resolving hidden identity."""
    law = applicable_law(record)
    if law == '국가계약법':
        return {
            'below_ceiling': NATIONAL_GOODS_SERVICES,
            'above_ceiling': NATIONAL_GOODS_SERVICES,
            'status': 'exact',
            'scope': 'national_goods_services',
        }
    if law != '지방계약법':
        return {'below_ceiling': None, 'above_ceiling': None,
                'status': 'unknown_law', 'scope': None}

    service_scope = _local_service_scope(record)
    if service_scope == 'safety':
        return {'below_ceiling': LOCAL_SAFETY_SERVICE,
                'above_ceiling': LOCAL_SAFETY_SERVICE, 'status': 'exact',
                'scope': 'local_statutory_safety_service'}
    if service_scope == 'technical':
        return {'below_ceiling': LOCAL_TECHNICAL_SERVICE,
                'above_ceiling': LOCAL_TECHNICAL_SERVICE, 'status': 'exact',
                'scope': 'local_statutory_technical_service'}

    notice_amount, notice_basis = _delegated_local_notice_amount(record)
    if basic_notice_authority(record):
        return {'below_ceiling': LOCAL_ORDINARY_NONCOVERED,
                'above_ceiling': LOCAL_ORDINARY_NONCOVERED, 'status': 'exact',
                'scope': 'local_basic_authority_ordinary_goods_services'}
    if _single_notice_authority(record) == '광역자치단체':
        return {'below_ceiling': notice_amount, 'above_ceiling': notice_amount,
                'status': 'exact', 'scope': notice_basis}
    return {
        'below_ceiling': notice_amount,
        'above_ceiling': LOCAL_ORDINARY_NONCOVERED,
        'status': 'interval_authority_unresolved',
        'scope': notice_basis,
    }
