# -*- coding: utf-8 -*-
"""v19 dedicated stage (switches.DEDICATED = ('v19',)): 제조사·공급사·기술지원사의 물품공급·기술지원(A/S) 확약서를 낙찰자 결정 전에 요구.

CPU: a line is a candidate when a 확약 token has a pledge-kind word in its window (and, for known boilerplate, an issuer word);
each candidate becomes a snippet of its heading context (CTX_BEFORE non-empty lines above, CTX_AFTER below, same document);
snippets are ranked (공고문 first, issuer word, stage word) and capped at MAX_SNIPPETS. One packet per notice.
Model: per snippet — pledge_kind / pledger / required_from_bidder / timing / timing_basis (facts only, no violation judgment).
CPU rule: SUPPLY_OR_TECH_SUPPORT ∧ pledger ∈ FIRE_PLEDGERS ∧ required YES ∧ timing ∈ FIRE_TIMINGS → evidence = the snippet's
first anchor line (original text). Without a reading (journal-less replay) the keyword stand-in decides; an empty reading
(mock engine) is a reading that fires nothing.
Design: scratchpad h08/clean3/pledge/BLUEPRINT.md (dev dry run TP 6 / FP 4 / FN 0).
"""
import json
import re

FAM = 'd_pledge'
PROMPT_TOKEN_LIMIT = 12000
MAX_TOKENS = 400
MAX_SNIPPETS = 6
CTX_BEFORE = 8          # non-empty lines kept above the first anchor of a snippet
CTX_AFTER = 2           # non-empty lines kept below the last anchor
CTX_CHARS = 160
ANCHOR_CHARS = 400
MERGE_GAP = 4           # anchors this many raw lines apart share one snippet
WIN_BEFORE, WIN_AFTER = 60, 40
ISSUER_WIN = 30         # on a boilerplate line the issuer must be attached to the pledge ("제조사의 정품 공급 확약서"), not merely mentioned
DOC_PRIORITY = {'공고문': 0, '규격서': 1, '제안요청서': 2, '과업지시서': 3}

KINDS = ('SUPPLY_OR_TECH_SUPPORT', 'OTHER', 'NONE')
PLEDGERS = ('THIRD_PARTY_MAKER_OR_SUPPLIER', 'BIDDER_ITSELF', 'UNSPECIFIED')
REQUIRED = ('YES', 'NO')
TIMINGS = ('AT_OR_BEFORE_BID', 'BEFORE_AWARD', 'AFTER_AWARD', 'UNSPECIFIED')
BASES = ('EXPLICIT_IN_LINE', 'FROM_CONTEXT', 'NONE')
# Blueprint §3.3 switches. Default: anything the bidder must hold before the 낙찰자 is decided fires; only a named third-party issuer.
FIRE_TIMINGS = ('AT_OR_BEFORE_BID', 'BEFORE_AWARD')
FIRE_PLEDGERS = ('THIRD_PARTY_MAKER_OR_SUPPLIER',)
# The shared v19 rule skips 수의계약 unless switches.V19_PRIVATE; the blueprint judges 견적 제출 the same as a bid (off = judge all).
EXCLUDE_PRIVATE = False

# ------------------------------------------------------------------------------------------------ candidate selection
ANCHOR = re.compile(r'확약')
KIND = re.compile(r'물품\s*공급|제품\s*공급|정품\s*공급|공급|납품|기술\s*지원|(?<![A-Za-z])A\s*/?\s*S(?![A-Za-z])|'
                  r'사후\s*(?:관리|지원|서비스)|유지\s*보수|정품|무상\s*지원|기술\s*확약')
MFR = re.compile(r'제조\s*(?:사|업체|회사|원|자)|공급\s*(?:사|업체|원|자)|생산\s*(?:업체|자)|총판|대리점|제작\s*(?:사|업체)|기술\s*지원\s*사|'
                 r'수입\s*(?:사|업체|원|자)|공식\s*(?:수입|판매)|OEM|Agency|국내\s*지사')
BOILER = re.compile(r'입찰\s*보증금|조세\s*포탈|확약하며\s*\(?(?:규격|가격)?\)?\s*\(?(?:입찰서|제안서|견적서)\)?를?\s*제출|근로\s*조건|근로자|청렴|'
                    r'보안\s*(?:서약|확약)|기밀|지급\s*확약|납부\s*확약|하도급|불이익도\s*감수|개인정보')
WS_RE = re.compile(r'[ \t　]+')


def clean(text):
    return WS_RE.sub(' ', (text or '').replace('​', ' ')).strip()


