"""v12 일반제품 직생 제한 as a dedicated stage (switches.DEDICATED = ('v12',)).

Violation: the notice makes the 판로지원법 제9조 직접생산확인증명서 (or 직접생산 confirmation) a bid-participation condition or a
mandatory bid document while the notice's own subject (공고 대상 품명) is not a 중소기업자간 경쟁제품 at the notice's terms
(국가계약령 제21조①8 / 지방계약령 제20조①8; talkboard: the subject decides, not the certificate's 품명; a 특이사항 cap exceeded
makes the product non-competing).

CPU: whitespace-tolerant 직접생산 mentions tagged on their own line (not_required > demand > penalty > validity > other);
penalty/validity-only notices and notices whose registered 세부품명 (meta code, else a 공고문 품명 line) is listed inside its
price limit never reach the model. Model (one guided-JSON call): how the notice treats the certificate, what is actually
procured and which listed 세부품명 that is, non-price scope conditions, the demand line. CPU decides the label; with no
reading (mock, deadline, invalid output) the CPU default reads the demand from the line tags and the subject from the
registered code or catalog.classify.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace

from .. import catalog, families as F, switches

FAM = 'd_general_dp'
PROMPT_TOKEN_LIMIT = 12000
MAX_TOKENS = 500
NONE_ITEM = '해당없음(경쟁제품 아님)'
UNKNOWN = F.UNKNOWN
DEMAND_ENUM = ('참가자격_필수', '제출서류_필수', '선택_또는_대체가능', '계약후_제출서류', '요구하지_않음_명시', '안내문구만', UNKNOWN)
MANDATORY = ('참가자격_필수', '제출서류_필수')
SCOPE_ENUM = ('조건충족', '조건불충족', '조건없음_또는_판단불가', UNKNOWN)
MAX_WINDOWS = 8
MAX_CITED = 6
WINDOW_TAIL = 170          # chars shown after the term (following lines of the same document)
WINDOW_LINES = 3           # following lines at most
HEAD_LINES = 6
OVERVIEW_LINES = 7
ATTACH_LINES = 5
EVENT_FAMILY = frozenset({'8014190201', '8014198801', '8014198901', '8014199001', '9015189001'})   # 회의·전시회·국제행사·기타행사·축제

# ---------------------------------------------------------------- candidate lines
DP_TERM = re.compile(r'직\s*접\s*생\s*산')
CERT_TERM = re.compile(r'직\s*접\s*생\s*산\s*(?:확\s*인)?\s*(?:증\s*명\s*서|확\s*인\s*서|증\s*명|확\s*인\s*증)')
TAGS = {
    'not_required': re.compile(r'(요구|요청)하지\s*않|제출하지\s*않아도|별도로\s*요구하지|생략'),
    'demand': re.compile(
        r'(증명서|확인서|증명|확인증)\s*(사본\s*)?(을|를)?\s*(모두\s*)?(소지|보유|갖춘|구비|제출|첨부|받은|발급받은|등록)'
        r'|(소지|보유|구비)\s*(한|하고\s*있는|하는)\s*(자|업체|기업)'
        r'|직\s*접\s*생\s*산\s*(하는|할\s*수\s*있는|이\s*가능한)\s*(업체|자|기업|중소기업)'
        r'|직접생산\s*(업체|기업|자)\b'
        r'|(증명서|확인서|확인증)\s*(사본\s*)?(각\s*)?\d\s*(부|통|매)'
        r'|직\s*접\s*생\s*산\s*[:：]'
        r'|직접생산\s*여부\s*(를\s*)?확인'
        r'|(업체|자|기업|것|사업자|중소기업자)\s*(이어야|여야|어야)\s*(합니다|함|한다|하며)'),
    'penalty': re.compile(r'(조건|기준)\s*(을|를)\s*위반|위반\s*(하여|한|할)\s*(경우|계약|사실|납품)|부정당|제재|계약\s*해지|국고\s*귀속'),
    'validity': re.compile(r'유효\s*기간|유효하여야|확인이\s*(안|되지)|확인되지\s*않|종합\s*정보망|smpp'),
}
# CPU-default demand (no model reading): the certificate is named and held/submitted, with no condition or alternative.
POSSESS = re.compile(r'(소\s*지|보\s*유|발\s*급\s*받|취\s*득|갖\s*춘|구\s*비)|(증명서|확인서|확인증)\s*(사본\s*)?(각\s*)?\d\s*(부|통|매)')
CONDITIONAL = re.compile(r'해\s*당\s*(시|자|업체|기업|되는\s*경우|하는\s*경우|품\s*목)|필요\s*시|또는\s*[^\n]{0,40}(공급\s*증명원|확약서|대체)|대체할\s*수'
                         r'|제조사\s*(의\s*)?직접생산|경\s*쟁\s*제\s*품[^.。]{0,24}(인|일|으로\s*입\s*찰\s*공\s*고\s*한|에\s*해\s*당\s*하\s*는)\s*경\s*우|계약\s*(시|체결\s*시|후)')
VERIFY_ONLY = re.compile(r'(확\s*인|조\s*회)\s*(\([^)]{0,120}\))?\s*(이|가)?\s*(안\s*될|안\s*되는|되지\s*않을|되지\s*않는|불가할)\s*(경우|시)')

PUM_LINE = re.compile(r'^\s*(?:[가-힣]\.|[○ㅇ◦•\-\d]+[.)]?|[①-⑳]|[▸►▶■□◎※]|\|)?\s*(?:세부)?\s*품\s*명\s*[:：]\s*(.+)$')
OVERVIEW_HEAD = re.compile(r'(사업|용역|과업|입찰|구매)\s*(개요|목적|내용|범위|명)')


def classify(core_text):
    t = re.sub(r'\s+', ' ', core_text)
    tags = {k for k, p in TAGS.items() if p.search(t)}
    for kind in ('not_required', 'demand', 'penalty', 'validity'):
        if kind in tags:
            return kind, tags
    return 'other', tags


def windows(notice):
    """[{'core': [Line], 'lines': [Line], 'kind', 'tags', 'has_cert'}] — one per 직접생산 mention (a term split over two lines
    counts once), the core being the mention's own line(s) and `lines` the core plus following lines of the same document."""
    out, taken = [], set()
    lines = notice.lines
    for k, ln in enumerate(lines):
        if ln.i in taken or not ln.text.strip():
            continue
        nxt = lines[k + 1] if k + 1 < len(lines) and lines[k + 1].doc == ln.doc else None
        if DP_TERM.search(ln.text):
            core = [ln]
        elif nxt is not None and DP_TERM.search(ln.text.rstrip() + nxt.text.lstrip()) and not DP_TERM.search(nxt.text):
            core = [ln, nxt]
        else:
            continue
        core_text = '\n'.join(c.text for c in core)
        kind, tags = classify(core_text)
        shown = list(core)
        after, m = 0, DP_TERM.search(core_text)
        after = len(core_text) - (m.end() if m else 0)
        j = k + len(core)
        while after < WINDOW_TAIL and len(shown) - len(core) < WINDOW_LINES and j < len(lines) and lines[j].doc == ln.doc:
            if lines[j].text.strip():
                shown.append(lines[j])
                after += len(lines[j].text)
            j += 1
        taken.update(c.i for c in core)
        out.append({'core': core, 'lines': shown, 'kind': kind, 'tags': sorted(tags), 'has_cert': bool(CERT_TERM.search(core_text))})
    return out


