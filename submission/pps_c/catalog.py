"""Is the procured object a 중소기업자간 경쟁제품? (talkboard route: identify the object → match the catalog name → apply
the amount-type 특이사항 to meta 입찰추정가격; 소프트웨어 진흥법 제48조 entries by 사업금액 = 추정가격 + VAT).

Goods carry their 세부품명 in meta. Service notices carry none, so the service is identified from the project title and
the registered license (the catalog's 31 service entries), or from a catalog code the notice itself cites next to a
direct-production clause.
"""
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import switches

HERE = Path(__file__).resolve().parent
EOK = 1e8

TITLE_LABEL = re.compile(r'(용\s*역\s*명|사\s*업\s*명|공\s*고\s*명|입\s*찰\s*건\s*명|공\s*고\s*건\s*명|사\s*업\s*건\s*명|건\s*명|과\s*업\s*명|계\s*약\s*건\s*명|(?<!부)(?<!부\s)품\s*명(?=\s*[:：]))\s*[:：|]?\s*(.*)')
# Audit E: 입찰명·계약명·구매명·공사명 label the project name as well (1,165 of 20,000 train notices have no title without them).
TITLE_LABEL_FIX = re.compile(r'(용\s*역\s*명|사\s*업\s*명|공\s*고\s*명|입\s*찰\s*건\s*명|공\s*고\s*건\s*명|사\s*업\s*건\s*명|건\s*명|과\s*업\s*명|계\s*약\s*건\s*명'
                             r'|(?:입\s*찰|계\s*약|구\s*매|공\s*사)\s*명(?!\s*[세의])|(?<!부)(?<!부\s)품\s*명(?=\s*[:：]))\s*[:：|]?\s*(.*)')
# (family, catalog code, title pattern, license pattern, amount limit, limit basis)
SERVICE_FAMILIES = [
    ('festival', '9015189001', r'축제|문화제', r'행사대행', 3 * EOK, 'estimate_below'),
    ('exhibition', '8014198801', r'전시회|박람회|엑스포|기획전|전시\s*연출|전시\s*운영', r'행사대행', None, None),
    ('international', '8014198901', r'국제\s*(행사|회의|포럼|컨퍼런스|대회)', r'행사대행', 10 * EOK, 'estimate_below'),
    ('conference', '8014190201', r'(?<!대)회의(?!실|장)|포럼|컨퍼런스|세미나|심포지엄|학술\s*대회|워크숍', r'행사대행', 10 * EOK, 'estimate_below'),
    ('event', '8014199001', r'(?<![여대])행사(?!장)|페스티벌|페스타|기념식|시상식|개막식|폐막식|공연(?!장)|버스킹|대회\s*운영|설명회\s*운영|발표회',
     r'행사대행', 10 * EOK, 'estimate_below'),
    ('transport', '7811189902', r'통학\s*(버스|차량|택시)|통근\s*(버스|차량)|전세\s*버스|버스\s*임차|(수학여행|체험\s*학습|수련\s*활동|현장\s*학습|견학|야영)\s*(차량|버스)|학생\s*수송', r'여객자동차운수사업\s*\((?!자동차\s*대여)|전세\s*버스', None, None),
    ('cleaning', '7611150101', r'청소(?!년|차)|환경\s*미화|미화\s*용역', r'청소(?!년)|건물위생', None, None),
    ('security', '9212159901', r'시설\s*경비|경비\s*(용역|업무|근무)|보안\s*경비', r'시설경비', None, None),
    ('video', '8213160301', r'홍보\s*(동)?영상|(동)?영상.{0,12}홍보|홍보.{0,12}(동)?영상\s*(제작|촬영)', None, None, None),
    ('design', '8214150201', r'디자인', r'디자인', None, None),
    ('sw', '8111189901', None,
     r'소프트웨어사업자|컴퓨터관련서비스', 20 * EOK, 'project_below'),
    ('elevator', '7215401001', r'승강기.{0,6}(유지|보수|관리)', r'승강기', None, None),
    ('mail', '8014162201', r'우편\s*발송', None, None, None),
    ('geology', '8115179901', r'지질\s*(조사|연구)', None, 0.1 * EOK, 'estimate_floor'),
    ('water', '8110159601', r'유수율', None, None, None),
]
# Audit E: 토론회 and 교류회 are conference-type events of the 행사대행 catalog entry too.
SERVICE_FAMILIES_FIX = [(f, c, (t + r'|토론회|교류회') if f == 'conference' else t, lp, lim, bas) for f, c, t, lp, lim, bas in SERVICE_FAMILIES]
# Audit R2-B: "…페어" is an exhibition-type event (행사대행) and "영상 및 인쇄 광고물 제작" a promotional video (동영상제작서비스).
FAMILY_TITLE_ADD2 = {'exhibition': r'|페어(?!링|플레이)', 'video': r'|영상\s*(및\s*인쇄\s*)?광고물'}


