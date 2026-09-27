"""v24 dedicated stage — 공고서와 나라장터 입력값 상이 (h08 clean-room blueprint, input_mismatch/BLUEPRINT.md).

The 공고문 is the reference and meta the 나라장터 input. CPU selects the lines that state a budget, the contract method, a
seat-of-business restriction or a required 업종; one JSON-bound model call copies what those lines state (never meta, never a
verdict); the CPU compares the reading with meta on four axes and returns the quoted notice line as evidence. Without a
reading (deadline, parse failure) the CPU stand-in built from the same regexes decides.
"""
import collections
import json
import re

from .. import families as F, regions
from ..v24 import parse as vparse

FAM = 'd_input_mismatch'
PROMPT_TOKEN_LIMIT = 12000
MAX_TOKENS = 500
HEADER_LINES = 20
CAP = 60
MIN_CAP = 8
REGION_SUPERSET = False       # also fire when the notice lists more 시·도 than 나라장터 (dev DEV-10 = 0, DEV-072 = 1): off
REL_TOL = 0.01                # rounding tolerance for amounts (dev DEV-15: 36,752,500 vs 36,750,000 = 0)
GOSI = {'국가기관': 230_000_000, '준정부기관': 710_000_000, '공기업': 710_000_000}   # law/고시금액.txt, 물품·용역 WTO 개방대상금액

# ---------------------------------------------------------------------------------------------------------- budget
KW_BUDGET = re.compile(r'(사업예산|예산액|예산금액|배정예산|기초예산|추정예산|기초금액|추정가격|추정금액|사업금액|용역금액|총사업비|사업비|물품금액|구매금액|사업예정총액|예산)')
HEADLINE_KIND = {'기초금액': '기초금액', '추정가격': '추정가격', '사업금액': '사업금액', '용역금액': '사업금액'}
VARIABLE_AMOUNT = re.compile(r'에\s*따라\s*(?:변경|변동|조정)|(?:변경|변동)\s*될\s*수')   # '운행 일수에 따라 변경 될수 있음'
BAND_AMOUNT = re.compile(r'(?:\d[\d,.]*\s*(?:조|억|천만|백만|만|천)?\s*)+원?\s*(?:미만|이상|이하|초과)')   # '1억 5천만원 미만'
UNIT_PRICE = re.compile(r'단가\s*(?:계약|입찰|견적|금액|총액|공고)|개별\s*단가|단가\s*(?:로|를)\s*투찰|\(\s*단가\s*\)|단가입찰|단가계약')
PARTIAL = re.compile(r'금차|당해\s*연도|당해년도|금년도분|1차분|차수별|연차별|1차년도|1년차')
EXCLUDE_AMT_LINE = re.compile(r'실적|규모\s*\(금액|보증금|보험|수수료|위약|지체|채권|배상|과태료|벌금|손해|자본금|매출액|연매출|신용평가|검색|조회')
VAT_EXCL = re.compile(r'부가(?:가치)?세\s*(?:별도|미포함|제외|불포함)|VAT\s*(?:별도|미포함|제외)')
VAT_INCL = re.compile(r'부가(?:가치)?세\s*포함|VAT\s*포함')
EST_LABELLED = re.compile(r'추정가격\s*[:：]?\s*(?:금)?\s*(\d{1,3}(?:,\d{3})+|\d{7,})')
DIGIT_AMOUNT = re.compile(r'(?<![\d,.])(\d{1,3}(?:,\d{3}){2,}|\d{7,})(?![\d,])')

