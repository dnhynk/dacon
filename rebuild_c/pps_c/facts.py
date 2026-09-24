"""Per-notice fact bundle: CPU facts, family candidates, and readings (model over CPU default)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import catalog, families as F, meta as M, record

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


def sw_goods(meta):
    """A goods purchase is a software business only when what is bought is software: every listed 세부품명 is 소프트웨어류
    (4323); an equipment list with one software line is an equipment purchase."""
    return bool(meta.codes) and all(code.startswith('4323') or '소프트웨어' in name for name, code in meta.codes)


def content_only(text):
    sw = re.findall(r'소프트웨어\s*사업자\s*[(（]([^)）]*)', text)
    return bool(sw) and all('콘텐츠' in x for x in sw) and bool(MEDIA_LICENSE.search(text))


def sw_license(bundle):
    lic = str(bundle.meta.license or '')
    if any(k in lic for k in SW_LICENSE):
        return not content_only(lic)
    return any('소프트웨어사업자' in ln.text.replace(' ', '') and not content_only(ln.text)
               for ln in bundle.notice.lines if ln.sec == 'QUAL')


def sw_named(bundle):
    """The title or a project-overview line names a software deliverable."""
    if any(SW_OBJECT.search(t) and not STUDY_HEAD.search(catalog.title_head(t)) for t in bundle.titles):
        return True
    return any(SW_OBJECT.search(ln.text) and not STUDY_HEAD.search(catalog.title_head(ln.text)) for ln in bundle.notice.lines[:200]
               if ln.doc_type == '공고문' and ln.sec in ('OVERVIEW', 'TOP') and SW_OVERVIEW.search(ln.text))


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
        return F.SW_PROJECT.values[0] if sw_goods(bundle.meta) and sw_license(bundle) else F.SW_PROJECT.values[1]
    if sw_license(bundle) or sw_named(bundle):
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
        if v == F.SW_PROJECT.values[0] and not (sw_license(b) or sw_named(b)):
            v = None
        if v and v != F.UNKNOWN:
            b.sw_project, b.sw_source = v, 'model'
    if name == 'values' and top:
        b.values = dict(top)
    return True
