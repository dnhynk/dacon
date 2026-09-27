"""v10/v11/v13 중소기업자간 경쟁제품 group as one dedicated stage (switches.DEDICATED = ('v10', 'v11', 'v13')).

  A (v10) 직생 없음   — no participation clause requires the 판로지원법 제9조 직접생산확인증명서
  B (v11) 중소 없음   — no participation clause restricts bidders to 중소기업자 (중소기업기본법 제2조)
  C (v13) 소기업·소상공인 제한 — a participation clause admits only 소기업·소상공인 (중기업 excluded)

Common gate: the notice is a 경쟁입찰 (not 수의계약) for a 중기부 고시 경쟁제품 세부품명 inside its 특이사항 amount band
(goods: meta code in the catalog; services: the object read from the title/사업명 or an explicitly cited 세부품명), and the
notice states no 시행령 제7조 reason for buying outside 중소기업자간 경쟁. One guided-JSON call per candidate notice reads
typed facts (which listed item the object is, a spec-type 특이사항 exclusion, how the certificate and the size clause are
worded, a stated exception); the CPU decides A, B and C from those fields. Without a reading (mock, replay, invalid output)
the CPU defaults decide: the runtime's catalog classification for the object, regex readers for the clauses.
Blueprint: BLUEPRINT.md next to this tree (CPU rule and enums unchanged; see IMPLEMENTATION.md for the seams).
"""
from __future__ import annotations

import json
import re
from dataclasses import replace

from .. import catalog, families as F
from ..meta import EOK

FAM = 'd_competition_product'
PROMPT_TOKEN_LIMIT = 12000
MAX_TOKENS = 400
UNKNOWN = F.UNKNOWN
NONE_ITEM = '해당 없음'
NOTE_ENUM = ('해당 없음', '특이사항 제외 대상', '판단 불가', UNKNOWN)
DP_ENUM = ('참가자격 요건', '제출서류·평가서류로만', '제재·안내 문구만', '언급 없음', UNKNOWN)
DP_REQUIRED = '참가자격 요건'
SIZE_ENUM = ('중소기업자 요건', '소기업·소상공인 한정', '대기업 참여제한만', '없음', UNKNOWN)
SIZE_SME, SIZE_SMALL, SIZE_LARGE_ONLY, SIZE_NONE = SIZE_ENUM[:4]
EXC_ENUM = ('없음', '중소기업자간 경쟁 외 방법 사유 기재', '우선구매·조합추천·수의 사유 기재', UNKNOWN)
SW_CAP = 20 * EOK              # 소프트웨어 진흥법 제48조 + 지침 별표1: 사업금액(추정가격+VAT) 20억 미만 = 중소기업만 참여 가능 구간
HEAD_LINES = 6
OVERVIEW_LINES = 7
ATTACH_LINES = 3
QUAL_CAP = 50
KEY_CAP = 24
SPEC_CAP = 12
UNIT_LINES = 4

# ---------------------------------------------------------------- service families (용역 notices carry no meta code)
FAMILIES = {
    '행사': dict(items=('8014190201', '8014198801', '8014198901', '8014199001', '9015189001'),
                 head=r'행사|축제|문화제|페스티벌|공연|기념식|박람회|포럼|컨퍼런스|콘퍼런스|세미나|심포지엄|시상식|개막|폐막|'
                      r'경진대회|이벤트|엑스포|워크숍|워크샵|설명회|캠페인|체험전|한마당|대행', licence=('9901',)),
    '운송': dict(items=('7811189901', '7811189902', '7811189904'),
                 head=r'통학|통근|버스|차량\s*임차|셔틀|수송|운송', licence=('5805',)),
    '경비': dict(items=('9212159901',), head=r'경비|보안\s*인력|보안\s*요원|시설\s*보안', licence=('1164', '1162', '1163')),
    '청소': dict(items=('7611150101',), head=r'청소|미화', licence=()),
    'SW': dict(items=('8111159801', '8111159901', '8111179901', '8111181101', '8111189901', '8111200201', '8111200202',
                      '8111219901', '8111229901', '8115169901'),
               head=r'시스템|소프트웨어|S/?W\b|정보화|플랫폼|홈페이지|웹|앱\b|어플|유지보수|유지관리|데이터|DB|전산|고도화|구축|ISP|정보시스템',
               licence=('1468',)),
    '전시': dict(items=('7215409901', '7215409902'), head=r'전시\s*부스|홍보관|전시\s*연출|전시관|부스\s*설치|전시\s*시공', licence=()),
    '디자인': dict(items=('8214150201',), head=r'디자인', licence=('4444', '4440', '4441', '4442', '4443')),
    '영상': dict(items=('8213160301',), head=r'영상\s*제작|홍보\s*영상|동영상|영상물|영상\s*콘텐츠', licence=()),
    '승강기': dict(items=('7215401001',), head=r'승강기|엘리베이터|에스컬레이터', licence=()),
    '우편': dict(items=('8014162201',), head=r'우편|DM\s*발송|고지서\s*발송|발송\s*용역', licence=()),
    '측량': dict(items=('8115160401', '8115179901', '8110159601'), head=r'측량|지질|유수율', licence=()),
}
FAMILY_OF_CODE = {code: fam for fam, spec in FAMILIES.items() for code in spec['items']}
EXHIBITION = re.compile(r'전시|박람회|엑스포|페어|EXPO|Fair', re.I)
FESTIVAL = re.compile(r'축제|문화제')
EXHIBITION_ITEM, FESTIVAL_ITEM = '8014198801', '9015189001'

