"""v24 (공고서와 나라장터 입력값 상이): more places and shapes in which a notice states the four compared values.

Each rule is its own switch (all False = current behaviour) and only runs when every other v24 rule is silent:
- V24_TAG_ANY: every method/band tag in the 공고문 head, in any bracket or separator shape ("(제한경쟁·1억원미만)",
  "(일반경쟁, 1억원 미만)", "[제한경쟁·3억원미만]", "(제한경쟁·1억미만)"), not only the first strict one. A tag names the
  contract method and the amount band of the bid; a tag with another method than 나라장터 계약방법, or a band that neither
  나라장터 amount (추정가격, 배정예산) falls in, disagrees with 나라장터.
- V24_AMOUNT_SENTENCE: a budget or 추정가격 stated in a sentence ("본 사업의 소요예산은 금 X원(부가가치세 포함)입니다",
  "추정가격은 X원(부가가치세 별도)이며"). When no such statement agrees with 나라장터 (equal, rounded or a VAT relation) and one
  lies within half to double of its field's registered amount (and is no 1/k share), the notice states another budget.
- V24_BASE_VERTICAL: a 기초금액 written as a vertical table (the label alone on its line, the amount opening the next line),
  judged as V24_BASE_ZONE judges an inline 기초금액 (silent when any 기초금액 statement lies within 5% of a registered amount).
- V24_ATTACH_LICENCE: an attachment line outside the qualification section that states the licence the bidder must have
  registered ("…학술연구용역(업종코드: 1169)으로 입찰참가자격을 등록한 자") with codes none of which 나라장터 업종 registers.
- V24_ATTACH_REGION: an attachment line restricting the bidder's seat (본점·주된 영업소 소재지) to 시·도 none of which 나라장터
  제한지역 registers.
- V24_REGION_STATED: a 공고문 region statement ("지역제한(경상남도)", "지역제한 : 경상남도"), any 공고문 qualification clause on
  the bidder's seat, or a seat clause elsewhere in the 공고문 that limits bidders ("…에 본점을 둔 업체로 참가를 제한"), naming
  시·도 none of which 나라장터 제한지역 registers, also when another clause agrees.
- V24_BASIC_SUPERSET: 나라장터 registers 시·군 tokens only and a 공고문 qualification clause on the bidder's seat names a
  registered token and another one ("[지역:r1…] 또는 [지역:r10…]에 둔 자"): the notice admits bidders 나라장터 excludes.
- V24_LICENCE_FIELD: a 공고문 line outside the qualification section labelled as the licence field ("업종 :", "등록업종 :",
  "입찰참가 등록업종 : …[1169]"), or stating the licence the bidder must have registered ("…(1253)의 업종으로 입찰참가자격을
  등록한 자") outside the joint-contract section, whose codes share none with 나라장터 업종.
- V24_AMOUNT_LATE: labelled budget or 추정가격 statements past the first 150 공고문 lines (유의사항, notes), judged as the
  budget readers judge the first 150 lines, when none of them agrees with 나라장터.
Partner, address, facility and no-restriction wording stay out as in the region and licence axes.
"""
import re

from . import families, regions, switches

# ------------------------------------------------------------------------------------------------ V24_TAG_ANY
TAG_ANY = re.compile(r'[\(\[【（〔]\s*(수의계약|제한경쟁|일반경쟁|지명경쟁)\s*(?:입\s*찰)?\s*[·ㆍ・,/]?\s*'
                     r'(\d+(?:\.\d+)?)\s*(천만|억|만)\s*원?\s*(미만|이상|이하|초과)\s*[\)\]】）〕]')
TAG_UNIT = {'천만': 1e7, '억': 1e8, '만': 1e4}


def tag_contradicts(b, method, num, unit, side):
    from . import judge as J
    if b.meta.method and method != b.meta.method:
        return True
    vals = [v for v in (b.meta.P, b.meta.B) if v]
    if not vals:
        return False
    edge = float(num) * TAG_UNIT[unit]
    oks = []
    for v in vals:
        ok = J.band_ok(v, f'{num}{unit}원', side) if side in ('미만', '이상') else None
        if ok is None:
            ok = v < edge if side == '미만' else v >= edge if side == '이상' else v <= edge if side == '이하' else v > edge
        oks.append(ok)
    return not any(oks)


def tag_any(b):
    for ln in b.notice.notice_lines()[:80]:
        for m in TAG_ANY.finditer(ln.text):
            if tag_contradicts(b, m.group(1), m.group(2), m.group(3), m.group(4)):
                return ln
    return None


