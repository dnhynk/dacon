"""Original specification-form boundaries for auditing proposed product links.

These are observed document blocks, not a legal scope classifier. A condition
may apply across blocks; such a link needs a bridge rather than a nearest-name
assumption. No model judgment is changed here.
"""
from bisect import bisect_right
import re

_FORM = re.compile(r'(?m)^[ \t]*규[ \t]*격[ \t]*서[ \t]*\r?$')


def form_blocks(record):
    blocks = []
    for di, doc in enumerate(record['docs']):
        text = doc['text']
        starts = []
        for match in _FORM.finditer(text):
            # Require an actual product/model table header. A reference to a
            # specification, or an ordinary repeating page title, is insufficient.
            lines = text[match.end():match.end()+320].splitlines()
            fields = {re.sub(r'\s', '', line) for line in lines}
            cells = {re.sub(r'\s', '', cell) for line in lines if '|' in line for cell in line.split('|')}
            # Two common extracted layouts are supported: a vertical form with
            # separate 품명/모델명 fields, and a purchase table headed
            # 구분|품명|단위|수량.  Both are observed boundaries only.
            if ({'품명', '모델명'} <= fields or {'품명', '수량'} <= cells):
                starts.append((match.start(), match.end()))
        for n, (lo, heading_end) in enumerate(starts):
            blocks.append({'doc_index': di, 'start': lo,
                'end': starts[n+1][0] if n+1 < len(starts) else len(text),
                'heading': {'doc_index': di, 'start': lo, 'end': heading_end,
                            'text': text[lo:heading_end]}})
    return blocks


def permission_link_issues(facts, record):
    """Flag a link to another explicit form without a cited scope bridge.

Competing named products must be observed in the permission's block. Repeated
forms naming the same product are not treated as different purchase targets.
The flag is an unresolved link, never proof that cross-block application is false.
"""
    blocks = form_blocks(record)
    if not blocks:
        return []
    documents = {}
    for i, block in enumerate(blocks):
        documents.setdefault(block['doc_index'], []).append((block['start'], i))

    def containing(locations):
        result = set()
        for loc in locations:
            entries = documents.get(loc['doc_index'], [])
            pos = bisect_right([start for start, _ in entries], loc['start']) - 1
            if pos >= 0:
                i = entries[pos][1]
                if loc['end'] <= blocks[i]['end']:
                    result.add(i)
        return result

    def names(product):
        return {re.sub(r'\W|_', '', loc['text']).casefold()
                for loc in product['source_locations'] if len(loc['text'].strip()) >= 4}

    products = facts['products']
    locations = [containing(p['source_locations']) for p in products]
    identities = [names(p) for p in products]
    issues = []
    for i, permission in enumerate(facts['permissions']):
        if not permission['product']:
            continue
        target = permission['product'] - 1
        if products[target]['specificity'] != 'named' or not locations[target]:
            continue
        observed = containing(permission['source_locations'])
        if not observed or observed & locations[target]:
            continue
        if containing(permission['target_locations']) & locations[target]:
            continue  # Explicit bridge is available for semantic review.
        competitors = [j for j, p in enumerate(products) if j != target
            and p['specificity'] == 'named' and locations[j] & observed
            and not identities[j] & identities[target]]
        if not competitors:
            continue
        issues.append({'kind': 'cross_form_permission_without_scope_bridge',
            'permission': i + 1, 'linked_product': target + 1,
            'other_products_in_permission_form': [j+1 for j in competitors],
            'product_form_headers': [blocks[j]['heading'] for j in sorted(locations[target])],
            'permission_form_headers': [blocks[j]['heading'] for j in sorted(observed)],
            'permission_sources': permission['sources'], 'target_sources': permission['target_sources'],
            'semantic_truth_certified': False,
            'note': 'The cited permission lies in another product form. Read the scope bridge before applying it to the linked product; block distance alone does not determine legal scope.'})
    return issues