# ---------------------------------------------------------------- line readers (shared by the excerpt and the CPU defaults)
KEY_LINE = re.compile(r'직\s*접\s*생\s*산|직생|중소기업|중기업|소기업|소상공인|대기업|중견기업|유찰|재공고|경쟁제품|중기간|판로지원|'
                      r'입\s*찰\s*방\s*법|계\s*약\s*방\s*법|제한경쟁|일반경쟁')
BOILER = re.compile(r'참가자격\s*등록규정|참가자격\s*제한\s*처분|부정당업자|참가자격\s*제한기준|상생협력|하도급|청렴|공공구매론')
LINE_END = re.compile(r'[다함음자체임됨요됩니다\.\)）」』\]※]\s*$')
LIST_START = re.compile(r'^\s*(?:[가-힣]\.|\d{1,2}[\.\)]|\(\d+\)|[①-⑳]|[\-○◦•▶◇■□※·\*]|[IVXⅠ-Ⅹ]+\.)')
LABEL_LINE = re.compile(r'^[\W\d]{0,8}(입\s*찰\s*방\s*법|계\s*약\s*방\s*법|입찰\s*및\s*계약\s*방법|낙찰\s*방법|입찰\s*구분|계약\s*구분)')
DOCLIST = re.compile(r'\d\s*(부|통|매)\b|각\s*1부|사본|제출서류|제출\s*서류|증빙서류|구비서류')
SANCTION = re.compile(r'위반|제재|계약해지|해제|취소|부정당|불이익|무효')
REQ_CUE = re.compile(r'소지|갖춘|보유|받은\s*(자|업체)|발급받은|이어야|에\s*한(함|한다)|한정|자격을\s*갖|업체(이어야|만|로\s*제한|$)|'
                     r'자로서|으로서|업체로서|인\s*자|해당하는\s*자|만\s*(참가|참여|입찰)|참가\s*가능|참가할\s*수\s*있|등재|등록한\s*자|중소기업자\s*$'
                     r'|(소상공인|창업자|창업기업|벤처기업)\s*$')
# A clause that admits only 소기업·소상공인 names them as the qualifying class or requires their certificate; a document note
# ("…확인서를 발급받지 못한 업체에 한함", "신청을 증빙할 수 있는 서류") is no clause.
SMALL_CUE = re.compile(r'소지|보유|갖춘|이어야|으로서|자로서|인\s*자|만\s*(참가|참여|입찰)|참가할\s*수\s*있|자격이\s*있|(소상공인|창업자|창업기업|벤처기업)\s*$')
DOC_NOTE = re.compile(r'발급받지\s*못한|증빙할\s*수\s*있는\s*서류|서류\s*$')
BUNDLE_COUNT = re.compile(r'(\d{2,4})\s*종')
SEP = r'[·ㆍ,\.․‧∙•・/]'
DP_WORD = re.compile(r'직접\s*생산\s*(확인)?\s*(증명|확인서)|직접생산\s*확인\s*(증명)?서|직생\s*확인|직접\s*생산\s*등재|'
                     r'제\s*9\s*조[^\n]{0,60}시행령\s*제?\s*10\s*조[^\n]{0,200}(등록한\s*자|소지|등재)|'
                     r'직접\s*생산\s*확인\s*기준[^\n]{0,160}(소지|등록|등재)')
