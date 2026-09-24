"""Competition-service classifier (CSC): is a service notice's purchase a 중소기업자간 경쟁제품 service?

The supplied catalog (중기부고시_경쟁제품_세부품명.csv) designates services by name, with conditions in 특이사항.
Service notices usually carry no 세부품명 code, so the purchase is identified from the project title (사업명·용역명·
건명 lines of the 공고문) and the registered license restriction (meta 면허업종제한목록), then the entry's condition is
applied: event/conference/international events below 10억, festivals below 3억 (추정가격), 소프트웨어 진흥법 제48조
services below 20억 사업금액 (추정가격 + VAT), geological surveys from 1천만; security excludes machine and special
guarding, and video is limited to a public body's promotion (the title must say 홍보).

classify(record) -> {'family', 'code', 'condition': 'met'|'not_met'|'no_condition', 'competition': bool|None,
                     'title', 'signals'}; competition is None when no catalog service is identified.
"""
import re
import unicodedata

TITLE_LABEL = re.compile(r'(용\s*역\s*명|사\s*업\s*명|입\s*찰\s*건\s*명|공\s*고\s*건\s*명|사\s*업\s*건\s*명|건\s*명|과\s*업\s*명|계\s*약\s*건\s*명)\s*[:：|]?\s*(.*)')
FAMILIES = [
    # (family, catalog code, title pattern, license pattern, amount limit on 추정가격 or None, limit basis)
    ('festival', '9015189001', r'축제|문화제', r'행사대행', 300_000_000, 'estimate'),
    ('exhibition', '8014198801', r'전시회|박람회|엑스포|기획전|전시\s*연출|전시\s*운영', r'행사대행', None, None),
    ('international', '8014198901', r'국제\s*(행사|회의|포럼|컨퍼런스|대회)', r'행사대행', 1_000_000_000, 'estimate'),
    ('conference', '8014190201', r'회의|포럼|컨퍼런스|세미나|심포지엄|학술\s*대회|워크숍', r'행사대행', 1_000_000_000, 'estimate'),
    ('event', '8014199001', r'행사|페스티벌|페스타|기념식|시상식|개막식|폐막식|공연|버스킹|체험\s*프로그램|캠프|대회\s*운영|설명회\s*운영|발표회', r'행사대행', 1_000_000_000, 'estimate'),
    ('transport', '7811189902', r'통학\s*(버스|차량)|통근\s*(버스|차량)|전세\s*버스|버스\s*임차|차량\s*임차|수학여행\s*차량', r'여객자동차|전세버스', None, None),
    ('cleaning', '7611150101', r'청소', r'청소|건물위생', None, None),
    ('security', '9212159901', r'시설\s*경비|경비\s*(용역|업무|근무)|보안\s*경비', r'시설경비', None, None),
    ('video', '8213160301', r'홍보\s*(동)?영상|(동)?영상.{0,12}홍보|홍보.{0,12}(동)?영상\s*(제작|촬영)', None, None, None),
    ('design', '8214150201', r'디자인', r'디자인', None, None),
    ('sw', '8111189901', r'정보\s*시스템|(시스템|체계)\s*(구축|개발|유지|고도화|운영|재구축|개선)|유지\s*(보수|관리)|홈페이지|누리집|플랫폼|데이터\s*(구축|처리|분석)|DB\s*구축|소프트웨어|S/?W|전산|어플리케이션|앱\s*(개발|구축)|빅데이터|인공지능|AI\s*(개발|구축)',
     r'소프트웨어사업자|컴퓨터관련서비스|정보통신', 2_000_000_000, 'project'),
    ('elevator', '7215401001', r'승강기.{0,6}(유지|보수|관리)', r'승강기', None, None),
    ('mail', '8014162201', r'우편\s*발송', None, None, None),
    ('geology', '8115179901', r'지질\s*(조사|연구)', None, 10_000_000, 'floor'),
    ('water', '8110159601', r'유수율', None, None, None),
]


def norm(text):
    return unicodedata.normalize('NFKC', text or '')


