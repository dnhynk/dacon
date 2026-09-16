"""Stored prompt facts preserve history; current source drives rule execution."""
import copy
import json

import pytest

from submission.pps.comparison import compare
from submission.pps.pipeline import _response_row
from submission.pps.prompts import Config
from submission.pps.retrieval import Span
from tests.test_comparison import record


@pytest.mark.parametrize('amount,stale_status,expected', [
    (66_000_000, 'same', 1),
    (55_000_000, 'different', 0),
])
def test_a_stale_derived_conclusion_cannot_control_current_source_rules(amount, stale_status, expected):
    text = f'사업예산: {amount:,}원 (부가세 포함)'
    rec = record(text)
    frozen = compare(rec)
    next(c for c in frozen['comparisons'] if c['field'] == 'budget')['status'] = stale_status
    preserved = copy.deepcopy(frozen)
    prompt = {'spans': [Span(0, '공고문', 0, len(text), text)], 'comparison_facts': frozen}
    result, details = _response_row(rec, {'text': json.dumps({'v': [0], 'e': [0]})},
        prompt, (24,), Config(mode='evidence_first', rule_checks=True, cross_source_facts=True), None, (24,))
    assert result['v24'] == expected
    assert frozen == preserved
    assert any(d.get('computed_from_current_record') and d.get('packet_facts_equal_current') is False
               for d in details)
