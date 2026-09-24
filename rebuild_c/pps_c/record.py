"""Notice representation: numbered lines across documents and a section map.

Every line keeps its exact source text, so evidence is always a substring of a provided document. Lines are numbered
globally in document order 공고문 → 규격서 → 과업지시서 → 제안요청서 → 예외공표서 (ties keep input order).
"""
from __future__ import annotations

import gzip
import io
import json
import re
import unicodedata
from dataclasses import dataclass, field

DOC_ORDER = ('공고문', '규격서', '과업지시서', '제안요청서', '예외공표서')

# Section labels. QUAL = participation qualification (입찰·견적 참가자격), DOCS = documents to submit, JV = joint
# contract, BRIEF = briefing session, OVERVIEW = what is procured, EVAL = award/evaluation, BID = bidding procedure,
# NOTE = cautions and other administrative text, OTHER = an unclassified top-level heading, TOP = before the first
# heading of a document.
SECTION_PATTERNS = (
    ('QUAL', re.compile(r'참\s*가\s*(자\s*의\s*)?자\s*격|자\s*격\s*요\s*건|자\s*격\s*에\s*관\s*한|참\s*여\s*자\s*격|입\s*찰\s*자\s*격|견\s*적\s*(제\s*출\s*)?자\s*격')),
    ('DOCS', re.compile(r'제\s*출\s*서\s*류|구\s*비\s*서\s*류|서\s*류\s*제\s*출|제\s*출\s*목\s*록|증\s*빙\s*서\s*류|심\s*사\s*서\s*류|첨\s*부\s*서\s*류')),
    ('JV', re.compile(r'공\s*동\s*(계\s*약|수\s*급|도\s*급|이\s*행)')),
    ('BRIEF', re.compile(r'설\s*명\s*회|현\s*장\s*설\s*명|과\s*업\s*설\s*명|요\s*청\s*서\s*설\s*명|사\s*업\s*설\s*명')),
    ('OVERVIEW', re.compile(r'부\s*치\s*는\s*사\s*항|입\s*찰\s*개\s*요|사\s*업\s*개\s*요|과\s*업\s*개\s*요|용\s*역\s*개\s*요|공\s*고\s*개\s*요|구\s*매\s*개\s*요|사\s*업\s*내\s*용|과\s*업\s*내\s*용')),
    ('EVAL', re.compile(r'낙\s*찰|평\s*가|적\s*격\s*심\s*사|협\s*상|계\s*약\s*상\s*대\s*자|심\s*사')),
    ('BID', re.compile(r'입\s*찰\s*(방\s*법|서|일\s*정|절\s*차|및|진\s*행)|개\s*찰|전\s*자\s*입\s*찰|견\s*적\s*서?\s*제\s*출|제\s*안\s*서\s*(제\s*출|접\s*수)|가\s*격\s*입\s*찰|계\s*약\s*방\s*법')),
    ('NOTE', re.compile(r'유\s*의\s*사\s*항|기\s*타\s*(사\s*항|유\s*의|참\s*고)|^\W*기\s*타\W*$|무\s*효|청\s*렴|보\s*증\s*금|하\s*도\s*급|안\s*전|문\s*의|붙\s*임|계\s*약\s*체\s*결|대\s*가\s*(의\s*)?지\s*급|참\s*고\s*사\s*항|공\s*정|서\s*약')),
)
# Top-level heading markers end the current section even when the heading names no known section.
TOP_MARK = re.compile(r'^\s*(?:\d{1,2}\s*\.(?!\d)|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+\s*[\.\)]?|[IVX]{1,4}\s*\.|[□■▣◆◇◈]|【|<\s*\S)')
# Sub-level markers switch the section only for a title-like line that names a known section.
SUB_MARK = re.compile(r'^\s*(?:[가-하]\s*[\.\)]|\(\s*\d{1,2}\s*\)|\d{1,2}\s*\)|[①-⑳]|[○●◎▶►▷ㅇ◦•\-]|\[)')
HEADING_MAX = 40
# A table row or label that opens with the qualification / document-list name and then its content.
LABEL_ROW = re.compile(r'^\W{0,3}(?:(?P<QUAL>(입\s*찰|견\s*적\s*(제\s*출)?)?\s*참\s*가\s*(자\s*의\s*)?자\s*격)|(?P<DOCS>제\s*출\s*서\s*류|구\s*비\s*서\s*류))\s*[:：|‣▶►]')
SENTENCE_END = re.compile(r'(다|함|음|됨|것|요|임|며|고|나|면)\s*[\.。]?\s*$|니다|습니다|않|없|하여야|해야')
VALUE_AFTER_COLON = re.compile(r'[:：]\s*\S')


def title_like(body, limit):
    body = body.strip()
    return 0 < len(body) <= limit and not SENTENCE_END.search(body) and not VALUE_AFTER_COLON.search(body)


@dataclass
class Line:
    i: int
    doc: int
    doc_type: str
    text: str
    sec: str = 'TOP'
    head: int = -1


@dataclass
class Notice:
    id: str
    meta: dict
    docs: list
    lines: list = field(default_factory=list)
    dropped: dict = field(default_factory=dict)
    _has_qual: bool | None = None

    def window(self, i, before=2, after=2):
        lo, hi = max(0, i - before), min(len(self.lines), i + after + 1)
        return [ln for ln in self.lines[lo:hi] if ln.doc == self.lines[i].doc]

    def by_section(self, sec):
        return [ln for ln in self.lines if ln.sec == sec]

    def notice_lines(self):
        return [ln for ln in self.lines if ln.doc_type == '공고문']

    @property
    def has_qual(self):
        if self._has_qual is None:
            self._has_qual = any(ln.sec == 'QUAL' and ln.doc_type == '공고문' for ln in self.lines)
        return self._has_qual

    def full_text(self):
        return '\n'.join(d['text'] for d in self.docs)


