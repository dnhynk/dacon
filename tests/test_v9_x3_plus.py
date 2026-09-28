"""Directional and scope controls for the optional X3 extension."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import catalog, facts, judge, switches
from pps_c.dedicated import model_name
from pps_c import rtd_v9_literal


def bundle(*lines):
    return facts.build({'id': 'x3-plus-control', 'meta': {'업무구분': '물품'},
                        'docs': [{'type': '규격서', 'text': '\n'.join(lines)}]}, catalog.load())


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(switches, 'V9_X3', True)
    monkeypatch.setattr(switches, 'V9_X3_PLUS', True)


def test_default_off():
    assert switches.V9_X3_PLUS is False


@pytest.mark.parametrize('text,reason', [
    ('CPU : Intel Core i5 1335U 이상', 'component_floor'),
    ('GPU : RTX 5090급 이상', 'component_floor'),
    ('GPU : RTX 5090 이상급', 'component_floor'),
    ('CPU : Intel i5-1335U 이상의 제품', 'component_floor'),
    ('O/S : Windows 11 또는 동등 이상', 'component_floor'),
    ('C.P.U : Intel Core i7-13700 상당', 'component_floor'),
    ('CPU : Intel Core i7 G12 processor 동급 이상', 'component_floor'),
    ('RAM : Samsung DDR5 Memory 이상', 'component_floor'),
    ('그래픽 : Intel Arc Graphics 이상', 'component_floor'),
    ('VGA : NVIDIA RTX5090 동급', 'component_floor'),
    ('Operating System : Windows 11 이상', 'component_floor'),
    ('C．P．U : Intel Core i7-13700 상당', 'component_floor'),
    ('칩셋 : Intel Q770 동급', 'component_floor'),
    ('RAM : Samsung DDR5 이상', 'component_floor'),
    ('SSD : Samsung 990 PRO 이상', 'component_floor'),
    ('HDD : Seagate ST8000 이상', 'component_floor'),
    ('CPU: Intel i5-1335U 이상; GPU: RTX5090 이상', 'component_floor'),
    ('CPU: Intel i5-1335U 이상, GPU: RTX5090 이상', 'component_floor'),
    ('모델 : AX900 또는 등등 제품 이상', 'equivalence_typo'),
    ('모델 : AX900 또는 등등 (제품) 이상', 'equivalence_typo'),
    ('Dell AX900, HP BX800 등 지원', 'example_list'),
    ('Dell AX900, HP BX800 등 다양한 장비 지원', 'example_list'),
    ('장비(Dell AX900, HP BX800 등)', 'example_list'),
])
def test_requested_removals(text, reason, enabled):
    b = bundle(text)
    assert judge.x3_plus_reason(text) == reason
    assert judge.x3_drop(b, b.notice.lines[0])


@pytest.mark.parametrize('text', [
    'Chipset: GB10 Blackwell',
    'CPU : Intel Core i5 1335U',
    'GPU : RTX5090',
    'CPU : Intel Core i5 1335U 이상 불가',
    'CPU : Intel Core i5 1335U 동등 제품은 허용하지 않는다.',
    'CPU : Intel Core i5 1335U, 수량 3대 이상',
    'CPU : Intel Core i5 1335U; 수량 : 3대 이상',
    'CPU : Intel Core i5 1335U 이상; GPU : RTX5090',
    '모델명: Dell AX900; CPU: Intel i5-1335U 이상',
    'CPU: Intel i5-1335U 이상; Dell AX900 납품',
    '동일 제조사 장비 도입(Dell AX900, HP BX800 등)',
    'Dell AX900, HP BX800 등과 호환되어야 한다.',
    'Dell AX900, HP BX800 등 지원 불가',
    'Dell AX900 (규격 등)',
    'Dell AX900 등록 제품',
    '장비(Dell AX900, HP BX800 등), 모델명: AX100 필수',
    '모델명 : AX900 또는 등등 제품 이상은 허용하지 않는다.',
    '모델명: AX100; 지원 모델(Dell AX900, HP BX800 등)',
    '모델명: AX100; CPU: Intel i5-1335U 또는 등등 제품 이상',
    '제조사: Dell; 장비(Dell AX900, HP BX800 등)',
    '모델명: AX100, 지원 모델(Dell AX900, HP BX800 등)',
    '장비(Dell AX900, HP BX800 등), 삼성전자 제품 납품',
    'CPU: Intel i5-1335U 이상이며 Dell AX900 납품',
    'GPU: RTX5090 이상없음',
    '모델명: AX100, CPU: Intel i5-1335U 또는 등등 제품 이상',
    '모델명: AX100, 예시: Dell AX900, HP BX800 등 지원',
    'CPU: Intel i5-1335U 이상, GPU: RTX5090',
    'CPU: Intel i5-1335U 이상은 불허',
    'CPU: Intel i5-1335U 이상은 제외',
    'CPU: Intel i5-1335U 동급 제품은 허용되지 않는다.',
    'CPU: Intel i5-1335U, Processor: AMD Ryzen 9900 이상',
    'CPU: i5 core, 메모리: Samsung 16GB 이상',
    'CPU: i5 core, RAM: DDR5 이상',
    'RAM: Samsung DDR4, 27인치 모니터, Windows 11 또는 이상',
    'CPU: Intel Core i5, 1335U 이상',
    '모델명: X1, RAM: Samsung 16GB 이상',
    '제조사: 알파, RAM: Samsung 16GB 이상',
])
def test_exact_or_restricted_designations_stay(text, enabled):
    assert judge.x3_plus_reason(text) is None


@pytest.mark.parametrize('x3,plus', [(False, False), (False, True), (True, False)])
def test_requires_both_switches(monkeypatch, x3, plus):
    monkeypatch.setattr(switches, 'V9_X3', x3)
    monkeypatch.setattr(switches, 'V9_X3_PLUS', plus)
    b = bundle('CPU: Intel Core i5 1335U 이상')
    assert not judge.x3_plus_drop(b.notice.lines[0])
    assert not judge.x3_drop(b, b.notice.lines[0])


def test_floor_does_not_cross_lines_or_suppress_other_designation(enabled, monkeypatch):
    b = bundle('CPU: Intel Core i5 1335U 이상', 'Chipset: GB10 Blackwell')
    monkeypatch.setattr(judge, 'v9_lines', lambda _: b.notice.lines)
    monkeypatch.setattr(switches, 'V9_READ2', False)
    monkeypatch.setattr(switches, 'V9_STAGE2', False)
    assert judge.v9(b) == b.notice.lines[1]


def test_literal_fallback_continues_after_dropped_component(enabled, monkeypatch):
    b = bundle('CPU: Intel Core i5 1335U 이상', '제조사: Danfoss')
    monkeypatch.setattr(switches, 'RTD_V9_SPEC_FIELDS', True)
    assert rtd_v9_literal.verdict(b) == b.notice.lines[1]


def test_dedicated_stage_continues_after_dropped_component(enabled, monkeypatch):
    b = bundle('CPU: Intel Core i5 1335U 이상', 'Chipset: GB10 Blackwell')
    monkeypatch.setattr(switches, 'V9_STAGE_COMPUTER_PARTS', True)
    kind = next(iter(model_name.FIRE_KINDS))
    setattr(b, model_name.FAM, {'designations': [
        (b.notice.lines[0], 'Intel Core i5', kind, 'computer_component'),
        (b.notice.lines[1], 'GB10', kind, 'computer_component')]})
    assert model_name.verdict(b) == b.notice.lines[1]


def test_dedicated_cpu_fallback_continues(enabled):
    b = bundle('모델명: AX900 또는 등등 제품 이상', '모델명: BX800')
    assert model_name.verdict(b) == b.notice.lines[1]
