"""Korean money expressions and base-relative requirements (e.g. 기초금액의 130%)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import switches

NUM = r'\d+(?:[,，]\d{3})*(?:\.\d+)?'
MONEY = re.compile(
    r'(?:(?P<eok>' + NUM + r')\s*억\s*)?'
    r'(?:(?P<man>' + NUM + r')\s*(?P<manu>천\s*만|백\s*만|십\s*만|만)\s*)?'
    r'(?:(?P<chun>' + NUM + r')\s*천\s*)?'
    r'(?:(?P<won>' + NUM + r')\s*)?원')
# Amounts written without 원 before a threshold word: "5억 이상", "3억5천만 이상", "5천만 이상".
BARE = re.compile(r'(?:(?P<eok>' + NUM + r')\s*억\s*(?:(?P<man>' + NUM + r')\s*(?P<manu>천\s*만|백\s*만|만))?|(?P<man2>' + NUM + r')\s*(?P<manu2>천\s*만|백\s*만))'
                  r'(?=\s*(?:이상|이하|미만|초과|\(|,|규모|상당|의\s*실적))')
RATIO = re.compile(
    r'(?P<base>기초\s*금액|추정\s*가격|추정\s*금액|예정\s*가격|사업\s*예산|배정\s*예산|예산\s*액?|계약\s*금액|사업\s*금액|사업비|용역\s*금액)'
    r'\s*(?:의|대비|의\s*금액의)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>%|퍼센트|배|분의)')
VAT_INCL = re.compile(r'(VAT|부가\s*가치\s*세|부가세)\s*(포함|include)', re.I)
VAT_EXCL = re.compile(r'(VAT|부가\s*가치\s*세|부가세)\s*(별도|제외|불포함)', re.I)
UNIT = {'천만': 1e7, '백만': 1e6, '십만': 1e5, '만': 1e4}
# Audit R2-A/R2-E: a compound amount with Korean units in any order of magnitude is one number ("2천5백만원" = 25,000,000,
# "1억2,500만원", "금오억원"); the fixed-slot MONEY pattern reads only its last group ("5백만원").
KDIGIT = {'일': 1, '이': 2, '삼': 3, '사': 4, '오': 5, '육': 6, '칠': 7, '팔': 8, '구': 9}
SMALL_UNIT = {'십': 10, '백': 100, '천': 1000}
BIG_UNIT = {'만': 1e4, '억': 1e8}
KNUM = r'(?:\d+(?:[,，]\d{3})*(?:\.\d+)?|[일이삼사오육칠팔구])'
# A Korean numeral starts an amount only after a non-syllable or 금 ("검사 백만원" is no 4백만원).
COMPOUND = re.compile(r'(?:(?=\d)|(?:(?<=금)|(?<![가-힣]))(?=[일이삼사오육칠팔구십백천만억]))'
                      r'(?P<body>(?:' + KNUM + r'?\s*[십백천만억]\s*)+' + KNUM + r'?)\s*'
                      r'(?:(?P<won>원)|(?=(?:이상|이하|미만|초과|\(|,|규모|상당|의\s*실적)))')
COMPOUND_TOKEN = re.compile(r'\d+(?:[,，]\d{3})*(?:\.\d+)?|[일이삼사오육칠팔구십백천만억]')
THRESHOLD_AFTER = re.compile(r'\s*(?:이상|이하|미만|초과)')


def _compound(body):
    total, section, num = 0.0, 0.0, None
    for tok in COMPOUND_TOKEN.findall(body):
        if tok[0].isdigit():
            num = _num(tok)
        elif tok in KDIGIT:
            num = float(KDIGIT[tok])
        elif tok in SMALL_UNIT:
            section += (1.0 if num is None else num) * SMALL_UNIT[tok]
            num = None
        else:
            section += num or 0.0
            total += (section or 1.0) * BIG_UNIT[tok]
            section, num = 0.0, None
    return total + section + (num or 0.0)


def compound_money(text):
    out = []
    text = text or ''
    for m in COMPOUND.finditer(text):
        body = m.group('body').strip()
        # Without 원, only 억·천만·백만 amounts before a threshold word, as BARE reads them ("10만 이상" is no amount).
        if not m.group('won') and not re.search(r'억|천\s*만|백\s*만', body):
            continue
        # A unit without a numeral is a table unit ("(단위: 백만원)", "천원") unless it is a threshold ("천만원 이상").
        if not re.search(r'\d|[일이삼사오육칠팔구]', body) and (len(re.findall(r'[십백천만억]', body)) < 2
                                                              or not THRESHOLD_AFTER.match(text, m.end())):
            continue
        v = _compound(body)
        if v > 0:
            out.append(Money(v, m.start(), m.end(), m.group(0).strip()))
    return out


@dataclass
class Money:
    value: float
    start: int
    end: int
    raw: str


def _num(s):
    return float(s.replace(',', '').replace('，', ''))


def money(text):
    """Every money expression ending in 원 with at least one digit group; values in won."""
    out = compound_money(text) if switches.AUDIT_FIXES2 else []
    comp = list(out)
    for m in MONEY.finditer(text or ''):
        if any(o.start <= m.start() < o.end or m.start() <= o.start < m.end() for o in comp):
            continue
        g = m.groupdict()
        if not any(g[k] for k in ('eok', 'man', 'chun', 'won')):
            continue
        v = 0.0
        if g['eok']:
            v += _num(g['eok']) * 1e8
        if g['man']:
            v += _num(g['man']) * UNIT[re.sub(r'\s', '', g['manu'])]
        if g['chun']:
            v += _num(g['chun']) * 1e3
        if g['won']:
            v += _num(g['won'])
        if v <= 0:
            continue
        out.append(Money(v, m.start(), m.end(), m.group(0).strip()))
    for m in BARE.finditer(text or ''):
        if any(o.start <= m.start() < o.end for o in out):
            continue
        g = m.groupdict()
        v = 0.0
        if g['eok']:
            v += _num(g['eok']) * 1e8
            if g['man']:
                v += _num(g['man']) * UNIT[re.sub(r'\s', '', g['manu'])]
        elif g['man2']:
            v += _num(g['man2']) * UNIT[re.sub(r'\s', '', g['manu2'])]
        if v > 0:
            out.append(Money(v, m.start(), m.end(), m.group(0).strip()))
    return sorted(out, key=lambda o: o.start)


# C2_BUDGET_WORDS: the base named with no multiple and a comparison ("사업예산 이상"), and "…의 100분의 N".
RATIO_BARE = re.compile(r'(?P<base>기초\s*금액|추정\s*가격|추정\s*금액|예정\s*가격|사업\s*예산|배정\s*예산|예산|계약\s*금액|사업\s*금액|사업비|용역\s*금액)'
                        r'\s*(?:액|금\s*액|액\s*수)?\s*(?:이\s*상|을\s*초\s*과|를\s*초\s*과|초\s*과|을\s*상\s*회|과\s*같\s*거\s*나|과\s*동\s*일)')
RATIO_FRACTION = re.compile(r'(?P<base>기초\s*금액|추정\s*가격|추정\s*금액|예정\s*가격|사업\s*예산|배정\s*예산|예산\s*액?|계약\s*금액|사업\s*금액|사업비|용역\s*금액)'
                            r'\s*(?:의|대비)?\s*100\s*분\s*의\s*(?P<num>\d+(?:\.\d+)?)')


# C2's new implicit/fraction floors retain their own comparator polarity.
def c2_ratio_is_floor(text, end):
    tail=re.sub(r'\s+','',text[end:end+80])
    if re.match(r'^(?:이하|이내|미만)',tail):return False
    if re.match(r'^(?:이상|초과|상회)?(?:인|일|인것|일것)?(?:은|는)?(?:아니|아님|필요(?:가|는|은)?없|필요하지않)',tail):return False
    if re.match(r'^의?(?:(?:단일|동종|유사|용역|납품|수행|사업))*실적(?:을|은|이|의)?(?:요구하지않|필요(?:가|는|은)?없|요구하는것은아니)',tail):return False
    return True


def ratios(text):
    """Base-relative requirements: [(base, multiple)], e.g. ('기초금액', 1.3) for 기초금액의 130%."""
    out = []
    for m in RATIO.finditer(text or ''):
        n = float(m.group('num'))
        unit = m.group('unit')
        if unit in ('%', '퍼센트'):
            mult = n / 100.0
        elif unit == '배':
            mult = n
        else:
            continue
        out.append((re.sub(r'\s', '', m.group('base')), mult))
    if switches.C2_BUDGET_WORDS:
        for m in RATIO_FRACTION.finditer(text or ''):
            if not c2_ratio_is_floor(text,m.end()):continue
            out.append((re.sub(r'\s', '', m.group('base')), float(m.group('num')) / 100.0))
        if not out:
            for m in RATIO_BARE.finditer(text or ''):
                if not c2_ratio_is_floor(text,m.end()):continue
                out.append((re.sub(r'\s', '', m.group('base')), 1.0))
    return out


def vat_note(text):
    if VAT_INCL.search(text or ''):
        return 'incl'
    if VAT_EXCL.search(text or ''):
        return 'excl'
    return None