def candidate_windows(notice):
    wins = windows(notice)
    return wins if any(w['kind'] in ('demand', 'other') for w in wins) else []


def cpu_demand_window(wins):
    """The first demand-tagged window naming the certificate with holding/submission wording and no condition, alternative,
    contract-time or verification-only wording; None when there is none."""
    for w in wins:
        if w['kind'] != 'demand' or not w['has_cert']:
            continue
        t = re.sub(r'\s+', ' ', '\n'.join(c.text for c in w['core']))
        if POSSESS.search(t) and not CONDITIONAL.search(t) and not (VERIFY_ONLY.search(t) and not re.search(r'소\s*지|보\s*유', t)):
            return w
    return None


def cpu_demand(wins):
    """CPU default for the demand class (no model reading)."""
    return '참가자격_필수' if cpu_demand_window(wins) is not None else '안내문구만'


# ---------------------------------------------------------------- catalog facts
def _norm(s):
    return re.sub(r'[\s·ㆍ,.\-()（）/]', '', s or '')


def _limit(note):
    """'추정가격 3억원 미만에 한함' -> ('lt', 3e8); '1천만원 이상 … 한함' -> ('ge', 1e7); None when the 특이사항 has no price limit."""
    if not note:
        return None
    m = re.search(r'(\d+)\s*(천만|백만|억|만)\s*원\s*(미만|이하|이상|초과)', note)
    if not m or not re.search(r'추정가격|입찰\s*금액|금액|원\s*(미만|이상|이하|초과)', note):
        return None
    unit = {'천만': 1e7, '백만': 1e6, '억': 1e8, '만': 1e4}[m.group(2)]
    return {'미만': 'lt', '이하': 'le', '이상': 'ge', '초과': 'gt'}[m.group(3)], int(m.group(1)) * unit


