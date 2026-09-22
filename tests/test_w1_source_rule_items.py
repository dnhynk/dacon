"""Per-item source-only judgment (config `source_rule_items`): model verdicts replaced by the source rules."""
import dataclasses
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from submission.pps.prompts import Config
from submission.stream import RecordState, StreamingExecutor
from tests.test_stream_executor import CONFIG, Clock, FakeRunner, SyntheticPipeline, answer, csv_rows, record, run
from tools import cell_reconsume

SHIPPED = Path(__file__).resolve().parents[1] / 'submission/model/config.json'


def test_config_accepts_distinct_item_numbers_only():
    assert Config().source_rule_items == ()
    assert dataclasses.replace(Config(), source_rule_items=[6, 19]).source_rule_items == [6, 19]
    for bad in ([6, 6], [0], [25], ['6'], 6):
        with pytest.raises(ValueError):
            dataclasses.replace(Config(), source_rule_items=bad)


def test_shipped_items_are_the_measured_set():
    assert sorted(Config.load(SHIPPED).source_rule_items) == [6, 8, 10, 16, 18, 19, 24]


def test_the_executor_takes_listed_items_from_the_source_rules(tmp_path):
    pipeline = SyntheticPipeline(q10={'r1'})
    pipeline.config = SimpleNamespace(**vars(CONFIG), source_rule_items=(6, 16, 19))
    clock = Clock()
    runner = FakeRunner(answer, clock)
    runner.config = pipeline.config
    run(tmp_path, [record('r1')], pipeline, answer, clock=clock, runner=runner)
    row = csv_rows(tmp_path / 'output/submission.csv')['r1']
    # The synthetic model answers 1 everywhere; the source rules find nothing in this record.
    assert [row[f'v{k}'] for k in (6, 16, 19)] == ['0', '0', '0']
    assert all(row[f'v{k}'] == '1' for k in range(1, 25) if k not in (6, 16, 19))


def test_replay_assembly_matches_the_executor():
    rec = record('r1')
    state = RecordState(0, rec)
    state.rows['A1'] = {f'{f}{k}': (1 if f == 'v' else 'model') for k in range(1, 10) for f in 've'}
    state.rows['A19'] = {f'{f}{k}': (1 if f == 'v' else 'model') for k in range(19, 25) for f in 've'}
    rules = {'A1': {f'{f}{k}': (0 if f == 'v' else '') for k in range(1, 10) for f in 've'},
             'A10': {f'{f}{k}': (1 if f == 'v' else 'rule') for k in range(10, 19) for f in 've'},
             'A19': {f'{f}{k}': (0 if f == 'v' else '') for k in range(19, 25) for f in 've'}}
    state.fallback_rows = {p: (row, []) for p, row in rules.items()}
    executor = SimpleNamespace(counts=Counter(), source_rule_items=(6, 19, 24))
    expected = StreamingExecutor._assemble(executor, state)
    actual, owners = cell_reconsume.assemble(rec['id'], {p: state.rows[p] for p in ('A1', 'A19')}, rules, rec, None, (6, 19, 24))
    assert actual == expected
    assert (actual['v6'], actual['v19'], actual['v24'], actual['v7']) == (0, 0, 0, 1)
    assert owners['v6'] == owners['v24'] == 'source_rules' and owners['v7'] == 'A1'
