# -*- coding: utf-8 -*-
"""v20 dedicated stage (switches.DEDICATED = ('v20',)): 소프트웨어사업 공고의 대기업 참여제한(사업금액 하한) 문구 누락.

Legal basis: 소프트웨어 진흥법 제48조②, 시행령 제41조②, 중소 소프트웨어사업자의 사업 참여 지원에 관한 지침 제2조(별표1: 20/40/80억)·
제3조② ("입찰공고문 또는 제안요청서에 대기업 참여제한 하한제도 적용 여부(적용 근거 포함)를 명시").
CPU: a notice is a candidate when it treats its object as an SW project itself (SW사업자 1468 bid qualification, an 8111xxxxxx
IT-service 직접생산 code or a 4323 SW 세부품명: self_sw) or when its head text carries SW-project vocabulary without that
qualification (content_sw). A whitespace-normalised, SW-anchored regex with an application verb finds the 참여제한 statement;
such a notice is compliant without the model. Other candidates go to the model as an excerpt of numbered lines.
Model: object_type (the main contract object under 진흥법 제2조) and band_clause (which kind of 대기업 sentence the excerpt has),
plus a verbatim quote. It never decides the violation.
CPU rule: no statement (CPU or model) → self_sw fires unless the model read a plainly non-software object; content_sw fires only
on a software object. Evidence is blank (the required sentence is absent). Without a reading (mock engine's 불명 answer, or a
replay without this journal) the CPU facts decide alone: self_sw fires, content_sw does not.
Design: scratchpad h08/clean3/sw_participation/BLUEPRINT.md (dev dry run TP 5 / FP 1 / FN 0).
"""
import json
import re

FAM = 'd_sw_participation'
PROMPT_TOKEN_LIMIT = 12000
MAX_TOKENS = 200
MAX_LINE_CHARS = 300
HEAD_CHARS = 700            # 공고문 opening shown
QUAL_CHARS = 1200           # 입찰참가자격 section shown
ATTACH_CHARS = 500          # each attachment's opening
OVERVIEW_CHARS = 400        # 과업개요/사업개요 lines of an attachment
TOKEN_GROUPS = 5            # clause-token windows (line ± 1)
CONTENT_HITS = 2            # strong SW-vocabulary hits in the head text that make a content_sw candidate
UNKNOWN = '불명'            # the runtime's unknown value (mock engine): object 'unclear', clause 'no reading'

SW_OBJECTS = ('sw_development', 'sw_maintenance_or_operation', 'sw_license_or_product', 'it_service_data_security',
              'computing_hardware_with_sw_service')
NON_SW_OBJECTS = ('hardware_or_equipment_only', 'non_it_service_or_goods')
OBJECT_TYPES = SW_OBJECTS + NON_SW_OBJECTS + ('unclear', UNKNOWN)
COMPLIANT_CLAUSES = ('states_restriction_applies', 'states_not_applied_with_basis')
BAND_CLAUSES = COMPLIANT_CLAUSES + ('only_cross_shareholding_group', 'only_generic_large_firm_exclusion',
                                    'name_or_citation_only', 'absent', UNKNOWN)
QUOTE_MAX = 240
# Blueprint §5 switch. Literal default: 소기업·소상공인-restricted and 수의계약 SW notices are judged like any other (지침 제3조② has no
# size or procedure exception; DEV-132 is flagged under a 중소기업 restriction). True = such notices are not judged.
EXEMPT_SMALL_FIRM_OR_PRIVATE = False

