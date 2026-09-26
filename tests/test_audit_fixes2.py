"""Round-2 audit fixes behind switches.AUDIT_FIXES2 (runs/rebuild_c/transfer_20260925/audit/R2-A..E.md). Each test shows the
round-1 behaviour (AUDIT_FIXES on, AUDIT_FIXES2 off) and the fixed behaviour with AUDIT_FIXES2 on."""
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import amounts, catalog, dates, facts, judge, record, regions, switches  # noqa: E402
from pps_c.families import brief_select, perf_select  # noqa: E402


@pytest.fixture
def fix2(monkeypatch):
    monkeypatch.setattr(switches, 'AUDIT_FIXES', True)

    def turn(on):
        monkeypatch.setattr(switches, 'AUDIT_FIXES2', on)
    turn(False)
    return turn


class Line:
    def __init__(self, text, i=0, doc=0, doc_type='공고문', sec='QUAL'):
        self.text, self.i, self.doc, self.doc_type, self.sec = text, i, doc, doc_type, sec


def notice_of(*texts, doc_type='공고문'):
    return record.build({'id': 'T', 'meta': {}, 'docs': [{'type': doc_type, 'text': '\n'.join(texts)}]})


def line(n, start):
    return next(x for x in n.lines if x.text.startswith(start))


class FakeBundle:
    def __init__(self, notice, readings=None, cands=None, **meta):
        self.notice, self._readings, self.cands = notice, readings or {}, cands or {}
        base = {'P': 5e7, 'B': 5.5e7, 'law': '국가', 'region_sido': set(), 'region_basic': 0, 'T_lo': 2.3e8, 'T_hi': 2.3e8,
                'local_private': False, 'method': '제한경쟁', 'posted': None}
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


def values(text):
    return [m.value for m in amounts.money(text)]


def test_switch_defaults_to_current():
    assert switches.AUDIT_FIXES2 is False


def test_korean_compound_amounts(fix2):
    assert values('2천5백만원') == [5e6]
    fix2(True)
    assert values('2천5백만원') == [2.5e7]
    assert values('3억 5천 2백만원 이상') == [3.52e8]
    assert values('1억2,500만원') == [1.25e8]
    assert values('일금 삼천만원정') == [3e7] and values('금오억원 이상') == [5e8]
    assert values('천만원 이상') == [1e7] and values('5억 이상') == [5e8]
    assert values('150,000원') == [1.5e5] and values('25,000천원') == [2.5e7]
    # table units, a lone unit, a counter and a word ending in a digit syllable are no amounts
    assert values('(단위: 백만원)') == [] and values('억원') == [] and values('10만 이상') == [] and values('검사 백만원') == []


def test_short_year_dates(fix2):
    text = '사업설명회 : ’26. 2. 3. 14:00'
    assert dates.find(text, 2026) == []
    fix2(True)
    assert [d for d, _ in dates.find(text, 2026)] == [dt.date(2026, 2, 3)]


def test_region_groups_and_city_forms(fix2):
    assert regions.mentions('대전 또는 충청 지역에 소재한 업체')['sido'] == {'대전광역시'}
    assert regions.mentions('수도권 소재의 대학교')['sido'] == set()
    fix2(True)
    assert regions.mentions('대전 또는 충청 지역에 소재한 업체')['sido'] == {'대전광역시', '충청북도', '충청남도'}
    assert regions.mentions('충청권 소재 업체')['sido'] == {'대전광역시', '세종특별자치시', '충청북도', '충청남도'}
    assert regions.mentions('소재지가 경기도, 강원도, 충청도에 소재하고')['sido'] == {'경기도', '강원특별자치도', '충청북도', '충청남도'}
    assert regions.mentions('수도권 소재의 대학교')['sido'] == {'서울특별시', '인천광역시', '경기도'}
    assert regions.mentions('서울시를 비롯한 경기도 수도권')['sido'] == {'서울특별시', '인천광역시', '경기도'}
    assert regions.mentions('본사가 충청남도에 있는 업체')['sido'] == {'충청남도'}
    assert regions.mentions('수도권정비계획법에 따른 과밀억제권역')['sido'] == set()


def test_region_none_and_attachment_locations(fix2, monkeypatch):
    rec = {'id': 'T', 'meta': {}, 'docs': [
        {'type': '공고문', 'text': '2. 입찰참가자격\n가. 본점 소재지가 강원특별자치도에 있는 업체\n나. 참가지역제한 : 없음'},
        {'type': '과업지시서', 'text': '1. 참가자격\n가. 본점 소재지가 울산광역시인 업체'}]}
    n = record.build(rec)
    own, none, att = line(n, '가. 본점 소재지가 강원'), line(n, '나. 참가지역'), line(n, '가. 본점 소재지가 울산')
    monkeypatch.setattr(judge, 'lines_where', lambda *a, **k: [own, none, att])
    b = FakeBundle(n)
    assert judge.region_restriction(b)[1] == {'강원특별자치도', '울산광역시'}
    fix2(True)
    lines, sido, _ = judge.region_restriction(b)
    assert lines == [own, att] and sido == {'강원특별자치도'}
    monkeypatch.setattr(judge, 'lines_where', lambda *a, **k: [none])
    assert judge.region_restriction(b)[0] == []


