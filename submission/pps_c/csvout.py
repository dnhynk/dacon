"""submission.csv writer and self-check (organizer baseline contract: 49 columns, 0/1 integers, evidence ≤500 chars,
exact substrings, blank for absence items, no leading =,+,@)."""
from __future__ import annotations

import csv
import io
import os
import re
import unicodedata

from . import switches


ITEMS = [f'v{k}' for k in range(1, 25)]
EVID = [f'e{k}' for k in range(1, 25)]
COLUMNS = ['id'] + ITEMS + EVID
ABSENCE = ('v10', 'v11', 'v16', 'v18', 'v20')


def write(rows, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with io.open(path, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator='\n')
        w.writeheader()
        for r in rows:
            w.writerow({k: unicodedata.normalize('NFC', str(r[k])) for k in COLUMNS})


# A clause the layout wrapped onto the next line is quoted to its end: the evidence continues over at most two line breaks
# while a bracket is open, the line ends with a comma or a conjunction, or the next line opens with one; it never runs into
# a line that starts a new list item, and it stays inside one document and within 500 characters.
WRAP_MARK = re.compile(r'^(?:[-–—·•○●◦▪■□◆◇▶▷※*]+|\(?\d{1,2}\)|\d{1,2}\.(?!\d)|\(?[가-하]\)|[가-하]\.|[①-⑳])')
WRAP_START = re.compile(r'^(및|또는|등|」|』|\)|）)')


def wrap(ev, source_text, docs=None):
    k = source_text.find(ev)
    if not ev or k < 0:
        return ev
    end = k + len(ev)
    for _ in range(2):
        if end >= len(source_text) or source_text[end] != '\n':
            break
        j = source_text.find('\n', end + 1)
        nxt = source_text[end + 1:j if j >= 0 else len(source_text)]
        cur, n = source_text[k:end], nxt.strip()
        open_b = cur.count('「') > cur.count('」') or cur.count('(') > cur.count(')') or cur.count('（') > cur.count('）')
        if not n or WRAP_MARK.match(n):
            break
        if not (open_b or cur.rstrip().endswith((',', '，', '및', '또는')) or WRAP_START.match(n)):
            break
        if end + 1 + len(nxt) - k > 500:
            break
        end += 1 + len(nxt)
    s = source_text[k:end]
    if docs is not None and not any(s in d for d in docs):
        return ev
    return s


def row(rec_id, verdicts, source_text, docs=None):
    out = {'id': rec_id}
    for k, it in enumerate(ITEMS, 1):
        hit, ev = verdicts.get(it, (0, ''))
        ev = unicodedata.normalize('NFC', ev or '').strip()
        if switches.EVIDENCE_WRAP and hit and ev:
            ev = wrap(ev, source_text, docs).strip()
        if not hit or it in ABSENCE or ev[:1] in ('=', '+', '@') or len(ev) > 500 or (ev and ev not in source_text):
            ev = ''
        out[it] = 1 if hit else 0
        out[f'e{k}'] = ev
    return out


def validate(path, expected_ids):
    errs = []
    with io.open(path, 'r', encoding='utf-8', newline='') as fh:
        rd = csv.reader(fh)
        header = next(rd, None)
        rows = list(rd)
    if header != COLUMNS:
        return [f'header mismatch ({len(header or [])} columns)']
    if len(rows) != len(expected_ids):
        errs.append(f'rows {len(rows)} != input {len(expected_ids)}')
    ids = [r[0] for r in rows]
    if len(set(ids)) != len(ids) or set(ids) != set(expected_ids):
        errs.append('id set mismatch')
    absence_idx = {COLUMNS.index('e' + v[1:]) for v in ABSENCE}
    for r in rows:
        if len(r) != len(COLUMNS):
            errs.append(f'{r[0]}: {len(r)} columns')
            continue
        if any(x not in ('0', '1') for x in r[1:25]):
            errs.append(f'{r[0]}: non 0/1 verdict')
        if any(len(x) > 500 for x in r[25:]):
            errs.append(f'{r[0]}: evidence > 500')
        if any(r[j] for j in absence_idx):
            errs.append(f'{r[0]}: evidence on absence item')
        if any(x.startswith(('=', '+', '@')) for x in r[25:]):
            errs.append(f'{r[0]}: formula prefix')
    return errs