# ------------------------------------------------------------------------------------------------ text and signals
SW = r'소\s?프\s?트\s?웨\s?어'          # tolerates extraction spacing ("소 프트웨어")
WS_RE = re.compile(r'\s+')
SWQ_TOKEN = re.compile(SW + r'\s*사업자|컴퓨터\s*관련\s*서비스|(?<!\d)1468(?!\d)')
QUAL_CTX = re.compile(r'등록|신고|자격|업종|면허|소지|확인서|보유')
IT_SERVICE_CODE = re.compile(r'(?<!\d)8111\d{6}(?!\d)')
# V20_STAGE_IT_NAME: a listed IT-service 세부품명 written out (정보시스템개발서비스, 정보시스템유지관리서비스, 패키지소프트웨어
# 개발및도입서비스, 정보인프라구축서비스, 데이터처리서비스, 빅데이터분석서비스, 인터넷지원개발서비스, 소프트웨어유지및지원서비스;
# the generic 운영위탁서비스 aside).
IT_SERVICE_NAME = re.compile(r'(정\s*보\s*시\s*스\s*템\s*(개\s*발|유\s*지\s*관\s*리)|패\s*키\s*지\s*소\s*프\s*트\s*웨\s*어\s*개\s*발\s*및\s*도\s*입'
                             r'|정\s*보\s*인\s*프\s*라\s*구\s*축|데\s*이\s*터\s*처\s*리|빅\s*데\s*이\s*터\s*분\s*석|인\s*터\s*넷\s*지\s*원\s*개\s*발'
                             r'|소\s*프\s*트\s*웨\s*어\s*유\s*지\s*및\s*지\s*원)\s*서\s*비\s*스')
STRONG_SW = re.compile(
    r'(시스템|홈페이지|웹\s*사이트|앱|어플리케이션|애플리케이션|플랫폼|솔루션|프로그램|' + SW +
    r'|S/W|SW|DB|데이터베이스|포털|ERP|LMS|CMS|클라우드|챗봇|AI\s*서비스)\s*'
    r'(구축|개발|고도화|개편|개선|유지\s*보수|유지\s*관리|운영\s*(및\s*)?유지|리뉴얼|도입|전환|통합|이전)'
    r'|정보시스템|정보화\s*(사업|전략|계획)|전산\s*(시스템|장비|화)|라이선스|라이센스|'
    + SW + r'\s*(구매|구입|갱신|임차|사용권)|SW\s*(구매|구입|개발)|정보보호\s*(관리체계|컨설팅|서비스)|'
    r'보안관제|취약점\s*(분석|점검)|데이터\s*(구축|분석|플랫폼)|SOFTWARE')
CLAUSE_TOKENS = re.compile(r'대기업|중견기업|하한|제\s*48\s*조|중소\s*' + SW + r'|상호출자')
BAND = re.compile(
    r'사업금액\s*(의)?\s*하한'
    r'|' + SW + r'\s*(산업)?\s*진흥법\s*」?\s*(제\s*)?48\s*조'
    r'|중소\s*' + SW + r'\s*사업자의\s*사업\s*참여\s*지원'
    r'|(20|40|80)\s*억\s*원?\s*(미만|이상)[^.]{0,80}(대기업|중견기업)'
    r'|(대기업|중견기업)[^.]{0,80}(20|40|80)\s*억'
    r'|(대기업|중견기업)[^.]{0,40}' + SW + r'\s*사업자[^.]{0,60}(참여|참가|입찰)[^.]{0,20}(제한|불가|없)'
    r'|' + SW + r'\s*사업자[^.]{0,20}(대기업|중견기업)[^.]{0,60}(참여|참가|입찰)[^.]{0,20}(제한|불가|없)'
    r'|(대기업|중견기업)[^.]{0,60}(참여|참가|입찰)[^.]{0,20}(제한|불가|없)[^.]{0,40}(' + SW + r'|SW|S/W)')
# A match is a statement only with an application verb on its own line or the next one; "참여할 수" is not a verb here because
# the 고시's title reads "…참여할 수 있는 사업금액의 하한" (a bare name in a regulation list goes to the model, DEV-039 rule).
STATEMENT_VERB = re.compile(r'제한|불가|없|적용|가능|준수|배제')
# 상호출자제한기업집단 (제48조④) lines are not the 구간별 statement even when they cite 제48조: they reach the model through
# CLAUSE_TOKENS and are classified only_cross_shareholding_group (blueprint §1 decision table).
SOJA = re.compile(r'상호출자')
BAND_MARK = re.compile(r'하한|(20|40|80)\s*억|중견기업|사업금액|중소\s*' + SW + r'|구간')
HWS_RE = re.compile(r'[ \t　​]+')
SMALL_FIRM = re.compile(r'소기업|소상공인')
QUAL_HEAD = re.compile(r'입찰\s*참가\s*자격|참가\s*자격')
OVERVIEW = re.compile(r'과업\s*개요|사업\s*개요|사업\s*목적|과업\s*목적|용도\s*개요|사업\s*내용|과업\s*내용|용도')


