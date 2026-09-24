import copy

from tools.source_plan_replay import consume_saved
from tools.replay_task_context_consumer import (
    _parse_expected_baseline_changes, _verify_code_only_baseline_delta,
)


def _plan(*, model_items=(17,), fixed=None, gate='old'):
    fixed = fixed or {str(i): {'value': 0, 'evidence': '', 'reason': 'fixed', 'stage': 'source_rules'}
                      for i in range(10, 19) if i not in model_items}
    return {'version': 'source_questions_v1', 'record_sha256': 'record',
        'fixed': fixed, 'model_items': list(model_items),
        'response_dependent_source_items': {}, 'source_purchase_status': 'general',
        'source_purchase_uncertainty': [], 'condition_questions': [],
        'general_fact_branch_gate': gate, 'source_service_provider': {},
        'unknown_is_not_fixed_zero': True, 'absence_from_retrieval_is_not_proof': True}


def test_nonvisible_plan_diagnostic_can_rebind_without_changing_response(monkeypatch):
    old, current = _plan(gate='old'), _plan(gate='new')
    from submission.b4_entry import digest
    packet = {'source_questions': old, 'source_questions_sha256': digest(old),
              'batch': 'A10', 'family': 'A', 'items': [17]}
    monkeypatch.setattr('submission.pps.source_questions.plan', lambda record, knowledge: current)

    class Pipe:
        knowledge = object()
        def consume(self, record, rebound, response):
            assert rebound['source_questions'] == current
            return {'v17': 0, 'e17': ''}, ['normal']

    row, trace, receipt = consume_saved(Pipe(), {'id': 'R'}, packet, {'text': 'saved'})
    assert row['v17'] == 0 and trace == ['normal']
    assert receipt['mode'] == 'model_visible_contract_unchanged'


def test_new_code_only_plan_ignores_old_native_response(monkeypatch):
    old = _plan(model_items=(17,))
    current = _plan(model_items=(), fixed={str(i): {
        'value': int(i == 17), 'evidence': 'source' if i == 17 else '',
        'reason': 'fixed', 'stage': 'source_qualification'} for i in range(10, 19)})
    from submission.b4_entry import digest
    packet = {'source_questions': old, 'source_questions_sha256': digest(old),
              'batch': 'A10', 'family': 'A', 'items': [17]}
    monkeypatch.setattr('submission.pps.source_questions.plan', lambda record, knowledge: current)

    class Pipe:
        knowledge = object()
        def consume(self, *_):
            raise AssertionError('A newly code-only packet must not consume the old response')

    row, trace, receipt = consume_saved(Pipe(), {'id': 'R'}, packet, {'text': 'wrong'})
    assert row['v17'] == 1 and row['e17'] == 'source'
    assert trace[0]['old_native_response_unused']
    assert receipt['mode'] == 'current_code_only'


def test_changed_model_visible_partition_is_rejected(monkeypatch):
    import pytest
    old, current = _plan(model_items=(17,)), _plan(model_items=(17, 18))
    from submission.b4_entry import digest
    packet = {'source_questions': old, 'source_questions_sha256': digest(old),
              'batch': 'A10', 'family': 'A', 'items': [17]}
    monkeypatch.setattr('submission.pps.source_questions.plan', lambda record, knowledge: current)
    pipe = type('Pipe', (), {'knowledge': object()})()
    with pytest.raises(ValueError, match='incompatible'):
        consume_saved(pipe, {'id': 'R'}, packet, {'text': 'saved'})


def test_baseline_delta_requires_matching_code_only_receipt():
    changes = [{'record_id': 'R', 'field': 'v10', 'before': '1', 'after': '0'}]
    adaptations = [{'record_id': 'R', 'row': {'v10': '0', 'e10': ''},
                    'receipt': {'mode': 'current_code_only'}}]
    assert _verify_code_only_baseline_delta(changes, adaptations)


def test_baseline_delta_rejects_reinterpreted_or_unexplained_cells():
    import pytest
    changes = [{'record_id': 'R', 'field': 'v10', 'before': '1', 'after': '0'}]
    with pytest.raises(AssertionError, match='code-only'):
        _verify_code_only_baseline_delta(changes, [])
    with pytest.raises(AssertionError, match='deterministic source value'):
        _verify_code_only_baseline_delta(changes, [{'record_id': 'R', 'row': {'v10': '1'},
            'receipt': {'mode': 'current_code_only'}}])


def test_exact_baseline_change_declaration_is_structured():
    assert _parse_expected_baseline_changes(['PPS-DEV-119:v10:1:0']) == [{
        'record_id': 'PPS-DEV-119', 'field': 'v10', 'before': '1', 'after': '0'}]