def test_evaluation_record_text_is_no_qualification(fix2, monkeypatch):
    n = notice_of('2. 입찰참가자격',
                  '가. 최근 3년 이내 단일 용역 5억원 이상 수행 실적이 있는 업체',
                  '나. 단, 참가자격에 따른 실적은 수급체 중 1개사만 갖추어도 됨',
                  '다. 인사사고 업체는 입찰서를 제출할 수 없으며, 낙찰자는 무사고 운행실적 확인서를 제출',
                  '실적 / 없음',
                  '3) 심사기준',
                  '가) 이행실적 : 최근 3년간 동일 용역 수행 실적',
                  '① 이행실적의 당해 용역규모 : 금액기준')
    perf = [x for x in n.lines if x.text not in ('2. 입찰참가자격', '3) 심사기준')]
    monkeypatch.setattr(judge, 'lines_where', lambda b, fam, section=False, **want: perf if section else [])
    b = FakeBundle(n)
    assert judge.perf_lines(b) == perf
    fix2(True)
    assert judge.perf_lines(b) == [perf[0]]


def test_record_amount_on_the_next_parenthetical_line(fix2, monkeypatch):
    n = notice_of('2. 입찰참가자격', '가. 최근 3년 이내 동종 용역을 1건 이상 수행한 업체', '(단일 건 계약금액 5억원 이상)')
    ln = line(n, '가.')
    monkeypatch.setattr(judge, 'perf_lines', lambda b: [ln])
    b = FakeBundle(n, P=1.8e8, B=2e8)
    assert judge.v3(b) is None
    fix2(True)
    assert judge.v3(b) is ln


def test_firm_experience_and_site_visit_candidates(fix2):
    n = notice_of('2. 입찰참가자격', '가. 애니메이션 3작품 이상 제작 경험이 있는 업체', '나. 넥타이 제작을 수행한 자',
                  '다. 5억원 이상의 동종 용역을 1건 이상 수행한 업체', '라. 책임연구원은 관련 분야 경력 5년 이상인 자')
    visit = notice_of('4. 현장답사', '가. 현장답사에 참석한 업체에 한하여 입찰에 참가할 수 있음', '나. 현장답사 일정: 3월 5일')
    assert perf_select(n) == [] and brief_select(visit) == []
    fix2(True)
    assert [x.text[:2] for x in perf_select(n)] == ['가.', '나.', '다.']
    assert [x.text[:2] for x in brief_select(visit)] == ['가.']


def test_size_clause_typography_and_names(fix2):
    cases = {'가. 중⸱소기업자 또는 소상공인으로서 확인서를 소지한 자': ('small', 'sme'),
             '가. 중・소기 업 또는 소상공인으로서 확인서를 소지한 자': ('small', 'sme'),
             '가. 중소기업자 또는 소상공인으로, 중소기업 ·소기업(소상공인)확인서를 소지한 업체': ('small', 'sme'),
             '가. 중기업,소기 업,소상공인확인서를 소지한 자': ('small', 'sme'),
             '가. 제2조의2(중소기업자의 우선조달계약)에 따라 소기업 또는 소상공인으로 제한': ('sme', 'small'),
             '가. 중소기업을 창업하여 7년이 지나지 않은 자': ('sme', None),
             '가. 중소기업 및 소상공인으로서 발급된 중소기업확인서(소기업·소상공인)를 소지한 자': ('sme', 'small')}
    for text, (off, on) in cases.items():
        n = notice_of('2. 입찰참가자격', text)
        ln, b = line(n, '가.'), FakeBundle(n)
        fix2(False)
        assert judge.size_class(b, ln) == off, text
        fix2(True)
        assert judge.size_class(b, ln) == on, text


def test_size_state_reads_eligibility_and_declarations(fix2):
    n = notice_of('2. 입찰참가자격', '※ 입찰참가업체는 「중소기업기본법」 제2조에 따른 소기업 또는 소상공인으로서 소기업·소상공인 확인서를 제출해야 함')
    ln = line(n, '※')
    b = FakeBundle(n, {('size', ln.i): {'역할': '제출서류'}}, {'size': [ln]})
    assert judge.size_state(b)[0] is None
    fix2(True)
    assert judge.size_state(b)[0] == 'small' and judge.size_state(b, positive=True)[0] == 'small'
    bid = notice_of('1. 입찰 및 계약방법', '가. 입찰방법 : 제한경쟁(소기업)', '나. 「중소기업기본법」 제2조에 따른 중소기업자로서 중소기업 확인서를 소지한 자',
                    '2. 입찰참가자격', '가. 경쟁입찰 참가자격을 갖춘 자')
    tag, stated = line(bid, '가. 입찰방법'), line(bid, '나.')
    assert tag.sec == stated.sec == 'BID'
    read = {('size', x.i): {'역할': '참가자격 제한'} for x in (tag, stated)}
    b = FakeBundle(bid, read, {'size': [tag, stated]})
    fix2(False)
    assert judge.size_state(b, positive=True)[0] is None
    fix2(True)
    assert judge.size_state(b, positive=True)[1] == [stated]
    decl = notice_of('5. 낙찰자 결정방법', '가. 본 입찰은 ‘소기업 또는 소상공인’ 간 우선조달계약 대상이며 적격심사로 결정')
    b = FakeBundle(decl)
    fix2(False)
    assert judge.size_state(b)[0] is None
    fix2(True)
    assert judge.size_state(b)[0] == 'small'


