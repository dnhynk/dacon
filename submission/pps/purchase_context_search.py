"""Combine literal list/detail discovery with bounded factual notice retrieval.

All candidates retain original coordinates. Literal matching and printed counts
do not certify item identity, applicable catalog conditions, or absence.
"""
from types import SimpleNamespace

from .notice_search import NoticeSearch, factual_queries, merge_ranges
from .purchase_details import detail_links
from .supply_lists import candidates
from .table_structure import table_structures


class LinkedDetailSearch(NoticeSearch):
    def __init__(self, record, tokenizer, encoder=None):
        self.detail_inventory = [detail_links(doc['text']) for doc in record['docs']]
        super().__init__(record, tokenizer, encoder)

    def _context(self, span):
        ranges = list(super()._context(span))
        local = self.detail_inventory[span.doc_index]
        cards = {card['key']: card for card in local['cards']}
        for link in local['links']:
            row, card = link['summary_row'], cards[link['card']]
            if any(span.start < ref['end'] and ref['start'] < span.end for ref in (row, card['source'])):
                refs = [link['summary_header'], row, card['source']]
                if card['preceding_caption']:
                    refs.append(card['preceding_caption'])
                ranges.extend((span.doc_index, ref['start'], ref['end']) for ref in refs)
        return merge_ranges(ranges, self.rec['docs'])


def summary_ranges(record, inventories):
    """Include unmatched rows; a matched subset cannot become the whole table."""
    ranges = []
    for di, local in enumerate(inventories):
        for table in table_structures(record['docs'][di]['text']):
            if any(table['start'] <= link['summary_row']['start'] < table['end'] for link in local['links']):
                ranges.append((di, table['start'], table['end']))
    return merge_ranges(ranges, record['docs'])


def observations(record, search):
    values = []
    for di, doc in enumerate(record['docs']):
        for value in candidates(doc['text']):
            span = SimpleNamespace(doc_index=di, **value['source'])
            values.append({**value, 'doc_index': di, 'context': search._context(span)})
    return values


def choose_reserve(search, values, cap, *, initial=()):
    """Deterministic newly covered contexts per marginal original-source token."""
    selected = merge_ranges(initial, search.rec['docs'])
    if search.token_cost(selected) > cap:
        raise ValueError('Initial source reserve exceeds its token budget')
    pending = list(range(len(values)))
    def complete(ranges, value):
        return all(any(d == di and lo <= a and b <= hi for d, lo, hi in ranges)
                   for di, a, b in value['context'])
    while pending:
        proposals, base = [], search.token_cost(selected)
        for index in pending:
            proposed = merge_ranges([*selected, *values[index]['context']], search.rec['docs'])
            cost = search.token_cost(proposed)
            if cost <= cap:
                gained = sum(complete(proposed, v) and not complete(selected, v) for v in values)
                if gained:
                    proposals.append((gained/max(1, cost-base), -cost, -index, proposed))
        if not proposals:
            break
        best = max(proposals, key=lambda p: p[:3])
        selected = best[3]
        pending.remove(-best[2])
    return selected


class PurchaseContextSearch(LinkedDetailSearch):
    def __init__(self, record, tokenizer, encoder=None):
        super().__init__(record, tokenizer, encoder)
        self.supply_observations = observations(record, self)
        self.summaries = summary_ranges(record, self.detail_inventory)
        self.has_context = bool(self.supply_observations or self.summaries)

    def select(self, token_budget, *, method='lexical'):
        # A too-large complete summary is left to normal bounded selection;
        # neither a clipped table nor an inferred row is marked as reserved.
        summaries_fit = self.token_cost(self.summaries) <= token_budget
        required = self.summaries if summaries_fit else ()
        reserve_cap = max(token_budget//2, self.token_cost(required))
        required = choose_reserve(self, self.supply_observations, reserve_cap, initial=required)
        groups = None
        if self.supply_observations:
            names = tuple(dict.fromkeys(v['value_source']['text'] for v in self.supply_observations))
            items = tuple(range(1, 25))
            groups = {
                'other_conditions': factual_queries(tuple(range(1, 9))+tuple(range(10, 25))),
                'specification': factual_queries((9,)),
                'counted_source_names': tuple(n+' 실제 구매 대상과 구성품, 기존 장비의 관계 및 대체품 허용 조건' for n in names)}
        else:
            items = (9, 10, 11, 18)
        selected = self.search(items, token_budget=token_budget, method=method,
            required_ranges=required, queries=None if groups else factual_queries(items),
            query_groups=groups, selection_policy='evidence_cover')
        selected['diagnostics']['purchase_context'] = {
            'full_source_syntax_candidates': len(self.supply_observations),
            'literal_detail_links': sum(len(v['links']) for v in self.detail_inventory),
            'complete_summary_reserved': bool(self.summaries) and summaries_fit,
            'reserve_token_cap': reserve_cap, 'reserved_source_ranges': required,
            'candidate_identity_certified': False, 'absence_verified': False}
        return selected
