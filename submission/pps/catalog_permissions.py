"""Original permission candidates and bounded reading extents, not repaired text."""
from __future__ import annotations

import re

# Discover an attribute release even when it never says 동등/선택/예외.
# These patterns identify reading candidates, not a legally effective waiver.
_RELEASE = re.compile(r'(?:변경|대체|생략|면제|제외)(?:하여도|해도|해|하여|할|이|가|는|를|을)?\s*'
    r'(?:무방|가능|허용|수\s*있)|(?:변경|대체|생략|면제|제외).{0,12}허용|'
    r'(?:이외|외의|다른).{0,24}허용|제한(?:을)?\s*두지\s*않|'
    r'준수(?:할)?\s*(?:의무|필요)(?:가)?\s*없|달라도\s*(?:무방|가능)|무관(?:하다|함)')
_CONTINUES = re.compile(r'(?:[가-힣](?:을|를|은|는|의|에|으로|로)|및|또는|혹은|'
    r'경우(?:에는|에|는)?|따라|하되|하며|하고|아니라|수)\s*[,，:]?\s*$')
_BARRIER = re.compile(r'^\s*(?:\d+(?:[.)]|\s*\|)|[가-하][.)]|[①-⑳○●□■※]|[-*]\s|'
    r'요구사항\s*(?:ID|명)|(?:품명|사양|규격|납기|수량)\s*[:：]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.\s])')
_CLAUSE = re.compile(r'[.;；,，]|(?:않으며|않고|없고|하며|하고|하되|하지만|되며|되고)')
_NAME_FIELD = re.compile(r'(?:사업명|과업명|용역명|물품명|품명|구매품목명|공고명|입찰건명)\s*[:：]\s*(.+)')
_GLOBAL_SCOPE = re.compile(r'(?:(?:본|이|해당)\s*)?(?:계약|공고|규격서|과업|사업)(?:의|에서)?\s*'
    r'(?:전체|모든|일체)|(?:전체|모든)\s*(?:납품(?:할)?\s*대상|계약\s*산출물|구매\s*품목)')


def _scope_names(witness):
    """Literal names from selected source fields/rows, without alias inference."""
    names = []
    for line in re.finditer(r'[^\r\n]+', witness['text']):
        match = _NAME_FIELD.search(line[0])
        if match:
            names.append(match[1].strip())
        if witness.get('role') == 'literal_item_row_candidate':
            from .table_structure import pipe_cells
            cells = pipe_cells(line[0],0,len(line[0]))
            if len(cells) == 4:
                names.append(cells[1]['text'])
    return {re.sub(r'\s+', '', name).casefold() for name in names if name}


def other_subject_scope(full, owner, targets):
    """Different nearby headings do not exclude a target named by the clause.

This is a contradiction/coverage guard, not a truth certificate for the model's
remaining interpretation. A shared or contract-wide clause stays unresolved.
"""
    text = full['text']
    normalized = re.sub(r'\s+', '', text).casefold()
    owner_names = set().union(*(_scope_names(w) for w in owner))
    target_names = set().union(*(_scope_names(w) for w in targets))
    if _GLOBAL_SCOPE.search(text):
        issue = 'contract_wide_permission_cannot_be_other_subject'
    elif owner_names & target_names:
        issue = 'same_original_name_is_not_other_subject'
    elif any(name in normalized for name in target_names):
        issue = 'permission_mentions_current_named_target'
    elif not (any(name in normalized for name in owner_names)
              or re.search(r'(?:이|본|해당)\s*품목(?:의|은|는|에만|에\s*한)', text)):
        issue = 'different_header_does_not_prove_exclusive_permission_scope'
    else:
        issue = None
    return {'issue':issue, 'owner_names':sorted(owner_names), 'target_names':sorted(target_names),
        'source_statement':full, 'semantic_truth_certified':False}


def line_statement(record, evidence):
    """The historical v1/v2 role-inventory convention remains reproducible."""
    text = record['docs'][evidence['doc_index']]['text']
    lo = text.rfind('\n', 0, evidence['start'])+1
    hi = text.find('\n', evidence['end'])
    hi = len(text) if hi < 0 else hi
    return {'doc_index': evidence['doc_index'], 'start': lo, 'end': hi, 'text': text[lo:hi]}