def test_size_state_drops_sw_and_verification_lines(fix2):
    n = notice_of('2. 입찰참가자격',
                  '가. 중소기업자로서 중소기업 확인서를 소지한 자',
                  '※ 소기업·소상공인 확인서가 종합정보망에서 확인이 안 될 경우라도 신청한 업체는 인정',
                  '나. ｢중소 소프트웨어사업자의 사업 참여 지원에 관한 지침｣에 따라 대기업인 소프트웨어사업자는 참여할 수 없으며 중소기업만 참여 가능')
    main, note, sw = line(n, '가.'), line(n, '※'), line(n, '나.')
    read = {('size', x.i): {'역할': '참가자격 제한'} for x in (main, note, sw)}
    b = FakeBundle(n, read, {'size': [main, note, sw]})
    assert judge.size_state(b)[1] == [main, note, sw]
    fix2(True)
    assert judge.size_state(b)[1] == [main] and judge.size_state(b)[0] == 'sme'
    only_sw = FakeBundle(n, read, {'size': [sw]})
    assert judge.size_state(only_sw)[0] is None


def test_flattened_block_with_an_sw_sentence_keeps_its_class(fix2):
    n = notice_of('2. 입찰참가자격', '가. ○ 소기업 또는 소상공인으로서 소기업·소상공인 확인서를 소지한 자 '
                  '○ ｢중소 소프트웨어사업자의 사업 참여 지원에 관한 지침｣에 따라 대기업인 소프트웨어사업자는 참여할 수 없음')
    flat = line(n, '가.')
    b = FakeBundle(n, {('size', flat.i): {'역할': '참가자격 제한'}}, {'size': [flat]})
    fix2(True)
    assert judge.size_state(b)[0] == 'small'


def test_size_note_line_does_not_borrow_the_clause_above(fix2):
    n = notice_of('2. 입찰참가자격', '가. 「중소기업기본법」 제2조에 따른 중소기업자로서',
                  '「중소기업 범위 및 확인에 관한 규정」에 따라 발급된 중소기업 확인서를 소지한 자',
                  '* 소상공인 확인서는 마감일 전일까지 발급된 것으로 함',
                  '나. 4) 총 계약금액 1억원 미만: 소기업 또는 소상공인으로서 소기업·소상공인 확인서를 소지한 자')
    main = line(n, '가.')
    b = FakeBundle(n, {('size', main.i): {'역할': '참가자격 제한'}}, {'size': [main]})
    fix2(True)
    assert judge.size_state(b, positive=True)[1] == [main] and judge.size_state(b)[0] == 'sme'


def inst_bundle(*texts, reading=None, method='제한경쟁'):
    n = notice_of('2. 입찰참가자격', *texts)
    ln = n.lines[-1]
    r = reading or {'역할': '참가자격', '요건': '기관 유형 한정'}
    return FakeBundle(n, {('inst', ln.i): r}, {'inst': [ln]}, method=method), ln


def test_v1_alternatives_that_admit_firms(fix2):
    joined, ln1 = inst_bundle('가. 대학, 연구기관이나 업체에 한함')
    listed, ln2 = inst_bundle('가. 다음 중 하나에 해당하는 자', '(가) 중소기업 또는 소상공인', '(나) 대학 또는 연구기관')
    assert judge.v1(joined) is ln1 and judge.v1(listed) is ln2
    fix2(True)
    assert judge.v1(joined) is None and judge.v1(listed) is None
    excluded, ln3 = inst_bundle('가. 대학 또는 연구기관에 한하며, 업체는 참여할 수 없음')
    assert judge.v1(excluded) is ln3


def test_v1_exclusions_disclaimers_buyers_and_size_definitions(fix2):
    assert judge.institution_limit('* 비영리법인 참여불가')
    fix2(True)
    assert not judge.institution_limit('* 비영리법인 참여불가')
    assert not judge.institution_limit('비영리법인의 투찰 불가')
    assert judge.institution_limit('영리법인 참여불가')
    cases = [inst_bundle('가. 대학 및 연구기관에 한함 (단, 입찰제한을 두는 것은 아님)'),
             inst_bundle('가. 발주처가 정부 또는 공공기관인 용역 실적이 있는 업체'),
             inst_bundle('③ 중기업 : 중소기업기본법 시행령 제3조에 따른 상시근로자수 50명 이상 300명 미만',
                         reading={'역할': '참가자격', '요건': '기타'})]
    fix2(False)
    assert [judge.v1(b) for b, _ in cases] == [ln for _, ln in cases]
    fix2(True)
    assert [judge.v1(b) for b, _ in cases] == [None, None, None]


