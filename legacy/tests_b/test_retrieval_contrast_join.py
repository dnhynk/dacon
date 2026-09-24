import copy
import pytest
from tools.score_retrieval_contrast import join_items


def example():
    baseline = [{'id': 'notice', 'v11': '0', 'e11': '', 'v20': '1', 'e20': '원래 근거'}]
    packets = [{'request_key': 'a', 'record_id': 'notice', 'arm': 'control', 'items': [11]},
               {'request_key': 'b', 'record_id': 'notice', 'arm': 'control', 'items': [20]}]
    results = [{**p, 'parse_error': None, 'row': {f'v{p["items"][0]}': 1, f'e{p["items"][0]}': '새 원문'}} for p in packets]
    return baseline, packets, results


def test_disjoint_cases_of_one_notice_join_without_overwriting_other_case():
    baseline, packets, results = example()
    prior = copy.deepcopy(baseline)
    joined, detail = join_items(baseline, packets, results)
    assert joined[0]['v11'] == joined[0]['v20'] == '1'
    assert detail['fresh_fields'] == 2 and len(detail['changed_bits']) == 1
    assert baseline == prior


def test_invalid_format_withholds_the_whole_arm_instead_of_zero_filling():
    baseline, packets, results = example()
    results[1]['parse_error'] = 'invalid'
    results[1]['row'] = None
    joined, detail = join_items(baseline, packets, results)
    assert joined is None and detail['status'] == 'withheld_unresolved_format'


@pytest.mark.parametrize('bad', ('missing', 'duplicate_item', 'extra_field', 'wrong_notice'))
def test_incomplete_or_competing_prediction_join_is_rejected(bad):
    baseline, packets, results = example()
    if bad == 'missing':
        results.pop()
    elif bad == 'duplicate_item':
        packets[1]['items'] = [11]
        results[1]['items'] = [11]
        results[1]['row'] = results[0]['row'].copy()
    elif bad == 'extra_field':
        results[0]['row']['v20'] = 0
    else:
        results[0]['record_id'] = 'another'
    with pytest.raises(ValueError):
        join_items(baseline, packets, results)
