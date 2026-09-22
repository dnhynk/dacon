"""Separate a supplied quote route from item-level waiver assertions.

The fixed law9/ordinance10 threshold applies to specified private contracts,
not every competitive procurement. A source claim never reclassifies a product.
"""
from __future__ import annotations

import re

from .amounts import WON, won_value
from .law_declarations import LAW, named_scopes, _reference_reason
from .legal_context import applicable_law
from .prices import project_prices
from .production_exceptions import exception_observations


def unqualified_verification_requirements(record, product, eligibility):
    """An observed bidder check can refute absence before catalog classification.

    This does not establish a product's designation or certify every item's
    document. Only a standalone, unqualified verification-and-exclusion clause
    in the current notice's eligibility section is admitted. Named/alternative
    scopes, mixed purchases and unresolved waivers keep their earlier result.
    """
    from . import sme
    if (product['uncertainty'] or len(product['products']) > 1
            or any(x['minimum_total'] > 1 for x in product.get('purchase_item_counts', []))):
        return []
    candidates = [e for e in eligibility['active_direct']
        if e.get('direct_requirement_basis') == 'mandatory_database_verification_with_exclusion'
        and e['section_role'] == 'eligibility' and not e['codes']
        and e['evidence']['document_role'] == '공고문' and not e.get('certificate_table')]
    if not candidates:
        return []
    if any(x['action'] != 'submission' for x in exception_observations(record)):
        return []
    shared_size = re.compile(sme.CLASS + r'(?:[,·ㆍ/](?:' + sme.CLASS + r'))*확인서및')
    qualified = re.compile(r'경우|조건부|한하여|한해|일부|제조사|제조업체|협력|하도급|'
        r'구성원|대표사|분담|예시|참고|가정|유효|선정후|낙찰후|계약상대자|예외')
    result = []
    for entry in candidates:
        n = sme.norm(entry['evidence']['text'])
        # A certificate list with AND may share the same verification duty;
        # an item name, OR option or participant qualifier cannot disappear.
        prefix = n[:n.find('직접생산')]
        prefix = re.sub(r'^(?:[※○●□■·ㆍ*✓\-]|\d+(?:[-.]\d+)*[.)]|[가-하][.)])*', '', prefix)
        prefix = re.sub(r'[「」『』“”"\'<>〈〉]', '', prefix)
        if prefix and not shared_size.fullmatch(prefix):
            continue
        if not re.search(r'(?:입찰|견적)(?:참가|제출)?자격(?:이|은)?없(?:습니다|음|다)[.。]?$', n):
            continue
        if any(qualified.search(sme.norm(h['text'])) for h in entry.get('heading_ancestors', [])):
            continue
        result.append({'reason': 'unqualified_bidder_production_verification_required',
            'evidence': entry['evidence'], 'catalog_identity_certified': False,
            'all_item_certificates_certified': False, 'absence_criterion_refuted': True})
    return result


_CITATION = r'제\s*(?P<article>25|26)\s*조\s*제\s*1\s*항\s*제\s*5\s*호\s*(?P<leaf>가\s*목)?'
_BASIS = re.compile(r'^[ \t]*(?:(?:[-*•※○]|\d+[.)]|[가-하][.)])[ \t]*)?'
    r'(?P<subject>(?:본|이|해당)\s*(?:입찰|계약)(?:은|는))\s*'
    + rf'(?P<law>{LAW})\s*(?:시행령|시행령[」｣])\s*{_CITATION}'
    + r'\s*에\s*(?:따라|의하여|근거하여)\s*수의\s*계약(?:으로|을)\s*'
    r'(?:체결|진행|집행)(?:합니다|한다|함)[.。]?\s*$')
_SPECIAL = re.compile(r'제\s*7\s*조\s*제\s*1\s*항\s*제\s*4\s*호')
_RELATION = re.compile(r'\s*(미만|이하|이상|초과)')


