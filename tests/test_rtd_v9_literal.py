"""Synthetic RT-D v9 scope, boundary and monotonicity checks (CPU only)."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import catalog, facts, judge, switches, rtd_v9_literal as rt

def bundle(text, *, dtype='규격서', notice='1. 입찰에 부치는 사항\n가. 입찰건명: 장비 구매'):
    return facts.build({'id':'synthetic', 'meta':{'업무구분':'물품(내자)'},
                        'docs':[{'type':'공고문','text':notice},{'type':dtype,'text':text}]}, catalog.load())

@pytest.mark.parametrize('text', [
    '장비 규격\n진공펌프(ABC-\n7300XYZ)',
    '품목 사양\n모델명: QRS-\n2345B',
    '규격\n제조사: 삼성전자',
    '규격\n제조사\n\nSiemens',
    '규격\n제품 모델\n내장형펌프 5.5kW RUV-UP555G',
    '컴퓨터 규격\nCPU : Intel Core i5',
    '컴퓨터 규격\nC. P. U : 인텔 i7',
    '컴퓨터 규격\n칩셋: Intel Q770',
    '부품 규격\n컨트롤은 Danfoss 컨트롤로 하며 협의하여 변경한다.',
    '컴퓨터 규격\nCPU: Intel Core i5 또는 동등 이상',
])
def test_literal_supplied_goods(text):
    assert rt.find(bundle(text), wrapped=True, fields=True)

@pytest.mark.parametrize('text', [
    '기존 장비 현황\n모델명: ABC-7300XYZ',
    '기존 장비 유지보수\n제조사\nSiemens',
    '호환 대상\n모델명\nABC-7300XYZ',
    '부품 규격\n제조사: ㅇㅇㅇㅇ',
    '부품 규격\n모델명: 작성 또는 기재',
    '모델명:\n제조사:\n수량:',
    '재질 규격\n모델명: STS304',
    '재질 규격\n교반기모터: GC200',
    '표준\n모델명: ISO 9001',
    '전원\n모델명: 220VAC',
    '통신규격\n장치(ISO-\n9001)',
    'AAA, AA0, AA-\nA1',
    '브랜드 홍보\n제조사: 삼성전자',
    '부품 규격\n애플리케이션 설치',
    '전력\n1/15 HP 송풍기를 설치한다.',
    '컴퓨터 규격\nCPU: 8코어 이상',
    '공고번호(ABC-\n7300XYZ)',
    '요구사항 번호: 모델명: ABC-7300XYZ',
    '문서번호(ABC-\n7300XYZ)',
])
def test_exclusions(text):
    assert rt.find(bundle(text), wrapped=True, fields=True) is None

def test_different_documents_cannot_join():
    b=bundle('장비(ABC-', notice='1. 입찰에 부치는 사항\n모델명:')
    b.notice.lines[-1].doc_type='공고문'
    assert rt.find(b, wrapped=True, fields=True) is None

def test_model_label_does_not_cross_document():
    b=bundle('ABC-7300XYZ', notice='모델명:')
    assert rt.find(b, fields=True) is None

def test_no_new_service_assumption():
    b=bundle('CPU: Intel Core i5')
    b.meta.work='용역'
    assert rt.find(b, fields=True) is None

def test_default_off_and_x3b_fallthrough(monkeypatch):
    assert not switches.RTD_V9_WRAPPED_MODEL
    assert not switches.RTD_V9_SPEC_FIELDS
    b=bundle('CPU: Intel Core i5')
    monkeypatch.setattr(switches,'V9_X3B',True)
    assert judge.v9(b) is None
    monkeypatch.setattr(switches,'RTD_V9_SPEC_FIELDS',True)
    assert judge.v9(b).text=='CPU: Intel Core i5'

def test_original_evidence_is_preserved(monkeypatch):
    b=bundle('CPU: Intel Core i5\n모델명: ABC-7300XYZ')
    original=b.notice.lines[-1]
    monkeypatch.setattr(judge,'v9_lines',lambda _: [original])
    monkeypatch.setattr(switches,'RTD_V9_SPEC_FIELDS',True)
    monkeypatch.setattr(switches,'RTD_V9_WRAPPED_MODEL',True)
    assert judge.v9(b) is original

def test_switches_are_independent():
    b=bundle('장비(ABC-\n7300XYZ)')
    assert rt.find(b,wrapped=True) and rt.find(b,fields=True) is None
    b=bundle('CPU: Intel Core i5')
    assert rt.find(b,fields=True) and rt.find(b,wrapped=True) is None

def test_wrapped_code_cannot_cross_document_boundary():
    b = bundle("7300XYZ)", notice="장비(ABC-")
    assert rt.find(b, wrapped=True) is None
