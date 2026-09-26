"""Region tokens (`[지역:rN|단위=기초|광역=경기도]`) and 시·도 names."""
from __future__ import annotations

import re

from . import switches

TOKEN = re.compile(r'\[(?:등록)?지역:(r\d+)\|단위=(기초|광역)\|광역=([^\]\|]+)\]')
SIDO = {
    '서울특별시': ('서울특별시', '서울'),
    '부산광역시': ('부산광역시', '부산'),
    '대구광역시': ('대구광역시', '대구'),
    '인천광역시': ('인천광역시', '인천'),
    '광주광역시': ('광주광역시', '광주'),
    '대전광역시': ('대전광역시', '대전'),
    '울산광역시': ('울산광역시', '울산'),
    '세종특별자치시': ('세종특별자치시', '세종'),
    '경기도': ('경기도', '경기'),
    '강원특별자치도': ('강원특별자치도', '강원도', '강원'),
    '충청북도': ('충청북도', '충북'),
    '충청남도': ('충청남도', '충남'),
    '전북특별자치도': ('전북특별자치도', '전라북도', '전북'),
    '전라남도': ('전라남도', '전남'),
    '경상북도': ('경상북도', '경북'),
    '경상남도': ('경상남도', '경남'),
    '제주특별자치도': ('제주특별자치도', '제주도', '제주'),
}
_ALIASES = sorted(((alias, canon) for canon, names in SIDO.items() for alias in names), key=lambda x: -len(x[0]))
# A short alias counts only when a separator or a region word follows (부산물, 경기장, 대구경 are not regions).
_FOLLOW = r'(?=$|[\s,·ㆍ/\)\]』」"\'”’]|도\b|시\b|지역|권|내|또는|및|소재|에\s*(?:주된|본점|소재|둔)|에$)'
_PATTERNS = [(re.compile(re.escape(alias) + ('' if len(alias) >= 3 and alias.endswith(('도', '시')) else _FOLLOW)), canon)
             for alias, canon in _ALIASES]
# Audit B/F: a short alias before a location noun ("대구 경북에 사업장을 소재한") or a full stop names the 시·도 as well.
_FOLLOW_FIX = r'(?=$|[\s,·ㆍ/\)\]』」"\'”’.]|도\b|시\b|지역|권|내|또는|및|소재|에\s*(?:주된|본점|소재|둔|사업장|본사|주사무소|사무소|영업소|영업장|위치|있는|두고)|에$)'
_PATTERNS_FIX = [(re.compile(re.escape(alias) + ('' if len(alias) >= 3 and alias.endswith(('도', '시')) else _FOLLOW_FIX)), canon)
                 for alias, canon in _ALIASES]
# Probe (switches.REGION_PARTICLE): a short alias followed by a case particle ("전남의", "서울시인") names the region.
_PARTICLE = r'|(?:의|인|이|가|은|는|을|를|로|으로|와|과|에서|에|까지)(?=$|[^가-힣])'
_FOLLOW_P = _FOLLOW[:-1] + _PARTICLE + ')'
_PATTERNS_P = [(re.compile(re.escape(alias) + ('' if len(alias) >= 3 and alias.endswith(('도', '시')) else _FOLLOW_P)), canon)
               for alias, canon in _ALIASES]
_FOLLOW_FIX_P = _FOLLOW_FIX[:-1] + _PARTICLE + ')'
_PATTERNS_FIX_P = [(re.compile(re.escape(alias) + ('' if len(alias) >= 3 and alias.endswith(('도', '시')) else _FOLLOW_FIX_P)), canon)
                   for alias, canon in _ALIASES]
# Audit R2-A: region-group names by common usage — 수도권 = 서울·인천·경기, 충청권 = 대전·세종·충북·충남, 영남(권) =
# 부산·대구·울산·경북·경남, 호남(권) = 광주·전북·전남; "충청도"·"충청 지역" name the two 충청 provinces ("경기도, 강원도, 충청도"
# registered as 경기·강원·충북·충남; "대전 또는 충청 지역") — and 시 short forms ("서울시를 비롯한"; 광주시 is left out, a 경기도
# city too).
_GROUPS = [(re.compile(r'수\s*도\s*권(?!\s*(?:정\s*비|과\s*밀))'), ('서울특별시', '인천광역시', '경기도')),
           (re.compile(r'충\s*청\s*권'), ('대전광역시', '세종특별자치시', '충청북도', '충청남도')),
           (re.compile(r'충\s*청(?!\s*[남북])(?=\s*(?:지역|지방|도|[\s,·ㆍ/\)\]]|$))'), ('충청북도', '충청남도')),
           (re.compile(r'영\s*남\s*(?:권|지역|지방)'), ('부산광역시', '대구광역시', '울산광역시', '경상북도', '경상남도')),
           (re.compile(r'호\s*남\s*(?:권|지역|지방)'), ('광주광역시', '전북특별자치도', '전라남도'))]
_CITY_FORMS = [(re.compile(re.escape(alias)), canon) for alias, canon in (
    ('서울시', '서울특별시'), ('부산시', '부산광역시'), ('대구시', '대구광역시'), ('인천시', '인천광역시'), ('대전시', '대전광역시'),
    ('울산시', '울산광역시'), ('세종시', '세종특별자치시'))]


def canon_sido(name):
    name = (name or '').strip()
    for canon, names in SIDO.items():
        if name in names:
            return canon
    return None


def mentions(text):
    """Regions named in a line: {'sido': set of 시·도, 'basic': set of (rid, 시·도) for 기초-unit tokens}."""
    text = text or ''
    sido, basic = set(), set()
    for rid, unit, wide in TOKEN.findall(text):
        wide_c = canon_sido(wide) or wide
        if unit == '기초':
            basic.add((rid, wide_c))
        sido.add(wide_c)
    masked = TOKEN.sub(' ', text)
    taken = [False] * len(masked)
    if switches.REGION_PARTICLE:
        patterns = _CITY_FORMS + (_PATTERNS_FIX_P if switches.AUDIT_FIXES else _PATTERNS_P)
    else:
        patterns = _PATTERNS_FIX if switches.AUDIT_FIXES else _PATTERNS
    if switches.AUDIT_FIXES2:
        for pat, members in _GROUPS:
            for m in pat.finditer(masked):
                if any(taken[m.start():m.end()]):
                    continue
                for k in range(m.start(), m.end()):
                    taken[k] = True
                sido.update(members)
        patterns = _CITY_FORMS + patterns
    for pat, canon in patterns:
        for m in pat.finditer(masked):
            if any(taken[m.start():m.end()]):
                continue
            for k in range(m.start(), m.end()):
                taken[k] = True
            sido.add(canon)
    return {'sido': sido, 'basic': basic}


def meta_regions(value):
    """meta 제한지역코드목록 → (시·도 set, 기초 token count)."""
    if not value:
        return set(), 0
    m = mentions(str(value))
    return m['sido'], len(m['basic'])
