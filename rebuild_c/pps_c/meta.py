"""나라장터 meta facts and the regional-restriction threshold T (organizer notice 2026-09-22)."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from . import amounts, regions

EOK = 1e8
NOTICE_AMOUNT = 2.3 * EOK          # 고시금액 for v2, v14–v16 and the national regional threshold
LOCAL_SIDO_T = 3.5 * EOK
LOCAL_OTHER_T = 5.0 * EOK
CONSTRUCTION_TECH_T = 3.3 * EOK
SAFETY_CHECK_T = 1.5 * EOK

# Orderer types read from the anonymisation tokens `[수요기관(유형)]` / `[기관(유형)]` and meta 소관구분.
SIDO_TYPES = ('광역자치단체',)
FIVE_TYPES = ('기초자치단체', '교육청', '교육지원청', '초등학교', '중학교', '고등학교', '유치원', '특수학교', '학교',
              '교육기관', '지방공기업')
ORG_TOKEN = re.compile(r'\[(?:수요기관|기관)\(([^)\]]+)\)')
CONSTRUCTION_TECH = re.compile(r'건설\s*기술|설계|감리|엔지니어링|측량|지반\s*조사|타당성\s*조사')
SAFETY_CHECK = re.compile(r'안전\s*점검|정밀\s*안전\s*진단|안전\s*진단')


def _money(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _date(v):
    s = re.sub(r'\D', '', str(v or ''))
    if len(s) != 8:
        return None
    try:
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:]))
    except ValueError:
        return None


@dataclass
class Meta:
    law: str | None
    work: str | None
    method: str | None
    award: str | None
    P: float | None
    B: float | None
    P_source: str
    orderer_types: tuple
    T_lo: float
    T_hi: float
    T_basis: str
    codes: list = field(default_factory=list)
    region_flag: str | None = None
    region_sido: set = field(default_factory=set)
    region_basic: int = 0
    license: str | None = None
    license_flag: str | None = None
    clause: str | None = None
    posted: dt.date | None = None
    opening: dt.date | None = None
    urgent: bool = False
    jv: str | None = None

    @property
    def local(self):
        return self.law == '지방'

    @property
    def local_private(self):
        """항목표 비고 "지방 + 소액수의 가능": 지방계약법 수의계약."""
        return self.law == '지방' and self.method == '수의계약'

    @property
    def private(self):
        return self.method == '수의계약' or self.award == '소액수의견적'

    @property
    def negotiation(self):
        return self.award == '협상에의한계약'


def _law(v):
    v = str(v or '')
    return '지방' if '지방' in v else ('국가' if '국가' in v else None)


def _work(v):
    v = str(v or '')
    return '물품' if '물품' in v else ('용역' if '용역' in v else None)


def orderer_types(notice_lines, meta):
    found = []
    for ln in notice_lines[:40]:
        found.extend(ORG_TOKEN.findall(ln.text))
    kind = str(meta.get('소관구분') or '')
    if kind:
        found.append(kind)
    return tuple(dict.fromkeys(t.strip() for t in found if t.strip()))


def threshold(law, work, types, title, license_text):
    """Regional-restriction threshold T as an interval (lo, hi) and its basis."""
    if law != '지방':
        return NOTICE_AMOUNT, NOTICE_AMOUNT, 'national'
    subject = f'{title} {license_text or ""}'
    if work == '용역' and SAFETY_CHECK.search(subject):
        return SAFETY_CHECK_T, SAFETY_CHECK_T, 'safety_check_service'
    if work == '용역' and CONSTRUCTION_TECH.search(subject):
        return CONSTRUCTION_TECH_T, CONSTRUCTION_TECH_T, 'construction_tech_service'
    if any(t in SIDO_TYPES for t in types):
        return LOCAL_SIDO_T, LOCAL_SIDO_T, 'local_sido'
    if any(t in FIVE_TYPES for t in types) or any('세종' in t for t in types):
        return LOCAL_OTHER_T, LOCAL_OTHER_T, 'local_five'
    return LOCAL_SIDO_T, LOCAL_OTHER_T, 'local_unknown'


def parse_codes(value):
    """meta 세부품명번호목록 "우유[5013170203], 채소류[5040990101]" → [(name, code)]."""
    out = []
    for name, code in re.findall(r'([^\[\],]+?)\s*\[(\d{8,10})\]', str(value or '')):
        out.append((name.strip(), code))
    return out


def stated_estimate(notice_lines):
    """추정가격 stated in the notice body when meta has none."""
    for ln in notice_lines[:120]:
        if re.search(r'추\s*정\s*가\s*격', ln.text):
            vals = [m.value for m in amounts.money(ln.text) if m.value >= 1e5]
            if vals:
                return vals[0]
    return None


def build(notice, title=''):
    m = notice.meta
    law, work = _law(m.get('적용계약법')), _work(m.get('업무구분'))
    P, B = _money(m.get('입찰추정가격')), _money(m.get('배정예산금액'))
    src = 'meta'
    if P is None:
        P = stated_estimate(notice.notice_lines())
        src = 'notice'
        if P is None and B is not None:
            P, src = B / 1.1, 'budget'
        if P is None:
            src = 'none'
    types = orderer_types(notice.notice_lines(), m)
    lo, hi, basis = threshold(law, work, types, title, m.get('면허업종제한목록'))
    sido, basic = regions.meta_regions(m.get('제한지역코드목록'))
    return Meta(law=law, work=work, method=(m.get('계약방법') or None), award=(m.get('낙찰방법') or None),
                P=P, B=B, P_source=src, orderer_types=types, T_lo=lo, T_hi=hi, T_basis=basis,
                codes=parse_codes(m.get('세부품명번호목록')), region_flag=m.get('지역제한여부'),
                region_sido=sido, region_basic=basic, license=m.get('면허업종제한목록'),
                license_flag=m.get('업종제한여부'), clause=m.get('조항호내용'),
                posted=_date(m.get('공고게시일자')), opening=_date(m.get('개찰예정일자')),
                urgent=(m.get('긴급공고여부') == 'Y'), jv=m.get('공동도급구성방식'))