# -------------------------------------------------------------------------------------------------- contract method
METHOD_HEAD = re.compile(r'(계약\s*방법|입찰\s*방법|계약\s*방식|입찰\s*방식|입찰\s*형태|입찰\s*및\s*계약\s*방[법식]|본\s*입찰은|본\s*용역은|본\s*계약은|본\s*건은|이\s*입찰은|본\s*공고는|입찰\s*구분|계약\s*및\s*낙찰\s*방법)')
METHOD_CAT = (
    ('수의계약', re.compile(r'수의\s*계약|소액\s*수의|수의\s*견적|수의\s*\(|\(\s*수의\s*\)|총액\s*\(\s*수의|견적\s*제출\s*안내|견적\s*제출\s*대상|전자\s*견적\s*제출|견적\s*입찰')),
    ('제한경쟁', re.compile(r'제한\s*경쟁|제한\s*\(\s*총액|제한\s*총액|제한\s*입찰|제한\s*\(\s*지역|제한\s*\(\s*업종')),
    ('지명경쟁', re.compile(r'지명\s*경쟁')),
    ('일반경쟁', re.compile(r'일반\s*경쟁|일반\s*\(\s*총액|일반\s*총액|일반\s*입찰')),
)
METHOD_NOISE = re.compile(r'검색|조회|지정한\s*후|참고하시|예시|유찰|재입찰|결렬|재공고\s*시')
SME_CUE = re.compile(r'소기업|소상공인|중소기업자|중소기업\s*확인서|직접생산확인|중기업')
QUOTE_CUE = re.compile(r'견적|수의')

# ---------------------------------------------------------------------------------------------------------- region
SIDO_NAMES = tuple(regions.SIDO)
SEAT_CUE = re.compile(r'소재지|본점|영업소|주\s*사무소|주된\s*사무소|사업장\s*(?:소재|주소|이)|관내\s*(?:업체|소재|에\s*소재|사업자)|지역\s*제한|제한\s*지역|지역\s*업체|지역에\s*(?:소재|있는|둔|주소)')
BIDDER_CUE = re.compile(r'업체|사업자|법인|자이어야|자로서|한\s*자|둔\s*자|자\s*$|자에\s*한|제한')
NOREGION_CUE = re.compile(r'지역\s*제한\s*(?:없음|없이|은?\s*없|을\s*하지|미적용|을\s*두지)|소재지\s*(?:를|에)?\s*제한(?:하지|없)|전국\s*(?:을\s*대상|의\s*업체|단위\s*(?:업체|사업자))|전국\s*업체')
ADDRESS_LINE = re.compile(r'제출\s*장소|납품\s*장소|개찰\s*장소|접수\s*장소|설치\s*장소|이행\s*장소|사업\s*장소|근무\s*지|주\s*소\s*[:：]|☎|☏|전화|홈페이지|E-?mail|이메일|FAX|팩스')
LAW_QUOTE = re.compile(r'제\d+항에도\s*불구하고|<개정|지역제한경쟁입찰을\s*부치는\s*경우')
LOCAL_CUE = re.compile(r'\[(?:수요기관|기관)\([^\]]*\)[^\]]*\]\s*(?:관내|내에|내|에\s*(?:있는|소재|둔)|지역)')

# -------------------------------------------------------------------------------------------------------- industry
CODE_RE = re.compile(r'업종\s*코드\s*[:：]?\s*(\d{4})(?!\d)|\[\s*업종코드\s*(\d{4})\s*\]|\(\s*업종코드\s*[:：]?\s*(\d{4})\s*\)'
                     r'|(?<![\d\-.])(\d{4})\s*[)\]】]?\s*(?:으로|로|의)\s*(?:입찰\s*참가\s*자격|입찰\s*참가|업종을|등록)'
                     r'|업종\s*[:：]?\s*[가-힣·\s()]{2,40}?\(\s*(\d{4})\s*\)'
                     r'|(?:업종명\s*)?[가-힣]{2,20}(?:업|사업자?|용역|서비스업|기획자|회사)\s*(?:\(|:|：)\s*(\d{4})\s*\)?(?!\s*분의)')