def test_v4_private_first_buyer_list_and_operation(fix2):
    assert judge.buyer_limit('민간기업 및 공공기관 대상 교육 운영 실적이 있는 업체') == 'specific'
    assert judge.buyer_limit('300명 이상의 고등학교 기숙사를 위탁 운영한 실적') is None
    fix2(True)
    assert judge.buyer_limit('민간기업 및 공공기관 대상 교육 운영 실적이 있는 업체') == 'open'
    assert judge.buyer_limit('300명 이상의 고등학교 기숙사를 위탁 운영한 실적') == 'specific'


def test_v6_registered_basic_units(fix2, monkeypatch):
    n = notice_of('2. 입찰참가자격', '가. 본점 소재지가 경기도 여주, 양평에 있는 업체')
    ln = line(n, '가.')
    monkeypatch.setattr(judge, 'region_restriction', lambda b: ([ln], {'경기도'}, set()))
    b = FakeBundle(n, region_basic=2)
    assert judge.v6(b) is None
    fix2(True)
    assert judge.v6(b) is ln
    assert judge.v6(FakeBundle(n, region_basic=0)) is None


def test_v9_designation_needs_a_named_maker(fix2):
    for text in ('브랜드 캐릭터를 활용한 IP 마케팅 상품 제작', '투찰한 업체명과 동일한 브랜드의 제품을 납품할 것', '모델명 :',
                 '○○○제조회사 : 설비제작', '모델 : 130cm', '제조사/모델명 :', '직영 서비스센터 운영중인 제조사 제품에 한함'):
        assert not judge.designates(text), text
    for text in ('제조사 : 삼성전자, 모델명 : ABC-100', '모델명 : 3M 8210', '○○ 브랜드 제품일 것', '정품만 납품'):
        assert judge.designates(text), text
    n = notice_of('3. 규격', '가. 모델명 :')
    ln = line(n, '가.')
    b = FakeBundle(n, {('model', ln.i): {'성격': '구매 대상의 제조사·모델 지정'}}, {'model': [ln]})
    assert judge.v9(b) is ln
    fix2(True)
    assert judge.v9(b) is None


def test_v9_broad_fires_on_a_bare_model_code(fix2, monkeypatch):
    assert switches.V9_BROAD is False
    n = notice_of('3. 규격', '가. 파종기(DPM-8000GMP) 1대', '나. USB 3.0 포트 2개 이상', '다. 파종기 1대')
    lns = [line(n, s) for s in ('가.', '나.', '다.')]
    b = FakeBundle(n, {('model', ln.i): {'성격': '구매 대상의 제조사·모델 지정'} for ln in lns}, {'model': lns})
    assert judge.v9(b) is None
    monkeypatch.setattr(switches, 'V9_BROAD', True)
    assert judge.v9(b) is lns[0]
    fix2(True)
    assert judge.v9(b) is lns[0]
    rest = FakeBundle(n, {('model', ln.i): {'성격': '구매 대상의 제조사·모델 지정'} for ln in lns[1:]}, {'model': lns[1:]})
    assert judge.v9(rest) is None


def test_v12_certificate_named_in_the_evidence_sentence():
    clause = '가. G2B 물품분류번호 제조물품으로 등록된 업체. 직접생산확인증명서는 입찰마감일 전일까지 발급받은 것에 한함'
    assert judge.DP_CERT.search(clause)
    assert not judge.DP_CERT.search(judge.evidence_sentence(clause, '가. G2B 물품분류번호 제조물품으로 등록된 업체.'))
    assert judge.DP_CERT.search(judge.evidence_sentence(clause, '직접생산확인증명서는 입찰마감일 전일까지 발급받은 것에 한함'))


def pledge_bundle(*texts, timing='불명', doc_type='공고문'):
    n = notice_of(*texts, doc_type=doc_type)
    ln = n.lines[-1]
    r = {'발급 주체': '제3자(제조사·공급사·기술지원사)', '시점': timing}
    return FakeBundle(n, {('pledge', ln.i): r}, {'pledge': [ln]}), ln


