"""Select visible statutory units without reconstructing missing structure.

Every excerpt retains its heading and parent introduction. A selected paragraph
or numbered definition includes all its children and provisos. Gaps remain
explicit; original offsets address the unmodified supplied text.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


_CIRCLES = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚㉛㉜㉝㉞㉟㊱㊲㊳㊴㊵㊶㊷㊸㊹㊺㊻㊼㊽㊾㊿'
_ARTICLE = re.compile(r'^[ \t]*제\d+조(?:의\d+)?\([^\r\n]*?\)', re.M)
_STOP = re.compile(r'^[ \t]*(?:부칙(?:[ \t\r\n<〈(]|$)|\[별(?:표|지))', re.M)
_PARAGRAPH = re.compile(r'^[ \t]*([' + _CIRCLES + r'])', re.M)
_NUMBER = re.compile(r'^([ \t]*)(\d+(?:의\d+)?)\.[ \t]+', re.M)


@dataclass(frozen=True)
class Unit:
    alias: str
    article: str
    paragraphs: tuple[int, ...] = ()
    number: str | None = None

    @property
    def reference(self):
        suffix = (' ' + '·'.join(f'제{n}항' for n in self.paragraphs)
                  if self.paragraphs else '')
        if self.number is not None:
            suffix += f' 제{self.number}호'
        return self.article + suffix


def _merge(spans):
    out = []
    for lo, hi in sorted(spans):
        if lo >= hi:
            continue
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(hi, out[-1][1]))
        else:
            out.append((lo, hi))
    return tuple(out)


def select_unit(text, unit):
    """Return source ranges, or a diagnostic; never substitute a nearby unit."""
    if not isinstance(unit, Unit) or not re.fullmatch(r'제\d+조(?:의\d+)?', unit.article):
        raise ValueError('Expected a numbered statutory Unit')
    if (any(type(n) is not int or not 1 <= n <= len(_CIRCLES) for n in unit.paragraphs)
            or tuple(sorted(set(unit.paragraphs))) != unit.paragraphs):
        raise ValueError('Paragraph numbers must be unique, increasing positive integers')
    if unit.number is not None and (not isinstance(unit.number, str)
            or not re.fullmatch(r'\d+(?:의\d+)?', unit.number)
            or len(unit.paragraphs) > 1):
        raise ValueError('A numbered child must have one unambiguous parent')
    fail = lambda why: {'unit': unit.reference, 'alias': unit.alias,
                        'status': why, 'spans': []}
    if not isinstance(text, str) or not text:
        return fail('source_missing')
    stop = _STOP.search(text)
    limit = stop.start() if stop else len(text)
    articles = list(_ARTICLE.finditer(text, 0, limit))
    matches = [i for i, m in enumerate(articles)
               if re.match(r'[ \t]*' + re.escape(unit.article) + r'\(', m.group())]
    if len(matches) != 1:
        return fail('article_missing' if not matches else 'ambiguous_article')
    i = matches[0]
    heading = articles[i]
    end = articles[i+1].start() if i+1 < len(articles) else limit
    # Chapter headings are not part of the preceding article.
    chapter = re.search(r'^[ \t]*제\d+장[ \t]', text[heading.end():end], re.M)
    if chapter:
        end = heading.end()+chapter.start()
    if not unit.paragraphs and unit.number is None:
        spans = ((heading.start(), end),)
    else:
        body_start = heading.end()
        markers = list(_PARAGRAPH.finditer(text, body_start, end))
        # The first paragraph may share the heading's line in a supplied dump.
        inline = re.match(r'[ \t]*([' + _CIRCLES + '])', text[body_start:end])
        if inline and not any(m.start() == body_start for m in markers):
            # Match against the original string to keep absolute offsets.
            m = re.compile(r'[ \t]*([' + _CIRCLES + '])').match(text, body_start, end)
            markers.insert(0, m)
        numbers = [_CIRCLES.index(m[1])+1 for m in markers]
        if markers and numbers != list(range(1, len(markers)+1)):
            return fail('paragraph_sequence_ambiguous')
        if unit.paragraphs:
            if any(n not in numbers for n in unit.paragraphs):
                return fail('paragraph_missing')
            spans = [(heading.start(), markers[0].start())]
            parents = [(markers[n-1].start(), markers[n].start() if n < len(markers) else end)
                       for n in unit.paragraphs]
        else:
            if markers:
                return fail('number_requires_explicit_paragraph')
            spans, parents = [(heading.start(), heading.end())], [(body_start, end)]
        if unit.number is None:
            spans.extend(parents)
        else:
            lo, hi = parents[0]
            children = list(_NUMBER.finditer(text, lo, hi))
            if not children:
                return fail('number_missing')
            indentation = min(len(m[1].expandtabs(8)) for m in children)
            children = [m for m in children if len(m[1].expandtabs(8)) == indentation]
            for line in text[children[0].start():hi].splitlines():
                if not line.strip():
                    continue
                indent = len(line)-len(line.lstrip(' \t'))
                width = len(line[:indent].expandtabs(8))
                if width < indentation and not re.fullmatch(r'\s*\[(?:본조|전문)[^\]]*\]\s*', line):
                    # A dedented postscript might qualify the whole list. Do
                    # not assign it to the last sibling or silently discard it.
                    return fail('number_parent_tail_ambiguous')
            child_ids = [m[2] for m in children]
            # Repeated, reordered or skipped top-level numbers cannot certify
            # ownership. Inserted n의m definitions are allowed in numeric order.
            order = [tuple(map(int, s.split('의'))) if '의' in s else (int(s), 0)
                     for s in child_ids]
            bases = sorted(set(a for a, _ in order))
            if (order != sorted(set(order)) or bases != list(range(1, max(bases)+1))):
                return fail('number_sequence_ambiguous')
            if unit.number not in child_ids:
                return fail('number_missing')
            n = child_ids.index(unit.number)
            spans.extend([(lo, children[0].start()),
                          (children[n].start(), children[n+1].start() if n+1 < len(children) else hi)])
        spans = _merge(spans)
    return {'unit': unit.reference, 'alias': unit.alias, 'status': 'observed_unit',
            'spans': [list(s) for s in spans], 'all_children_retained': True,
            'source_structure_repaired': False}


def render_unit(text, selection):
    if selection['status'] != 'observed_unit':
        return ''
    chunks = []
    for lo, hi in selection['spans']:
        # Whitespace is presentation only; all lexical content, including XML
        # debris and amendment notes, stays addressable in the original ranges.
        chunks.append('\n'.join(re.sub(r'[ \t]+', ' ', line).strip()
                                for line in text[lo:hi].splitlines() if line.strip()))
    return (f"[{selection['alias']} / {selection['unit']}; 하위 호·목 포함]\n"
            + '\n[중간 단위 생략]\n'.join(chunks))
