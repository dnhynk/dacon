"""Input preparation must consume the same budget as loading and inference."""
import json
from types import SimpleNamespace

import pytest

from submission import runtime
from tests.test_submission_runtime import packets


def test_exhausted_preparation_never_allocates_the_model(tmp_path, monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr(runtime, 'time', SimpleNamespace(
        monotonic=lambda: elapsed[0], time=lambda: 1000 + elapsed[0]))
    config = SimpleNamespace(batch_size=32, total_runtime_seconds=40,
                             notice_source_policy='current', specification_review='current')

    class SlowPreparation:
        def __init__(self, *args, **kwargs):
            self.config = config

        def packets(self, recs):
            elapsed[0] = 41.0
            return packets(recs)

    monkeypatch.setattr(runtime, 'B4Pipeline', SlowPreparation)
    input_path = tmp_path / 'input.jsonl'
    input_path.write_text(json.dumps({'id': 'synthetic', 'meta': {},
        'docs': [{'doc_id': 'N1', 'type': '공고문', 'text': '입찰 공고'}]},
        ensure_ascii=False) + '\n', encoding='utf8')
    calls = []

    def model_factory(*args, **kwargs):
        calls.append('unexpected model allocation')
        raise AssertionError('Model was allocated after the total budget expired')

    with pytest.raises(TimeoutError, match='budget'):
        runtime.execute(input_path, tmp_path, tmp_path / 'output', tokenizer=object(),
                        runner_factory=model_factory)
    assert calls == []
    assert not list((tmp_path / 'output').glob('*.csv'))


def test_preparation_checks_between_notices_before_starting_more_work():
    from submission.b4_entry import B4Pipeline
    pipe=B4Pipeline.__new__(B4Pipeline)
    completed=[]
    def guard():
        if completed:
            raise TimeoutError('budget exhausted')
    pipe.preparation_guard=guard
    pipe.bundle=lambda rec: completed.append(rec['id']) or []
    with pytest.raises(TimeoutError,match='budget'):
        pipe.packets([{'id':'first'},{'id':'second'}])
    assert completed==['first']