def test_v19_pre_award_lists_documents_and_capability(fix2, monkeypatch):
    listed, ln = pledge_bundle('3. 제출서류', '가. 입찰참가신청서 1부', '나. 제조사 공급증명원 1부')
    contract, _ = pledge_bundle('나. 계약시 구비서류', '1) 제조사 공급확약서 1부')
    no_doc, ln2 = pledge_bundle('2. 입찰참가자격', '가. 제조사 공식 고객서비스센터를 보유한 업체', timing='입찰 전 발급·보유 또는 입찰서와 함께 제출')
    scored, _ = pledge_bundle('5. 평가기준', '가. 제출서류', '- 제조자 증명원 및 A/S 확약서')
    for x in scored.notice.lines:
        x.sec = 'EVAL'      # a scored document list inside an evaluation table
    capable = Line('제조사로부터 기술지원확약서를 제출할 수 있는 업체')
    advice = Line('※ 발주기관과 제조사간 기체결한 협약서 사본을 근거로 발급권자에게 기술지원확약서를 발급받을 수 있음')
    assert judge.v19(listed) is None and judge.v19(no_doc) is ln2
    assert judge.pledge_demanded(capable) and judge.pledge_demanded(advice)
    fix2(True)
    assert judge.v19(listed) is None             # the list-stage inference is its own switch (dev labels disagree)
    monkeypatch.setattr(switches, 'V19_LIST_STAGE', True)
    assert judge.v19(listed) is ln and judge.v19(contract) is None and judge.v19(scored) is None
    attached, _ = pledge_bundle('3. 제출서류', '가. 입찰참가신청서 1부', '나. 제조사 공급증명원 1부', doc_type='과업지시서')
    assert judge.v19(attached) is None
    reference, _ = pledge_bundle('2. 입찰참가자격', '※ 제조사와 발주기관의 기술지원협약서는 [붙임2] 참고')
    assert judge.v19(reference) is None
    assert judge.v19(no_doc) is None and not judge.pledge_demanded(capable) and not judge.pledge_demanded(advice)
    between, _ = pledge_bundle('※ 기술지원확약서의 발급은 기술지원사와 낙찰자(또는 계약상대자) 간에 세부협약으로 이루어짐')
    agreement, ln3 = pledge_bundle('2. 입찰참가자격', '나. 제조업체로부터 정품공급협약서를 발급받아 제출', timing='입찰 전 발급·보유 또는 입찰서와 함께 제출')
    assert not judge.pledge_document(between, between.notice.lines[-1]) and judge.v19(agreement) is ln3


def test_v21_fraction_in_a_joint_contract_line(fix2):
    n = notice_of('5. 공동계약', '가. 공동수급체 구성원별 최소 지분율은 100분의 5 이상으로 한다')
    boiler = notice_of('가. 주식·지분 총수의 100분의 30 이상을 소유한 자')
    assert judge.v21(FakeBundle(n)) is None
    fix2(True)
    assert judge.v21(FakeBundle(n)) is line(n, '가.')
    assert judge.v21(FakeBundle(boiler)) is None


def test_v22_bidder_presentation_and_quoted_invalidity(fix2):
    presenter = Line('발표자는 제안사의 직원으로 하며 제안설명회 참가자 전원의 재직증명서를 제출')
    quoted = Line('현장설명에 참가를 요하는 입찰에 있어서 당해 현장설명에 참가하지 아니한 자가 한 입찰')
    assert judge.bid_briefing(presenter) and judge.bid_briefing(quoted)
    fix2(True)
    assert not judge.bid_briefing(presenter) and not judge.bid_briefing(quoted)
    assert judge.bid_briefing(Line('사업설명회에 참석한 업체에 한하여 제안서를 제출할 수 있음'))
    assert judge.bid_briefing(Line('사업설명회 참석 시 재직증명서를 지참하여야 함'))


def test_v23_briefing_date_below_its_heading(fix2):
    n = notice_of('7. 제안요청 설명회', '- 장소 : 본관 3층 회의실', '- 대상 : 입찰참가 희망업체', '- 내용 : 사업 개요 및 과업 설명',
                  '- 일시 : 2026. 2. 3.(화) 14:00', '8. 제안서 제출')
    ln = line(n, '7.')
    b = FakeBundle(n, {('brief', ln.i): {'참석': '참석해야 입찰·제안 가능'}}, {'brief': [ln]}, posted=dt.date(2026, 1, 28))
    assert judge.briefing_date(b) == (None, None)
    fix2(True)
    assert judge.briefing_date(b) == (dt.date(2026, 2, 3), ln)


def test_v24_rounding_breakdown_and_licence_alternatives(fix2):
    assert not judge.agrees(414545460, 414545455) and not judge.agrees(349090900, 349090909)
    b = FakeBundle(notice_of('추정가격 : 1,000,000,000원', '1차년도 추정가격 : 572,727,273원', '2차년도 추정가격 : 427,272,727원'),
                   P=1e9, B=1.1e9)
    assert judge.v24_amount_parts(b) is not None
    fix2(True)
    assert judge.agrees(414545460, 414545455) and judge.agrees(349090900, 349090909)
    assert not judge.agrees(414545470, 414545455)
    assert judge.v24_amount_parts(b) is None
    assert judge.LICENSE_ALT.search('수집·운반 능력을 갖춘 자 : 폐기물수집운반업(업종코드 6728)으로 등록한 자 또는 장비기준을 충족한 자')
    assert not judge.LICENSE_ALT.search('건물위생관리업(업종코드 4993)을 등록한 자')


