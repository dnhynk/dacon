"""Per-notice code/field vocabulary shared by prompts, sampler and consumer."""
from __future__ import annotations

import copy
import hashlib
import json
import re


def build(plan):
    from .catalog_condition_facts import _LABELS
    pairs = []
    for product in plan:
        program = product.get('program')
        if not program or not program['required_fields']:
            continue
        declared = [f['field'] for f in product['fields']]
        if sorted(declared) != program['required_fields'] or not set(declared) <= set(_LABELS):
            raise ValueError('Condition plan fields differ from the supplied predicate')
        pairs.extend([product['code'], field] for field in declared)
    result = {'version': 1, 'fields': sorted(pairs),
        'plan_sha256': hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True,
                                               separators=(',', ':')).encode()).hexdigest()}
    validate(result)
    return result


def validate(contract):
    from .catalog_condition_facts import _LABELS
    if (type(contract) is not dict or set(contract) != {'version', 'fields', 'plan_sha256'}
            or type(contract['version']) is not int or contract['version'] != 1
            or not isinstance(contract['plan_sha256'], str)
            or not re.fullmatch(r'[0-9a-f]{64}', contract['plan_sha256'])):
        raise ValueError('Invalid catalog field contract')
    pairs = contract['fields']
    if not isinstance(pairs, list) or not pairs:
        raise ValueError('Empty catalog field contract')
    for pair in pairs:
        if (not isinstance(pair, list) or len(pair) != 2 or type(pair[0]) is not str
                or not re.fullmatch(r'[0-9]{10}', pair[0]) or type(pair[1]) is not str or pair[1] not in _LABELS):
            raise ValueError('Invalid catalog code/field pair')
    if pairs != sorted(pairs) or len({tuple(p) for p in pairs}) != len(pairs):
        raise ValueError('Duplicated or noncanonical catalog fields')
    return pairs


def constrain(schema, contract, *, boolean_fields=()):
    """Preserve source/reference bounds while restricting each code's fields."""
    pairs = validate(contract)
    result = copy.deepcopy(schema)
    for channel, array in result['properties'].items():
        if channel not in {'findings', 'unresolved_fields', 'semantic_readings', 'permissions'}:
            continue
        base = array['items']
        nullable = 'anyOf' in base and any(v == {'type': 'null'} for v in base['anyOf'])
        if nullable:
            base = next(v for v in base['anyOf'] if v != {'type': 'null'})
        codes = {}
        for code, field in pairs:
            if channel == 'semantic_readings' and field not in boolean_fields:
                continue
            codes.setdefault(code, []).append(field)
        choices = []
        for code, fields in codes.items():
            item = copy.deepcopy(base)
            item['properties']['code'] = {'type': 'string', 'const': code}
            item['properties']['field'] = {'type': 'string', 'enum': fields}
            choices.append(item)
        if not choices:
            array.update(items=False, maxItems=0)
        elif nullable:
            array['items'] = {'anyOf': [*choices, {'type': 'null'}]}
        else:
            array['items'] = choices[0] if len(choices) == 1 else {'anyOf': choices}
    return result


def validate_prepared(packet, plan=None):
    declared = packet.get('catalog_conditions', {}).get('field_contract')
    generated = packet.get('generation', {}).get('catalog_fields')
    if declared is None and generated is None:
        return None  # Historical packets keep their original readable contract.
    if declared is None or generated is None:
        raise ValueError('Incomplete prepared catalog field contract')
    expected = build(plan if plan is not None else packet['catalog_conditions']['plan'])
    if declared != expected or generated != expected:
        raise ValueError('Prepared catalog fields differ from the current condition plan')
    return expected