IND_CUE = re.compile(r'업종|업으로\s*등록|으로\s*등록한|로\s*등록한|등록을\s*필한|면허|허가를\s*받은|허가를\s*득한|등록증을\s*보유|등록된\s*업체|사업자로\s*등록|입찰\s*참가\s*자격을\s*등록')
IND_NOISE = re.compile(r'입찰참가자격등록증|이용자\s*등록|변경등록|부정당|사업자등록증|법인등기|등록정보|등록사항')
EXEMPT = re.compile(r'등록하지\s*않아도|등록하지\s*아니하여도|없이도\s*입찰|면제')
DOTS = '·ㆍ‧・'     # · ㆍ ‧ ・
GENERIC_TOKENS = {'업', '사업', '사업자', '서비스', '기타', '용역', '관련', '일반', '전문', '종합', '및', '등', '자유업', '기타자유업'}


# =============================================================================================== CPU extraction (Lines)

def notice_lines(notice):
    return [ln for ln in notice.notice_lines() if ln.text.strip()]


def band_values(quote):
    """Amounts a quote states as band thresholds ('1억원 이상 2억원 미만인 용역' -> 1e8, 2e8)."""
    return {a for m in BAND_AMOUNT.finditer(quote or '') for a in amounts_in(m.group(0))}


def amounts_in(text):
    """won amounts written on a line (digits with commas, 천원·백만원 units, Korean numerals), ≥ 100,000."""
    return vparse.money(text)


def budget_lines(lines):
    """(Line, amounts, keyword, table_cell): a budget keyword with an amount, or an amount cell within 8 lines of a bare keyword."""
    out, pending = [], None
    for ln in lines:
        s = ln.text.strip()
        if EXCLUDE_AMT_LINE.search(s) or len(s) > 300:
            continue
        kw, am = KW_BUDGET.search(s), amounts_in(s)
        if kw and am:
            out.append((ln, am, kw.group(1), False)); pending = None
        elif kw and not am and len(s) < 40:
            pending = (ln, kw.group(1))
        elif am and pending and ln.doc == pending[0].doc and ln.i - pending[0].i <= 8:
            out.append((ln, am, pending[1], True))
    return out


def method_statements(lines):
    out = []
    for ln in lines:
        s = ln.text.strip()
        if len(s) > 400 or METHOD_NOISE.search(s) or not METHOD_HEAD.search(s):
            continue
        cats = [c for c, p in METHOD_CAT if p.search(s)]
        if cats:
            out.append((ln, cats))
    return out


def region_statements(notice, lines):
    """(anchor Line, window Lines, 시·도 set, local, nationwide) for 3-line windows stating where the bidder's seat must be."""
    out, seen = [], set()
    for ln in lines:
        s = ln.text.strip()
        if ADDRESS_LINE.search(s) or LAW_QUOTE.search(s):
            continue
        window = [w for w in notice.window(ln.i, 0, 2) if w.text.strip()]
        win = ' '.join(w.text.strip() for w in window)[:900]
        sido = regions.mentions(win)['sido']
        local = bool(LOCAL_CUE.search(win)) or (bool(re.search(r'관내', win)) and not sido)
        if NOREGION_CUE.search(win):
            if 'NORES' not in seen:
                seen.add('NORES'); out.append((ln, window, set(), False, True))
            continue
        if not ((sido or local) and SEAT_CUE.search(win) and BIDDER_CUE.search(win)):
            continue
        key = (frozenset(sido), local)
        if key in seen:
            continue
        seen.add(key); out.append((ln, window, sido, local, False))
    return out


def industry_codes(lines):
    out = collections.OrderedDict()
    for ln in lines:
        s = ln.text.strip()
        if EXEMPT.search(s) or re.search(r'\d{4}\s*분의', s):
            continue
        for m in CODE_RE.finditer(s):
            code = next(g for g in m.groups() if g)
            if code[:2] in ('19', '20', '00') or code == '1000' or not (IND_CUE.search(s) or re.search(r'등록|참가자격', s)):
                continue
            out.setdefault(code, ln)
    return out


def industry_lines(lines):
    return [ln for ln in lines if IND_CUE.search(ln.text) and not IND_NOISE.search(ln.text)]