def is_anchor_line(text):
    """Some 확약 token has a pledge-kind word within its window; a boilerplate line also needs an issuer word there."""
    boiler = bool(BOILER.search(text))
    for m in ANCHOR.finditer(text):
        win = text[max(0, m.start() - WIN_BEFORE): m.end() + WIN_AFTER]
        if not KIND.search(win):
            continue
        if boiler and not MFR.search(text[max(0, m.start() - ISSUER_WIN): m.end()]):
            continue
        return True
    return False


def anchors(notice):
    return [ln for ln in notice.lines if is_anchor_line(ln.text)]


def _nonempty(notice, doc, lo, hi):
    return [ln for ln in notice.lines[max(0, lo):min(len(notice.lines), hi)] if ln.doc == doc and ln.text.strip()]


def snippets(notice, anchor_lines, ctx_before=CTX_BEFORE):
    """Group the anchor lines into snippets (same document, MERGE_GAP raw lines) with their heading context.
    Each snippet: {'doc_type', 'anchors': [Line], 'rows': [(Line, is_anchor)]} in document order."""
    groups = []
    for ln in sorted(anchor_lines, key=lambda x: x.i):
        if groups and ln.doc == groups[-1][-1].doc and ln.i - groups[-1][-1].i <= MERGE_GAP:
            groups[-1].append(ln)
        else:
            groups.append([ln])
    out = []
    for g in groups:
        first, last = g[0], g[-1]
        rows = {}
        for w in _nonempty(notice, first.doc, first.i - 20, first.i)[-ctx_before:] if ctx_before else []:
            rows[w.i] = w
        for w in _nonempty(notice, first.doc, first.i, last.i + 1):
            rows[w.i] = w
        for w in _nonempty(notice, last.doc, last.i + 1, last.i + 7)[:CTX_AFTER]:
            rows[w.i] = w
        aset = {a.i for a in g}
        out.append({'doc_type': first.doc_type, 'anchors': g, 'rows': [(rows[i], i in aset) for i in sorted(rows)]})
    return out


def _anchor_text(s):
    return ' '.join(a.text for a in s['anchors'])


def _rank_key(s):
    t = _anchor_text(s)
    staged = bool(BID_T.search(t) or AWARD_T.search(t) or AFTER_T.search(t))
    return (DOC_PRIORITY.get(s['doc_type'], 9), 0 if MFR.search(t) else 1, 0 if staged else 1, s['anchors'][0].i)


def select(notice, cap=MAX_SNIPPETS):
    """Candidate lines: the anchors of the top `cap` snippets, in document order (the request's cands)."""
    snips = sorted(snippets(notice, anchors(notice)), key=_rank_key)[:cap]
    return sorted((a for s in snips for a in s['anchors']), key=lambda ln: ln.i)


# ------------------------------------------------------------------------------------------------ prompt and schema
SYSTEM = ('당신은 공공조달 입찰공고문을 읽고, 아래 발췌문(들)에서 "확약서"에 관한 요구사항을 사실대로 분류합니다. '
          '위반 여부는 판단하지 않습니다. 발췌문마다 정확히 하나의 결과를 JSON으로 출력합니다.')

