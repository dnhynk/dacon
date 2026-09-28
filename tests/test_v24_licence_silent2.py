import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'submission'))
from pps_c import record,switches,judge
from pps_c.v24_licence_silent2 import detect,qualifications,industry_matches,meta_names

BASE='5. 입찰 참가자격\n가. 국가계약법상 자격을 갖춘 업체\n나. 서울에 소재한 소기업 확인서를 소지한 업체\n6. 낙찰자 결정방법\n최저가 순으로 결정합니다.'
def bundle(text=BASE,attachment='',lic='[집단급식소식품판매업(5246)]',flag='Y'):
    docs=[{'type':'공고문','text':text}]
    if attachment:docs.append({'type':'제안요청서','text':attachment})
    n=record.build({'id':'arbitrary','meta':{'업종제한여부':flag,'면허업종제한목록':lic},'docs':docs})
    return SimpleNamespace(notice=n,meta=SimpleNamespace(license_flag=flag))

@pytest.mark.parametrize('heading',['입찰참가자격','참가자격','견적제출 자격','참가 자격 요건','5 입찰 참가자격','7 | | 입찰참가자격','【입찰 참가자격】','견적 제출 참가자격'])
def test_qualification_variants(heading):
    b=bundle(BASE.replace('5. 입찰 참가자격',heading))
    assert detect(b).text==heading

@pytest.mark.parametrize('text',[
    '식품위생법령에 따른 집단급식소 식품판매\n업 영업신고를 필하고 조달청 승인을 필한 업체',
    '※ 유해화학물질, 시약판매업 신고로 조달청에 등록되어 있어야 함',
    '소프트웨어 사업자(디지털콘텐츠개발서비스사업:1469)\n신고를 필한 업체',
    '폐기물관리법 제25조에 따른 폐기물중간재활용업(지정폐기물외 폐기물)을 등록한 업체',
    '집단급식소 식품판매업으로 신고된 업체이어야 합니다.',
    '소프트웨어사업자로 등록한 자이어야 합니다.',
    '입찰 참가자는 업종코드 1234로 등록하여야 합니다.',
    '입찰 참가자는 식육판매업 면허를 보유한 업체이어야 합니다.',
    '시약판매업으로 등록된 자만 참여할 수 있습니다.',
])
def test_real_requirement_any_document(text):
    b=bundle(attachment='수행 업체에 관한 사항\n'+text)
    assert industry_matches(b.notice)
    assert detect(b) is None

def test_all_meta_entries_and_space_tolerant_name():
    b=bundle(attachment='비영리법인 연구단체로 등록한 업체이어야 합니다.',lic=['식육판매업(5245)','비영리법인 연구단체(1260)'])
    assert '비영리법인연구단체' in meta_names(b.notice.meta)[0]
    assert detect(b) is None

@pytest.mark.parametrize('attachment',[
    '제출서류\n- 집단급식소식품판매업 신고증 사본 1부\n- 소프트웨어사업자신고확인서 1부',
    '평가항목\n소프트웨어사업자 등록증 제출 | 배점 5점\n식육판매업 허가증 사본 제출 시 가점 2점',
    '제출서류\n사업자등록증 사본 1부',
    '사업명: 집단급식소식품판매업 현황 연구\n연구 내용은 별첨과 같습니다.',
])
def test_certificate_or_score_or_title_alone_not_requirement(attachment):
    assert detect(bundle(attachment=attachment)) is not None

def test_eligibility_not_ignored_even_under_evaluation_heading():
    assert detect(bundle(attachment='평가기준\n시약판매업 신고를 필한 업체만 입찰참가가 가능함')) is None

@pytest.mark.parametrize('text',[
    '입찰 참가 자격등록 및 나라장터 시스템 관련 문의\n가. 소기업 확인서를 소지한 업체\n나. 서울에 소재한 업체',
    '입찰참가자격을 증명하는 서류 사본 1통\n가. 소기업 확인서를 소지한 업체\n나. 서울에 소재한 업체',
    '4. (조달청 고시) 국가종합전자조달시스템 입찰참가자격등록규정\n가. 소기업 확인서를 소지한 업체\n나. 서울에 소재한 업체',
    '참가자격\n가. 소기업 확인서를 소지한 업체\n제출서류\n나. 서울에 소재한 업체',
    '참가자격\n가. 소기업 확인서를\n소지한 업체\n계약체결일까지 유효해야 합니다.',
])
def test_reject_false_heading_or_insufficient_distinct_requirements(text):
    assert detect(bundle(text=text)) is None

def test_requirement_in_notice_body_suppresses():
    assert detect(bundle(text=BASE+'\n기타 사항\n시약판매업 신고를 필한 업체만 참여할 수 있습니다.')) is None

def test_generic_nara_registration_is_not_business_type():
    b=bundle(text=BASE.replace('가. 국가계약법상 자격을 갖춘 업체','가. 국가계약법상 자격을 갖추고 나라장터 이용자등록을 필한 업체'))
    assert detect(b) is not None

def test_legacy_section_tags_are_not_trusted():
    b=bundle(text='기타 사항\n가. 국가계약법상 자격을 갖춘 업체\n나. 서울에 소재한 업체')
    for ln in b.notice.lines:ln.sec='QUAL'
    assert detect(b) is None

def test_no_restriction_meta_never_fires():
    assert detect(bundle(flag='N')) is None