# ------------------------------------------------------------------------------------------------ V24_AMOUNT_SENTENCE
SENT_LABEL = re.compile(r'(추\s*정\s*가\s*격|배\s*정\s*예\s*산|사\s*업\s*예\s*산|소\s*요\s*예\s*산|예\s*산\s*액|예\s*산\s*금\s*액|총\s*사\s*업\s*비|사\s*업\s*비'
                        r'|용\s*역\s*예\s*산|과\s*업\s*예\s*산|사\s*업\s*금\s*액|용\s*역\s*금\s*액|총\s*예\s*산|(?<![가-힣])예\s*산)'
                        r'\s*(?:[\(（][^)）]{0,14}[\)）])?\s*(?:은|는|이|가|으로|로)?\s*(?:총\s*(?:액\s*)?)?[:：]?\s*(?:금\s*)?[₩￦]?\s*'
                        r'(\d{1,3}(?:,\d{3})+|\d{6,})\s*원')
SENT_BAND = re.compile(r'^\s*(?:[\(（][^)）]{0,20}[\)）]\s*)?(?:이\s*상|이\s*하|미\s*만|초\s*과|이\s*내|범\s*위|한\s*도|내\s*외|정\s*도|수\s*준|까\s*지)')


def amount_sentence(b):
    from . import judge as J
    P, B = b.meta.P, b.meta.B
    if not (P or B):
        return None
    found = []
    for ln in b.notice.notice_lines()[:150]:
        t = ln.text
        if '단가' in t or J.UNIT_CUE.search(t) or J.BREAKDOWN.search(t):
            continue
        for m in SENT_LABEL.finditer(t):
            if SENT_BAND.match(t[m.end():]):
                continue
            v = float(m.group(2).replace(',', ''))
            if v >= 1e5:
                found.append((ln, v, bool(J.ESTIMATE_FIELD.search(m.group(1)))))
    if not found or any(J.agrees(v, P) or J.agrees(v, B) or any(J.vat_related(v, r) for r in (P, B) if r) for _, v, _ in found):
        return None
    for ln, v, est in found:
        ref = (P if est else B) or P or B
        if ref and 0.5 <= v / ref <= 2 and not any(abs(r / v - k) <= 0.005 * k for r in (P, B) if r for k in (2, 3, 4, 5)):
            return ln
    return None


# ------------------------------------------------------------------------------------------------ V24_BASE_VERTICAL
BASE_LABEL_ONLY = re.compile(r'^\W*기\s*초\s*금\s*액\s*(?:[\(（][^)）]{0,12}[\)）])?\W*$')
BASE_VALUE_LEAD = re.compile(r'^\s*(?:금\s*)?[₩￦\\]?\s*(\d{1,3}(?:,\d{3})+|\d{6,})\s*원?(?![\d,])')


def base_vertical(b):
    from . import judge as J
    B, P = b.meta.B, (b.meta.P if b.meta.P_source == 'meta' else None)
    refs = [r for r in (B, P * 1.1 if P else None, P, B / 1.1 if B else None) if r]
    if not refs or any(J.UNIT_CONTRACT.search(t) for t in getattr(b, 'titles', ())[:3]):
        return None
    lines = b.notice.notice_lines()
    ratios, vertical = [], []
    for k, ln in enumerate(lines):
        for m in J.BASE_AMOUNT_L.finditer(ln.text):
            v = float(m.group(1).replace(',', ''))
            if v >= 1e5:
                ratios.append(min((v / r for r in refs), key=lambda x: abs(x - 1)))
        if not BASE_LABEL_ONLY.match(ln.text):
            continue
        j = next((j for j in range(k + 1, min(k + 4, len(lines))) if lines[j].text.strip()), None)
        if j is None or lines[j].doc != ln.doc:
            continue
        m = BASE_VALUE_LEAD.match(lines[j].text)
        if not m:
            continue
        v = float(m.group(1).replace(',', ''))
        if v < 1e5:
            continue
        r = min((v / x for x in refs), key=lambda x: abs(x - 1))
        ratios.append(r)
        vertical.append((lines[j], r, bool(J.UNIT_CUE.search(lines[j].text)) or J.unit_note(lines, j)))
    if not vertical or any(abs(r - 1) <= 0.05 for r in ratios):
        return None
    for ln, r, unit in vertical:
        if not unit and (0.5 <= r < 0.95 or 1.05 < r <= 2):
            return ln
    return None


# ------------------------------------------------------------------------------------------------ V24_ATTACH_LICENCE
LIC_REG_CUE = re.compile(r'(입\s*찰\s*)?참\s*가\s*(자\s*격\s*)?(을\s*|으로\s*)?등\s*록\s*(한|된|하여야|되어\s*있는)'
                         r'|(으로|로)\s*(입\s*찰\s*)?(참\s*가\s*)?등\s*록\s*(한|된)\s*(자|업\s*체)|(을|를)\s*등\s*록\s*한\s*(자|업\s*체)')