def service_families():
    fams = SERVICE_FAMILIES_FIX if switches.AUDIT_FIXES else SERVICE_FAMILIES
    if switches.AUDIT_FIXES2:
        fams = [(f, c, t + FAMILY_TITLE_ADD2[f] if t and f in FAMILY_TITLE_ADD2 else t, lp, lim, bas) for f, c, t, lp, lim, bas in fams]
    if switches.AUDIT_FIXES3:
        # Audit R3-B: 회의록 (minutes) is no 회의 (회의기획·운영).
        fams = [(f, c, t.replace('회의(?!실|장)', '회의(?!실|장|록)') if f == 'conference' else t, lp, lim, bas) for f, c, t, lp, lim, bas in fams]
    return fams


# The listed SW services (정보시스템개발·유지관리, 소프트웨어유지및지원, 데이터처리, 인터넷지원개발 …) are work on software or an
# information system: a system or software object followed by development, maintenance or operation. Facility and
# energy systems are not information systems.
SW_OBJECT = (r'(정보\s*(화\s*)?시스템|[가-힣A-Za-z]{0,12}시스템|(정보|전산|통신|출입\s*통제|관제|업무|행정|전자|납품|분류|정보화)\s*체계'
             r'|전산\s*(장비|망|실|자원|시스템)?|소프트웨어|S/?W(?![A-Za-z])|홈페이지|누리집|웹\s*사이트|포털|플랫폼|정보망|데이터\s*베이스|DB'
             r'|빅\s*데이터|데이터|인공\s*지능|AI(?![A-Za-z])|어플리케이션|애플리케이션|앱|챗봇|ERP|그룹웨어|LMS|클라우드|서버|솔루션)')
SW_ACTION = r'(유지\s*(보수|관리)|유지|보수|관리|운영|구축|개발|고도화|재구축|개선|개편|전환|통합|이전|도입|위탁|업그레이드)'
# Between the object and the work nothing may name other work ("…플랫폼 조성 주민역량강화사업 운영" is a community programme).
SW_GAP = r'(?:(?!사업|용역|조성|교육|지원|행사|학습|연수|체험|캠프|컨설팅|연구|조사|공사|폐기물|철거|정비|설치|교체|청소|수리|/).){0,16}?'
SW_SERVICE = re.compile(SW_OBJECT + SW_GAP + SW_ACTION + r'|프로그램\s*(개발|구축|고도화|유지)|(데이터|DB|정보)\s*(처리|분석)')
# Audit E (title fix side effect): a program without a computer qualifier ("역량 강화 프로그램 개발") is education, as
# facts.SW_OBJECT reads it.
SW_SERVICE_FIX = re.compile(SW_OBJECT + SW_GAP + SW_ACTION + r'|(컴퓨터|응용|업무|관리|행정|전산|정보)\s*프로그램\s*(개발|구축|고도화|유지)'
                            r'|(데이터|DB|정보)\s*(처리|분석)')
# Audit R2-B (SW진흥법 §2: SW사업 = 개발·구축·유지관리): a title naming 감리 anywhere is audit work (the catalog lists no
# 감리 service), ISO-type management systems (안전보건경영시스템, 품질경영체계) are no information systems, and the gap between an
# SW object and the work may not run through challenge, recruitment, provider selection, application or remote-monitoring work.
SW_AUDIT_TITLE = re.compile(r'감\s*리')
MGMT_SYSTEM = re.compile(r'경\s*영\s*(시\s*스\s*템|체\s*계)|(안\s*전|보\s*건|품\s*질|환\s*경|에\s*너\s*지)\s*(경\s*영|관\s*리)\s*(시\s*스\s*템|체\s*계)')
SW_GAP2 = (r'(?:(?!사업|용역|조성|교육|지원|행사|학습|연수|체험|캠프|컨설팅|연구|조사|공사|폐기물|철거|정비|설치|교체|청소|수리|/'
           r'|챌린지|공모|경진|채용|경력자|제공\s*기관|선정|원서|수납|원격\s*감시).){0,16}?')