def nfc(text):
    return unicodedata.normalize('NFC', text or '')


NUM_MARK = re.compile(r'^\s*(\d{1,2})\s*\.(?!\d)')
ROMAN_MARK = re.compile(r'^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+\s*[\.\)]?|[IVX]{1,4}\s*\.)')
BOX_MARK = re.compile(r'^\s*(?:[□■▣◆◇◈]|【|<\s*\S)')
# List items that end with a document count ("위임장 1부", "사본 1부.") are never headings.
COUNT_END = re.compile(r'\d+\s*(부|매|통|식)\s*[\.。)）]?\s*$|\d+\s*부\s*[\(（【]')


def _named(body, patterns=SECTION_PATTERNS):
    head = re.split(r'[:：|‣]', body, maxsplit=1)[0]
    for label, pat in patterns:
        if pat.search(head):
            return label
    return None


@dataclass
class Numbering:
    """Top-level numbering of one document: "N." is a top-level heading only in sequence (N = previous + 1), or at
    the start; a document headed by Roman numerals keeps "N." as second-level items."""
    last: int | None = None
    roman: bool = False


def heading_label(text, num=None):
    """Section label for a heading line, or None when the line is not a heading.

    Top-level headings (in-sequence "N.", Roman numerals) always start a section (OTHER when unnamed). Box markers
    (□, 【, <), sub-level items (가., 1), -, ①) and unmarked lines start one only when they read as a title naming a
    known section, so list items such as "- 업종: 기타자유업", "2. 위임장 1부" or "다. 본 용역은 공동수급을 허용하지
    않습니다." stay in their section.
    """
    num = num if num is not None else Numbering()
    s = text.strip()
    if not s or COUNT_END.search(s):
        return None
    m = NUM_MARK.match(s)
    if m and not num.roman:
        n, body = int(m.group(1)), s[m.end():]
        named = _named(body[:24]) if len(s) <= HEADING_MAX else _named(body[:20], SECTION_PATTERNS[:2])
        in_seq = (num.last is None and n <= 3) or (num.last is not None and n == num.last + 1) or (n == 1 and named)
        if in_seq:
            num.last = n
            return named or 'OTHER'
        return named if named in ('QUAL', 'DOCS') and len(s) <= HEADING_MAX and title_like(body.split(':')[0], 18) else None
    if ROMAN_MARK.match(s):
        num.roman, num.last = True, None
        body = s[ROMAN_MARK.match(s).end():]
        return (_named(body[:24]) or 'OTHER') if len(s) <= HEADING_MAX else _named(body[:20], SECTION_PATTERNS[:2])
    sub = SUB_MARK.match(s)
    label_row = LABEL_ROW.match(s)
    if label_row and not sub:
        return 'QUAL' if label_row.group('QUAL') else 'DOCS'
    if len(s) > HEADING_MAX:
        return None
    box = BOX_MARK.match(s)
    mark = box or sub or (m if m else None)
    body = s[mark.end():] if mark else s
    if not title_like(body, 18 if mark else 14) or body.startswith('('):
        return None
    return _named(body)


def build(rec):
    order = {t: k for k, t in enumerate(DOC_ORDER)}
    idx = sorted(range(len(rec['docs'])), key=lambda k: (order.get(nfc(rec['docs'][k].get('type')), len(DOC_ORDER)), k))
    notice = Notice(id=rec['id'], meta=rec.get('meta') or {}, docs=rec['docs'], dropped=rec.get('dropped_doc_counts') or {})
    for k in idx:
        doc = rec['docs'][k]
        dtype = nfc(doc.get('type'))
        sec, head = ('TOP' if dtype == '공고문' else 'ATTACH'), -1
        num = Numbering()
        parent = None          # (section, heading index, marker kind) that a sub-level heading interrupted
        for raw in doc['text'].split('\n'):
            n = len(notice.lines)
            before = num.last, num.roman
            label = heading_label(raw, num)
            kind = marker_kind(raw)
            top = (num.last, num.roman) != before or ROMAN_MARK.match(raw.strip() or ' ') is not None
            if label is not None:
                if top or kind is None:
                    parent = None
                elif parent is None:
                    parent = (sec, head, kind)
                sec, head = label, n
            elif parent is not None and kind == parent[2]:
                # The next sibling item of the interrupting sub-heading returns to the enclosing section.
                sec, head = parent[0], parent[1]
                parent = None
            notice.lines.append(Line(i=n, doc=k, doc_type=dtype, text=raw, sec=sec, head=head))
    return notice


MARKER_KINDS = (('korean', re.compile(r'^\s*[가-하]\s*[\.\)]')), ('paren', re.compile(r'^\s*\(\s*\d{1,2}\s*\)')),
                ('num_paren', re.compile(r'^\s*\d{1,2}\s*\)')), ('circled', re.compile(r'^\s*[①-⑳]')),
                ('num_dot', re.compile(r'^\s*\d{1,2}\s*\.(?!\d)')), ('bullet', re.compile(r'^\s*[○●◎▶►▷ㅇ◦•\-❍]')))


def marker_kind(text):
    for kind, pat in MARKER_KINDS:
        if pat.match(text or ''):
            return kind
    return None


def open_text(path):
    return gzip.open(path, 'rt', encoding='utf-8') if str(path).endswith('.gz') else io.open(path, 'r', encoding='utf-8')


def iter_records(path, limit=None):
    n = 0
    with open_text(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            for d in rec.get('docs', []):
                d['text'] = nfc(d.get('text'))
                d['type'] = nfc(d.get('type'))
            yield rec
            n += 1
            if limit and n >= limit:
                return