def attach_licence(b):
    from . import judge as J
    if b.meta.license_flag != 'Y' or not b.meta.license:
        return None
    meta_codes = set(re.findall(r'\((\d{4})\)', str(b.meta.license)))
    if not meta_codes:
        return None
    for ln in b.notice.lines:
        if ln.doc_type == '공고문' or ln.sec == 'QUAL' or not LIC_REG_CUE.search(ln.text):
            continue
        t = J.clause_text(b.notice, ln)
        if J.LICENSE_GUIDE.search(t) or J.LICENSE_ALT.search(t) or J.JV_PARTNER3.search(t) or J.PARTNER_WORK.search(t) \
                or J.alternative_item(b.notice, ln):
            continue
        codes = set(J.CODE_LABELLED.findall(t)) | set(J.CODE_AFTER_INDUSTRY.findall(t)) | J.wide_license_codes(t) \
            | {x for g in J.LICENSE_CODE.findall(t) for x in g if x}
        if codes and not (codes & meta_codes):
            return ln
    return None


# ------------------------------------------------------------------------------------------------ V24_ATTACH_REGION / V24_REGION_STATED
SEAT = re.compile(r'(본\s*점|주\s*된\s*(영\s*업\s*소|사\s*무\s*소))\s*(의\s*)?(소\s*재\s*지?)?\s*(\([^)]{0,20}\)\s*)?(가|를|이|는)?')
REGION_FIELD = re.compile(r'지\s*역\s*제\s*한\s*(?:경\s*쟁\s*|견\s*적\s*)?(?:입\s*찰\s*)?(?:[\(（]\s*([^)）]{2,60})[\)）]|\s*[:：|]\s*([^|,.\n]{2,60}))'
                          r'|제\s*한\s*지\s*역\s*[:：]\s*([^|,.)）\n]{2,60})')
REGION_FIELD_NONE = re.compile(r'없\s*음|해\s*당\s*없|미\s*적\s*용|전\s*국')
SEAT_LIMIT = re.compile(r'제\s*한|한\s*함|한\s*정|에\s*한\s*하|업\s*체\s*(로|만|이어야|여야)|자\s*(로|만|이어야|여야)|둔\s*(자|업\s*체)|있\s*는\s*(자|업\s*체|사\s*업\s*자)')


def seat_clause_disjoint(b, ln, meta):
    from . import judge as J
    if not SEAT.search(ln.text) or not families.BIDDER_LOC.search(ln.text):
        return False
    t = J.region_clause_text(b.notice, ln)
    if J.REGION_NONE.search(t) or J.JV_PARTNER3.search(t) or J.PARTNER_WORK.search(t) or J.BASIC_ADDRESS.search(ln.text) \
            or J.ORDERER_TOKEN.search(t):
        return False
    s = regions.mentions(ln.text)['sido']
    return bool(s) and not (s & meta)


def attach_region(b):
    if b.meta.region_flag != 'Y' or not b.meta.region_sido:
        return None
    meta = set(b.meta.region_sido)
    for ln in b.notice.lines:
        if ln.doc_type != '공고문' and seat_clause_disjoint(b, ln, meta):
            return ln
    return None


def region_stated(b):
    from . import judge as J
    if b.meta.region_flag != 'Y' or not b.meta.region_sido:
        return None
    meta = set(b.meta.region_sido)
    for ln in b.notice.notice_lines()[:150]:
        for m in REGION_FIELD.finditer(ln.text):
            val = m.group(1) or m.group(2) or m.group(3) or ''
            if J.REGION_NONE.search(val) or REGION_FIELD_NONE.search(val):
                continue
            s = regions.mentions(val)['sido']
            if s and not (s & meta):
                return ln
    for ln in b.notice.lines:
        if ln.doc_type == '공고문' and J.qual_section(ln, b.notice) and seat_clause_disjoint(b, ln, meta):
            return ln
    for ln in b.notice.notice_lines():
        if not J.qual_section(ln, b.notice) and SEAT_LIMIT.search(ln.text) and seat_clause_disjoint(b, ln, meta):
            return ln
    return None


# ------------------------------------------------------------------------------------------------ V24_BASIC_SUPERSET
def basic_superset(b):
    from . import judge as J
    if b.meta.region_flag != 'Y':
        return None
    raw = str(b.notice.meta.get('제한지역코드목록') or '')
    entries = [e.strip() for e in raw.split(',') if e.strip()]
    if not entries or not all(J.BASIC_TOKEN.fullmatch(e) for e in entries):
        return None
    meta_ids = set(J.BASIC_TOKEN.findall(raw))
    for ln in b.notice.lines:
        if ln.doc_type != '공고문' or not J.qual_section(ln, b.notice) or not J.BASIC_CUE.search(ln.text):
            continue
        t = J.region_clause_text(b.notice, ln)
        if J.ORDERER_TOKEN.search(t) or J.REGION_NONE.search(t) or J.JV_PARTNER3.search(t) or J.BASIC_ADDRESS.search(ln.text):
            continue
        ids = set(J.BASIC_TOKEN.findall(ln.text))
        if ids & meta_ids and ids - meta_ids:
            return ln
    return None