def amount_ok(prod, P):
    lim = _limit(prod.note)
    if lim is None or P is None:
        return True
    op, v = lim
    return {'lt': P < v, 'le': P <= v, 'ge': P >= v, 'gt': P > v}[op]


def family(code):
    return '행사' if code in EVENT_FAMILY else code[:6]


def service_products(cat):
    return [p for p in cat.by_code.values() if p.code and p.code[0] in '789']


def cited(text, cat):
    """(listed products, unlisted 10-digit codes) named in a text: codes, 8-digit code prefixes, or catalog names."""
    found, unlisted = {}, set()
    for c in re.findall(r'(?<!\d)(\d{10})(?!\d)', text):
        if c in cat.by_code:
            found[c] = cat.by_code[c]
        else:
            unlisted.add(c)
    for c8 in re.findall(r'(?<!\d)(\d{8})(?!\d)', text):
        for code, p in cat.by_code.items():
            if code.startswith(c8):
                found[code] = p
    flat = _norm(text)
    for p in cat.by_code.values():
        if len(_norm(p.name)) >= 4 and _norm(p.name) in flat:
            found[p.code] = p
    return list(found.values()), sorted(unlisted)


def registered(b, cat):
    """(products, unlisted codes) of the registered 세부품명: meta codes, else the 공고문 품명 line(s)."""
    if b.meta.codes:
        prods, unl = [], []
        for name, code in b.meta.codes:
            p = cat.by_code.get(code) or cat.by_name.get(re.sub(r'\s', '', name))
            (prods.append(p) if p else unl.append(code))
        return prods, unl, 'meta'
    prods, unl = [], []
    for ln in pum_lines(b.notice):
        ps, us = cited(ln.text, cat)
        prods += ps
        unl += us
    return prods, unl, '품명행'


def pum_lines(notice):
    out = []
    for ln in notice.notice_lines()[:600]:
        if PUM_LINE.match(ln.text):
            out.append(ln)
    return out[:4]


def precheck(b, cat):
    """Reason string when the registered subject is a listed competition product inside its price limit (lawful demand,
    no model call); None otherwise."""
    prods, unl, src = registered(b, cat)
    ok = [p for p in prods if amount_ok(p, b.meta.P)]
    if ok:
        return f'{src}: {",".join(p.name for p in ok)}'
    return None


# ---------------------------------------------------------------- excerpt and prompt
def subject_lines(notice):
    """Lines that state what is procured: 공고문 head, title-label lines, 품명 lines, the first overview block, the first lines
    of the first two attachments."""
    out, seen = [], set()

    def add(ln):
        if ln is not None and ln.i not in seen and ln.text.strip():
            seen.add(ln.i)
            out.append(ln)

    gong = notice.notice_lines()
    for ln in [x for x in gong if x.text.strip()][:HEAD_LINES]:
        add(ln)
    for ln in gong[:200]:
        if catalog.TITLE_LABEL.search(ln.text):
            add(ln)
            if len(out) > HEAD_LINES + 3:
                break
    for ln in pum_lines(notice):
        add(ln)
    for k, ln in enumerate(gong[:800]):
        if OVERVIEW_HEAD.search(ln.text):
            for x in [y for y in gong[k:k + OVERVIEW_LINES * 2] if y.text.strip()][:OVERVIEW_LINES]:
                add(x)
            break
    docs = []
    for ln in notice.lines:
        if ln.doc_type != '공고문' and ln.doc not in docs:
            docs.append(ln.doc)
    for d in docs[:2]:
        for ln in [x for x in notice.lines if x.doc == d and x.text.strip()][:ATTACH_LINES]:
            add(ln)
    return out