def norm(s):
    return WS_RE.sub(' ', s or '').strip()


def full_text(notice):
    return norm(' \n '.join(d.get('text', '') for d in notice.docs))


def full_lines(notice):
    """The record's non-empty lines, horizontal whitespace collapsed, one per line (the statement check reads by line)."""
    return '\n'.join(HWS_RE.sub(' ', ln.text).strip() for ln in notice.lines if ln.text.strip())


def head_text(notice):
    parts = []
    for d in notice.docs:
        n = 1500 if d.get('type') == '공고문' else 800
        parts.append(norm(d.get('text', '')[:n]))
    return ' '.join(parts)


def meta_codes(notice):
    v = notice.meta.get('세부품명번호목록')
    return re.findall(r'\d{8,10}', str(v)) if v else []


def sw_qualification(notice, full):
    """The notice requires SW사업자 registration (text, or the 나라장터 면허업종제한목록)."""
    if re.search(r'소프트웨어\s*사업자|1468', str(notice.meta.get('면허업종제한목록') or '')):
        return True
    for m in SWQ_TOKEN.finditer(full):
        if m.group(0).endswith('1468') or '컴퓨터' in m.group(0):
            return True
        if QUAL_CTX.search(full[max(0, m.start() - 80): m.end() + 80]):
            return True
    return False


# V20_STAGE_CITATION: a match that is only the law article or the 지침 name is a statement when its own line, with the
# 지침 name, article titles naming no restriction and law-list labels (적용법령·관련법령·근거) removed, has an application
# verb, or when the line wraps (no sentence end) into a next line that is not a new list item and has one.
CITE_ALT = re.compile(SW + r'\s*(산업)?\s*진흥법\s*」?\s*(제\s*)?48\s*조|중소\s*' + SW + r'\s*사업자의\s*사업\s*참여\s*지원')
CITE_STRIP = re.compile(r'[「『｢]?\s*중소\s*' + SW + r'\s*사업자의\s*사업\s*참여\s*지원에\s*관한\s*지침\s*[」』｣]?(\s*\([^)]{0,40}\))?'
                        r'|(?<=조)\s*\((?![^)]*(제한|하한|불가|적용|대기업|중견))[^)]{0,40}\)|(적용|관련|근거)\s*(법령|법규|규정|근거)\s*[:：]?')
CITE_END = re.compile(r'(다|함|음|임|것|요)\s*[.。]?\s*$|[.。)」』]\s*$')
CITE_ITEM = re.compile(r'^\s*([가-하]\s*[.)]|\(?\d{1,2}\s*[.)]|[①-⑳]|[○●◎◦•ㅇ❍□■▶►▷※*\-]|\|)')


def citation_statement(text, m):
    """V20_STAGE_CITATION: whether a citation-only BAND match states the restriction (see CITE_STRIP)."""
    lo = text.rfind('\n', 0, m.start()) + 1
    eol = text.find('\n', m.end())
    eol = len(text) if eol < 0 else eol
    own = CITE_STRIP.sub(' ', text[lo:eol])
    if STATEMENT_VERB.search(own):
        return True
    if eol >= len(text) or CITE_END.search(text[lo:eol]):
        return False
    nxt_end = text.find('\n', eol + 1)
    nxt = text[eol + 1:len(text) if nxt_end < 0 else nxt_end]
    return not CITE_ITEM.match(nxt) and bool(STATEMENT_VERB.search(CITE_STRIP.sub(' ', nxt)[:160]))


