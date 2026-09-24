"""Read explicitly labelled requirement frames without repairing table order."""
from __future__ import annotations

import re

_ID_LABEL = re.compile(r'요구\s*사항\s*(?:ID|아이디|고유\s*번호|번호)', re.I)
_ID = re.compile(r'[A-Za-z]{2,8}\s*[-－]\s*\d{1,4}')
_NAME = re.compile(r'요구\s*사항\s*명')
_NEXT_FIELD = re.compile(r'요구\s*사항\s*(?:분류|상세\s*설명)|상세\s*설명|산출\s*정보')


def frames(record):
    result = []
    for di, doc in enumerate(record['docs']):
        text = doc['text']
        lines = list(re.finditer(r'[^\r\n]+', text))
        starts = []
        for i, line in enumerate(lines):
            raw = line[0].strip().strip('|').strip()
            label = _ID_LABEL.match(raw)
            if not label:
                continue
            tail = raw[label.end():].strip(' :：|\t')
            value, last = (tail, i) if tail else ((lines[i+1][0].strip(), i+1)
                if i+1 < len(lines) else ('', i))
            if not _ID.fullmatch(value):
                continue
            # A following name field, not a list of unrelated column headings.
            if last+1 >= len(lines):
                continue
            name_line = lines[last+1]
            name_label = _NAME.match(name_line[0].strip().strip('|').strip())
            if not name_label:
                continue
            name_raw = name_line[0].strip().strip('|').strip()
            name = name_raw[name_label.end():].strip(' :：|\t')
            end = name_line.end()
            if not name:
                if last+2 >= len(lines):
                    continue
                following = lines[last+2]
                name, end = following[0].strip(), following.end()
            if (not name or _NEXT_FIELD.match(name) or _ID_LABEL.match(name)
                    or _ID.fullmatch(name) or len(name) > 150):
                continue
            starts.append({'doc_index': di, 'start': line.start(), 'header_end': end,
                'id': re.sub(r'\s+', '', value).upper().replace('－', '-'), 'name': name,
                'heading': {'doc_index': di, 'start': line.start(), 'end': end,
                            'text': text[line.start():end]},
                'scope_certified': False})
        for n, frame in enumerate(starts):
            frame['end'] = starts[n+1]['start'] if n+1 < len(starts) else len(text)
            frame['end_is_next_requirement'] = n+1 < len(starts)
            result.append(frame)
    return result


def containing(record, evidence):
    return [f for f in frames(record) if f['doc_index'] == evidence['doc_index']
            and f['header_end'] <= evidence['start'] < evidence['end'] <= f['end']]
