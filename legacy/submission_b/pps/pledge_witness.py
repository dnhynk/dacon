"""Reject a capacity-only quotation as proof of an early pledge obligation.

This is a review of the supplied positive witness, not a negative legal finding.
Independent source decisions must run afterwards, including positive ones.
"""
import re


CAPACITY_QUALIFICATION = re.compile(
    r'제출(?:(?:이)?가능한|할수있는)(?:업체|자)'
    r'(?:이어야한다|여야한다|이어야합니다|여야합니다|에한함|에한한다)?[.。]?$')
UNRESOLVED_FRAME = re.compile(r'예시|가정|주장|해석|검토|아니|않|삭제|철회|다만|하지만|사본|원본')


def validate_model_witness(record, row, facts):
    quote = row.get('e19', '')
    if row.get('v19') not in (1, '1') or not isinstance(quote, str) or not quote.strip():
        return None
    from .pledge_document_function import review
    function = review(record, quote)
    if function is not None:
        return function
    occurrences = []
    for di, doc in enumerate(record['docs']):
        for match in re.finditer(re.escape(quote), doc['text']):
            candidates = [p for p in facts['pledges']
                if p['clause_evidence']['doc_index'] == di
                and p['clause_evidence']['start'] < match.end()
                and p['clause_evidence']['end'] > match.start()]
            if not candidates:
                return None
            for p in candidates:
                source = p['clause_evidence']
                compact = re.sub(r'\s+', '', source['quote'])
                events = p['action_modality']['events']
                if (not p['submission_capability_only'] or p['possession_required']
                        or p['uncertain_context'] or p['structural_links']
                        or not events or any(e['action'] != 'submit' or e['modality'] != 'capability'
                                             for e in events)
                        or not CAPACITY_QUALIFICATION.search(compact) or UNRESOLVED_FRAME.search(compact)):
                    return None
            occurrences.append({'doc_index': di, 'start': match.start(), 'end': match.end(),
                'quote': match.group(), 'capacity_clauses': [p['clause_evidence'] for p in candidates]})
    if not occurrences:
        return None
    return {'item': 19, 'value': 0, 'evidence': '', 'semantic_value': None,
            'reason': 'model_witness_only_establishes_submission_capacity',
            'source': 'pledge_witness_validation', 'absence_verified': False,
            'rejected_witness': quote, 'occurrences': occurrences}
