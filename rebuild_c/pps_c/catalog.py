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

HERE = Path(__file__).resolve().parent
EOK = 1e8

TITLE_LABEL = re.compile(r'(용\s*역\s*명|사\s*업\s*명|공\s*고\s*명|입\s*찰\s*건\s*명|공\s*고\s*건\s*명|사\s*업\s*건\s*명|건\s*명|과\s*업\s*명|계\s*약\s*건\s*명|(?<!부)(?<!부\s)품\s*명(?=\s*[:：]))\s*[:：|]?\s*(.*)')
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
FACILITY_SYSTEM = re.compile(r'(태양광|발전|냉난방|냉방|난방|공조|소방|급수|배수|승강기|전기|조명|음향|방송|기계\s*설비|보일러|하수|정수|주차\s*관제|CCTV|경비|방범|감시|블록)\s*(설비\s*)?(시스템|체계)')
# A title that names some work: a license alone cannot then assign a family the title does not name.
TITLE_WORK = re.compile(r'용\s*역|사\s*업|구\s*축|유\s*지|보\s*수|관\s*리|운\s*영|개\s*발|구\s*매|구\s*입|임\s*차|제\s*작|설\s*치|위\s*탁|서\s*비\s*스|교\s*육|조\s*성|지\s*원|행\s*사|개\s*선|대\s*행|컨\s*설\s*팅|연\s*구|조\s*사|보\s*급')


def sw_service_title(title):
    t = FACILITY_SYSTEM.sub(' ', title or '')
    return bool(SW_SERVICE.search(t))


def placeholder_title(title):
    return not (title or '').strip() or bool(re.fullmatch(r'\s*\[?[가-힣]{1,4}명\]?\s*', title or '')) or not TITLE_WORK.search(title or '')


# The procured service is what the title ends with. A title whose head names another kind of work ("…박람회장 시설관리
# 용역", "…엑스포공원 … 폐기물처리 용역", "…꽃박람회 입장권 판매 대행") is that work, whatever event it serves.
TITLE_TAIL = re.compile(r'(\s*[\(（][^)）]*[\)）]|\s*(용\s*역|사\s*업|업체\s*선정|위탁|계약|입찰\s*공고|공고|견적|안내|대행|의\s*건|서비스))+\s*$')
OTHER_HEAD = re.compile(r'(폐기물\s*(처리|수집|운반)?|시설\s*(관리|물\s*관리|유지\s*관리)|환경\s*(관리|정비)|청소|경비|보험(\s*가입)?|건립(\s*공사)?|공사|입장권(\s*판매)?|판매|급식|인쇄|조사|연구|설계|감리|측량|수송|차량\s*임차|임차|임대|구매|구입|소프트웨어\s*개발|유지\s*보수|관리)$')
HEAD_OWNED = {'cleaning': re.compile(r'청소|환경\s*정비'), 'security': re.compile(r'경비'), 'transport': re.compile(r'임차|수송'),
              'sw': re.compile(r'소프트웨어\s*개발|유지\s*보수|관리'), 'elevator': re.compile(r'관리|유지\s*보수'),
              'geology': re.compile(r'조사|연구')}


def title_head(title):
    t = TITLE_TAIL.sub('', title or '').strip()
    return t[-12:]


# 건물청소서비스 is building cleaning: water tanks, roads, rivers, vehicles and equipment are other work.
NOT_BUILDING = re.compile(r'저수조|물\s*탱크|배수지|정수|수도\s*시설|도로|노면|하천|해변|해수욕장|수중|계곡|냉방기|난방기|냉난방|공조|에어컨|덕트|정화조|하수|측구|배수로|쓰레기|폐기물|차량|버스|전동차|방역|소독\s*용역|굴뚝|탱크|수조|관로')


def head_conflict(fam, title):
    """The title's head names a different service than the family's own."""
    if fam == 'cleaning' and NOT_BUILDING.search(title or ''):
        return True
    head = title_head(title)
    m = OTHER_HEAD.search(head)
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


def project_titles(notice):
    """Project-name values of the 공고문 (용역명·사업명·건명 lines), in order."""
    out = []
    lines = [ln for ln in notice.lines if ln.doc_type == '공고문']
    for k, ln in enumerate(lines[:200]):
        m = TITLE_LABEL.search(ln.text)
        if not m:
            continue
        value = m.group(2).strip(' :：|‣·-')
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


SW_BUILD = re.compile(r'(플랫폼|시스템|아카이브|DB|데이터\s*베이스|홈페이지|누리집|앱|어플리케이션)[^/]{0,8}(구축|개발|고도화)')


def classify_service(notice, meta, cat):
    title = ' / '.join(project_titles(notice))
    license_text = str(meta.license or '')
    head = title or first_project_line(notice)
    cited = cited_service_families(notice, cat)
    if cited:
        placeholder = bool(re.fullmatch(r'\s*\[?[가-힣]{1,4}명\]?\s*', head or ''))
        for fam, code, tpat, lpat, limit, basis in SERVICE_FAMILIES:
            if fam not in cited:
                continue
            lic = r'비디오물|영상|방송' if fam == 'video' else lpat
            # A cited certificate names the procured service only when the title or license agrees or the title is
            # withheld; an informative title for other work keeps the purchase non-competitive (talkboard: v12 is judged
            # by the procured object, not by the certificate's 세부품명).
            by_title = titled(fam, tpat, head) and not head_conflict(fam, head)
            if not (by_title or (lic and license_text and re.search(lic, license_text)) or placeholder):
                continue
            if fam == 'cleaning' and NOT_BUILDING.search(head or ''):
                continue
            ok = amount_ok(meta.P, limit, basis)
            return Scope(ok, f'service_cited:{fam}', head[:80])
    for fam, code, tpat, lpat, limit, basis in SERVICE_FAMILIES:
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
        if fam == 'design' and (not by_title or re.search(r'설계|공간|인테리어|실내|조성|시공|설치', title) or SW_BUILD.search(title)):
            continue
        if fam == 'security' and re.search(r'기계\s*경비|특수\s*경비', title + ' ' + license_text):
            continue
        if fam == 'cleaning' and NOT_BUILDING.search(title or ''):
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


def classify(notice, meta, cat=None):
    cat = cat or load()
    if meta.work == '물품':
        return classify_goods(meta, cat)
    if meta.work == '용역':
        return classify_service(notice, meta, cat)
    return Scope(None, 'unknown_work')
