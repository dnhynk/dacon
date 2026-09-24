"""No partial-format or best-per-record score may conceal scope abstentions."""
import copy
import pytest

from tools.score_catalog_scope import join_arm


def baseline():
    return [{'id': 'a', 'v10': '1', 'e10': '', 'v14': '0', 'e14': '', 'v20': '1', 'e20': ''},
            {'id': 'b', 'v10': '0', 'e10': '', 'v14': '1', 'e14': 'original', 'v20': '0', 'e20': ''}]


def observation(rid, row=None, error=None):
    return {'request_key': 'scope:'+rid+':current', 'record_id': rid, 'row': row,
            'parse_error': error, 'raw_values': None, 'model_judgment_present': False}


def test_semantic_abstention_preserves_baseline_and_only_computed_fields_change():
    before = baseline()
    saved = copy.deepcopy(before)
    result, report = join_arm(before, [observation('a'), observation('b', {'v10': 1, 'e10': ''})], {'a', 'b'})
    assert before == saved and result[0] == before[0]
    assert result[1]['v10'] == '1' and result[1]['v14'] == '1' and result[1]['e14'] == 'original'
    assert report['abstentions'] == 1 and report['computed_notices'] == 1


def test_one_unresolved_format_withholds_the_entire_arm():
    result, report = join_arm(baseline(), [observation('a', error='invalid JSON'), observation('b')], {'a', 'b'})
    assert result is None and report['status'] == 'withheld_unresolved_format'


@pytest.mark.parametrize('observations', [[], [observation('a')], [observation('a'), observation('a')]])
def test_missing_or_duplicate_results_cannot_be_dropped_from_the_denominator(observations):
    with pytest.raises(ValueError, match='exactly one'):
        join_arm(baseline(), observations, {'a', 'b'})


@pytest.mark.parametrize('proposal', [{'v20': 0, 'e20': ''}, {'v10': 0}, {'v10': True, 'e10': ''}])
def test_scope_cannot_change_another_item_or_accept_malformed_computed_fields(proposal):
    with pytest.raises(ValueError):
        join_arm(baseline(), [observation('a', proposal)], {'a'})