def meta_codes(meta):
    return set(re.findall(r'\((\d{4})\)', str(meta.license or ''))) if meta.license else set()


def name_tokens(name):
    """Content tokens of a licence name, split at the dots and also with the dots removed, so 상·하수도 / 상ㆍ하수도 /
    상하수도 all share 상하수도..., while a partial name (창호공사업) still meets 실내건축·창호공사업 (Codex, review 9/27)."""
    name = name or ''
    split = re.sub('[' + DOTS + r'.\s()]', ' ', name)
    joined = re.sub(r'[.\s()]', ' ', re.sub('[' + DOTS + ']', '', name))
    toks = set(re.findall(r'[가-힣]{2,}', split)) | set(re.findall(r'[가-힣]{2,}', joined))
    return {t for t in toks if t not in GENERIC_TOKENS}


def meta_name_tokens(meta):
    toks = set()
    for m in re.finditer(r'([가-힣·ㆍ‧・.\s]+?)(?:\(([^()]*)\))?\((\d{4})\)', str(meta.license or '')):
        toks |= name_tokens(m.group(1))
        if m.group(2):
            toks |= name_tokens(m.group(2))
    return toks


def registered_amounts(b):
    """(배정예산금액, 입찰추정가격) as registered on 나라장터; a P derived by meta.build from the notice or from B is not an input."""
    return b.meta.B, (b.meta.P if b.meta.P_source == 'meta' else None)


def near(x, amounts):
    return x is not None and any(abs(x - a) <= max(10, REL_TOL * x) for a in amounts)


def amount_consistent(B, P, amts):
    """a notice amount equals 배정예산 or 입찰추정가격 within 1 %, directly, via VAT (×1.1, ÷1.1) or as the sum of the listed items."""
    if not amts:
        return False
    cands = list(amts) + [a * 1.1 for a in amts] + [a / 1.1 for a in amts] + [sum(set(amts))]
    return near(B, cands) or near(P, cands)


def clause_ceiling(clause, law, agency):
    """upper bound (won, exclusive?) of the 추정가격 band 나라장터 조항호내용 names; None when the clause has no numeric ceiling."""
    if not clause:
        return None
    c = str(clause).replace(' ', '')
    m = re.search(r'추정가격(?:\d+천만원(?:초과|이상))?(\d+)억원(이하|미만)', c)
    if m:
        return int(m.group(1)) * 100_000_000, m.group(2) == '미만'
    m = re.search(r'추정가격(?:\d+천만원(?:초과|이상))?(\d+)천만원(이하|미만)', c)
    if m:
        return int(m.group(1)) * 10_000_000, m.group(2) == '미만'
    if '고시금액미만' in c and law == '국가' and agency in GOSI:
        return GOSI[agency], True
    return None


# ================================================================================================= candidate selection

