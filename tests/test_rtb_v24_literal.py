from types import SimpleNamespace
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "submission"))
from pps_c import record, meta, rtb_v24_literal as R


def bundle(text, method='제한경쟁', licence='[학술.연구용역(1169)]', flag='Y'):
    n=record.build({'id':'synthetic', 'meta':{'계약방법':method,'면허업종제한목록':licence,'업종제한여부':flag},
                    'docs':[{'type':'공고문','text':text}]})
    return SimpleNamespace(notice=n,meta=meta.build(n))


@pytest.mark.parametrize('text,method,expected',[
    ('입찰방법 : 일반경쟁입찰','제한경쟁',True),
    ('입찰방식 | ◦ 제한(단가)경쟁입찰(협상에 의한 계약)','일반경쟁',True),
    ('입찰방법 : 일반경쟁입찰','일반경쟁',False),
    ('제한경쟁(3억원미만)\n입찰방법: 일반경쟁입찰','제한경쟁',True),
    ('입찰방법: 제한경쟁\n입찰방식: 일반경쟁','제한경쟁',True),
    ('본 입찰은 일반경쟁입찰에 참가할 자격을 갖춘 자','제한경쟁',False),
    ('입찰방법: 제한/일반경쟁입찰','제한경쟁',False),
    ('입찰방법: 제한일반경쟁','제한경쟁',False),
    ('입찰방법: 일반경쟁, 제한경쟁','제한경쟁',False),
    ('입찰방법: 유찰된 경우 일반경쟁으로 전환','제한경쟁',False),
    ('입찰방법: 협상에 의한 계약','제한경쟁',False),
    ('입찰방법: 제한경쟁','수의계약',False),
    ('입찰방법: 일반경쟁입찰은 아님','제한경쟁',False),
    ('입찰방법: 수의계약','제한경쟁',False),
    ('1. 유의사항\n입찰방법: 일반경쟁입찰','제한경쟁',False),
])
def test_body_method(text,method,expected):
    assert (R.method_body(bundle(text,method)) is not None)==expected


@pytest.mark.parametrize('clause,licence,expected',[
    ('학술연구용역(업종코드:1169) 등록한 자 또는 협력기관(업종코드:4492)으로 지정된 기관', '[학술.연구용역(1169)]', True),
    ('근로자파견사업(1172) 면허 보유 업체이거나 행사대행업(9901) 보유한 업체', '[기타자유업(행사대행업)(9901)]', True),
    ('학술연구용역(1169)으로 등록한 업체', '[학술.연구용역(1169)]업종 또는[협력기관(4492)]', True),
    ('학술연구용역(1169) 또는 협력기관(4492)으로 등록한 업체', '[학술.연구용역(1169)]업종 또는[협력기관(4492)]', False),
    ('학술연구용역(1169) 또는 협력기관으로 등록한 업체', '[학술.연구용역(1169)]업종 또는[협력기관(4492)]', False),
    ('학술연구용역(1169) 또는\n협력기관(4492)으로 등록한 업체', '[학술.연구용역(1169)]업종 또는[협력기관(4492)]', False),
    ('학술연구용역(1169)으로 등록한 자 또는 필요한 장비를 보유한 자', '[학술.연구용역(1169)]', False),
    ('집단급식소식품판매업(5246) 및 식육판매업(4010)을 신고한 업체로 상해보험 또는 음식물배상보험 가입업체', '[식품판매업(집단급식소식품판매업)(5246)]', False),
    ('학술연구용역(1169) 및 협력기관(4492)으로 등록한 업체', '[학술.연구용역(1169)]', False),
    ('행사대행업(9901) 또는 광고대행업(9902)으로 등록한 업체', '[행사대행업(9901) 과 광고대행업(9902)]', False),
    ('면허보완을 위해 학술연구용역(1169) 또는 협력기관(4492)과 공동수급 가능', '[학술.연구용역(1169)]', False),
    ('학술연구용역(1169) 또는 협력기관(4492)으로 등록하지 않아도 되는 업체', '[학술.연구용역(1169)]', False),
    ('학술연구용역(1169) 미등록도 참가 가능한 업체', '[학술.연구용역(1169)]업종 또는[협력기관(4492)]', False),
    ('등록 서류는 추후 확인한다.', '[학술.연구용역(1169)]', False),
])
def test_licence_alternatives(clause,licence,expected):
    b=bundle('1. 입찰참가자격\n가. '+clause,licence=licence)
    assert (R.licence_or(b) is not None)==expected


def test_industry_flag_absent_is_not_this_switch():
    b=bundle('1. 입찰참가자격\n가. 학술연구용역(1169) 또는 협력기관(4492)으로 등록한 업체',flag='N')
    assert R.licence_or(b) is None