def test_switch_is_off_and_independent(monkeypatch):
    assert switches.V24_LICENCE_SILENT2 is False
    monkeypatch.setattr(switches,'V24_LICENCE_SILENT',False)
    assert judge.v24_licence_silent2(bundle()) is not None

@pytest.mark.parametrize('qualification',[
    '지방계약법 시행령 제13조 및 시행규칙 제14조에 따라 사업자등록을 필한 업체',
    '신규사업자인 경우 사업자등록일 기준으로 판단하고 주된 영업소가 서울에 소재한 업체',
    '중소기업자로 등록되어 있으며 나라장터 이용자등록을 필한 업체',
])
def test_generic_business_tax_registration_not_licence(qualification):
    assert detect(bundle(text=BASE.replace('가. 국가계약법상 자격을 갖춘 업체','가. '+qualification))) is not None

def test_recent_report_in_certificate_list_not_eligibility():
    assert detect(bundle(attachment='제출서류\n◦ 중소기업확인서 또는 최근 결산 신고된 소프트웨어사업자 일반현황관리확인서 1부.')) is not None

def test_qual_parent_and_child_requirement_heading():
    b=bundle(text=BASE.replace('5. 입찰 참가자격','5 입찰 참가자격\n1. 입찰 참가자 요건'))
    assert detect(b).text=='1. 입찰 참가자 요건'

@pytest.mark.parametrize('text',[
    '다. 「식품위생법」에 의거 관계 기관의 인/허가 자격조건을 구비한 업체.',
    '카. “집단급식소 식품판매업 영업신고”를 완료하고 영업신고증을 제출할 수 있는 업체이어여 합니다.',
    '마. 집단급식소 식품판매업 신고를 한 업체로서 HACCP 인정업체이어야 합니다.\n※ 증명서류 등 제출 안내\n- 영업신고증 사본 1부',
    '가. 전문건설업 중 상‧하수도설비공사업을 등록한 업체\n※ 등록기준 미달 시 적격심사 점수 감점',
    '가. 사업자등록증에 기재된 사업의 종류에 교육관련 사업내용이 포함되어야 합니다.',
    '마. 사업의 종류에 당해 물품과 관련된 사업자등록증을 교부받은 자',
    '입찰서제출 마감일 전일까지 (학술연구업종, 업종코드 : 1169)으로 입찰참가자격을 등록한 자',
])
def test_audit_requirement_expressions_not_lost(text):
    assert detect(bundle(text=BASE+'\n'+text)) is None

def test_name_list_under_registration_lead_not_checklist():
    b=bundle(text=BASE.replace('가. 국가계약법상 자격을 갖춘 업체',
        '가. 다음의 사항을 입찰참가자격으로 등록한 자\n- 근로자파견사업자(1172)\n※ 계약 시 근로자파견사업 허가증 제출'),
        lic='[근로자파견사업(1172)]')
    assert detect(b) is None

def test_bare_meta_name_is_requirement_in_qualification():
    b=bundle(text=BASE.replace('가. 국가계약법상 자격을 갖춘 업체','가. 의료법 제3조에 따른 종합병원'),lic='[의료기관개설허가(종합병원)(5373)]')
    assert detect(b) is None

def test_joint_contract_prohibition_not_end_of_qualification():
    b=bundle(text=BASE.replace('가. 국가계약법상 자격을 갖춘 업체','가. 국가계약법상 자격을 갖춘 업체\n※ 공동계약 불가\n나. 엔지니어링산업진흥법에 따른 전기/전자 분야 엔지니어링사업자'),lic='[엔지니어링사업(전기설비)(3570)]')
    assert detect(b) is None

def test_meta_dot_variants_in_attachment():
    b=bundle(attachment='입찰참가자는 학술․연구용역으로 등록한 업체이어야 합니다.',lic='[학술.연구용역(1169)]')
    assert detect(b) is None

def test_address_document_list_is_not_licence_requirement():
    b=bundle(text=BASE.replace('가. 국가계약법상 자격을 갖춘 업체','가. 법인등기부상 본점 또는 사업자등록증이나 관련 법령에 따른 허가·인가·면허·등록·신고 서류에 기재된 사업장 소재지가 서울인 업체'))
    assert detect(b) is not None

def test_year_in_generic_registration_is_not_industry_code():
    b=bundle(text=BASE.replace('가. 국가계약법상 자격을 갖춘 업체','가. (2026) 국가계약법상 자격을 갖추고 나라장터 이용자등록을 필한 업체'))
    assert detect(b) is not None

def test_only_one_requirement_after_intro_is_insufficient():
    b=bundle(text='입찰참가자격\n다음의 요건을 모두 갖춘 업체\n가. 서울에 소재한 업체\n제출서류\n사업자등록증 사본 1부')
    assert detect(b) is None

def test_notice_qualification_in_concerning_heading():
    b=bundle(text=BASE.replace('5. 입찰 참가자격','3. 입찰참가 자격에 관한 사항').replace('가. ','◉ ').replace('나. ','◉ '))
    assert detect(b).text=='3. 입찰참가 자격에 관한 사항'

def test_briefing_attendee_heading_not_bid_qualification():
    b=bundle(text='제안서 설명 장소 및 유의사항\n. 참가자격 : 해당 업체 1년이상 재직자 또는 대표자 / 업체별 2인이내\n가. 서울에 소재한 업체\n나. 소기업 확인서를 소지한 업체')
    assert detect(b) is None