def select(b):
    """CPU prescreen. Returns (cands, reach, parts) — reach is the set of axes with something for the model to resolve."""
    notice = b.notice
    lines = notice_lines(notice)
    text = '\n'.join(ln.text for ln in lines)
    B, P = registered_amounts(b)
    bl = budget_lines(lines)
    amts = [a for _, am, _, _ in bl for a in am]
    ms = method_statements(lines)
    rs = region_statements(notice, lines)
    ic = industry_codes(lines)
    il = industry_lines(lines)
    reach = set()
    if bl and (B or P) and not amount_consistent(B, P, amts):
        reach.add('budget')
    ceil = clause_ceiling(b.meta.clause, b.meta.law, notice.meta.get('소관구분'))
    if bl and ceil and any(a / 1.1 >= ceil[0] * 0.95 for a in amts if a <= 20 * max(B or 0, P or 0, 1)):
        reach.add('band')
    if ms and b.meta.method and any(b.meta.method not in cats for _, cats in ms):
        reach.add('method')
    if b.meta.region_flag == 'Y' and rs:
        tg = set().union(*[s for _, _, s, _, _ in rs])
        if any(nr for *_, nr in rs) or (tg and tg != set(b.meta.region_sido)):
            reach.add('region')
    if b.meta.license_flag == 'Y':
        mc = meta_codes(b.meta)
        if ic and mc and not (set(ic) & mc):
            reach.add('industry')
        elif not ic and il and not (meta_name_tokens(b.meta) & name_tokens(re.sub(r'[^가-힣]', ' ', text))):
            reach.add('industry')
    prio = {}
    for ln, *_ in bl:
        prio[ln.i] = min(prio.get(ln.i, 9), 0)
    for ln, _ in ms:
        prio[ln.i] = min(prio.get(ln.i, 9), 1)
    for ln, window, *_ in rs:
        for w in window:
            prio[w.i] = min(prio.get(w.i, 9), 2)
    for code, ln in ic.items():
        prio[ln.i] = min(prio.get(ln.i, 9), 3)
    for ln in il[:12]:
        prio[ln.i] = min(prio.get(ln.i, 9), 4)
    for ln in lines[:HEADER_LINES]:
        prio[ln.i] = min(prio.get(ln.i, 9), 5)
    parts = dict(lines=lines, text=text, bl=bl, ms=ms, rs=rs, ic=ic, il=il)
    return prio, reach, parts


def cut(notice, prio, cap):
    keep = sorted(prio, key=lambda i: (prio[i], i))[:cap]
    return [notice.lines[i] for i in sorted(keep)]


# ======================================================================================================= model step
SYSTEM = ('너는 공공조달 입찰공고문을 읽고 사실만 옮겨 적는 판독기다. 아래 발췌만 근거로 JSON을 채운다. 위반 여부는 판단하지 않는다.\n'
          '판단 규칙:\n'
          '1. budget: 공고문이 밝힌 이 공고 전체의 금액(사업예산·예산액·기초금액·추정가격·사업금액)을 각각 한 항목으로 적는다. amount는 그 금액을 '
          '적힌 그대로 복사한다(예: 금37,930,000원, 300,000천원). 부가가치세 포함 여부를 vat에 적는다. 금액이 금차분·당해연도분이면 scope=금차분, '
          '단가계약의 단가이면 scope=단가, 품목별·학교별·구역별 부분 금액이면 scope=항목별, 공고 전체 총액이면 scope=총액. 이행실적 기준금액·보증금·'
          '수수료·적격심사 기준 금액대는 적지 않는다.\n'
          '2. contract_method: 공고문이 자기 계약방법으로 밝힌 것 하나. 수의계약·소액수의·견적제출 → 수의계약, 제한경쟁·제한(총액)입찰·지역/업종 제한입찰 '
          '→ 제한경쟁, 지명경쟁 → 지명경쟁, 일반경쟁·일반(총액)입찰 → 일반경쟁. 여러 표현이 함께 있으면 수의계약 > 제한경쟁 > 지명경쟁 > 일반경쟁 순으로 '
          '고른다. 낙찰방법(적격심사·협상에 의한 계약·규격가격동시입찰)은 계약방법이 아니다. 나라장터 검색 안내와 유찰 시 절차 문장은 무시한다. 밝힌 것이 없으면 없음.\n'
          '3. participation_restriction: 입찰참가자격에 본점·주된 영업소 소재지 제한이 있으면 region=있음, 전국·지역제한 없음을 명시하면 전국명시. 특정 '
          '업종 등록·면허·허가를 요구하면 industry=있음. 중소기업·소기업·소상공인 확인서나 직접생산확인증명서를 요구하면 sme=있음.\n'
          '4. region: 소재지 제한이 가리키는 광역 시·도를 모두 적는다(시·군·구 단위 제한은 그 시·도로, [지역:…|광역=X] 표식은 X로, 수요기관 관내 제한은 '
          '수요기관관내). 납품장소·행사장소·주소는 적지 않는다.\n'
          '5. industry: 참가자격으로 요구한 업종의 4자리 업종코드와 업종명을 적는다. 세부품명번호(8~10자리)와 등록 면제 문장은 제외한다.\n'
          'quote는 근거가 적힌 발췌 줄의 문자열을 그대로 한 줄 복사한다(L번호와 앞의 표식은 빼고). 근거가 없으면 quote는 빈 문자열, 모르면 불명.')


