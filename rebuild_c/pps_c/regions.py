"""Region tokens (`[지역:rN|단위=기초|광역=경기도]`) and 시·도 names."""
from __future__ import annotations

import re

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
    for pat, canon in _PATTERNS:
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
