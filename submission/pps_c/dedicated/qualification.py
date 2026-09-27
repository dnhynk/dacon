"""v1 / v4 / v8 as one dedicated stage (switches.DEDICATED = ('v1', 'v4', 'v8'); MODULES 'qualification:A/B/C').

  A (v1) 참가자격 특정기관 제한: participation limited to a kind of organisation (대학·산학협력단·연구기관·협회 회원사 …) that
         shuts out ordinary businesses, or a 시설·인력·장비 보유 condition beyond the task's need (전국 수리센터, 보안인력 50명,
         특정 지역 소재 시설 직접 보유); holding conditions are exempt under 수의계약 (소액수의 견적: 지방 집행기준 제5장), when
         they restate a 법령·허가기준, or when the task plainly needs them / 임차 is allowed.
  B (v4) 특정기관·특정실적: a participation 실적 requirement limited to public-sector / specific issuers' 실적 ("국가기관이 발주한",
         "[수요기관]에서 발주한", "대학병원에 납품한"; not "… 또는 민간") or to a specifically named 실적 narrower than the
         contract's kind. No amount gate, no 소액수의 exemption (집행기준 제2장 §5 ④ 2·3; 지방 집행기준 제1장 7.나 5)·6)).
  C (v8) 실적 + 지역 중복제한: a participation 실적 requirement together with a 본점·주된 영업소 소재지 requirement
         (시행규칙 §25 ⑤/⑦; 지방 집행기준 제4장 제3절 1.나), except 지방계약법 ∧ 계약방법 = 수의계약 (제5장 제3절 1.나 7)).

CPU: the 공고문 qualification block (runtime section map, else a heading-style-aware block) plus 실적/지역/기관/보유 cue lines
(공고문 anywhere; attachments only when the line itself speaks of 참가자격), tagged with document·section and a 서식/붙임 zone.
The model is called only when a 실적, 기관 or 보유 cue exists and extracts facts (which line is a participation requirement,
issuer / named scope of the 실적, whether businesses are excluded, proportionality of a holding condition). The CPU decides
A/B/C from those fields and the meta exemptions. Without a reading (mock, replay, invalid output, 불명 fields) the regex proxy
of the blueprint stands in for the model, field by field.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace

from .. import families as F

FAM = 'd_qualification'
PROMPT_TOKEN_LIMIT = 12000
MAX_TOKENS = 200
UNKNOWN = F.UNKNOWN
MAX_LINES = 60
MIN_LINES = 8
LINE_CHARS = 300
SECTION_MAX = 45
NONE_LINE = -1

ISSUER_ENUM = ('공공기관등만', '특정발주처', '제한없음_또는_민간포함', '해당없음', UNKNOWN)
NAMED_ENUM = ('계약목적물과_같은_종류_전반', '특정_명칭·대상·조건으로_한정', '해당없음', UNKNOWN)
EXCL_ENUM = ('예', '아니오', '해당없음', UNKNOWN)
HOLD_ENUM = ('법령·허가기준', '과업수행에_필수·통상_수준', '과업_필요를_넘는_수준', '해당없음', UNKNOWN)
LINE_FIELDS = ('perf_req_line', 'region_req_line', 'institution_line', 'holding_line')
ENUM_FIELDS = {'perf_issuer_scope': ISSUER_ENUM, 'perf_named_scope': NAMED_ENUM,
               'institution_excludes_business': EXCL_ENUM, 'holding_basis': HOLD_ENUM}
REQUIRED = ('perf_req_line', 'perf_issuer_scope', 'perf_named_scope', 'region_req_line',
            'institution_line', 'institution_excludes_business', 'holding_line', 'holding_basis')

# ---------------------------------------------------------------- candidate lines
HEAD_RE = re.compile(r'(참가\s*자격|참가자의\s*자격|응찰\s*자격|참여\s*자격|자격\s*요건|참가\s*제한)')
HEAD_EXCL = re.compile(r'등록규정|제한기간|없음|없습니다|처분|유의사항|증빙|증명|서류|등록증|등록\s*마감|관련\s*안내|등록\s*안내|판단기준일|조세포탈|공지사항')
TOP_NUM = re.compile(r'^\s*(\d{1,2}|[ⅠⅡⅢⅣⅤ]+|[IVX]+)\s*[\.\)]\s*\S')
SYM_HEAD = re.compile(r'^\s*([□■○●◇◆◎▣▶►☞※])\s*\S')
FORM_HEAD = re.compile(r'^\s*[\[【<(〔]?\s*(서식|별지|별첨|붙임)\s*(제?\s*\d+|\d+)?\s*(호)?\s*[\]】>)〕]?')

REQM = re.compile(r'이상|있는\s*(업체|자|기관|사업자)|있어야|보유한|보유하고|갖춘|갖추|이어야|여야|한함|한합니다|한정|만\s*(참|입찰|가능)|자격이\s*있|충족|제한|가능함|가능합니다|둔\s*(업체|자|사업자)|소재한|소재하고')
SCORE = re.compile(r'배점|평가|점수|가점|감점|평점|만점|채점|심사기준|심사항목|평가항목|평가기준|산식|등급|동등이상용역|동등\s*이상\s*용역|PQ|사전심사|이행\s*완료\s*된\s*시점|이행완료된\s*시점')
DOCLIST = re.compile(r'증명서\s*(사본\s*)?\d*\s*(부|통)|각\s*\d+\s*부|\d+\s*부\.?\s*$|(증명서|확인서|증명원)\s*[\.\)]?\s*$|서식|제출서류|첨부|증빙|원본으로\s*제출|실적증명서는|반드시\s*명기|명기\)')
TASKTXT = re.compile(r'보고|기록|검사|일지|정산|감독|수급인|계약상대자는|낙찰자는|착수|준공\s*후|납품\s*후|계약체결\s*후|계약\s*후|지급|대가|검수')

PERF = re.compile(r'실적')
REGION = re.compile(r'본점\s*소재지|본점(이|을)|주된\s*영업(소|장)|주\s*사무소|소재지(를|가|는)|에\s*소재한|소재하고|관내에|에\s*둔\s|을\s*둔\s|를\s*둔\s|지역에\s*소재|(시|도|군|구)\s*내에\s*(둔|소재|있)|지역업체|관할구역\s*안')
BUYER_TOKEN = re.compile(r'\[수요기관\([^\]]*\)[^\]]*\]')   # anonymised buyer name: never a participant restriction
INST = re.compile(r'대학(교)?|산학협력단|연구기관|연구소|국공립|공공기관|\[기관\((대학|공공기관|연구|공공시설|병원)[^\]]*\)\]|협회\s*(회원|정회원)|회원사|조합원|학회|재단')
INST_MARK = re.compile(r'만\s*(참여|참가|입찰|가능|응찰)|에\s*한하여|에\s*한함|에\s*한합니다|만이\s*(참|입찰)|참여\s*가능|참가\s*가능|가능함$|가능합니다\.?$|가능$')
INST_EXCL = re.compile(r'부정당|퇴직자|임직원|임\s*[ㆍ·]|임명한|재직|대리인|전자입찰|이용자\s*등록|등록을\s*한|등록한\s*자만|신고|홈페이지|회신|담합|보완|서류|문의|요청|열람|교부|제출|실적|주소|장소|일시|방문|강사|투입|배치|인력|등\s*(입찰\s*)?참(가|여)\s*가능|도\s*참(가|여)|참여하는\s*경우|현황')
HOLD = re.compile(r'(시설|장비|인력|센터|차량|사무소|지사|지점|공장|기계|설비|본부|창고|점포|대리점|A/S|AS\s*망|수리)')
HOLD_MARK = re.compile(r'보유|소유|갖춘|갖추|구비|설치|운영\s*중|있는\s*(업체|자)')
HOLD_EXCL = re.compile(r'확인서|증명서|인증서|등록증|부정당|제재|콜센터|고객지원센터|전산장비|공동수급|보증|납품\s*후|계약체결\s*후|계약상대자는|낙찰자는|하수급인|자재·장비업자|과업|숙박|침실|운행개시|신고하여야|고용|승계|입찰대리인|현황|제안서|실적')
QUAL_WORD = re.compile(r'참가\s*자격|입찰\s*참가|참가\s*등록|참여\s*자격|입찰에\s*참')


def line_cues(s):
    """Cue names carried by one stripped line."""
    cues = set()
    if len(s) < 8 or len(s) > 600:
        return cues
    if PERF.search(s) and REQM.search(s) and not SCORE.search(s):
        cues.add('PERF')
    if REGION.search(s) and not SCORE.search(s) and re.search(r'업체|자|사업자|법인|제한|둔|소재|있어야|이어야|여야', s):
        cues.add('REGION')
    s2 = BUYER_TOKEN.sub('', s)
    if INST.search(s2) and INST_MARK.search(s2) and not INST_EXCL.search(s2):
        cues.add('INST')
    if HOLD.search(s) and HOLD_MARK.search(s) and REQM.search(s) and not HOLD_EXCL.search(s) and not SCORE.search(s):
        cues.add('HOLD')
    return cues


def heading_block(lines, max_lines=SECTION_MAX):
    """Fallback qualification block (list of Lines) found by heading when the runtime section map has no QUAL lines:
    from the heading to the next heading of the same numbering style with a larger number."""
    for k, ln in enumerate(lines):
        s = ln.text.strip()
        if 3 < len(s) < 45 and HEAD_RE.search(s) and not HEAD_EXCL.search(s):
            m_sym = SYM_HEAD.match(s)
            m_num = re.match(r'^\s*(\d{1,2})\s*([\.\)])', s)
            out, n = [ln], 0
            for nx in lines[k + 1:k + 400]:
                t = nx.text.strip()
                if not t:
                    continue
                if m_num:
                    m2 = re.match(r'^\s*(\d{1,2})\s*([\.\)])\s*\S', t)
                    stop = bool(m2 and m2.group(2) == m_num.group(2) and int(m2.group(1)) > int(m_num.group(1)))
                elif m_sym:
                    stop = bool(SYM_HEAD.match(t) and SYM_HEAD.match(t).group(1) == m_sym.group(1))
                else:
                    stop = bool(TOP_NUM.match(t))
                if n > 1 and stop:
                    break
                out.append(nx)
                n += 1
                if n >= max_lines:
                    break
            return out
    return []


def form_zone(notice):
    """Line ids after the first 서식/별지/붙임 heading of their document (appended forms)."""
    ids, start = set(), {}
    pos = {}
    for ln in notice.lines:
        pos[ln.doc] = pos.get(ln.doc, -1) + 1
        if ln.doc not in start and pos[ln.doc] > 20 and FORM_HEAD.match(ln.text.strip()):
            start[ln.doc] = ln.i
        if ln.doc in start and ln.i >= start[ln.doc]:
            ids.add(ln.i)
    return ids


def section_lines(notice):
    gong = notice.notice_lines()
    qual = [ln for ln in gong if ln.sec == 'QUAL' and ln.text.strip()]
    if qual:
        return qual[:SECTION_MAX]
    return [ln for ln in heading_block(gong) if ln.text.strip()]


def infos(notice, cap=MAX_LINES):
    """[{'line', 'where', 'cues', 'zone'}] — the qualification block first, then cue lines of the 공고문, then attachment cue
    lines that speak of 참가자격; capped, deterministic (recomputable without the request)."""
    zone = form_zone(notice)
    out, seen = [], set()

    def add(ln, where, cues):
        if ln.i in seen:
            return
        seen.add(ln.i)
        out.append({'line': ln, 'where': where, 'cues': sorted(cues), 'zone': 'form' if ln.i in zone else 'body'})

    for ln in section_lines(notice):
        add(ln, 'section', line_cues(ln.text.strip()))
    for ln in notice.notice_lines():
        s = ln.text.strip()
        c = line_cues(s)
        if c:
            add(ln, 'cue', c)
    for ln in notice.lines:
        if ln.doc_type == '공고문':
            continue
        s = ln.text.strip()
        c = line_cues(s)
        if c and QUAL_WORD.search(s):
            add(ln, 'other', c)
    order = {'section': 0, 'cue': 1, 'other': 2}
    out.sort(key=lambda d: (order[d['where']], d['line'].i))
    return out[:cap]


def cands(notice, cap=MAX_LINES):
    return [d['line'] for d in infos(notice, cap)]


def needs_model(inf):
    """CPU gate: a 실적, 기관 or 보유 cue (region alone never fires)."""
    return any(set(d['cues']) & {'PERF', 'INST', 'HOLD'} for d in inf)


# ---------------------------------------------------------------- prompt and schema
SYSTEM = ("당신은 공공조달 입찰공고를 읽고 '입찰참가자격 요건'을 사실대로 추출하는 검토관입니다. 위법 여부는 판단하지 않습니다.\n"
          "아래 [후보 줄]은 공고문의 입찰참가자격 부분과 관련 줄을 번호(L#)로 나열한 것입니다.\n"
          "'참가자격 요건'이란 그 조건을 갖추지 못하면 입찰(견적 제출)에 참가할 수 없는 조건입니다.\n"
          "제안서 평가·적격심사 배점, 실적 인정 범위 안내, 제출서류 목록, 서식·붙임 영역의 문구, 과업 수행 중 의무는 참가자격 요건이 아닙니다.\n"
          "같은 종류의 줄이 여러 개면 가장 명확한 한 줄을 고르십시오. 없으면 줄 번호는 -1, 항목은 \"해당없음\"으로 답하십시오. "
          "발췌만으로 알 수 없으면 \"불명\"을 고르십시오.\n\n"
          "추출 항목\n"
          "1) perf_req_line: 일정 실적(수행·납품·운행 실적 등)을 참가자격 요건으로 요구하는 줄 번호.\n"
          "   perf_issuer_scope: 그 실적의 발주처 범위.\n"
          "     \"공공기관등만\"   = 국가·지자체·공공기관·공기업 등 공공부문 발주 실적만 인정(민간 실적 불인정)\n"
          "     \"특정발주처\"     = 수요기관 자신, 대학병원, 특정 기관명 등 특정 발주처의 실적만 인정\n"
          "     \"제한없음_또는_민간포함\" = 발주처를 한정하지 않거나 민간 실적도 인정\n"
          "   perf_named_scope: 실적의 종류 범위.\n"
          "     \"계약목적물과_같은_종류_전반\" = 이 계약과 같은 종류의 실적을 폭넓게 인정(예: 행사대행 실적, 디자인 용역 실적)\n"
          "     \"특정_명칭·대상·조건으로_한정\" = 특정 명칭·대상·조건의 실적만 인정하여 같은 종류의 유사 실적을 배제\n"
          "       (예: \"중고등학생 대상 숙박 포함 국내여행 실적\", \"○○박람회 실적\", 특정 모델 납품 실적)\n"
          "2) region_req_line: 법인등기부상 본점·주된 영업소(사업장) 소재지를 특정 시·도/시·군 등으로 요구하는 줄 번호\n"
          "   (지역업체 가점·우대는 제외).\n"
          "3) institution_line: 입찰참가자를 특정 종류의 기관으로 한정하는 줄 번호\n"
          "   (예: \"대학·산학협력단만 참여 가능\", \"국공립연구기관·공공기관 가능\", \"○○협회 회원사에 한함\").\n"
          "   법령에 따른 허가·인가·면허·등록·신고(예: 매장유산 조사기관 등록, 요양보호사교육기관 등록, 업종 등록)나\n"
          "   중소기업·소상공인 확인서 요건은 기관 한정이 아닙니다.\n"
          "   institution_excludes_business: 그 줄이 일반 사업자(회사·업체)의 참여를 배제하면 \"예\",\n"
          "   \"대학·연구기관 또는 컨설팅회사\"처럼 일반 회사도 허용하면 \"아니오\".\n"
          "4) holding_line: 시설·인력·장비·차량·사무소·수리센터 등의 보유를 참가자격 요건으로 요구하는 줄 번호.\n"
          "   holding_basis:\n"
          "     \"법령·허가기준\" = 법령·허가기준(별표, 시행규칙 기준 등)이 요구하는 수준을 그대로 요구\n"
          "     \"과업수행에_필수·통상_수준\" = 이 과업 수행에 당연히 필요한 정도(예: 급식 운반용 냉장차량 보유 또는 임차,\n"
          "        통학버스 운행 대수만큼의 차량, 수용 인원 조건, 임차·협력으로 충족 가능)\n"
          "     \"과업_필요를_넘는_수준\" = 과업 규모를 넘는 수량(예: 한 병원 응급실 경비에 보안인력 50명 이상),\n"
          "        전국·모든 광역단체 단위의 시설망, 특정 지역에 소재한 시설의 직접 보유(임차 불허) 등\n\n"
          "반드시 아래 JSON 스키마대로만 답하십시오.")


def shown(text):
    t = text.strip()
    return t if len(t) <= LINE_CHARS else t[:LINE_CHARS] + '…'


def excerpt(inf):
    rows = []
    for d in inf:
        ln = d['line']
        tag = f"{ln.doc_type}·{F.SECTION_NAME.get(ln.sec, ln.sec)}" + ('·서식/붙임 영역' if d['zone'] == 'form' else '')
        rows.append(f'L{ln.i} ({tag}) {shown(ln.text)}')
    return '\n'.join(rows)


def title_of(b):
    return (b.titles[0] if getattr(b, 'titles', None) else '')[:80]


def messages(b, inf):
    m = b.notice.meta
    user = ('[공고 개요]\n'
            f'제목: {title_of(b)}\n'
            f'적용계약법={m.get("적용계약법")}, 계약방법={m.get("계약방법")}, 낙찰방법={m.get("낙찰방법")}, 업무구분={m.get("업무구분")}, '
            f'추정가격={m.get("입찰추정가격")}, 지역제한여부={m.get("지역제한여부")}({m.get("제한지역코드목록")}), '
            f'업종제한={m.get("면허업종제한목록")}\n\n'
            '[후보 줄]\n' + excerpt(inf) + '\n\n[출력] 위 스키마의 JSON 객체 하나만 출력.')
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}]


def schema():
    line = {'type': 'integer', 'minimum': NONE_LINE, 'maximum': 100000}
    return {'type': 'object', 'additionalProperties': False, 'required': list(REQUIRED),
            'properties': {'perf_req_line': line,
                           'perf_issuer_scope': {'type': 'string', 'enum': list(ISSUER_ENUM)},
                           'perf_named_scope': {'type': 'string', 'enum': list(NAMED_ENUM)},
                           'region_req_line': line,
                           'institution_line': line,
                           'institution_excludes_business': {'type': 'string', 'enum': list(EXCL_ENUM)},
                           'holding_line': line,
                           'holding_basis': {'type': 'string', 'enum': list(HOLD_ENUM)}}}


def request(engine, b, k, request_cls):
    """None when the gate drops the notice or there is no candidate; else one reading request. Over the token limit the
    candidate cap shrinks by 25 % steps (section lines are kept first), then meta values are shortened."""
    inf = infos(b.notice)
    if not inf or not needs_model(inf):
        return None
    cap, view = MAX_LINES, b
    while True:
        shown_inf = infos(view.notice, cap)
        ids = engine.token_ids(messages(view, shown_inf))
        if len(ids) + MAX_TOKENS <= min(PROMPT_TOKEN_LIMIT, engine.max_model_len - 64):
            break
        if cap > MIN_LINES:
            cap = max(MIN_LINES, int(cap * 0.75))
        elif view is b:
            view = replace(b, notice=replace(b.notice, meta={key: None if value is None else F.shown(str(value))
                                                           for key, value in b.notice.meta.items()}))
        elif cap > 1:
            cap = max(1, int(cap * 0.75))
        else:
            raise ValueError('qualification: minimum reading request exceeds the engine token limit')
    return request_cls(k, FAM, [d['line'] for d in shown_inf], (), ids, schema(), MAX_TOKENS, 0)


# ---------------------------------------------------------------- regex proxy (CPU stand-in for the model, field by field)
PUBLIC_ISSUER = re.compile(r'국가기관|공공기관|지방자치단체|지자체|정부투자기관|정부기관|정부\s*\(|공기업|관공서|국가\s*[,·및]|국가에서|\[수요기관|수요기관|대학병원|병원에|학교에|교육청|공공\s*발주')
ISSUE_VERB = re.compile(r'발주|납품|시행|수행|공급|운행|이행|계약')
PRIVATE_OK = re.compile(r'민간|민자|발주처\s*(무관|불문)|기관\s*무관|기업체|일반\s*기업|사기업|기업\s*등|일반\s*업체')
NAMED = re.compile(r'(학생|어린이|청소년|노인|장애인|군인|공무원|유아)\s*(을|를)?\s*대상|숙박이\s*(들어간|포함된)|특정\s*모델|박람회\s*실적')
LAW_NAME = re.compile(r'「[^」]*」|(지방자치단체|국가)를\s*당사자로\s*하는\s*계약에\s*관한\s*법률(\s*시행령|\s*시행규칙)?|지방계약법|국가계약법')
LEGAL_BASIS = re.compile(r'법\s*제\d+조|시행규칙|별표|허가기준|등록기준|법령|기준을\s*충족|기준에\s*적합')
LEASE_OK = re.compile(r'임차|임대|협력')
EXCESS = re.compile(r'전국|모든\s*(광역|시\s*·?\s*도|지역)|(인력|직원|기술자|기사|요원|근로자|인원)(을|를)?\s*\d{2,}\s*(명|인)\s*이상|\d{2,}\s*(명|인)\s*이상의\s*(인력|직원|기술자|보안|경비|요원|기사)|\d{2,}\s*개소')
CAPACITY = re.compile(r'수용|참가인원|정원|좌석')
REGION_FACILITY = re.compile(r'(지역|도|시|군|구)(에|내에)\s*소재한\s*\S*\s*(시설|사무소|공장|센터|지사|지점)(을|를)\s*(보유|소유)')
REGION_MARK = re.compile(r'(둔|소재한|소재하고|위치한|있는|인)\s*(업체|자|사업자|법인)|이어야|여야|제한하며|로\s*제한|내에\s*있|에\s*있는\s*업체|소재지가\s*[가-힣]+(시|도)')
REGION_SAME = re.compile(r'본점|주된\s*영업|본사|소재를\s*둔|소재지(가|를)|[가-힣]{2,6}에\s*소재한\s*(?!요양원|시설|기관|병원|학교|복지)')
PERF_STRONG = re.compile(r'(업체|사업자|기관|법인|자)\s*(이어야|여야|만|로서|에\s*한|가\s*있|참가|참여|를\s*대상|로\s*제한)|이상(의|인)?\s*(실적|\S+\s*실적)|실적(이|을)\s*(있는|있어야|보유한|보유하고|가진|충족)')
PERF_REQ = re.compile(r'이상|있는\s*(업체|자|사업자)|있어야|보유한|보유하고|보유한\s*자|충족|한함|자격이\s*있|우수한\s*업체')


def issuer_scope(s):
    s2 = LAW_NAME.sub('', s)
    win = s2[:s2.find('실적')][-70:] if '실적' in s2 else s2
    if PRIVATE_OK.search(win):
        return '제한없음_또는_민간포함'
    if PUBLIC_ISSUER.search(win) and ISSUE_VERB.search(win + s2[s2.find('실적'):s2.find('실적') + 8]):
        return '특정발주처' if re.search(r'\[수요기관|수요기관|대학병원', win) else '공공기관등만'
    return '제한없음_또는_민간포함'


def named_scope(s):
    return '특정_명칭·대상·조건으로_한정' if NAMED.search(s) else '계약목적물과_같은_종류_전반'


def excludes_business(s):
    s = BUYER_TOKEN.sub('', s)
    return '아니오' if re.search(r'\(회사\)|회사|기업|업체\s*(또는|이나|,)|사업자\s*(또는|이나)', s) else '예'


def holding_basis(s):
    if LEGAL_BASIS.search(s):
        return '법령·허가기준'
    if LEASE_OK.search(s):
        return '과업수행에_필수·통상_수준'
    if (EXCESS.search(s) and not CAPACITY.search(s)) or REGION_FACILITY.search(s):
        return '과업_필요를_넘는_수준'
    return '과업수행에_필수·통상_수준'


def _is_perf_req(d):
    s = d['line'].text.strip()
    if '실적' not in re.sub(r'실적\s*증명서|실적증명원|실적\s*확인서', '', s):
        return False
    if DOCLIST.search(s) and not re.search(r'이상(의|인)\s*(\S+\s*)?실적|실적(이|을)\s*(있는|보유)', s):
        return False
    if TASKTXT.search(s) and not re.search(r'실적(이|을)\s*(있는|보유|가진)|이상(의|인)\s*실적', s):
        return False
    if not PERF_REQ.search(s):
        return False
    if d['where'] == 'other' and not QUAL_WORD.search(s):
        return False
    if d['where'] != 'section' and not PERF_STRONG.search(s):
        return False
    if d['zone'] == 'form' and not QUAL_WORD.search(s):
        return False
    if d['where'] != 'section' and d['line'].sec in ('EVAL', 'DOCS') and not QUAL_WORD.search(s):
        return False
    return True


def _next_text(inf, d):
    for e in inf:
        if e['line'].doc == d['line'].doc and e['line'].i in (d['line'].i + 1, d['line'].i + 2):
            return e['line'].text.strip()
    return ''


def proxy(inf):
    """Schema-shaped reading from the regexes of the blueprint (no model)."""
    out = {'perf_req_line': NONE_LINE, 'perf_issuer_scope': '해당없음', 'perf_named_scope': '해당없음',
           'region_req_line': NONE_LINE, 'institution_line': NONE_LINE, 'institution_excludes_business': '해당없음',
           'holding_line': NONE_LINE, 'holding_basis': '해당없음'}
    for d in inf:
        if 'PERF' in d['cues'] and _is_perf_req(d):
            s = d['line'].text.strip()
            out['perf_req_line'] = d['line'].i
            out['perf_issuer_scope'] = issuer_scope(s)
            out['perf_named_scope'] = named_scope(s)
            break
    for d in inf:
        if 'REGION' not in d['cues'] or d['line'].doc_type != '공고문':
            continue
        s = d['line'].text.strip() + ' ' + _next_text(inf, d)
        if re.search(r'지역제한\s*없|전국을\s*대상|지역\s*무관|가점|우대', s):
            continue
        same = 'PERF' in d['cues'] and REGION_SAME.search(d['line'].text)
        if same or REGION_MARK.search(s):
            out['region_req_line'] = d['line'].i
            break
    for d in inf:
        if 'INST' not in d['cues']:
            continue
        s = BUYER_TOKEN.sub('', d['line'].text.strip())
        if re.search(r'등록(한|을\s*필한|된)\s*(업체|자)|허가|면허|신고를\s*필|확인서', s) and not re.search(r'만\s*(참여|참가|입찰)|만이', s):
            continue
        if d['where'] != 'section' and not re.search(r'만\s*(참여|참가|입찰|가능|응찰)|만이|에\s*한(하여|함|합니다)|한정', s):
            continue
        if d['where'] != 'section' and not re.search(r'참여|참가|입찰|응찰|한함|한하여|한정', s):
            continue
        out['institution_line'] = d['line'].i
        out['institution_excludes_business'] = excludes_business(s)
        break
    for d in inf:
        if 'HOLD' not in d['cues']:
            continue
        s = d['line'].text.strip()
        out['holding_line'] = d['line'].i
        out['holding_basis'] = holding_basis(s)
        if out['holding_basis'] == '과업_필요를_넘는_수준':
            break
    return out


# ---------------------------------------------------------------- consume
def consume(b, cands_, text):
    """Validate the model's JSON against the stage schema and store it as b.d_qualification; False when invalid (retry).
    Line numbers not among the shown lines become -1; the mock's all-불명 / -1 answer is valid and leaves the CPU proxy in
    charge."""
    try:
        obj = json.loads(text.split('<channel|>', 1)[-1])
    except (ValueError, AttributeError):
        return False
    if not isinstance(obj, dict) or any(key not in obj for key in REQUIRED):
        return False
    if any(type(obj[f]) is not int for f in LINE_FIELDS):
        return False
    if any(obj[f] not in enum for f, enum in ENUM_FIELDS.items()):
        return False
    shown_ids = {ln.i for ln in cands_}
    reading = {f: (obj[f] if obj[f] in shown_ids else NONE_LINE) for f in LINE_FIELDS}
    reading.update({f: obj[f] for f in ENUM_FIELDS})
    setattr(b, FAM, reading)
    return True


# ---------------------------------------------------------------- verdict
def _line(notice, inf, i, must):
    """The candidate Line for id i when it carries the expected cue (CPU check against hallucinated ids), else None."""
    if not isinstance(i, int) or i < 0 or i >= len(notice.lines):
        return None
    for d in inf:
        if d['line'].i == i:
            return d['line'] if must.search(d['line'].text) else None
    return None


def reading(b, inf):
    """Effective reading: the model's fields where they are known, the proxy elsewhere. A model-chosen line whose enum is
    불명/해당없음 gets the proxy's judgement of that same line."""
    px = proxy(inf)
    md = getattr(b, FAM, None)
    if not md:
        return px, 'cpu'
    eff = dict(px)
    src = 'cpu'
    for f in LINE_FIELDS:
        if md.get(f, NONE_LINE) != NONE_LINE:
            eff[f] = md[f]
            src = 'model'
    text = {i: d['line'].text.strip() for i, d in ((d['line'].i, d) for d in inf)}
    if md.get('perf_req_line', NONE_LINE) != NONE_LINE:
        s = text.get(md['perf_req_line'], '')
        eff['perf_issuer_scope'] = md['perf_issuer_scope'] if md.get('perf_issuer_scope') not in (UNKNOWN, '해당없음') else issuer_scope(s)
        eff['perf_named_scope'] = md['perf_named_scope'] if md.get('perf_named_scope') not in (UNKNOWN, '해당없음') else named_scope(s)
    if md.get('institution_line', NONE_LINE) != NONE_LINE:
        s = text.get(md['institution_line'], '')
        eff['institution_excludes_business'] = (md['institution_excludes_business'] if md.get('institution_excludes_business') not in (UNKNOWN, '해당없음')
                                                else excludes_business(s))
    if md.get('holding_line', NONE_LINE) != NONE_LINE:
        s = text.get(md['holding_line'], '')
        eff['holding_basis'] = md['holding_basis'] if md.get('holding_basis') not in (UNKNOWN, '해당없음') else holding_basis(s)
    return eff, src


