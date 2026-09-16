"""Provided documents and unavailable named references are separate observations.

This scans original source only. It neither fetches a document nor invents its
contents. An explicit deferral of bidder qualifications or submission documents
blocks an absence proof in those domains until the referenced role is supplied.
"""
from __future__ import annotations

import re
from .assertions import clause


NAMES = {'제안요청서': r'제\s*안\s*요\s*청\s*서',
         '과업지시서': r'과업\s*(?:지시서|내용서|설명서)',
         '규격서': r'규격서|시방서', '예외공표서': r'예외\s*공표서'}
REFERENCE = re.compile(r'첨부|붙임|별첨|별도|참조|참고|따른|따름|따라|따르|확인|숙지|의함|의한|열람|정한')
_BID_DOCUMENTS = r'입찰\s*참가\s*(?:등록\s*)?서류'
QUALIFICATION = re.compile(r'참가\s*자격(?!\s*(?:제한(?:을|처분|제재)|등록\s*(?:규정|마감)))|참가\s*요건|참가\s*조건|제출\s*서류|구비\s*서류|자격\s*요건|' + _BID_DOCUMENTS)
_QUALIFICATION_HEADING = re.compile(r'\s*[○□■·ㆍ-]?\s*(?:[가-하\d]+[.)]\s*)?'
    r'(?:제출\s*서류|구비\s*서류|입찰\s*참가\s*자격|' + _BID_DOCUMENTS + r')\s*(?:은|는)?\s*[:：|]?\s*')
_DOCUMENT_NAME = re.compile('|'.join('(?:'+p+')' for p in NAMES.values()))
_QUOTES = re.compile(r'[「」『』｢｣\[\]<>〈〉“”‘’\"\']')
_FORM_DOCUMENT_REQUIREMENT = re.compile(
    r'\s*(?:의\s*)?(?:붙임\s*)?(?:서식\s*)?'
    r'(?:에\s*따른|에\s*의한|에서\s*정한|상의)\s*서류\s*'
    r'(?:(?:일체|전부)\s*)?(?:(?:를|는|은)\s*)?'
    r'(?:(?:각\s*)?\d{1,3}\s*부(?:\s*(?:를\s*)?제출(?:하여야\s*한다|해야\s*한다|한다|할\s*것|함))?'
    r'|(?:모두\s*)?제출(?:하여야\s*한다|해야\s*한다|한다|할\s*것|함))'
    r'(?=[ \t]*(?:[.。][ \t]*)?(?:\r?\n|$))')


def _form_submission_end(text, start, end):
    """An explicit referenced-document list is not a technical reference.

    A quantity-ended submission-list entry can state the duty without repeating
    제출서류. Keep the original occurrence, including a visibly wrapped line;
    do not reconstruct a missing form or borrow another document's predicate.
    """
    match = _FORM_DOCUMENT_REQUIREMENT.match(text, end, min(len(text), end + 240))
    if not match or re.search(r'\n\s*\n', match[0]) or match[0].count('\n') > 2:
        return None
    lo, hi = clause(text, start, end)
    if match.end() > hi:
        return None
    line_start = max(lo, text.rfind('\n', lo, start) + 1)
    prefix = re.sub(r'\s', '', text[line_start:start])
    if re.search(r'예시|작성예|참고용|가정|납품후|준공후|낙찰후|낙찰자|계약상대자|계약체결후', prefix):
        return None
    previous = text[:line_start].rstrip().rsplit('\n', 1)[-1]
    previous = re.sub(r'\s', '', previous)
    if re.fullmatch(r'(?:\d{1,3}[.)])?(?:낙찰후|낙찰자|계약상대자|계약체결후|납품후|준공후)'
                    r'(?:제출|구비)?(?:서류|서류목록)[:：]?', previous):
        return None
    return match.end()


def _reference_prefix(text):
    """Only a document list may inherit a preceding qualification field.

    A new '납품규격:' field owns its reference even when it immediately follows
    a '제출서류' heading. No line reordering or inferred table cells are used.
    """
    value = _DOCUMENT_NAME.sub('D', _QUOTES.sub('', text))
    value = re.sub(r'^\s*(?:[○□■·ㆍ-]|[가-하\d]+[.)])\s*', '', value)
    return bool(re.fullmatch(r'\s*(?:(?:D|및|또는|과|와|첨부|붙임|별첨|별도|[,/·ㆍ])\s*)*', value))


