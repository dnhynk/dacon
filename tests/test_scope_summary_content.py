"""A valid JSON shell is not a model-proposed description of the purchase."""
import copy

import pytest

from submission.b4_entry import B4Pipeline, parse_error
from submission.pps.catalog_scope import validate
from tests.test_catalog_scope_contract import setup, response, DATA


@pytest.mark.parametrize('summary', [',', ': ', ' \t ', '…', '---'])
def test_punctuation_only_summary_abstains_without_format_retry_or_raw_repair(summary):
    rec, packet, obj, knowledge = setup()
    obj['task_summary'] = summary
    observed = response(obj)
    before = copy.deepcopy(observed)
    assert validate(observed['text'],packet['spans']) == obj
    assert parse_error(packet,observed) is None
    row, log = B4Pipeline(DATA,None).consume(rec,packet,observed)
    assert row is None and log['gate']=='model_task_summary_without_content'
    assert not log['source_scope_promoted']
    assert log['model_scope']['task_summary']==summary and observed==before


def test_human_readable_task_with_punctuation_retains_its_original_claim():
    rec, packet, obj, knowledge = setup()
    obj['task_summary'] = ': 자문 및 운영 분석'
    row,log = B4Pipeline(DATA,None).consume(rec,packet,response(obj))
    assert row['v14']==1 and log['source_scope_promoted']