SW_SERVICE_FIX2 = re.compile(SW_OBJECT + SW_GAP2 + SW_ACTION + r'|(컴퓨터|응용|업무|관리|행정|전산|정보)\s*프로그램\s*(개발|구축|고도화|유지)'
                             r'|(데이터|DB|정보)\s*(처리|분석)')
FACILITY_SYSTEM = re.compile(r'(태양광|발전|냉난방|냉방|난방|공조|소방|급수|배수|승강기|전기|조명|음향|방송|기계\s*설비|보일러|하수|정수|주차\s*관제|CCTV|경비|방범|감시|블록)\s*(설비\s*)?(시스템|체계)')
# Audit R3-B (소프트웨어 진흥법 제2조: SW사업 = SW 개발·제작·유지관리, 정보시스템 구축·운영): a named system or database that is the
# object of insurance, content production, vulnerability assessment, recruitment, provider selection, remote monitoring or
# counselling work ("…여객처리시스템 책임보험", "…정보시스템 콘텐츠 제작 및 배포", "…정보망 서비스 상담센터 위탁 운영") is no SW
# deliverable. One word list serves this gap test and facts.sw_named (NOT_SW_AFTER: before any SW work word).
NOT_SW_WORK = (r'보\s*험|콘\s*텐\s*츠|취\s*약\s*점|보\s*안\s*(진\s*단|점\s*검)|분\s*석\s*[·ㆍ및\s]*평\s*가|채\s*용|경\s*력\s*자|제\s*공\s*기\s*관'
               r'|원\s*격\s*감\s*시|상\s*담|콜\s*센\s*터|고\s*객\s*센\s*터|헬\s*프\s*데\s*스\s*크')
SW_GAP3 = (r'(?:(?!사업|용역|조성|교육|지원|행사|학습|연수|체험|캠프|컨설팅|연구|조사|공사|폐기물|철거|정비|설치|교체|청소|수리|/'
           r'|챌린지|공모|경진|채용|경력자|제공\s*기관|선정|원서|수납|원격\s*감시|' + NOT_SW_WORK + r').){0,16}?')
SW_SERVICE_FIX3 = re.compile(SW_OBJECT + SW_GAP3 + SW_ACTION + r'|(컴퓨터|응용|업무|관리|행정|전산|정보)\s*프로그램\s*(개발|구축|고도화|유지)'
                             r'|(데이터|DB|정보)\s*(처리|분석)')
NOT_SW_AFTER = re.compile(r'(?:(?!' + SW_ACTION + r').){0,16}?(?:' + NOT_SW_WORK + r')')
# A title that names some work: a license alone cannot then assign a family the title does not name.
TITLE_WORK = re.compile(r'용\s*역|사\s*업|구\s*축|유\s*지|보\s*수|관\s*리|운\s*영|개\s*발|구\s*매|구\s*입|임\s*차|제\s*작|설\s*치|위\s*탁|서\s*비\s*스|교\s*육|조\s*성|지\s*원|행\s*사|개\s*선|대\s*행|컨\s*설\s*팅|연\s*구|조\s*사|보\s*급')


def sw_service_title(title):
    t = FACILITY_SYSTEM.sub(' ', title or '')
    if switches.AUDIT_FIXES2:
        return not SW_AUDIT_TITLE.search(t) and bool((SW_SERVICE_FIX3 if switches.AUDIT_FIXES3 else SW_SERVICE_FIX2).search(MGMT_SYSTEM.sub(' ', t)))
    return bool((SW_SERVICE_FIX if switches.AUDIT_FIXES else SW_SERVICE).search(t))


def placeholder_title(title):
    return not (title or '').strip() or bool(re.fullmatch(r'\s*\[?[가-힣]{1,4}명\]?\s*', title or '')) or not TITLE_WORK.search(title or '')


