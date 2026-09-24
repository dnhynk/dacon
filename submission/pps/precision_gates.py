"""Precision gates (config switch ``precision_gates``, default ``()``).

Each enabled gate clears a positive cell (v=0, e='') that lacks the edit signal
measured in runs/harness_improve_20260919/precision_analysis/; the rules in
``SET_RULES`` instead set a zero cell to 1 on positive evidence. The
definitions are those of the analysis scripts named below and must stay
identical to them:

- ``v16_size_registration`` / ``v18_size_registration`` (A_deletion_traces/signals.py
  ``meta_residue``): keep v16 only when meta 조항호내용 registers a size restriction
  or a competition-product designation, v18 only when it names 소기업 or 소상공인.
- ``v24_non_method_witness`` (B_v24_tampering/summarize.py ``C1_cpu_nonmethod_witness``):
  keep v24 only when ``comparison.positive_decision`` proves it by a budget,
  estimated-price, region, industry or title-tag difference. e24 is left as is;
  with 24 in ``source_rule_items`` it already is that witness's evidence.
- ``v2_amount`` (C_insertion_fingerprints/cues.py ``perf_amount_any`` over the
  measure.py ``clause_bounds`` of the quoted lines): keep a quoted v2 only when a
  실적/경험 clause the quote overlaps states a KRW amount of at least 1,000,000 or
  a ratio of the price.
- ``long_line`` (C cues.py ``overlong``): clear a quoted positive of v1-v4, v8,
  v12-v15 or v17 whose anchor line (the quoted line with the largest overlap) is
  longer than 1.25 x the document's line-length p90 + 5.
- ``v20_applicability`` (W3/PREREGISTRATION.md): clear v20 in a notice whose meta
  계약방법 is 수의계약. The SW participation-floor disclosure belongs in an
  입찰공고문 or 제안요청서 (지침 제3조②), which a 소액수의 견적 notice is not.
- ``briefing_placeholder`` (set rule; C positive_evidence.py held-briefing line
  with a cues.py placeholder): in a 협상에의한계약 notice (meta 낙찰방법), set a
  zero v22 to 1 when one held-briefing line carries a placeholder (room noun such
  as 발주기관 대회의실, a spaced em dash, or a deferred date/place) and an
  attendance requirement that restricts participation (W3: attendance verb
  참석/불참/미참석, no 상관없음-style negation). e22 is that line (at most 500
  characters). v23 is never set: a placeholder does not prove its schedule shortfall.

- ``v6_local_private_basic`` (set rule; runs/replica_20260922/recovery_02/REPORT.md
  official-exception probe): in a 지방계약법 수의계약 notice whose meta registers a
  regional restriction (지역제한여부 Y) with a basic-level (기초) region code, set a
  zero v6 to 1. The item table remarks "지방 + 소액수의 가능" for v6, so this rule
  measures whether the organizer applies that exception; e6 is the first notice
  line carrying a basic-level region token (at most 500 characters), else empty.

The quoted gates skip a positive without a quote, and a quote that cannot be
located leaves its cell unchanged.
"""
from __future__ import annotations

import re

GATES = ('v16_size_registration', 'v18_size_registration', 'v24_non_method_witness', 'v2_amount', 'long_line',
         'v20_applicability', 'v20_software_project', 'v20_participation_statement', 'v23_local_negotiated',
         'v21_minimum_share', 'v24_amount_permutation', 'v2_price_band', 'local_private_exception', 'v8_region_required',
         'v9_designation', 'briefing_placeholder', 'v6_local_private_basic')
SET_RULES = frozenset({'briefing_placeholder', 'v6_local_private_basic', 'v21_minimum_share', 'v24_amount_permutation',
                       'v9_designation'})

# A: registrations in meta 조항호내용 (signals.META_SME for v16, signals.META_SMALL for v18).
_SIZE_REGISTRATION = re.compile(r'중기업|소기업|소상공인|중소기업자|중기간\s*경쟁|지정\s*[.·]?\s*고시한\s*제품|지정\s*공고한\s*물품')
_SMALL_REGISTRATION = re.compile(r'소기업|소상공인')
# B: positive_decision witnesses other than the competition method.
NON_METHOD_FIELDS = frozenset({'budget', 'estimated_price', 'region', 'industry', 'title_tag'})
# C: items of the long-line gate.
LONG_LINE_ITEMS = (1, 2, 3, 4, 8, 12, 13, 14, 15, 17)

