"""Explicit numbered parents for qualification roles; source order is unchanged.

Only visible numeric paths prove a parent. Unnumbered titles and orphan paths
cannot borrow a prior requirement. These are document roles, not legal facts.
"""
from __future__ import annotations

import re

from .notice_search import numbered_heading


_BARRIER = re.compile(r'예시|작성예|참고용|가정|삭제|철회|적용하지|적용되지|'
    r'계약조건|계약체결|낙찰후|계약이후|납품후|선정후|평가기준|평가방법|평가항목')
_OTHER_TITLE = re.compile(r'(?:계약조건|계약체결|계약이행|입찰보증금(?:및세입조치)?|'
    r'입찰의무효|예정가격및낙찰자결정방법|낙찰자결정방법|기타(?:사항)?|'
    r'장비공급및설치|교육및기술지원|무상유지보수)')


def path(text):
    """Read a dot/hyphen path or a pipe-separated section number only."""
    raw = re.sub(r'^\s*ParaShape="\d+"\s*Style="\d+">', '', text, flags=re.I).strip()
    raw = re.sub(r'^[○●□■❍•·ㆍ※]+\s*', '', raw)
    pipe = re.match(r'^(\d{1,3}(?:[.-]\d{1,3}){0,4})\s*(?:\|\s*)+[^\d\s|]', raw)
    if pipe:
        return tuple(map(int, re.split('[.-]', pipe[1])))
    # Hyphenated section paths are explicit too. Normalize only the prefix for
    # the shared numbering grammar; all evidence retains the original bytes.
    prefix = re.match(r'^\d{1,3}(?:-\d{1,3})+', raw)
    candidate = prefix[0].replace('-', '.')+raw[prefix.end():] if prefix else raw
    number = numbered_heading(candidate)
    if number and all(len(part) <= 3 for part in number['path'].split('.')):
        return tuple(map(int, number['path'].split('.')))
    return None


def explicit_other_title(normalized):
    """Known operative-stage captions may close even without decimal markers."""
    prefix = re.match(r'^[|○●□■❍•·ㆍ※-]*(?:(?:\d{1,3}(?:[.-]\d{1,3}){0,4})[.)|]*|[ivx]{1,8}[.)]?)?[|○●□■❍•·ㆍ※-]*', normalized)
    title = normalized[prefix.end():]
    if not prefix[0] and title not in {'입찰보증금','입찰의무효','계약조건','낙찰자결정방법','기타사항'}:
        return False  # Bare "기타" table cells and contract-flow nodes are not titles.
    return bool(_OTHER_TITLE.fullmatch(title))


def contexts(record, doc_index, lines, recognize, normalize, evidence):
    """Return one role per physical line plus complete qualification ranges."""
    text = record['docs'][doc_index]['text']
    stack, result, sections = [], [], []
    active = None
    for line in lines:
        normalized = normalize(line[0])
        new = recognize(normalized)
        number = path(line[0])
        # ``3. 입찰참가자격`` commonly contains ``1) ... 6) ...`` members.
        # Flattened PDF text loses indentation, but the closing parenthesis is
        # still a distinct list grammar from the dotted section heading. Keep
        # such members under the active eligibility governor unless their text
        # explicitly starts a later-stage/other section.
        if (active is not None and new == 'other'
                and re.match(r'^\s*\d{1,3}[)]', line[0])
                and not _BARRIER.search(normalized)
                and not explicit_other_title(normalized)):
            new = None
        if (new == 'other' and number is None
                and re.match(r'^\s*\d+(?:\.\d+)+', line[0])):
            new = None  # A rejected decimal/date cannot close a source role.
        # A raw decimal path without the final dot needs its original space.
        # The normalized heading recognizer cannot see that separator.
        if new is None and number and len(number) > 1 and len(normalized) < 85:
            from .notice_search import is_heading
            candidate = re.sub(r'^(\d{1,3}(?:-\d{1,3})+)', lambda m: m[0].replace('-', '.'), line[0].strip())
            if is_heading(candidate):
                new = 'other'
        if new:
            parents = [node for node in stack if number and node['number']
                and len(node['number']) < len(number)
                and number[:len(node['number'])] == node['number']]
            if active is not None and not any(node is active for node in parents):
                sections.append({'evidence':evidence(record, doc_index, active['evidence']['start'], line.start()),
                    'closed':True})
                active = None
            ev = evidence(record, doc_index, line.start(), line.end())
            role = new
            parent = parents[-1] if parents else None
            inherited = new == 'other' and parent is not None and not _BARRIER.search(normalized)
            if inherited:
                role = parent['role']
            node = {'number':number, 'role':role, 'evidence':ev,
                'governor':parent['governor'] if inherited else ev}
            stack = parents+[node]
            if new == 'eligibility' and active is None:
                active = node
        if stack:
            node = stack[-1]
            result.append({'role':node['role'], 'heading':node['governor'],
                'ancestors':[n['evidence'] for n in stack]})
        else:
            result.append({'role':'unknown', 'heading':None, 'ancestors':[]})
    if active is not None:
        sections.append({'evidence':evidence(record, doc_index, active['evidence']['start'], len(text)),
            'closed':False})
    return result, sections
