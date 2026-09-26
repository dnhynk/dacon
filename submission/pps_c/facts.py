"""Per-notice fact bundle: CPU facts, family candidates, and readings (model over CPU default)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import catalog, families as F, meta as M, record, switches

SW_LICENSE = ('소프트웨어사업자', '컴퓨터관련서비스')
# A deliverable that is software (소프트웨어 진흥법 제2조): the title or overview names a program, information system,
# website, platform or database being built, upgraded, maintained or run.
# "SW 교육", "소프트웨어 캠프" and "역량 강화 프로그램 개발" are education, not software (제2조 제9호 소프트웨어교육 is its own term).
SW_OBJECT = re.compile(r'(소프트웨어|S/?W(?![A-Za-z]))(?!\s*(교육|캠프|중심|인재|체험|교실|동아리|경진|대회|강사|코딩|교구|박람회|축제|주간))'
                       r'|정보\s*시스템|전산\s*(시스템|프로그램)|홈페이지|누리집|웹\s*사이트|포털|정보망|데이터\s*베이스'
                       r'|플랫폼\s*(구축|개발|고도화|재구축|유지|운영|개선|개편)|DB\s*(구축|개발|고도화|유지)|챗봇|ERP|그룹웨어|어플리케이션|애플리케이션'
                       r'|(AI|인공\s*지능|빅\s*데이터|클라우드)\s*(기반\s*)?(시스템|플랫폼|서비스)?\s*(구축|개발|고도화|도입)'
                       r'|앱\s*(개발|구축|고도화|유지|운영)|(컴퓨터|응용|업무|관리|행정)\s*프로그램\s*(개발|구축|고도화|유지|개선)'
                       r'|시스템\s*(구축|개발|고도화|재구축|유지|운영|개선|개편|전환|통합|기능)|(관리|정보|통합|운영|행정|업무|학사|예약|민원|전자|온라인|모바일)\s*시스템')
SW_OVERVIEW = re.compile(r'(사\s*업|용\s*역|과\s*업|입\s*찰|계\s*약)\s*(명|내\s*용|개\s*요|범\s*위|건\s*명)|목\s*적')


# A title whose head is study, audit, planning, teaching or event work names the system it studies, not a deliverable.
STUDY_HEAD = re.compile(r'(연\s*구|조\s*사|컨\s*설\s*팅|감\s*리|계\s*획\s*수\s*립|방\s*안|전\s*략|진\s*단|평\s*가|분\s*석|설\s*계|기\s*획|ISP|ISMP|BPR'
                        r'|마\s*스\s*터\s*플\s*랜|교\s*육|강\s*의|훈\s*련|연\s*수|행\s*사|홍\s*보)$')
# Media, design and publishing registrations: with them, SW사업자 offered only in its 디지털콘텐츠 category buys content.
MEDIA_LICENSE = re.compile(r'비디오물|방송\s*영상|문화\s*산업|디자인|출판|광고\s*대행|공연|음반')
# Audit R3-B: the e-learning content registration (이러닝콘텐츠업) is a content registration too.
MEDIA_LICENSE3 = re.compile(MEDIA_LICENSE.pattern + r'|이\s*러\s*닝\s*콘\s*텐\s*츠')
# Audit R3-B: running a course or an event ("…실습 교육 위탁운영") is teaching or event work too.
TEACHING_RUN = re.compile(r'(교\s*육|강\s*의|훈\s*련|연\s*수|행\s*사)\s*(위\s*탁\s*)?운\s*영$')
# Audit R3-B: an overview line names the project only as a label line ("사업명 :", "과업내용", "목적"); a sentence that mentions
# the orderer's 홈페이지 as a reporting channel ("…계약내용과 무관한 … 홈페이지([URL]) … 신고") names no deliverable.
SW_OVERVIEW_LABEL = re.compile(r'^\W*(?:[가-하]\s*[\.\)]|\d{1,2}\s*[\.\)]|\(\s*\d{1,2}\s*\))?\s*(?:(사\s*업|용\s*역|과\s*업|입\s*찰|계\s*약)\s*(명|내\s*용|개\s*요|범\s*위|건\s*명)'
                               r'|((사\s*업|과\s*업|용\s*역)\s*의\s*)?목\s*적)')


def fix2():
    """Round-2 SW-scope fixes: with AUDIT_FIXES2, or with V20_SCOPE (this scope feeds only v20)."""
    return switches.AUDIT_FIXES2 or switches.V20_SCOPE


def fix3():
    return switches.AUDIT_FIXES3 or switches.V20_SCOPE


def study_head(head):
    return bool(STUDY_HEAD.search(head) or fix3() and TEACHING_RUN.search(head))


def sw_goods(meta):
    """A goods purchase is a software business only when what is bought is software: every listed 세부품명 is 소프트웨어류
    (4323); an equipment list with one software line is an equipment purchase."""
    return bool(meta.codes) and all(code.startswith('4323') or '소프트웨어' in name for name, code in meta.codes)


def content_only(text):
    sw = re.findall(r'소프트웨어\s*사업자\s*[(（]([^)）]*)', text)
    return bool(sw) and all('콘텐츠' in x for x in sw) and bool((MEDIA_LICENSE3 if fix3() else MEDIA_LICENSE).search(text))


def sw_license(bundle):
    lic = str(bundle.meta.license or '')
    if any(k in lic for k in SW_LICENSE):
        return not content_only(lic)
    return any('소프트웨어사업자' in ln.text.replace(' ', '') and not content_only(ln.text)
               for ln in bundle.notice.lines if ln.sec == 'QUAL')


def sw_text(t):
    """Audit R2-B: a title or overview naming 감리 anywhere studies a system (no deliverable), and ISO-type management systems
    (안전보건경영시스템, 품질경영체계) are no information systems."""
    if not fix2():
        return t
    if fix3():
        t = catalog.FACILITY_SYSTEM.sub(' ', t)     # audit R3-B: facility monitoring systems (수질원격감시시스템), as in the catalog
    return '' if catalog.SW_AUDIT_TITLE.search(t) else catalog.MGMT_SYSTEM.sub(' ', t)


def sw_object(t):
    """A named software object; with AUDIT_FIXES3 not one that is the object of other work (catalog.NOT_SW_AFTER)."""
    t = sw_text(t)
    if not fix3():
        return bool(SW_OBJECT.search(t))
    return any(not catalog.NOT_SW_AFTER.match(t, m.end()) for m in SW_OBJECT.finditer(t))


# Expert audit X6 C20a (switch V20_CONTENT; runs/rebuild_c/transfer_20260925/audit/expert/X6/REPORT.md): 소프트웨어 진흥법
# 제2조 — a title whose object is teaching or training (제9호 소프트웨어교육 is no SW사업), a programme or project run, content
# production, insurance, recruitment, water-quality telemetry upkeep, an ISO management system, a data-provider selection or
# PC and equipment distribution names no SW사업, even when an SW사업자 licence is required (dev DEV-135 그린PC 보급 = 0; the
# five dev positives name security operation, systems, equipment rental and licences). The object is the last such word with
# no system word after it, in a label title or the band-tagged title line, orderer tokens removed.
NOT_SW_TITLE = re.compile(r'교\s*육(?!\s*(청|부|지\s*원\s*청|용|행\s*정|정\s*보))|연\s*수(?!\s*원)|((?<!정보화)(?<!정보화\s)사\s*업|프\s*로\s*젝\s*트|프\s*로\s*그\s*램)\s*(위\s*탁\s*)?운\s*영'
                          r'|콘\s*텐\s*츠|컨\s*텐\s*츠|보\s*험|채\s*용|측\s*정\s*기\s*기|원\s*격\s*감\s*시\s*(시\s*스\s*템)?|(?<![A-Za-z])TMS|경\s*영\s*시\s*스\s*템'
                          r'|제\s*공\s*기\s*관|(PC|컴\s*퓨\s*터|장\s*비|단\s*말\s*기|기\s*기)\s*보\s*급')
SYSTEM_WORD = re.compile(r'시\s*스\s*템|프\s*로\s*그\s*램|솔\s*루\s*션|플\s*랫\s*폼|홈\s*페\s*이\s*지|누\s*리\s*집|데\s*이\s*터\s*베\s*이\s*스|DB|앱|어\s*플'
                         r'|애\s*플|서\s*버|네\s*트\s*워\s*크|소\s*프\s*트\s*웨\s*어|S/?W(?![A-Za-z])')
ORDERER_TOKEN = re.compile(r'\[[^\]]*\]')


def title_texts(bundle):
    """Label titles and band-tagged 공고문 lines, cut at the band tag, orderer tokens removed."""
    tagged = [ln.text for ln in [x for x in bundle.notice.lines if x.doc_type == '공고문'][:60] if catalog.BAND_TAG.search(ln.text)]
    out = []
    for t in list(bundle.titles) + tagged:
        m = catalog.BAND_TAG.search(t)
        out.append(ORDERER_TOKEN.sub(' ', t[:m.start()] if m else t))
    return out


def not_sw_title(bundle):
    for t in title_texts(bundle):
        hits = list(NOT_SW_TITLE.finditer(t))
        if hits and not SYSTEM_WORD.search(t, hits[-1].end()):
            return True
    return False


def sw_named(bundle):
    """The title or a project-overview line names a software deliverable."""
    if switches.V20_CONTENT and not_sw_title(bundle):
        return False
    if any(sw_object(t) and not study_head(catalog.title_head(t)) for t in bundle.titles):
        return True
    overview = SW_OVERVIEW_LABEL.match if fix3() else SW_OVERVIEW.search
    return any(sw_object(ln.text) and not study_head(catalog.title_head(ln.text)) for ln in bundle.notice.lines[:200]
               if ln.doc_type == '공고문' and ln.sec in ('OVERVIEW', 'TOP') and overview(ln.text))


def sw_license_framing(bundle):
    """The SW사업자 licence frames the purchase as software, unless the title's head is study or audit work (audit E: a
    감리 project that asks for an SW사업자 licence studies the system, as the named path already treats it)."""
    if not sw_license(bundle) or switches.V20_CONTENT and not_sw_title(bundle):
        return False
    return not (switches.AUDIT_FIXES and any(study_head(catalog.title_head(t)) for t in bundle.titles)
                or fix2() and any(catalog.SW_AUDIT_TITLE.search(t) for t in bundle.titles))


@dataclass
class Bundle:
    notice: record.Notice
    meta: M.Meta
    scope: catalog.Scope
    titles: list
    cands: dict = field(default_factory=dict)          # family -> [Line]
    readings: dict = field(default_factory=dict)       # family -> {line index: {field: value}}
    source: dict = field(default_factory=dict)         # family -> 'model' | 'cpu'
    sw_candidate: bool = False
    sw_project: str | None = None                      # 사업 성격 value
    sw_source: str = 'cpu'
    values: dict = field(default_factory=dict)         # stated 예산·추정가격·계약방법 read by the model (v24)

    def read(self, fam, ln):
        return self.readings.get(fam, {}).get(ln.i, {})


def sw_default(bundle):
    """CPU reading when the model gives none: the orderer's own framing decides (goods by what is bought, services by an
    SW사업자 license requirement; dev: 전산기기 임차 and 라이선스 구매 under that framing are v20 cases)."""
    if bundle.meta.work == '물품':
        return (F.SW_PROJECT.values[0] if sw_goods(bundle.meta) and (switches.SW_GOODS_ANY or sw_license(bundle))
                else F.SW_PROJECT.values[1])
    if sw_license_framing(bundle) or sw_named(bundle):
        return F.SW_PROJECT.values[0]
    return F.SW_PROJECT.values[2] if not bundle.sw_candidate else F.UNKNOWN


def build(rec, cat=None):
    notice = record.build(rec)
    titles = catalog.project_titles(notice)
    meta = M.build(notice, ' '.join(titles))
    scope = catalog.classify(notice, meta, cat)
    b = Bundle(notice=notice, meta=meta, scope=scope, titles=titles)
    b.sw_candidate = F.sw_candidate(notice, meta, titles)
    for name, fam in F.FAMILIES.items():
        if name == 'sw' and not b.sw_candidate:
            continue
        if name == 'brief' and not meta.negotiation:
            continue
        if name == ('model' if switches.V9_READ2 else 'model2'):
            continue          # v9 is read by one of the two model families
        cands = fam.selector(notice)
        if cands or name == 'sw':
            b.cands[name] = cands
    for name, cands in b.cands.items():
        fam = F.FAMILIES[name]
        b.readings[name] = {ln.i: fam.default(notice, ln) for ln in cands}
        b.source[name] = 'cpu'
    b.sw_project = sw_default(b) if b.sw_candidate or sw_goods(meta) else F.SW_PROJECT.values[2]
    return b


def apply_model(b, name, lines, top):
    """Model readings override CPU defaults field by field; 불명 keeps the CPU default."""
    if lines is None:
        return False
    cur = b.readings.setdefault(name, {})
    for i, got in lines.items():
        base = dict(cur.get(i, {}))
        for k, v in got.items():
            if k == '이유' or v != F.UNKNOWN:
                base[k] = v
        cur[i] = base
    b.source[name] = 'model'
    if name == 'sw' and top and b.meta.work != '물품':
        v = top.get(F.SW_PROJECT.name)
        # A reading of "software" needs the orderer's own framing: an SW사업자 license or a named software deliverable.
        if v == F.SW_PROJECT.values[0] and not (sw_license_framing(b) or sw_named(b)):
            v = None
        if v and v != F.UNKNOWN:
            b.sw_project, b.sw_source = v, 'model'
    if name == 'values' and top:
        b.values = dict(top)
    return True
