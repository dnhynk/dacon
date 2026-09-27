"""v10/v11/v13 dedicated stage pps_c/dedicated/competition_product (switches.DEDICATED = ('v10', 'v11', 'v13')): the CPU gate,
the decision rule on hand-written model answers (blueprint dev positives and look-alike negatives), request building, consume,
and the verdict without a reading. Runs against the copied runtime in impl/submission."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import catalog, dedicated, facts, judge, main, switches  # noqa: E402
from pps_c.dedicated import competition_product as C  # noqa: E402
from pps_c.runner import MockEngine  # noqa: E402

QUAL = ['1. 입찰에 부치는 사항', '가. 용 역 명 : {title}', '나. 추정가격 : 별도', '2. 입찰참가자격', '가. 나라장터 입찰참가자격을 등록한 자']
DP_BUS = '다.「중소기업제품 구매촉진 및 판로지원에 관한 법률」제9조 및 같은 법 시행령 10조에 의한 직접생산확인증명서 [세부품명：통학운송서비스/세부품명번호：7811189902]를 소지한 업체이어야 합니다.'
DP_EVENT = '④ ｢중소기업제품 구매촉진 및 판로지원에 관한 법률｣ 제6조, 제8조, 제9조 및 동법 시행령 제9조에 의한 유자격자로서 기타행사기획 및 대행서비스(세부 제품 번호: 8014199001) 품목 등에 대한 직접생산증명서를 소지한 업체'
DP_DOC = '아. 산업디자인전문회사 신고확인증, 중소기업확인서, 직접생산확인증명확인서 각 1부'
DP_PENALTY = '마. 계약담당공무원은 계약상대자가 직접생산 확인기준을 위반한 사실을 확인한 경우 계약해지, 계약보증금 국고 귀속, 입찰참가자격 제한 등의 조치를 할 수 있습니다.'
SME = '나.「중소기업기본법」 제2조에 따른 중소기업자로서 「중소기업 범위 및 확인에 관한 규정」에 따라 발급된 중소기업확인서를 소지한 업체'
SME_OR_SOHO = '다. 「중소기업기본법」제2조에 따른 중소기업자 또는 「소상공인 보호 및 지원에 관한 법률」 제2조에 따른 소상공인으로서 「중소기업 범위 및 확인에 관한 규정」에 따라 발급된 중소기업․소상공인확인서를 소지한 자'
SME_MID_CERT = '- 「중소기업기본법」제2조에 따른 중‧소기업 또는「소상공인 보호 및 지원에 관한 법률」제2조에 따른 소상공인으로서 발급된 중기업 또는 소기업․소상공인확인서를 소지한 업체'
SMALL = '• 『중소기업제품 구매촉진 및 판로지원에 관한 법률』 제2조에 따른 소기업자로서 중소기업 범위 및 확인에 관한 규정에 따라 발급된 소기업, 소상공인확인서를 소지한 업체이어야 합니다'
SMALL_CERT = '나. 「중소기업제품 구매촉진 및 판로지원에 관한 법률」에 의한 중소기업으로서, 소기업 ․ 소상공인확인서를 소지한 업체이면서, 기타행사기획 및 대행서비스 부문의 직접생산확인증명서를 소지한 업체.'
SMALL_LIST = ['- | 「중소기업기본법」 제2조제2항에 따른 소기업자, 「소상공인기본법」 제2조에 따른 소상공인, 「벤처기업육성에 관한 특별조치법」 제2조제1항에 따른 벤처기업 또는 「중소기업창업 지원법」 제2조제2호에 따른 창업자']
LARGE_ONLY = ['1) 소프트웨어진흥법 제58조에 따른 소프트웨어사업자로 신고한 업체', '※ 소프트웨어진흥법 제48조에 따른 소프트웨어사업자의 사업금액별 참여 제한',
              '※ 중소기업 확인은 중소기업 공공구매정보망(www.smpp.go.kr)을 활용하여 확인하므로, 등록마감일시까지 공공구매정보망에서 확인 가능하여야 함']
LABEL = '입 찰 방 법: 제한경쟁(소기업·소상공인), 총액(전자)입찰'
DOC_NOTE = ['⑥ 소기업, 소상공인 확인서 신청을 증빙할 수 있는 서류', '※ 전자 입찰서 제출 마감일 전일까지 소기업, 소상공인 확인서를 발급받지 못한 업체에 한함']
EXCEPTION = '- 본 입찰은 중기간경쟁제품인 드론을 요구하지만, 수요기관의 특별한 사정으로 인하여 사업에서 요구되는 규격이 특정한 기술을 필요로 하므로 ｢중소기업제품 구매촉진 및 판로지원에 관한 법률 시행령｣ 제7조제1항제4호 규정을 적용하여 직접생산확인품목에서 제외하고 일반물품으로 입찰공고합니다.'
BUS_LIC = '[여객자동차운수사업(구역여객자동차운송사업-전세버스)(5805)]'
EVENT_LIC = '[기타자유업(행사대행업)(9901)]'
SW_LIC = '[소프트웨어사업자(컴퓨터관련서비스사업)(1468)]'


def rec(title, lines, work='일반용역', P=1e8, codes=None, license_=None, docs=None, method='제한경쟁', award='적격심사제'):
    meta = {'업무구분': work, '적용계약법': '지방계약법', '계약방법': method, '낙찰방법': award, '입찰추정가격': P,
            '세부품명번호목록': codes, '면허업종제한목록': license_, '조항호내용': None}
    text = '\n'.join(l.format(title=title) for l in QUAL) + '\n' + '\n'.join(lines)
    return {'id': 'T', 'meta': meta, 'docs': [{'type': '공고문', 'text': text}] + (docs or [])}


def bundle(*args, **kw):
    return facts.build(rec(*args, **kw), catalog.load())


def answer(item='통학운송서비스', note='해당 없음', dp='참가자격 요건', size='중소기업자 요건', line=-1, quote='', exc='없음'):
    return {'object_item': item, 'note_exclusion': note, 'dp_requirement': dp, 'size_clause': size, 'size_line': line,
            'size_quote': quote, 'exception_clause': exc}


def read(b, **kw):
    setattr(b, C.FAM, answer(**kw))
    return b


def line_of(b, needle):
    return next(ln for ln in b.notice.lines if needle in ln.text)


def bus(lines, **kw):
    return bundle('초등학교 통학차량 임차용역', lines, license_=BUS_LIC, **kw)


def event(lines, **kw):
    return bundle('2026년 상반기 라이브공연 행사대행 용역', lines, license_=EVENT_LIC, **kw)


# ---------------------------------------------------------------- wiring
def test_stage_is_registered(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED', ('v10', 'v11', 'v13'))
    assert dedicated.stage('v10') is C and dedicated.stage('v13') is C and C.FAM == 'd_competition_product'
    assert dedicated.active() == [C]


# ---------------------------------------------------------------- CPU gate
def test_private_contracts_never_reach_the_model():
    assert C.select(bus([DP_BUS], method='수의계약')) is None
    assert C.select(bus([DP_BUS], award='소액수의견적')) is None
    assert C.select(bus([DP_BUS])) is not None


def test_goods_code_in_and_out_of_the_list_and_amount_notes():
    listed = bundle('서버 구매', [SME], work='물품(내자)', P=1.1e8, codes='컴퓨터서버[4321150102]')
    assert C.select(listed)['kind'] == 'goods' and [p.name for p in C.select(listed)['items']] == ['컴퓨터서버']
    assert C.select(bundle('간식 구매', [SME], work='물품(내자)', P=3e7, codes='과일류[5030990101]')) is None            # not listed
    assert C.select(bundle('비품 구매', [SME], work='물품(내자)', P=3e7, codes=None)) is None                           # no code
    cap = '그리드형토목용보강재[3012178401]'
    assert C.select(bundle('보강재 구매', [SME], work='물품(내자)', P=12e8, codes=cap)) is None                          # 10억 cap
    assert C.select(bundle('보강재 구매', [SME], work='물품(내자)', P=9e8, codes=cap)) is not None
    assert C.select(bundle('보강재 구매', [SME], work='물품(내자)', P=1, codes=cap)) is not None                         # placeholder price


def test_service_families_and_amount_notes():
    ev = event([DP_EVENT], P=7e8)
    assert C.select(ev)['kind'] == 'service' and '기타행사기획및대행서비스' in [p.name for p in C.select(ev)['items']]
    assert C.select(event([DP_EVENT], P=12e8)) is None                                                                 # 행사 10억 cap
    fest = bundle('『마늘한우 축제』행사대행 용역', [DP_EVENT], P=3.27e8, license_=EVENT_LIC)
    assert C.select(fest) is None                                                                                      # 축제 3억 cap governs
    assert '축제기획및대행서비스' in [p.name for p in C.select(bundle('『마늘한우 축제』행사대행 용역', [DP_EVENT], P=2e8))['items']]
    sw = bundle('행정정보시스템 고도화 용역', [SME], P=5e8, license_=SW_LIC)
    assert C.select(sw) is not None and all('제48조' in p.note for p in C.select(sw)['items'])
    assert C.select(bundle('행정정보시스템 고도화 용역', [SME], P=2.1e9, license_=SW_LIC)) is None                        # 사업금액 ≥ 20억
    assert C.select(bundle('학술 연구 용역', [SME], P=5e7, license_='[학술.연구용역(1169)]')) is None                      # no family
    assert '전시회기획및대행서비스' not in [p.name for p in C.select(event([DP_EVENT], P=7e8))['items']]                    # no 전시 word


# ---------------------------------------------------------------- decision rule on hand-written answers
def test_A_is_the_missing_certificate_requirement():
    b = event([DP_DOC, SME], P=2.2e8)                                                                                   # DEV-13
    read(b, item='기타행사기획및대행서비스', dp='제출서류·평가서류로만', size='중소기업자 요건')
    assert C.verdict(b, 'A') is True and C.verdict(b, 'B') is None and C.verdict(b, 'C') is None
    read(b, item='기타행사기획및대행서비스', dp='참가자격 요건', size='중소기업자 요건')
    assert C.verdict(b, 'A') is None
    for dp in ('제재·안내 문구만', '언급 없음'):
        read(b, item='기타행사기획및대행서비스', dp=dp)
        assert C.verdict(b, 'A') is True, dp


def test_B_is_the_missing_sme_clause_and_C_the_small_only_clause():
    b = bus([DP_BUS], P=1.9e8)                                                                                          # DEV-14
    read(b, size='없음')
    assert C.verdict(b, 'B') is True and C.verdict(b, 'A') is None and C.verdict(b, 'C') is None
    read(b, size='대기업 참여제한만')                                                                                     # DEV-064 wording
    assert C.verdict(b, 'B') is True
    b = bus([DP_BUS, SMALL], P=1.4e8)                                                                                   # DEV-16
    read(b, size='소기업·소상공인 한정', line=line_of(b, '소기업자로서').i)
    assert C.verdict(b, 'C') is line_of(b, '소기업자로서') and C.verdict(b, 'B') is None and C.verdict(b, 'A') is None
    read(b, size='중소기업자 요건')                                                                                       # DEV-068/075 wording
    assert C.verdict(b, 'B') is None and C.verdict(b, 'C') is None


def test_evidence_line_follows_the_model_then_the_quote_then_the_cpu():
    b = event([DP_EVENT, SMALL_CERT], P=8.8e7)                                                                          # DEV-077
    small = line_of(b, '소기업 ․ 소상공인확인서')
    read(b, item='기타행사기획및대행서비스', size='소기업·소상공인 한정', line=small.i)
    assert C.verdict(b, 'C') is small
    read(b, item='기타행사기획및대행서비스', size='소기업·소상공인 한정', line=line_of(b, '용 역 명').i, quote='중소기업으로서, 소기업 ․ 소상공인확인서를 소지한 업체')
    assert C.verdict(b, 'C') is small                              # a pointed line without the words is ignored; the quote locates it
    read(b, item='기타행사기획및대행서비스', size='소기업·소상공인 한정')
    assert C.verdict(b, 'C') is small                              # CPU reader as the last resort


def test_look_alikes_never_fire():
    b = bundle('숙박형 현장체험학습 위탁용역', [SMALL], P=6e7, license_=BUS_LIC)
    read(b, item=C.NONE_ITEM, dp='언급 없음', size='소기업·소상공인 한정')
    assert all(C.verdict(b, v) is None for v in 'ABC')                                                                  # object not listed
    drone = bundle('소방 구조장비 구매', [EXCEPTION, SME_OR_SOHO], work='물품(내자)', P=1.4e8, codes='드론[2513189901]')      # DEV-23
    read(drone, item='드론', dp='언급 없음', size='중소기업자 요건', exc='중소기업자간 경쟁 외 방법 사유 기재')
    assert all(C.verdict(drone, v) is None for v in 'ABC')
    read(drone, item='드론', dp='언급 없음', note='특이사항 제외 대상')
    assert C.verdict(drone, 'A') is None
    read(drone, item='드론', dp='언급 없음', note='판단 불가', exc='없음')
    assert C.verdict(drone, 'A') is None                           # the stated exception is read by the CPU even when the model misses it
    b = bus([DP_BUS, SME_MID_CERT], P=1.4e8)
    read(b, size='중소기업자 요건')
    assert C.verdict(b, 'C') is None and C.verdict(b, 'B') is None


# ---------------------------------------------------------------- request building
class LenEngine:
    max_model_len = 16384

    def __init__(self, per_char=0.5):
        self.per_char, self.calls = per_char, 0

    def token_ids(self, messages, thinking=False):
        self.calls += 1
        return list(range(int(sum(len(m['content']) for m in messages) * self.per_char)))


def test_request_is_built_only_for_candidates():
    eng = LenEngine()
    b = bus([DP_BUS, SMALL], P=1.4e8)
    r = C.request(eng, b, 3, main.Request)
    assert r is not None and r.rec == 3 and r.fam == C.FAM and r.max_tokens == C.MAX_TOKENS and r.budget == 0 and r.extra == ()
    assert line_of(b, '소기업자로서') in r.cands and line_of(b, '직접생산확인증명서') in r.cands and all(ln in b.notice.lines for ln in r.cands)
    names = r.schema['properties']['object_item']['enum']
    assert '통학운송서비스' in names and names[-2:] == [C.NONE_ITEM, '불명'] and r.schema['properties']['size_clause']['enum'][-1] == '불명'
    assert len(r.token_ids) + r.max_tokens <= C.PROMPT_TOKEN_LIMIT
    assert C.request(eng, bus([DP_BUS], method='수의계약'), 0, main.Request) is None
    assert C.request(eng, bundle('학술 연구 용역', [SME], license_='[학술.연구용역(1169)]'), 0, main.Request) is None


def test_request_shrinks_to_the_token_limit():
    long_lines = [DP_BUS, SMALL] + [f'{k}) 중소기업 관련 안내 문장 ' + '가' * 300 for k in range(20)]
    long_docs = [{'type': '과업지시서', 'text': '\n'.join('과업 내용 ' + '나' * 300 for _ in range(30))}]
    b = bus(long_lines, P=1.4e8, docs=long_docs)
    eng = LenEngine(per_char=2.0)
    r = C.request(eng, b, 0, main.Request)
    assert r is not None and len(r.token_ids) + r.max_tokens <= C.PROMPT_TOKEN_LIMIT and eng.calls > 1
    assert line_of(b, '소기업자로서') in r.cands


# ---------------------------------------------------------------- consume
def test_consume_valid_prefixed_invalid_and_mock():
    eng = LenEngine()
    b = bus([DP_BUS, SMALL], P=1.4e8)
    r = C.request(eng, b, 0, main.Request)
    good = json.dumps(answer(size='소기업·소상공인 한정', line=line_of(b, '소기업자로서').i), ensure_ascii=False)
    assert C.consume(b, r.cands, good) is True and getattr(b, C.FAM)['size_line'] == line_of(b, '소기업자로서').i
    assert C.consume(b, r.cands, '<|channel>생각<channel|>' + good) is True
    assert C.consume(b, r.cands, 'not json') is False
    assert C.consume(b, r.cands, json.dumps(dict(answer(), size_clause='없는값'), ensure_ascii=False)) is False
    assert C.consume(b, r.cands, json.dumps(dict(answer(), object_item='드론'), ensure_ascii=False)) is False           # not offered
    assert C.consume(b, r.cands, json.dumps({'object_item': '통학운송서비스'})) is False
    assert C.consume(b, r.cands, json.dumps(dict(answer(), size_line='3'))) is False
    assert C.consume(b, r.cands, json.dumps(dict(answer(), size_line=99999), ensure_ascii=False)) is True and getattr(b, C.FAM)['size_line'] == -1
    mock = MockEngine().generate([(r.token_ids, r.schema, r.max_tokens)])[0][0]
    assert C.consume(b, r.cands, mock) is True
    reading = getattr(b, C.FAM)
    assert reading['object_item'] == '불명' and reading['size_clause'] == '불명' and reading['size_line'] == -1 and reading['size_quote'] == '-'
    assert C.verdict(b, 'C') is line_of(b, '소기업자로서') and C.verdict(b, 'A') is None     # 불명 falls back to the CPU readers


def test_main_consume_routes_to_the_stage(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED', ('v10', 'v11', 'v13'))
    b = bus([DP_BUS], P=1.4e8)
    r = C.request(LenEngine(), b, 0, main.Request)
    assert main.consume(b, r, json.dumps(answer(size='없음'), ensure_ascii=False)) is True and getattr(b, C.FAM)['size_clause'] == '없음'


# ---------------------------------------------------------------- verdict without a reading
def test_verdict_without_reading_uses_cpu_defaults():
    b = bus([DP_BUS], P=1.9e8)                                                                                          # DEV-14
    assert b.scope.competitive is True and C.verdict(b, 'B') is True and C.verdict(b, 'A') is None and C.verdict(b, 'C') is None
    b = bus([DP_BUS, SMALL], P=1.4e8)                                                                                   # DEV-16
    assert C.verdict(b, 'C') is line_of(b, '소기업자로서') and C.verdict(b, 'B') is None
    b = event([SMALL_LIST[0], '3. 낙찰자 결정방법'], P=7.3e7)                                                              # DEV-069
    assert C.verdict(b, 'A') is True and C.verdict(b, 'C') is line_of(b, '창업자') and C.verdict(b, 'B') is None
    b = event([DP_DOC, SME], P=2.2e8)                                                                                   # DEV-13
    assert C.verdict(b, 'A') is True and C.verdict(b, 'B') is None
    b = event([LABEL, DP_PENALTY, '6. 제출서류'] + DOC_NOTE, P=6.4e7)                                                     # DEV-039
    assert C.verdict(b, 'A') is True and C.verdict(b, 'B') is True and C.verdict(b, 'C') is None
    b = bundle('학습분석시스템 유지보수', LARGE_ONLY, P=6.9e8, license_=SW_LIC)                                              # DEV-064
    assert b.scope.competitive is True and C.verdict(b, 'A') is True and C.verdict(b, 'B') is True
    b = bus([DP_BUS, SME_MID_CERT], P=1.4e8)                                                                            # DEV-11 wording
    assert C.verdict(b, 'B') is None and C.verdict(b, 'C') is None
    drone = bundle('소방 구조장비 구매', [EXCEPTION, SME_OR_SOHO], work='물품(내자)', P=1.4e8, codes='드론[2513189901]')      # DEV-23
    assert all(C.verdict(drone, v) is None for v in 'ABC')
    assert C.verdict(bundle('숙박형 현장체험학습 위탁용역', [SMALL], P=6e7, license_='[종합여행업(1261)]'), 'C') is None    # not a listed object
    assert C.verdict(bus([DP_BUS], method='수의계약'), 'B') is None


def test_judge_takes_the_stage_verdicts():
    b = bus([DP_BUS, SMALL], P=1.4e8)
    out = judge.judge(b)
    assert out['v10'] == (0, '') and out['v11'] == (0, '') and out['v13'] == (1, SMALL.strip())
    out = judge.judge(event([SMALL_LIST[0], '3. 낙찰자 결정방법'], P=7.3e7))
    assert out['v10'] == (1, '') and out['v11'] == (0, '') and out['v13'][0] == 1
