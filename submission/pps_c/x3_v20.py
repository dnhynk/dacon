"""Round-6 v20 statement check (switch X3_V20_NEEDS_BASIS), consulted by judge.v20 and the v20 stage's band status.

지침 제3조②: the notice or RFP states "대기업 참여제한 하한제도 적용 여부(적용 근거 포함)"; the organizer (talkboard 9/28)
repeats "적용 근거 포함". With the switch, a statement line counts only when its clause, or the heading line just above it,
names that basis: the 소프트웨어 (산업) 진흥법, its 제48조, the 중소 소프트웨어사업자의 사업 참여 지원에 관한 지침 or the
과학기술정보통신부 고시 that issues it. "대기업인 소프트웨어사업자는 참여할 수 없음" alone states no basis.
"""
import re

from . import switches

SW_BASIS = re.compile(r'(소\s*프\s*트\s*웨\s*어|S\s*/?\s*W)\s*(산\s*업\s*)?진\s*흥\s*법|제\s*48\s*조'
                      r'|중\s*소\s*소\s*프\s*트\s*웨\s*어\s*사\s*업\s*자\s*의\s*사\s*업\s*참\s*여\s*지\s*원'
                      r'|과\s*학\s*기\s*술\s*정\s*보\s*통\s*신\s*부\s*(장\s*관\s*)?고\s*시|과\s*기\s*정\s*통\s*부\s*(장\s*관\s*)?고\s*시'
                      r'|대\s*기\s*업\s*인\s*(소\s*프\s*트\s*웨\s*어|S\s*/?\s*W)\s*사\s*업\s*자\s*가\s*참\s*여\s*할\s*수\s*있\s*는\s*사\s*업\s*금\s*액', re.I)


NL = chr(10)


def on():
    return bool(getattr(switches, 'X3_V20_NEEDS_BASIS', False))


def _J():
    from . import judge
    return judge


def basis_ok(notice, ln):
    """The statement line's clause (wrapped list item) or the nearest non-empty line above it names the basis."""
    J = _J()
    if SW_BASIS.search(J.clause_text(notice, ln)):
        return True
    for x in reversed(notice.lines[max(0, ln.i - 3):ln.i]):
        if x.doc != ln.doc:
            break
        if x.text.strip():
            return bool(SW_BASIS.search(x.text))
    return False


def sw_statement(b):
    """judge.sw_statement with every statement line (model-read or matched) needing its basis."""
    J = _J()
    for ln in J.lines_where(b, 'sw', 표기='대기업 참여제한 여부·근거 기재'):
        if basis_ok(b.notice, ln):
            return True
    for ln in b.notice.lines:
        t = ln.text
        pat = J.SW_STATEMENT3 if switches.AUDIT_FIXES3 or switches.V20_SCOPE or switches.V20_CONTENT else J.SW_STATEMENT
        if (J.sw_statement_match(pat, t) if switches.V20_STATEMENT_STRICT else pat.search(t)) \
                and not (J.CROSS_ONLY.search(t) and not re.search(r'대기업|중견|중소\s*소프트웨어|하한|금액', t)) \
                and basis_ok(b.notice, ln):
            return True
    return False


def band_status(text):
    """dedicated.sw_participation.band_status, where a match is a statement only when the text from the line above it to
    the end of its window names the basis (SW_BASIS); otherwise it is a citation for the model to read."""
    from .dedicated import sw_participation as swp
    status = 'absent'
    for m in swp.BAND.finditer(text):
        lo = text.rfind(NL, 0, m.start()) + 1
        hi = text.find(NL, m.end())
        hi = len(text) if hi < 0 else text.find(NL, hi + 1)
        win = text[lo:min(len(text) if hi < 0 else hi, m.end() + 160)]
        if swp.SOJA.search(win) and not swp.BAND_MARK.search(win):
            continue
        prev = text.rfind(NL, 0, max(0, lo - 1)) + 1
        based = bool(SW_BASIS.search(text[prev:lo] + win))
        if switches.V20_STAGE_CITATION and swp.CITE_ALT.fullmatch(m.group(0)):
            if swp.citation_statement(text, m) and based:
                return 'statement'
            status = 'citation'
            continue
        if swp.STATEMENT_VERB.search(win) and based:
            return 'statement'
        status = 'citation'
    return status


