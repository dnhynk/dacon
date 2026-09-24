import pytest
from tools.run_retrieval_contrast import first_format_gate


def test_valid_semantic_unknown_is_not_rejected_or_rerolled():
    first_format_gate([{'parse_error': None, 'row': None,
        'details': {'gate': 'model_scope_unresolved_or_mixed'}}])


@pytest.mark.parametrize('rows', [[], [{'parse_error': 'length'}],
    [{'parse_error': None}, {'parse_error': 'invalid_source_id'}]])
def test_format_pilot_requires_every_initial_response(rows):
    with pytest.raises(RuntimeError, match='remaining requests not submitted'):
        first_format_gate(rows)


def test_failed_format_gate_records_all_failures_before_stopping(tmp_path):
    import json
    from submission.runtime import Journal
    rows=[{'request_key':'first','parse_error':None},
          {'request_key':'second','parse_error':'length'},
          {'request_key':'third','parse_error':'invalid_json'}]
    journal=Journal(tmp_path/'run')
    with pytest.raises(RuntimeError):
        first_format_gate(rows,journal)
    report=json.loads((tmp_path/'run/first_batch_format_gate.json').read_text('utf8'))
    assert report['status']=='FAIL' and report['primary_requests']==3
    assert report['invalid']==[{'request_key':'second','parse_error':'length'},
                               {'request_key':'third','parse_error':'invalid_json'}]
