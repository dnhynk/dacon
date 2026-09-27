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
    mod = types.ModuleType('fake_stage')
    mod.FAM, mod.verdict = 'd_fake', (verdict if callable(verdict) else lambda b: verdict)
    mod.consume = lambda b, cands, text: calls.append(text) or True
    return mod, calls


def test_default_uses_the_shared_rules():
    assert switches.DEDICATED == () and switches.DEDICATED_OR == () and dedicated.active() == []
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


def test_group_module_decides_each_listed_violation(monkeypatch):
    mod, _ = fake_stage(lambda b, tag: True if tag == 'A' else None)
    monkeypatch.setattr(switches, 'DEDICATED', ('v10', 'v11'))
    monkeypatch.setattr(dedicated, 'MODULES', {'v10': 'grp:A', 'v11': 'grp:B'})
    monkeypatch.setattr(dedicated, 'stage', lambda item: mod)
    out = judge.judge(bundle())
    assert out['v10'] == (1, '') and out['v11'] == (0, '')
    assert dedicated.active() == [mod]


def test_or_keeps_the_shared_verdict_and_adds_the_stage(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED_OR', ('v12',))
    for shared, staged, want in ((None, True, (1, '')), (True, None, (1, '')), (None, None, (0, ''))):
        mod, _ = fake_stage(staged)
        monkeypatch.setattr(dedicated, 'stage', lambda item: mod)
        monkeypatch.setitem(judge.RULES, 'v12', lambda b: shared)
        assert judge.judge(bundle())['v12'] == want
        assert dedicated.active() == [mod]


def test_x4_drops_a_competition_stage_firing_on_an_excluded_object(monkeypatch):
    mod, _ = fake_stage(lambda b, tag: True)
    monkeypatch.setattr(switches, 'DEDICATED_OR', ('v10',))
    monkeypatch.setattr(dedicated, 'stage', lambda item: mod)
    monkeypatch.setitem(judge.RULES, 'v10', lambda b: None)
    monkeypatch.setattr(judge, 'food_basket', lambda b: True)
    monkeypatch.setattr(judge, 'designation_excluded', lambda b: False)
    assert judge.judge(bundle())['v10'] == (1, '')
    monkeypatch.setattr(switches, 'DEDICATED_X4', True)
    assert judge.judge(bundle())['v10'] == (0, '')
    monkeypatch.setattr(judge, 'food_basket', lambda b: False)
    assert judge.judge(bundle())['v10'] == (1, '')


def test_v13_stage_fires_only_on_a_small_only_clause(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED_OR', ('v13',))
    monkeypatch.setitem(judge.RULES, 'v13', lambda b: None)
    clauses = {
        '「중소기업기본법」 제2조에 따른 소기업 또는 「소상공인기본법」 제2조에 따른 소상공인으로서 소기업·소상공인 확인서를 소지한 자': 1,
        '중소기업기본법 제2조(중소기업자의 범위)에 따른 소기업과 소상공인으로서 중‧소기업‧소상공인 및 장애인기업 확인요령에 따라 발급된 '
        '소기업 ‧ 소상공인 확인서를 소지한 자': 1,
        '「중소기업기본법」 제2조에 따른 중·소기업 및 「소상공인 기본법」 제2조에 따른 소상공인으로서 중·소기업·소상공인 확인서를 소지한 자': 0,
        '중소기업기본법 제2조에 따른 중소기업자, 소기업·소상공인으로서 발급된 중기업·소기업·소상공인확인서를 소지한 자': 0,
        '제2조에 따른 중소기업 또는 소상공인으로서 중소기업(소상공인) 확인서를 소지한 업체': 0,
        '「중소기업제품 구매촉진 및 판로지원에 관한 법률」에 의한 중소기업으로서, 소기업 ․ 소상공인확인서(중소기업현황 정보시스템 발급 '
        '또는 지방중소기업청 발급)를 소지한 업체': 1,
        '「중·소기업 및 소상공인 지원을 위한 특별조치법」에 따른 중·소기업 또는 소상공인 업체로서 중소기업확인서 또는 '
        '소상공인확인서를 소지한 자': 0,
        '「중소기업기본법」 제2조에 따른 중소기업 또는 「소상공인기본법」 제2조에 따른 소상공인으로서 입찰에 참가할 수 있는 자': 0,
        '중소기업확인서를 소지한 업체만 참가할 수 있습니다.': 0,
        '「중소기업확인서」를 소지한 업체만 참가할 수 있습니다.': 0,
        '중소기업자로서 소기업·소상공인 확인서를 소지한 업체만 참가할 수 있습니다.': 1,
        '중소기업자로서 「소기업·소상공인 확인서」를 소지한 업체만 참가할 수 있습니다.': 1,
        '중기업 또는 소기업·소상공인 확인서를 소지한 업체만 참가할 수 있습니다.': 0,
        '중기업 또는\n소기업·소상공인 확인서를 소지한 업체만 참가할 수 있습니다.': 0,
        '소기업·소상공인 확인서를 소지한 업체만 참가할 수 있습니다. 중기업 확인서는 인정하지 않습니다.': 1,
        '소기업·소상공인 확인서를 소지한 업체만 참가할 수 있습니다. 중기업 확인서를 인정하지 않습니다.': 1,
        '소기업·소상공인 확인서를 소지한 업체만 참가할 수 있습니다. 중기업 확인서의 경우는 인정하지 않습니다.': 1,
        '소기업·소상공인 확인서를 소지한 업체만 참가할 수 있습니다. 중기업 확인서로는 인정하지 않습니다.': 1,
        '소기업·소상공인 확인서를 소지한 업체만 참가할 수 있습니다. 중기업 확인서(사본)는 인정하지 않습니다.': 1,
        '중소기업확인서를 제출하지 않을 경우 인정하지 않습니다.': 0,
    }
    for text, want in clauses.items():
        b = facts.build({'id': 'T', 'meta': {}, 'docs': [{'type': '공고문', 'text': '2. 입찰참가자격\n' + text}]}, catalog.load())
        line = next((ln for ln in b.notice.lines if '확인서' in ln.text), b.notice.lines[-1])
        mod, _ = fake_stage(lambda b, tag: line)
        monkeypatch.setattr(dedicated, 'stage', lambda item: mod)
        monkeypatch.setattr(switches, 'V13_STAGE_SMALL_ONLY', False)
        assert judge.judge(b)['v13'][0] == 1
        monkeypatch.setattr(switches, 'V13_STAGE_SMALL_ONLY', True)
        assert judge.judge(b)['v13'][0] == want, text