_WS = re.compile(r'\s+')
# C cues.marker: circled, dingbat, negative circled and parenthesized numbers.
_NUMBERED = frozenset(chr(c) for first, n in ((0x2460, 20), (0x2780, 10), (0x278A, 10), (0x2776, 10), (0x2474, 20))
                      for c in range(first, first + n))
_BULLETS = frozenset('○◦❍•·ㆍ∙‣▶▷►□■◆◇◈◐◎●※*-–☞✓√▪▫ㅇo∘')
_HANGUL_ORD = '가나다라마바사아자차카타파하'
_TABLE_PREFIX = re.compile(r'^[\s|]*')
_HG = re.compile(r'^(\()?([가-하])\s*([\.\)])(?=\s|\S)')
_NUM_HY = re.compile(r'^(\d{1,2})\s*-\s*(\d{1,2})\s*[\.\)]?')
_HG_HY = re.compile(r'^([가-하])\s*-\s*(\d{1,2})\s*[\.\)]?')
_NUM = re.compile(r'^(\()?(\d{1,2})\s*([\.\)])(?!\d)')
_AMOUNT = re.compile(r'(\d[\d,]*(?:\.\d+)?)\s*(억|천만|백만|만)?\s*원')
_UNIT = {'억': 10 ** 8, '천만': 10 ** 7, '백만': 10 ** 6, '만': 10 ** 4, None: 1}
_RATIO = re.compile(r'(?:기초금액|추정가격|추정금액|사업비|사업예산|예산|계약금액|배정예산)[^\n]{0,12}\d+(?:\.\d+)?\s*(?:%|배|퍼센트)')
_PERF_WIDE = re.compile(r'실적|경험')
# C positive_evidence.py: a held-briefing line (HELD, not PROPOSAL, not OMITTED) and cues.py placeholders.
_HELD = re.compile(r'(?:현장|사업|과업|제안요청서?|입찰)\s*설명회?')
_PROPOSAL = re.compile(r'제안\s*설명회|제안서\s*설명회|제안\s*발표')
_OMITTED = re.compile(r'생략|실시하지\s*않|개최하지\s*않|없음')
_PLACEHOLDERS = (
    re.compile(r'(?:발주기관|수요기관|주관기관|발주처|당\s?기관|본\s?기관)\s*(?:의\s*)?'
               r'(?:대회의실|중회의실|소회의실|회의실|별관|본관|대강당|강당|세미나실|회의동|교육장|접견실)'),   # VENUE_RE
    re.compile(r'\S\s—\s\S'),                                                                                  # SPACED_EM_RE
    re.compile(r'\([^()\n]{0,12}(?:일시|일정)[^()\n]{0,20}(?:안내|공지|통보|게시|참조)[^()\n]{0,5}\)'
               r'|(?:일시|일정)\s*[·ㆍ・･∙]\s*장소'))                                                         # DEFERRED_RE
# W3: C's ATTEND without 참가 (participation, not attendance), on a line without a negation.
_ATTENDANCE = re.compile(r'(?:참석|불참|미참석)[^\n]{0,60}(?:자격|제외|불허|허용되지|접수하지|한하여|한함|부여|불가)'
                         r'|참석한\s*자')
_ATTENDANCE_NOT_REQUIRED = re.compile(r'상관\s*없|관계\s*없|무관|제외하지\s*않|제외되지\s*않|불이익\s*(?:이\s*)?없')


def _non_method_witness(record):
    """comparison.positive_decision proves v24 by a field other than the competition method."""
    from .comparison import compare, positive_decision
    decision = positive_decision(record, compare(record))
    return bool(decision) and decision['comparison'].get('field') in NON_METHOD_FIELDS


def _locate(record, quote):
    """(doc_index, start, end) of the quote's first occurrence: exact, else whitespace-free (C common.locate)."""
    for i, doc in enumerate(record['docs']):
        at = doc['text'].find(quote)
        if at >= 0:
            return i, at, at + len(quote)
    target = _WS.sub('', quote)
    if not target:
        return None
    for i, doc in enumerate(record['docs']):
        text = doc['text']
        keep = [j for j, ch in enumerate(text) if not ch.isspace()]
        at = ''.join(text[j] for j in keep).find(target)
        if at >= 0:
            return i, keep[at], keep[at + len(target) - 1] + 1
    return None