def goods_shortlist(b, cat, k=12):
    text = _norm(' '.join(ln.text for ln in subject_lines(b.notice)[:12]) + ' ' + str(b.notice.meta.get('세부품명번호목록') or ''))
    grams = {text[i:i + 2] for i in range(len(text) - 1)}
    scored = []
    for p in cat.by_code.values():
        if not p.code or p.code[0] in '789':
            continue
        n = _norm(p.name)
        g = {n[i:i + 2] for i in range(len(n) - 1)}
        ov = len(g & grams)
        if ov:
            scored.append((ov / max(1, len(g)), p.name))
    scored.sort(reverse=True)
    return [n for _, n in scored[:k]]


def subject_enum(b, cat, wins):
    if b.meta.work == '물품':
        names = goods_shortlist(b, cat)
    else:
        names = [p.name for p in service_products(cat)]
        extra, _ = cited('\n'.join(c.text for w in wins for c in w['core']), cat)
        names += [p.name for p in extra if p.code and p.code[0] not in '789' and p.name not in names]
    return names + [NONE_ITEM, UNKNOWN]


def schema(b, cat, wins):
    return {'type': 'object', 'additionalProperties': False,
            'required': ['demand', 'demand_line', 'cited_items', 'subject_summary', 'subject_item', 'scope_condition', 'evidence'],
            'properties': {
                'demand': {'type': 'string', 'enum': list(DEMAND_ENUM)},
                'demand_line': {'type': 'integer', 'minimum': -1, 'maximum': 100000},
                'cited_items': {'type': 'array', 'maxItems': MAX_CITED, 'items': {'type': 'string', 'maxLength': 40}},
                'subject_summary': {'type': 'string', 'maxLength': 60},
                'subject_item': {'type': 'string', 'enum': subject_enum(b, cat, wins)},
                'scope_condition': {'type': 'string', 'enum': list(SCOPE_ENUM)},
                'evidence': {'type': 'string', 'maxLength': 300}}}


