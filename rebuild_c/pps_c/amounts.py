"""Korean money expressions and base-relative requirements (e.g. 기초금액의 130%)."""
from __future__ import annotations

import re
from dataclasses import dataclass

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
    out = []
    for m in MONEY.finditer(text or ''):
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
    return out


def vat_note(text):
    if VAT_INCL.search(text or ''):
        return 'incl'
    if VAT_EXCL.search(text or ''):
        return 'excl'
    return None
