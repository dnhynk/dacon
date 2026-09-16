import copy
import math

import pytest

from tools.verify_platform_packets import compare_packets


def packet(key='one'):
    return {'request_key': key, 'token_ids': [1, 2],
            'source_search': {'utility': 0.5}, 'generation': {'thinking': 768}}


def test_equal_producers_pass():
    source = [packet(), packet('two')]
    assert compare_packets(source, copy.deepcopy(source))['status'] == 'PASS'


def test_single_ulp_diagnostic_difference_still_fails():
    source = [packet()]
    other = copy.deepcopy(source)
    other[0]['source_search']['utility'] = math.nextafter(0.5, 1.0)
    result = compare_packets(source, other)
    assert result['status'] == 'FAIL'
    assert result['changed_packets'] == [{'request_key': 'one', 'fields': ['source_search']}]


def test_reordered_input_cannot_pass():
    source = [packet(), packet('two')]
    result = compare_packets(source, source[::-1])
    assert result['status'] == 'FAIL' and not result['request_order_equal']


def test_missing_added_or_type_changed_fields_cannot_pass():
    source = [packet()]
    for other in ([packet('two')], [dict(packet(), extra=False)],
                  [dict(packet(), token_ids=[True, 2])]):
        assert compare_packets(source, other)['status'] == 'FAIL'


def test_duplicate_and_empty_producers_rejected():
    for other in ([], [packet(), packet()]):
        with pytest.raises(ValueError):
            compare_packets([packet()], other)
