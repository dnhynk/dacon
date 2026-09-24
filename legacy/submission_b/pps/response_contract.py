"""Deterministic JSON boundary: ambiguous model answers are never guessed."""
from __future__ import annotations

import json


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('Non-finite JSON constant: ' + value)


def loads(text):
    if not isinstance(text, str):
        raise ValueError('Model JSON must be a string')
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def validate_items(items):
    if (not items or any(type(k) is not int or not 1 <= k <= 24 for k in items)
            or len(items) != len(set(items))):
        raise ValueError('Items must be unique integers in 1..24')
