"""Source-bound lower bounds on production-certificate requirements.

Codes in alternative branches are intersected, never flattened into an AND.
Unknown attachment/list structure can remove a proof, but cannot prove absence,
possession by an actual bidder, or a statutory waiver.
"""
from __future__ import annotations

import re

from .products import CODE, normalized_map

_CERT = re.compile(r'직접생산(?:확인)?(?:증명|확인)?서')
_ANCHOR = re.compile(_CERT.pattern + r'|직접생산확인기준')
_HOLD = re.compile(r'(?:소지|보유)(?:한|하여|해야|하여야|하고)')
_OR = re.compile(r'또는|혹은|내지|(?<![a-z])or(?![a-z])')
_CHOICE = re.compile(r'(?:중|중에서)(?:어느)?(?:하나|한가지|1개)|택[일1]|선택')
_PREFIX_DONE = re.compile(r'(?:등록(?:한(?:자|업체)(?:로서|이며)|하고)|'
    r'(?:소지|보유)(?:하고(?:있으며)?|한(?:자|업체)(?:로서|이며)))')
_OTHER_DUTY = re.compile(r'(?:제출서류|증빙자료|서류제출방법|제출방법|제출서류의제출방법)(?:는|은)|'
    r'(?:업종코드|사업자등록증)(?:는|은)')
_OTHER_DOCUMENT = re.compile(r'[가-힣]*(?:확인서|증명서|등록증|확약서|허가증|면허증)')
_OPTION_GOVERNOR = re.compile(r'^(?:[○●□■·ㆍ※-]|\d+[.)]|[가-하][.)])*'
    r'(?:다음|아래|각호).{0,55}(?:어느하나|중하나|1개)(?:의)?'
    r'(?:자격|요건|조건|사항)?(?:을|를|에)?(?:갖춘|충족|해당)')
_NONOPERATIVE = re.compile(r'예시|작성예|참고용|가정|인용|삭제|철회|'
    r'필요없|필요가없|불필요|면제|요구하지|경우에만')


def _source(record, entry, start, end, positions):
    ev = entry['evidence']
    a, b = ev['start'] + positions[start], ev['start'] + positions[end-1] + 1
    return {**ev, 'start': a, 'end': b,
        'text': record['docs'][ev['doc_index']]['text'][a:b]}


def _list_governors(record, entry):
    """Retain a visible choice governor within this source qualification block.

    A separate role/numbered heading ends the search. An unresolved choice list
    cannot make its child certificates jointly mandatory. No guessed siblings
    or cross-document heading are used to certify a common alternative.
    """
    from .sme import heading
    ev, head = entry['evidence'], entry.get('heading')
    if not head or head['doc_index'] != ev['doc_index']:
        return []
    text = record['docs'][ev['doc_index']]['text']
    result = []
    for line in re.finditer(r'[^\r\n]+', text[head['start']:ev['start']]):
        n = normalized_map(line[0])[0]
        if heading(n):
            result = []
        if _OPTION_GOVERNOR.search(n) and not _NONOPERATIVE.search(n):
            a, b = head['start']+line.start(), head['start']+line.end()
            result.append({**ev, 'start': a, 'end': b, 'text': text[a:b]})
    return result


def _shared_object(text, position, anchor):
    """Only an open, balanced-so-far certificate group supplies an omitted noun."""
    pairs = {')': '(', ']': '[', '】': '【', '〉': '〈'}
    stack = []
    for char in text[anchor.end():position]:
        if char in pairs.values():
            stack.append(char)
        elif char in pairs:
            if not stack or stack.pop() != pairs[char]:
                return False
    return bool(stack)


def _branch_objects(branch, shared):
    """Read codes of certificate objects before their own holding predicate.

    A second code after "holds" or in a registration/other-document object is
    not borrowed. This is a lower bound, not a reconstruction of missing cells.
    """
    anchors = list(_ANCHOR.finditer(branch))
    masked = _ANCHOR.sub(lambda m: ' '*len(m[0]), branch)
    other = list(_OTHER_DOCUMENT.finditer(masked))
    if not anchors:
        stop = _HOLD.search(branch)
        codes = set(CODE.findall(branch[:stop.start() if stop else len(branch)]))
        return (codes, True) if shared and not other else (set(), False)
    codes = set()
    for anchor in anchors:
        before = branch[:anchor.start()]
        start = max((m.end() for regex in (_PREFIX_DONE, _HOLD)
            for m in regex.finditer(before)), default=0)
        prefix = branch[start:anchor.start()]
        if not re.search(r'등록', prefix) and not _OTHER_DOCUMENT.search(
                _ANCHOR.sub(lambda m: ' '*len(m[0]), prefix)):
            codes.update(CODE.findall(prefix))
        stop = _HOLD.search(branch, anchor.end())
        end = stop.start() if stop else len(branch)
        end = min([end, *(m.start() for m in other if anchor.end() <= m.start() < end)])
        codes.update(CODE.findall(branch[anchor.end():end]))
    return codes, True


