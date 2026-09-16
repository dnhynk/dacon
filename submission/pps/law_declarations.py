"""Source-bound governing-law declarations, separate from ordinary citations.

This grammar does not infer a contract's law from cited articles, an agency name,
or how often a law is mentioned. Unsupported explicit declarations remain visible
and unresolved instead of silently authorizing a metadata fallback.
"""
from __future__ import annotations

import re


def _spelling(word):
    return r'\s*'.join(map(re.escape, word))


NATIONAL = rf'(?:{_spelling("국가계약법")}|{_spelling("국가를당사자로하는계약에관한법률")})'
LOCAL = rf'(?:{_spelling("지방계약법")}|{_spelling("지방자치단체를당사자로하는계약에관한법률")})'
_BARE = rf'(?:{NATIONAL}|{LOCAL})'
_PAIRS = (('「', '」'), ('『', '』'), ('｢', '｣'), ('[', ']'))
LAW = '(?:' + '|'.join(re.escape(a)+r'\s*'+_BARE+r'\s*'+re.escape(b)
                       for a,b in _PAIRS) + '|' + _BARE + ')'
LAW_LIST = rf'{LAW}(?:\s*(?:및|과|와|,|/|·|ㆍ)\s*{LAW})*'
_CLEAN = re.compile(rf'\s*{LAW_LIST}\s*')
_START = (r'(?:^|(?<=[.;；。|]))[ \t]*(?:[|][ \t]*)?'
          r'(?:(?:[-*•※○◦●□■◇◆◎▶▷①-⑳➀-➉]|\d{1,3}[.)]|[가-하][.)])[ \t]*)?'
          r'(?:[|][ \t]*)?')
_LABEL = rf'(?:{_spelling("적용계약법")}|{_spelling("계약적용법령")})'
_SUBJECT = r'(?:본|이|금번|해당)\s*(?:입찰(?:공고)?|공고|계약)'
_VALUE = r'[^\r\n.;；。|]{0,320}'
_FIELD = re.compile(_START+rf'(?P<body>{_LABEL}\s*[:：=|][ \t]*(?:[|][ \t]*)?(?P<value>{_VALUE}))', re.M)
_DEFINED = re.compile(_START+rf'(?P<body>{_SUBJECT}\s*의\s*적용\s*(?:계약법|법령)'
                      rf'(?:은|는)[ \t]*(?P<value>{_VALUE}))', re.M)
_OPERATIVE = re.compile(_START+rf'(?P<body>{_SUBJECT}\s*(?:은|는|에(?:는)?)\s*'
    rf'(?P<law>{LAW_LIST})\s*(?:(?:을|를|이|가)\s*(?P<action>적용{_VALUE})|'
    rf'에\s*(?:따라|의하여)\s*(?P<procedure>(?:체결|집행|진행|실시){_VALUE})))', re.M)
_REFERENCE = re.compile(r'(?:참고|예시|작성예|작성예시|기재예|인용|교육자료)'
                        r'(?:문구|자료|사항|양식|공고문?|기재|작성|인용)*')
_RESUME = re.compile(r'(?:본문|실제공고(?:문)?(?:내용)?|본공고(?:문)?(?:적용사항|내용)|계약조건|공고내용)')
_HEADER_PREFIX = re.compile(r'^\s*(?:(?:[-*•※○◦●□■◇◆◎▶▷①-⑳➀-➉]|\d{1,3}[.)]|[가-하][.)])\s*)?')


def named_scopes(value):
    if not isinstance(value, str):
        return []
    return [scope for scope, pattern in (('national', NATIONAL), ('local', LOCAL))
            if re.search(pattern, value)]


def clean_scopes(value):
    return named_scopes(value) if isinstance(value, str) and _CLEAN.fullmatch(value) else []


def _caption(line):
    line = _HEADER_PREFIX.sub('', line).strip().strip('|[]【】()（）:： ').strip()
    return re.sub(r'\s+', '', line)


def _numbered_heading(line):
    match = re.fullmatch(r'\s*(?P<number>\d{1,3}(?:\.\d{1,3}){0,3})(?P<mark>[.)])\s*'
                         r'(?P<title>[^.:：;；。!?\n]{1,48})\s*', line)
    if not match or re.search(r'합니다|한다|이다|됩니다|된다|않음|적용함|적용임',match['title']):
        return None
    if named_scopes(match['title']):
        return None
    parts = tuple(map(int,match['number'].split('.')))
    return (len(parts),match['mark']), parts


def _reference_reason(text, start):
    # A multi-line example remains an example across intervening prose and blank
    # lines. Only an explicit return to notice content ends this bounded scope.
    reference, marker = False, None
    for line in text[:start].splitlines():
        caption = _caption(line)
        if _REFERENCE.fullmatch(caption):
            reference = True
            marker = _numbered_heading(line)
        elif _RESUME.fullmatch(caption):
            reference = False
        elif reference and marker:
            heading = _numbered_heading(line)
            if heading and heading[0]==marker[0] and heading[1]>marker[1]:
                reference = False
    if reference:
        return 'explicit_reference_block'
    line_start = text.rfind('\n', 0, start) + 1
    inline = _caption(text[line_start:start])
    if (_REFERENCE.fullmatch(inline) or
            re.fullmatch(_REFERENCE.pattern+r'[:：|](?:\d{1,3}[.)])?',inline)):
        return 'inline_reference_caption'
    # A quoted multi-line sample can place a syntactically valid declaration at
    # the beginning of a physical line. Preserve that quotation's ownership.
    pairs = dict(_PAIRS + (('“','”'), ('‘','’'), ('"','"'), ("'","'")))
    stack = []
    for char in text[:start]:
        if stack and char == stack[-1]:
            stack.pop()
        elif char in pairs:
            stack.append(pairs[char])
    if stack:
        return ('inside_quotation_or_unclosed_bracket' if text.find(stack[-1],start)>=0
                else 'unclosed_quotation_context')
    return None