# The procured service is what the title ends with. A title whose head names another kind of work ("…박람회장 시설관리
# 용역", "…엑스포공원 … 폐기물처리 용역", "…꽃박람회 입장권 판매 대행") is that work, whatever event it serves.
TITLE_TAIL = re.compile(r'(\s*[\(（][^)）]*[\)）]|\s*(용\s*역|사\s*업|업체\s*선정|위탁|계약|입찰\s*공고|공고|견적|안내|대행|의\s*건|서비스))+\s*$')
OTHER_HEAD = re.compile(r'(폐기물\s*(처리|수집|운반)?|시설\s*(관리|물\s*관리|유지\s*관리)|환경\s*(관리|정비)|청소|경비|보험(\s*가입)?|건립(\s*공사)?|공사|입장권(\s*판매)?|판매|급식|인쇄|조사|연구|설계|감리|측량|수송|차량\s*임차|임차|임대|구매|구입|소프트웨어\s*개발|유지\s*보수|관리)$')
HEAD_OWNED = {'cleaning': re.compile(r'청소|환경\s*정비'), 'security': re.compile(r'경비'), 'transport': re.compile(r'임차|수송'),
              'sw': re.compile(r'소프트웨어\s*개발|유지\s*보수|관리'), 'elevator': re.compile(r'관리|유지\s*보수'),
              'geology': re.compile(r'조사|연구')}


# Audit R3-B: a title headed by translation or publication work ("…회의록 제6~10권 번역·해제·발간") is publishing.
PUBLISH_HEAD = re.compile(r'(번\s*역|해\s*제|발\s*간|편\s*찬|출\s*판)$')


def title_head(title):
    t = TITLE_TAIL.sub('', title or '').strip()
    return t[-12:]


# 건물청소서비스 is building cleaning: water tanks, roads, rivers, vehicles and equipment are other work.
NOT_BUILDING = re.compile(r'저수조|물\s*탱크|배수지|정수|수도\s*시설|도로|노면|하천|해변|해수욕장|수중|계곡|냉방기|난방기|냉난방|공조|에어컨|덕트|정화조|하수|측구|배수로|쓰레기|폐기물|차량|버스|전동차|방역|소독\s*용역|굴뚝|탱크|수조|관로')


# Audit R2-B: the amount-band tag "(제한경쟁·3억원미만)" is no part of the title's head.
BAND_TAG = re.compile(r'[\(（]\s*(수의계약|제한경쟁|일반경쟁|지명경쟁)\s*[·ㆍ・]\s*[\d.]+\s*(천만|억)?\s*원\s*(미만|이상)\s*[\)）]')


def water_tank_license(license_text):
    """Audit R2-B: a licence list of water-tank or other non-building cleaning (저수조청소업) without building cleaning names no
    건물청소서비스."""
    lic = license_text or ''
    return switches.AUDIT_FIXES2 and bool(NOT_BUILDING.search(lic)) and not re.search(r'건\s*물\s*(위\s*생|청\s*소)', lic)


def head_conflict(fam, title):
    """The title's head names a different service than the family's own."""
    if switches.AUDIT_FIXES2:
        title = BAND_TAG.sub(' ', title or '')
    if fam == 'cleaning' and NOT_BUILDING.search(title or ''):
        return True
    head = title_head(title)
    m = OTHER_HEAD.search(head) or switches.AUDIT_FIXES3 and PUBLISH_HEAD.search(head)
    if not m:
        return False
    own = HEAD_OWNED.get(fam)
    return not (own and own.search(m.group(0)))


EVENT_SUBFAMILIES = ('festival', 'exhibition', 'international', 'conference')
CATALOG_FAMILY = [
    (r'축제', 'festival'), (r'국제행사', 'international'), (r'회의기획', 'conference'), (r'전시회기획', 'exhibition'),
    (r'전시부스|전시홍보관', 'exhibition'), (r'행사기획', 'event'), (r'통학운송|통근운송|도로여객운송', 'transport'),
    (r'청소', 'cleaning'), (r'경비', 'security'), (r'동영상', 'video'), (r'디자인서비스$', 'design'),
    (r'소프트웨어|정보시스템|정보인프라|운영위탁|데이터처리|빅데이터|인터넷지원|공간정보', 'sw'), (r'승강기', 'elevator'),
    (r'우편발송', 'mail'), (r'지질', 'geology'), (r'유수율', 'water'),
]
AMOUNT_CAP = re.compile(r'(\d+(?:\.\d+)?)\s*억\s*원\s*미만에\s*한함')