def reading_extent(record, evidence, *, max_lines=6, max_characters=1200):
    """Follow visible line continuations without crossing another source block.

    A dangling line at a heading/blank/cap is unresolved. Full recovered layout
    or clause completeness is never inferred from a prettier concatenation.
    """
    text = record['docs'][evidence['doc_index']]['text']
    ev = line_statement(record, evidence)
    lo, hi = ev['start'], ev['end']
    count = len(text[lo:hi].splitlines())
    missing_prefix = False
    while lo and count < max_lines:
        end = lo-1
        start = text.rfind('\n', 0, end)+1
        prior, current = text[start:end].rstrip('\r'), text[lo:hi]
        if (not prior.strip() or not _CONTINUES.search(prior) or _BARRIER.match(current)
                or '|' in prior or '|' in current):
            break
        if hi-start > max_characters:
            missing_prefix = True
            break
        lo, count = start, count+1
    if lo and count >= max_lines:
        end = lo-1
        prior = text[text.rfind('\n', 0, end)+1:end]
        missing_prefix |= bool(_CONTINUES.search(prior) and not _BARRIER.match(text[lo:hi]))
    while _CONTINUES.search(text[lo:hi]):
        start = hi+1
        end = text.find('\n', start)
        end = len(text) if end < 0 else end
        following = text[start:end].rstrip('\r')
        if (hi >= len(text) or not following.strip() or _BARRIER.match(following)
                or '|' in following or count >= max_lines or end-lo > max_characters):
            break
        hi, count = end, count+1
    if hi > lo and text[hi-1] == '\r':
        hi -= 1
    return {'evidence': {'doc_index': ev['doc_index'], 'start': lo, 'end': hi, 'text': text[lo:hi]},
        'unclosed_continuation': bool(_CONTINUES.search(text[lo:hi])) or missing_prefix
            or hi-lo > max_characters or count > max_lines,
        'line_count': count, 'source_reordered': False, 'semantic_relation_certified': False}


def occurrences(record, fields=()):
    from .catalog_condition_facts import _SCOPE_GUARD, _LABELS
    selected = set(fields) or set(_LABELS)
    labels = [re.sub(r'\s+', '', label).casefold() for field in selected for label in _LABELS.get(field, ())]
    result = []
    for di, doc in enumerate(record['docs']):
        text = doc['text']
        for match in _SCOPE_GUARD.finditer(text):
            result.append({'kind': 'unresolved_scope_or_modality', 'doc_index': di,
                'start': match.start(), 'end': match.end(), 'text': match[0]})
        for match in _RELEASE.finditer(text):
            ev = {'doc_index': di, 'start': match.start(), 'end': match.end()}
            full = reading_extent(record, ev)['evidence']
            normalized = re.sub(r'\s+', '', full['text']).casefold()
            if not (any(label in normalized for label in labels) or re.search(r'규격|사양|성능\s*기준|상기\s*사항', full['text'])):
                continue
            if any(r['doc_index'] == di and r['start'] <= match.start() and match.end() <= r['end'] for r in result):
                continue
            result.append({'kind': 'unresolved_attribute_permission', **ev, 'text': match[0]})
    return sorted(result, key=lambda r: (r['doc_index'], r['start'], r['end'], r['kind']))


def preservation_conflict(field, text):
    from .catalog_semantics import cue
    # Bind a release to the property's clause. A memory change in a subsequent
    # clause is not proof that the CPU architecture constraint was relaxed.
    for clause in _CLAUSE.split(text):
        if not cue(field, clause):
            continue
        releases = list(_RELEASE.finditer(clause))
        if releases:
            if any(not re.match(r'\s*(?:하|되)지\s*(?:않|아니|못)', clause[m.end():]) for m in releases):
                return True
            continue
        if re.search(r'다른|이외|바꿀|삭제|철회|불필요|적용하지|선택할\s*수', clause):
            return True
    return False


def proposed_issues(record, spans, readings, existing):
    """An uncatalogued model permission cannot vanish at the lexical boundary."""
    result = []
    for reading in readings:
        covered = {(r['doc_index'], r['start'], r['end']) for r in
            (reading_extent(record, issue)['evidence'] for issue in existing)}
        for n in reading['source_units']:
            span = spans[n-1]
            if not span.text.strip():
                continue
            # Each physical source line is accounted for even if a reference
            # unit contains more than one statement. Preserve its exact range.
            for line in re.finditer(r'[^\r\n]+', span.text):
                ev = {'doc_index': span.doc_index, 'start': span.start+line.start(), 'end': span.start+line.end()}
                full = reading_extent(record, ev)['evidence']
                key = full['doc_index'], full['start'], full['end']
                if key in covered:
                    continue
                issue = {'kind': 'model_proposed_permission_scope', **full, 'field': reading['field']}
                if issue not in result:
                    result.append(issue)
    return result