def _value_state(value):
    value = value.strip().rstrip('.').strip()
    value = re.sub(r'(?:입니다|이다|임)\s*$', '', value).strip()
    scopes = clean_scopes(value)
    if scopes:
        return 'affirmed', scopes, None
    # The polarity belongs to this field's law, never a neighbouring field.
    negative = re.fullmatch(rf'(?P<law>{LAW_LIST})\s*(?:\((?:미적용|적용\s*제외)\)|'
        r'(?:을|를)?\s*(?:미적용|적용\s*제외|적용하지\s*(?:않음|않는다|않습니다|아니한다)))', value)
    if negative:
        return 'excluded', named_scopes(negative['law']), None
    return 'unresolved', named_scopes(value), 'unsupported_explicit_law_value'


def _related_tail(text, end):
    """Read explicitly linked continuations, stopping at an independent field."""
    notes = []
    for _ in range(4):
        gap = re.match(r'[ \t]*[.。]?[ \t]*[|]?[ \t]*(?:\r?\n[ \t]*){1,2}', text[end:])
        if not gap:
            break
        start = end + gap.end()
        stop = text.find('\n', start)
        stop = min(len(text) if stop<0 else stop, start+320)
        line = text[start:stop]
        plain = _HEADER_PREFIX.sub('', line).strip().strip('|').strip()
        conjunction = re.match(rf'(?:및|과|와|,|/|·|ㆍ)\s*{LAW}', plain)
        alternative = re.match(rf'(?:또는|혹은)\s*{LAW}', plain)
        condition = re.match(r'(?:비고|주석|적용조건|법령조건|참고사항)\s*[:：]|'
            r'(?:위|상기|해당|본|이|그)\s*(?:적용\s*)?(?:법령|계약법|적용법|법\s*의)', plain)
        if not (conjunction or alternative or condition):
            break
        reason = 'law_list_continuation' if conjunction and not re.search(r'[.。]',gap.group()) else 'related_law_condition'
        notes.append(dict(start=start,end=stop,text=line,reason=reason))
        end = stop
    return end, notes


def declarations(text):
    """Return declarations and ignored samples, all at original source offsets."""
    found = []
    for pattern, form in ((_FIELD, 'field'), (_DEFINED, 'definition'), (_OPERATIVE, 'statement')):
        for match in pattern.finditer(text):
            start, end = match.span('body')
            reason = _reference_reason(text, start)
            if form != 'statement':
                value = match['value']
                # A law name may wrap in a text dump. Continue only if the exact
                # characters can complete a bare/quoted name; do not consume an
                # unrelated next field or complete an ambiguous condition.
                if form=='field' and not clean_scopes(value.strip()):
                    wrapped = re.match(rf'(?P<value>{LAW_LIST})(?=[ \t]*(?:$|[\r\n.;；。|]))',
                                       text[match.start('value'):], re.M)
                    if wrapped is None and not value.strip():
                        wrapped = re.match(rf'\s*(?P<value>{LAW_LIST})(?=[ \t]*(?:$|[\r\n.;；。|]))',
                                           text[match.start('value'):], re.M)
                    if wrapped and wrapped.end()>len(value):
                        value = wrapped['value']
                        end = match.start('value')+wrapped.end()
                kind, scopes, unresolved = _value_state(value)
            else:
                scopes = named_scopes(match['law'])
                action = (match['action'] or match['procedure']).strip().rstrip('.').strip()
                if re.fullmatch(r'적용(?:한다|합니다|함|된다|됩니다|됨)|'
                                r'(?:체결|집행|진행|실시)(?:한다|합니다|함)', action):
                    kind, unresolved = 'affirmed', None
                elif re.fullmatch(r'적용(?:하지\s*(?:않는다|않습니다|않음|아니한다)|'
                                  r'되지\s*(?:않는다|않습니다|않음|아니한다))', action):
                    kind, unresolved = 'excluded', None
                else:
                    kind, unresolved = 'unresolved', 'unsupported_law_predicate_or_continuation'
                if re.search(r'가정|예시|인용', action):
                    reason = reason or 'explicit_nonoperative_predicate'
            end, notes = _related_tail(text, end)
            if notes:
                if form!='statement' and all(n['reason']=='law_list_continuation' for n in notes):
                    kind, scopes, unresolved = _value_state(value+'\n'+'\n'.join(n['text'] for n in notes))
                else:
                    kind, unresolved = 'unresolved', 'related_law_condition_requires_review'
            if reason=='unclosed_quotation_context':
                kind, unresolved, reason = 'unresolved', reason, None
            found.append(dict(start=start, end=end, text=text[start:end].strip(), scopes=scopes,
                              kind=kind, form=form, unresolved_reason=unresolved,
                              ignored_reason=reason, scope_notes=notes))
    return sorted(found, key=lambda signal: (signal['start'], signal['end']))