def _reference_list_only(text):
    return bool(_DOCUMENT_NAME.search(text) and _reference_prefix(text))


def _context(text, start, end):
    """Keep a bounded, visibly wrapped field/list at its original coordinates."""
    lo = text.rfind('\n', 0, start) + 1
    hi = text.find('\n', end)
    hi = len(text) if hi < 0 else hi
    clause_lo, clause_hi = clause(text, start, end)
    lo, hi = max(lo, clause_lo), min(hi, clause_hi)
    if _reference_prefix(text[lo:start]):
        cursor = lo
        for _ in range(4):
            prefix = text[:cursor].rstrip()
            previous_start = prefix.rfind('\n') + 1
            previous = prefix[previous_start:]
            if not previous or start-previous_start > 500:
                break
            if _QUALIFICATION_HEADING.fullmatch(previous):
                lo = previous_start
                break
            if not _reference_list_only(previous):
                break
            lo = cursor = previous_start
    # A header followed by a split list must retain the final '참조/따름'.
    # The source is a reading range, not an assertion that list members are AND.
    cursor = start
    for _ in range(4):
        if not _reference_list_only(text[cursor:hi]):
            break
        next_line = re.search(r'\S[^\r\n]*', text[hi:])
        if not next_line:
            break
        a, b = hi+next_line.start(), hi+next_line.end()
        following = text[a:b]
        if b-start > 500:
            break
        prefix = re.match(r'\s*(?:(?:및|또는|과|와|[,/·ㆍ])\s*)?', following)
        tail = following[prefix.end():]
        if not (_DOCUMENT_NAME.match(_QUOTES.sub('', tail)) or
                re.match(r'^(?:참조|참고|확인|따름|열람)(?:\s|[.。]|$|한다|합니다|할)', tail)):
            break
        cursor, hi = a, b
    return lo, hi


def _nonreference(tail):
    # Object particles matter. '참조하지 않는다는 뜻은 아니다' and an optional
    # reference are not an unconditional declaration that a reference is unused.
    value = _QUOTES.sub('', tail)
    return bool(re.match(r'\s*(?:은|는|이|가|을|를|에는|에)?\s*'
        r'(?:(?:참조|참고|적용|사용|준용)하지|따르지)\s*'
        r'(?:않(?:는다|습니다|음|으며|고)|아니(?:한다|함|하며|하고))(?=\s|[.。,;；]|$)', value))


def _availability_denial(tail):
    value = _QUOTES.sub('', tail)
    return bool(re.match(r'\s*(?:은|는|이|가|을|를|에는|에)?\s*'
        r'(?:없(?:다|습니다|음)|미사용|(?:작성|첨부|제공)하지\s*'
        r'(?:않(?:는다|습니다|음|으며|고)|아니(?:한다|함|하며|하고)))'
        r'(?=\s|[.。,;；)]|$)', value))


def _qualification_deferral(context, reference_start, reference_end, form_required):
    """Bind the referenced property instead of inheriting a neighboring noun.

    Registration is a completed predicate on the bidder. A subsequent named
    task-capability condition owns its technical document list. Only this
    explicit separation is resolved here; an unbound qualification mention,
    document submission, or another qualification reference remains open.
    Offsets and the entire original reading range are retained by assess().
    """
    if form_required:
        return True
    qualifications = list(QUALIFICATION.finditer(context))
    if not qualifications:
        return False
    # Every qualification mention must be the object of its own completed
    # registration clause before the technical reference, with no embedded
    # document, negation, exception, or second qualification property.
    registration_end = None
    for qualification in qualifications:
        if qualification.end() > reference_start:
            return True
        prefix = context[qualification.start():reference_start]
        match = re.match(r'참가\s*자격\s*을\s*[^\r\n;。]{0,180}?'
            r'등록(?:\s*을\s*마친|한)\s*(?:자\s*로서|업체\s*(?:로서|이며|이면서))\s*[,，]?\s*', prefix)
        if (not match or _DOCUMENT_NAME.search(match[0])
                or re.search(r'예시|가정|아니|않|다만|제외|요건|조건|서류', match[0])):
            return True
        end = qualification.start() + match.end()
        if not _reference_prefix(context[end:reference_start]):
            return True
        registration_end = end
    if registration_end is None:
        return True
    # The current occurrence can be any member of a visibly coordinated
    # document list. Neither a mere '참조' nor generic ability proves which
    # property is delegated, so require the task and its actual predicate.
    tail = _QUOTES.sub('', context[reference_end:])
    document = '(?:' + _DOCUMENT_NAME.pattern + ')'
    technical = re.fullmatch(
        r'\s*(?:(?:및|또는|과|와|[,/·ㆍ])\s*' + document + r'\s*)*'
        r'(?:의\s*)?(?:내용\s*)?(?:에\s*따라|에\s*따른|에서\s*정한)\s*'
        r'(?:과업|사업|업무)\s*(?:을\s*)?(?:수행|시행)(?:\s*[·ㆍ/및]+\s*납품)?\s*'
        r'(?:이\s*가능한|할\s*수\s*있는)\s*(?:업체|자)\s*'
        r'(?:(?:이어야|여야)\s*(?:한다|함|합니다))?\s*[.。]?\s*', tail)
    return not bool(technical)