DP_REQ = re.compile(r'소지|보유|갖춘|발급받은|이어야|받은\s*(자|업체)|에\s*한(함|한다)|등록한\s*자|등재|업체로서|자로서|'
                    r'확인되지\s*않(을|는)\s*경우[^\n]{0,20}참가\s*자격이\s*없')
DP_EXC = re.compile(r'시행령\s*제\s*7\s*조\s*제?\s*1\s*항|직접생산확인품목에서\s*제외|일반물품으로\s*입찰|중소기업자\s*간\s*경쟁\s*(입찰\s*)?외|'
                    r'유찰[^\n]{0,80}(일반경쟁|일반입찰)|직접생산확인증명서를\s*(별도로\s*)?요구하지\s*않')
SMALL_WORD = re.compile(r'소기업|소상공인')
SPEC_UNIT = re.compile(r'CPU|GHz|kW|kVA|톤|mm|㎡|TB|GB|cd/|용량|중량|고정익|소재|재질|규격')


def price(b):
    P = b.meta.P
    return None if P is None or P <= 1000 else float(P)      # 1, 2, 4 … are placeholders: no amount gate


def admits(p, P):
    """The product's 특이사항 admits a purchase at 추정가격 P: cap-type notes, the 제48조 project band, floor-type notes."""
    if P is None:
        return True
    if p.cap is not None and P >= p.cap:
        return False
    if '제48조' in (p.note or '') and P * 1.1 >= SW_CAP:
        return False
    m = re.search(r'(\d+)\s*천만원\s*이상', p.note or '')
    if m and P < int(m.group(1)) * 1e7:
        return False
    return True


def units(notice):
    """[(text, [Line])]: physical lines re-joined while a line does not end a sentence and the next is no list item
    (PDF/HWP wraps split one clause over 2–4 lines), so a clause is read whole."""
    out, k, lines = [], 0, notice.lines
    while k < len(lines):
        ln = lines[k]
        if not ln.text.strip():
            k += 1
            continue
        group = [ln]
        while (len(group) < UNIT_LINES and k + len(group) < len(lines) and lines[k + len(group)].doc == ln.doc
               and lines[k + len(group)].text.strip() and not LINE_END.search(group[-1].text.rstrip())
               and not LIST_START.match(lines[k + len(group)].text)):
            group.append(lines[k + len(group)])
        out.append((' '.join(g.text.strip() for g in group), group))
        k += len(group)
    return out


# ---------------------------------------------------------------- CPU gate
def head_text(b):
    gong = [ln for ln in b.notice.notice_lines() if ln.text.strip()]
    return '\n'.join([ln.text for ln in gong[:HEAD_LINES]] + list(b.titles[:4]) + [catalog.first_project_line(b.notice)])


def cited_services(text, cat):
    """Listed service products the notice itself names (10-digit code or 세부품명), in order."""
    found = []
    for code in re.findall(r'(?<!\d)(\d{10})(?!\d)', text):
        p = cat.by_code.get(code)
        if p is not None and p.code[0] in '789' and p not in found:
            found.append(p)
    flat = re.sub(r'\s', '', text)
    for name, p in cat.by_name.items():
        if p.code and p.code[0] in '789' and len(name) >= 4 and name in flat and p not in found:
            found.append(p)
    return found