def messages(b, cands):
    work = b.notice.meta.get('업무구분')
    title = (b.titles[0] if b.titles else '')[:80]
    info = ' / '.join(x for x in (f'업무구분: {work}' if work else '', f'사업명: {title}' if title else '') if x)
    user = f'[공고 정보] {info}\n[발췌]\n{F.excerpt(b.notice, cands, 0)}'
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}]


def schema():
    def s(n):
        return {'type': 'string', 'maxLength': n}

    def e(*values):
        return {'type': 'string', 'enum': list(values)}

    def obj(props):
        return {'type': 'object', 'additionalProperties': False, 'required': list(props), 'properties': props}
    item = obj({'kind': e('사업예산', '기초금액', '추정가격', '사업금액', '기타', '불명'), 'amount': s(60), 'vat': e('포함', '미포함', '불명'),
                'scope': e('총액', '금차분', '단가', '항목별', '불명'), 'quote': s(400)})
    return obj({
        'budget': obj({'stated': e('있음', '없음', '불명'), 'items': {'type': 'array', 'maxItems': 6, 'items': item}}),
        'contract_method': obj({'stated': e('일반경쟁', '제한경쟁', '지명경쟁', '수의계약', '없음', '불명'), 'quote': s(400)}),
        'participation_restriction': obj({'region': e('있음', '없음', '전국명시', '불명'), 'industry': e('있음', '없음', '불명'),
                                          'sme': e('있음', '없음', '불명')}),
        'region': obj({'regions': {'type': 'array', 'maxItems': 17, 'items': e(*SIDO_NAMES, '수요기관관내')}, 'quote': s(400)}),
        'industry': obj({'codes': {'type': 'array', 'maxItems': 8, 'items': s(4)},
                         'names': {'type': 'array', 'maxItems': 8, 'items': s(40)}, 'quote': s(400)}),
    })


def request(engine, b, k, request_cls):
    prio, reach, _ = select(b)
    if not reach or not prio:
        return None
    cap = min(CAP, len(prio))
    while True:
        cands = cut(b.notice, prio, cap)
        ids = engine.token_ids(messages(b, cands))
        if len(ids) + MAX_TOKENS <= min(PROMPT_TOKEN_LIMIT, engine.max_model_len - 64):
            return request_cls(k, FAM, cands, (), ids, schema(), MAX_TOKENS, 0)
        if cap <= 1:
            return None
        cap = max(1, int(cap * 0.75)) if cap > MIN_CAP else cap - 1


# ========================================================================================================== consume
ENUMS = {('budget', 'stated'): ('있음', '없음', '불명'),
         ('contract_method', 'stated'): ('일반경쟁', '제한경쟁', '지명경쟁', '수의계약', '없음', '불명'),
         ('participation_restriction', 'region'): ('있음', '없음', '전국명시', '불명'),
         ('participation_restriction', 'industry'): ('있음', '없음', '불명'),
         ('participation_restriction', 'sme'): ('있음', '없음', '불명')}
ITEM_ENUMS = {'kind': ('사업예산', '기초금액', '추정가격', '사업금액', '기타', '불명'), 'vat': ('포함', '미포함', '불명'),
              'scope': ('총액', '금차분', '단가', '항목별', '불명')}


def ground(cands, quote):
    """The shown line the quote was copied from: an exact substring of the shown or the original line text, else None."""
    q = (quote or '').strip()
    if len(q) < 2:
        return None
    for ln in cands:
        if q in F.shown(ln.text) or q in ln.text:
            return ln
    return None


