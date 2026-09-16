from tools.replay_catalog_scope import first_accepted
from tests.test_catalog_scope_contract import response, setup


def test_replay_uses_primary_unknown_after_lossless_normalization_not_certain_retry():
    _, packet, obj, _ = setup()
    obj.update(whole_task_units=[1, 1], unresolved_scope=True, catalog_relation='unknown')
    primary = {'attempt': 0, 'response': response(obj)}
    obj.update(whole_task_units=[1], unresolved_scope=False,
               catalog_relation='outside_all_listed_service_categories')
    retry = {'attempt': 1, 'response': response(obj)}
    assert first_accepted(packet, [retry, primary]) is primary


def test_replay_keeps_unresolved_failure_instead_of_dropping_the_observation():
    _, packet, obj, _ = setup()
    obj['whole_task_units'] = [99]
    invalid = {'attempt': 0, 'response': response(obj)}
    assert first_accepted(packet, [invalid]) is invalid
