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
_HOLD = re.compile(r'(?:소지|보유)(?:한|하여|해야|하여야|하고|(?:업체(?:\(자\))?|자)'
                   r'(?=$|[.,]|이어야|여야|로서|에한(?:함|한다)))')
_OR = re.compile(r'또는|혹은|내지|(?<![a-z])or(?![a-z])')
_CHOICE = re.compile(r'(?:중|중에서)(?:어느)?(?:하나|한가지|1개)|택[일1]|선택')
_PREFIX_DONE = re.compile(r'(?:등록(?:한(?:자|업체)(?:로서|이며)|하고)|'
    r'(?:소지|보유)(?:하고(?:있으며)?|한(?:자|업체)(?:로서|이며)))')
_REGISTRATION_DONE = re.compile(r'(?:등록|신고)[을를]?필한(?:자|업체)(?:로서|이며)')
_OTHER_DUTY = re.compile(r'(?:제출서류|증빙자료|서류제출방법|제출방법|제출서류의제출방법)(?:는|은)|'
    r'(?:업종코드|사업자등록증)(?:는|은)')
_OTHER_DOCUMENT = re.compile(r'[가-힣]*(?:확인서|증명서|등록증|확약서|허가증|면허증)')
_OPTION_GOVERNOR = re.compile(r'^(?:[○●□■·ㆍ※-]|\d+[.)]|[가-하][.)])*'
    r'(?:다음|아래|각호).{0,55}(?:어느하나|중하나|1개)(?:의)?'
    r'(?:자격|요건|조건|사항)?(?:을|를|에)?(?:갖춘|충족|해당)')
_NONOPERATIVE = re.compile(r'예시|작성예|참고용|가정|인용|삭제|철회|'
    r'필요없|필요가없|불필요|면제|요구하지|경우에만')
# The opening bracket of a cited statute name ("「…법률 제9조 … 에 의한").
_CITATION_OPENER = re.compile(r'[「『｢](?=[^」』｣]{0,60}?(?:법률|법|시행령|시행규칙|고시|규정))')
_PAIRS = {'「': '」', '『': '』', '｢': '｣', '“': '”', '"': '"', '(': ')', '[': ']'}


def _unclosed_citations(n, stop):
    return [m.start() for m in _CITATION_OPENER.finditer(n, 0, stop)
            if _PAIRS[n[m.start()]] not in n[m.start() + 1:stop]]


def _item_qualifier_end(n, pos):
    """End of an item-name qualifier bracketed right after the certificate noun."""
    if pos >= len(n) or n[pos] not in _PAIRS or not re.match(r'(?:세부)?품명', n[pos + 1:pos + 5]):
        return None
    opener, closer, depth = n[pos], _PAIRS[n[pos]], 0
    for i in range(pos, len(n)):
        if n[i] == closer and (i > pos or opener != closer):
            depth -= 1
            if depth <= 0:
                return i + 1
        elif n[i] == opener:
            depth += 1
    return None


def _source(record, entry, start, end, positions):
    ev = entry['evidence']
    a, b = ev['start'] + positions[start], ev['start'] + positions[end-1] + 1
    return {**ev, 'start': a, 'end': b,
        'text': record['docs'][ev['doc_index']]['text'][a:b]}


def statutory_absence_review(record):
    """An incorporated production rule or current exception blocks absence.

    Damaged columns need not prove certificate possession to make its absence
    unresolved. Article numbers must be bound to the production-law namespace;
    neither these references nor an exception declaration certify a waiver.
    """
    statute = r'(?:중소기업제품구매촉진및판로지원에관한법률|판로지원법)'
    result = []
    for di, doc in enumerate(record['docs']):
        if doc['type'] != '공고문':
            continue
        n, positions = normalized_map(doc['text'])
        patterns = (
            ('incorporated_production_law_registration', statute +
             r'[」｣』]?제9조.{0,100}제10조.{0,180}등록(?:한|된|을필한|되어있는)(?:자|업체)'),
            ('current_competition_exception_requires_review',
             r'(?:본|이번|해당)입찰.{0,180}' + statute +
             r'[」｣』]?시행령[」｣』]?제7조(?:제)?1항(?:제)?4호.{0,100}'
             r'경쟁입찰(?:의)?예외(?:임|에해당|를적용)'),
        )
        for reason, pattern in patterns:
            for match in re.finditer(pattern, n):
                a, b = positions[match.start()], positions[match.end()-1] + 1
                line_start = doc['text'].rfind('\n', 0, a) + 1
                context = doc['text'][line_start:b]
                if re.search(r'예시|참고용|가정|해당하지않|적용하지않|예외가아',
                             normalized_map(context)[0]):
                    continue
                result.append({'reason': reason, 'waiver_certified': False,
                    'possession_certified': False, 'evidence': {
                        'doc_index': di, 'doc_id': doc.get('doc_id'),
                        'document_role': doc['type'], 'start': a, 'end': b,
                        'text': doc['text'][a:b]}})
    return result


