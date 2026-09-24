"""Dates in notice text (v23 briefing interval)."""
from __future__ import annotations

import datetime as dt
import re

FULL = re.compile(r'(20\d{2})\s*[\.\-/년]\s*(\d{1,2})\s*[\.\-/월]\s*(\d{1,2})\s*일?')
SHORT = re.compile(r'(?<![\d\.])(\d{1,2})\s*[\.월/]\s*(\d{1,2})\s*[\.일]?\s*(?:\(\s*[월화수목금토일]\s*\)|[월화수목금토일]요일)')
BARE_SHORT = re.compile(r'(?<![\d\.])(\d{1,2})\.\s*(\d{1,2})\.(?!\d)')
KOREAN_SHORT = re.compile(r'(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일')


def _mk(y, m, d):
    try:
        return dt.date(int(y), int(m), int(d))
    except ValueError:
        return None


def find(text, year=None):
    """Dates with their positions: [(date, start)], full dates first; short dates borrow `year`."""
    out, taken = [], []
    for m in FULL.finditer(text or ''):
        d = _mk(*m.groups())
        if d:
            out.append((d, m.start()))
            taken.append((m.start(), m.end()))
    if year:
        for pat in (SHORT, KOREAN_SHORT, BARE_SHORT):
            for m in pat.finditer(text or ''):
                if any(a <= m.start() < b for a, b in taken):
                    continue
                d = _mk(year, m.group(1), m.group(2))
                if d:
                    out.append((d, m.start()))
                    taken.append((m.start(), m.end()))
    return sorted(out, key=lambda x: x[1])
