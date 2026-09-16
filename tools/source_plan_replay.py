"""Conservative adapter for saved A10 responses after source-plan evolution.

This is replay tooling, not submission behavior.  It permits a saved response
only when the model-visible source-question contract is unchanged, or when the
current plan has become wholly deterministic and therefore needs no response.
It never projects an old response onto a new or reordered set of model items.
"""
from __future__ import annotations

import copy


def _model_visible(plan):
    """Fields consumed by ``source_questions._guidance``."""
    return {
        'fixed': plan['fixed'],
        'model_items': plan['model_items'],
        'source_purchase_status': plan['source_purchase_status'],
        'source_purchase_uncertainty': plan['source_purchase_uncertainty'],
        'condition_questions': plan['condition_questions'],
    }


def consume_saved(pipe, record, packet, response):
    """Consume one frozen response, returning an explicit adaptation receipt."""
    if 'source_questions' not in packet:
        row, trace = pipe.consume(record, packet, response)
        return row, trace, None

    from submission.b4_entry import digest
    from submission.pps.source_questions import (fixed_row, plan,
                                                   validate_structure)

    old = validate_structure(packet)
    current = plan(record, pipe.knowledge)
    if old == current:
        row, trace = pipe.consume(record, packet, response)
        return row, trace, None

    receipt = {
        'record_id': record['id'],
        'old_plan_sha256': digest(old),
        'current_plan_sha256': digest(current),
        'old_model_items': old['model_items'],
        'current_model_items': current['model_items'],
        'saved_response_reinterpreted': False,
    }
    if not current['model_items']:
        # Current production would skip this call.  The old native response is
        # retained as evidence but contributes no value to the current row.
        row = fixed_row(current)
        trace = [{'source': 'current_source_plan_became_code_only',
                  'fixed': current['fixed'],
                  'old_native_response_unused': True,
                  'current_source_questions_sha256': receipt['current_plan_sha256']}]
        return row, trace, {**receipt, 'mode': 'current_code_only'}

    if _model_visible(old) == _model_visible(current):
        # Non-prompt diagnostics (for example, a newly available deterministic
        # branch gate) may change while the exact requested items and guidance
        # stay fixed.  Rebind only the integrity receipt before normal consume.
        rebound = copy.deepcopy(packet)
        rebound['source_questions'] = current
        rebound['source_questions_sha256'] = digest(current)
        row, trace = pipe.consume(record, rebound, response)
        return row, trace, {**receipt, 'mode': 'model_visible_contract_unchanged'}

    raise ValueError(
        'Saved source-question response is incompatible with the current model-visible plan: '
        + record['id'])