def _list_governors(record, entry):
    """Retain a visible choice governor within this source qualification block.

    A separate role/numbered heading ends the search. An unresolved choice list
    cannot make its child certificates jointly mandatory. No guessed siblings
    or cross-document heading are used to certify a common alternative.
    """
    from .sme import heading, list_marker
    ev, head = entry['evidence'], entry.get('heading')
    if not head or head['doc_index'] != ev['doc_index']:
        return []
    text = record['docs'][ev['doc_index']]['text']
    result = []
    for line in re.finditer(r'[^\r\n]+', text[head['start']:ev['start']]):
        n = normalized_map(line[0])[0]
        if heading(n):
            result = []
        marker = list_marker(line[0].strip())
        if marker:
            result = [g for g in result if not _later_sibling(marker, list_marker(g['text'].strip()))]
        if _OPTION_GOVERNOR.search(n) and not _NONOPERATIVE.search(n):
            a, b = head['start']+line.start(), head['start']+line.end()
            result.append({**ev, 'start': a, 'end': b, 'text': text[a:b]})
    marker = list_marker(ev['text'].strip())
    return [g for g in result if not _later_sibling(marker, list_marker(g['text'].strip()))]


def _later_sibling(marker, governor):
    return bool(marker and governor and marker[0] == governor[0] and marker[1] > governor[1])


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


def clause_coverage(record, entry, *, completed_registration=False):
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
        if reference == 'unclosed_quotation_context' and predicate:
            # An item/code qualifier immediately attached to the certificate
            # noun is not a quotation of its holding predicate. Ignore only
            # that unmatched opener; any outer quote/bracket remains binding.
            qualifier = n[anchor.end():predicate.start()].rstrip('를을')
            if (re.fullmatch(r'\[(?:세부품명|품명)[:：][가-힣a-z0-9():：,·ㆍ/\-]+', qualifier)
                    and CODE.search(qualifier)):
                pos = positions[anchor.end()]
                repaired = ev['text'][:pos] + ' ' + ev['text'][pos+1:]
                reference = _reference_reason(repaired, positions[point])
            elif completed_registration:
                # Each alternative may repeat the certificate noun followed
                # by its own unclosed item qualifier. Remove only those
                # anchored openers; an outer quote/reference remains binding.
                from .qualification_predicates import certificate_qualifier_openers
                openers = certificate_qualifier_openers(n, predicate.start())
                if openers and len(openers) > 1:
                    repaired = list(ev['text'])
                    for start in openers:
                        repaired[positions[start]] = ' '
                    reference = _reference_reason(''.join(repaired), positions[point])
        cited = _unclosed_citations(n, anchor.start()) if reference == 'unclosed_quotation_context' else []
        if cited and predicate:
            # A statute name cited without its closing bracket is not a
            # quotation of the holding predicate either. Ignore only those
            # openers; any other unclosed quote or bracket remains binding.
            repaired = list(ev['text'])
            for start in cited:
                repaired[positions[start]] = ' '
            reference = _reference_reason(''.join(repaired), positions[point])
        if not reference and _NONOPERATIVE.search(n[:anchor.start()]):
            reference = 'explicit_nonoperative_clause_prefix'
    if reference:
        result['uncertainty'].append('nonoperative_certificate_reference:' + reference)
        return result
    # A completed independent registration/SME condition owns its own codes
    # and ORs. Neither lends them to the following certificate object.
    prefixes = (_PREFIX_DONE, _REGISTRATION_DONE) if completed_registration else (_PREFIX_DONE,)
    start = max((m.end() for pattern in prefixes
                 for m in pattern.finditer(n[:anchor.start()])), default=0)
    holding = _HOLD.search(n, anchor.end())
    if holding is None:
        if (completed_registration
                and entry.get('direct_requirement_basis') == 'source_bidder_holding_or_prebid_exclusion'
                and not _OR.search(n) and not _CHOICE.search(n) and not result['choice_governors']):
            result['certificate_required_in_every_branch'] = True
            return result  # The pre-bid duty does not prove code-to-object binding.
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
    choice = _CHOICE.search(scope)
    qualifier_end = _item_qualifier_end(scope, local_anchor.end()) if local_anchor else None
    if choice and not options and qualifier_end and choice.end() <= qualifier_end:
        # "certificate(item names A, B, C 중 1개)": every branch is the same
        # certificate for one of the named items, so only the code is open.
        guaranteed = set()
        result['uncertainty'].append('choice_among_certificate_item_names')
    elif choice:
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


