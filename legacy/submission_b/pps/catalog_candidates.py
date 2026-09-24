"""Static-catalog retrieval, with complete category coverage kept separately.

Only the provided catalog is shared between notices. Query text and judgments
are never cached here. Relevance retrieves candidates; it cannot establish
product identity, satisfy a designation condition, or certify catalog absence.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import re

from .products import lexical_grams, lexical_text


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


class CatalogCandidates:
    def __init__(self, products, encoder=None):
        # Row identity is distinct from the commodity code. The supplied
        # catalog includes an unnumbered defense category; a code-keyed index
        # must not silently omit it or collapse two rows sharing a code.
        if isinstance(products, dict):
            supplied = []
            for code, row in products.items():
                if row.get('세부품명번호', code) != code:
                    raise ValueError('Catalog key and supplied code disagree')
                supplied.append({**row, '세부품명번호': code})
        elif isinstance(products, (list, tuple)):
            supplied = products
        else:
            raise ValueError('Original catalog rows are required')
        self.rows = []
        for row in supplied:
            code = row.get('세부품명번호')
            if not isinstance(code, str) or (code and not re.fullmatch(r'[0-9]{10}', code)):
                raise ValueError('Catalog codes must be original ten-digit strings or explicitly blank')
            fields = ('대분류', '제품명', '세부품명', '특이사항')
            if any(not isinstance(row.get(k), str) for k in fields) or not row['세부품명']:
                raise ValueError('Catalog names and conditions must be supplied strings')
            self.rows.append({'code': code, 'name': row['세부품명'], 'parent': row['제품명'],
                              'category': row['대분류'], 'condition': row['특이사항']})
        if not self.rows:
            raise ValueError('A complete supplied catalog is required')
        self.rows.sort(key=lambda r: (r['code'], r['category'], r['parent'], r['name'], r['condition']))
        for i, row in enumerate(self.rows):
            row['row_id'] = i+1
        self.catalog_sha256 = fingerprint(self.rows)
        self.by_code = {}
        for i, row in enumerate(self.rows):
            if row['code']:
                self.by_code.setdefault(row['code'], []).append(i)
        self.features = [(lexical_grams(r['name']), lexical_grams(r['parent'])) for r in self.rows]
        df = Counter(g for detail, parent in self.features for g in detail | parent)
        self.idf = {g: math.log(1+len(self.rows)/(1+n)) for g, n in df.items()}
        self.encoder, self._vectors = encoder, None

    def complete_groups(self):
        """Every member is mapped; a broad parent name is not a definition."""
        groups = {}
        for row in self.rows:
            groups.setdefault((row['category'], row['parent']), []).append(row['row_id'])
        return [{'group': i+1, 'category': category, 'parent': parent, 'row_ids': row_ids,
                 'codes': [self.rows[r-1]['code'] for r in row_ids]}
                for i, ((category, parent), row_ids) in enumerate(sorted(groups.items()))]

    def members(self, group_ids):
        groups = self.complete_groups()
        if (not isinstance(group_ids, (list, tuple))
                or any(type(i) is not int or not 1 <= i <= len(groups) for i in group_ids)):
            raise ValueError('Unknown original catalog group')
        row_ids = {r for i in group_ids for r in groups[i-1]['row_ids']}
        return [dict(r) for r in self.rows if r['row_id'] in row_ids]

    @staticmethod
    def render_rows(rows):
        return '\n'.join((r['code'] or '[고시 코드 공란]')+' '+r['category']+' / '+r['parent']+' / '+r['name']
                         + ('; 조건: '+r['condition'] if r['condition'] else '') for r in rows)

    def _dense(self, queries):
        import numpy as np
        if self.encoder is None:
            raise ValueError('Dense or hybrid catalog retrieval needs the supplied encoder')
        if self._vectors is None:
            self._vectors = self.encoder.encode([self.render_rows([r]) for r in self.rows])
        vectors = self.encoder.encode(queries)
        if (not isinstance(self._vectors, np.ndarray) or not isinstance(vectors, np.ndarray)
                or self._vectors.ndim != 2 or vectors.ndim != 2
                or self._vectors.shape[0] != len(self.rows) or vectors.shape[0] != len(queries)
                or not self._vectors.shape[1] or self._vectors.shape[1] != vectors.shape[1]
                or not np.isfinite(self._vectors).all() or not np.isfinite(vectors).all()):
            raise ValueError('Invalid catalog or query embeddings')
        scores = vectors @ self._vectors.T
        if not np.isfinite(scores).all():
            raise ValueError('Non-finite catalog relevance')
        return scores

    def search(self, queries, tokenizer, *, token_budget, method='hybrid',
               required_codes=(), max_candidates=24, selection_policy='facility',
               dense_queries=None):
        if method not in {'lexical', 'dense', 'hybrid'}:
            raise ValueError('Unknown catalog retrieval method')
        if selection_policy not in {'facility', 'rank_frontier', 'semantic_frontier'}:
            raise ValueError('Unknown catalog candidate selection policy')
        if type(token_budget) is not int or token_budget < 1 or type(max_candidates) is not int or max_candidates < 1:
            raise ValueError('Positive exact-integer catalog budgets are required')
        if (not isinstance(queries, (list, tuple)) or not queries
                or any(not isinstance(q, str) or not q.strip() for q in queries)):
            raise ValueError('Catalog queries must contain current source text')
        if dense_queries is None:
            dense_queries = queries
        if (not isinstance(dense_queries, (list, tuple))
                or len(dense_queries) != len(queries)
                or any(not isinstance(q, str) or not q.strip() for q in dense_queries)):
            raise ValueError('Dense catalog queries must align one-to-one with lexical queries')
        # Repeating the same paired item cannot vote its category up repeatedly.
        # Lexical and dense text stay separate: adding semantic context must not
        # inject generic specification words into the exact-name channel.
        pairs = list(dict.fromkeys((re.sub(r'\s+', ' ', left).strip(),
                                    re.sub(r'\s+', ' ', right).strip())
                                   for left, right in zip(queries, dense_queries)))
        queries = [left for left, _ in pairs]
        dense_queries = [right for _, right in pairs]
        if (not isinstance(required_codes, (list, tuple, set))
                or any(not isinstance(c, str) or not re.fullmatch(r'[0-9]{10}', c) for c in required_codes)):
            raise ValueError('Lookup codes must be supplied ten-digit strings')
        required_codes = sorted(set(required_codes))
        dense = self._dense(dense_queries) if method != 'lexical' else None
        scores = [[0.] * len(queries) for _ in self.rows]
        ranks = [[{} for _ in queries] for _ in self.rows]
        for qi, query in enumerate(queries):
            grams = lexical_grams(query, query=True)
            lexical = []
            for ri, (detail, parent) in enumerate(self.features):
                shared = detail & grams
                if shared:
                    support = math.fsum(self.idf[g] for g in sorted(shared))
                    score = support/math.sqrt(max(1, math.fsum(self.idf[g] for g in sorted(detail)))*max(1, len(grams)))
                    exact = lexical_text(self.rows[ri]['name']) in lexical_text(query)
                    lexical.append((ri, (exact, score, len(shared), len(parent & grams))))
            orders = {}
            if method != 'dense':
                orders['lexical'] = [i for i, _ in sorted(lexical, key=lambda pair: (
                    -int(pair[1][0]), -pair[1][1], -pair[1][2], -pair[1][3], self.rows[pair[0]]['code']))]
            if dense is not None:
                orders['dense'] = sorted(range(len(self.rows)), key=lambda i: (-float(dense[qi, i]), self.rows[i]['code']))
            for route, order in orders.items():
                for rank, ri in enumerate(order, 1):
                    scores[ri][qi] += 1/(60+rank)
                    ranks[ri][qi][route] = rank
                    if route == 'lexical':
                        ranks[ri][qi]['lexical_exact'] = dict(lexical)[ri][0]

        selected, omitted_required = [], []
        used = 0
        row_costs = [len(tokenizer.encode(self.render_rows([row]), add_special_tokens=False)) for row in self.rows]

        def cost(indices):
            return len(tokenizer.encode(self.render_rows([self.rows[i] for i in indices]), add_special_tokens=False))

        # Exact lookup is preserved even when a budget cannot show the row.
        # Neither a supplied code nor inclusion in this list certifies identity.
        for code in required_codes:
            for ri in self.by_code.get(code, []):
                needed = cost([*selected, ri])
                if needed <= token_budget and len(selected) < max_candidates:
                    selected.append(ri)
                    used = needed
                else:
                    omitted_required.append(code)
        covered = [max((scores[i][q] for i in selected), default=0.) for q in range(len(queries))]
        pool = {i for i, row in enumerate(scores) if any(row)} - set(selected)
        # Rank evidence and display cost are different quantities. A short row
        # must not outrank a much stronger candidate just because its complete
        # designation condition takes fewer tokens. Each query/route exposes
        # three alternatives, with diminishing returns after it is represented.
        # This is a retrieval frontier, never a calibrated relevance threshold.
        # A semantic frontier spends at most one dense rank band per query
        # within the global row cap.  Single/few-item searches can inspect a
        # wider semantic neighbourhood; long mixed inventories retain the
        # narrow frontier rather than filling the prompt with rank tails.
        semantic_dense_depth = min(10, max(3, max_candidates//len(queries)))
        frontier_depths = ({'lexical': 3, 'dense': semantic_dense_depth}
                           if selection_policy == 'semantic_frontier'
                           else {'lexical': 3, 'dense': 3})
        channels = [(q, route) for q in range(len(queries)) for route in ('lexical', 'dense')
                    if any(route in row[q] for row in ranks)]
        def on_frontier(i, q, route):
            rank = ranks[i][q].get(route, frontier_depths[route]+1)
            if rank > frontier_depths[route]:
                return False
            # In a hybrid semantic search, fuzzy lexical fragments must not
            # crowd out the wider dense neighbourhood. Exact name inclusion
            # remains a high-value candidate; lexical-only mode is unchanged.
            return not (selection_policy == 'semantic_frontier'
                and method == 'hybrid' and route == 'lexical'
                and not ranks[i][q].get('lexical_exact'))
        def frontier_count(q, route):
            return sum(on_frontier(i, q, route) for i in selected)
        # Facility coverage across distinct source queries prevents one common
        # product word from crowding out every other item in a mixed purchase.
        while pool and len(selected) < max_candidates:
            best = None
            for ri in sorted(pool):
                if selection_policy in {'rank_frontier', 'semantic_frontier'}:
                    gain = math.fsum(1/(ranks[ri][q][route]*(1+frontier_count(q, route)))
                        for q, route in channels
                        if on_frontier(ri, q, route))
                    if not gain:
                        continue
                    key = (gain, max(scores[ri]), -row_costs[ri], -ri)
                else:
                    gain = math.fsum(max(v-c, 0.) for v, c in zip(scores[ri], covered))
                    relevance = max(scores[ri])
                    key = (gain/max(1, row_costs[ri]+bool(selected)), gain, relevance, -row_costs[ri], -ri)
                if best is None or key > best[0]:
                    best = key, ri
            if best is None:
                break
            _, ri = best
            pool.remove(ri)
            needed = cost([*selected, ri])
            if needed > token_budget:
                continue
            selected.append(ri)
            used = needed
            covered = [max(c, v) for c, v in zip(covered, scores[ri])]
        candidates = [{**self.rows[i], 'query_ranks': ranks[i]} for i in selected]
        return {'catalog_sha256': self.catalog_sha256, 'method': method, 'queries': queries,
            'dense_queries': dense_queries,
            'selection_policy': selection_policy,
            'candidate_frontier_depth': (3 if selection_policy == 'rank_frontier' else
                                         semantic_dense_depth
                                         if selection_policy == 'semantic_frontier' else None),
            'candidate_frontier_depths': (frontier_depths
                if selection_policy in {'rank_frontier', 'semantic_frontier'} else None),
            'candidates': candidates, 'catalog_tokens': used, 'catalog_token_budget': token_budget,
            'required_lookups': [{'code': c, 'listed': c in self.by_code,
                                 'row_ids': [self.rows[i]['row_id'] for i in self.by_code.get(c, [])]}
                                for c in required_codes],
            'omitted_required_codes': sorted(set(omitted_required)), 'catalog_rows': len(self.rows),
            'all_catalog_rows_shown': len(selected) == len(self.rows),
            'unshown_rows': len(self.rows)-len(selected), 'conditions_truncated': False,
            'identity_certified': False, 'absence_certified': False,
            'complete_parent_groups': self.complete_groups(),
            'parent_names_are_not_product_definitions': True}