GUIDE = """[발췌문의 형식] 각 발췌문은 한 문서(공고문/규격서/제안요청서/과업지시서)의 연속된 줄입니다. ">>" 로 시작하는 줄이 판단 대상이고,
그 앞뒤 줄은 소속 목차·항목 제목 등 문맥입니다. 판단 대상이 목록의 한 항목이면 그 목록의 제목(예: "입찰참가서류", "계약 시 제출서류",
"적격심사 서류")에서 제출 시점을 읽습니다. 한 발췌문에 ">>" 줄이 여러 개이면 가장 강한 요구(가장 이른 시점)를 기준으로 답합니다.

[분류 항목]
1. pledge_kind — ">>" 줄의 확약서(확약)가 무엇에 관한 것인가
 - SUPPLY_OR_TECH_SUPPORT: 납품할 물품의 공급(물품공급·제품공급·정품공급·납품·공급물량)이나 그 물품의 기술지원·A/S·사후관리·유지보수에 관한 확약(서).
   "공급증명원(공급자증명원) 및 A/S 확약서"처럼 묶여 있으면 포함.
 - OTHER: 그 밖의 확약(청렴, 보안, 입찰보증금·계약보증금 지급, 조세포탈 서약, 근로조건 보호, 하도급, 납기·준공 준수 서약,
   입찰서 서식의 "…확약하며 입찰서를 제출합니다" 등)
 - NONE: 확약(서)에 대한 언급이 아님
2. pledger — 그 확약서를 발급(작성)하는 주체
 - THIRD_PARTY_MAKER_OR_SUPPLIER: 입찰자가 아닌 제3자, 즉 물품의 제조사(제조업체·제조회사·제조원·원제조사)·공급사(공급업체·공급자·총판·
   대리점·수입사·국내지사)·기술지원사가 발급하고 입찰자가 받아 오는 확약서. 발췌문이 "제조사의 / 제조사로부터 / 제조업체가 발급한 /
   제조사와의 확약서"처럼 발급 주체를 밝히거나, 제조사 명의 서류(공급증명원·공급자증명원·정품인증서)와 한 묶음으로 요구될 때.
 - BIDDER_ITSELF: 입찰자(당사·본인·계약상대자) 자신이 작성·서명하는 확약서(예: "당사는 …을 확약합니다", 입찰자가 쓰는 확약서 서식)
 - UNSPECIFIED: 발급 주체를 알 수 없음(예: 목록에 "정품공급 확약서 1부"만 있고 누구의 확약서인지 문맥에도 없음)
3. required_from_bidder — 입찰참가자(입찰자·제안자·공급업체·납품업체·판매업자·낙찰자·계약상대자)에게 그 확약서를 발급받아 두거나
   제출하도록 요구하는가
 - YES: 제출·보유·발급·첨부·소지를 요구하거나, 확약서를 가진 자(확약된 자)를 참가자격으로 정함. "미제출 시 제외" 같은 제재가 있으면 YES.
 - NO: 요구가 아님 — 확약서 발급 절차·책임을 설명하는 안내문, 발주기관과 제조사 간 협약의 설명, 확약서 서식(별지·첨부 양식) 자체,
   평가·검수 체크리스트의 항목명, "제출 가능한 업체이어야 한다"처럼 제출 능력만 말하는 자격 문구, 다른 서류로 갈음한다는 문구
4. timing — 입찰참가자가 그 확약서를 가장 이르게 갖추어야(발급·보유·제출) 하는 시점. 여러 시점이 있으면 가장 이른 시점.
 - AT_OR_BEFORE_BID: 입찰 전·입찰 시·입찰(참가)신청 시, 입찰서·규격입찰서·제안서·견적서 제출 시(첨부), 입찰서 제출 마감일(전일)까지
   발급·보유·제출, 입찰참가자격으로서 확약서를 보유·소지한 자 또는 (제조사로부터) 확약된 자, 입찰(참가)서류·제안서 구성 목록의 항목
 - BEFORE_AWARD: 입찰서 제출 이후 낙찰자 결정 이전 — 적격심사(서류 제출) 시, 규격·기술심사 시, 낙찰(예정)자 통보·선정 이전까지 제출
 - AFTER_AWARD: 낙찰(자 결정) 후, 계약 시·계약 체결 전·후, 계약 후 n일 이내, 착수 시, 납품 시·전, 검수 시
 - UNSPECIFIED: 시점을 알 수 없음(목록 제목에도 없음), 또는 required_from_bidder가 NO
5. timing_basis — EXPLICIT_IN_LINE(">>" 줄 자체에 시점이 있음) / FROM_CONTEXT(앞뒤 줄·목록 제목에서 읽음) / NONE

[규칙] 발췌문에 적힌 사실만 사용합니다. 법령 지식이나 업계 통례로 시점이나 발급 주체를 추정하지 않습니다.
확실하지 않으면 UNSPECIFIED를 선택합니다."""

TASK = ('출력 형식(JSON, 발췌문 수와 같은 개수):\n'
        '{"snippets": [{"idx": 1, "pledge_kind": "...", "pledger": "...", "required_from_bidder": "...", "timing": "...", '
        '"timing_basis": "..."}, ...]}\nJSON으로만 답하십시오.')


def render(k, s):
    out = [f"[발췌 {k} | 문서: {s['doc_type']}]"]
    for ln, is_anchor in s['rows']:
        t = clean(ln.text)
        out.append(('>> ' + t[:ANCHOR_CHARS]) if is_anchor else ('   ' + t[:CTX_CHARS]))
    return '\n'.join(out)


def messages(b, cands, ctx_before=CTX_BEFORE):
    snips = snippets(b.notice, cands, ctx_before)
    body = '\n\n'.join(render(k, s) for k, s in enumerate(snips, 1))
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': f'{GUIDE}\n\n[발췌문]\n{body}\n\n{TASK}'}]


