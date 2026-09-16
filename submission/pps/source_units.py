"""Finite original-text units for model selection without quote reconstruction."""
from __future__ import annotations

import dataclasses
import re


MAX_UNIT_CHARS = 220


def validate(units, record=None):
    """Validate every offered address, including those a model does not select."""
    for unit in units:
        if (not isinstance(unit.text, str)
                or type(unit.doc_index) is not int or unit.doc_index < 0
                or type(unit.start) is not int or type(unit.end) is not int
                or unit.start < 0 or unit.end - unit.start != len(unit.text)
                or not 0 < len(unit.text) <= MAX_UNIT_CHARS):
            raise ValueError('References require bounded original source units')
        if record is not None:
            if unit.doc_index >= len(record['docs']):
                raise ValueError('Source document does not exist')
            doc = record['docs'][unit.doc_index]
            if unit.doc_type != doc['type'] or doc['text'][unit.start:unit.end] != unit.text:
                raise ValueError('Source unit differs from the current document')


def unitize(spans):
    """Partition every selected character once, keeping source order and offsets.

    Prefer physical lines, then sentence/word boundaries. A boundary describes
    reading coordinates, never a recovered semantic relation or a PDF repair.
    Adjacent units can be selected together for a complete conditional clause.
    """
    units = []
    for span in spans:
        if (type(span.start) is not int or type(span.end) is not int
                or span.start < 0 or span.end - span.start != len(span.text)
                or not span.text):
            raise ValueError('Invalid source span for finite units')
        cursor = 0
        for line in span.text.splitlines(keepends=True):
            offset = 0
            while offset < len(line):
                stop = min(len(line), offset + MAX_UNIT_CHARS)
                if stop < len(line):
                    boundaries = list(re.finditer(r'[.。;；](?=\s)|\s+', line[offset:stop]))
                    preferred = [m.end() for m in boundaries if m.end() >= MAX_UNIT_CHARS // 2]
                    if preferred:
                        stop = offset + preferred[-1]
                start, end = cursor + offset, cursor + stop
                units.append(dataclasses.replace(span, start=span.start + start,
                    end=span.start + end, text=span.text[start:end]))
                offset = stop
            cursor += len(line)
        if cursor != len(span.text):
            raise AssertionError('Source units must preserve every selected character')
    merged = []
    for unit in units:
        if (merged and merged[-1].doc_index == unit.doc_index and merged[-1].end == unit.start
                and (not unit.text.strip() or not merged[-1].text.strip())
                and len(merged[-1].text) + len(unit.text) <= MAX_UNIT_CHARS):
            prior = merged[-1]
            merged[-1] = dataclasses.replace(prior, end=unit.end, text=prior.text + unit.text)
        else:
            merged.append(unit)
    return merged


def render(units):
    """Show short selection IDs; retain detailed coordinates in the packet.

    Repeating a full document/offset header for every physical line can cost
    more tokens than the source itself. Show that header once per contiguous
    source range instead, without removing any original character.
    """
    blocks, groups = [], []
    for number, unit in enumerate(units, 1):
        if not groups or unit.doc_index != groups[-1][-1][1].doc_index or unit.start != groups[-1][-1][1].end:
            groups.append([])
        groups[-1].append((number, unit))
    for group in groups:
        first, last = group[0][1], group[-1][1]
        blocks.append(f'\n[문서{first.doc_index}|{first.doc_type}|원문{first.start}:{last.end}]\n')
        blocks.extend(f'[S{n}]\n{s.text}\n' for n, s in group)
    return ''.join(blocks)
