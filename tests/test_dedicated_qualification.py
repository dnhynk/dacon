"""v1/v4/v8 dedicated stage pps_c/dedicated/qualification (switches.DEDICATED = ('v1', 'v4', 'v8')): candidate selection
(block terminator, cue lines, the 서식/붙임 zone), the decision rules for A, B and C on hand-written model answers (blueprint
positives and look-alike negatives), request building, consume, and the verdict without a reading. Runs against the copied
runtime in impl/submission."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import catalog, dedicated, facts, judge, main, record, switches  # noqa: E402
from pps_c.dedicated import qualification as Q  # noqa: E402
from pps_c.runner import MockEngine  # noqa: E402

HEAD = ['1. 입찰에 부치는 사항', '가. 용 역 명 : {title}', '나. 추정가격 : 별도', '2. 입찰방법', '가. 총액입찰, 전자입찰']
QUAL_HEAD = '3. 입찰참가자격'
BASE = '가. 「지방자치단체를 당사자로 하는 계약에 관한 법률 시행령」제13조 및 같은 법 시행규칙 제14조의 규정에 의한 자격을 갖춘 자'
TAIL = ['4. 낙찰자 결정방법', '가. 예정가격 이하 최저가격으로 입찰한 자 순으로 적격심사를 하여 낙찰자를 결정합니다.']

UNIV = '라. 본 용역은 「고등교육법」 제2조에 따른 대학 또는 「산업교육진흥 및 산학연협력촉진에 관한 법률」 제25조에 따른 산학협력단만 참여 가능함'
UNIV_ANY = ('라. 국가, 지방자치단체 또는 공기업·준정부기관이 발주한 당해 사업 관련 용역수행이 가능한 대학([기관(공공기관)] 포함)이나 연구기관 또는 '
            '컨설팅기관(회사)로서 나라장터 이용자 등록을 필하고 사업 수행에 필요한 조직의 인력자격 요건을 갖춘 업체')
CENTER = '4) 전국 모든 광역단체에 골재기계 수리 센터가 있는 업체'
GUARDS = ('다. 국가종합전자조달시스템(G2B) 입찰참가자격등록규정에 따라 입찰서 제출 마감일 전일까지 「경비업법」 제4조에 따른 시설경비업(업종코드 1164)을 '
          '등록하고 50명 이상의 보안인력을 보유한 업체')
FACILITY = '3) 경기도 [지역:r1|단위=기초|광역=경기도] 또는 제주도 지역에 소재한 교육(연수) 시설을 보유하고 있는 자'
LEASE = '다. 급식품 공급에 필요한 시설이나 점포를 임차 또는 보유하고 있으며, 급식품 운반에 필요한 냉동·냉장 차량을 소유 또는 임차하고 있는 업체'
LEGAL = ('나. 「건설폐기물 재활용촉진에 관한 법률」 제21조 규정에 의거 건설폐기물 중간처리업 허가를 득하고(건설폐기물 수집 ․ 운반을 위한 시설, 장비를 '
         '갖추어야 함) 주된 영업소의 소재지가 충청북도인 업체이어야 합니다.')
TRUCK = '① 계약상대자 소유 15톤 이상 암롤차량 보유한 업체'
PERF_GOV = '다. 최근 5년간 국가기관이 발주한 국제회의 개최 대행 용역의 이행실적이 4억원 이상인 업체'
PERF_PRIV = '바. 공고일 기준 최근 5년간 국가, 지방자치단체, 공공기관, 민간에서 시행한 축제, 기념행사, 문화예술행사 등 단일용역 건으로 1억원 이상(부가세 포함)의 실적이 있는 업체'
PERF_NAMED = '나. 최근 3년 이내 중고등학교 학생 대상 숙박이 들어간 국내 여행 1억원 이상의 실적이 있는 업체이어야 합니다.'
PERF_PLAIN = '나. 공고일 기준 5년 이내 공공디자인 용역 실적이 3천만원 이상인 업체'
PERF_BUYER = '④ 입찰공고일 기준 최근 3년 이내에 단일건으로 1억 원 이상 [수요기관(공공기관)]에서 발주한 유사사업 및 동등이상 관련 사업 등을 실적이 있는 업체(하도급 제외)'
PERF_SUB = '2) 공고일 전일 기준 최근 5년 이내 국가,정부투자기관에서 시행한 단일 건 3억 원(VAT포함) 이상의 축제, 공연 등 문화행사 수행실적이 있어야 합니다.'
REGION_LN = ('나. 입찰공고일 전일부터 입찰일(낙찰자는 계약체결일)까지 법인등기부상 본점 소재지(개인사업자인 경우에는 사업자등록증 또는 관련 법령에 따른 '
             '허가·인가·면허·등록·신고 등에 관련된 서류에 기재된 사업장의 소재지)를 경상남도에 둔 자이어야 합니다.')
REGION_PERF_ONE = '다. 공고일 기준 부가가치세법에 따른 이벤트행사대행업으로 당해 사업에 관한 사업자등록증을 갖춘 경북에 소재한 실적이 우수한 업체.'
EVAL_PERF = '용역실적 | ◦최근 5년간 유사 분야(행사, 포럼, 컨벤션 등) 용역 수행실적 (단일 건으로 2억 이상 실적) 10건 이상 | 5점'
FORM_NOTE = '※ 최근 3년간 중․고등학교 수련활동 실적에 한함.'


def rec(title, qual, law='지방계약법', method='제한경쟁', award='협상에의한계약', work='일반용역', P=3e8, docs=None, after=()):
    meta = {'업무구분': work, '적용계약법': law, '계약방법': method, '낙찰방법': award, '입찰추정가격': P, '배정예산금액': P * 1.1,
            '지역제한여부': 'N', '제한지역코드목록': None, '면허업종제한목록': None, '세부품명번호목록': None, '조항호내용': None}
    text = '\n'.join(l.format(title=title) for l in HEAD) + '\n' + '\n'.join([QUAL_HEAD, BASE] + list(qual) + TAIL + list(after))
    return {'id': 'T', 'meta': meta, 'docs': [{'type': '공고문', 'text': text}] + (docs or [])}


def bundle(*args, **kw):
    return facts.build(rec(*args, **kw), catalog.load())


def answer(**kw):
    a = {'perf_req_line': -1, 'perf_issuer_scope': '해당없음', 'perf_named_scope': '해당없음', 'region_req_line': -1,
         'institution_line': -1, 'institution_excludes_business': '해당없음', 'holding_line': -1, 'holding_basis': '해당없음'}
    a.update(kw)
    return a


def read(b, **kw):
    setattr(b, Q.FAM, answer(**kw))
    return b


def line_of(b, needle):
    """The first line containing `needle`, skipping the title line of the header."""
    return next(ln for ln in b.notice.lines if needle in ln.text and '용 역 명' not in ln.text)


class LenEngine:
    max_model_len = 16384

    def __init__(self, per_char=0.5):
        self.per_char, self.calls = per_char, 0

    def token_ids(self, messages, thinking=False):
        self.calls += 1
        return list(range(int(sum(len(m['content']) for m in messages) * self.per_char)))


# ---------------------------------------------------------------- wiring
def test_stage_is_registered():
    assert switches.DEDICATED in ((), ('v1', 'v4', 'v8'))       # the copy sets the three items for the mock run; canonical default is ()
    assert dedicated.stage('v1') is Q and dedicated.stage('v4') is Q and dedicated.stage('v8') is Q and Q.FAM == 'd_qualification'
    assert dedicated.MODULES['v1'] == 'qualification:A' and dedicated.MODULES['v4'] == 'qualification:B' and dedicated.MODULES['v8'] == 'qualification:C'


# ---------------------------------------------------------------- candidate selection
def test_section_block_keeps_sub_items_and_stops_at_the_next_heading():
    b = bundle('축제 행사대행 용역', ['1) 행사대행업으로 등록한 업체', PERF_SUB])
    inf = Q.infos(b.notice)
    where = {d['line'].text: d['where'] for d in inf}
    assert where[PERF_SUB] == 'section' and 'PERF' in next(d for d in inf if d['line'].text == PERF_SUB)['cues']
    assert TAIL[0] not in where and HEAD[0] not in where
    # the heading-based fallback block: sub-items "1)" / "2)" stay, "4." ends it
    notice = record.build(rec('x', ['1) 행사대행업으로 등록한 업체', PERF_SUB]))
    block = [ln.text for ln in Q.heading_block(notice.notice_lines())]
    assert block[0] == QUAL_HEAD and PERF_SUB in block and TAIL[0] not in block


def test_cue_lines_outside_the_section_and_the_form_zone():
    filler = [f'- 안내 문구 {k}' for k in range(24)]
    b = bundle('수련활동 위탁 용역', [REGION_LN], after=filler + ['[서식 1] 실적증명서', FORM_NOTE])
    d = next(d for d in Q.infos(b.notice) if d['line'].text == FORM_NOTE)
    assert d['where'] == 'cue' and d['zone'] == 'form' and 'PERF' in d['cues']
    assert Q.proxy(Q.infos(b.notice))['perf_req_line'] == -1                     # a form footnote is never the 실적 requirement
    assert Q.verdict(b, 'C') is None
    rfp = [{'type': '제안요청서', 'text': '평가항목\n' + EVAL_PERF + '\n입찰참가자격: ' + PERF_GOV}]
    inf = Q.infos(bundle('행사 용역', [REGION_LN], docs=rfp).notice)
    texts = {d['line'].text: d for d in inf}
    assert EVAL_PERF not in texts and ('입찰참가자격: ' + PERF_GOV) in texts and texts['입찰참가자격: ' + PERF_GOV]['where'] == 'other'


def test_gate_needs_a_perf_institution_or_holding_cue():
    assert not Q.needs_model(Q.infos(bundle('청소 용역', [REGION_LN]).notice))
    assert Q.needs_model(Q.infos(bundle('행사 용역', [PERF_PLAIN]).notice))
    assert Q.needs_model(Q.infos(bundle('도서 구매', [UNIV]).notice))
    assert Q.needs_model(Q.infos(bundle('골재 구매', [CENTER]).notice))


# ---------------------------------------------------------------- decision rule A on hand-written answers
def test_A_institution_restriction():
    univ = bundle('축제 대행 용역', [UNIV])
    assert Q.verdict(read(univ, institution_line=line_of(univ, '산학협력단').i, institution_excludes_business='예'), 'A') is line_of(univ, '산학협력단')
    assert Q.verdict(read(univ, institution_line=line_of(univ, '산학협력단').i, institution_excludes_business='아니오'), 'A') is None
    fresh = bundle('축제 대행 용역', [UNIV])                                          # no reading: the proxy reads "만 참여 가능함"
    assert Q.verdict(fresh, 'A') is line_of(fresh, '산학협력단')
    anyco = bundle('연구 용역', [UNIV_ANY])
    assert Q.verdict(anyco, 'A') is None                                            # DEV-10: 컨설팅기관(회사) admitted
    small = bundle('축제 대행 용역', [UNIV], method='수의계약', award='소액수의견적')
    assert Q.verdict(small, 'A') is line_of(small, '산학협력단')                     # 기관 종류 지정 has no 수의계약 exemption


def test_A_holding_conditions():
    center = bundle('골재 구매', [CENTER], work='물품(내자)')
    assert Q.verdict(center, 'A') is line_of(center, '수리 센터')                     # DEV-046
    assert Q.verdict(read(center, holding_line=line_of(center, '수리 센터').i, holding_basis='과업수행에_필수·통상_수준'), 'A') is None
    guards = bundle('응급실 보안인력 위탁 용역', [GUARDS], law='국가계약법')
    assert Q.verdict(guards, 'A') is line_of(guards, '보안인력')                      # DEV-063
    fac = bundle('연수 운영 용역', [FACILITY])
    assert Q.verdict(fac, 'A') is line_of(fac, '교육(연수)')                          # DEV-072
    assert Q.verdict(bundle('급식품 구매', [LEASE], work='물품(내자)'), 'A') is None      # 보유 또는 임차
    assert Q.verdict(bundle('폐기물 처리 용역', [LEGAL]), 'A') is None                  # 법령 장비기준
    assert Q.verdict(bundle('폐기물 운반 용역', [TRUCK], method='수의계약', award='소액수의견적'), 'A') is None   # DEV-083
    assert Q.verdict(bundle('폐기물 운반 용역', [CENTER], method='수의계약', award='소액수의견적'), 'A') is None  # 소액수의 exemption
    assert Q.verdict(read(bundle('급식품 구매', [LEASE]), holding_line=0, holding_basis='과업_필요를_넘는_수준'), 'A') is None  # a line outside the candidates is ignored


# ---------------------------------------------------------------- decision rule B
def test_B_issuer_and_named_scope():
    gov = bundle('국제회의 개최 대행 용역', [PERF_GOV], law='국가계약법', P=7.48e8)
    assert Q.verdict(gov, 'B') is line_of(gov, '국가기관이 발주한')                     # DEV-06 (proxy)
    assert Q.verdict(read(gov, perf_req_line=line_of(gov, '국가기관이').i, perf_issuer_scope='공공기관등만', perf_named_scope='계약목적물과_같은_종류_전반'), 'B') is line_of(gov, '국가기관이')
    assert Q.verdict(read(gov, perf_req_line=line_of(gov, '국가기관이').i, perf_issuer_scope='제한없음_또는_민간포함', perf_named_scope='계약목적물과_같은_종류_전반'), 'B') is None
    assert Q.verdict(read(gov, perf_req_line=line_of(gov, '국가기관이').i, perf_issuer_scope='불명', perf_named_scope='불명'), 'B') is line_of(gov, '국가기관이')  # 불명 → proxy on that line
    assert Q.verdict(bundle('축제 대행 용역', [PERF_PRIV]), 'B') is None               # DEV-054: … 민간
    named = bundle('수학여행 위탁 용역', [PERF_NAMED], P=6.1e7)
    assert Q.verdict(named, 'B') is line_of(named, '숙박이 들어간')                     # DEV-042
    assert Q.verdict(bundle('공공디자인 용역', [PERF_PLAIN], P=8.9e7), 'B') is None       # same-kind 실적, below 고시금액: not B
    buyer = bundle('행사 용역', [PERF_BUYER], law='국가계약법', P=2.27e7)
    assert Q.verdict(buyer, 'B') is line_of(buyer, '[수요기관(공공기관)]에서 발주')       # DEV-053
    small = bundle('통근버스 운행 용역', ['◦ 공고일 기준 최근 3년 이내 공공기관(국가,지자체,공기업 포함) 통근버스 운행 실적이 2억원 이상 있는 업체만 참가 자격이 있습니다.'],
                   method='수의계약', award='소액수의견적', P=8.3e7)
    assert Q.verdict(small, 'B') is line_of(small, '통근버스')                           # DEV-059: no 소액수의 exemption for B


# ---------------------------------------------------------------- decision rule C
def test_C_needs_both_and_respects_the_local_private_exemption():
    both = bundle('공공디자인 용역', [REGION_LN, PERF_PLAIN])
    assert Q.verdict(both, 'C') is line_of(both, '공공디자인 용역 실적')
    assert Q.verdict(read(both, perf_req_line=line_of(both, '공공디자인 용역 실적').i, region_req_line=line_of(both, '본점 소재지').i,
                          perf_issuer_scope='제한없음_또는_민간포함', perf_named_scope='계약목적물과_같은_종류_전반'), 'C') is line_of(both, '공공디자인 용역 실적')
    assert Q.verdict(read(both, perf_req_line=line_of(both, '공공디자인 용역 실적').i, region_req_line=-1,
                          perf_issuer_scope='제한없음_또는_민간포함', perf_named_scope='계약목적물과_같은_종류_전반'), 'C') is line_of(both, '공공디자인 용역 실적')  # region from the proxy
    assert Q.verdict(bundle('공공디자인 용역', [REGION_LN, PERF_PLAIN], method='수의계약', award='소액수의견적'), 'C') is None      # 지방 + 소액수의
    nat = bundle('공공디자인 용역', [REGION_LN, PERF_PLAIN], law='국가계약법', method='수의계약', award='소액수의견적')
    assert Q.verdict(nat, 'C') is line_of(nat, '공공디자인 용역 실적')                                                   # 국가 수의계약: not exempt
    gen = bundle('행사 용역', [REGION_PERF_ONE], method='일반경쟁')
    assert Q.verdict(gen, 'C') is line_of(gen, '경북에 소재한')                                                          # DEV-048: one line, 일반경쟁
    assert Q.verdict(bundle('공공디자인 용역', [REGION_LN]), 'C') is None
    assert Q.verdict(bundle('공공디자인 용역', [PERF_PLAIN]), 'C') is None
    rfp = [{'type': '제안요청서', 'text': '평가항목\n' + EVAL_PERF}]
    assert Q.verdict(bundle('행사 용역', [REGION_LN], docs=rfp), 'C') is None                                            # evaluation 실적 is not a requirement


# ---------------------------------------------------------------- request building
def test_request_is_built_only_when_the_model_is_needed():
    eng = LenEngine()
    gov = bundle('국제회의 개최 대행 용역', [PERF_GOV], law='국가계약법')
    r = Q.request(eng, gov, 3, main.Request)
    assert r is not None and r.rec == 3 and r.fam == Q.FAM and r.max_tokens == Q.MAX_TOKENS and r.budget == 0 and r.extra == ()
    assert line_of(gov, '국가기관이') in r.cands and all(ln in gov.notice.lines for ln in r.cands)
    assert r.schema['properties']['perf_issuer_scope']['enum'][-1] == '불명' and r.schema['properties']['perf_req_line']['minimum'] == -1
    assert len(r.token_ids) + r.max_tokens <= Q.PROMPT_TOKEN_LIMIT
    assert Q.request(eng, bundle('청소 용역', [REGION_LN]), 0, main.Request) is None                                      # region alone: gate closed
    assert Q.request(eng, bundle('청소 용역', ['나. 청소 인력을 성실히 배치할 것']), 0, main.Request) is None


def test_request_shrinks_to_the_token_limit():
    long_lines = [PERF_GOV] + [f'{k}) 실적이 있는 업체 ' + '가' * 280 for k in range(40)]
    gov = bundle('국제회의 개최 대행 용역', long_lines, law='국가계약법')
    eng = LenEngine(per_char=2.0)
    r = Q.request(eng, gov, 0, main.Request)
    assert r is not None and len(r.token_ids) + r.max_tokens <= Q.PROMPT_TOKEN_LIMIT and eng.calls > 1
    assert line_of(gov, '국가기관이') in r.cands and len(r.cands) < 41


# ---------------------------------------------------------------- consume
def test_consume_valid_prefixed_invalid_and_mock():
    eng = LenEngine()
    gov = bundle('국제회의 개최 대행 용역', [PERF_GOV], law='국가계약법')
    r = Q.request(eng, gov, 0, main.Request)
    i = line_of(gov, '국가기관이').i
    good = json.dumps(answer(perf_req_line=i, perf_issuer_scope='공공기관등만', perf_named_scope='계약목적물과_같은_종류_전반'), ensure_ascii=False)
    assert Q.consume(gov, r.cands, good) is True and getattr(gov, Q.FAM)['perf_req_line'] == i
    assert Q.consume(gov, r.cands, '<|channel>생각<channel|>' + good) is True
    assert Q.consume(gov, r.cands, 'not json') is False
    assert Q.consume(gov, r.cands, json.dumps(answer(perf_issuer_scope='없는값'), ensure_ascii=False)) is False
    assert Q.consume(gov, r.cands, json.dumps({'perf_req_line': 3})) is False
    assert Q.consume(gov, r.cands, json.dumps(answer(perf_req_line='3'))) is False
    assert Q.consume(gov, r.cands, json.dumps(answer(perf_req_line=99999))) is True and getattr(gov, Q.FAM)['perf_req_line'] == -1   # unknown id → -1
    mock = MockEngine().generate([(r.token_ids, r.schema, r.max_tokens)])[0][0]
    assert Q.consume(gov, r.cands, mock) is True
    reading = getattr(gov, Q.FAM)
    assert reading['perf_req_line'] == -1 and reading['perf_issuer_scope'] == '불명' and reading['holding_basis'] == '불명'
    assert Q.verdict(gov, 'B') is line_of(gov, '국가기관이') and Q.explain(gov)['source'] == 'cpu'   # all-불명 → proxy decides


def test_main_consume_routes_to_the_stage(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED', ('v1', 'v4', 'v8'))
    gov = bundle('국제회의 개최 대행 용역', [PERF_GOV], law='국가계약법')
    r = Q.request(LenEngine(), gov, 0, main.Request)
    assert main.consume(gov, r, json.dumps(answer(), ensure_ascii=False)) is True and getattr(gov, Q.FAM)['perf_req_line'] == -1


# ---------------------------------------------------------------- verdict without a reading, judge integration
def test_verdict_without_reading_uses_the_proxy():
    gov = bundle('국제회의 개최 대행 용역', [PERF_GOV], law='국가계약법')
    assert getattr(gov, Q.FAM, None) is None and Q.verdict(gov, 'B') is line_of(gov, '국가기관이') and Q.verdict(gov, 'A') is None and Q.verdict(gov, 'C') is None
    both = bundle('공공디자인 용역', [REGION_LN, PERF_PLAIN])
    assert Q.verdict(both, 'C') is line_of(both, '공공디자인 용역 실적') and Q.verdict(both, 'B') is None
    assert Q.explain(bundle('청소 용역', ['나. 청소 인력을 성실히 배치할 것']))['gate'] is False


def test_judge_takes_the_stage_verdicts(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED', ('v1', 'v4', 'v8'))
    b = bundle('공공디자인 용역', [UNIV, REGION_LN, PERF_GOV])
    out = judge.judge(b)
    assert out['v1'] == (1, UNIV) and out['v4'] == (1, PERF_GOV) and out['v8'] == (1, PERF_GOV)
    small = bundle('공공디자인 용역', [REGION_LN, PERF_PLAIN], method='수의계약', award='소액수의견적')
    assert judge.judge(small)['v8'] == (0, '') and judge.judge(small)['v4'] == (0, '')
