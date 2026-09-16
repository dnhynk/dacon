"""Shared, typed project prices for applicability, catalog and rule consumers.

No VAT conversion or base-price substitution is implicit. The official data
contract prioritizes notice amounts for applicability; all observations and
registration conflicts remain visible to the model and v24 comparator.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .comparison import amount_facts


def _described_tax_basis(observations):
    """Bind an explicit scalar-free VAT description to one exact field value.

    ``사업금액: 5억원`` and a later ``본 사업금액은 부가세가 포함된
    금액`` are two source observations of one field.  The description is not a
    second amount, but it can resolve the tax basis when there is exactly one
    otherwise-unknown whole value with the same label in the same document.
    Ambiguous repeated assignments and conflicting explicit bases stay
    unresolved.
    """
    descriptions = {(fact['doc_index'], fact['label']) for fact in observations
        if fact.get('scope') == 'nonassignment'
        and fact.get('amount_use') == 'tax_basis_description'}
    related = set()
    for key in descriptions:
        candidates = [fact for fact in observations
            if (fact['doc_index'], fact['label']) == key
            and fact.get('scope') == 'whole' and fact.get('value') is not None]
        if len(candidates) == 1 and candidates[0].get('basis') == 'unknown':
            fact = candidates[0]
            related.add((fact['doc_index'], fact['anchor_start']))
    return related


def numeric(value):
    if type(value) not in (int, float):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite() or number <= 0 or number != number.to_integral_value():
        return None
    return int(number)


def project_prices(record):
    facts = amount_facts(record)
    result = {}
    for kind, key in (('estimated_price', '입찰추정가격'), ('budget', '배정예산금액')):
        observations = [f for f in facts if f['field'] == kind]
        described_inclusive = _described_tax_basis(observations) if kind == 'budget' else set()
        body, values, notice_values = [], set(), set()
        unresolved_basis = notice_basis_unresolved = False
        unresolved_literal = notice_literal_unresolved = False
        for fact in observations:
            value = Decimal(fact['value']) if fact['value'] is not None else None
            described_basis = (fact['doc_index'], fact['anchor_start']) in described_inclusive
            effective_basis = 'including_vat' if described_basis else fact['basis']
            usable = fact['scope'] == 'whole' and value is not None
            literal_error = fact.get('literal_error') if fact['scope'] == 'unparsed' else None
            if usable and value != value.to_integral_value():
                literal_error = 'fractional_won_not_integer_project_amount'
            if literal_error:
                unresolved_literal = True
                notice_literal_unresolved |= fact['doc_type'] == '공고문'
            basis_ok = (effective_basis == 'excluding_vat' if kind == 'estimated_price'
                        else effective_basis in {'including_vat', 'unknown'})
            if usable and not basis_ok:
                unresolved_basis = True
                notice_basis_unresolved |= fact['doc_type'] == '공고문'
            usable = usable and basis_ok and value == value.to_integral_value()
            if usable:
                values.add(int(value))
                if fact['doc_type'] == '공고문':
                    notice_values.add(int(value))
            doc = record['docs'][fact['doc_index']]
            ev = {'doc_index': fact['doc_index'], 'doc_id': doc.get('doc_id'),
                  'document_role': doc['type'], 'start': fact['start'], 'end': fact['end'],
                  'text': doc['text'][fact['start']:fact['end']]}
            body.append({'won': int(value) if value is not None and value == value.to_integral_value() else None,
                         'field': fact['label'], 'vat': {'including_vat': 'included', 'excluding_vat': 'excluded'}.get(effective_basis, 'unspecified'),
                         'price_role': 'project_total_candidate' if usable else 'excluded_or_unresolved',
                         'scope': fact['scope'], 'source_context': ev, 'evidence': ev,
                         'literal_error': literal_error,
                         'basis_relation': 'same_document_unique_field_tax_description' if described_basis else None})
        meta = numeric(record.get('meta', {}).get(key))
        all_values = values | ({meta} if meta is not None else set())
        # https://dacon.io/competitions/official/236754/data : stated notice
        # values govern law/amount bands. Do not mutate meta or the v24 facts.
        notice_priority = bool(notice_values or notice_basis_unresolved or notice_literal_unresolved)
        selected = notice_values if notice_priority else all_values
        selected_basis_unresolved = notice_basis_unresolved if notice_priority else unresolved_basis
        selected_literal_unresolved = notice_literal_unresolved if notice_priority else unresolved_literal
        conflict = len(selected) > 1
        status = 'conflict' if conflict else 'unknown' if selected_basis_unresolved or selected_literal_unresolved or not selected else 'known'
        result[kind] = {'meta': {'field': key, 'won': meta}, 'body': body,
                        'candidate_values_won': sorted(selected),
                        'all_observed_values_won': sorted(all_values), 'source_conflict': len(all_values) > 1,
                        'status': status, 'value_won': next(iter(selected)) if status == 'known' else None,
                        'basis': 'body_and_meta' if values and meta is not None else 'body' if values else 'meta_only',
                        'unresolved_tax_basis': selected_basis_unresolved,
                        'all_sources_unresolved_tax_basis': unresolved_basis,
                        'unresolved_literal': selected_literal_unresolved,
                        'all_sources_unresolved_literal': unresolved_literal,
                        'effective_source': 'notice' if notice_priority else 'available_body_and_metadata',
                        'policy': 'official_notice_priority_for_applicability_keep_all_comparison_observations', 'typed_observations': observations}
    return result


def in_band(price, *, lower=0, upper=None):
    """Evaluate an invariant predicate without resolving conflicting amounts.

    Two different amounts below the same ceiling establish that band. Amounts
    straddling a boundary cannot do so. The exact price remains unresolved.
    """
    values = price.get('candidate_values_won', [])
    if not values or price.get('unresolved_tax_basis') or price.get('unresolved_literal'):
        return None
    results = {v >= lower and (upper is None or v < upper) for v in values}
    return next(iter(results)) if len(results) == 1 else None