def _quoted_lines(record, quote):
    """(lines, first, last, anchor) of the located quote's document (C measure.cell_features), or None."""
    located = _locate(record, quote)
    if located is None:
        return None
    di, a, b = located
    lines = record['docs'][di]['text'].split('\n')
    spans, start = [], 0
    for line in lines:
        spans.append((start, start + len(line)))
        start += len(line) + 1
    covered = [i for i, (lo, hi) in enumerate(spans) if lo < b and hi > a]
    if not covered:
        return None
    anchor = max(covered, key=lambda j: min(b, spans[j][1]) - max(a, spans[j][0]))
    return lines, covered[0], covered[-1], anchor


def _is_marker(line):
    """The line starts with a list marker (C cues.marker is not None)."""
    s = _TABLE_PREFIX.sub('', line)
    if not s:
        return False
    ch = s[0]
    if ch in _NUMBERED:
        return True
    m = _HG_HY.match(s)
    if m and m.group(1) in _HANGUL_ORD:
        return True
    if _NUM_HY.match(s):
        return True
    m = _HG.match(s)
    if m and m.group(2) in _HANGUL_ORD and (len(s) < 3 or s[2:3] in ' .)\t' or not '가' <= (s[2:3] or 'a') <= '힣'):
        return True
    if _NUM.match(s):
        return True
    return ch in _BULLETS and (ch not in 'oㅇ-*' or s[1:2] in (' ', ''))


def _clause_bounds(lines, markers, anchor):
    """Nearest marker line at or up to 4 lines above the anchor, to the clause end (C measure.clause_bounds)."""
    start = anchor
    for j in range(anchor, max(-1, anchor - 5), -1):
        if markers[j]:
            start = j
            break
        if j < anchor and not lines[j].strip():
            break
    if not markers[start]:
        return start, anchor
    last = start                                   # C cues.clause_end_line
    for j in range(start + 1, min(len(lines), start + 12)):
        if markers[j]:
            break
        if lines[j].strip():
            last = j
    return start, max(anchor, last)


def _states_amount(clause):
    """C cues.amounts(clause) is nonempty: a KRW amount of at least 1,000,000."""
    for m in _AMOUNT.finditer(clause):
        try:
            value = float(m.group(1).replace(',', '')) * _UNIT[m.group(2)]
        except ValueError:
            continue
        if value >= 10 ** 6:
            return True
    return False


def _performance_amount(lines, first, last):
    """C cues.perf_amount_any over the clauses of the quoted lines: None without a 실적/경험 clause."""
    markers = [_is_marker(line) for line in lines]
    clauses, seen = [], set()
    for j in range(first, last + 1):
        if lines[j].strip():
            bounds = _clause_bounds(lines, markers, j)
            if bounds not in seen:
                seen.add(bounds)
                clauses.append(' '.join(lines[i] for i in range(bounds[0], bounds[1] + 1) if lines[i].strip()))
    performance = [c for c in clauses if _PERF_WIDE.search(c)]
    if not performance:
        return None
    return any(_states_amount(c) or bool(_RATIO.search(c)) for c in performance)


def _overlong(lines, anchor):
    """C cues.overlong with cues.doc_wrap_width: None for fewer than 30 lines of at least 10 characters."""
    lengths = sorted(len(x.strip()) for x in lines if len(x.strip()) >= 10)
    if len(lengths) < 30:
        return None
    return len(lines[anchor].strip()) > 1.25 * lengths[int(0.9 * (len(lengths) - 1))] + 5


def _placeholder_attendance_clause(record):
    """The first held-briefing line with a placeholder and a participation-restricting attendance requirement."""
    for doc in record['docs']:
        for line in doc['text'].split('\n'):
            if (_HELD.search(line) and not _PROPOSAL.search(line) and not _OMITTED.search(line)
                    and any(rx.search(line) for rx in _PLACEHOLDERS)
                    and _ATTENDANCE.search(line) and not _ATTENDANCE_NOT_REQUIRED.search(line)):
                clause = line.strip()
                if len(clause) <= 500 and not clause.startswith(('=', '+', '@')):
                    return clause
    return None


# Track B A19 family (items/a19/REPORT.md). Each rule restates an item-table condition on the source text or meta.
_PARTICIPATION_STATEMENT = re.compile(
    r'대기업.{0,40}(참여|참가).{0,20}제한|참여\s*제한.{0,30}(대기업|중견|하한)|중소\s*소프트웨어\s*사업자'
    r'|대기업인\s*소프트웨어사업자|소프트웨어\s*진흥법\s*제48조|사업금액.{0,30}(대기업|중견)')
