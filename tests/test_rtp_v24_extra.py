import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'submission'))
from pps_c import switches,judge
from test_dedicated_input_mismatch import bundle,line

@pytest.mark.parametrize('text,registered',[
 ('가. 부산・경남 지역 소재 업체',{'부산광역시','경상남도'}),
 ('가. 경기도, 강원도, 충청도에 소재한 업체',{'경기도','강원특별자치도','충청남도','충청북도'}),
])
def test_complete_text_region_set(monkeypatch,text,registered):
 b=bundle(text,지역제한여부='Y',제한지역코드목록=', '.join(sorted(registered)))
 ln=line(b,'가.')
 parsed=judge.regions.mentions(text)['sido']
 monkeypatch.setattr(judge,'region_restriction',lambda b:([ln],parsed,set()))
 assert judge.v24_region(b) is not None
 monkeypatch.setattr(switches,'RTP_V24_REGION_ALIASES',True)
 assert judge.v24_region(b) is None


def test_region_alias_keeps_omitted_registered_area(monkeypatch):
 b=bundle('가. 부산・경남 지역 소재 업체',지역제한여부='Y',제한지역코드목록='경상남도')
 ln=line(b,'가.')
 monkeypatch.setattr(judge,'region_restriction',lambda b:([ln],{'부산광역시','경상남도'},set()))
 monkeypatch.setattr(switches,'RTP_V24_REGION_ALIASES',True)
 assert judge.v24_region(b) is not None


def annual(a=60000000,z=40000000,total=True):
 rows=(['추정가격: 100,000,000원'] if total else [])+[
  f'- 1차년도 추정가격: {a:,}원', f'- 2차년도 추정가격: {z:,}원']
 return bundle(*rows,배정예산금액=110000000,입찰추정가격=100000000)


def test_annual_parts_are_not_whole_estimate(monkeypatch):
 b=annual()
 assert judge.v24_amount(b) is not None
 monkeypatch.setattr(switches,'RTP_V24_ANNUAL_PARTS',True)
 assert judge.v24_amount(b) is None

@pytest.mark.parametrize('b',[annual(65000000),annual(total=False)])
def test_annual_part_needs_total_and_exact_sum(monkeypatch,b):
 monkeypatch.setattr(switches,'RTP_V24_ANNUAL_PARTS',True)
 assert judge.v24_amount(b) is not None


def licence(text):
 b=bundle('2. 입찰참가자격',text,업종제한여부='Y',면허업종제한목록='[처리업(1253)]')
 return b


def test_explicit_capability_alternative(monkeypatch):
 b=licence('※ 수집·운반 능력을 갖춘 자: 수집운반업(업종코드 6728)으로 등록한 자 또는 건설폐기물 재활용 촉진에 관한 법률 시행규칙 별표2의 장비기준을 충족한 자.')
 assert judge.v24_license(b) is not None
 monkeypatch.setattr(switches,'RTP_V24_CAPABILITY_OR',True)
 assert judge.v24_license(b) is None

@pytest.mark.parametrize('text',[
 '수집운반업(업종코드 6728)으로 등록하고 장비기준을 충족한 자.',
 '수집운반업(업종코드 6728) 또는 운송업(업종코드 9999)으로 등록한 자.',
 '업종코드 6728 및 업종코드 9999로 등록한 자 또는 장비기준을 충족한 자.',
])
def test_capability_guard_keeps_other_mismatches(monkeypatch,text):
 b=licence(text)
 monkeypatch.setattr(switches,'RTP_V24_CAPABILITY_OR',True)
 assert judge.v24_license(b) is not None


def test_extra_defaults_off():
 assert not switches.RTP_V24_REGION_ALIASES
 assert not switches.RTP_V24_ANNUAL_PARTS
 assert not switches.RTP_V24_CAPABILITY_OR