# ------------------------------------------------------------------------------------------------ V24_LICENCE_FIELD
LIC_FIELD = re.compile(r'(?:등\s*록\s*|참\s*가\s*|입\s*찰\s*)?업\s*종\s*(?:명|제\s*한)?\s*[:：|]\s*([^\n]{2,80})')
CODE_ANY = re.compile(r'[\(\[【]\s*(?:업\s*종\s*코\s*드\s*[:：]?\s*)?(\d{4})(?!\d)\s*[\)\]】]')


def licence_field(b):
    from . import judge as J
    if b.meta.license_flag != 'Y' or not b.meta.license:
        return None
    meta_codes = set(re.findall(r'\((\d{4})\)', str(b.meta.license)))
    if not meta_codes:
        return None
    for ln in b.notice.notice_lines():
        if ln.sec == 'QUAL':
            continue
        m = LIC_FIELD.search(ln.text)
        if not m:
            continue
        t = J.clause_text(b.notice, ln)
        if J.LICENSE_GUIDE.search(t) or J.LICENSE_ALT.search(t) or J.JV_PARTNER3.search(t) or J.PARTNER_WORK.search(t):
            continue
        codes = set(CODE_ANY.findall(m.group(1))) | J.wide_license_codes(m.group(1))
        if codes and not (codes & meta_codes):
            return ln
    # a registration statement outside the qualification and joint-contract sections ("…(1253)의 업종으로 입찰참가자격을
    # 등록한 자"), read as V24_ATTACH_LICENCE reads attachment lines
    for ln in b.notice.notice_lines():
        if ln.sec in ('QUAL', 'JV') or not LIC_REG_CUE.search(ln.text):
            continue
        t = J.clause_text(b.notice, ln)
        if J.LICENSE_GUIDE.search(t) or J.LICENSE_ALT.search(t) or J.JV_PARTNER3.search(t) or J.PARTNER_WORK.search(t) \
                or J.alternative_item(b.notice, ln):
            continue
        codes = set(J.CODE_LABELLED.findall(t)) | set(J.CODE_AFTER_INDUSTRY.findall(t)) | J.wide_license_codes(t) \
            | {x for g in J.LICENSE_CODE.findall(t) for x in g if x}
        if codes and not (codes & meta_codes):
            return ln
    return None


# ------------------------------------------------------------------------------------------------ V24_AMOUNT_LATE
def amount_late(b):
    from . import judge as J
    P, B = b.meta.P, b.meta.B
    nl = b.notice.notice_lines()
    if not (P or B) or len(nl) <= 150:
        return None
    late = []
    for ln in nl[150:]:
        t = ln.text
        if '단가' in t:
            continue
        for m in J.AMOUNT_FIELD_L.finditer(t):
            a = J.AMOUNT_AFTER_LABEL_L.match(t[m.end():])
            if a and not J.OTHER_AMOUNT_FIELD.search(t[m.end():m.end() + a.start(1)]):
                late.append((ln, float(a.group(1).replace(',', '')), bool(J.ESTIMATE_FIELD.fullmatch(m.group(1)))))
    if not late or any(J.agrees(v, P) or J.agrees(v, B) or any(J.vat_related(v, r) for r in (P, B) if r) for _, v, _ in late):
        return None
    for ln, v, est in late:
        if v < 1e5 or J.BREAKDOWN.search(ln.text) or J.UNIT_CUE.search(ln.text):
            continue
        ref = (P if est else B) or P or B
        if ref and 0.5 <= v / ref <= 2 and not any(abs(r / v - k) <= 0.005 * k for r in (P, B) if r for k in (2, 3, 4, 5)):
            return ln
    return None


RULES = (('V24_TAG_ANY', tag_any), ('V24_AMOUNT_SENTENCE', amount_sentence), ('V24_BASE_VERTICAL', base_vertical),
         ('V24_ATTACH_LICENCE', attach_licence), ('V24_ATTACH_REGION', attach_region), ('V24_REGION_STATED', region_stated),
         ('V24_BASIC_SUPERSET', basic_superset), ('V24_LICENCE_FIELD', licence_field), ('V24_AMOUNT_LATE', amount_late))


def extra(b):
    """The evidence line of the first switched-on rule that fires, else None."""
    for name, rule in RULES:
        if getattr(switches, name, False):
            ln = rule(b)
            if ln is not None:
                return ln
    return None