def select(b):
    """{'kind', 'families', 'items', 'explicit'} for a notice the model must read, else None (CPU decides: no violation).
    수의계약 (소액수의 exception), notices of unknown work, goods with no listed code, services naming no listed family, and
    objects outside their 특이사항 amount band never reach the model."""
    meta = b.meta
    if meta.private or meta.work not in ('물품', '용역'):
        return None
    cat, P = catalog.load(), price(b)
    if meta.work == '물품':
        items = []
        for name, code in meta.codes:
            p = cat.by_code.get(code) or cat.by_name.get(re.sub(r'\s', '', name))
            if p is not None and p not in items:
                items.append(p)
        items = [p for p in items if admits(p, P)]
        return dict(kind='goods', families=(), items=items, explicit=[]) if items else None
    explicit = cited_services(b.notice.full_text(), cat)
    fams = {FAMILY_OF_CODE[p.code] for p in explicit if p.code in FAMILY_OF_CODE}
    head, lic = head_text(b), str(meta.license or '')
    for fam, spec in FAMILIES.items():
        if re.search(spec['head'], head) or any(c in lic for c in spec['licence']):
            fams.add(fam)
    if not fams:
        return None
    items = list(explicit)
    for fam in sorted(fams):
        for code in FAMILIES[fam]['items']:
            p = cat.by_code.get(code)
            if p is None or p in items:
                continue
            if code == EXHIBITION_ITEM and not EXHIBITION.search(head):
                continue          # the 전시회 item (no cap) is offered only for exhibition notices, never as a stand-in for 행사·축제
            items.append(p)
    festival = cat.by_code.get(FESTIVAL_ITEM)
    if festival is not None and FESTIVAL.search(head) and not admits(festival, P):
        items = [p for p in items if p.code not in FAMILIES['행사']['items']]   # the specific 축제 designation (3억 cap) governs, even
        #                                                                       when the orderer cites another 행사 item (dev-054)
    items = [p for p in items if admits(p, P)]
    return dict(kind='service', families=tuple(sorted(fams)), items=items, explicit=explicit) if items else None


# ---------------------------------------------------------------- excerpt
def subject_lines(notice, cap=None):
    out, seen = [], set()

    def add(ln):
        if ln is not None and ln.i not in seen and ln.text.strip():
            seen.add(ln.i)
            out.append(ln)

    gong = notice.notice_lines()
    for ln in [x for x in gong if x.text.strip()][:HEAD_LINES]:
        add(ln)
    if cap is not None:
        return out[:cap]
    for ln in gong[:200]:
        if catalog.TITLE_LABEL.search(ln.text):
            add(ln)
            if len(out) > HEAD_LINES + 3:
                break
    for k, ln in enumerate(gong[:800]):
        if ln.sec == 'OVERVIEW' and ln.head == ln.i:
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


def shown_lines(b, cand, key_cap=KEY_CAP, qual_cap=QUAL_CAP, spec_on=True, subj_cap=None):
    """The notice lines the model sees: subject block, 참가자격 sections of every document, other lines naming the
    certificate / firm size / exceptions (with their wrapped continuations), goods 규격 lines with units."""
    notice = b.notice
    out, seen = [], set()

    def add(ln):
        if ln.i not in seen and ln.text.strip():
            seen.add(ln.i)
            out.append(ln)

    for ln in subject_lines(notice, subj_cap):
        add(ln)
    for ln in [x for x in notice.lines if x.sec == 'QUAL' and x.text.strip()][:qual_cap]:
        add(ln)
    n = 0
    for text, group in units(notice):
        if n >= key_cap:
            break
        if group[0].i in seen and len(group) == 1:
            continue
        if KEY_LINE.search(text) and not BOILER.search(text):
            for ln in group:
                add(ln)
            n += 1
    if spec_on and cand['kind'] == 'goods':
        k = 0
        for ln in notice.lines:
            if ln.doc_type != '공고문' and SPEC_UNIT.search(ln.text) and ln.text.strip():
                add(ln)
                k += 1
                if k >= SPEC_CAP:
                    break
    return sorted(out, key=lambda ln: ln.i)