def assess(record):
    docs = record.get('docs') or []
    actual = [{'doc_index': i, 'doc_id': d.get('doc_id'), 'document_role': d.get('type'),
               'source_chars': len(d.get('text') or '')} for i, d in enumerate(docs)]
    roles = {d['document_role'] for d in actual if d['source_chars']}
    references = []
    for di, doc in enumerate(docs):
        text = doc.get('text') or ''
        for role, pattern in NAMES.items():
            for match in re.finditer(pattern, text):
                lo, hi = _context(text, match.start(), match.end())
                form_end = _form_submission_end(text, match.start(), match.end())
                if form_end is not None:
                    hi = max(hi, form_end)
                context = text[lo:hi]
                if (form_end is None and not REFERENCE.search(context)) or re.search(r'예시|작성\s*예|가정|참고용', context):
                    continue
                # A statement about non-attachment does not cancel an operative
                # qualification deferral. Also bind a denial to this occurrence,
                # not every mention of the same document elsewhere in the line.
                tail = text[match.end():hi]
                if _nonreference(tail):
                    continue
                qualification = _qualification_deferral(context, match.start()-lo,
                    match.end()-lo, form_end is not None)
                if _availability_denial(tail) and not qualification:
                    continue
                references.append({'referenced_role': role, 'available_as_document_role': role in roles,
                    'qualification_deferral': qualification,
                    'evidence': {'doc_index': di, 'doc_id': doc.get('doc_id'),
                                 'document_role': doc.get('type'), 'start': lo, 'end': hi,
                                 'text': context}})
    missing = [r for r in references if not r['available_as_document_role']]
    from .input_contract import provided_complete
    complete = provided_complete(record)
    return {'actual_provided_docs': actual, 'declared_input_complete': complete,
            'references': references, 'referenced_unavailable_docs': missing,
            'qualification_deferrals_resolved': not any(r['qualification_deferral'] for r in missing),
            'all_referenced_document_roles_present': not missing,
            'reference_contents_inferred': False,
            'scope': 'Provided source and explicit qualification deferrals only; generic technical references are reported separately.'}