def decide(b, inf, rd):
    notice = b.notice
    perf = _line(notice, inf, rd['perf_req_line'], PERF)
    region = _line(notice, inf, rd['region_req_line'], REGION)
    inst = _line(notice, inf, rd['institution_line'], INST)
    hold = _line(notice, inf, rd['holding_line'], HOLD)
    private = b.meta.method == '수의계약'
    out = {'A': None, 'B': None, 'C': None}
    if inst is not None and rd['institution_excludes_business'] == '예':
        out['A'] = inst
    elif hold is not None and rd['holding_basis'] == '과업_필요를_넘는_수준' and not private:
        out['A'] = hold
    if perf is not None and (rd['perf_issuer_scope'] in ('공공기관등만', '특정발주처') or rd['perf_named_scope'] == '특정_명칭·대상·조건으로_한정'):
        out['B'] = perf
    if perf is not None and region is not None and not b.meta.local_private:
        out['C'] = perf
    return out


def verdict(b, violation):
    """The evidence Line of violation 'A', 'B' or 'C', or None. Works without a reading (mock, replay): the regex proxy stands
    in for the model."""
    inf = infos(b.notice)
    if not inf:
        return None
    rd, _ = reading(b, inf)
    return decide(b, inf, rd).get(violation)


def explain(b):
    inf = infos(b.notice)
    if not inf:
        return {'candidate': False}
    rd, src = reading(b, inf)
    dec = decide(b, inf, rd)
    return {'candidate': True, 'gate': needs_model(inf), 'source': src, 'reading': rd,
            'verdict': {k: (v.i if v is not None else None) for k, v in dec.items()}}