def valid(obj):
    if not isinstance(obj, dict):
        return False
    for (sec, key), values in ENUMS.items():
        if not isinstance(obj.get(sec), dict) or obj[sec].get(key) not in values:
            return False
    for sec in ('contract_method', 'region', 'industry'):
        if not isinstance(obj.get(sec), dict) or not isinstance(obj[sec].get('quote'), str):
            return False    # a malformed section is an invalid reading (retry), never an exception
    items = obj['budget'].get('items')
    if not isinstance(items, list):
        return False
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get('amount'), str) or not isinstance(it.get('quote'), str):
            return False
        if any(it.get(k) not in v for k, v in ITEM_ENUMS.items()):
            return False
    if not isinstance(obj['region'].get('regions'), list) or not all(isinstance(r, str) for r in obj['region']['regions']):
        return False
    if not all(isinstance(obj['industry'].get(k), list) and all(isinstance(x, str) for x in obj['industry'][k]) for k in ('codes', 'names')):
        return False
    return True


def consume(b, cands, text):
    """Parse the JSON, validate it, keep only quotes found in the shown lines, and store the reading as b.d_input_mismatch."""
    try:
        obj = json.loads(text.split('<channel|>', 1)[-1])
    except (ValueError, AttributeError):
        return False
    if not valid(obj):
        return False
    items = []
    for it in obj['budget']['items'][:6]:
        ln = ground(cands, it['quote'])
        vals = amounts_in(it['amount'])
        stated = amounts_in(it['quote'])
        if ln is None or not vals or not any(abs(vals[0] - a) <= max(1, 1e-6 * vals[0]) for a in stated):
            continue    # the amount must be written in its own quote
        if VARIABLE_AMOUNT.search(it['quote']):
            continue    # the notice says the amount varies (per days of use etc.), so it is not the contract amount
        if any(abs(vals[0] - a) <= max(1, 1e-6 * vals[0]) for a in band_values(it['quote'])):
            continue    # the quote states this amount as a band threshold (…원 미만/이상), not as the notice's price
        items.append((ln, it['kind'], vals[0], it['vat'], it['scope']))
    pr = obj['participation_restriction']
    sido = {regions.canon_sido(r) or r for r in obj['region']['regions'] if r != '수요기관관내'}
    b.d_input_mismatch = {
        'budget': items,
        'contract_method': (obj['contract_method']['stated'], ground(cands, obj['contract_method']['quote'])),
        'restriction': {'region': pr['region'], 'industry': pr['industry'], 'sme': pr['sme']},
        'region': (sido, '수요기관관내' in obj['region']['regions'], ground(cands, obj['region']['quote'])),
        'industry': ({c.strip() for c in obj['industry']['codes'] if re.fullmatch(r'\d{4}', c.strip())},
                     [n.strip() for n in obj['industry']['names'] if n.strip()], ground(cands, obj['industry']['quote'])),
        'source': 'model',
    }
    return True


# ========================================================================================================= stand-in

def standin(b):
    """The same reading shape from the CPU regexes (used when the model gave none)."""
    _, _, parts = select(b)
    unit = bool(UNIT_PRICE.search(parts['text']))
    items = []
    for ln, am, kw, cell in parts['bl']:
        s = ln.text.strip()
        kind = HEADLINE_KIND.get(kw, '사업예산' if ('예산' in kw or kw in ('총사업비', '사업비', '사업예정총액')) else '기타')
        scope = '단가' if unit else ('금차분' if PARTIAL.search(s) else ('항목별' if (cell or re.search(re.escape(kw) + r'\s*\(', s)) else '총액'))
        vat = '미포함' if VAT_EXCL.search(s) else ('포함' if VAT_INCL.search(s) else '불명')
        est = EST_LABELLED.search(s)
        if est:
            items.append((ln, '추정가격', float(est.group(1).replace(',', '')), '미포함', scope))
        items.append((ln, kind, max(am), vat, scope))
    stated, mline = '없음', None
    for pref in ('수의계약', '제한경쟁', '지명경쟁', '일반경쟁'):
        for ln, cats in parts['ms']:
            if pref in cats:
                stated, mline = pref, ln
                break
        if mline is not None:
            break
    sido, local, nationwide, rline = set(), False, False, None
    for ln, window, s, loc, nr in parts['rs']:
        if nr:
            nationwide = True
            rline = rline or ln
            continue
        sido |= s
        local |= loc
        if rline is None:
            rline = next((w for w in window if regions.mentions(w.text)['sido'] or LOCAL_CUE.search(w.text)), ln)
    ic = parts['ic']
    iline = next(iter(ic.values()), None) or (parts['il'][0] if parts['il'] else None)
    return {
        'budget': items,
        'contract_method': (stated, mline),
        'restriction': {'region': '전국명시' if nationwide and not sido else ('있음' if (sido or local) else '없음'),
                        'industry': '있음' if (ic or parts['il']) else '없음',
                        'sme': '있음' if SME_CUE.search(parts['text']) else '없음'},
        'region': (sido, local, rline),
        'industry': (set(ic), [], iline),
        'source': 'cpu',
    }