def test_service_scope_titles_and_licences(fix2):
    titles = {'용역명 : 공공디자인 진흥계획 수립 용역': ('service:design', 'service:none'),
              '용역명 : 디자인-기술협업 액셀러레이팅 프로그램 운영': ('service:design', 'service:none'),
              '용역명 : 브랜드 디자인 개발 용역': ('service:design', 'service:design'),
              '용역명 : 에코업(業) 페어 운영 대행': ('service:none', 'service:exhibition'),
              '용역명 : 영상 및 인쇄 광고물 제작용역': ('service:none', 'service:video')}
    for title, (off, on) in titles.items():
        fix2(False)
        assert service(title).scope.basis == off, title
        fix2(True)
        assert service(title).scope.basis == on, title
    tank = ('용역명 : 목포항 청소 용역', '가. 저수조청소업 등록 업체', '[저수조청소업(4993)]')
    building = ('용역명 : 청사 청소 용역', '가. 건물위생관리업 등록 업체', '[건물위생관리업(4993)]')
    fix2(False)
    assert service(*tank).scope.basis == 'service:cleaning'
    fix2(True)
    assert service(*tank).scope.basis == 'service:none' and service(*building).scope.basis == 'service:cleaning'


def test_sw_service_titles(fix2):
    not_sw = ('차세대 ERP 구축 사업 감리용역(제한경쟁·3억원미만)', '통합정보시스템 구축 감리 및 개인정보 영향평가', '안전보건경영시스템 구축 용역',
              'AI챌린지 프로그램 기획 및 운영', '해외 우수 SW 경력자 채용연계 위탁')
    assert all(catalog.sw_service_title(t) for t in not_sw)
    fix2(True)
    assert not any(catalog.sw_service_title(t) for t in not_sw)
    assert catalog.sw_service_title('학사행정 정보시스템 유지관리 용역') and catalog.sw_service_title('홈페이지 구축 용역')


def test_reg_consistency_probe_helpers():
    from types import SimpleNamespace as NS
    b = lambda **m: NS(meta=NS(**{'region_flag': 'N', 'region_sido': None, 'clause': None, **m}))
    assert switches.REG_CONSISTENCY is False                     # canonical package: the DATASET-FIT probe switch is off
    assert judge.region_agrees(b(region_flag='Y', region_sido=['경기도']), {'경기도'})
    assert not judge.region_agrees(b(region_flag='Y', region_sido=['경기도']), {'경기도', '서울특별시'})
    assert not judge.region_agrees(b(region_flag='N'), {'경기도'}) and not judge.region_agrees(b(region_flag='Y', region_sido=['경기도']), set())
    assert judge.sme_registered(b(clause='[판로지원법 시행령] 중기업,소기업,소상공인 제한'))
    assert not judge.sme_registered(b(clause='[판로지원법 시행령] 소기업,소상공인제한'))
    assert not judge.sme_registered(b(clause='[판로지원법] 중기간경쟁제품 소기업 소상공인 제한경쟁, 공동사업'))


def test_af_items_scope_the_fixes_to_their_items(monkeypatch):
    seen = {}
    for it in ('v1', 'v2', 'v3'):
        monkeypatch.setitem(judge.RULES, it, lambda b, it=it: seen.setdefault(it, (switches.AUDIT_FIXES, switches.AUDIT_FIXES2)) and None)
    monkeypatch.setattr(switches, 'AF_ITEMS', ('v1',))
    monkeypatch.setattr(switches, 'AF1_ITEMS', ('v2',))
    judge.judge(service('가. 용역명 : 시설 청소 용역'))
    assert seen == {'v1': (True, True), 'v2': (True, False), 'v3': (False, False)}
    assert (switches.AUDIT_FIXES, switches.AUDIT_FIXES2) == (False, False)


def test_af_list_marker_is_not_form_wording():
    t = '(1) 최근 3년 이내 단일 용역 실적 일천만원 이상인 업체'
    assert judge.PERF_FORMISH.search(t) and not judge.PERF_FORMISH_ATTACH.search(t)
    assert judge.PERF_FORMISH_ATTACH.search('(1) 실적증명서 서식')


def test_hold_by_bid_is_timed_before_the_award():
    assert switches.V19_HOLD_BY_BID is False
    assert judge.HOLD_BY_BID.search('입찰마감일 전일까지 제조사의 물품공급확약서를 발급받아 계약 시 제출')
    assert not judge.HOLD_BY_BID.search('낙찰자는 계약 체결 시 물품공급확약서를 제출')


