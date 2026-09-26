"""Audit A–F fixes behind switches.AUDIT_FIXES (runs/rebuild_c/transfer_20260925/audit/). Each test shows the P3a behaviour
with the switch off and the fixed behaviour with it on."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import catalog, facts, judge, meta, record, regions, switches  # noqa: E402
from pps_c.families import size_words  # noqa: E402


@pytest.fixture
def fixes(monkeypatch):
    def turn(on):
        monkeypatch.setattr(switches, 'AUDIT_FIXES', on)
    return turn


class Line:
    def __init__(self, text, i=0, doc=0, doc_type='공고문', sec='QUAL'):
        self.text, self.i, self.doc, self.doc_type, self.sec = text, i, doc, doc_type, sec


def notice_of(*texts, doc_type='공고문'):
    return record.build({'id': 'T', 'meta': {}, 'docs': [{'type': doc_type, 'text': '\n'.join(texts)}]})


class FakeBundle:
    def __init__(self, notice, readings=None, P=5e7):
        self.notice, self._readings = notice, readings or {}
        self.meta = type('M', (), {'P': P})()

    def read(self, fam, ln):
        return self._readings.get((fam, ln.i), {})


def service(title_line, qual, clause='None', license_text=None):
    m = {'적용계약법': '국가계약법', '업무구분': '일반용역', '계약방법': '제한경쟁', '낙찰방법': '협상에의한계약',
         '입찰추정가격': '90000000', '배정예산금액': '99000000', '조항호내용': clause}
    if license_text:
        m['면허업종제한목록'] = license_text
    return facts.build({'id': 'T', 'meta': m, 'docs': [{'type': '공고문', 'text': '\n'.join(
        ['입찰공고', '1. 입찰에 부치는 사항', title_line, '2. 입찰참가자격', qual])}]}, catalog.load())


def test_switch_defaults_to_p3a():
    assert switches.AUDIT_FIXES is False


def test_short_region_alias_before_location_noun(fixes):
    text = '대구 경북에 사업장을 소재한 사업자'
    assert regions.mentions(text)['sido'] == {'대구광역시'}
    fixes(True)
    assert regions.mentions(text)['sido'] == {'대구광역시', '경상북도'}


def test_safety_check_threshold_is_for_statutory_inspections(fixes):
    args = ('지방', '용역', (), '어린이놀이시설 안전점검 용역', '')
    assert meta.threshold(*args)[2] == 'safety_check_service'
    fixes(True)
    assert meta.threshold(*args)[2] != 'safety_check_service'
    assert meta.threshold('지방', '용역', (), '교량 정밀안전진단 용역', '')[2] == 'safety_check_service'


def test_table_row_heading(fixes):
    assert record.heading_label('4 | | 입찰참가자격') is None
    fixes(True)
    assert record.heading_label('4 | | 입찰참가자격') == 'QUAL'
    assert record.heading_label('7 | | 예정가격 결정 및 낙찰자 결정방법') == 'EVAL'
    assert record.heading_label('1 | | | |') is None


def test_research_firm_and_preference_period_are_no_size_class(fixes):
    research = '연구소기업 육성 사업을 수행한 비영리기관 또는 협회'
    preference = '최근 5년 이내(단, 소기업·소상공인·창업기업의 경우 7년 이내) 동일 용역 실적이 있는 업체'
    assert size_words(research) == 'small' and size_words(preference) == 'small'
    fixes(True)
    assert size_words(research) is None and size_words(preference) is None
    assert size_words('소기업 또는 소상공인으로서 소기업·소상공인 확인서를 소지한 자') == 'small'


def test_title_labels(fixes):
    n = notice_of('입찰공고', '입 찰 명 : 차세대 ERP 전환 구축 감리', '계약명세서 참조')
    assert catalog.project_titles(n) == []
    fixes(True)
    assert catalog.project_titles(n) == ['차세대 ERP 전환 구축 감리']


def test_conference_words_and_education_programs(fixes):
    b = service('용역명 : 2026 금융혁신토론회 운영', '가. 경쟁입찰 참가자격을 갖춘 자')
    assert b.scope.basis == 'service:none'
    assert catalog.sw_service_title('역량 강화 프로그램 개발 및 운영 용역')
    fixes(True)
    assert service('용역명 : 2026 금융혁신토론회 운영', '가. 경쟁입찰 참가자격을 갖춘 자').scope.basis == 'service:conference'
    assert not catalog.sw_service_title('역량 강화 프로그램 개발 및 운영 용역')
    assert catalog.sw_service_title('행정 업무 프로그램 개발 용역')


def test_registered_designated_product(fixes):
    args = ('용역명 : 2026 연구실 운영 지원', '가. 경쟁입찰 참가자격을 갖춘 자', '중소기업청장이 지정.고시한 제품')
    assert service(*args).scope.competitive is False
    fixes(True)
    b = service(*args)
    assert b.scope.competitive is True and b.scope.basis == 'service:none:registered'


def test_sw_licence_does_not_frame_an_audit_title(fixes):
    args = ('용역명 : 차세대 ERP 전환 구축 감리', '가. 소프트웨어 진흥법에 따른 소프트웨어사업자로 신고한 자', 'None', '[소프트웨어사업자(컴퓨터관련서비스사업)(1468)]')
    assert facts.sw_license_framing(service(*args))
    fixes(True)
    assert not facts.sw_license_framing(service(*args))


def test_facility_buyers(fixes):
    text = '최근 3년 이내 직장어린이집에 납품한 실적이 있는 업체'
    assert judge.buyer_limit(text) is None
    fixes(True)
    assert judge.buyer_limit(text) == 'specific'
    assert judge.buyer_limit('200병상 이상 병원급에서 파견 실적이 있는 업체') == 'specific'


def test_v1_institution_list_only_in_attachment(fixes):
    rec = {'id': 'T', 'meta': {'계약방법': '제한경쟁'}, 'docs': [
        {'type': '공고문', 'text': '입찰공고\n1. 입찰참가자격\n가. 경쟁입찰 참가자격을 갖춘 소기업 또는 비영리법인'},
        {'type': '제안요청서', 'text': '제안요청서\n1. 참가자격\n가. 유자격 연구기관만 참가할 수 있습니다'}]}
    b = facts.build(rec, catalog.load())
    ln = next(x for x in b.notice.lines if '연구기관만' in x.text)
    b.cands['inst'] = [ln]
    b.readings['inst'] = {ln.i: {'역할': '참가자격', '요건': '기관 유형 한정'}}
    assert judge.v1(b) is ln
    fixes(True)
    assert judge.v1(b) is None


def test_title_band_tag_is_no_required_record(fixes):
    b = FakeBundle(notice_of('입찰공고'))
    b.meta.B, b.meta.P = 4.4e7, 4e7
    ln = Line('OO 청소 용역(수의계약·5천만원미만)(실적제한)')
    assert judge.required_amount(b, ln) == 5e7
    fixes(True)
    assert judge.required_amount(b, ln) is None


def test_attachment_form_header_is_no_record_requirement(fixes, monkeypatch):
    header = Line('외국인 초청 및 연수행사 대행용역 참여실적', i=5, doc_type='제안요청서')
    stated = Line('47개소 중 20개소 이상에서 6개월 이상 연속 수행한 실적에 한함', i=6, doc_type='규격서')
    monkeypatch.setattr(judge, 'lines_where', lambda b, fam, section=False, **want: [header, stated] if section else [])
    b = FakeBundle(notice_of('입찰공고'))
    assert judge.perf_lines(b) == [header, stated]
    fixes(True)
    assert judge.perf_lines(b) == [stated]


def test_region_registered_superset(fixes, monkeypatch):
    n = notice_of('2. 입찰참가자격', '가. 본점 소재지가 경상북도, 대구광역시에 있는 업체')
    ln = next(x for x in n.lines if '본점' in x.text)
    monkeypatch.setattr(judge, 'lines_where', lambda *a, **k: [ln])
    b = FakeBundle(n)
    b.meta.region_sido = {'대구광역시', '경상북도', '경상남도'}
    assert judge.region_restriction(b)[1] == {'대구광역시', '경상북도'}
    fixes(True)
    assert judge.region_restriction(b)[1] == {'대구광역시', '경상북도', '경상남도'}


def test_v9_listing_and_uniformity_are_no_designation():
    assert judge.LISTED_STANDARD.search('작물보호제 지침서에 수록된 상표의 사양을 따를 것')
    assert judge.SAME_MAKER.search('납품 제품은 동일 제조사, 동일 규격이어야 함')
    assert not judge.LISTED_STANDARD.search('제조사: 삼성전자, 모델명: ABC-100')


def test_size_class_without_class_word(fixes):
    n = notice_of('2. 입찰참가자격', '가. 해당 업종 등록 업체')
    ln = next(x for x in n.lines if '업종' in x.text)
    b = FakeBundle(n, {('size', ln.i): {'허용 대상': '소기업·소상공인만'}})
    assert judge.size_class(b, ln) == 'small'
    fixes(True)
    assert judge.size_class(b, ln) is None


def test_exception_split_over_lines(fixes):
    n = notice_of('※ 본 입찰은 ｢중소기업제품 공공구매제도 운영요령｣ 제44조(중소기업자 우선조달계약에 대한 예외)를',
                  '다른 단의 문장이 끼어듭니다', '적용합니다.')
    assert judge.exception_in_notice(FakeBundle(n))
    admission = notice_of('판로지원법 시행령 제2조의3(중소기업자와의 우선조달계약에 대한 예외)에 해당하는 비영리법인은 참가 가능')
    assert not judge.exception_in_notice(FakeBundle(admission))


def test_sibling_options_and_deemed_tail(fixes):
    fixes(True)
    n = notice_of('2. 입찰참가자격', '가) 중·소기업으로서 중소기업 확인서를 소지한 자 및 중소기업자로 간주되는 법인 또는 조합',
                  '나) 소상공인으로서 소상공인 확인서를 소지한 자')
    sme, small = [x for x in n.lines if x.text.startswith(('가)', '나)'))]
    assert not judge.size_condition(sme)
    assert judge.sibling_options(FakeBundle(n), [sme, small], ['sme', 'small'])
    bullets = notice_of('○ 중소기업자로서 중소기업 확인서를 소지한 자', '- 소기업·소상공인 확인서가 종합정보망에서 확인될 것')
    a, z = bullets.lines
    assert not judge.sibling_options(FakeBundle(bullets), [a, z], ['sme', 'small'])


def test_sme_certificate_requirement(fixes):
    need = notice_of('2. 입찰참가자격', '※ 직접생산증명서 및 중소기업‧소상공인확인서는 유효기간 내에 있어야 합니다')
    optional = notice_of('2. 입찰참가자격', '중소기업확인서(해당 시) 제출 시 가점 부여')
    assert judge.sme_certificate_required(FakeBundle(need))
    assert not judge.sme_certificate_required(FakeBundle(optional))


def test_v12_needs_the_certificate_named():
    assert judge.DP_CERT.search('직접생산확인증명서를 소지한 업체')
    assert not judge.DP_CERT.search('가금류를 직접 생산·제조하는 업체가 아닌 경우 참가 불가')


def test_pledge_issuer_and_stage(fixes):
    line = Line('본 사업의 확약서를 제출하여야 함')
    assert judge.names_issuer(line)
    fixes(True)
    assert not judge.names_issuer(line)
    assert judge.before_award('물품공급·기술지원확약서는 적격심사 시 반드시 제출')
    assert not judge.before_award('확약서 발급 가능 여부를 확인 후 투찰하시기 바라며, 확약서를 제출하지 못한 책임은 낙찰(예정)자에게 있습니다')
    assert not judge.before_award('확약서는 계약 체결 시 제출')


def test_shareholding_is_no_joint_contract_share():
    assert judge.SHAREHOLDING.search('소유구조 현황(지분율 5% 이상 주주)')
    assert not judge.SHAREHOLDING.search('공동수급체 구성원별 최소 지분율은 5% 이상')


def test_briefing_replaced_by_documents(fixes):
    assert not judge.NO_BRIEF.search('설명회: 제안요청서 및 과업이행요청서로 대체')
    assert judge.NO_BRIEF_FIX.search('설명회: 제안요청서 및 과업이행요청서로 대체')
    assert judge.NO_BRIEF_FIX.search('과업설명: <과업지시서 및 제안요청서> 참조')


def test_v24_amount_agreement():
    assert judge.agrees(81800000, 81818182)            # truncated to 10만원
    assert judge.agrees(154111000, 154110545)          # rounded to 1,000원
    assert not judge.agrees(80000000, 81818182)        # units above 10만원 are not rounding
    assert not judge.agrees(150000000, 154110545)

    def bundle(*lines, P, B):
        b = FakeBundle(notice_of(*lines))
        b.meta.P, b.meta.B = P, B
        return b
    split = bundle('추정가격 : 52,197,242원', '추정가격 : 101,913,304원', P=154110545, B=169521600)
    assert judge.v24_amount_parts(split) is None
    assert judge.v24_amount_parts(bundle('추정가격 : 90,000,000원', P=81818182, B=90000000)) is None
    assert judge.v24_amount_parts(bundle('추정가격 : 70,000,000원', P=81818182, B=90000000)) is not None


def test_v24_region_partner_and_licence_guidance():
    assert judge.JV_PARTNER.search('면허보완을 위하여 서울특별시, 경기도, 인천광역시 소재 건설폐기물 중간처리업 업체와 공동도급이 가능')
    assert not judge.JV_PARTNER.search('본점 소재지가 서울특별시에 있는 업체')
    assert judge.LICENSE_GUIDE.search('허용업종인 약국(5332)은 참여 불가')