def titles(record):
    out = []
    for doc in record.get('docs', []):
        if doc.get('type') != '공고문':
            continue
        lines = [ln.strip() for ln in norm(doc.get('text', '')).splitlines()]
        for i, ln in enumerate(lines[:200]):
            m = TITLE_LABEL.search(ln)
            if not m:
                continue
            value = m.group(2).strip(' :：|')
            if len(value) < 4:
                value = next((x for x in lines[i + 1:i + 4] if len(x) >= 4), '')
            if value:
                out.append(value[:160])
        break
    return out


CITED_LICENSE = {'video': r'비디오물|영상|방송'}


def amount_condition(estimate, limit, basis):
    if limit is None:
        return 'no_condition'
    if not isinstance(estimate, (int, float)) or estimate <= 0:
        return 'unknown'
    if basis == 'estimate':
        return 'met' if estimate < limit else 'not_met'
    if basis == 'project':
        return 'met' if estimate * 1.1 < limit else 'not_met'
    return 'met' if estimate >= limit else 'not_met'


def first_project_line(record):
    """The first 공고문 line naming the procured work, when no 사업명·용역명 line exists."""
    for doc in record.get('docs', []):
        if doc.get('type') == '공고문':
            for ln in norm(doc.get('text', '')).splitlines()[:40]:
                if re.search(r'용역|구매|사업|제작|운영|위탁', ln) and len(ln.strip()) >= 6:
                    return ln.strip()[:160]
    return ''


def classify(record, products=None, cited=True):
    meta = record.get('meta', {})
    kind = meta.get('업무구분')
    if kind != '일반용역':
        return {'family': None, 'competition': None, 'reason': 'not_general_service'}
    title_text = ' / '.join(titles(record))
    license_text = norm(str(meta.get('면허업종제한목록') or ''))
    estimate = meta.get('입찰추정가격')
    cited = cited_service_families(record, products) if cited else set()
    if cited:
        # The notice's own direct-production clause names a catalog service code. It is that competition service when
        # the project matches the service (title or license), or the title is an anonymized placeholder; a cited code
        # for an unrelated task (DEV-056 style) is not. Amount conditions still apply.
        head = title_text or first_project_line(record)
        placeholder = bool(re.fullmatch(r'\s*\[?[가-힣]{1,4}명\]?\s*', head or ''))
        for family, code, tpat, lpat, limit, basis in FAMILIES:
            if family not in cited:
                continue
            lic = CITED_LICENSE.get(family, lpat)
            if not ((head and re.search(tpat, head)) or (lic and license_text and re.search(lic, license_text)) or placeholder):
                continue
            condition = amount_condition(estimate, limit, basis)
            competition = {'met': True, 'no_condition': True, 'not_met': False, 'unknown': None}[condition]
            return {'family': family, 'code': code, 'condition': condition, 'competition': competition,
                    'title': (head or '')[:120], 'signals': {'cited_code': True}}
    for family, code, tpat, lpat, limit, basis in FAMILIES:
        by_title = bool(title_text and re.search(tpat, title_text))
        by_license = bool(lpat and license_text and re.search(lpat, license_text))
        # Event subfamilies need the title; the event license alone selects the generic event entry.
        if family in ('festival', 'exhibition', 'international', 'conference') and not by_title:
            continue
        if family == 'event' and not (by_title or by_license):
            continue
        if family not in ('festival', 'exhibition', 'international', 'conference', 'event') and not (by_title or by_license):
            continue
        # SW by title alone is too broad (유지보수 of a facility); require the license or an information-system word.
        if family == 'sw' and not by_license and not re.search(r'정보\s*시스템|소프트웨어|S/?W|전산|홈페이지|누리집|플랫폼|데이터|DB|빅데이터|인공지능|어플리케이션|앱|(시스템|체계)\s*(유지\s*(보수|관리)|구축|고도화|재구축)', title_text):
            continue
        # Design service (아트디자인서비스) is named in the title; a design license alone, or spatial/interior work
        # (공간·인테리어·조성·시공·설치·설계), is not that catalog service.
        if family == 'design' and (not by_title or re.search(r'설계|공간|인테리어|실내|조성|시공|설치', title_text)):
            continue
        # Catalog notes: security excludes machine and special guarding; video is limited to a public body's promotion.
        if family == 'security' and re.search(r'기계\s*경비|특수\s*경비', title_text + ' ' + license_text):
            continue
        condition = amount_condition(estimate, limit, basis)
        competition = {'met': True, 'no_condition': True, 'not_met': False, 'unknown': None}[condition]
        return {'family': family, 'code': code, 'condition': condition, 'competition': competition,
                'title': title_text[:120], 'signals': {'title': by_title, 'license': by_license}}
    return {'family': None, 'competition': None, 'reason': 'no_catalog_service', 'title': title_text[:120]}