SYSTEM = ('당신은 공공조달 입찰공고 검토관입니다. 아래 공고 발췌만 보고, 정해진 JSON 스키마대로만 답하십시오. 추측으로 채우지 말고, '
          '발췌에 없는 것은 "불명"/"해당없음"을 고르십시오.\n'
          '[판단할 것]\n'
          '1. demand — 이 공고가 「직접생산확인증명서」(중소기업자간 경쟁제품 직접생산 확인) 보유를 어떻게 다루는가. 여기서 증명서란 「중소기업제품 '
          '구매촉진 및 판로지원에 관한 법률」 제9조의 직접생산확인(중소벤처기업부·중소기업중앙회가 경쟁제품에 대해 발급)을 말합니다. 제조사가 스스로 '
          '써 주는 "직접생산 확인서/증명서", "제조사 확인서"는 이 증명서가 아닙니다.\n'
          '   - 참가자격_필수: 증명서를 소지·보유한 자(업체)만 입찰에 참가할 수 있다고 정함 (예: "직접생산확인증명서를 소지한 업체여야 합니다", '
          '"직접생산확인을 받은 업체", "직접생산확인증명서 및 ○○를 제출할 수 있는 자")\n'
          '   - 제출서류_필수: 입찰참가신청·제안서·견적서의 제출서류 목록에 증명서가 필수 항목으로 들어 있음 (예: "직접생산확인증명서 1부") — '
          '"해당 시" 같은 단서가 없을 때만\n'
          '   - 선택_또는_대체가능: "해당 시", "해당자에 한함", "해당 업체에 한함", "직접생산확인증명서 또는 공급증명원/확약서" 처럼 조건부·선택·'
          '다른 서류로 대체 가능\n'
          '   - 계약후_제출서류: 낙찰 후 계약 체결 시·납품 시에 내는 서류로만 언급됨 (입찰참가 조건이 아님)\n'
          '   - 요구하지_않음_명시: 증명서를 요구하지 않는다고 명시\n'
          '   - 안내문구만: 요구 문장 없이 유효기간·확인 방법·위반 시 제재·법령 설명·"직접 생산한 제품/업체"라는 일반 조건만 있음\n'
          '   ※ "유효기간 내에 있어야 하며 확인이 안 될 경우 참가자격이 없습니다" 같은 문장만 있고 소지·보유·제출을 요구하는 문장이 없으면 안내문구만.\n'
          '   ※ 증명서 언급 없이 "직접 생산하는 업체", "제조사"만 요구하는 문장은 안내문구만.\n'
          '   ※ "경쟁제품으로 입찰공고한 경우", "경쟁제품에 해당하는 경우" 처럼 조건이 붙은 요구는 선택_또는_대체가능.\n'
          '2. demand_line — 요구를 정한 줄의 L번호(숫자만). 없으면 -1.\n'
          '3. cited_items — 요구 문장에 적힌 세부품명(증명서의 품명)들을 적힌 그대로. 없으면 [].\n'
          '4. subject_summary — 이 공고에서 계약상대자가 실제로 납품·수행하는 것이 무엇인지 한 줄(40자 이내). 증명서에 적힌 품명이 아니라 '
          '공고명·개요에서 판단.\n'
          '5. subject_item — 실제 수행 내용이 [경쟁제품 목록] 중 어느 세부품명에 해당하는가. 핵심 업무가 그 서비스 자체일 때만 고르고, 연구·조사·'
          '컨설팅·교육과정 운영·여행·관광·급식·인쇄·임대·시설관리·공사·단순 구매 등 목록에 없는 일이면 "' + NONE_ITEM + '". 발주기관이 어떤 '
          '증명서를 요구했는지는 근거가 아님.\n'
          '   ※ 기념식·세미나·포럼·워크숍·박람회·페스티벌·축제·대회 등 행사의 기획·운영·대행은 행사기획(회의·전시회·국제행사·기타행사·축제) 항목에 해당함.\n'
          '   ※ "' + NONE_ITEM + '"은 공고명·개요를 읽고 목록 밖의 일이라고 확인될 때만 고름. 공고명이 가려져 있거나([공고명]) 개요가 없어 실제 '
          '내용을 알 수 없으면 면허·업종 제한과 발췌에서 가장 가까운 항목을 고름.\n'
          '6. scope_condition — 고른 세부품명에 [조건: ...]이 있으면 공고 내용이 그 조건을 충족하는지. 금액 조건은 여기서 판단하지 말고(별도 계산) '
          '성격 조건만 판단. 조건이 없거나 알 수 없으면 조건없음_또는_판단불가.\n'
          '7. evidence — demand가 참가자격_필수 또는 제출서류_필수이면 그 요구 문장 하나를 발췌에서 글자 그대로 복사(300자 이내). 아니면 "".\n'
          '- 출력은 JSON 하나다.')


def _note(p, n=60):
    if not p.note:
        return ''
    note = re.sub(r'\s+', ' ', p.note)[:n]
    return ' [조건: ' + note + ']'


def menu_text(b, cat, wins):
    if b.meta.work == '물품':
        names = goods_shortlist(b, cat)
        rows = ['- ' + n for n in names] or ['(없음)']
        head = '공고 내용과 글자가 비슷한 물품 세부품명'
    else:
        rows = [f'- {p.name}{_note(p)}' for p in service_products(cat)]
        extra, _ = cited('\n'.join(c.text for w in wins for c in w['core']), cat)
        rows += [f'- {p.name} (공고에 인용된 물품 세부품명){_note(p)}' for p in extra if p.code and p.code[0] not in '789']
        head = '용역 세부품명 전부'
    return f'[경쟁제품 목록] ({head})\n' + '\n'.join(rows) + f'\n- {NONE_ITEM}'