SYSTEM = ('당신은 공공조달 입찰공고 검토관입니다. 아래 공고 발췌만 보고 사실만 추출하여, 정해진 JSON 스키마대로만 답하십시오. 위반 여부는 '
          '판단하지 않습니다. 추측으로 채우지 말고, 발췌로 알 수 없는 것은 "불명"을 고르십시오.\n'
          '[판단할 것]\n'
          '1. object_item — 이 공고가 조달하는 물품·용역이 [경쟁제품 세부품명 후보] 중 어느 세부품명에 해당하는가. 공고명·사업명·과업 내용으로 '
          '판단합니다. 발주기관이 직접생산확인증명서의 세부품명으로 지정한 후보는, 공고명이 명백히 다른 일(연수·연구·여행 등)을 가리키지 않는 한 '
          '그 항목으로 봅니다. 행사·축제·공연·박람회·세미나의 기획·운영·대행은 행사기획및대행서비스 '
          '계열, 통학·통근 버스 임차는 통학/통근운송서비스, 시스템·홈페이지·플랫폼의 구축·개발·유지보수는 정보시스템개발/유지관리서비스입니다. '
          '장비 임차, 교육·연수·체험학습·여행, 학술연구·조사, 컨설팅·전략·계획 수립, 건축 설계, 폐기물 처리, 회계·정산, 채용 대행은 "' + NONE_ITEM +
          '". 여러 품목을 묶어 사는 공고에서 후보 세부품명이 부수적인 일부(예: 공산품 400여 종 중 1종)이면 "' + NONE_ITEM + '". '
          '공고명이 가려져([공고명]) 개요로도 알 수 없으면 "불명".\n'
          '2. note_exclusion — 고른 세부품명에 [특이사항: …]이 있을 때 공고 대상이 그 제외 조건에 해당하는가. 규격에서 명확히 제외 조건에 '
          '해당하면 "특이사항 제외 대상", 특이사항이 없거나 해당 근거가 없으면 "해당 없음", 발췌만으로 알 수 없으면 "판단 불가". 금액 조건은 '
          '여기서 판단하지 않습니다(별도 계산).\n'
          '3. dp_requirement — 「중소기업제품 구매촉진 및 판로지원에 관한 법률」 제9조·시행령 제10조의 직접생산확인증명서(직접생산증명서)를 '
          '어떻게 다루는가.\n'
          '   - 참가자격 요건: 입찰(제안·견적) 참가자격으로 소지·보유하여야 한다고 요구. 참가자격 절이 아니어도, 첨부문서라도 요건 문장이면 해당. '
          '"제9조 및 시행령 제10조에 따라 …로 등록한 자", "공공구매 종합정보망에 직접 생산 등재된 업체", "직접생산확인증명서가 확인되지 않으면 '
          '참가자격이 없다"도 요건입니다.\n'
          '   - 제출서류·평가서류로만: 제출서류·증빙·평가 서류 목록에만 나옴 (예: "직접생산확인증명서 1부", "…확인서, 직접생산확인증명서 각 1부")\n'
          '   - 제재·안내 문구만: 위반 시 제재, 하도급 금지, 법령 설명 등 안내에만 나옴\n'
          '   - 언급 없음\n'
          '   입찰방법·계약방법 표시(예: "중소기업자간 경쟁", "제한경쟁(중소기업)"), 조항호, 서류명만으로는 요건으로 보지 않습니다.\n'
          '4. size_clause — 참가자격으로 요구하는 기업 규모 요건.\n'
          '   - 중소기업자 요건: 「중소기업기본법」 제2조의 중소기업자(중기업 포함)·중소기업확인서 소지·"중소기업 또는 소상공인"·'
          '"중·소기업·소상공인확인서"·"중기업·소기업·소상공인확인서"를 요구\n'
          '   - 소기업·소상공인 한정: 소기업자·소상공인(벤처·창업기업 병기 포함)만 참가할 수 있거나 "소기업·소상공인 확인서" 소지를 요구하여 '
          '중기업이 배제됨. 문장이 "중소기업으로서"로 시작해도 요구하는 확인서가 소기업·소상공인 확인서이면 이것.\n'
          '   - 대기업 참여제한만: 소프트웨어 진흥법 제48조 등 대기업 참여 제한만 있고 중소기업자 요건은 없음 ("중소기업 확인은 공공구매정보망을 '
          '활용" 같은 확인 방법 안내만 있는 경우 포함)\n'
          '   - 없음: 규모 요건이 참가자격에 없음. 입찰방법 표시 "제한경쟁(소기업·소상공인)", 제출서류 목록의 확인서, 조항호만 있는 경우도 없음.\n'
          '5. size_line — size_clause의 근거 문장이 있는 줄의 L번호(숫자만). 없으면 -1.\n'
          '6. size_quote — 그 문장을 발췌에서 글자 그대로 복사(300자 이내). 없으면 "".\n'
          '7. exception_clause — 중소기업자간 경쟁입찰 외의 방법으로 조달한다는 사유 기재 여부.\n'
          '   - 중소기업자간 경쟁 외 방법 사유 기재: 판로지원법 시행령 제7조 제1항(특정 기술·유찰 등)을 들어 직접생산확인품목에서 제외·일반물품 '
          '입찰·일반경쟁 전환을 명시, 또는 해당 품목이 추정가격 1천만원 미만·전체 중 비중이 작아 직접생산확인증명서를 요구하지 않는다고 명시\n'
          '   - 우선구매·조합추천·수의 사유 기재: 우선구매 대상 제품, 조합 추천 수의계약 등을 명시\n'
          '   - 없음\n'
          '- 출력은 JSON 하나다.')