def band_status(text):
    """'statement' | 'citation' | 'absent' for the 대기업 참여제한(사업금액 하한) 문구 on line-preserving text (full_lines).
    A statement needs an application verb on the match's line or the next one; a bare name or citation goes to the model;
    a 상호출자제한 line without a band marker is no statement at all."""
    status = 'absent'
    for m in BAND.finditer(text):
        lo = text.rfind('\n', 0, m.start()) + 1
        hi = text.find('\n', m.end())
        hi = len(text) if hi < 0 else text.find('\n', hi + 1)
        win = text[lo:min(len(text) if hi < 0 else hi, m.end() + 160)]
        if SOJA.search(win) and not BAND_MARK.search(win):
            continue
        from .. import switches
        if switches.V20_STAGE_CITATION and CITE_ALT.fullmatch(m.group(0)):
            if citation_statement(text, m):
                return 'statement'
            status = 'citation'
            continue
        if STATEMENT_VERB.search(win):
            return 'statement'
        status = 'citation'
    return status


# V20_STATEMENT_WRAP: the 대기업 participation sentence with the SW law or the 지침 as its basis, read over wrapped lines
# (up to two more non-empty lines of the same document until a sentence end, not across a new list item).
WRAP_BASIS = re.compile(SW + r'\s*(산\s*업\s*)?진\s*흥\s*법|중\s*소\s*' + SW + r'\s*사\s*업\s*자\s*의\s*사\s*업\s*참\s*여\s*지\s*원'
                        r'|대\s*기\s*업\s*인\s*' + SW + r'\s*사\s*업\s*자\s*가\s*참\s*여\s*할\s*수\s*있\s*는\s*사\s*업\s*금\s*액')
WRAP_BAR = re.compile(r'(대\s*기\s*업|중\s*견\s*기\s*업)[^.。]{0,60}?(참\s*여|참\s*가|입\s*찰)[^.。]{0,24}?(제\s*한|불\s*가|없|배\s*제)')
WRAP_END = re.compile(r'(다|함|음|임|것|요)\s*[.。]?\s*$|[.。]\s*$')
WRAP_ITEM = re.compile(r'^\s*([가-하]\s*[.)]|\(?\d{1,2}\s*[.)]|[①-⑳]|[○●◎◦•ㅇ❍□■▶►▷※*\-ｏ]|\|)')


def wrapped_statement(notice):
    lines = notice.lines
    for k, ln in enumerate(lines):
        joined = norm(ln.text)
        if not joined:
            continue
        j, added = k, 0
        while added < 2 and not WRAP_END.search(joined):
            j += 1
            while j < len(lines) and lines[j].doc == ln.doc and not norm(lines[j].text):
                j += 1
            if j >= len(lines) or lines[j].doc != ln.doc or WRAP_ITEM.match(lines[j].text):
                break
            joined += ' ' + norm(lines[j].text)
            added += 1
        if not (WRAP_BASIS.search(joined) and WRAP_BAR.search(joined)):
            continue
        if SOJA.search(joined) and not BAND_MARK.search(joined):
            continue
        return True
    return False


def signals(notice):
    """{'cand': 'self_sw' | 'content_sw' | None, 'band': status or None, 'content_hits': int}."""
    full = full_text(notice)
    codes = meta_codes(notice)
    self_sw = (sw_qualification(notice, full) or bool(IT_SERVICE_CODE.search(full))
               or any(c[:4] in ('8111', '4323') for c in codes))
    hits = len(STRONG_SW.findall(head_text(notice)))
    from .. import switches
    # The notice naming a listed IT-service 세부품명 (a certificate "직접생산확인(정보시스템개발서비스) 증명서", "세부품명:
    # 소프트웨어유지및지원서비스") states its own IT-service object, as its 8111 code does.
    self_sw = self_sw or (switches.V20_STAGE_IT_NAME and bool(IT_SERVICE_NAME.search(full)))
    cand = 'self_sw' if self_sw else ('content_sw' if hits >= switches.V20_STAGE_CONTENT_HITS else None)
    band = band_status(full_lines(notice)) if cand else None
    if band not in (None, 'statement') and switches.V20_STATEMENT_WRAP and wrapped_statement(notice):
        band = 'statement'
    return {'cand': cand, 'band': band, 'content_hits': hits}


def cpu(b):
    """The CPU signals, computed once per bundle."""
    key = FAM + '_cpu'
    sig = getattr(b, key, None)
    if sig is None:
        sig = signals(b.notice)
        setattr(b, key, sig)
    return sig


