"""Round-3 audit fixes behind switches.AUDIT_FIXES3 (runs/rebuild_c/transfer_20260925/audit/R3-A..C.md). Each test shows the
round-2 behaviour (AUDIT_FIXES and AUDIT_FIXES2 on, AUDIT_FIXES3 off) and the fixed behaviour with AUDIT_FIXES3 on; the fixes
that change model prompts also need switches.FIX3_PROMPTS."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import catalog, facts, judge, record, switches  # noqa: E402
from pps_c.families import perf_default, perf_select, region_select, size_normal, size_words  # noqa: E402


@pytest.fixture
def fix3(monkeypatch):
    monkeypatch.setattr(switches, 'AUDIT_FIXES', True)
    monkeypatch.setattr(switches, 'AUDIT_FIXES2', True)

    def turn(on, prompts=False):
        monkeypatch.setattr(switches, 'AUDIT_FIXES3', on)
        monkeypatch.setattr(switches, 'FIX3_PROMPTS', prompts)
    turn(False)
    return turn


def notice_of(*texts, doc_type='공고문'):
    return record.build({'id': 'T', 'meta': {}, 'docs': [{'type': doc_type, 'text': '\n'.join(texts)}]})


def notice_docs(*docs):
    return record.build({'id': 'T', 'meta': {}, 'docs': [{'type': t, 'text': '\n'.join(x)} for t, x in docs]})


def line(n, start, doc_type=None):
    return next(x for x in n.lines if x.text.startswith(start) and (doc_type is None or x.doc_type == doc_type))


class FakeBundle:
    def __init__(self, notice, readings=None, cands=None, **meta):
        self.notice, self._readings, self.cands, self.titles = notice, readings or {}, cands or {}, []
        base = {'P': 5e7, 'B': 5.5e7, 'law': '국가', 'region_sido': set(), 'region_basic': 0, 'T_lo': 2.3e8, 'T_hi': 2.3e8,
                'local_private': False, 'method': '제한경쟁', 'posted': None, 'region_flag': 'N'}
        base.update(meta)
        self.meta = type('M', (), base)()

    def read(self, fam, ln):
        return self._readings.get((fam, ln.i), {})


def read_as(fam, lines, **reading):
    return {(fam, ln.i): dict(reading) for ln in lines}


def service(title_line, qual='가. 경쟁입찰 참가자격을 갖춘 자', license_text=None, extra=()):
    m = {'적용계약법': '국가계약법', '업무구분': '일반용역', '계약방법': '제한경쟁', '낙찰방법': '협상에의한계약',
         '입찰추정가격': '90000000', '배정예산금액': '99000000', '조항호내용': 'None'}
    if license_text:
        m['면허업종제한목록'] = license_text
    return facts.build({'id': 'T', 'meta': m, 'docs': [{'type': '공고문', 'text': '\n'.join(
        ['입찰공고', '1. 입찰에 부치는 사항', title_line, *extra, '2. 입찰참가자격', qual])}]}, catalog.load())


def test_switch_defaults_to_current():
    assert switches.AUDIT_FIXES3 is False and switches.FIX3_PROMPTS is False


def perf_bundle(monkeypatch, n, perf):
    monkeypatch.setattr(judge, 'lines_where', lambda b, fam, section=False, **want: perf if section else [])
    return FakeBundle(n)


def test_staff_career_exception_reason_and_document_notes(fix3, monkeypatch):
    n = notice_of('2. 입찰참가자격',
                  '가. 최근 3년 이내 동종 용역 수행 실적이 있는 업체',
                  '* 경력 : 국가ㆍ지자체ㆍ공공기관 근무 및 용역사업 참여실적 기준으로 인정하고 경력을 증명할 수 있는 자료로 증빙',
                  '다. 동일 또는 유사한 용역 이행 경험 등이 필요한 점을 감안하여 「중소기업제품 구매촉진 및 판로지원에 관한 법률 시행령」'
                  '제2조의3(중소기업자와의 우선 조달계약에 대한 예외)을 적용합니다.',
                  '마. 사업실적증명서 1부',
                  '* 입찰공고일 기준 최근 3년 이내 공공기관 대상 계약실적으로 계약 상대기관 대표자 직인 날인',
                  '바. 참여인력의 경력증명서 및 수행실적이 있는 업체의 실적증명서')
    perf = [x for x in n.lines if x.text != '2. 입찰참가자격' and not x.text.startswith('마.')]
    b = perf_bundle(monkeypatch, n, perf)
    assert judge.perf_lines(b) == perf
    fix3(True)
    assert judge.perf_lines(b) == [perf[0], perf[-1]]      # a firm subject keeps a line that also names 경력증명서


def test_form_annex_lines(fix3, monkeypatch):
    n = notice_of('2. 입찰참가자격', '가. 최근 3년 이내 동종 용역 수행 실적이 있는 업체',
                  '6. 제출서식', '[별지 제10호 서식]', '주요(유사분야)사업실적', '용역 참가 자격용',
                  '- 입찰공고시에 제시한 용역범위 및 기준 조건에 부합되는 실적에 한하여 제출')
    perf = [line(n, '가.'), line(n, '주요'), line(n, '- 입찰공고시')]
    b = perf_bundle(monkeypatch, n, perf)
    assert judge.perf_lines(b) == perf
    fix3(True)
    assert judge.perf_lines(b) == [perf[0]]


def test_evaluation_heading_and_definition(fix3, monkeypatch):
    n = notice_of('2. 입찰참가자격', '가. 최근 3년 이내 통학차량 운행 실적이 있는 업체',
                  '6. 낙찰자 결정', '바. 적격심사 시 적용할 이행실적 평가기준은 다음과 같습니다. 시기 바랍니다.',
                  '가) 이행실적 : 입찰공고일 현재 이행완료 된 시점이 최근 5년 이내인 통학차량 운행 실적',
                  '7. 유의사항', '가. 입찰서는 전자입찰로 제출', '나. 보증금은 면제', '다. 공동계약 불가', '라. 청렴계약 이행',
                  '마. 기타 사항은 관계 법령에 따름', '바. 문의처는 계약부서',
                  '※ 실적은 입찰공고일 기준 최근 3년 이내 완료한 초·중등교육법에 따른 학교의 늘봄학교 운영 위탁용역 이행실적으로 하되 2025학년도는 포함',
                  '※ 경기도 내 소재 기관이 발주한 급식 실적만 인정')
    perf = [line(n, '가. 최근'), line(n, '가) 이행실적'), line(n, '※ 실적은'), line(n, '※ 경기도')]
    b = perf_bundle(monkeypatch, n, perf)
    assert judge.perf_lines(b) == perf
    fix3(True)
    assert judge.perf_lines(b) == [perf[0], perf[-1]]      # a recognition note may carry a record limit (kept)
    assert not judge.EVAL_HEAD3.search('나. 적격심사는 조달청 일반용역 적격심사 심사기준에 따라 실시')


def test_v2_placeholder_amounts(fix3, monkeypatch):
    n = notice_of('2. 입찰참가자격', '가. 최근 3년 이내 급식 운영 실적이 있는 업체')
    monkeypatch.setattr(judge, 'perf_lines', lambda b: [n.lines[-1]])
    placeholder, budget, above = FakeBundle(n, P=1.0, B=1.0), FakeBundle(n, P=1.0, B=5.5e7), FakeBundle(n, P=1.0, B=3.3e8)
    assert judge.v2(placeholder) and judge.v2(budget) and judge.v2(above)
    fix3(True)
    assert judge.v2(above) is None and judge.v2(budget) is n.lines[-1]
    assert judge.v2(placeholder) is n.lines[-1]      # no real budget: the registered value stands (organizer-planted cells)


def test_financial_institution_buyer(fix3):
    text = '금융기관에서 발주한 리스크관리 컨설팅 실적이 있는 업체'
    assert judge.buyer_limit(text) is None
    fix3(True)
    assert judge.buyer_limit(text) == 'specific'
    assert judge.buyer_limit('금융기관 또는 민간기업에서 발주한 컨설팅 실적') == 'open'


def test_firm_experience_candidates_change_prompts(fix3):
    n = notice_of('2. 입찰참가자격', '○ 금융기관에 대손충당금 등 리스크관리 업무 관련 컨설팅 유경험 업체',
                  '○ 책임연구원은 관련 분야 유경험자로 한다')
    firm = line(n, '○ 금융기관')
    assert perf_select(n) == []
    fix3(True)
    assert perf_select(n) == [] and perf_default(n, firm)['역할'] != '참가자격'      # AUDIT_FIXES3 alone keeps the prompts
    fix3(True, prompts=True)
    assert perf_select(n) == [firm]
    assert perf_default(n, firm)['역할'] == '참가자격'


def test_region_bid_section_clause_and_shared_performance_partner(fix3):
    bid = notice_of('1. 입찰방법', '가. 제한경쟁 입찰', '나. 입찰공고일 전일부터 주된 영업소 소재지를 계속 경상남도 또는 대구광역시에 둔 업체',
                    '2. 입찰참가자격', '가. 경쟁입찰 참가자격을 갖춘 자')
    stated = line(bid, '나. 입찰공고일')
    assert stated.sec == 'BID'
    b = FakeBundle(bid, read_as('region', [stated], 역할='참가자격', 대상='입찰자 소재지 제한'), {'region': [stated]})
    assert judge.region_restriction(b)[0] == []
    fix3(True)
    assert judge.region_restriction(b)[:2] == ([stated], {'경상남도', '대구광역시'})
    jv = notice_of('2. 입찰참가자격', '다. 주된 영업소가 서울특별시에 소재한 업체',
                   '라. 다만, 주된 영업소가 서울특별시에 소재하고 수집·운반업 허가만 소지한 업체는 서울특별시, 경기도, 인천광역시에 소재한 '
                   '중간처리업 허가를 받은 자와 분담이행을 허용')
    own, partner = line(jv, '다.'), line(jv, '라.')
    b = FakeBundle(jv, read_as('region', [own, partner], 역할='참가자격', 대상='입찰자 소재지 제한'), {'region': [own, partner]},
                   region_flag='Y', region_sido={'서울특별시'})
    fix3(False)
    assert judge.region_restriction(b)[1] == {'서울특별시', '경기도', '인천광역시'} and judge.v24_region(b) is own
    fix3(True)
    assert judge.region_restriction(b)[:2] == ([own, partner], {'서울특별시'}) and judge.v24_region(b) is None
    only = FakeBundle(jv, read_as('region', [partner], 역할='참가자격', 대상='입찰자 소재지 제한'), {'region': [partner]},
                      region_flag='Y', region_sido={'서울특별시', '경기도', '인천광역시'})
    assert judge.region_restriction(only)[1] == {'서울특별시', '경기도', '인천광역시'}     # the registered restriction stands


def test_region_candidates_for_uncovered_province_wording_change_prompts(fix3):
    n = notice_of('2. 입찰참가자격', '□ 입찰공고일 현재 본사업장 소재지가 수도권 또는 충청북도인 업체',
                  '□ 법인등기부상 본점 소재지를 계속하여 경상북도에 두고 있는 자')
    assert region_select(n) == []
    fix3(True)
    assert region_select(n) == []
    fix3(True, prompts=True)
    assert region_select(n) == [line(n, '□ 입찰공고일'), line(n, '□ 법인등기부')]


def test_v24_region_prefers_the_notice_and_round_up_amounts(fix3):
    n = notice_docs(('공고문', ['2. 입찰참가자격', '다. 소재지가 강원특별자치도에 있어야 함']),
                    ('과업지시서', ['1. 참가자격', '• 입찰 참가 등록된 업체로 소재지가 울산광역시에 있어야 함']))
    own, att = line(n, '다.'), line(n, '•')
    b = FakeBundle(n, read_as('region', [own, att], 역할='참가자격', 대상='입찰자 소재지 제한'), {'region': [own, att]},
                   region_flag='Y', region_sido={'강원특별자치도'})
    assert judge.v24_region(b) is own
    assert not judge.agrees(109858880, 109858873)
    fix3(True)
    assert judge.v24_region(b) is None
    assert judge.agrees(109858880, 109858873) and not judge.agrees(109858890, 109858873)


def pledge_bundle(*texts, timing='입찰 전 발급·보유 또는 입찰서와 함께 제출', doc_type='공고문'):
    n = notice_of(*texts, doc_type=doc_type)
    ln = n.lines[-1]
    r = {'발급 주체': '제3자(제조사·공급사·기술지원사)', '시점': timing}
    return FakeBundle(n, {('pledge', ln.i): r}, {'pledge': [ln]}), ln


def test_v19_one_sentence_and_own_pledges(fix3):
    merged, _ = pledge_bundle('3. 입찰참가 자격 ◦ 전자입찰서 제출 마감일 전일까지 제작사 공식 고객서비스센터이거나 정비조직인증서를 받은 업체이어야 '
                              '합니다. ◦ 입찰자는 입찰서 제출 시 서약서를 제출하여야 합니다. 다만 전자입찰의 경우 전자입찰서 제출로 확약서 제출을 갈음합니다.')
    own, _ = pledge_bundle('- A/S기간 (1년 이상) 명시된 공급업체 자체 기술지원확약서 첨부', doc_type='규격서')
    maker, ln = pledge_bundle('2. 입찰참가자격', '나. 제조사의 물품공급 및 기술지원 확약서를 입찰서와 함께 제출')
    listed, ln2 = pledge_bundle('3. 제출서류', '1-4) 제조회사 공급 증명원 및 A/S 확약서 각 1부')
    assert judge.v19(merged) and judge.v19(own) and judge.v19(maker) is ln and judge.v19(listed) is ln2
    fix3(True)
    assert judge.v19(merged) is None and judge.v19(own) is None
    assert judge.v19(maker) is ln and judge.v19(listed) is ln2      # a list entry needs no demand verb (dev DEV-033)


def size_bundle(n, lines, **meta):
    return FakeBundle(n, read_as('size', lines, 역할='참가자격 제한'), {'size': lines}, **meta)


def test_size_class_comes_from_the_notice_eligibility_clause(fix3):
    notice_sme = ('공고문', ['2. 입찰참가자격', '다. 「중소기업기본법」 제2조에 따른 중소기업자 또는 「소상공인 보호 및 지원에 관한 법률」 '
                            '제2조에 따른 소상공인으로서 중소기업·소상공인 확인서를 소지한 업체'])
    rfp_small = ('제안요청서', ['1. 참가자격', '○ 「중소기업기본법」 제2조에 따른 소기업 또는 「소상공인 보호 및 지원에 관한 법률」 제2조에 '
                              '따른 소상공인으로서 소기업·소상공인 확인서를 소지한 업체'])
    n = notice_docs(notice_sme, rfp_small)
    b = size_bundle(n, [line(n, '다.'), line(n, '○')])
    assert judge.size_state(b, positive=True)[0] == 'small'
    fix3(True)
    assert judge.size_state(b, positive=True)[0] == 'sme'
    generic = notice_docs(('공고문', ['2. 입찰참가자격', '• 입찰 참가자는 공고사항을 숙지하고 중소기업관련자료를 참고하여야 함']), rfp_small)
    b = size_bundle(generic, [line(generic, '•'), line(generic, '○')])
    assert judge.size_state(b, positive=True)[0] == 'small'
    # a small-business line of the 공고문 itself still decides with the 공고문's SME clause (replica: planted lines)
    both = notice_docs(('공고문', notice_sme[1] + ['※ 본 입찰은 소기업 또는 소상공인만 참여 가능']))
    b = size_bundle(both, [line(both, '다.'), line(both, '※')])
    assert judge.size_state(b, positive=True)[0] == 'small'


def test_size_notice_line_read_outside_the_qualification_section(fix3):
    n = notice_docs(('공고문', ['1. 입찰에 부치는 사항', '2. 일반사항', '□ 「중소기업기본법」 제2조제2항에 따른 소기업, 「소상공인 보호 및 지원에 관한 법률」 '
                               '제2조에 따른 소상공인 업체로서 소기업 또는 소상공인 확인서 소지 업체', '3. 입찰참가자격', '가. 경쟁입찰 참가자격을 갖춘 자']),
                    ('제안요청서', ['1. 참가자격', '□ 「중소기업기본법」 제2조에 따른 중소기업자로서 ‘중소기업 확인서’를 소지한 자']))
    other, rfp = line(n, '□ 「중소기업기본법」 제2조제2항'), line(n, '□ 「중소기업기본법」 제2조에 따른 중소기업자')
    assert other.sec == 'OTHER'
    b = size_bundle(n, [other, rfp])
    assert judge.size_state(b, positive=True)[0] == 'sme'
    fix3(True)
    state, lines, _ = judge.size_state(b, positive=True)
    assert state == 'small' and other in lines


def test_size_note_admitting_bidders_without_the_certificate(fix3):
    n = notice_docs(('공고문', ['2. 입찰참가자격', '가. 경쟁입찰 참가자격을 갖춘 자']),
                    ('제안요청서', ['1. 참가자격', '가. 「중소기업기본법」 제2조에 따른 중소기업자로서 중소기업 확인서를 소지한 자',
                                  '※ 비영리법인은 소기업·소상공인 확인서가 없어도 입찰 참가 가능']))
    b = size_bundle(n, [line(n, '가. 「'), line(n, '※')])
    assert judge.size_state(b, positive=True)[0] == 'small'
    fix3(True)
    assert judge.size_state(b, positive=True)[0] == 'sme'


def test_size_note_parenthesis_and_mid_comma(fix3):
    text = ('나. 소기업 또는 소상공인 확인서를 소지한 업체 또는 벤처기업 또는 창업자이어야 합니다. (입찰참가자격의 판단기준일은 중, 소기업 또는 '
            '소상공인 확인서의 경우 적격심사서류 제출마감일까지 발급·신고·수정분으로 보며, 벤처기업 · 창업자의 경우 입찰참가등록마감일 입니다.)')
    assert size_words(size_normal(text)) == 'sme'
    fix3(True)
    assert size_words(size_normal(text)) == 'small'
    assert size_words(size_normal('가. 중, 소기업 확인서를 소지한 자')) == 'sme'


def test_sw_deliverable_shared_by_catalog_and_facts(fix3):
    not_deliverables = ('용역명 : 관광지식정보시스템 콘텐츠 제작 및 배포 용역', '용역명 : 공공하수처리시설 수질원격감시시스템 유지 관리 용역',
                        '용역명 : 「2026년 해외 우수 SW 경력자 채용연계」위탁 용역', '용역명 : 국내주식 의안분석 데이터베이스 제공기관 선정')
    kept = ('용역명 : 통합정보시스템 유지관리 용역', '용역명 : 학사관리시스템 구축 및 콘텐츠 제작 용역')
    assert all(facts.sw_named(service(t)) for t in not_deliverables + kept)
    fix3(True)
    assert not any(facts.sw_named(service(t)) for t in not_deliverables)
    assert all(facts.sw_named(service(t)) for t in kept)
    counselling = '기타기관 교육시설통합정보망 서비스 상담센터 위탁 운영 용역'
    fix3(False)
    assert catalog.sw_service_title(counselling)
    fix3(True)
    assert not catalog.sw_service_title(counselling) and catalog.sw_service_title('통합정보망 유지관리 용역')


def test_overview_channel_sentence_names_no_deliverable(fix3):
    channel = ('○ | 입찰 및 계약과 관련하여 직원이 해당 계약내용과 무관한 금품을 요구하는 경우 홈페이지([URL]) 신문고를 통하여 신고할 수 있습니다.',)
    b = service('용역명 : 공용여객처리시스템 책임보험', extra=channel)
    assert facts.sw_named(b)
    fix3(True)
    assert not facts.sw_named(b)
    assert facts.sw_named(service('용역명 : 교육 운영', extra=('가. 사업내용 : 학습관리 홈페이지 구축',)))


def test_table_row_title_and_teaching_head(fix3):
    rows = ('용 역 명 | 세부내역 | 입찰 등록 마감 일시 및 방법',
            '공공기관 SAS-EG 실습 교육 위탁운영 용역(제한경쟁·3억원미만) | [붙임] 과업지시서 참조 | ○ 일시: 2026. 4. 7.(화) 10:00')
    n = notice_of('1. 입찰에 부치는 사항', *rows)
    assert catalog.project_titles(n) == ['세부내역 | 입찰 등록 마감 일시 및 방법']
    b = service(rows[0], extra=rows[1:], license_text='[소프트웨어사업자(컴퓨터관련서비스사업)(1468)]')
    assert facts.sw_license_framing(b)
    fix3(True)
    assert catalog.project_titles(n) == ['세부내역 | 입찰 등록 마감 일시 및 방법']     # the title is shown in every prompt
    fix3(True, prompts=True)
    assert catalog.project_titles(n) == ['공공기관 SAS-EG 실습 교육 위탁운영 용역(제한경쟁·3억원미만)']
    b = service(rows[0], extra=rows[1:], license_text='[소프트웨어사업자(컴퓨터관련서비스사업)(1468)]')
    assert not facts.sw_license_framing(b)


def test_content_licence_combinations(fix3):
    lic = '[소프트웨어사업자(디지털콘텐츠개발서비스사업)(1469)]업종 또는[이러닝콘텐츠업(6527)]'
    assert not facts.content_only(lic)
    fix3(True)
    assert facts.content_only(lic)
    assert not facts.content_only('[소프트웨어사업자(디지털콘텐츠개발서비스사업)(1469)]')


def test_sw_statement_decree_article_41(fix3):
    n = notice_of('2. 입찰참가자격', '가. 「소프트웨어 진흥법 시행령」 제41조(중소 소프트웨어사업자의 기준 등) 제1항 제1호 또는 제2호에 해당하는 자')
    b = FakeBundle(n)
    assert not judge.sw_statement(b)
    fix3(True)
    assert judge.sw_statement(b)
    report = notice_of('2. 입찰참가자격', 'ㅇ 소프트웨어진흥법 시행령 제41조에 의해 소프트웨어사업자(컴퓨터 관련 서비스업 [업종코드: 1468])로 신고된 자')
    assert not judge.sw_statement(FakeBundle(report))


def test_minutes_and_publication_titles(fix3):
    minutes = service('용역명 : 6‧25전쟁『정전회담 회의록』 제6~10권 번역·해제·발간', license_text='[학술.연구용역(1169)]')
    assert minutes.scope.basis == 'service:conference'
    fix3(True)
    assert service('용역명 : 6‧25전쟁『정전회담 회의록』 제6~10권 번역·해제·발간', license_text='[학술.연구용역(1169)]').scope.basis == 'service:none'
    assert service('용역명 : 정책 토론회 및 전문가 회의 운영').scope.basis == 'service:conference'