@dataclass
class Product:
    code: str
    name: str
    note: str
    cap: float | None = None


@dataclass
class Catalog:
    by_code: dict = field(default_factory=dict)
    by_name: dict = field(default_factory=dict)
    service_family: dict = field(default_factory=dict)


_CATALOG = None


def load(data_dir=None):
    global _CATALOG
    if _CATALOG is not None:
        return _CATALOG
    paths = []
    if data_dir:
        paths.append(Path(data_dir) / '법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv')
    env = os.environ.get('PPS_DATA_DIR')
    if env:
        paths.append(Path(env) / '법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv')
    paths.append(HERE / 'assets/catalog.csv')
    cat = Catalog()
    for p in paths:
        if p.is_file():
            with open(p, encoding='utf-8-sig', newline='') as fh:
                for row in csv.DictReader(fh):
                    code, name, note = row['세부품명번호'].strip(), row['세부품명'].strip(), row.get('특이사항') or ''
                    cap = AMOUNT_CAP.search(note)
                    prod = Product(code, name, note, float(cap.group(1)) * EOK if cap else None)
                    cat.by_code[code] = prod
                    cat.by_name[re.sub(r'\s', '', name)] = prod
                    for pat, fam in CATALOG_FAMILY:
                        if re.search(pat, name):
                            cat.service_family[code] = fam
                            break
            break
    _CATALOG = cat
    return cat


def table_title(lines, k, m):
    """Audit R3-B: a header row "용 역 명 | 세부내역 | 입찰 등록 마감 일시 및 방법" (three or more cells, the neighbouring cell another
    short header, not the value of a "용 역 명 | …" key-value row) labels a column; the title is that column's cell in the next row
    with as many cells. Changes prompts (switches.FIX3_PROMPTS)."""
    cells, pos = lines[k].text.split('|'), 0
    for c, cell in enumerate(cells):
        if pos <= m.start(1) < pos + len(cell):
            break
        pos += len(cell) + 1
    else:
        return ''
    if len(cells) < 3 or re.sub(r'\s', '', cell) != re.sub(r'\s', '', m.group(1)):
        return ''
    beside = cells[c + 1] if c + 1 < len(cells) else cells[c - 1]
    if not 0 < len(beside.strip()) <= 16 or re.search(r'\d', beside):
        return ''
    row = next((x.text for x in lines[k + 1:k + 4] if x.text.strip()), '').split('|')
    return row[c].strip() if len(row) == len(cells) else ''


def project_titles(notice):
    """Project-name values of the 공고문 (용역명·사업명·건명 lines), in order."""
    out = []
    lines = [ln for ln in notice.lines if ln.doc_type == '공고문']
    for k, ln in enumerate(lines[:200]):
        m = (TITLE_LABEL_FIX if switches.AUDIT_FIXES else TITLE_LABEL).search(ln.text)
        if not m:
            continue
        value = m.group(2).strip(' :：|‣·-')
        if switches.AUDIT_FIXES3 and switches.FIX3_PROMPTS:
            value = table_title(lines, k, m) or value
        if len(value) < 4:
            value = next((x.text.strip() for x in lines[k + 1:k + 4] if len(x.text.strip()) >= 4), '')
        if value:
            out.append(value[:160])
    return out


def first_project_line(notice):
    for ln in [x for x in notice.lines if x.doc_type == '공고문'][:40]:
        if re.search(r'용역|구매|사업|제작|운영|위탁', ln.text) and len(ln.text.strip()) >= 6:
            return ln.text.strip()[:160]
    return ''


def amount_ok(P, limit, basis):
    if limit is None:
        return True
    if P is None:
        return None
    if basis == 'estimate_below':
        return P < limit
    if basis == 'project_below':
        return P * 1.1 < limit
    if basis == 'estimate_floor':
        return P >= limit
    return True


def cited_products(text, cat):
    found = [cat.by_code[c] for c in re.findall(r'(?<!\d)(\d{10})(?!\d)', text) if c in cat.by_code]
    flat = re.sub(r'\s', '', text)
    return found + [p for name, p in cat.by_name.items() if len(name) >= 3 and name in flat]


