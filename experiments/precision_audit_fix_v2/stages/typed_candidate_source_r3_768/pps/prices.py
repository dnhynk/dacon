"""Shared, typed project prices for applicability, catalog and rule consumers.

No VAT conversion, base-price substitution or selection of one conflicting
source is implicit. Conflicts remain visible to the model and v24 comparator.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .comparison import amount_facts


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
        body, values = [], set()
        unresolved_basis = False
        for fact in observations:
            value = Decimal(fact['value']) if fact['value'] is not None else None
            usable = fact['scope'] == 'whole' and value is not None
            basis_ok = (fact['basis'] == 'excluding_vat' if kind == 'estimated_price'
                        else fact['basis'] in {'including_vat', 'unknown'})
            if usable and not basis_ok:
                unresolved_basis = True
            usable = usable and basis_ok and value == value.to_integral_value()
            if usable:
                values.add(int(value))
            doc = record['docs'][fact['doc_index']]
            ev = {'doc_index': fact['doc_index'], 'doc_id': doc.get('doc_id'),
                  'document_role': doc['type'], 'start': fact['start'], 'end': fact['end'],
                  'text': doc['text'][fact['start']:fact['end']]}
            body.append({'won': int(value) if value is not None and value == value.to_integral_value() else None,
                         'field': fact['label'], 'vat': {'including_vat': 'included', 'excluding_vat': 'excluded'}.get(fact['basis'], 'unspecified'),
                         'price_role': 'project_total_candidate' if usable else 'excluded_or_unresolved',
                         'scope': fact['scope'], 'source_context': ev, 'evidence': ev})
        meta = numeric(record.get('meta', {}).get(key))
        all_values = values | ({meta} if meta is not None else set())
        conflict = len(all_values) > 1
        status = 'conflict' if conflict else 'unknown' if unresolved_basis or not all_values else 'known'
        result[kind] = {'meta': {'field': key, 'won': meta}, 'body': body,
                        'candidate_values_won': sorted(all_values),
                        'status': status, 'value_won': next(iter(all_values)) if status == 'known' else None,
                        'basis': 'body_and_meta' if values and meta is not None else 'body' if values else 'meta_only',
                        'unresolved_tax_basis': unresolved_basis,
                        'policy': 'same_field_same_scope_conflict_abstention', 'typed_observations': observations}
    return result


def in_band(price, *, lower=0, upper=None):
    """Evaluate an invariant predicate without resolving conflicting amounts.

    Two different amounts below the same ceiling establish that band. Amounts
    straddling a boundary cannot do so. The exact price remains unresolved.
    """
    values = price.get('candidate_values_won', [])
    if not values or price.get('unresolved_tax_basis'):
        return None
    results = {v >= lower and (upper is None or v < upper) for v in values}
    return next(iter(results)) if len(results) == 1 else None