def coverage(record, entries, *, completed_registration=False):
    # The consumer can repair a completed registration predicate without
    # changing the frozen auxiliary facts used to construct model inputs.
    observations = [clause_coverage(record, e, completed_registration=completed_registration)
                    for e in entries]
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


def governed_certificate_requirements(record, entries):
    """A bare certificate is mandatory only under a visible ALL governor.

    Keep source list ownership: a new sibling, choice, submission/scoring role
    or a reference closes that governor. A nominal document alone proves none
    of these relations and is not promoted.
    """
    from . import sme
    from .law_declarations import _reference_reason
    all_items = re.compile(r'(?:다음|아래)(?:의)?각(?:목|호|항)(?:의요건)?을모두'
                           r'(?:충족하는|갖춘|갖추어야하는)자')
    result = []
    for entry in entries:
        if entry['section_role'] != 'eligibility' or entry['status'] in {
                'submission_or_form', 'scoring', 'explicit_permission'}:
            continue
        ev, head = entry['evidence'], entry.get('heading')
        if not head or head['doc_index'] != ev['doc_index']:
            continue
        # Do not borrow a holding verb or code from a joined following line.
        raw = ev['text'].splitlines()[0]
        n = normalized_map(raw)[0]
        if not re.fullmatch(r'[‣○●•·ㆍ\-]'+_CERT.pattern+r'(?:\[[^\]]+\]|\([^\n]+\))', n):
            continue
        if not CODE.search(n) or _OR.search(n) or _CHOICE.search(n) or _NONOPERATIVE.search(n):
            continue
        text = record['docs'][ev['doc_index']]['text']
        prior = text[head['start']:ev['start']]
        pn, positions = normalized_map(prior)
        governors = list(all_items.finditer(pn))
        if not governors:
            continue
        governor = governors[-1]
        lo = head['start']+positions[governor.start()]
        hi = head['start']+positions[governor.end()-1]+1
        line_start = text.rfind('\n', head['start'], lo)+1
        marker = sme.list_marker(text[line_start:lo].strip())
        intervening = text[hi:ev['start']]
        if (_reference_reason(text, lo) or _reference_reason(text, ev['start'])
                or _NONOPERATIVE.search(normalized_map(prior[:positions[governor.start()]])[0])
                or _CHOICE.search(normalized_map(intervening)[0])
                or any(sme.heading(sme.norm(line)) or _later_sibling(sme.list_marker(line.strip()), marker)
                       for line in intervening.splitlines() if line.strip())):
            continue
        result.append({'reason': 'nominal_certificate_under_all_qualification_governor',
            'guaranteed_codes': sorted(set(CODE.findall(n))),
            'production_required_in_every_branch': True,
            'evidence': {**ev, 'end': ev['start']+len(raw), 'text': raw},
            'governor_evidence': {**ev, 'start': lo, 'end': hi, 'text': text[lo:hi]},
            'actual_bidder_certificate_verified': False})
    return result


def source_supplier_requirements(record):
    """Do not certify absence when a wrapped supplier duty is explicit.

    Normalization joins syllables, preserving original offsets. This only
    withholds an absence inference; it does not invent certificate/code scope
    from interleaved PDF columns or verify actual possession.
    """
    from .law_declarations import _reference_reason
    result = []
    for di, doc in enumerate(record.get('docs', [])):
        if doc.get('type') not in {'규격서', '과업지시서'}:
            continue
        text = doc['text']
        n, positions = normalized_map(text)
        pattern = (r'(?:공급|설치|납품)(?:및설치)?업체는[^.。]{0,100}' + _CERT.pattern
                   + r'(?:를|을)(?:받은|발급받은|소지한|보유한)업체(?:이어야|여야)(?:한다|합니다|함)')
        for match in re.finditer(pattern, n):
            if _NONOPERATIVE.search(match[0]) or _OR.search(match[0]) or _CHOICE.search(match[0]):
                continue
            a, b = positions[match.start()], positions[match.end()-1]+1
            if _reference_reason(text, a):
                continue
            result.append({'reason': 'explicit_source_supplier_production_duty',
                'evidence': {'doc_index': di, 'doc_id': doc.get('doc_id'),
                    'document_role': doc['type'], 'start': a, 'end': b, 'text': text[a:b]},
                'all_target_codes_certified': False, 'actual_bidder_certificate_verified': False})
    return result


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