def small_firm_or_private(b):
    meta = b.notice.meta
    if getattr(getattr(b, 'meta', None), 'private', False) or meta.get('계약방법') == '수의계약':
        return True
    return bool(SMALL_FIRM.search(str(meta.get('조항호내용') or '')))


# ------------------------------------------------------------------------------------------------ excerpt (candidate lines)
def _nonempty(lines):
    return [ln for ln in lines if ln.text.strip()]


def _take(lines, chars):
    out, n = [], 0
    for ln in lines:
        if n >= chars:
            break
        out.append(ln)
        n += min(len(norm(ln.text)), MAX_LINE_CHARS) + 1
    return out


def _qual_lines(notice, chars):
    """The 공고문 입찰참가자격 section (QUAL lines), else the lines after the 참가자격 heading with the most qualification words."""
    gong = [ln for ln in notice.lines if ln.doc_type == '공고문']
    qual = _nonempty([ln for ln in gong if ln.sec == 'QUAL'])
    if qual:
        return _take(qual, chars)
    best, best_score = [], -1
    for k, ln in enumerate(gong):
        if QUAL_HEAD.search(ln.text):
            seg = _take(_nonempty(gong[k:]), chars)
            score = sum(len(QUAL_CTX.findall(x.text)) for x in seg)
            if score > best_score:
                best, best_score = seg, score
    return best


def _token_lines(notice, groups):
    out, last = [], -9
    for ln in notice.lines:
        if len(out) >= groups:
            break
        if ln.i <= last + 1 or not CLAUSE_TOKENS.search(norm(ln.text)):
            continue
        out.append([w for w in notice.window(ln.i, 1, 1) if w.text.strip()])
        last = ln.i
    return out


def _swq_lines(notice):
    return [ln for ln in notice.lines if SWQ_TOKEN.search(norm(ln.text)) or IT_SERVICE_CODE.search(ln.text)][:2]


def _attach_lines(notice, chars, overview_chars):
    out = []
    for k, d in enumerate(notice.docs):
        if d.get('type') == '공고문':
            continue
        lines = _nonempty([ln for ln in notice.lines if ln.doc == k])
        head = _take(lines, chars)
        shown = {ln.i for ln in head}
        rest = [ln for ln in lines if ln.i not in shown]
        for j, ln in enumerate(rest):
            if OVERVIEW.search(ln.text):
                head += [x for x in _take(rest[j:], overview_chars) if x.i not in shown]
                break
        out.append(head)
    return out


def parts(notice, scale=1.0):
    """The excerpt as (label, [Line]) groups; `scale` shrinks the character budgets."""
    gong = _nonempty([ln for ln in notice.lines if ln.doc_type == '공고문'])
    out = [('공고문 앞부분', _take(gong, int(HEAD_CHARS * scale)))]
    qual = _qual_lines(notice, int(QUAL_CHARS * scale))
    if qual:
        out.append(('입찰참가자격 절', qual))
    swq = _swq_lines(notice)
    if swq:
        out.append(('소프트웨어사업자 요건 줄', swq))
    for g in _token_lines(notice, max(1, int(TOKEN_GROUPS * scale))):
        out.append(('대기업/중견기업/하한/제48조/상호출자 언급 부분', g))
    for g in _attach_lines(notice, int(ATTACH_CHARS * scale), int(OVERVIEW_CHARS * scale)):
        if g:
            out.append((f'첨부: {g[0].doc_type} 앞부분', g))
    return [(label, g) for label, g in out if g]


def select(notice, scale=1.0):
    """Candidate lines (the request's cands): every line the excerpt shows, in document order, without duplicates."""
    seen, out = set(), []
    for _label, g in parts(notice, scale):
        for ln in g:
            if ln.i not in seen:
                seen.add(ln.i)
                out.append(ln)
    return sorted(out, key=lambda ln: ln.i)