# ========================================================================================================== decision

def decide(b, r):
    """The evidence Line of the first axis that fires (예산 → 계약방법 → 지역 → 업종), else None. The 조항호내용 band is
    not compared: it is outside v24 (organizer Q&A boundary)."""
    meta = b.meta
    B, P = registered_amounts(b)
    unit = any(scope == '단가' for *_, scope in r['budget']) or bool(UNIT_PRICE.search(select(b)[2]['text']))
    totals = [it for it in r['budget'] if it[4] in ('총액', '불명') and it[2] >= 1e5]
    # 예산: a stated total equals neither registered amount (1 % rounding, VAT relation and item sums allowed)
    if totals and not unit and (B or P) and not amount_consistent(B, P, [it[2] for it in totals]):
        return totals[0][0]
    # 계약방법 (substance: 나라장터 일반경쟁 vs any other statement fires; 제한경쟁 vs 일반경쟁 only without a body restriction)
    stated, mline = r['contract_method']
    pr = r['restriction']
    m = meta.method
    if mline is not None and stated not in ('없음', '불명') and m and stated != m:
        no_restriction = all(pr[k] == '없음' for k in ('region', 'industry', 'sme'))
        if m == '일반경쟁':
            return mline
        if m == '제한경쟁' and stated == '일반경쟁' and no_restriction:
            return mline
        if m == '수의계약' and stated in ('일반경쟁', '제한경쟁', '지명경쟁') and meta.award != '소액수의견적' \
                and not any(QUOTE_CUE.search(ln.text) for ln in notice_lines(b.notice)):
            return mline
        if m == '지명경쟁' and stated in ('일반경쟁', '수의계약'):
            return mline
    # 지역제한: only when 나라장터 registers one (organizer ruling); disjoint 시·도 sets or an explicit nationwide statement
    sido, local, rline = r['region']
    mreg = set(meta.region_sido)
    if meta.region_flag == 'Y' and mreg and rline is not None:
        if pr['region'] == '전국명시' and not sido:
            return rline
        if sido and not (sido & mreg):
            return rline
        if REGION_SUPERSET and sido > mreg:
            return rline
    # 업종: only when 나라장터 registers one; disjoint codes, or names sharing no content token with the registered 업종
    codes, names, iline = r['industry']
    mc = meta_codes(meta)
    if meta.license_flag == 'Y' and mc and iline is not None:
        if codes and not (codes & mc):
            return iline
        if not codes and names:
            toks = set().union(*[name_tokens(n) for n in names])
            if toks and not (toks & meta_name_tokens(meta)) and not (toks & name_tokens(re.sub(r'[^가-힣]', ' ', str(meta.license)))):
                return iline
    return None


def verdict(b):
    """Fires only from a model reading. The CPU stand-in stays for diagnostics: it read threshold bands as budgets."""
    reading = getattr(b, 'd_input_mismatch', None)
    return decide(b, reading) if reading is not None else None
