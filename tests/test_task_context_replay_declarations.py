from tools.replay_task_context_consumer import _parse_expected_baseline_changes


def test_expected_baseline_change_preserves_colons_inside_cell_value():
    assert _parse_expected_baseline_changes([
        'PPS-DEV-051:e19:before:제출기간 : 14:00~15:00'
    ]) == [{
        'record_id': 'PPS-DEV-051',
        'field': 'e19',
        'before': 'before',
        'after': '제출기간 : 14:00~15:00',
    }]


def test_structured_baseline_change_preserves_colons_on_both_sides():
    value = {
        'record_id': 'PPS-DEV-051',
        'field': 'e19',
        'before': '제출기간 : 14:00',
        'after': '상태 : 해제',
    }
    assert _parse_expected_baseline_changes([value]) == [value]