def exception_claims(record, product):
    """Keep named candidates and an amount assertion separate from proof."""
    result = []
    for observation in exception_observations(record):
        ev = observation['evidence']
        text = ev['text']
        normalized = re.sub(r'\s+', '', text)
        candidates = []
        for row in product.get('products', []):
            code, name = row.get('code', ''), row.get('name', '')
            if ((code and re.search(r'(?<!\d)'+re.escape(code)+r'(?!\d)', text)) or
                    (name and re.sub(r'\s+', '', name) in normalized)):
                candidates.append({'code': code, 'name': name})
        amounts = []
        for match in WON.finditer(text):
            left = text[:match.start()]
            if not re.search(r'추정\s*가격\s*[:：]?\s*$', left):
                continue
            relation = _RELATION.match(text[match.end():])
            value = won_value(match[0])
            if value is None:
                continue
            end = match.end()+(relation.end() if relation else 0)
            amounts.append({'value_won': int(value) if value == int(value) else None,
                'literal_error': None if value == int(value) else 'fractional_won_not_integer_amount',
                'operator': relation[1] if relation else 'exact',
                'source_scope': 'item_assertion' if candidates else 'unbound_assertion',
                'project_total_certified': False,
                'evidence': {**ev, 'start': ev['start']+match.start(), 'end': ev['start']+end,
                    'text': text[match.start():end]}})
        result.append({**observation, 'named_item_candidates': candidates, 'claimed_amounts': amounts,
            'procurement_regime_exception_claim': bool(_SPECIAL.search(text)
                and re.search(r'판로지원|구매촉진', text)),
            'catalog_designation_changed': False, 'item_identity_certified': False})
    return result


def quote_requirement(record, product, *, actual_quote):
    """A narrow affirmative, original statutory route plus whole price is needed."""
    law = applicable_law(record)
    price = project_prices(record)['estimated_price']
    claims = exception_claims(record, product)
    report = {'scope': 'specified_private_contract_direct_production_requirement',
        'status': 'unresolved', 'reason': 'operative_quote_basis_not_verified',
        'effective_law': law, 'project_estimated_price': price, 'basis_evidence': [],
        'excluded_basis_candidates': [], 'exception_claims': claims,
        'catalog_designation_changed': False, 'waiver_certified': False,
        'statutory_references': ['판로지원법 제9조제1항', '판로지원법 시행령 제10조제1항·제2항']}
    if not actual_quote:
        report['reason'] = 'actual_private_quote_not_observed'
        return report
    from .temporal import contract_fields
    # A method stated inside a quotation notice is unresolved for comparison, but it
    # is exactly the contradiction this gate must keep seeing.
    conflicting = [f for f in contract_fields(record, include_unresolved=True) if f['value'] != '수의계약']
    if conflicting:
        report.update(reason='contradictory_original_contract_method', contract_method_conflicts=conflicting)
        return report
    required_scope = {'국가계약법': 'national', '지방계약법': 'local'}.get(law)
    for di, doc in enumerate(record['docs']):
        if doc['type'] != '공고문':
            continue
        for line in re.finditer(r'[^\r\n]+', doc['text']):
            match = _BASIS.search(line[0])
            if not match:
                continue
            ev = {'doc_index': di, 'doc_id': doc.get('doc_id'), 'doc_type': doc['type'],
                'start': line.start()+match.start(), 'end': line.start()+match.end(), 'text': match[0]}
            scope = named_scopes(match['law'])
            reference = _reference_reason(doc['text'], ev['start'])
            article_ok = (scope == ['national'] and match['article'] == '26' and match['leaf']) or (
                scope == ['local'] and match['article'] == '25' and not match['leaf'])
            if reference or scope != [required_scope] or not article_ok:
                report['excluded_basis_candidates'].append({'evidence': ev,
                    'reason': reference or 'wrong_governing_law_or_statutory_subparagraph'})
            else:
                report['basis_evidence'].append(ev)
    if not report['basis_evidence'] or report['excluded_basis_candidates']:
        return report
    if any(c['procurement_regime_exception_claim'] for c in claims):
        report['reason'] = 'disclosed_procurement_regime_exception_unresolved'
        return report
    if price['status'] != 'known' or price['value_won'] is None:
        report['reason'] = 'whole_contract_estimated_price_unresolved'
        return report
    value = price['value_won']
    report.update(status='required' if value >= 10_000_000 else 'below_trigger_amount',
        reason='specified_private_contract_at_or_above_threshold' if value >= 10_000_000
            else 'specified_private_contract_below_threshold', threshold_won=10_000_000,
        item_amount_used_as_project_total=False)
    return report
