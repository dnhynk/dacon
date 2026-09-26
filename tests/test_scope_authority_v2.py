"""Structural controls for the cited-certificate scope rule (SCOPE_FIXES; audit 2026-09-26, adopted from the review patch)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'submission'))
from pps_c import catalog, facts, switches


def service(title, certificate=False, family='sw'):
    cat = catalog.load()
    code, licence = {'sw': ('8111189901', '[소프트웨어사업자(컴퓨터관련서비스사업)(1468)]'),
                     'security': ('9212159901', '[시설경비업(1164)]'),
                     'cleaning': ('7611150101', '[건물위생관리업(1162)]')}[family]
    lines = ['1. 입찰에 부치는 사항', '용역명: ' + title, '2. 입찰참가자격', '가. 입찰참가자격을 등록한 업체']
    if certificate:
        prod = cat.by_code[code]
        lines.append(f'나. {prod.name}({prod.code}) 직접생산확인증명서를 소지한 업체')
    return facts.build({'id': 'synthetic_scope_authority',
                        'meta': {'적용계약법': '국가계약법', '업무구분': '일반용역', '계약방법': '제한경쟁',
                                 '입찰추정가격': 90_000_000, '배정예산금액': 99_000_000,
                                 '업종제한여부': 'Y', '면허업종제한목록': licence},
                        'docs': [{'type': '공고문', 'text': '\n'.join(lines)}]}, cat)


@pytest.mark.parametrize('title,family', [('교육용 전산기기 임차 용역', 'sw'),
                                        ('국방 경비체계 정책 연구 용역', 'security')])
def test_certificate_requirement_cannot_overrule_an_explicit_other_work_head(monkeypatch, title, family):
    monkeypatch.setattr(switches, 'SCOPE_FIXES', True)
    assert service(title, False, family).scope.competitive is False
    assert service(title, True, family).scope.competitive is False


def test_base_path_stays_unchanged_until_separately_enabled(monkeypatch):
    monkeypatch.setattr(switches, 'SCOPE_FIXES', False)
    monkeypatch.setattr(switches, 'CODE_AUDIT_BASE', False)
    assert service('교육용 전산기기 임차 용역', True).scope.competitive is True
    monkeypatch.setattr(switches, 'CODE_AUDIT_BASE', True)
    assert service('교육용 전산기기 임차 용역', True).scope.competitive is False


@pytest.mark.parametrize('title,family', [('정보시스템 유지관리 용역', 'sw'),
                                        ('본청 건물청소 용역', 'cleaning'),
                                        ('본청 시설경비 용역', 'security'),
                                        ('[용역명]', 'sw')])
def test_certificate_evidence_is_kept_for_its_own_work_or_a_withheld_title(monkeypatch, title, family):
    monkeypatch.setattr(switches, 'SCOPE_FIXES', True)
    assert service(title, True, family).scope.competitive is True


def test_unrecognized_title_is_not_itself_conflicting_evidence(monkeypatch):
    monkeypatch.setattr(switches, 'SCOPE_FIXES', True)
    title = '멀티미디어 자료 디지털화 용역'
    assert not catalog.head_conflict('sw', title)
    # This is an unchanged-evidence control, not a new legal classification.
    assert service(title, True).scope.competitive is True