def messages(b, cat, subj, wins):
    m = b.notice.meta
    cat_lines = []
    prods, unl, src = registered(b, cat)
    for p in prods:
        cat_lines.append(f'  · {p.name}({p.code}) → 경쟁제품 목록에 있음{_note(p)}')
    for c in unl:
        cat_lines.append(f'  · {c} → 경쟁제품 목록에 없음')
    info = [f'- 업무구분: {m.get("업무구분")} / 적용법: {m.get("적용계약법")} / 계약방법: {m.get("계약방법")} / 추정가격: {m.get("입찰추정가격")}원',
            f'- 나라장터 등록 세부품명: {m.get("세부품명번호목록") or "(미등록)"}'] + cat_lines + [
            f'- 면허·업종 제한: {m.get("면허업종제한목록") or "(없음)"}',
            f'- 제한 근거(조항호): {m.get("조항호내용") or "(없음)"}']
    order = {'demand': 0, 'other': 1, 'validity': 2, 'not_required': 3, 'penalty': 4}
    shown = sorted(wins, key=lambda w: (order.get(w['kind'], 9), w['core'][0].i))[:MAX_WINDOWS]
    dp_lines = [ln for w in shown for ln in w['lines']]
    user = ('[공고 기본정보]\n' + '\n'.join(info) + '\n\n'
            '[공고명·품명·개요 발췌]\n' + (F.excerpt(b.notice, subj, 0) or '(없음)') + '\n\n'
            '[직접생산 관련 문구 발췌]\n' + F.excerpt(b.notice, dp_lines, 0) + '\n\n'
            + menu_text(b, cat, wins) + '\n\n[출력] 위 스키마의 JSON 객체 하나만 출력.')
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}], dp_lines


def _cands(subj, dp_lines):
    seen, out = set(), []
    for ln in subj + dp_lines:
        if ln.i not in seen:
            seen.add(ln.i)
            out.append(ln)
    return out


def request(engine, b, k, request_cls):
    """None when the CPU decides alone (no candidate, or the registered item is a listed product); else one reading request.
    Over the token limit the excerpt shrinks: window tails, then the number of windows, then the subject block, then
    meta values (as main.make_request)."""
    cat = catalog.load()
    wins = candidate_windows(b.notice)
    if not wins or precheck(b, cat):
        return None
    view, subj, tail = b, subject_lines(b.notice), WINDOW_LINES
    shown = wins
    while True:
        trimmed = [dict(w, lines=w['core'] + w['lines'][len(w['core']):len(w['core']) + tail]) for w in shown]
        msgs, dp_lines = messages(view, cat, subj, trimmed)
        ids = engine.token_ids(msgs)
        if len(ids) + MAX_TOKENS <= min(PROMPT_TOKEN_LIMIT, engine.max_model_len - 64):
            break
        if tail > 0:
            tail -= 1
        elif len(shown) > 2:
            shown = shown[:max(2, int(len(shown) * 0.75))]
        elif len(subj) > HEAD_LINES:
            subj = subj[:HEAD_LINES]
        elif view is b:
            view = replace(b, notice=replace(b.notice, meta={key: None if value is None else F.shown(str(value))
                                                           for key, value in b.notice.meta.items()}))
        elif len(shown) > 1:
            shown = shown[:1]
        else:
            raise ValueError('general_dp: minimum reading request exceeds the engine token limit')
    cands = _cands(subj, dp_lines)
    setattr(b, FAM + '_schema', schema(b, cat, wins))
    return request_cls(k, FAM, cands, (), ids, getattr(b, FAM + '_schema'), MAX_TOKENS, 0)


# ---------------------------------------------------------------- consume
def consume(b, cands, text):
    """Validate the model's JSON against the stage schema and store it as b.d_general_dp; False when invalid (retry)."""
    try:
        obj = json.loads(text.split('<channel|>', 1)[-1])
    except (ValueError, AttributeError):
        return False
    if not isinstance(obj, dict):
        return False
    sch = getattr(b, FAM + '_schema', None) or schema(b, catalog.load(), candidate_windows(b.notice))
    props = sch['properties']
    if any(key not in obj for key in sch['required']):
        return False
    if obj['demand'] not in props['demand']['enum'] or obj['subject_item'] not in props['subject_item']['enum'] \
            or obj['scope_condition'] not in props['scope_condition']['enum']:
        return False
    if type(obj['demand_line']) is not int or not isinstance(obj['cited_items'], list) \
            or any(not isinstance(x, str) for x in obj['cited_items']) or len(obj['cited_items']) > MAX_CITED \
            or not isinstance(obj['subject_summary'], str) or not isinstance(obj['evidence'], str):
        return False
    shown_ids = {ln.i for ln in cands}
    setattr(b, FAM, {'demand': obj['demand'], 'demand_line': obj['demand_line'] if obj['demand_line'] in shown_ids else -1,
                     'cited_items': [x.strip() for x in obj['cited_items']][:MAX_CITED],
                     'subject_summary': obj['subject_summary'].strip()[:60], 'subject_item': obj['subject_item'],
                     'scope_condition': obj['scope_condition'], 'evidence': obj['evidence'].strip()[:300]})
    return True


