"""Necessary, source-local support for a claimed required software action.

This rejects explicit contrary modality; it does not prove procurement scope,
the legal result, or the absence of an exception elsewhere in the notice.
"""
import re

from .assertions import clause, compact, unresolved_assertion


# Retain the connective with the preceding predicate, especially "않으며".
_CONNECTIVE = re.compile(
    r'않으며|없으며|있으며|아니하고|않고|않지만|없지만|있지만|'
    r'하며|이며|이고|하되|하지만|그러나|하고')
_NEGATION = re.compile(
    r'(?:하|되|이루어지)지(?:는|도)?(?:않|아니)|않아도|'
    r'(?:의무|필요|필수|대상|범위|과업)(?:은|는|이|가|에서|에)?(?:없|아니|아님|아닌|제외)|'
    r'(?:제외|면제|불필요|미포함|미실시|미수행|미제공|미개발|미구매)')
_CONDITIONAL = re.compile(
    r'필요(?:한경우|할경우|시|하면)|추후협의|별도협의|'
    r'(?:부분|사항)(?:이|가)?(?:있으면|있는경우)|여부[^.。;；]{0,20}(?:협의|검토|미정)')
_OPTIONAL_TAIL = re.compile(
    r'^(?:[을를은는이가도]|의)*(?:(?:할|될)수있|가능|선택(?:사항|항목)|옵션)')
_METHOD_TAIL = re.compile(r'^\s*(?:방법|절차|설명|매뉴얼)')


def predicate_review(text, start, end):
    """Inspect the original clause around this exact action occurrence.

    Capacities before another action do not negate that later action: software
    may have to be developed *to allow* changes. Event-triggered maintenance is
    also not rejected merely because the clause contains "경우" or "발생 시".
    """
    left, right = clause(text, start, end)
    for match in _CONNECTIVE.finditer(text, left, right):
        if match.end() <= start:
            left = match.end()
        elif match.start() >= end:
            right = match.end()
            break
    scope = text[left:right]
    head, tail = compact(text[left:start]), compact(text[end:right])
    issues = []
    if _NEGATION.search(tail) or re.search(r'(?:안|미)$', head):
        issues.append('source_negates_or_waives_claimed_action')
    if _CONDITIONAL.search(compact(scope)):
        issues.append('source_leaves_claimed_action_conditional')
    if _OPTIONAL_TAIL.search(tail):
        issues.append('source_describes_capacity_or_optional_action')
    if _METHOD_TAIL.search(text[end:right]):
        issues.append('source_describes_action_instructions_only')
    if unresolved_assertion(scope):
        issues.append('source_assertion_unresolved_or_withdrawn')
    return {'issues': issues, 'start': left, 'end': right, 'quote': scope}