def admits(p, P):
    """The product's designation covers a purchase at P (its 추정가격 limit)."""
    return P is None or p.cap is None or P < p.cap


def cites_listed(text, cat, P, services_only=False):
    """The line names a listed competition product (10-digit code or 세부품명) whose designation admits P."""
    return any(admits(p, P) and (not services_only or p.code[0] in '789') for p in cited_products(text, cat))


def title_names_other_work(titles, clause, cat):
    """Probe (switches.V12_TITLE_OBJECT): the clause cites service products with title words (SERVICE_FAMILIES), and an
    informative notice title matches none of them, so the title names other work than the certified product."""
    fams = {cat.service_family[p.code] for p in cited_products(clause, cat) if p.code in cat.service_family}
    table = SERVICE_FAMILIES_FIX if switches.AUDIT_FIXES else SERVICE_FAMILIES
    pats = [t for f, _, t, *_ in table if f in fams and t]
    title = ' '.join(titles[:2])
    sw = switches.V12_TITLE_SW and 'sw' in fams          # audit RA V12TS: the sw family has no title words
    return (bool(pats) or sw) and not placeholder_title(title) and not any(re.search(t, title) for t in pats) \
        and not (sw and sw_service_title(title))


def cited_service_families(notice, cat):
    found = set()
    for doc in notice.docs:
        text = doc['text']
        for m in re.finditer(r'직접\s*생산|세부\s*품명\s*번호', text):
            for c in re.findall(r'(?<!\d)(\d{10})(?!\d)', text[max(0, m.start() - 120):m.end() + 160]):
                if c in cat.service_family:
                    found.add(cat.service_family[c])
    return found


@dataclass
class Scope:
    competitive: bool | None     # None = cannot tell (no rule decides v10–v18)
    basis: str
    detail: str = ''


WORK_LABEL = re.compile(r'(사\s*업|용\s*역|과\s*업|구\s*매|입\s*찰|계\s*약)\s*(명|내\s*용|개\s*요|범\s*위|건\s*명)|주\s*요\s*(내\s*용|과\s*업)')


def overview_work(notice):
    """The overview lines that state the work (사업명·과업내용 …), for notices whose title is withheld."""
    return ' / '.join(ln.text.strip() for ln in notice.lines[:250]
                      if ln.doc_type == '공고문' and ln.sec in ('OVERVIEW', 'TOP', 'OTHER') and WORK_LABEL.search(ln.text))


def titled(fam, tpat, text):
    if fam == 'sw':
        return sw_service_title(text)
    return bool(text and re.search(tpat, text))


# Audit R2-B: 디자인서비스 is design creation; a plan (진흥계획 수립), IP work (상표권·디자인 확보, 출원) or programme operation
# (액셀러레이팅, 육성, 프로그램 운영) with 디자인 in the title is other work.
DESIGN_NOT_CREATION = re.compile(r'계\s*획|수\s*립|마\s*스\s*터\s*플\s*랜|로\s*드\s*맵|상\s*표|특\s*허|디\s*자\s*인\s*권|출\s*원|권\s*리\s*확\s*보'
                                 r'|액\s*셀\s*러\s*레\s*이\s*팅|육\s*성|지\s*원\s*사\s*업|프\s*로\s*그\s*램\s*(기\s*획\s*및\s*)?운\s*영')
SW_BUILD = re.compile(r'(플랫폼|시스템|아카이브|DB|데이터\s*베이스|홈페이지|누리집|앱|어플리케이션)[^/]{0,8}(구축|개발|고도화)')