_NOTICE_AMOUNT = re.compile(r'(\d{1,3}(?:,\d{3})+|\d{5,})\s*원')
_JOINT_SHARE = re.compile(r'(지분|출자\s*비율|참여\s*비율|분담\s*비율|지분율).{0,40}?(\d{1,2}(?:\.\d+)?)\s*%\s*(이상|미만)')


def _notice_docs(record):
    return [doc.get('text', '') for doc in record.get('docs', []) if doc.get('type') == '공고문']


def _line_of(text, start, end):
    """The source line holding text[start:end], a substring of the document (at most 500 characters)."""
    lo = text.rfind('\n', 0, start) + 1
    hi = text.find('\n', end)
    line = text[lo:len(text) if hi < 0 else hi].strip()
    return line if len(line) <= 500 else text[start:end]


def _amount_permutation(record):
    """A 공고문 amount whose digits permute a registered amount (배정예산금액, 입찰추정가격) but differ from it (v24)."""
    meta = record.get('meta', {})
    registered = {str(int(v)) for v in (meta.get('배정예산금액'), meta.get('입찰추정가격'))
                  if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0}
    for text in _notice_docs(record):
        for m in _NOTICE_AMOUNT.finditer(text):
            digits = m.group(1).replace(',', '')
            if any(len(digits) == len(r) and digits != r and sorted(digits) == sorted(r) for r in registered):
                return _line_of(text, m.start(), m.end())
    return None


def _low_joint_share(record):
    """A stated joint-contract minimum share below 10% (국가계약법) or 5% (지방계약법), outside 분담이행 (v21)."""
    from .legal_context import applicable_law
    threshold = {'국가계약법': 10, '지방계약법': 5}.get(applicable_law(record))
    if threshold is None:
        return None
    for text in _notice_docs(record):
        if re.search(r'분담\s*이행', text):
            return None
        for m in _JOINT_SHARE.finditer(text):
            if m.group(3) == '이상' and float(m.group(2)) < threshold:
                return _line_of(text, m.start(), m.end())
    return None