def clause_coverage(record, entry):
    n, positions = normalized_map(entry['evidence']['text'])
    anchor = _CERT.search(n) or _ANCHOR.search(n)
    result = {'evidence': entry['evidence'], 'observed_codes': sorted(set(entry['codes'])),
        'guaranteed_codes': [], 'certificate_required_in_every_branch': False,
        'uncertainty': [], 'choice_governors': _list_governors(record, entry)}
    if anchor is None:
        result['uncertainty'].append('certificate_object_not_resolved')
        return result
    from .law_declarations import _reference_reason
    ev = entry['evidence']
    reference = _reference_reason(record['docs'][ev['doc_index']]['text'], ev['start'])
    if reference not in {'explicit_reference_block', 'inline_reference_caption'}:
        # A quoted law/certificate name is not a quoted requirement. Check the
        # actual predicate within this source entry, rather than inheriting an
        # unrelated unmatched quote from earlier pages of a flattened document.
        predicate = _HOLD.search(n, anchor.end())
        point = predicate.start() if predicate else anchor.end()-1
        reference = _reference_reason(ev['text'], positions[point])
        if not reference and _NONOPERATIVE.search(n[:anchor.start()]):
            reference = 'explicit_nonoperative_clause_prefix'
    if reference:
        result['uncertainty'].append('nonoperative_certificate_reference:' + reference)
        return result
    # A completed independent registration/SME condition owns its own codes
    # and ORs. Neither lends them to the following certificate object.
    start = max((m.end() for m in _PREFIX_DONE.finditer(n[:anchor.start()])), default=0)
    holding = _HOLD.search(n, anchor.end())
    if holding is None:
        if (entry.get('direct_requirement_basis') == 'mandatory_database_verification_with_exclusion'
                and not _OR.search(n) and not _CHOICE.search(n) and not result['choice_governors']):
            result['certificate_required_in_every_branch'] = True
            result['uncertainty'].append('verification_target_codes_not_bound_to_possession_object')
            return result
        result['uncertainty'].append('certificate_possession_predicate_not_resolved')
        return result
    end = len(n)
    other = _OTHER_DUTY.search(n, holding.end())
    if other:
        # A new subject after a completed holding predicate is a separate duty.
        # An outer OR is not such a conjunction and must remain in the review.
        bridge = n[holding.end():other.start()]
        if not _OR.search(bridge):
            end = other.start()
    scope = n[start:end]
    result['certificate_scope'] = _source(record, entry, start, end, positions)
    local_anchor = _ANCHOR.search(scope)
    options = list(_OR.finditer(scope))
    if options:
        branches, cursor = [], 0
        shared = all(_shared_object(scope, m.start(), local_anchor) for m in options)
        for option in [*options, None]:
            stop = option.start() if option else len(scope)
            branch = scope[cursor:stop]
            codes, own = _branch_objects(branch, shared)
            branches.append({'codes': sorted(codes),
                'certificate_object': own})
            cursor = option.end() if option else len(scope)
        guaranteed = set.intersection(*(set(b['codes']) for b in branches))
        required = all(b['certificate_object'] for b in branches)
        result['alternative_branches'] = branches
        result['uncertainty'].append('alternative_certificate_objects')
    else:
        guaranteed, required = _branch_objects(scope, False)
    if _CHOICE.search(scope):
        # A flat list followed by "one of" has no established logical tree.
        # Even a one-code list may contain an uncoded alternative document.
        guaranteed, required = set(), False
        result['uncertainty'].append('choice_list_membership_unresolved')
    if result['choice_governors']:
        guaranteed, required = set(), False
        result['uncertainty'].append('outer_qualification_choice_unresolved')
    result['guaranteed_codes'] = sorted(guaranteed)
    result['certificate_required_in_every_branch'] = required
    return result