def classify_service(notice, meta, cat):
    families = service_families()
    title = ' / '.join(project_titles(notice))
    license_text = str(meta.license or '')
    head = title or first_project_line(notice)
    cited = cited_service_families(notice, cat)
    if cited:
        placeholder = bool(re.fullmatch(r'\s*\[?[가-힣]{1,4}명\]?\s*', head or ''))
        for fam, code, tpat, lpat, limit, basis in families:
            if fam not in cited:
                continue
            lic = r'비디오물|영상|방송' if fam == 'video' else lpat
            # A cited certificate names the procured service only when the title or license agrees or the title is
            # withheld; an informative title for other work keeps the purchase non-competitive (talkboard: v12 is judged
            # by the procured object, not by the certificate's 세부품명).
            by_title = titled(fam, tpat, head) and not head_conflict(fam, head)
            if not (by_title or (lic and license_text and re.search(lic, license_text)) or placeholder):
                continue
            if fam == 'cleaning' and (NOT_BUILDING.search(head or '') or water_tank_license(license_text)):
                continue
            ok = amount_ok(meta.P, limit, basis)
            return Scope(ok, f'service_cited:{fam}', head[:80])
    for fam, code, tpat, lpat, limit, basis in families:
        by_title = titled(fam, tpat, title) and not head_conflict(fam, title)
        by_license = bool(lpat and license_text and re.search(lpat, license_text))
        if fam in EVENT_SUBFAMILIES and not by_title:
            continue
        if not (by_title or by_license):
            continue
        # A license names who may bid, not what is bought: it assigns a family only when the title names no other work
        # (dev: 전산기기 임차 and 그린PC 보급 under an SW license are not SW services). SW services need the work named: in
        # the title, or in the overview when the title is withheld.
        if not by_title and not placeholder_title(title) and (fam == 'sw' or any(head_conflict(fam, t) for t in project_titles(notice)[:2])):
            continue
        if fam == 'sw' and not by_title and not sw_service_title(overview_work(notice)):
            continue
        if fam == 'design' and (not by_title or re.search(r'설계|공간|인테리어|실내|조성|시공|설치', title) or SW_BUILD.search(title)
                                or switches.AUDIT_FIXES2 and DESIGN_NOT_CREATION.search(title)):
            continue
        if fam == 'security' and re.search(r'기계\s*경비|특수\s*경비', title + ' ' + license_text):
            continue
        if fam == 'cleaning' and (NOT_BUILDING.search(title or '') or water_tank_license(license_text)):
            continue
        ok = amount_ok(meta.P, limit, basis)
        return Scope(ok, f'service:{fam}', title[:80])
    return Scope(False, 'service:none', title[:80])


def classify_goods(meta, cat):
    if not meta.codes:
        return Scope(None, 'goods:no_code')
    hits = []
    for name, code in meta.codes:
        prod = cat.by_code.get(code) or cat.by_name.get(re.sub(r'\s', '', name))
        if prod is None:
            hits.append(False)
            continue
        if prod.cap is not None and meta.P is not None and meta.P >= prod.cap:
            hits.append(False)
            continue
        hits.append(True)
    if all(hits):
        return Scope(True, 'goods:all_listed')
    if not any(hits):
        return Scope(False, 'goods:none_listed')
    return Scope(None, 'goods:mixed')


# Audit E: the orderer's own 나라장터 registration of a designated competition product (조항호내용 "…지정·고시한 제품",
# "…지정 공고한 물품", "중기간경쟁제품 …"; 판로지원법 제6조) decides the object when the catalog match found nothing.
# Same wording as judge.COMPETITION_REGISTERED.
DESIGNATED_REGISTRATION = re.compile(r'중기간\s*경쟁\s*제품|지정\s*[.·]?\s*고시한\s*제품|지정\s*공고한\s*물품')


def classify(notice, meta, cat=None):
    if switches.SCOPE_FIXES and not (switches.AUDIT_FIXES and switches.AUDIT_FIXES2 and switches.AUDIT_FIXES3):
        saved = switches.AUDIT_FIXES, switches.AUDIT_FIXES2, switches.AUDIT_FIXES3
        switches.AUDIT_FIXES = switches.AUDIT_FIXES2 = switches.AUDIT_FIXES3 = True
        try:
            return classify(notice, meta, cat)
        finally:
            switches.AUDIT_FIXES, switches.AUDIT_FIXES2, switches.AUDIT_FIXES3 = saved
    cat = cat or load()
    if meta.work == '물품':
        scope = classify_goods(meta, cat)
    elif meta.work == '용역':
        scope = classify_service(notice, meta, cat)
    else:
        return Scope(None, 'unknown_work')
    if switches.AUDIT_FIXES and scope.basis in ('service:none', 'goods:no_code') and DESIGNATED_REGISTRATION.search(str(meta.clause or '')):
        return Scope(True, scope.basis + ':registered', scope.detail)
    return scope
