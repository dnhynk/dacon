import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'submission'))
from pps_c import switches, judge
from pps_c.dedicated import input_mismatch as st
from test_dedicated_input_mismatch import bundle,reading,line


def test_factory_prefix_is_not_g2b_code(monkeypatch):
    b=bundle('강선건조업(31111) 또는 기타 선박건조업(31113)으로 공장등록을 필한 업체',업종제한여부='Y',면허업종제한목록='[선박수리업(5958)]')
    r=reading(b,industry=('있음',{'3111'},['강선건조업'],'강선건조업'))
    assert st.decide(b,r) is not None
    monkeypatch.setattr(switches,'RTP_V24_FACTORY_CODE',True)
    assert st.decide(b,r) is None


def test_factory_guard_keeps_grounded_industry_mismatch(monkeypatch):
    b=bundle('강선건조업(31111) 공장등록 및 다른업종(3111)으로 등록한 업체',업종제한여부='Y',면허업종제한목록='[선박수리업(5958)]')
    r=reading(b,industry=('있음',{'3111'},['강선건조업'],'강선건조업'))
    monkeypatch.setattr(switches,'RTP_V24_FACTORY_CODE',True)
    assert st.decide(b,r) is not None


def test_factory_guard_does_not_erase_other_axis(monkeypatch):
    b=bundle('사업예산: 60,000,000원','강선건조업(31111) 공장등록을 필한 업체',업종제한여부='Y',면허업종제한목록='[선박수리업(5958)]')
    r=reading(b,budget=[('사업예산','사업예산',60000000,'포함','총액')],industry=('있음',{'3111'},['강선건조업'],'강선건조업'))
    monkeypatch.setattr(switches,'RTP_V24_FACTORY_CODE',True)
    assert st.decide(b,r) is line(b,'사업예산')






def region_bundle(own='서울특별시',partner='경기도'):
    b=bundle('가. 본점은 '+own+'에 소재한 업체이어야 합니다.',
             '나. '+own+' 소재 업체는 면허보완을 위해 '+partner+' 소재 업체와 공동도급이 가능합니다.',
             지역제한여부='Y',제한지역코드목록='서울특별시')
    return b


def test_partner_location_not_bidder_location(monkeypatch):
    b=region_bundle()
    ls=[line(b,'가. 본점'),line(b,'나. 서울')]
    monkeypatch.setattr(judge,'region_restriction',lambda b:(ls,{'서울특별시','경기도'},set()))
    assert judge.v24_region(b) is not None
    monkeypatch.setattr(switches,'RTP_V24_PARTNER_REGION',True)
    assert judge.v24_region(b) is None


def test_partner_guard_keeps_real_bidder_mismatch(monkeypatch):
    b=region_bundle('부산광역시')
    ls=[line(b,'가. 본점'),line(b,'나. 부산')]
    monkeypatch.setattr(judge,'region_restriction',lambda b:(ls,{'부산광역시','경기도'},set()))
    monkeypatch.setattr(switches,'RTP_V24_PARTNER_REGION',True)
    assert judge.v24_region(b) is not None


def test_all_default_off():
    assert not switches.RTP_V24_FACTORY_CODE
    assert not switches.RTP_V24_PARTNER_REGION