def coverage(record, entries):
    observations = [clause_coverage(record, e) for e in entries]
    for entry, observation in zip(entries, observations):
        # Item 12 also concerns an actual manufacturing qualification. Do not
        # erase that independent proof just because it has no certificate noun;
        # equally, it supplies no certificate-to-product-code binding for v10.
        n = normalized_map(entry['evidence']['text'])[0]
        manufacturing = re.search(r'직접생산하는(?:업체|자)(?:이어야|여야)(?:하며|합니다|한다|함)', n)
        separate = False
        if not observation['choice_governors'] and not _NONOPERATIVE.search(n):
            if manufacturing and not _OR.search(n[:manufacturing.end()]) and not _CHOICE.search(n[:manufacturing.end()]):
                from .law_declarations import _reference_reason
                _, positions = normalized_map(entry['evidence']['text'])
                separate = not _reference_reason(entry['evidence']['text'], positions[manufacturing.start()])
            elif (entry.get('direct_requirement_basis') == 'mandatory_database_verification_with_exclusion'
                    and not _OR.search(n) and not _CHOICE.search(n)):
                separate = True
        observation['production_required_in_every_branch'] = (
            observation['certificate_required_in_every_branch'] or separate)
    return {'version': 'production_certificate_relations_v1',
        'guaranteed_codes': sorted({c for e in observations for c in e['guaranteed_codes']}),
        'observations': observations, 'actual_bidder_possession_verified': False}


def unresolved_validity(record, entry):
    """An operative pre-bid validity condition prevents certified total absence.

    This is intentionally not a holding requirement, or a check of an actual
    bidder's issue date. Forms, examples and later contract stages do not qualify.
    """
    if entry['section_role'] != 'eligibility' or entry['status'] in {
            'submission_or_form', 'scoring', 'explicit_permission'}:
        return None
    n = normalized_map(entry['evidence']['text'])[0]
    cert = _CERT.search(n)
    if not cert or _NONOPERATIVE.search(n):
        return None
    from .law_declarations import _reference_reason
    ev = entry['evidence']
    if _reference_reason(record['docs'][ev['doc_index']]['text'], ev['start']):
        return None
    tail = n[cert.end():]
    other = _OTHER_DOCUMENT.search(tail)
    if other:
        tail = tail[:other.start()]  # Another certificate owns its own dates.
    deadline = re.search(r'(?:입찰|제출).{0,16}마감.{0,12}전일?까지.{0,20}발급', tail)
    valid = re.search(r'유효기간(?:내|이내)(?:에)?(?:있어야|이어야)|유효한것이어야', tail)
    if deadline and valid:
        return {'reason': 'operative_certificate_validity_scope_unresolved',
            'evidence': ev, 'possession_requirement_certified': False,
            'actual_bidder_certificate_verified': False}
    return None


def incorporated_registration_requirements(record, entries, declarations):
    """Join a statutory product registration clause to its certificate note.

    Some notices state the bidder predicate as registration under article 9 /
    enforcement-decree article 10 and put the direct-production certificate's
    issue-date and validity requirement on the immediately attached note.  No
    single substring says "possess", but the combined operative clause does
    require the named product certificate.  Require the declaration, exact
    codes, eligibility role, statute and validity note in one source entry.
    """
    result, seen = [], set()
    for declaration in declarations:
        if declaration.get('role') != 'purchase_registration' or not declaration.get('codes'):
            continue
        dev = declaration['evidence']
        for entry in entries:
            ev = entry['evidence']
            if (entry.get('section_role') != 'eligibility'
                    or ev['doc_index'] != dev['doc_index']
                    or ev['start'] > dev['start'] or dev['end'] > ev['end']):
                continue
            value = normalized_map(ev['text'])[0]
            if (_NONOPERATIVE.search(value)
                    or not re.search(r'중소기업제품구매촉진.{0,35}제9조', value)
                    or not re.search(r'시행령제10조', value)
                    or not _CERT.search(value)
                    or not re.search(r'(?:입찰|제출).{0,18}마감.{0,15}전일?까지.{0,25}발급', value)
                    or not re.search(r'유효기간(?:내|이내)(?:에)?있어야', value)):
                continue
            key = (ev['doc_index'], ev['start'], ev['end'], tuple(sorted(declaration['codes'])))
            if key in seen:
                continue
            seen.add(key)
            result.append({
                'reason': 'article9_10_registration_with_attached_certificate_validity_requirement',
                'guaranteed_codes': sorted(set(declaration['codes'])),
                'production_required_in_every_branch': True,
                'evidence': ev,
                'registration_evidence': dev,
                'actual_bidder_certificate_verified': False,
            })
    return result