# X3_V20_BAND_MORE (항목표 v20 비고: the restriction differs by 사업금액 band, 20·40·80억; 지침 제3조① 3: 사업금액 = 추정가격 +
# VAT): a band statement written as "N억원 이하", in Korean numerals ("이십억원 미만"), in won ("2,000,000,000원 미만") or as
# the open top band ("80억원 이상") names a band too; when every band line of the notice misfits the 사업금액, the notice
# states another project's restriction. The judge's V20_BAND_MISMATCH reads "N억 미만" on the main path only; this also
# serves the v20 stage.
KOR_N = {'이십': 20, '사십': 40, '팔십': 80}
N_ = r'(\d{2}|이\s*십|사\s*십|팔\s*십)'
BAND_LT = re.compile(r'(?<![\d.,])' + N_ + r'\s*억\s*원?\s*(이\s*상\s*' + N_ + r'\s*억\s*원?\s*)?(미\s*만|이\s*하)')
BAND_GE = re.compile(r'(?<![\d.,])' + N_ + r'\s*억\s*원?\s*이\s*상(?!\s*' + N_ + r'\s*억)')
WON = re.compile(r'(?<![\d,])([248]),000,000,000\s*원\s*(이\s*상\s*([48]),000,000,000\s*원\s*)?(미\s*만|이\s*하|이\s*상)')
BAND_CTX = re.compile(r'대\s*기\s*업|중\s*견|하\s*한|제\s*48\s*조|참\s*여\s*지\s*원')
BAND_SKIP = re.compile(r'연\s*간|경\s*우|이\s*면|인\s*때|구\s*간|각\s*각|\|')


def _n(s):
    s = re.sub(r'\s+', '', s)
    return KOR_N.get(s, int(s) if s.isdigit() else None)


def line_bands(t):
    """[(lo, hi)] in 억원 for each band expression of a line (20·40·80 only)."""
    out = []
    for m in BAND_LT.finditer(t):
        a, b = _n(m.group(1)), _n(m.group(3)) if m.group(3) else None
        if a not in (20, 40, 80) or m.group(3) and b not in (40, 80):
            continue
        incl = 1e-6 if re.sub(r'\s+', '', m.group(4)) == '이하' else 0      # "N억 이하" includes N
        out.append((a, b + incl) if b else (0, a + incl))
    for m in BAND_GE.finditer(t):
        if _n(m.group(1)) == 80:
            out.append((80, 10 ** 9))
    for m in WON.finditer(t):
        a = int(m.group(1)) * 10
        if m.group(3):
            out.append((a, int(m.group(3)) * 10))
        elif re.sub(r'\s+', '', m.group(4)) == '이상':
            if a == 80:
                out.append((80, 10 ** 9))
        else:
            out.append((0, a))
    return out


def amount_of(notice):
    def money(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f > 0 else None
    B, P = money(notice.meta.get('배정예산금액')), money(notice.meta.get('입찰추정가격'))
    return B or (P * 1.1 if P else None)


def band_misfit(notice, amount):
    """Some line names exactly one 지침 band and no band line fits the 사업금액."""
    if not amount:
        return False
    seen = False
    for ln in notice.lines:
        t = ln.text
        if not BAND_CTX.search(t) or BAND_SKIP.search(t):
            continue
        bands = line_bands(t)
        if len(bands) != 1:
            continue
        lo, hi = bands[0]
        seen = True
        if lo * 1e8 <= amount < hi * 1e8:
            return False
    return seen