def apply(record, row, enabled):
    """Apply the enabled gates in GATES order and return the changed (item, name) pairs in that order.

    A pair whose name is in SET_RULES set a zero cell to 1 with its evidence; every other name cleared a
    positive (v=0, e='').
    """
    changed = []

    def clear(k, gate):
        row[f'v{k}'], row[f'e{k}'] = 0, ''
        changed.append((k, gate))

    registration = str(record.get('meta', {}).get('조항호내용') or '')
    if 'v16_size_registration' in enabled and row['v16'] == 1 and not _SIZE_REGISTRATION.search(registration):
        clear(16, 'v16_size_registration')
    if 'v18_size_registration' in enabled and row['v18'] == 1 and not _SMALL_REGISTRATION.search(registration):
        clear(18, 'v18_size_registration')
    if 'v24_non_method_witness' in enabled and row['v24'] == 1 and not _non_method_witness(record):
        clear(24, 'v24_non_method_witness')
    if 'v2_amount' in enabled and row['v2'] == 1 and row['e2']:
        quoted = _quoted_lines(record, row['e2'])
        if quoted is not None and _performance_amount(*quoted[:3]) is not True:
            clear(2, 'v2_amount')
    if 'long_line' in enabled:
        for k in LONG_LINE_ITEMS:
            if row[f'v{k}'] == 1 and row[f'e{k}']:
                quoted = _quoted_lines(record, row[f'e{k}'])
                if quoted is not None and _overlong(quoted[0], quoted[3]) is True:
                    clear(k, 'long_line')
    meta = record.get('meta', {})
    if 'v20_applicability' in enabled and row['v20'] == 1 and meta.get('계약방법') == '수의계약':
        clear(20, 'v20_applicability')
    try:  # gate 'v20_software_project': a failure skips this gate only (the run must always write its CSV)
        if 'v20_software_project' in enabled and row['v20'] == 1:
            # v20 applies only to software projects (소프트웨어 진흥법 제2조, organizer notice).
            from .csc_probe import software_project
            if not software_project(record):
                clear(20, 'v20_software_project')
    except Exception:
        pass
    try:  # gate 'v20_participation_statement': a failure skips this gate only (the run must always write its CSV)
        if 'v20_participation_statement' in enabled and row['v20'] == 1:
            # v20 is the absence of the participation-limit statement; a notice that states it is not v20.
            if _PARTICIPATION_STATEMENT.search(chr(10).join(doc.get('text', '') for doc in record.get('docs', []))):
                clear(20, 'v20_participation_statement')
    except Exception:
        pass
    try:  # gate 'v23_local_negotiated': a failure skips this gate only (the run must always write its CSV)
        if 'v23_local_negotiated' in enabled and row['v23'] == 1:
            # v23 covers only 지방계약법 negotiated contracts (national notices are 0).
            from .legal_context import applicable_law
            if not (applicable_law(record) == '지방계약법' and meta.get('낙찰방법') == '협상에의한계약'):
                clear(23, 'v23_local_negotiated')
    except Exception:
        pass
    try:  # gate 'v21_minimum_share': a failure skips this gate only (the run must always write its CSV)
        if 'v21_minimum_share' in enabled and row['v21'] == 0:
            clause = _low_joint_share(record)
            if clause:
                row['v21'], row['e21'] = 1, clause
                changed.append((21, 'v21_minimum_share'))
    except Exception:
        pass
    try:  # gate 'v24_amount_permutation': a failure skips this gate only (the run must always write its CSV)
        if 'v24_amount_permutation' in enabled and row['v24'] == 0:
            clause = _amount_permutation(record)
            if clause:
                row['v24'], row['e24'] = 1, clause
                changed.append((24, 'v24_amount_permutation'))
    except Exception:
        pass
    try:  # gate 'v2_price_band': a failure skips this gate only (the run must always write its CSV)
        if 'v2_price_band' in enabled and row['v2'] == 1:
            # v2 applies only below the 2.3억 notice amount (item table; organizer notice keeps 2.3억 for v2).
            from .performance import project_prices as performance_prices
            estimate = meta.get('입찰추정가격') or (performance_prices(record).get('estimated_price') or {}).get('value_won')
            if isinstance(estimate, (int, float)) and estimate >= 230_000_000:
                clear(2, 'v2_price_band')
    except Exception:
        pass
    try:  # gate 'local_private_exception': a failure skips this gate only (the run must always write its CSV)
        if 'local_private_exception' in enabled and meta.get('계약방법') == '수의계약':
            # Official exception: a local-law private contract may restrict by record and region (항목표 '지방 + 소액수의 가능'
            # on v2, v6, v7, v8; build_21 LB confirmed it for v6).
            from .legal_context import applicable_law
            if applicable_law(record) == '지방계약법':
                for k in (2, 6, 7, 8):
                    if row[f'v{k}'] == 1:
                        clear(k, 'local_private_exception')
    except Exception:
        pass
    try:  # gate 'v8_region_required': a failure skips this gate only (the run must always write its CSV)
        if 'v8_region_required' in enabled and row['v8'] == 1 and meta.get('지역제한여부') != 'Y':
            # v8 needs a region restriction together with the record restriction.
            from .performance import performance_facts
            if not performance_facts(record, consumer=True)['operative_regions']:
                clear(8, 'v8_region_required')
    except Exception:
        pass
    try:  # gate 'v9_designation': a failure skips this gate only (the run must always write its CSV)
        if 'v9_designation' in enabled and row['v9'] == 0:
            # v9: a specification that names the manufacturer or model (제조사·모델명·상표 label with a named value, or a
            # brand + model + 시리즈). Numeric spec values, '동등' and maintenance of existing equipment are excluded.
            from .semantic_rules import v9_lines
            found = v9_lines(record)
            if found:
                row['v9'], row['e9'] = 1, found[0][1].lstrip('=+@ ')
                changed.append((9, 'v9_designation'))
    except Exception:
        pass
    if 'briefing_placeholder' in enabled and row['v22'] == 0 and meta.get('낙찰방법') == '협상에의한계약':
        clause = _placeholder_attendance_clause(record)
        if clause is not None:
            row['v22'], row['e22'] = 1, clause
            changed.append((22, 'briefing_placeholder'))
    if ('v6_local_private_basic' in enabled and row['v6'] == 0 and meta.get('적용계약법') == '지방계약법'
            and meta.get('계약방법') == '수의계약' and meta.get('지역제한여부') == 'Y'
            and '기초' in str(meta.get('제한지역코드목록') or '')):
        row['v6'], row['e6'] = 1, _basic_region_line(record)
        changed.append((6, 'v6_local_private_basic'))
    return changed


def _basic_region_line(record):
    """The first notice line carrying a basic-level region token, trimmed to 500 characters; '' when none."""
    lines = [line.strip() for doc in record.get('docs', []) for line in str(doc.get('text') or '').splitlines()
             if '단위=기초' in line and line.strip()]
    restricting = [line for line in lines if re.search(r'소재|영업소|본점|제한', line)]
    return (restricting or lines or [''])[0][:500]