def schema():
    item = {'type': 'object', 'additionalProperties': False,
            'required': ['idx', 'pledge_kind', 'pledger', 'required_from_bidder', 'timing', 'timing_basis'],
            'properties': {'idx': {'type': 'integer', 'minimum': 1},
                           'pledge_kind': {'type': 'string', 'enum': list(KINDS)},
                           'pledger': {'type': 'string', 'enum': list(PLEDGERS)},
                           'required_from_bidder': {'type': 'string', 'enum': list(REQUIRED)},
                           'timing': {'type': 'string', 'enum': list(TIMINGS)},
                           'timing_basis': {'type': 'string', 'enum': list(BASES)}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['snippets'],
            'properties': {'snippets': {'type': 'array', 'maxItems': MAX_SNIPPETS, 'items': item}}}


def request(engine, b, k, request_cls):
    """One request per notice with a candidate; the packet shrinks (fewer snippets, then less context) to the token limit."""
    cap, ctx = MAX_SNIPPETS, CTX_BEFORE
    while True:
        cands = select(b.notice, cap)
        if not cands:
            return None
        ids = engine.token_ids(messages(b, cands, ctx))
        if len(ids) + MAX_TOKENS <= min(PROMPT_TOKEN_LIMIT, engine.max_model_len - 64):
            break
        if cap > 1:
            cap = max(1, int(cap * 0.75))
        elif ctx > 0:
            ctx = ctx // 2
        else:
            raise ValueError(f'{FAM}: minimum reading request exceeds the engine token limit')
    return request_cls(k, FAM, cands, (), ids, schema(), MAX_TOKENS, 0)


# ------------------------------------------------------------------------------------------------ consume and verdict
def consume(b, cands, text):
    """Parse the model's JSON, validate it against schema(), store b.d_pledge. False = invalid (retry).
    An item whose idx names no shown snippet is dropped; an empty array (mock engine) is a reading that fires nothing."""
    try:
        obj = json.loads(text.split('<channel|>', 1)[-1])
    except (ValueError, AttributeError, TypeError):
        return False
    if not isinstance(obj, dict) or not isinstance(obj.get('snippets'), list) or len(obj['snippets']) > MAX_SNIPPETS:
        return False
    snips = snippets(b.notice, cands)
    items = []
    for it in obj['snippets']:
        if (not isinstance(it, dict) or type(it.get('idx')) is not int or it.get('pledge_kind') not in KINDS
                or it.get('pledger') not in PLEDGERS or it.get('required_from_bidder') not in REQUIRED
                or it.get('timing') not in TIMINGS or it.get('timing_basis') not in BASES):
            return False
        if 1 <= it['idx'] <= len(snips):
            items.append((it['idx'], it['pledge_kind'], it['pledger'], it['required_from_bidder'], it['timing'], it['timing_basis']))
    setattr(b, FAM, {'items': items, 'snippets': [(s['anchors'][0], s['doc_type']) for s in snips], 'shown': [ln.i for ln in cands]})
    return True


def fires(kind, pledger, required, timing):
    return kind == 'SUPPLY_OR_TECH_SUPPORT' and pledger in FIRE_PLEDGERS and required == 'YES' and timing in FIRE_TIMINGS


def _decide(items, snips):
    """Evidence line of the best firing snippet: 공고문 first, AT_OR_BEFORE_BID before BEFORE_AWARD, then document order."""
    hits = []
    for idx, kind, pledger, required, timing, _basis in items:
        if fires(kind, pledger, required, timing) and 1 <= idx <= len(snips):
            ln, doc_type = snips[idx - 1]
            hits.append((DOC_PRIORITY.get(doc_type, 9), 0 if timing == 'AT_OR_BEFORE_BID' else 1, idx, ln))
    if not hits:
        return None
    return sorted(hits, key=lambda h: h[:3])[0][3]


# ------------------------------------------------------------------------------------------------ keyword stand-in (no reading)
BID_T = re.compile(r'입찰\s*전|입찰\s*시|입찰\s*(?:참가\s*)?신청\s*시|입찰서|규격서\s*제출|제안서|견적서\s*제출|마감\s*일\s*(?:전일?)?\s*까지|'
                   r'참가\s*자격|확약\s*된\s*자|보유한\s*자|소지한\s*자|입찰\s*(?:참가|관련)\s*서류|응찰|투찰')
AWARD_T = re.compile(r'적격\s*심사|규격\s*심사|기술\s*심사|낙찰\s*(?:예정)?자?\s*(?:통보|결정|선정)\s*(?:이전|전)|낙찰\s*(?:대상)?자\s*제외')
AFTER_T = re.compile(r'계약\s*(?:체결\s*)?(?:시|전|후|일|이전|이후|이내|까지)|계약\s*체결|계약을\s*체결|낙찰\s*(?:후|일로부터|자는|자\s*선정\s*후)|'
                     r'납품\s*(?:시|전|후|이전|완료)|검수|착수|계약\s*상대자는|계약\s*후')
ABLE_T = re.compile(r'가능한\s*(?:업체|자)|제출\s*할\s*수\s*있는|제출할\s*수\s*있는|가능\s*여부|가능\s*업체')
NOTREQ_T = re.compile(r'발급은|이루어지므로|책임은|갈음|협의\s*후|서식|양식|\[별지|\[첨부\s*\d|\[붙임|여부\s*$|확인합니다')
FORM_TITLE_T = re.compile(r'확약서\s*$')
LIST_ITEM_T = re.compile(r'^\s*(?:[①-⑳]|\(?\d+[).\-]|[가-힣][).]|[-•ㅇ○◦▪※☞]|\d+\s*[-–]\s*\d+\))')
SELF_T = re.compile(r'당사는|본인은|당사가|확약합니다|확약하며|확약\s*한다')
HEAD_T = re.compile(r'서류|첨부|구비|제출|자격')
BID_LIST_T = re.compile(r'입찰\s*참가\s*신청서|입찰\s*보증|청렴\s*계약\s*이행|규격\s*입찰서|견적서|참가\s*신청')


def standin_items(snips):
    """The keyword stand-in: schema-shaped items for each snippet, from the anchor lines and the nearest heading above."""
    out = []
    for k, s in enumerate(snips, 1):
        texts = [clean(ln.text) for ln, a in s['rows'] if a]
        atext = ' '.join(texts)
        kind = 'OTHER' if (BOILER.search(atext) and not MFR.search(atext)) else 'SUPPLY_OR_TECH_SUPPORT'
        pledger = ('THIRD_PARTY_MAKER_OR_SUPPLIER' if MFR.search(atext) else 'BIDDER_ITSELF' if SELF_T.search(atext) else 'UNSPECIFIED')
        form_title = bool(FORM_TITLE_T.search(atext)) and not LIST_ITEM_T.search(texts[0])
        required = 'NO' if (ABLE_T.search(atext) or NOTREQ_T.search(atext) or form_title) else 'YES'
        timing, basis = 'UNSPECIFIED', 'NONE'
        if required == 'YES':
            if BID_T.search(atext):
                timing, basis = 'AT_OR_BEFORE_BID', 'EXPLICIT_IN_LINE'
            elif AWARD_T.search(atext):
                timing, basis = 'BEFORE_AWARD', 'EXPLICIT_IN_LINE'
            elif AFTER_T.search(atext):
                timing, basis = 'AFTER_AWARD', 'EXPLICIT_IN_LINE'
            else:
                first = s['anchors'][0].i
                for ln, _a in reversed([r for r in s['rows'] if r[0].i < first]):     # nearest heading first
                    t = clean(ln.text)
                    if not HEAD_T.search(t) and not (BID_T.search(t) or AWARD_T.search(t) or AFTER_T.search(t) or BID_LIST_T.search(t)):
                        continue
                    if AWARD_T.search(t):
                        timing, basis = 'BEFORE_AWARD', 'FROM_CONTEXT'
                    elif AFTER_T.search(t) and not BID_T.search(t):
                        timing, basis = 'AFTER_AWARD', 'FROM_CONTEXT'
                    elif BID_T.search(t) or BID_LIST_T.search(t):
                        timing, basis = 'AT_OR_BEFORE_BID', 'FROM_CONTEXT'
                    break
        out.append((k, kind, pledger, required, timing, basis))
    return out


def standin(notice):
    cands = select(notice)
    if not cands:
        return None
    snips = snippets(notice, cands)
    return _decide(standin_items(snips), [(s['anchors'][0], s['doc_type']) for s in snips])


def verdict(b):
    """Evidence line of the firing snippet; None otherwise. Without a reading the keyword stand-in decides."""
    if EXCLUDE_PRIVATE and getattr(getattr(b, 'meta', None), 'private', False):
        return None
    reading = getattr(b, FAM, None)
    if reading is None:
        return standin(b.notice)
    return _decide(reading['items'], reading['snippets'])