def test_cert_only_lines_leave_the_restriction_absent(monkeypatch):
    n = notice_of('2. 입찰참가자격', '가. 발급된 소기업·소상공인확인서의 유효기간이 지난 경우 입찰참가자격이 없습니다')
    ln = line(n, '가.')
    b = FakeBundle(n, {('size', ln.i): {'역할': '참가자격 제한'}}, {'size': [ln]})
    assert judge.size_state(b)[1] == [ln]
    monkeypatch.setattr(switches, 'CERT_ONLY_ABSENT', True)
    assert judge.size_state(b)[:2] == (None, [])
    elig = notice_of('2. 입찰참가자격', '가. 중소기업확인서를 발급받은 자로서 유효기간이 지난 경우 제외')
    e = line(elig, '가.')
    assert judge.size_state(FakeBundle(elig, {('size', e.i): {'역할': '참가자격 제한'}}, {'size': [e]}))[1] == [e]


def test_region_particle_names_the_region(monkeypatch):
    assert regions.mentions('전남의 업체로 제한')['sido'] == set()
    monkeypatch.setattr(switches, 'REGION_PARTICLE', True)
    assert regions.mentions('전남의 업체로 제한')['sido'] == {regions.canon_sido('전남')}
    assert regions.mentions('부산물 처리')['sido'] == set()


def test_v6_reg_basic_without_round_two(monkeypatch):
    n = notice_of('2. 입찰참가자격', '가. 본점 소재지가 경기도 여주, 양평에 있는 업체')
    ln = line(n, '가.')
    monkeypatch.setattr(judge, 'region_restriction', lambda b: ([ln], {'경기도'}, set()))
    b = FakeBundle(n, region_basic=2)
    assert judge.v6(b) is None
    monkeypatch.setattr(switches, 'V6_REG_BASIC', True)
    assert judge.v6(b) is ln
    assert judge.v6(FakeBundle(n, region_basic=0)) is None



def test_v24_vat_relation_and_method_axis(monkeypatch):
    assert judge.vat_related(110_000_000, 100_000_000) and judge.vat_related(100_000_000, 110_000_000)
    assert not judge.vat_related(120_000_000, 100_000_000)
    assert not judge.agrees(110_000_000, 100_000_000)
    monkeypatch.setattr(switches, 'V24_FIXES', True)
    assert judge.agrees(110_000_000, 100_000_000)
    seen = []
    monkeypatch.setattr(judge, 'V24_AXES', tuple(lambda b, n=n: seen.append(n) for n in ('title', 'method')))
    monkeypatch.setattr(judge, 'v24_method', judge.V24_AXES[1])
    monkeypatch.setattr(switches, 'V24_NO_METHOD', True)
    assert judge.v24(None) is None and seen == ['title']
    assert (switches.AUDIT_FIXES, switches.REGION_PARTICLE) == (False, False)


def test_v19_private_switch(monkeypatch):
    assert switches.V19_PRIVATE is True
    n = notice_of('2. 입찰참가자격', '가. 제조사의 물품공급확약서를 입찰서와 함께 제출')
    ln = line(n, '가.')
    b = FakeBundle(n, {('pledge', ln.i): {'시점': '입찰 전', '발급 주체': '제3자(제조사·공급사)'}}, {'pledge': [ln]}, private=True)
    before = judge.v19(b)
    monkeypatch.setattr(switches, 'V19_PRIVATE', False)
    assert judge.v19(b) is None
    monkeypatch.setattr(switches, 'V19_PRIVATE', True)
    assert judge.v19(b) is before


def test_v9_noise_ignores_platforms_and_empty_labels(monkeypatch):
    monkeypatch.setattr(switches, 'V9_BROAD', True)
    n = notice_of('3. 규격', '가. 운영체제 : Windows 11 Pro', '나. 모델명 :', '다. 파종기(DPM-8000GMP) 1대')
    lns = [line(n, s) for s in ('가.', '나.', '다.')]
    b = FakeBundle(n, {('model', ln.i): {'성격': '구매 대상의 제조사·모델 지정'} for ln in lns}, {'model': lns})
    assert judge.v9(b) is lns[0]
    monkeypatch.setattr(switches, 'V9_NOISE', True)
    assert judge.v9(b) is lns[2]


def test_v24_wide_readers():
    n = notice_of('1. 입찰에 부치는 사항', '사업비', '150,000,000', '2. 입찰참가자격')
    assert judge.v24_amount_wide(FakeBundle(n, P=1e8, B=1.1e8)) is not None
    assert judge.v24_amount_wide(FakeBundle(n, P=1.36e8, B=1.5e8)) is None
    n2 = notice_of('가. 용역금액 : 금 37,930,000', '나. 기타')
    b2 = FakeBundle(n2, P=36118182, B=39730000)
    assert judge.v24_transposed(b2) is None and judge.v24_transposed_wide(b2) is not None
    n3 = notice_of('2. 입찰참가자격', '가. 관광진흥법에 의한 종합여행업(1262)으로 등록한 자')
    b3 = FakeBundle(n3, license_flag='Y', license='[종합여행업(1261)]')
    assert judge.v24_license(b3) is None and judge.v24_license_wide(b3) is not None


