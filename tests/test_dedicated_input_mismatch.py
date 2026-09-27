"""v24 dedicated stage (pps_c/dedicated/input_mismatch.py, BLUEPRINT.md): per-axis decision rules on hand-written readings (dev
positives and look-alike negatives), request building with a length-based fake engine, consume of valid / invalid / mock
outputs, and the verdict from the CPU stand-in when no reading exists. Runs against the copy under impl/submission."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import catalog, dedicated, facts, judge, main, switches  # noqa: E402
from pps_c.dedicated import input_mismatch as st  # noqa: E402
from pps_c.runner import MockEngine  # noqa: E402

CAT = catalog.load()
META = {'적용계약법': '지방계약법', '업무구분': '일반용역', '계약방법': '제한경쟁', '낙찰방법': '협상에의한계약', '소관구분': '지방정부',
        '배정예산금액': 170000000, '입찰추정가격': 154545455, '지역제한여부': 'N', '제한지역코드목록': None,
        '업종제한여부': 'N', '면허업종제한목록': None, '조항호내용': '협상에 의한 계약', '공고게시일자': '20260302'}


class Req:
    def __init__(self, *a):
        self.rec, self.fam, self.cands, self.extra, self.token_ids, self.schema, self.max_tokens, self.budget = a


class LengthEngine:
    """token count = characters / 2, as MockEngine; max_model_len settable to force the excerpt to shrink."""
    def __init__(self, max_model_len=16384):
        self.max_model_len = max_model_len

    def token_ids(self, messages, thinking=False):
        return list(range(sum(len(m['content']) for m in messages) // 2))


def bundle(*lines, **meta):
    m = dict(META, **meta)
    text = '\n'.join(('입찰공고', '1. 입찰에 부치는 사항') + lines + ('2. 입찰참가자격', '가. 자격을 갖춘 자'))
    return facts.build({'id': 'T', 'meta': m, 'docs': [{'type': '공고문', 'text': text}]}, CAT)


def line(b, hint):
    return next(x for x in b.notice.lines if hint in x.text)


def reading(b, budget=(), method=('없음', None), region=('없음', set(), None), industry=('없음', set(), [], None), sme='없음'):
    """A model reading: budget = [(hint, kind, amount, vat, scope)], method = (stated, hint), region = (flag, 시·도 set, hint),
    industry = (flag, codes, names, hint)."""
    return {'budget': [(line(b, h), kind, float(v), vat, scope) for h, kind, v, vat, scope in budget],
            'contract_method': (method[0], line(b, method[1]) if method[1] else None),
            'restriction': {'region': region[0], 'industry': industry[0], 'sme': sme},
            'region': (set(region[1]), False, line(b, region[2]) if region[2] else None),
            'industry': (set(industry[1]), list(industry[2]), line(b, industry[3]) if industry[3] else None), 'source': 'test'}


def hit(b, r):
    ln = st.decide(b, r)
    return ln.text if ln is not None else None


# ------------------------------------------------------------------------------------------------------ decision rules

def test_budget_axis_dev29_and_look_alikes():
    b = bundle('나. 용역금액: 금37,930,000원(추정가격 34,481,818원, 부가가치세 3,448,182원)', 배정예산금액=39730000, 입찰추정가격=36118183)
    assert hit(b, reading(b, budget=[('용역금액', '사업금액', 37930000, '포함', '총액'), ('용역금액', '추정가격', 34481818, '미포함', '총액')])) \
        == '나. 용역금액: 금37,930,000원(추정가격 34,481,818원, 부가가치세 3,448,182원)'          # DEV-29 = 1
    same = bundle('라. 사업예정총액: 금36,752,500원', 배정예산금액=36750000, 입찰추정가격=33409091)
    assert hit(same, reading(same, budget=[('사업예정총액', '사업예산', 36752500, '불명', '총액')])) is None   # DEV-15: rounding
    vat = bundle('ㅇ 추정금액: 금306,039,000원(부가가치세 0원)', 배정예산금액=336642900, 입찰추정가격=336642900)
    assert hit(vat, reading(vat, budget=[('추정금액', '사업예산', 306039000, '미포함', '총액')])) is None       # exact VAT relation
    unit = bundle('마. 기 초 금 액 : 금2,046,000원(추정가격 1,860,000원)', '※ 단가계약 입찰이므로 기초금액을 기준으로 투찰', 배정예산금액=94035000, 입찰추정가격=85486364)
    assert hit(unit, reading(unit, budget=[('기 초 금 액', '기초금액', 2046000, '포함', '단가')])) is None    # DEV-097: unit price
    part = bundle('- 금차 계약금액: 14,800,000원', 배정예산금액=67600000, 입찰추정가격=67600000)
    assert hit(part, reading(part, budget=[('금차', '기타', 14800000, '불명', '금차분')])) is None           # partial amount
    items = bundle('가. 1차년도 : 85,000,000원', '나. 사업예산 : 170,000,000원')
    assert hit(items, reading(items, budget=[('1차년도', '기타', 85000000, '불명', '항목별'), ('사업예산', '사업예산', 170000000, '불명', '총액')])) is None
    zero = bundle('마. 기초금액 : 金103,200,000원(부가가치세 포함 금액)', 배정예산금액=0, 입찰추정가격=0)
    assert hit(zero, reading(zero, budget=[('기초금액', '기초금액', 103200000, '포함', '총액')])) is None    # no registered amount: nothing to compare


def test_clause_ceiling_axis_dev055_dev056_dev114():
    clause = '추정가격 1억원 미만 물품·용역(소기업, 소상공인, 벤처기업, 창업자)'
    b = bundle('ㅇ 사업금액 : 총 188,000,000원(부가가치세 포함)', 적용계약법='국가계약법', 배정예산금액=188000000, 입찰추정가격=170909091, 조항호내용=clause)
    assert hit(b, reading(b, budget=[('사업금액', '사업금액', 188000000, '포함', '총액')])) == 'ㅇ 사업금액 : 총 188,000,000원(부가가치세 포함)'   # DEV-055 = 1
    twin = bundle('ㅇ 사업금액 : 총 88,000,000원(부가가치세 포함)', 적용계약법='국가계약법', 배정예산금액=88000000, 입찰추정가격=80000000, 조항호내용=clause)
    assert hit(twin, reading(twin, budget=[('사업금액', '사업금액', 88000000, '포함', '총액')])) is None                                        # DEV-056 = 0
    below = bundle('나. 기초금액 : 금19,876,000원 - 부가가치세 포함', 계약방법='수의계약', 낙찰방법='소액수의견적', 배정예산금액=19876000, 입찰추정가격=18069091,
                   조항호내용='추정가격 2천만원 초과 1억원 이하 물품·용역(소기업 소상공인 계약)')
    assert hit(below, reading(below, budget=[('기초금액', '기초금액', 19876000, '포함', '총액')])) is None                                     # DEV-114: below the floor
    assert st.clause_ceiling('추정가격 1억원이상 고시금액 미만  물품·용역(중소기업자)', '국가', '국가기관') == (230000000, True)
    assert st.clause_ceiling('추정가격 1억원이상 고시금액 미만  물품·용역(중소기업자)', '지방', '지방정부') is None
    assert st.clause_ceiling('행정안전부령 금액 미만 본점소재지', '지방', '지방정부') is None
    assert st.clause_ceiling('추정가격 2천만원 이하 물품 또는 용역', '지방', '교육기관') == (20000000, False)


def test_method_axis_directions():
    b = bundle('가. 입찰방법 : 제한경쟁입찰(중·소기업, 소상공인, 직접생산업체)', 계약방법='일반경쟁', 낙찰방법='적격심사제', 업무구분='물품(내자)')
    r = reading(b, method=('제한경쟁', '입찰방법'), region=('있음', set(), None), industry=('있음', set(), [], None), sme='있음')
    assert hit(b, r) == '가. 입찰방법 : 제한경쟁입찰(중·소기업, 소상공인, 직접생산업체)'                                    # DEV-057 = 1
    slip = bundle('4) 입찰방식 : 일반경쟁입찰 (협상에의한계약)', '기타자유업(행사대행업:업종코드: 9901)으로 입찰참가자격을 등록한 업체',
                  업종제한여부='Y', 면허업종제한목록='[기타자유업(행사대행업)(9901)]')
    assert hit(slip, reading(slip, method=('일반경쟁', '입찰방식'), industry=('있음', {'9901'}, [], '9901'))) is None      # DEV-191 = 0
    bare = bundle('나. 입찰방식: 일반경쟁')
    assert hit(bare, reading(bare, method=('일반경쟁', '입찰방식'))) == '나. 입찰방식: 일반경쟁'                             # 제한경쟁 registered, nothing restricts
    unknown = reading(bare, method=('일반경쟁', '입찰방식'), region=('불명', set(), None), industry=('불명', set(), [], None), sme='불명')
    assert hit(bare, unknown) is None                                                                                  # 불명 is not "없음"
    quote = bundle('가. 본 입찰은 소액수의 견적(제출) 및 총액, 제한경쟁(지역, 소기업·소상공인) 입찰입니다.', 계약방법='수의계약', 낙찰방법='소액수의견적')
    assert hit(quote, reading(quote, method=('제한경쟁', '본 입찰은'))) is None                                             # 견적 boilerplate
    private = bundle('가. 입찰방법 : 일반경쟁입찰', 계약방법='수의계약', 낙찰방법='적격심사제')
    assert hit(private, reading(private, method=('일반경쟁', '입찰방법'))) == '가. 입찰방법 : 일반경쟁입찰'
    assert hit(b, reading(b, method=('없음', None))) is None and hit(b, reading(b, method=('불명', None))) is None


def test_region_axis():
    reg = dict(지역제한여부='Y', 제한지역코드목록='경기도')
    b = bundle('나. 본점 소재지를 계속 강원특별자치도에 둔 업체', **reg)
    assert hit(b, reading(b, region=('있음', {'강원특별자치도'}, '본점'))) == '나. 본점 소재지를 계속 강원특별자치도에 둔 업체'   # disjoint
    same = bundle('나. 본점 소재지를 계속 경기도에 둔 업체', **reg)
    assert hit(same, reading(same, region=('있음', {'경기도'}, '본점'))) is None
    superset = bundle('2) 본점 소재지를 계속 경기도 [지역:r1|단위=기초|광역=경기도] 또는 제주도에 둔 업체', **reg)
    assert hit(superset, reading(superset, region=('있음', {'경기도', '제주특별자치도'}, '본점'))) is None                        # DEV-072 / DEV-10: off by default
    st.REGION_SUPERSET = True
    try:
        assert hit(superset, reading(superset, region=('있음', {'경기도', '제주특별자치도'}, '본점'))) is not None
    finally:
        st.REGION_SUPERSET = False
    unreg = bundle('마. 본사(주된 영업소)의 소재지가 서울특별시에 있는 업체')                                                     # 나라장터 N: never
    assert hit(unreg, reading(unreg, region=('있음', {'서울특별시'}, '본사'))) is None
    nation = bundle('다. 지역제한 없음(전국 업체 참가 가능)', **reg)
    assert hit(nation, reading(nation, region=('전국명시', set(), '지역제한'))) == '다. 지역제한 없음(전국 업체 참가 가능)'
    local = bundle('본점소재지가 [수요기관(기초자치단체)]내에 소재하고', **reg)
    assert hit(local, reading(local, region=('있음', set(), '본점소재지'))) is None                                             # 관내 only: no 시·도 to compare


def test_industry_axis_dev079():
    lic = dict(업종제한여부='Y', 면허업종제한목록='[식품판매업(집단급식소식품판매업)(5246)]')
    b = bundle('출 마감일 전일까지 식품판매업(업종코드: 5210)으로 입찰참가자격을 등', **lic)
    assert hit(b, reading(b, industry=('있음', {'5210'}, [], '5210'))) == '출 마감일 전일까지 식품판매업(업종코드: 5210)으로 입찰참가자격을 등'
    ok = bundle('식품판매업(업종코드: 5246)으로 입찰참가자격을 등록한 자', **lic)
    assert hit(ok, reading(ok, industry=('있음', {'5246'}, [], '5246'))) is None
    alt = bundle('종합여행업[업종코드 1261] 또는 국내여행업[업종코드 1263] 으로 등록한 자', 업종제한여부='Y',
                 면허업종제한목록='[국내외여행업(1262)]업종 또는[종합여행업(1261)]업종 또는[국내여행업(1263)]')
    assert hit(alt, reading(alt, industry=('있음', {'1261', '1263'}, [], '종합여행업'))) is None                                 # subset of the registered codes
    names = bundle('나. 폐기물중간처리업(건설폐기물) 허가를 받은 업체', 업종제한여부='Y', 면허업종제한목록='[건설폐기물 중간처리업(1253)]')
    assert hit(names, reading(names, industry=('있음', set(), ['폐기물중간처리업(건설폐기물)'], '폐기물중간처리업'))) is None    # shared token
    other = bundle('나. 행사대행업으로 등록한 업체', 업종제한여부='Y', 면허업종제한목록='[학술.연구용역(1169)]')
    assert hit(other, reading(other, industry=('있음', set(), ['행사대행업'], '행사대행업'))) == '나. 행사대행업으로 등록한 업체'
    unreg = bundle('학술연구용역(업종코드 : 1169)로 업종을 등록한 자')                                                          # 나라장터 N: never (dev 7:1)
    assert hit(unreg, reading(unreg, industry=('있음', {'1169'}, [], '1169'))) is None


# ------------------------------------------------------------------------------------------------------- request

def test_request_prescreen_and_shrinking():
    quiet = bundle('나. 사업예산 : 170,000,000원', '가. 입찰방법 : 제한경쟁입찰')
    assert st.request(LengthEngine(), quiet, 0, Req) is None                                        # everything agrees: no call
    b = bundle('나. 용역금액: 금37,930,000원(추정가격 34,481,818원)', 배정예산금액=39730000, 입찰추정가격=36118183)
    r = st.request(LengthEngine(), b, 3, Req)
    assert r is not None and r.fam == st.FAM and r.rec == 3 and r.max_tokens == st.MAX_TOKENS and r.extra == ()
    assert any('용역금액' in ln.text for ln in r.cands) and r.schema['required'] == ['budget', 'contract_method', 'participation_restriction', 'region', 'industry']
    assert len(r.token_ids) + r.max_tokens <= st.PROMPT_TOKEN_LIMIT
    big = bundle(*[f'{k}) 항목 {k} 사업예산 : {k + 1},000,000원' for k in range(120)], 배정예산금액=999000000, 입찰추정가격=908181818)
    eng = LengthEngine(max_model_len=4000)
    r2 = st.request(eng, big, 0, Req)
    assert r2 is not None and len(r2.cands) < 120 and len(r2.token_ids) + st.MAX_TOKENS <= eng.max_model_len - 64
    tiny = LengthEngine(max_model_len=200)
    assert st.request(tiny, big, 0, Req) is None                                                   # cannot fit: no request, no error


# ------------------------------------------------------------------------------------------------------- consume

def model_output(**over):
    obj = {'budget': {'stated': '없음', 'items': []}, 'contract_method': {'stated': '없음', 'quote': ''},
           'participation_restriction': {'region': '없음', 'industry': '없음', 'sme': '없음'},
           'region': {'regions': [], 'quote': ''}, 'industry': {'codes': [], 'names': [], 'quote': ''}}
    for k, v in over.items():
        obj[k] = v
    return json.dumps(obj, ensure_ascii=False)


def test_consume_valid_invalid_and_mock():
    b = bundle('나. 용역금액: 금37,930,000원(추정가격 34,481,818원)', 배정예산금액=39730000, 입찰추정가격=36118183)
    r = st.request(LengthEngine(), b, 0, Req)
    out = model_output(budget={'stated': '있음', 'items': [
        {'kind': '사업금액', 'amount': '금37,930,000원', 'vat': '포함', 'scope': '총액', 'quote': '나. 용역금액: 금37,930,000원'},
        {'kind': '기타', 'amount': '99,999,999원', 'vat': '불명', 'scope': '총액', 'quote': '지어낸 줄'}]})
    assert st.consume(b, r.cands, '<|channel>생각<channel|>' + out) is True
    assert [(it[1], it[2]) for it in b.d_input_mismatch['budget']] == [('사업금액', 37930000.0)]       # the ungrounded quote is dropped
    assert st.verdict(b).text == '나. 용역금액: 금37,930,000원(추정가격 34,481,818원)'
    assert st.consume(b, r.cands, 'not json') is False
    assert st.consume(b, r.cands, '{"budget": 1}') is False
    assert st.consume(b, r.cands, model_output(contract_method={'stated': '협상', 'quote': ''})) is False   # enum violated
    eng = MockEngine()
    text, finish, _ = eng.generate([(r.token_ids, r.schema, r.max_tokens, 0)])[0]
    assert st.consume(b, r.cands, text) is True and b.d_input_mismatch['budget'] == []
    assert b.d_input_mismatch['contract_method'] == ('불명', None) and st.verdict(b) is None      # all-불명 answer decides nothing


def test_verdict_without_a_reading_uses_the_standin_and_judge_integration(monkeypatch):
    b = bundle('나. 용역금액: 금37,930,000원(추정가격 34,481,818원, 부가가치세 3,448,182원)', 배정예산금액=39730000, 입찰추정가격=36118183)
    assert not hasattr(b, 'd_input_mismatch')
    assert st.verdict(b).text == '나. 용역금액: 금37,930,000원(추정가격 34,481,818원, 부가가치세 3,448,182원)'
    lic = bundle('출 마감일 전일까지 식품판매업(업종코드: 5210)으로 입찰참가자격을 등', 업종제한여부='Y', 면허업종제한목록='[식품판매업(집단급식소식품판매업)(5246)]')
    assert st.verdict(lic).text.startswith('출 마감일')
    meth = bundle('가. 입찰방법 : 제한경쟁입찰(중·소기업, 소상공인, 직접생산업체)', 계약방법='일반경쟁', 낙찰방법='적격심사제')
    assert st.verdict(meth).text == '가. 입찰방법 : 제한경쟁입찰(중·소기업, 소상공인, 직접생산업체)'
    slip = bundle('4) 입찰방식 : 일반경쟁입찰 (협상에의한계약)', '기타자유업(행사대행업:업종코드: 9901)으로 입찰참가자격을 등록한 업체',
                  업종제한여부='Y', 面허업종제한목록='[기타자유업(행사대행업)(9901)]'.replace('面', '면'))
    assert st.verdict(slip) is None
    quiet = bundle('나. 사업예산 : 170,000,000원')
    assert st.verdict(quiet) is None
    shared = judge.judge(b)
    monkeypatch.setattr(switches, 'DEDICATED', ('v24',))
    assert dedicated.by_family(st.FAM) is st
    out = judge.judge(b)
    assert out['v24'] == (1, '나. 용역금액: 금37,930,000원(추정가격 34,481,818원, 부가가치세 3,448,182원)')
    assert {k: v for k, v in out.items() if k != 'v24'} == {k: v for k, v in shared.items() if k != 'v24'}
    assert judge.judge(quiet)['v24'] == (0, '')
    req = main.Request(0, st.FAM, st.request(LengthEngine(), b, 0, Req).cands, (), [], {}, 16)
    assert main.consume(b, req, model_output()) is True and b.d_input_mismatch['source'] == 'model'
