"""Probe switches from the expert audits and audit rounds RA–RG (runs/rebuild_c/transfer_20260925/audit/). Each test shows
the switch-off behaviour (current) and the switch-on behaviour on a small synthetic notice."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import catalog, facts, judge, record, switches  # noqa: E402

SW_LICENSE = '[소프트웨어사업자(컴퓨터관련서비스사업)(1468)]'
SW_PROJECT = '소프트웨어 개발·구축·유지관리·운영'
THIRD_PARTY_BY_BID = {'발급 주체': '제3자(제조사·공급사·기술지원사)', '시점': '입찰 전 발급·보유 또는 입찰서와 함께 제출'}


def notice_of(*texts, doc_type='공고문'):
    return record.build({'id': 'T', 'meta': {}, 'docs': [{'type': doc_type, 'text': '\n'.join(texts)}]})


class FakeBundle:
    def __init__(self, notice, readings=None, cands=None, **meta):
        self.notice, self._readings, self.cands = notice, readings or {}, cands or {}
        base = {'P': 5e7, 'B': 5.5e7, 'law': '국가', 'region_sido': set(), 'region_basic': 0, 'T_lo': 2.3e8, 'T_hi': 2.3e8,
                'local_private': False, 'method': '제한경쟁', 'posted': None, 'region_flag': None}
        base.update(meta)
        self.meta = type('M', (), base)()

    def read(self, fam, ln):
        return self._readings.get((fam, ln.i), {})


def service(title_line, qual='가. 경쟁입찰 참가자격을 갖춘 자', license_text=None):
    m = {'적용계약법': '국가계약법', '업무구분': '일반용역', '계약방법': '제한경쟁', '낙찰방법': '협상에의한계약',
         '입찰추정가격': '90000000', '배정예산금액': '99000000', '조항호내용': 'None'}
    if license_text:
        m['면허업종제한목록'] = license_text
    return facts.build({'id': 'T', 'meta': m, 'docs': [{'type': '공고문', 'text': '\n'.join(
        ['입찰공고', '1. 입찰에 부치는 사항', title_line, '2. 입찰참가자격', qual])}]}, catalog.load())


def goods(codes, item_line, qual='가. 제조업 등록 업체', license_text=None, clause=None, extra_docs=()):
    m = {'적용계약법': '국가계약법', '업무구분': '물품(내자)', '계약방법': '제한경쟁', '낙찰방법': '적격심사',
         '입찰추정가격': '90000000', '배정예산금액': '99000000', '세부품명번호목록': codes, '조항호내용': clause or 'None'}
    if license_text:
        m['면허업종제한목록'] = license_text
    docs = [{'type': '공고문', 'text': '\n'.join(['입찰공고', '1. 입찰에 부치는 사항', item_line, '2. 입찰참가자격', qual])}]
    return facts.build({'id': 'T', 'meta': m, 'docs': docs + [{'type': t, 'text': x} for t, x in extra_docs]}, catalog.load())


def test_expert_probe_switches_default_off():
    for k in ('V19_STAGE', 'V9_X3', 'X4_OBJECT', 'V12_TITLE_SW', 'V6_META_BASIC', 'V7_CLAUSE', 'V7_META_MULTI', 'PERF_UNREAD',
              'V1_REGION_FACILITY', 'BUYER_CERTIFIER', 'V20_CONTENT', 'V20_ORDERER', 'V22_PRESENTATION', 'V9_X3B', 'V12_X3',
              'V12_MANUF', 'V19_CPU_TIMED'):
        assert getattr(switches, k) is False, k
    assert switches.AF3_ITEMS == ()


# ---------------------------------------------------------------- v19 (expert audit X6)
STAGE_CASES = {
    'QUAL_STAGE': ['2. 입찰참가자격', '가. 적격심사 대상자는 제조사의 기술지원 확약서를 적격심사 서류와 함께 제출하여야 함'],
    'PRE': ['2. 입찰참가자격', '가. 입찰참가업체는 입찰서 제출 시 제조사의 기술지원 확약서를 제출하여야 함'],
    'POST': ['3. 기타', '가. 납품업체는 안정적인 유지보수를 위해 서버 제조사의 정품 공급증명원 및 3년 기술확약서를 제출한다.'],
    'PRE_LIST': ['3. 제출서류', '1) 입찰참가신청서 1부.', '5) 수의계약 체결 제한 여부 확인서 1부.', '6) 제조사 기술지원확약서 1부.'],
    'PRE_LIST_INTRO': ['가. 입찰에 참가하고자 하는 업체는 전자입찰 마감일시(메일 서버 도착시간 기준)까지 아래 서류를 구매팀으로 반드시 '
                       '제출하고, 규격심사에 합격한 업체이어야만 입찰참가 자격을 인정합니다.', '1) 입찰사양서 1부.', '2) 제조사 정품 공급 및 기술지원확약서 1부.'],
    'PRE_CAP': ['2. 입찰참가자격', '가. 제조사의 기술지원확약서를 제출할 수 있는 업체'],
    'LAWFUL': ['※ 기술지원확약서의 발급은 기술지원사와 낙찰자 간에 세부협약으로 이루어짐'],
    'OTHER': ['3. 기타', '가. 제조사 공급자 증명원'],
}


def test_x6_pledge_stage_classes():
    got = {}
    for name, texts in STAGE_CASES.items():
        n = notice_of(*texts)
        ln = n.lines[-1]
        b = FakeBundle(n)
        got[name] = (judge.x6_pledge_stage(b, ln), judge.x6_pledge_violation(b, ln))
    assert got == {'QUAL_STAGE': ('QUAL_STAGE', False), 'PRE': ('PRE', True), 'POST': ('POST', False),
                   'PRE_LIST': ('PRE_LIST', True), 'PRE_LIST_INTRO': ('PRE_LIST', True), 'PRE_CAP': ('PRE_CAP', False),
                   'LAWFUL': ('LAWFUL', False), 'OTHER': ('OTHER', False)}          # OTHER: a certificate is no 확약·협약
    # The sibling entry reads as a post-award heading on its own ("계약 체결"); as a list entry it is skipped.
    assert judge.X6_LIST_HEAD_POST.search('5) 수의계약 체결 제한 여부 확인서 1부.')
    assert judge.X6_LIST_ENTRY.search('5) 수의계약 체결 제한 여부 확인서 1부.')


def test_v19_stage_gates_v19(monkeypatch):
    fired = {}
    for name in ('QUAL_STAGE', 'POST', 'PRE_CAP', 'PRE', 'PRE_LIST'):
        n = notice_of(*STAGE_CASES[name])
        ln = n.lines[-1]
        b = FakeBundle(n, {('pledge', ln.i): THIRD_PARTY_BY_BID}, {'pledge': [ln]})
        monkeypatch.setattr(switches, 'V19_STAGE', False)
        off = judge.v19(b) is ln
        monkeypatch.setattr(switches, 'V19_STAGE', True)
        fired[name] = (off, judge.v19(b) is ln)
    assert fired == {'QUAL_STAGE': (True, False), 'POST': (True, False), 'PRE_CAP': (True, False),
                     'PRE': (True, True), 'PRE_LIST': (True, True)}


# ---------------------------------------------------------------- v9 (expert audit X3)
def v9_bundle(text, titles=('실험장비 구매',), codes=(('원심분리기', '4115151201'),), work='물품'):
    n = notice_of('3. 규격', text)
    ln = n.lines[-1]
    b = FakeBundle(n, {('model', ln.i): {'성격': '구매 대상의 제조사·모델 지정'}}, {'model': [ln]}, codes=list(codes), work=work)
    b.titles = list(titles)
    return b, ln


def test_v9_x3_drops_practice_and_noise(monkeypatch):
    monkeypatch.setattr(switches, 'V9_BROAD', True)
    cases = {'plain': v9_bundle('가. 모델명 : DPM-8000GMP'),
             'clause 동등 이상': v9_bundle('가. 모델명 : DPM-8000GMP 또는 동등 이상'),
             'SW licence title': v9_bundle('가. 모델명 : DPM-8000GMP', titles=('한컴오피스 라이선스 구매',)),
             'consumables title': v9_bundle('가. 모델명 : DPM-8000GMP', titles=('프린터 토너 구매',)),
             'service': v9_bundle('가. 모델명 : DPM-8000GMP', work='용역'),
             'platform only': v9_bundle('가. 운영체제 : Windows 11'),
             'platform with a model code': v9_bundle('가. 모델명 : HP-4500 (Windows 11)')}
    assert all(judge.v9(b) is ln for b, ln in cases.values())
    monkeypatch.setattr(switches, 'V9_X3', True)
    kept = {k for k, (b, ln) in cases.items() if judge.v9(b) is ln}
    assert kept == {'plain', 'platform with a model code'}
    assert {k for k, (b, ln) in cases.items() if judge.x3_drop(b, ln)} == set(cases) - kept


# ---------------------------------------------------------------- v10 v11 v13 (expert audit X4)
def test_x4_food_basket_and_excluded_designation(monkeypatch):
    monkeypatch.setattr(switches, 'COMPETITIVE_GOODS', ('v10', 'v11', 'v13'))
    basket = goods('햄류[5011209904]', '가. 품명 : 학교급식용 햄류 구매', qual='가. 식품판매업 등록 업체',
                   license_text='[식품판매업(집단급식소식품판매업)(5246)]')
    fast = goods('전기자동차용충전장치[2611170403]', '가. 품명 : 급속충전기 100kW 구매')
    slow = goods('전기자동차용충전장치[2611170403]', '가. 품명 : 완속충전기 7kW 구매')
    assert judge.food_basket(basket) and not judge.food_basket(fast)
    assert judge.designation_excluded(fast) and not judge.designation_excluded(slow)
    assert [judge.competitive_service(b, 'v10') for b in (basket, fast, slow)] == [True, True, True]
    assert [judge.v10(b) for b in (basket, fast, slow)] == [True, True, True]
    monkeypatch.setattr(switches, 'X4_OBJECT', True)
    assert [judge.competitive_service(b, 'v10') for b in (basket, fast, slow)] == [False, False, True]
    assert [judge.v10(b) for b in (basket, fast, slow)] == [None, None, True]


def test_x4_exception_in_any_document(monkeypatch):
    monkeypatch.setattr(switches, 'COMPETITIVE_GOODS', ('v10', 'v11', 'v13'))
    b = goods('전기자동차용충전장치[2611170403]', '가. 품명 : 완속충전기 7kW 구매', extra_docs=[(
        '규격서', '본 물품은 「중소기업제품 구매촉진 및 판로지원에 관한 법률 시행령」 제7조 제1항 제3호에 따라 중소기업자간 경쟁을 적용하지 않음')])
    assert judge.exception_anydoc(b) and not judge.competition_exception(b)
    assert judge.v10(b) is True and judge.v11(b) is True
    monkeypatch.setattr(switches, 'X4_OBJECT', True)
    assert judge.v10(b) is None and judge.v11(b) is None


def test_x4_registered_small_only_basis(monkeypatch):
    monkeypatch.setattr(switches, 'COMPETITIVE_GOODS', ('v10', 'v11', 'v13'))
    b = goods('전기자동차용충전장치[2611170403]', '가. 품명 : 완속충전기 7kW 구매',
              qual='가. 소기업 또는 소상공인으로서 중소기업확인서를 발급받은 업체', clause='중기간 경쟁제품 소기업 소상공인 제한경쟁')
    small = b.notice.lines[-1]
    assert judge.v13(b) is small
    monkeypatch.setattr(switches, 'X4_OBJECT', True)
    assert judge.v13(b) is None


# ---------------------------------------------------------------- v12 (audit RA)
def test_v12_title_sw_yields_an_sw_certificate_to_the_title(monkeypatch):
    cat = catalog.load()
    clause = '가. 직접생산확인증명서(정보시스템개발서비스, 세부품명번호 8111159901)를 보유한 업체'
    titles = ('청사 시설물 청소 용역', '업무용 정보시스템 유지보수 용역', '[공고명]')
    monkeypatch.setattr(switches, 'V12_TITLE_OBJECT', True)
    assert [catalog.title_names_other_work([t], clause, cat) for t in titles] == [False, False, False]
    monkeypatch.setattr(switches, 'V12_TITLE_SW', True)
    assert [catalog.title_names_other_work([t], clause, cat) for t in titles] == [True, False, False]


# ---------------------------------------------------------------- v6 v7 (audits RE, RG)
def test_v6_meta_basic_registration(monkeypatch):
    monkeypatch.setattr(judge, 'region_restriction', lambda b: ([], set(), set()))
    n = notice_of('2. 입찰참가자격', '가. 경쟁입찰 참가자격을 갖춘 자')
    basic, none = FakeBundle(n, region_flag='Y', region_basic=2), FakeBundle(n, region_flag='Y', region_basic=0)
    assert judge.v6(basic) is None and judge.v6(none) is None
    monkeypatch.setattr(switches, 'V6_META_BASIC', True)
    assert judge.v6(basic) is True and judge.v6(none) is None


def test_v7_meta_multi_and_clause(monkeypatch):
    monkeypatch.setattr(judge, 'region_restriction', lambda b: ([], set(), set()))
    n = notice_of('2. 입찰참가자격', '가. 경쟁입찰 참가자격을 갖춘 자')
    multi, single = FakeBundle(n, region_flag='Y', region_sido={'서울특별시', '경기도'}), FakeBundle(n, region_flag='Y', region_sido={'경기도'})
    two = notice_of('2. 입찰참가자격', '가. 입찰공고일 전일부터 입찰일까지 법인등기부상 본점 소재지가 서울특별시 또는 경기도에 있는 업체')
    one = notice_of('2. 입찰참가자격', '가. 입찰공고일 전일부터 입찰일까지 법인등기부상 본점 소재지가 경기도에 있는 업체')
    assert [judge.v7(b) for b in (multi, single, FakeBundle(two), FakeBundle(one))] == [None, None, None, None]
    monkeypatch.setattr(switches, 'V7_META_MULTI', True)
    assert judge.v7(multi) is True and judge.v7(single) is None
    monkeypatch.setattr(switches, 'V7_CLAUSE', True)
    assert judge.v7(FakeBundle(two)) is two.lines[-1] and judge.v7(FakeBundle(one)) is None


# ---------------------------------------------------------------- v1 v2 v4 (audit RD)
def test_perf_unread_held_record_line(monkeypatch):
    n = notice_of('2. 입찰참가자격', '가. 최근 3년 이내 전시물 제작 경험이 있는 업체')
    registered = notice_of('2. 입찰참가자격', '가. 나라장터에 경험 보유 업체로 등록한 업체')
    b = FakeBundle(n)
    assert judge.perf_lines(b) == [] and judge.v2(b) is None
    monkeypatch.setattr(switches, 'PERF_UNREAD', True)
    assert judge.perf_lines(b) == [n.lines[-1]] and judge.v2(b) is n.lines[-1]
    assert judge.perf_lines(FakeBundle(registered)) == []


def test_v1_region_facility(monkeypatch):
    facility = notice_of('2. 입찰참가자격', '가. 경기도에 소재한 교육장을 보유한 업체')
    office = notice_of('2. 입찰참가자격', '가. 본점 소재지가 경기도에 소재한 사업장을 보유한 업체')
    assert judge.v1(FakeBundle(facility)) is None
    monkeypatch.setattr(switches, 'V1_REGION_FACILITY', True)
    assert judge.v1(FakeBundle(facility)) is facility.lines[-1]
    assert judge.v1(FakeBundle(office)) is None                           # the bidder's own office is v5–v7's
    assert judge.v1(FakeBundle(facility, local_private=True)) is None


def test_buyer_certifier_is_no_buyer(monkeypatch):
    certified, buyer = '국가에서 인증받은 제품을 납품한 실적이 있는 업체', '국가기관에 납품한 실적이 있는 업체'
    assert judge.buyer_limit(certified) == 'specific' and judge.buyer_limit(buyer) == 'specific'
    monkeypatch.setattr(switches, 'BUYER_CERTIFIER', True)
    assert judge.buyer_limit(certified) is None and judge.buyer_limit(buyer) == 'specific'


def test_af3_items_scope_all_three_fix_rounds(monkeypatch):
    seen = {}
    for it in ('v1', 'v2'):
        monkeypatch.setitem(judge.RULES, it, lambda b, it=it: seen.setdefault(
            it, (switches.AUDIT_FIXES, switches.AUDIT_FIXES2, switches.AUDIT_FIXES3)) and None)
    monkeypatch.setattr(switches, 'AF3_ITEMS', ('v1',))
    judge.judge(service('가. 용역명 : 시설 청소 용역'))
    assert seen == {'v1': (True, True, True), 'v2': (False, False, False)}
    assert (switches.AUDIT_FIXES, switches.AUDIT_FIXES2, switches.AUDIT_FIXES3) == (False, False, False)


# ---------------------------------------------------------------- v20 (expert audit X6)
def test_v20_content_title_refuses_the_sw_framing(monkeypatch):
    titles = {'content': '가. 용역명 : 디지털 교육콘텐츠 개발 용역(제한경쟁·1억원미만)',
              'PC distribution': '가. 용역명 : 사랑의 그린PC 보급사업 용역(제한경쟁·1억원미만)',
              'orderer token': '가. 용역명 : [수요기관(교육청)] 학사행정시스템 유지보수 용역(제한경쟁·1억원미만)',
              'education system': '가. 용역명 : 교육행정정보시스템 유지보수 용역',
              'system after the word': '가. 용역명 : 교육 관리 시스템 구축 용역'}
    before = {k: service(t, license_text=SW_LICENSE) for k, t in titles.items()}
    assert all(facts.sw_license_framing(b) and b.sw_project == SW_PROJECT and judge.v20(b) is True for b in before.values())
    assert {k for k, b in before.items() if facts.not_sw_title(b)} == {'content', 'PC distribution'}
    monkeypatch.setattr(switches, 'V20_CONTENT', True)
    after = {k: service(t, license_text=SW_LICENSE) for k, t in titles.items()}
    refused = {k for k, b in after.items() if not facts.sw_license_framing(b) and not facts.sw_named(b)}
    assert refused == {'content', 'PC distribution'}
    assert {k for k, b in after.items() if judge.v20(b) is True} == {'orderer token', 'education system', 'system after the word'}


def test_v20_content_takes_the_decree_41_statement(monkeypatch):
    q = '가. 「소프트웨어 진흥법 시행령」 제41조(중소 소프트웨어사업자의 기준) 제1항 제1호 또는 제2호에 해당하는 자'
    b = service('가. 용역명 : 통합홈페이지 시스템 고도화', qual=q, license_text=SW_LICENSE)
    assert not judge.sw_statement(b) and judge.v20(b) is True
    monkeypatch.setattr(switches, 'V20_CONTENT', True)
    assert judge.sw_statement(b) and judge.v20(b) is None


def test_v20_orderer_outside_the_decree(monkeypatch):
    def sw_bundle(*texts):
        b = FakeBundle(notice_of(*texts), private=False)
        b.sw_project = SW_PROJECT
        return b
    college = sw_bundle('[수요기관(대학)] 공고', '[수요기관(대학)] 재무팀', '문의: [기관(공공기관)]')
    public = sw_bundle('[수요기관(공공기관)] 공고', '[수요기관(공공기관)] 재무팀', '문의: [기관(대학)]', '[기관(대학)]', '[기관(대학)]')
    unknown = sw_bundle('공고', '재무팀')
    assert [judge.unbound_orderer(b) for b in (college, public, unknown)] == [True, False, False]
    assert [judge.v20(b) for b in (college, public, unknown)] == [True, True, True]
    monkeypatch.setattr(switches, 'V20_ORDERER', True)
    assert [judge.v20(b) for b in (college, public, unknown)] == [None, True, True]


# ---------------------------------------------------------------- v22 (audit RE, expert audit X6)
def test_v22_registration_rule_is_no_briefing(monkeypatch):
    def brief_bundle(*texts):
        n = notice_of(*texts)
        ln = n.lines[-1]
        return FakeBundle(n, {('brief', ln.i): {'참석': '참석해야 입찰·제안 가능'}}, {'brief': [ln]}, negotiation=True), ln
    registration, ln1 = brief_bundle('5. 제출서류', '3) 규격제안서 제출 및 제안 설명회 등록 시 대표자가 등록하여야만 함. 다만,대표자가')
    briefing, ln2 = brief_bundle('5. 현장설명회', '가. 현장설명회에 참석한 업체에 한하여 입찰참가자격을 부여함')
    assert judge.v22(registration) is ln1 and judge.v22(briefing) is ln2
    monkeypatch.setattr(switches, 'V22_PRESENTATION', True)
    assert judge.v22(registration) is None and judge.v22(briefing) is ln2


def test_x6_list_entry_line_is_complete_and_post_heading_names_documents():
    # A flattened bid-document list ("… 각 1부") is not joined with the next line, and "납품 및 검수 완료" heads no list.
    n = notice_of('나. 납품기한: 2026. 2. 23.까지 납품 및 검수 완료', '3. 입찰참가자격',
                  '1) 입찰관련서류 1-1) 사업자등록증 1부 1-2) 제조회사 공급 증명원 및 A/S 확약서(원본) 각 1부',
                  '2) 낙찰자는 계약 체결 시 제출')
    ln = line(n, '1) 입찰관련서류')
    assert judge.x6_pledge_stage(FakeBundle(n), ln) == 'OTHER' and judge.x6_pledge_violation(FakeBundle(n), ln)
    n = notice_of('가. 계약 체결 시 제출서류', '1) 제조사 기술지원 확약서 1부.')
    assert judge.x6_pledge_stage(FakeBundle(n), n.lines[-1]) == 'POST'


# ---------------------------------------------------------------- v9 (b) and v12 (expert audit X3)
def test_v9_x3b_vehicle_model_and_brand(monkeypatch):
    van = goods('승합자동차[2510150301]', '가. 품명 : 차 종 스타리아 하이브리드 투어러 11인승')
    charger = goods('전기자동차용충전장치[2611170403]', '가. 품명 : 급속충전기 (디스플레이 포함)')
    brand = goods('노트북컴퓨터[4321150201]', '가. 품명 : 삼성전자 갤럭시 북4 NT750XGR')
    brand_eq = goods('노트북컴퓨터[4321150201]', '가. 품명 : 삼성전자 갤럭시 북4 NT750XGR 또는 동등 이상')
    assert judge.x3b_line(van) is not None and judge.x3b_line(brand) is not None
    assert judge.x3b_line(charger) is None and judge.x3b_line(brand_eq) is None
    assert judge.v9(van) is None
    monkeypatch.setattr(switches, 'V9_X3B', True)
    assert judge.v9(van) is judge.x3b_line(van)
    assert judge.X3B_VEHICLE_MODEL.search('기아 레이 밴') and not judge.X3B_VEHICLE_MODEL.search('파노라믹 커브드 디스플레이')


def test_v12_x3_alternative_route_and_manufacturer_only(monkeypatch):
    alt = notice_of('2. 입찰참가자격', '가. 직접생산확인증명서 또는 정품 공급증명서를 제출한 업체')
    assert judge.V12_ALT.search(alt.lines[-1].text) and judge.V12_VERIFY2.search('확인이 안 되거나 유효기간이 지난 경우')
    maker = goods('오일필터[2610150301]', '가. 품명 : 차량용 오일필터', qual='가. 해당 물품을 생산하는 제조업체이어야 합니다.')
    dealer = goods('승합자동차[2510150301]', '가. 품명 : 통학차량',
                   qual='가. 자동차 제조 공장등록 증명서 또는 공급자의 공급사 증명서 소지한 업체여야 합니다.')
    special = goods('오일필터[2610150301]', '가. 품명 : 차량용 오일필터', qual='가. 해당 물품을 생산하는 제조업체이어야 합니다.',
                    clause='특수한설비,기술의보유및실적(물품제조)')
    assert judge.v12_manuf_line(maker) is not None
    assert judge.v12_manuf_line(dealer) is None and judge.v12_manuf_line(special) is None
    monkeypatch.setattr(switches, 'V12_MANUF', True)
    assert maker.scope.competitive is False
    assert judge.v12(maker) is judge.v12_manuf_line(maker)


def test_v20_content_keeps_atms_and_informatization_runs():
    b = service('가. 용역명 : 교통정보센터 ATMS 유지보수 용역(제한경쟁·1억원미만)', license_text=SW_LICENSE)
    assert not facts.not_sw_title(b)
    b = service('가. 용역명 : 2026년 정보화사업 운영 지원 용역(제한경쟁·1억원미만)', license_text=SW_LICENSE)
    assert not facts.not_sw_title(b)
    b = service('가. 용역명 : 슬기로운 동네생활 SW사업 운영 용역(제한경쟁·1억원미만)', license_text=SW_LICENSE)
    assert facts.not_sw_title(b)


def line(n, start):
    return next(x for x in n.lines if x.text.startswith(start))


def test_x6_list_subheadings_and_qualification_stage_heading():
    n = notice_of('5. 제출서류', '가. 계약 전', '1) 제조사의 무상보증(2년) 확약서 1부')
    assert judge.x6_pledge_stage(FakeBundle(n), n.lines[-1]) == 'POST'
    n = notice_of('4. 제출 서류 (적격심사 시 제출)', '① 제조사 공급 확약서 및 사후봉사(A/S) 확약서 각 1 부.')
    assert judge.x6_pledge_stage(FakeBundle(n), n.lines[-1]) == 'QUAL_STAGE'
    n = notice_of('8. 제출 서류', '가. 사업자등록증 사본 1부', '다. 제조사의 물품공급 및 기술지원확약서 사본 1부')
    assert judge.x6_pledge_stage(FakeBundle(n), n.lines[-1]) == 'PRE_LIST'


def test_v19_cpu_timed_counts_a_bid_timed_demand(monkeypatch):
    untimed = {'발급 주체': '제3자(제조사·공급사·기술지원사)', '시점': '제출 가능 여부만(시점 없음)'}
    n = notice_of('2. 입찰참가자격', '- 입찰참가업체는 제조사로부터 기술서비스 확약서를 보유해야 한다.')
    ln = n.lines[-1]
    b = FakeBundle(n, {('pledge', ln.i): untimed}, {'pledge': [ln]})
    monkeypatch.setattr(switches, 'V19_STAGE', True)
    assert judge.v19(b) is None
    monkeypatch.setattr(switches, 'V19_CPU_TIMED', True)
    assert judge.v19(b) is ln


def test_v12_title_object2_names_a_non_event_object():
    trip = service('가. 용역명 : 2026학년도 1학년 수련활동 위탁용역(숙박)(제한경쟁·1억원미만)')
    course = service('가. 용역명 : 공공기관 국제연수과정 위탁운영 용역(제한경쟁·1억원미만)')
    research = service('가. 용역명 : 지역 관광 활성화 방안 연구 용역(제한경쟁·1억원미만)')
    fair = service('가. 용역명 : 진로교육 박람회 운영 용역(제한경쟁·1억원미만)')
    content = service('가. 용역명 : 디지털 교육 콘텐츠 개발 용역(제한경쟁·1억원미만)')
    assert judge.v12_other_object(trip) and judge.v12_other_object(course) and judge.v12_other_object(research)
    assert not judge.v12_other_object(fair) and not judge.v12_other_object(content)
