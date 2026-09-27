"""ZERO_ITEMS (diagnostic probes): the listed item columns are written as 0, so 24 x the score drop is that item's exact F1.
The default writes every verdict."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import csvout, switches  # noqa: E402

VERDICTS = {'v9': (1, '모델명 A-100'), 'v10': (1, '')}


def test_default_writes_every_verdict():
    assert switches.ZERO_ITEMS == ()
    row = csvout.row('n1', VERDICTS, '규격: 모델명 A-100')
    assert (row['v9'], row['e9'], row['v10']) == (1, '모델명 A-100', 1)


def test_zeroed_item_is_written_as_zero(monkeypatch):
    monkeypatch.setattr(switches, 'ZERO_ITEMS', ('v9',))
    row = csvout.row('n1', VERDICTS, '규격: 모델명 A-100')
    assert (row['v9'], row['e9'], row['v10']) == (0, '', 1)


def test_ones_item_is_written_as_one(monkeypatch):
    assert switches.ONES_ITEMS == ()
    monkeypatch.setattr(switches, 'ONES_ITEMS', ('v12', 'v10'))
    row = csvout.row('n1', VERDICTS, '규격: 모델명 A-100')
    assert (row['v12'], row['e12'], row['v10'], row['e10'], row['v9'], row['e9']) == (1, '', 1, '', 1, '모델명 A-100')