# ---------------------------------------------------------------- verdict
def _subject_violation(b, cat, wins, reading):
    """Reason string when the subject is not a competition product at this notice's terms, else None."""
    P = b.meta.P
    prods, unl, src = registered(b, cat)
    if src == 'meta' and b.meta.codes:
        ok = [p for p in prods if amount_ok(p, P)]
        if ok:
            return None
        return 'registered item listed but outside its price limit' if prods else f'registered item not in the list: {",".join(unl)}'
    ok = [p for p in prods if amount_ok(p, P)]
    if ok:
        return None
    if prods:
        return '품명 line listed but outside its price limit'
    item = reading.get('subject_item') if reading else None
    if item in (None, UNKNOWN):
        # CPU default: the runtime's catalog classification of the service (title, licence, cited certificate)
        if b.scope.competitive is False:
            return f'catalog: {b.scope.basis}'
        return None
    if item == NONE_ITEM:
        return f'subject not a competition product (model): {reading.get("subject_summary", "")}'
    prod = cat.by_name.get(re.sub(r'\s', '', item))
    if prod is None:
        return None
    core_text = '\n'.join(c.text for w in wins for c in w['core'] if w['kind'] in ('demand', 'other', 'validity'))
    cited_prods, _ = cited(core_text, cat)
    same = [p for p in cited_prods if family(p.code) == family(prod.code)]
    if same:
        if any(amount_ok(p, P) for p in same):
            return f'scope condition not met: {prod.name}' if reading.get('scope_condition') == '조건불충족' else None
        return f'cited {",".join(p.name for p in same)} but 추정가격 {P} outside limit'
    if not amount_ok(prod, P):
        return f'subject {prod.name} but 추정가격 {P} outside limit'
    if reading.get('scope_condition') == '조건불충족':
        return f'scope condition not met: {prod.name}'
    return None


def evidence_line(b, wins, reading):
    lines = b.notice.lines
    if reading and 0 <= reading.get('demand_line', -1) < len(lines):
        ln = lines[reading['demand_line']]
        if DP_TERM.search(ln.text) or CERT_TERM.search(ln.text):
            return ln
    ev = (reading or {}).get('evidence') or ''
    if len(ev.strip()) >= 8:
        key = re.sub(r'\s', '', ev)[:40]
        for w in wins:
            for ln in w['lines']:
                if key and key in re.sub(r'\s', '', ln.text) and (DP_TERM.search(ln.text) or CERT_TERM.search(ln.text)):
                    return ln
    strict = cpu_demand_window(wins)
    if strict is not None:
        return strict['core'][0]
    for kind in ('demand', 'other'):
        for w in wins:
            if w['kind'] == kind:
                return w['core'][0]
    return wins[0]['core'][0]


def verdict(b):
    """The evidence line of a v12 violation, or None. Works without a reading: registered-item cases and the CPU-default
    demand/subject decide."""
    wins = candidate_windows(b.notice)
    if not wins:
        return None
    cat = catalog.load()
    if precheck(b, cat):
        return None
    reading = getattr(b, FAM, None)
    demand = reading['demand'] if reading and reading.get('demand') not in (None, UNKNOWN) else cpu_demand(wins)
    if demand not in MANDATORY:
        return None
    if _subject_violation(b, cat, wins, reading) is None:
        return None
    return evidence_line(b, wins, reading)


def explain(b):
    """Diagnostics: (candidate, precheck, demand, subject reason)."""
    wins = candidate_windows(b.notice)
    if not wins:
        return {'candidate': False}
    cat = catalog.load()
    reading = getattr(b, FAM, None)
    demand = reading['demand'] if reading and reading.get('demand') not in (None, UNKNOWN) else cpu_demand(wins)
    return {'candidate': True, 'precheck': precheck(b, cat), 'demand': demand, 'source': 'model' if reading else 'cpu',
            'subject': _subject_violation(b, cat, wins, reading)}