def _note(p, n=80):
    if not p.note:
        return ''
    return ' [특이사항: ' + re.sub(r'\s+', ' ', p.note)[:n] + ']'


def messages(b, cand, cands):
    m = b.notice.meta
    info = [f'- 업무구분: {m.get("업무구분")} / 적용법: {m.get("적용계약법")} / 계약방법: {m.get("계약방법")} / 낙찰방법: {m.get("낙찰방법")} '
            f'/ 추정가격: {m.get("입찰추정가격")}원',
            f'- 나라장터 등록 세부품명: {m.get("세부품명번호목록") or "(미등록)"}',
            f'- 면허·업종 제한: {m.get("면허업종제한목록") or "(없음)"}',
            f'- 제한 근거(조항호): {m.get("조항호내용") or "(없음)"}']
    menu = [f'- {p.name}({p.code}){_note(p)}' for p in cand['items'][:12]] + [f'- {NONE_ITEM}']
    user = ('[공고 기본정보]\n' + '\n'.join(info) + '\n\n[경쟁제품 세부품명 후보]\n' + '\n'.join(menu) + '\n\n[공고 발췌]\n'
            + (F.excerpt(b.notice, cands, 0) or '(없음)') + '\n\n[출력] 위 스키마의 JSON 객체 하나만 출력.')
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}]


def schema(cand):
    names = [p.name for p in cand['items'][:12]] + [NONE_ITEM, UNKNOWN]
    return {'type': 'object', 'additionalProperties': False,
            'required': ['object_item', 'note_exclusion', 'dp_requirement', 'size_clause', 'size_line', 'size_quote', 'exception_clause'],
            'properties': {
                'object_item': {'type': 'string', 'enum': names},
                'note_exclusion': {'type': 'string', 'enum': list(NOTE_ENUM)},
                'dp_requirement': {'type': 'string', 'enum': list(DP_ENUM)},
                'size_clause': {'type': 'string', 'enum': list(SIZE_ENUM)},
                'size_line': {'type': 'integer', 'minimum': -1, 'maximum': 100000},
                'size_quote': {'type': 'string', 'maxLength': 300},
                'exception_clause': {'type': 'string', 'enum': list(EXC_ENUM)}}}