# ------------------------------------------------------------------------------------------------ prompt and schema
SYSTEM = ('당신은 공공조달 입찰공고 검토자입니다. 아래 발췌문만 근거로 (1) 주된 계약 대상이 무엇인지, (2) 대기업 참여제한 문구가 어떤 종류로 '
          '적혀 있는지를 분류합니다. 위반 여부는 판단하지 않습니다. 출력은 JSON 하나입니다.')

GUIDE = """[판정 1] 주된 계약 대상(object_type)
「소프트웨어 진흥법」 제2조: "소프트웨어사업"은 소프트웨어의 개발·제작·생산·유통·운영·유지관리와 그 밖에 소프트웨어 관련 서비스를 제공하는 경제활동입니다.
다음 다섯 가지는 소프트웨어사업입니다.
- sw_development: 시스템·홈페이지·앱·플랫폼 등의 개발·구축·고도화
- sw_maintenance_or_operation: 정보시스템·홈페이지·소프트웨어의 유지보수·유지관리·운영
- sw_license_or_product: 소프트웨어 제품·라이선스·사용권의 구매·갱신·임차
- it_service_data_security: 정보보호·보안관제·IT 컨설팅·데이터 구축/분석 등 소프트웨어 관련 서비스
- computing_hardware_with_sw_service: 전산기기(PC·서버·네트워크 장비)의 구매·임차·정비에 소프트웨어 설치·유지보수 등 서비스가 결합된 사업
다음 둘은 소프트웨어사업이 아닙니다.
- hardware_or_equipment_only: 계측기·설비·차량·장치 등 장비 구매 (내장 소프트웨어가 있어도 대상은 장비)
- non_it_service_or_goods: 정보기술과 무관한 용역·물품 (교육, 행사, 인쇄, 청소, 연구, 시약 등)
발췌문으로 대상을 알 수 없으면 "unclear".
공고가 소프트웨어사업자 등록이나 정보기술 서비스 직접생산확인을 참가자격으로 요구하더라도 대상이 무엇인지는 사업 내용(건명·과업개요·규격)으로 판단하십시오.

[판정 2] 대기업 참여제한 문구(band_clause)
「소프트웨어 진흥법」 제48조제2항과 「중소 소프트웨어사업자의 사업 참여 지원에 관한 지침」 제2조·제3조: 소프트웨어사업 공고에는 사업금액(20억·40억·80억 구간)에 따른 대기업(중견기업 포함) 참여제한 하한제도의 적용 여부(근거 포함)를 명시해야 합니다.
- states_restriction_applies: 이 사업이 사업금액 하한/구간에 따라 대기업(중견기업)의 참여를 제한한다고 명시 (예: "20억원 미만 사업으로 대기업 및 중견기업 소프트웨어사업자 참여 불가", "사업금액의 하한에 따라 대기업 참여 제한")
- states_not_applied_with_basis: 하한제도를 적용하지 않는다고 근거와 함께 명시 (예외사업, 분리발주 등)
- only_cross_shareholding_group: 상호출자제한기업집단 소속회사 배제만 있고 사업금액 구간 문구는 없음
- only_generic_large_firm_exclusion: 소기업·소상공인/중소기업 제한 등 다른 근거로 "대기업 참여 불가"만 있고 소프트웨어사업 하한제도 언급은 없음
- name_or_citation_only: 고시명·법조문·서류명(예: 소프트웨어사업자 확인서)만 나열되고 참여제한 적용 여부 문장은 없음
- absent: 관련 문구가 전혀 없음
band_clause_quote에는 판정 근거 문장을 발췌문에서 그대로 240자 이내로 옮기고, absent이면 빈 문자열로 두십시오.
발췌문에 없는 내용을 추측하지 마십시오. "불명"은 고르지 마십시오."""

TASK = ('출력 형식: {"object_type": "...", "band_clause": "...", "band_clause_quote": "..."}\nJSON으로만 답하십시오.')