def eligibility_absence_coverage(record, sections, coverage):
    """Resolve only form pointers superseded by a later exhaustive notice clause.

    ``assess`` deliberately keeps every unavailable submission-document pointer.
    That raw coverage remains useful for document completeness, but a pointer to
    *registration/submission forms* before a later notice clause saying that the
    bidder must satisfy all of the following qualifications does not delegate an
    additional enterprise-size or direct-production predicate.  Treating it as
    such made an explicit, closed eligibility list unusable for every absence
    check.

    This is intentionally ordered and narrow.  A reference that names bidder
    qualifications, follows the exhaustive clause, lacks an ``all following``
    governor, or belongs to another document remains unresolved.  The missing
    document and its exact source coordinates are never removed from ``coverage``.
    """
    missing = [r for r in coverage.get('referenced_unavailable_docs', [])
               if r.get('qualification_deferral')]
    exhaustive = []
    governor = re.compile(
        r'(?:다음|아래)(?:의)?(?:각호|각항|사항|요건|자격)?.{0,45}'
        r'(?:자격|요건|사항).{0,25}(?:모두|전부)(?:갖춘|갖추|충족)|'
        r'(?:아래|다음).{0,45}(?:모두|전부)(?:갖춘|갖추|충족)|'
        r'(?:아래|다음)(?:의)?(?:각호|각항|사항|요건|자격)?.{0,25}'
        r'(?:자격|요건|사항)(?:을|를)?(?:갖춘|충족한)(?:자|업체)(?:이어야|여야)')
    for section in sections:
        evidence = section.get('evidence', {})
        if (section.get('closed') and evidence.get('document_role') == '공고문'
                and governor.search(re.sub(r'\s+', '', evidence.get('text', '')))):
            exhaustive.append(evidence)

    form_field = re.compile(
        r'(?:입찰\s*참가\s*(?:등록\s*)?서류|(?:제안서\s*)?제출\s*서류|구비\s*서류)\s*[:：|]|'
        r'(?:제안서\s*)?제출\s*방법\s*[,，]?\s*구비\s*서류\s*'
        r'(?:서식|양식)(?:\s*및\s*작성\s*요령)?|'
        r'입찰\s*참가\s*서류\s*(?:목록|양식|서식)')
    bid_registration_field = re.compile(
        r'(?:^|[\r\n])\s*입찰\s*참가\s*(?:등록\s*)?서류\s*[:：|]')
    delegated_predicate = re.compile(
        r'(?:입찰\s*)?참가\s*(?:자격|요건|조건)\s*(?:은|는|이|가|을|를|의|[:：|])|'
        r'(?:자격|요건|조건)\s*(?:을|를)?\s*(?:갖추|충족|따르|정한)')
    resolved_forms, unresolved = [], []
    for reference in missing:
        evidence = reference.get('evidence', {})
        text = evidence.get('text', '')
        later = [section for section in exhaustive
                 if section.get('doc_index') == evidence.get('doc_index')
                 and evidence.get('end', 10**30) <= section.get('start', -1)]
        earlier = [section for section in exhaustive
                   if section.get('doc_index') == evidence.get('doc_index')
                   and section.get('end', 10**30) <= evidence.get('start', -1)]
        generic_form_after = (earlier and form_field.search(text)
                              and not bid_registration_field.search(text))
        if (form_field.search(text) and not delegated_predicate.search(text)
                and (later or generic_form_after)):
            resolved_forms.append(reference)
        else:
            unresolved.append(reference)
    # Global completeness may be false solely because a named technical/form
    # attachment was dropped.  A closed exhaustive notice qualification still
    # covers this domain when every dropped role is explicitly referenced and
    # none of those references delegates a bidder predicate.
    from .input_contract import provided_complete
    declared_complete = provided_complete(record)
    state = record.get('input_completeness') or {}
    dropped = {role for role, count in (record.get('dropped_doc_counts') or {}).items()
               if type(count) is int and count > 0}
    unavailable = coverage.get('referenced_unavailable_docs', [])
    represented = {item.get('referenced_role') for item in unavailable}
    unresolved_ids = {(item['evidence'].get('doc_index'), item['evidence'].get('start'),
                       item['evidence'].get('end')) for item in unresolved}
    dropped_roles_safe = bool(dropped) and dropped <= represented
    if dropped_roles_safe:
        for item in unavailable:
            if item.get('referenced_role') not in dropped:
                continue
            ev = item['evidence']
            if (ev.get('doc_index'), ev.get('start'), ev.get('end')) in unresolved_ids:
                dropped_roles_safe = False
                break
    domain_complete = (declared_complete or (
        state.get('공고문_실재') is True and state.get('추출_성공') is True
        and dropped_roles_safe and bool(exhaustive)))
    return {
        'size_and_direct_predicates_resolved': not unresolved,
        'eligibility_source_complete': domain_complete,
        'global_source_complete': declared_complete,
        'dropped_roles_scoped_outside_qualification': sorted(dropped) if domain_complete and not declared_complete else [],
        'form_references_scoped_by_later_exhaustive_notice_eligibility': resolved_forms,
        'unresolved_predicate_references': unresolved,
        'raw_document_coverage_unchanged': True,
        'reference_contents_inferred': False,
    }