def test_v24_base_zone():
    assert switches.V24_BASE_ZONE is False
    reg = dict(P=207300000, B=228030000, P_source='meta')
    assert judge.v24_base_zone(FakeBundle(notice_of('가. 기초금액 : 250,833,000원', '나. 기타'), **reg)) is not None
    assert judge.v24_base_zone(FakeBundle(notice_of('가. 기초금액 : 228,030,000원'), **reg)) is None
    both = notice_of('가. 기초금액 : 250,833,000원', '나. 기초금액 : 228,030,000원')
    assert judge.v24_base_zone(FakeBundle(both, **reg)) is None          # one statement agrees (a lot beside the total)
    unit = notice_of('가. 기초금액 : 150,000,000원 (단가)')
    assert judge.v24_base_zone(FakeBundle(unit, **reg)) is None
    assert judge.v24_base_zone(FakeBundle(notice_of('가. 기초금액 : 250,833,000원'), P=207300000, B=None,
                                          P_source='notice')) is None   # no registered amount


def test_v9_read2(monkeypatch):
    assert switches.V9_READ2 is False
    n = notice_of('가. 모델명 : DPM-8000GMP', '나. 기존 장비 IBM P740 유지보수', '다. 대한항공(KE2117) 29석')
    lns = n.lines[:3]
    got = {(('model2', ln.i)): r for ln, r in zip(lns, (
        {'대상': '납품 물품', '방식': '지정', '동등': '명시 없음'},
        {'대상': '기존 장비', '방식': '지정', '동등': '명시 없음'},
        {'대상': '해당 없음', '방식': '지정', '동등': '명시 없음'}))}
    b = FakeBundle(n, got, {'model2': lns})
    assert judge.v9(b) is None                       # switch off: v9 reads the model family, which has no lines here
    monkeypatch.setattr(switches, 'V9_READ2', True)
    assert judge.v9(b) is lns[0]
    b2 = FakeBundle(n, {k: v for k, v in got.items() if k[1] != lns[0].i}, {'model2': lns[1:]})
    assert judge.v9(b2) is None                      # serviced equipment and a flight are no designation


def test_v9_read2_family_choice(monkeypatch):
    rec = {'id': 'T', 'meta': {'업무구분': '물품(내자)'}, 'docs': [{'type': '규격서', 'text': '1. 모델명 : DPM-8000GMP'}]}
    assert 'model' in facts.build(rec).cands and 'model2' not in facts.build(rec).cands
    monkeypatch.setattr(switches, 'V9_READ2', True)
    b = facts.build(rec)
    assert 'model2' in b.cands and 'model' not in b.cands


def test_v9_stage2_drops_existing_equipment(monkeypatch):
    monkeypatch.setattr(switches, 'V9_BROAD', True)
    n = notice_of('가. 유지보수 대상 장비 : IBM P740', '나. 모델명 : DPM-8000GMP')
    lns = n.lines[:2]
    got = {('model', ln.i): {'성격': '구매 대상의 제조사·모델 지정'} for ln in lns}
    b = FakeBundle(n, got, {'model': lns})
    assert judge.v9(b) is lns[0]                     # switch off: the first designation line
    monkeypatch.setattr(switches, 'V9_STAGE2', True)
    assert judge.v9(b) is lns[0]                     # no second-stage reading: the line is kept
    got[('v9obj', lns[0].i)] = {'대상': '기존 장비'}
    got[('v9obj', lns[1].i)] = {'대상': '납품 물품'}
    assert judge.v9(b) is lns[1]


def test_audit_probe_switches_default_off():
    for k in ('SCOPE_FIXES', 'V11_ELIGIBLE', 'V19_PLEDGE_FIXES', 'V20_SCOPE', 'V22_PRESENTATION', 'T_SAFETY_STATUTE', 'V5_ANY_SECTION',
              'V3_EXACT', 'V23_LEGAL_COUNT', 'V24_BASE_ZONE', 'V9_READ2', 'V9_STAGE2', 'V17_WIDEN_ANYWHERE', 'SIZE_TAG_NOT_QUAL'):
        assert getattr(switches, k) is False, k


def test_v22_presentation_context():
    n = notice_of('가. 제안서 평가 및 발표', '나. 발표 순서는 추첨', '다. 설명회에 참석한 업체만 제안서 제출 가능', '라. 현장설명회 참석 필수')
    assert judge.presentation_context(FakeBundle(n), line(n, '다.')) is True       # four lines above hold 발표
    assert judge.presentation_context(FakeBundle(n), line(n, '라.')) is False      # names the orderer's 현장설명


def test_scope_fixes_restores_switches(monkeypatch):
    monkeypatch.setattr(switches, 'SCOPE_FIXES', True)
    before = switches.AUDIT_FIXES, switches.AUDIT_FIXES2, switches.AUDIT_FIXES3
    service('가. 공공디자인 진흥계획 수립 용역')
    assert (switches.AUDIT_FIXES, switches.AUDIT_FIXES2, switches.AUDIT_FIXES3) == before

