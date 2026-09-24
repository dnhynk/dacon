"""Explicit source decisions and optional no-op reviews are different skips."""
from __future__ import annotations


def validate(packet, entry):
    if entry.get('model_called') is not False:
        raise ValueError('Skipped request cannot contain a model call')
    if entry.get('rule') == 'all_A10_items_source_fixed':
        from .pps.source_questions import validate_structure
        plan = validate_structure(packet)
        if (plan['model_items'] or entry.get('prediction_inferred') is not True
                or entry.get('normality_from_missing_response') is not False
                or entry.get('source_questions_sha256') != packet['source_questions_sha256']):
            raise ValueError('Invalid code-only A10 decision')
        return 'source_A10'
    if (packet.get('batch') != 'S9' or entry.get('prediction_inferred') is not False
            or entry.get('shared_v9') == 1 or entry.get('candidate_count') != 0
            or packet['specification_inventory']['candidates']):
        raise ValueError('Invalid frozen specialist skip decision')
    return 'optional_S9'


def consume(record, packet, entry, knowledge):
    kind = validate(packet, entry)
    if kind == 'source_A10':
        from .pps.source_questions import code_only
        row, current = code_only(record, packet, knowledge)
        if current != entry:
            raise ValueError('Frozen code-only decision changed')
    else:
        row = {}
    return row, [{'source': 'frozen_execution_skip', 'decision': entry}]
