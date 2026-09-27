"""Dedicated item stages (switches.DEDICATED): off by default; when an item is listed, its stage's verdict replaces the shared
rule for that item only, and main.consume hands the stage's responses to the stage."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import catalog, dedicated, facts, judge, main, switches  # noqa: E402

TEXT = '1. 입찰에 부치는 사항\n가. 입찰건명: 사무용 복합기 구매\n규격: 제조사 A사 모델명 AB-1000'


def bundle():
    return facts.build({'id': 'T', 'meta': {}, 'docs': [{'type': '공고문', 'text': TEXT}]}, catalog.load())


def fake_stage(verdict):
    calls = []
    mod = types.SimpleNamespace(FAM='d_fake', verdict=lambda b: verdict,
                                consume=lambda b, cands, text: calls.append(text) or True)
    return mod, calls


def test_default_uses_the_shared_rules():
    assert switches.DEDICATED == () and dedicated.active() == []
    assert dedicated.by_family('d_fake') is None


def test_listed_item_takes_the_stage_verdict(monkeypatch):
    shared = judge.judge(bundle())
    mod, _ = fake_stage(True)
    monkeypatch.setattr(switches, 'DEDICATED', ('v12',))
    monkeypatch.setattr(dedicated, 'stage', lambda item: mod)
    out = judge.judge(bundle())
    assert out['v12'] == (1, '')
    assert {k: v for k, v in out.items() if k != 'v12'} == {k: v for k, v in shared.items() if k != 'v12'}


def test_consume_goes_to_the_stage(monkeypatch):
    mod, calls = fake_stage(None)
    monkeypatch.setattr(switches, 'DEDICATED', ('v12',))
    monkeypatch.setattr(dedicated, 'stage', lambda item: mod)
    r = main.Request(0, 'd_fake', [], (), [], {}, 16)
    assert main.consume(bundle(), r, '{"x": 1}') is True and calls == ['{"x": 1}']