SW_LICENSE = re.compile(r'소프트웨어사업자|컴퓨터관련서비스')
SW_PHRASE = re.compile(r'(정보\s*시스템|경영\s*정보\s*시스템|시스템|플랫폼|홈페이지|누리집|소프트웨어|S/?W|데이터\s*베이스|DB|전산|정보화|응용\s*프로그램|어플리케이션)'
                       r'\s*(구축|개발|고도화|재구축|유지\s*(보수|관리)|운영|개선|도입|임차)')


def software_project(record, lines=80):
    """Whether the notice is a software project (소프트웨어 진흥법 제2조, judged from the notice content): the SW
    service family of classify(), a software license restriction in meta, or a strong software-project phrase in
    the opening lines of the 공고문."""
    if classify(record, cited=False).get('family') == 'sw':
        return True
    if SW_LICENSE.search(str(record.get('meta', {}).get('면허업종제한목록') or '')):
        return True
    for doc in record.get('docs', []):
        if doc.get('type') == '공고문':
            return bool(SW_PHRASE.search(chr(10).join(norm(doc.get('text', '')).splitlines()[:lines])))
    return False

CATALOG_FAMILY = [  # catalog service name pattern -> family (same families as FAMILIES)
    (r'축제', 'festival'), (r'국제행사', 'international'), (r'회의기획', 'conference'), (r'전시회기획', 'exhibition'),
    (r'전시부스|전시홍보관', 'exhibition'), (r'행사기획', 'event'), (r'통학운송|통근운송|도로여객운송', 'transport'),
    (r'청소', 'cleaning'), (r'경비', 'security'), (r'동영상', 'video'), (r'디자인서비스$', 'design'),
    (r'소프트웨어|정보시스템|정보인프라|운영위탁|데이터처리|빅데이터|인터넷지원|공간정보', 'sw'), (r'승강기', 'elevator'),
    (r'우편발송', 'mail'), (r'지질', 'geology'), (r'유수율', 'water'),
]
_CATALOG = None


def catalog_service_codes(products=None):
    """{세부품명번호: family} for the service entries of the competition catalog.

    products: the runtime's loaded catalog ({세부품명번호: row with '세부품명'}, ProductFacts.products). Without it the
    repository copy is read only when present (offline analysis); a missing file yields {} and never raises, so the
    evaluation server (whose data directory this module does not know) never fails here.
    """
    global _CATALOG
    if products is not None:
        out = {}
        for code, row in products.items():
            name = str(row.get('세부품명') or '')
            for pat, fam in CATALOG_FAMILY:
                if re.search(pat, name):
                    out[code] = fam
                    break
        return out
    if _CATALOG is None:
        import csv as _csv
        from pathlib import Path as _Path
        _CATALOG = {}
        for path in (_Path(__file__).resolve().parents[2] / 'data_open/data/법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv',):
            if path.is_file():
                with open(path, encoding='utf-8-sig', newline='') as fh:
                    for row in _csv.DictReader(fh):
                        for pat, fam in CATALOG_FAMILY:
                            if re.search(pat, row['세부품명']):
                                _CATALOG[row['세부품명번호']] = fam
                                break
                break
    return _CATALOG


def cited_service_families(record, products=None):
    """Families of catalog service codes cited next to a direct-production clause in the notice documents."""
    codes = catalog_service_codes(products)
    found = set()
    for doc in record.get('docs', []):
        text = norm(doc.get('text', ''))
        for m in re.finditer(r'직접\s*생산|세부\s*품명\s*번호', text):
            for c in re.findall(r'(?<!\d)(\d{10})(?!\d)', text[max(0, m.start() - 120):m.end() + 160]):
                if c in codes:
                    found.add(codes[c])
    return found