def request(engine, b, k, request_cls):
    """None when the CPU gate drops the notice; else one reading request. Over the token limit the excerpt shrinks: fewer
    key-line units, fewer 참가자격 lines, no 규격 lines, the subject block to its head, then meta values (as main.make_request)."""
    cand = select(b)
    if cand is None:
        return None
    view, key_cap, qual_cap, spec_on, subj_cap = b, KEY_CAP, QUAL_CAP, True, None
    while True:
        cands = shown_lines(view, cand, key_cap, qual_cap, spec_on, subj_cap)
        ids = engine.token_ids(messages(view, cand, cands))
        if len(ids) + MAX_TOKENS <= min(PROMPT_TOKEN_LIMIT, engine.max_model_len - 64):
            break
        if key_cap > 6:
            key_cap = max(6, int(key_cap * 0.75))
        elif qual_cap > 12:
            qual_cap = max(12, int(qual_cap * 0.75))
        elif spec_on:
            spec_on = False
        elif subj_cap is None:
            subj_cap = HEAD_LINES
        elif view is b:
            view = replace(b, notice=replace(b.notice, meta={key: None if value is None else F.shown(str(value))
                                                           for key, value in b.notice.meta.items()}))
        elif key_cap > 1 or qual_cap > 1:
            key_cap, qual_cap = max(1, key_cap // 2), max(1, qual_cap // 2)
        else:
            raise ValueError('competition_product: minimum reading request exceeds the engine token limit')
    setattr(b, FAM + '_schema', schema(cand))
    return request_cls(k, FAM, cands, (), ids, getattr(b, FAM + '_schema'), MAX_TOKENS, 0)


# ---------------------------------------------------------------- consume
def consume(b, cands, text):
    """Validate the model's JSON against the stage schema and store it as b.d_competition_product; False when invalid."""
    try:
        obj = json.loads(text.split('<channel|>', 1)[-1])
    except (ValueError, AttributeError):
        return False
    if not isinstance(obj, dict):
        return False
    sch = getattr(b, FAM + '_schema', None)
    if sch is None:
        cand = select(b)
        if cand is None:
            return False
        sch = schema(cand)
    props = sch['properties']
    if any(key not in obj for key in sch['required']):
        return False
    for key in ('object_item', 'note_exclusion', 'dp_requirement', 'size_clause', 'exception_clause'):
        if obj[key] not in props[key]['enum']:
            return False
    if type(obj['size_line']) is not int or not isinstance(obj['size_quote'], str):
        return False
    shown_ids = {ln.i for ln in cands}
    setattr(b, FAM, {'object_item': obj['object_item'], 'note_exclusion': obj['note_exclusion'], 'dp_requirement': obj['dp_requirement'],
                     'size_clause': obj['size_clause'], 'size_line': obj['size_line'] if obj['size_line'] in shown_ids else -1,
                     'size_quote': obj['size_quote'].strip()[:300], 'exception_clause': obj['exception_clause']})
    return True


# ---------------------------------------------------------------- CPU defaults (no reading, or 불명 fields)
def normalize_mid(s):
    s = re.sub(r'중\s*' + SEP + r'?\s*소기업', 'MID', s)                      # 중소기업, 중·소기업, 중ㆍ소기업 …
    return re.sub(r'중기업\s*(?:' + SEP + r'|또는|및|이나|\s)\s*소기업', 'MID', s)   # 중기업·소기업, 중기업 또는 소기업


def classify_size(text):
    """SIZE_SME | SIZE_SMALL | SIZE_LARGE_ONLY | None for one clause (merged unit text)."""
    if LABEL_LINE.match(text):
        return None                                        # 입찰방법·계약방법 표시는 참가자격 요건이 아님 (DEV-039 판정)
    if SANCTION.search(text) and not REQ_CUE.search(text):
        return None
    if DOCLIST.search(text) and not re.search(r'소지|이어야|자로서|으로서', text):
        return None
    if not REQ_CUE.search(text):
        return None
    n = normalize_mid(text)
    has_small = re.search(r'소기업', n) is not None and '중기업' not in n and not DOC_NOTE.search(text)
    has_mid = ('MID' in n) or ('중기업' in n)
    small_cert = re.search(r'소기업\s*' + SEP + r'*\s*소상공인\s*(등\s*)?확인서|소기업\s*(자)?\s*(또는|,|·|ㆍ)\s*소상공인|소기업자', n)
    if has_small and (small_cert or not has_mid) and SMALL_CUE.search(text):
        return SIZE_SMALL
    if has_mid and re.search(r'MID\s*(자|기본법|확인서|으로서|또는|만|에\s*한|을\s*대상)|MID[^\n]{0,40}확인서|MID\s*$', n):
        return SIZE_SME
    if re.search(r'대기업[^\n]{0,30}(참여|참가)\s*(제한|불가|할\s*수\s*없)', text):
        return SIZE_LARGE_ONLY
    return None


def cpu_size(notice):
    """(size class, unit lines) from the 참가자격 sections first, then any other section but 제출서류."""
    best = (SIZE_NONE, None)
    for pass_qual in (True, False):
        for text, group in units(notice):
            sec = group[0].sec
            if (sec == 'QUAL') != pass_qual or sec == 'DOCS':
                continue
            c = classify_size(text)
            if c == SIZE_SMALL:
                return c, group
            if c in (SIZE_SME, SIZE_LARGE_ONLY) and best[1] is None:
                best = (c, group)
        if best[1] is not None:
            return best
    return best


def cpu_dp(notice):
    dp = '언급 없음'
    for text, group in units(notice):
        if DP_WORD.search(text):
            if group[0].sec == 'DOCS' or (DOCLIST.search(text) and not re.search(r'소지|이어야', text)):
                dp = '제출서류·평가서류로만' if dp != DP_REQUIRED else dp
            elif SANCTION.search(text) and not re.search(r'소지|이어야|참가\s*자격이\s*없', text):
                dp = dp if dp != '언급 없음' else '제재·안내 문구만'
            elif DP_REQ.search(text):
                return DP_REQUIRED
            else:
                dp = '제출서류·평가서류로만' if dp != DP_REQUIRED else dp
        elif re.search(r'직접\s*생산', text) and dp == '언급 없음':
            dp = '제재·안내 문구만'
    return dp


def cpu_exception(notice):
    return EXC_ENUM[1] if DP_EXC.search(notice.full_text()) else '없음'


def cpu_in_scope(b, cand):
    """Without a model reading of the object, the runtime's catalog classification decides (services: title/licence family
    inside its amount band; goods: every registered code listed), or the orderer's own framing: a certificate requirement
    naming a listed service 세부품명 (dev-078 디자인서비스, dev-013 전시홍보관). A goods notice buying tens of kinds of items
    (급식 공산품 "간장 등 406종") under one registered code is a bundle whose listed item is incidental (dev-170)."""
    if b.scope.competitive is not True:
        return bool(cand['kind'] == 'service' and cand['explicit'] and cpu_dp(b.notice) == DP_REQUIRED)
    if b.meta.work == '물품':
        for ln in b.notice.notice_lines()[:80]:
            m = BUNDLE_COUNT.search(ln.text)
            if m and int(m.group(1)) >= 20 and re.search(r'품\s*명|구\s*입|구\s*매|내\s*역|물\s*품', ln.text):
                return False
    return True


def _known(reading, key):
    v = (reading or {}).get(key)
    return None if v in (None, UNKNOWN, '') else v


def in_scope(b, cand, reading):
    item = _known(reading, 'object_item')
    if item is None:
        if not cpu_in_scope(b, cand):
            return False
    elif item == NONE_ITEM or item not in {p.name for p in cand['items']}:
        return False
    if _known(reading, 'note_exclusion') == '특이사항 제외 대상':
        return False
    exc = _known(reading, 'exception_clause')
    if exc in (None, '없음'):
        exc = cpu_exception(b.notice)        # a stated 시행령 제7조 reason is read by the CPU too (the model may miss it)
    return exc == '없음'


def evidence_line(b, reading, group):
    lines = b.notice.lines
    i = (reading or {}).get('size_line', -1)
    if isinstance(i, int) and 0 <= i < len(lines) and SMALL_WORD.search(lines[i].text):
        return lines[i]
    quote = re.sub(r'\s', '', (reading or {}).get('size_quote') or '')
    if len(quote) >= 8:
        key = quote[:30]
        for ln in lines:
            if key in re.sub(r'\s', '', ln.text) and SMALL_WORD.search(ln.text):
                return ln
    if group:
        return next((ln for ln in group if SMALL_WORD.search(ln.text)), group[0])
    return next((ln for ln in lines if SMALL_WORD.search(ln.text) and REQ_CUE.search(ln.text)), None)


def verdict(b, violation):
    """A/B: True (evidence blank) or None. C: the evidence Line (an original notice line) or None. Works without a reading."""
    cand = select(b)
    if cand is None:
        return None
    reading = getattr(b, FAM, None)
    if not in_scope(b, cand, reading):
        return None
    if violation == 'A':
        dp = _known(reading, 'dp_requirement') or cpu_dp(b.notice)
        return True if dp != DP_REQUIRED else None
    size, group = _known(reading, 'size_clause'), None
    if size is None:
        size, group = cpu_size(b.notice)
    if violation == 'B':
        return True if size in (SIZE_NONE, SIZE_LARGE_ONLY) else None
    if violation == 'C':
        if size != SIZE_SMALL:
            return None
        if group is None:
            _, group = cpu_size(b.notice)
        return evidence_line(b, reading, group)
    return None


def explain(b):
    """Diagnostics: gate, items offered, the fields used (model or CPU) and the three verdicts."""
    cand = select(b)
    if cand is None:
        return {'candidate': False}
    reading = getattr(b, FAM, None)
    return {'candidate': True, 'kind': cand['kind'], 'families': cand['families'], 'items': [p.name for p in cand['items']],
            'source': 'model' if reading else 'cpu', 'in_scope': in_scope(b, cand, reading),
            'dp': _known(reading, 'dp_requirement') or cpu_dp(b.notice),
            'size': _known(reading, 'size_clause') or cpu_size(b.notice)[0],
            'exception': _known(reading, 'exception_clause') or cpu_exception(b.notice),
            'A': verdict(b, 'A') is not None, 'B': verdict(b, 'B') is not None, 'C': verdict(b, 'C') is not None}
