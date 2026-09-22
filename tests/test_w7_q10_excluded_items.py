"""Q10 overlay exclusion (config `q10_excluded_items`): listed items keep their A10-group row."""
import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest

from submission.pps.prompts import Config
from submission.prep import consume_response
from submission.stream import StreamOptions
from tests.test_stream_executor import CONFIG, Clock, FakeRunner, SyntheticPipeline, answer, csv_rows, packet, record, run, valid

SHIPPED = Path(__file__).resolve().parents[1] / 'submission/model/config.json'


def response(pkt):
    text, finish_reason = valid(pkt, 1)
    return {'text': text, 'finish_reason': finish_reason}


def test_config_accepts_distinct_q10_items_only():
    assert Config().q10_excluded_items == ()
    assert dataclasses.replace(Config(), q10_excluded_items=[11]).q10_excluded_items == [11]
    for bad in ([11, 11], [9], [19], ['11'], 11):
        with pytest.raises(ValueError):
            dataclasses.replace(Config(), q10_excluded_items=bad)


def test_shipped_exclusion_is_the_preregistered_item():
    assert list(Config.load(SHIPPED).q10_excluded_items) == [11]


def test_consume_drops_excluded_cells_from_q10_rows_only():
    pipeline = SyntheticPipeline()
    pipeline.config = SimpleNamespace(**vars(CONFIG), q10_excluded_items=(11,))
    rec = record('r1')
    q10 = consume_response(pipeline, rec, packet(rec, 'Q10'), response(packet(rec, 'Q10')))
    assert 'v11' not in q10['row'] and 'e11' not in q10['row']
    assert all(q10['row'][f'v{k}'] == 1 for k in range(10, 19) if k != 11)
    a10 = consume_response(pipeline, rec, packet(rec, 'A10'), response(packet(rec, 'A10')))
    assert a10['row']['v11'] == 1


def test_consume_leaves_q10_rows_whole_without_the_switch():
    pipeline = SyntheticPipeline()
    rec = record('r1')
    q10 = consume_response(pipeline, rec, packet(rec, 'Q10'), response(packet(rec, 'Q10')))
    assert all(q10['row'][f'v{k}'] == 1 for k in range(10, 19))


def test_the_executor_keeps_the_a10_group_row_for_excluded_items(tmp_path):
    pipeline = SyntheticPipeline(q10={'r1'})
    pipeline.config = SimpleNamespace(**vars(CONFIG), q10_excluded_items=(11,))
    clock = Clock()
    runner = FakeRunner(answer, clock)
    runner.config = pipeline.config
    options = StreamOptions(total_runtime_seconds=100000, tier_ceiling=2, tier_floor=2)
    run(tmp_path, [record('r1')], pipeline, answer, options=options, clock=clock, runner=runner)
    row = csv_rows(tmp_path / 'output/submission.csv')['r1']
    # At tier 2 A10 is not requested: the source rules (nothing found here) own items 10-18 unless Q10 overlays them.
    assert row['v11'] == '0'
    assert all(row[f'v{k}'] == '1' for k in range(10, 19) if k != 11)