def messages(b, scale=1.0):
    notice = b.notice
    meta = notice.meta
    header = ' / '.join(f'{k}={meta.get(k)}' for k in ('업무구분', '계약방법', '배정예산금액', '조항호내용', '면허업종제한목록', '세부품명번호목록')
                        if meta.get(k) not in (None, ''))
    title = (b.titles[0] if getattr(b, 'titles', None) else '')[:80]
    if title:
        header = f'제목={title} / ' + header
    body = []
    for label, g in parts(notice, scale):
        body.append(f'=== {label} ===')
        body += [norm(ln.text)[:MAX_LINE_CHARS] for ln in g]
    user = f'{GUIDE}\n\n[나라장터 메타] {header}\n\n' + '\n'.join(body) + f'\n\n{TASK}'
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}]


def schema():
    return {'type': 'object', 'additionalProperties': False, 'required': ['object_type', 'band_clause', 'band_clause_quote'],
            'properties': {'object_type': {'type': 'string', 'enum': list(OBJECT_TYPES)},
                           'band_clause': {'type': 'string', 'enum': list(BAND_CLAUSES)},
                           'band_clause_quote': {'type': 'string', 'maxLength': QUOTE_MAX}}}


def needs_model(sig):
    return sig['cand'] is not None and sig['band'] != 'statement'


def request(engine, b, k, request_cls):
    """One request per candidate without a CPU statement; the excerpt shrinks by 75% steps to the token limit."""
    sig = cpu(b)
    if not needs_model(sig):
        return None
    scale = 1.0
    while True:
        cands = select(b.notice, scale)
        if not cands:
            return None
        ids = engine.token_ids(messages(b, scale))
        if len(ids) + MAX_TOKENS <= min(PROMPT_TOKEN_LIMIT, engine.max_model_len - 64):
            break
        if scale > 0.1:
            scale *= 0.75
        else:
            raise ValueError(f'{FAM}: minimum reading request exceeds the engine token limit')
    return request_cls(k, FAM, cands, (), ids, schema(), MAX_TOKENS, 0)


# ------------------------------------------------------------------------------------------------ consume and verdict
def consume(b, cands, text):
    """Parse the model's JSON, validate it against schema(), store b.d_sw_participation. False = invalid (retry).
    The mock engine's 불명 answer is a valid reading that decides nothing (the CPU facts decide)."""
    try:
        obj = json.loads(text.split('<channel|>', 1)[-1])
    except (ValueError, AttributeError, TypeError):
        return False
    if not isinstance(obj, dict) or set(obj) != {'object_type', 'band_clause', 'band_clause_quote'}:
        return False
    if obj['object_type'] not in OBJECT_TYPES or obj['band_clause'] not in BAND_CLAUSES:
        return False
    if not isinstance(obj['band_clause_quote'], str) or len(obj['band_clause_quote']) > QUOTE_MAX:
        return False
    setattr(b, FAM, {'object_type': obj['object_type'], 'band_clause': obj['band_clause'],
                     'quote': obj['band_clause_quote'].strip(), 'shown': [ln.i for ln in cands]})
    return True


def decide(sig, reading):
    """True (violation, blank evidence) or None. `reading` may be None (no model answer)."""
    if sig['cand'] is None or sig['band'] == 'statement':
        return None
    obj = reading['object_type'] if reading else UNKNOWN
    clause = reading['band_clause'] if reading else UNKNOWN
    if clause in COMPLIANT_CLAUSES:
        return None
    from .. import switches
    if sig['cand'] == 'self_sw' and switches.V20_STAGE_SELF_SW_HW and obj == 'hardware_or_equipment_only':
        return True
    if sig['cand'] == 'content_sw' and switches.V20_STAGE_CONTENT_HW and obj in ('hardware_or_equipment_only', 'unclear', UNKNOWN):
        return True
    if sig['cand'] == 'self_sw':
        # The notice's own SW사업자/IT-service requirement establishes the SW project (organizer on DEV-132); the model may
        # override it only when the object is plainly not software (진흥법 제2조).
        return None if obj in NON_SW_OBJECTS else True
    return True if obj in SW_OBJECTS else None


def verdict(b):
    """True when the required statement is absent from an SW project; None otherwise. Without a reading the CPU facts decide
    alone (self_sw fires, content_sw does not)."""
    if EXEMPT_SMALL_FIRM_OR_PRIVATE and small_firm_or_private(b):
        return None
    return decide(cpu(b), getattr(b, FAM, None))
