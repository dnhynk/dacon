"""Repeated reads are idempotent; malformed identities and claims are not."""
import copy
import json

import pytest

from submission.b4_entry import parse_error
from submission.pps.catalog_scope import decode_response, review
from tests.test_catalog_scope_contract import response, setup


def test_duplicate_source_reads_have_the_same_decision_and_preserved_raw_receipt():
    rec, packet, obj, knowledge = setup()
    expected, original = review(rec, response(obj), packet, knowledge)
    duplicated = copy.deepcopy(obj)
    duplicated['whole_task_units'] = [1, 1, 1]
    raw = response(duplicated)
    before = copy.deepcopy(raw)
    assert parse_error(packet, raw) is None
    actual, log = review(rec, raw, packet, knowledge)
    assert actual == expected and log['model_scope'] == original['model_scope']
    assert raw == before
    assert log['reference_normalization']['changes'] == [
        {'path': 'whole_task_units', 'original': [1, 1, 1], 'canonical': [1]}]
    assert log['reference_normalization']['semantic_fields_changed'] is False


def test_unknown_and_conflicting_links_are_never_repaired_by_set_normalization():
    rec, packet, obj, knowledge = setup()
    obj.update(unresolved_scope=True, catalog_relation='unknown', whole_task_units=[1, 1])
    obj['relationships'] = [{'code': '8014190201', 'role': role, 'source_units': [1, 1]}
                            for role in ('whole', 'component')]
    canonical, receipt = decode_response(response(obj)['text'], packet['spans'])
    assert canonical['unresolved_scope'] is True
    assert canonical['catalog_relation'] == 'unknown'
    assert [link['role'] for link in canonical['relationships']] == ['whole', 'component']
    assert len(receipt['changes']) == 3
    assert review(rec, response(obj), packet, knowledge)[0] is None
    canonical['unresolved_scope'] = False
    _, log = review(rec, response(canonical), packet, knowledge)
    assert log['gate'] == 'unrecognized_or_conflicting_catalog_links'


@pytest.mark.parametrize('refs', [[1, True], [1, 1.0], [1, 0], [1, -1], [1, 2], ['1', '1'], [1] * 13])
def test_only_exact_valid_bounded_source_repetitions_can_be_normalized(refs):
    _, packet, obj, _ = setup()
    obj['whole_task_units'] = refs
    assert parse_error(packet, response(obj)) is not None


def test_duplicate_json_properties_are_still_rejected_before_normalization():
    _, packet, obj, _ = setup()
    text = json.dumps(obj)[:-1] + ',"unresolved_scope":true}'
    assert 'Duplicate JSON key' in parse_error(packet, {'finish_reason': 'stop', 'text': text})


def test_normalization_does_not_allow_a_different_packet_item_contract():
    _, packet, obj, _ = setup()
    packet['items'] = [20]
    obj['whole_task_units'] = [1, 1]
    assert parse_error(packet, response(obj)) is not None
