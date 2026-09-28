"""Literal SW task obligations, exclusions and full-document absence guard."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import catalog, facts, judge, switches, rt_c_v20_task as task


def bundle(text, *, meta=None, other_docs=()):
    m = {'업무구분': '일반용역', '소관구분': '국가기관', '계약방법': '제한경쟁', '입찰추정가격': 1000000}
    m.update(meta or {})
    docs = [{'type': '공고문', 'text': '용역 입찰공고\n1. 입찰에 부치는 사항\n가. 용역명: 연구용역\n2. 입찰참가자격\n소기업·소상공인으로 제한한다.'},
            {'type': '과업지시서', 'text': text}, *other_docs]
    return facts.build({'id': 'synthetic', 'meta': m, 'docs': docs}, catalog.load())


@pytest.mark.parametrize('text', [
    '- 세부과업 2. 점검용 SW자동화도구 개발\n산출물: 소스코드 및 설치 매뉴얼',
    '본 과업의 목적은 자동화 분석 소프트웨어 모듈을 개발하는 데 있다.',
    '□ 실시간 판매·정산 운영 소프트웨어 개발 및 종합 정보 프로그램 구축·운영',
    '○ 업무 소프트웨어 유지·관리',
    '○ 분석 소프트웨어 유지ㆍ보수',
    '○ S / W 자동화 도구 개발',
])
def test_explicit_current_task(text):
    b = bundle(text)
    assert task.task_lines(b.notice)
    assert task.extra(b) is True


@pytest.mark.parametrize('text', [
    '○ 소프트웨어사업자 신고업체만 참여한다.',
    '수행실적내용 | 구분 | 소프트웨어개발 ( ) 유지관리·운영위탁 ( )',
    '○ 소프트웨어 개발 교육 및 SDK 실습',
    '○ 소프트웨어개발자 채용 연계',
    '◦ 인도 SW개발 인력 유치 및 채용행사 운영',
    '2. 소프트웨어 개발보안에 필요한 사항',
    '○ 소프트웨어 개발 정책을 연구한다.',
    '○ 소프트웨어 개발을 위한 계획을 수립한다.',
    '○ 소프트웨어 유지관리 현황을 조사한다.',
    '○ 소프트웨어 모듈을 개발한 실적을 제시한다.',
    '○ 소프트웨어 개발 및 공급업 업종을 조사한다.',
    '○ 기존 소프트웨어 개발 경험을 기재한다.',
    '○ 소프트웨어 개발을 제외한다.',
    '○ 소프트웨어 개발하지 않는다.',
    '○ 소프트웨어 개발할 경우 발주처 승인을 받는다.',
    '○ 소프트웨어 유지관리는 제외한다.',
    '○ 소프트웨어 유지·관리를 제외한다.',
    '○ 소프트웨어 유지ㆍ보수하지 않는다.',
    '○ 소프트웨어 구축하지 않는다.',
    '○ 소프트웨어 개발 도구를 구매한다.',
    '○ 데이터베이스 구축을 위한 기초조사',
    '○ 행사 홈페이지 제작 및 콘텐츠 게시',
    '○ 전산기기 구매 및 설치',
    '과거 실적\n○ 소프트웨어 개발\n실적증명서를 제출한다.',
])
def test_no_task_from_lookalikes(text):
    assert task.extra(bundle(text)) is None


@pytest.mark.parametrize('statement', [
    '소프트웨어 진흥법 제48조에 따라 대기업 참여 불가',
    '대 기 업 및\n중 견 기 업은 이 사업에 참가할 수 없다.',
    '사업금액의 하한 제도를 적용하지 않는 예외 사업이다.',
    '중소 소프트웨어사업자의 사업 참여 지원에 관한 지침',
    '소프트웨어 진흥법 시행령 제41조에 따른 중소 소프트웨어사업자만 참여 가능',
])
def test_statement_anywhere_even_late_attachment(statement):
    b = bundle('○ 소프트웨어 모듈 개발', other_docs=[{'type': '제안요청서', 'text': ('기타 사항\n' * 500) + statement}])
    assert task.task_lines(b.notice)
    assert task.extra(b) is None


def test_no_hardware_goods():
    assert task.extra(bundle('○ 소프트웨어 모듈 개발', meta={'업무구분': '물품(내자)'})) is None


def test_small_private_contract_is_not_exempt(monkeypatch):
    monkeypatch.setattr(switches, 'V20_PRIVATE', True)
    monkeypatch.setattr(switches, 'V20_MIN_ESTIMATE', 0)
    assert task.extra(bundle('○ 소프트웨어 모듈 개발', meta={'계약방법': '수의계약', '입찰추정가격': 500000})) is True


def test_existing_scope_controls_preserved(monkeypatch):
    b = bundle('○ 소프트웨어 모듈 개발', meta={'계약방법': '수의계약'})
    monkeypatch.setattr(switches, 'V20_PRIVATE', False)
    assert task.extra(b) is None
    monkeypatch.setattr(switches, 'V20_PRIVATE', True)
    monkeypatch.setattr(switches, 'V20_MIN_ESTIMATE', 2000000)
    assert task.extra(b) is None


def test_switch_is_default_off_and_only_adds_absence(monkeypatch):
    assert switches.V20_TASK_SW is False
    b = bundle('본 과업의 목적은 소프트웨어 모듈을 개발하는 데 있다.')
    b.sw_project = '불명'
    assert judge.v20(b) is None
    monkeypatch.setattr(switches, 'V20_TASK_SW', True)
    assert judge.v20(b) is True
