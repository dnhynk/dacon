"""Budgeted search/read boundary for the current notice only.

Dense scores are relevance, never legal probabilities. All returned source text
is re-read from original document offsets. Context expansion is shared by the
lexical, dense and hybrid arms; its effect can be measured independently.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict
from functools import lru_cache
import hashlib
import math
import re

from .retrieval import NoticeIndex, Span, QUERIES, _CONDITION, _HEADING, _source_units

FACT_QUERIES = {
    'specification': (
        '구매하여 납품할 제품의 필수 규격, 제조사, 모델명과 부품의 사양',
        '동등 제품 또는 대체품을 납품할 수 있는 조건과 허용 범위 및 예외',
        '기존 장비와 연결되는 신규 구매 품목 및 전체 납품 대상의 관계'),
    'eligibility': (
        '입찰에 참가할 수 있는 업체의 업종, 면허, 등록 및 필수 증명서',
        '입찰방법과 참가자격에 정한 중기업, 소기업, 소상공인 제한 및 확인서',
        '직접생산확인증명서가 필요한 실제 구매 품목과 적용 조건',
        '비영리법인 등의 참가 허용, 자격 면제, 대체 서류 및 예외 조건',
        '비영리법인의 이윤과 부가가치세를 계약금액에서 정산하는 조건'),
    'assurance': (
        '기술지원확약서와 물품공급확약서를 발급하는 주체 및 제출하는 주체',
        '입찰 참가 서류와 낙찰 후 계약 서류의 종류 및 제출 시점',
        '제조사의 확약서 제출을 면제하거나 다른 자료로 대체할 수 있는 조건'),
    'software': (
        '계약상대자가 제출해야 하는 소프트웨어 소스, 실행파일, 사용권 및 관련 자료',
        '납품 부품이 기존 시스템과 연결되어 작동하도록 요구하는 조건',
        '전체 과업과 납품 내역에 포함된 개발, 수정, 커스터마이징, 업데이트 및 운영 의무',
        '수급인의 작업 도구 및 교육생의 실습 활동과 발주기관에 제공하는 산출물의 구별',
        '소프트웨어 사업금액과 대기업 참여 하한 제한, 상호출자 제한 및 허용 예외'),
    'comparison': (
        '전체 사업금액과 차수별 계약금액 및 추정가격과 부가가치세의 관계',
        '공고문과 첨부 문서에 각각 명시된 참가자격, 지역, 공동계약, 업종 및 입찰방법'),
}


def factual_queries(items):
    topics, other = [], []
    for item in items:
        if type(item) is not int or item not in QUERIES:
            raise ValueError('Invalid search item')
        topic = ('specification' if item == 9 else 'eligibility' if 10 <= item <= 18
                 else 'assurance' if item == 19 else 'software' if item == 20
                 else 'comparison' if item == 24 else None)
        if topic:
            if topic not in topics:
                topics.append(topic)
        else:
            other.append('다음 사항에 관한 실제 조건, 허용, 면제 및 예외: ' + ', '.join(QUERIES[item]))
    return tuple(q for topic in topics for q in FACT_QUERIES[topic]) + tuple(other)


def merge_ranges(ranges, docs):
    """Coalesce only overlap or whitespace; never hide an omitted word."""
    try:
        coordinates = iter(ranges)
    except TypeError as exc:
        raise ValueError('Source ranges must be an iterable of coordinates') from exc
    validated = set()
    for coordinate in coordinates:
        if not isinstance(coordinate, (tuple, list)) or len(coordinate) != 3:
            raise ValueError('Source coordinates must contain document, start and end')
        di, lo, hi = coordinate
        if type(di) is not int or not 0 <= di < len(docs):
            raise ValueError('Invalid source document')
        if type(lo) is not int or type(hi) is not int or not 0 <= lo < hi <= len(docs[di]['text']):
            raise ValueError('Invalid source offsets')
        # Validate every occurrence before hashing: False == 0 and 3. == 3
        # must not let a malformed JSON coordinate hide behind a valid one.
        validated.add((di, lo, hi))
    result = []
    for di, lo, hi in sorted(validated):
        if result and result[-1][0] == di:
            previous = result[-1]
            if lo <= previous[2] or docs[di]['text'][previous[2]:lo].isspace():
                result[-1] = (di, previous[1], max(previous[2], hi))
                continue
        result.append((di, lo, hi))
    return tuple(result)


def _validate_source_budget(budget):
    if type(budget) is not int or budget < 1:
        raise ValueError('Source token budget must be a positive integer')


_NUMBERED_HEADING = re.compile(r'^(?:제\s*\d+\s*[장절]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[. ]|\d+(?:\.\d+)*[.)]|[가-하][.)]|[□■])\s*\S')
_NUMERIC_HEADING = re.compile(r'^(?P<path>\d+(?:\.\d+)*)(?P<end>[.)])(?P<gap>\s*)(?P<title>\S.*)$')
_DECIMAL_SECTION = re.compile(r'^(?P<path>\d+(?:\.\d+)+)(?P<end>)\s+(?P<title>[^\d\s].*)$')
_QUANTITY_TITLE = re.compile(
    r'^(?:%|℃|°|(?:억|천만|백만|만)\s*(?:원)?(?:\s|[()/]|$)|원(?:\s|[()/]|$)|'
    r'(?:이상|이하|미만|초과)(?:\s|[()/]|$)|'
    r'(?:세트|개|대|식|장|년|월|일|명)(?:\s|[()/]|$)|'
    r'(?:[kmg]?hz|[munck]?m|[kmg]?b|[kmg]?bps|[mk]?v|[mk]?w|mah|[mk]?a|kg|g|l|ml)(?:\s|[()/]|$))', re.I)


def numbered_heading(text):
    """Use the same explicit numbering grammar for discovery and ancestry.

    A decimal quantity is not a section path. An explicit dot/parenthesis can
    touch its caption; a path without its final dot needs a separating space.
    This identifies source structure, not the legal scope of the heading.
    """
    date = re.match(r'^\d{4}\s*\.\s*(?P<month>\d{1,2})(?:\s*\.\s*(?P<day>\d{1,2}))?(?=[.\s(（]|$)', text)
    if date and 1 <= int(date['month']) <= 12 and (not date['day'] or 1 <= int(date['day']) <= 31):
        return None
    match = _DECIMAL_SECTION.fullmatch(text) or _NUMERIC_HEADING.fullmatch(text)
    if not match:
        return None
    title, path = match['title'], match['path'].split('.')
    if match['end'] == '.' and not match['gap'] and title[0].isdigit():
        return None  # Do not backtrack from a decimal value to an integer heading.
    if not any(c.isalpha() for c in title):
        return None
    if len(path) > 1 and _QUANTITY_TITLE.search(title):
        return None
    return match


def is_heading(text, *, compact_numbering=True):
    if compact_numbering and re.match(r'\d', text):
        return bool(numbered_heading(text) and len(text) <= 90
                    and not re.search(r'(?:한다|합니다|하여야|해야|있다|없다|한함)[.。]?$', text))
    return bool(_HEADING.fullmatch(text) or
                (len(text) <= 90 and _NUMBERED_HEADING.search(text)
                 and not re.search(r'(?:한다|합니다|하여야|해야|있다|없다|한함)[.。]?$', text)))


def heading_ancestry(text, units, *, compact_numbering=True):
    """Track explicit source numbering, including numbered operative clauses.

    A sentence can close a preceding sibling even when it is too long or too
    verbal to be a title. Keep that original sentence as structural context;
    this does not infer repaired reading order or semantic/legal applicability.
    """
    stack, paths = [], []
    for index, (lo, hi) in enumerate(units):
        line = text[lo:hi]
        number = (numbered_heading(line) if compact_numbering else
                  re.match(r'(?P<path>\d+(?:\.\d+)*)(?P<end>[.)]?)(?=\s)', line))
        structural = compact_numbering and (number is not None or re.match(
            r'^(?:제\s*\d+\s*[장절]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[. ]|[가-하][.)])\s*\S', line))
        if structural or is_heading(line, compact_numbering=compact_numbering):
            path = tuple(map(int, number['path'].split('.'))) if number else None
            if re.match(r'제\s*\d+\s*장', line):
                rank = 0
            elif re.match(r'제\s*\d+\s*절', line):
                rank = 1
            elif re.match(r'[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[. ]', line):
                rank = 2
            elif number:
                rank = 10 + len(path) - 1 if number['end'] == '.' or len(path) > 1 else 30
            elif re.match(r'[가-하][.)]', line):
                rank = 20
            else:
                rank = None
            if rank is None:
                stack = []  # An unnumbered heading has no proved parent level.
            else:
                stack = [p for p in stack if p[1] is not None and p[1] < rank]
                if path is not None and len(path) > 1:
                    # An orphan 3.1 must not borrow 2 as its governing parent.
                    stack = [p for p in stack if p[2] is None or
                             (len(p[2]) < len(path) and path[:len(p[2])] == p[2])]
            stack.append((index, rank, path))
        paths.append(tuple(p[0] for p in stack))
    return paths


class NoticeSearch:
    def __init__(self, rec, source_tokenizer, encoder=None, *, heading_context='ancestors', table_context='local'):
        if heading_context not in {'ancestors', 'nearest', 'legacy_ancestors'}:
            raise ValueError('Unknown heading context policy')
        self.heading_context = heading_context
        if table_context not in {'local', 'atomic_purchase'}:
            raise ValueError('Unknown purchase table context policy')
        self.table_context = table_context
        from .purchase_tables import purchase_tables
        self.purchase_tables = [purchase_tables(d['text']) for d in rec['docs']] if table_context != 'local' else []
        self.rec = rec
        self.tokenizer = source_tokenizer
        self.encoder = encoder
        self.lexical = NoticeIndex(rec)
        self.chunks = self.lexical.spans
        self.units = [_source_units(d['text']) for d in rec['docs']]
        compact_numbering = heading_context == 'ancestors'
        # Explicit controls preserve the old input construction for comparisons.
        self.headings = [[i for i, (lo, hi) in enumerate(units)
                          if is_heading(d['text'][lo:hi], compact_numbering=compact_numbering)]
                         for d, units in zip(rec['docs'], self.units)]
        self.ancestors = [heading_ancestry(d['text'], units, compact_numbering=compact_numbering)
                          for d, units in zip(rec['docs'], self.units)]
        self.doc_hashes = [hashlib.sha256(d['text'].encode('utf-8')).hexdigest() for d in rec['docs']]
        self.vectors = None
        self.sparse_vectors = None
        self.colbert_vectors = None
        self._queries = {}
        self._query_features = {}
        self._colbert_rankings = {}
        self.colbert_receipt = {'scored_query_chunk_pairs': 0, 'seconds': 0.}
        self._contexts = [self._context(s) for s in self.chunks]
        # Cache lifetime is this notice. Neither contents nor answers are shared.
        self._token_cost = lru_cache(maxsize=32768)(self._count_range)

    def _count_range(self, di, lo, hi):
        return len(self.tokenizer.encode(self.rec['docs'][di]['text'][lo:hi], add_special_tokens=False))

    def token_cost(self, ranges):
        return sum(self._token_cost(*r) for r in merge_ranges(ranges, self.rec['docs']))

    def _context(self, span):
        text, units = self.rec['docs'][span.doc_index]['text'], self.units[span.doc_index]
        ends = [hi for lo, hi in units]
        starts = [lo for lo, hi in units]
        first = max(0, bisect_right(ends, span.start))
        last = min(len(units) - 1, bisect_left(starts, span.end) - 1)
        first, last = max(0, first - 1), min(len(units) - 1, last + 1)
        # Do not cut off a following or preceding contiguous proviso chain.
        while first and _CONDITION.search(text[slice(*units[first - 1])]):
            first -= 1
        while last + 1 < len(units) and _CONDITION.search(text[slice(*units[last + 1])]):
            last += 1
        ranges = [(span.doc_index, min(span.start, units[first][0]), max(span.end, units[last][1]))]
        # Heading references are separate exact ranges when intervening text was
        # omitted. We do not pretend that PDF reading order has been repaired.
        if self.heading_context == 'nearest':
            # Explicit control for fixed-vector comparisons with older inputs.
            preceding = [i for i in self.headings[span.doc_index] if i <= first]
            ancestors = preceding[-1:]
        else:
            ancestors = sorted({i for unit in range(first, last + 1)
                                for i in self.ancestors[span.doc_index][unit]})
        ranges.extend((span.doc_index, *units[i]) for i in ancestors)
        # Include the start/header of the same contiguous pipe-table.
        table_first = first
        if '|' in text[slice(*units[first])]:
            while table_first and '|' in text[slice(*units[table_first - 1])]:
                table_first -= 1
            ranges.append((span.doc_index, *units[table_first]))
        if self.table_context == 'atomic_purchase':
            for table in self.purchase_tables[span.doc_index]:
                if span.start < table['end'] and table['start'] < span.end:
                    ranges.append((span.doc_index, table['start'], table['end']))
                    if table['parent_range'] is not None:
                        ranges.append((span.doc_index, *table['parent_range']))
        return merge_ranges(ranges, self.rec['docs'])

    def _lexical_lists(self, items):
        return [[i for i, score in self.lexical.ranked[k]] for k in items]

    def _query_lexical_lists(self, queries):
        # Source-local character terms handle Korean inflection without another
        # model. No other evaluation notice contributes document frequencies.
        rankings = []
        for query in queries:
            words = re.findall(r'[가-힣]+|[a-z0-9_-]+', query.lower())
            terms = sorted({w for w in words if len(w) > 1}
                | {w[i:i+3] for w in words if re.fullmatch('[가-힣]+', w) for i in range(len(w)-2)})
            score = [0.] * len(self.chunks)
            for term in terms:
                counts = [t.count(term) for t in self.lexical.compact]
                df = sum(bool(n) for n in counts)
                if not df:
                    continue
                idf = math.log(1 + (len(counts)-df+.5)/(df+.5))
                for i, count in enumerate(counts):
                    if count:
                        score[i] += idf * count * 2.2 / (count + 1.2 * (.25 +
                            .75 * len(self.chunks[i].text) / self.lexical.average_length))
            rankings.append(sorted((i for i, s in enumerate(score) if s), key=lambda i: (-score[i], i)))
        return rankings

    def _dense_lists(self, queries):
        if self.encoder is None:
            raise ValueError('Dense search requires an explicit offline encoder')
        import numpy as np
        if self.vectors is None:
            if self._joint_features_enabled():
                self._cache_document_features()
            else:
                self.vectors = self.encoder.encode([s.text for s in self.chunks])
        if queries not in self._queries:
            if self._joint_features_enabled():
                if queries not in self._query_features:
                    self._query_features[queries] = self._encode_features(queries)
                q = self._query_features[queries]['dense']
            else:
                q = self.encoder.encode(queries)
            scores = self.vectors @ q.T
            if scores.shape != (len(self.chunks), len(queries)) or not np.isfinite(scores).all():
                raise ValueError('Invalid dense retrieval scores')
            self._queries[queries] = [sorted(range(len(self.chunks)), key=lambda i: (-float(scores[i, j]), i))
                                      for j in range(len(queries))]
        return self._queries[queries]

    def _joint_features_enabled(self):
        return (getattr(self.encoder, 'sparse_enabled', False)
                or getattr(self.encoder, 'colbert_enabled', False))

    def _cache_document_features(self):
        features = self._encode_features([s.text for s in self.chunks])
        self.vectors = features['dense']
        self.sparse_vectors = features.get('sparse')
        self.colbert_vectors = features.get('colbert')

    def _encode_features(self, texts):
        import numpy as np
        from .embeddings import sparse_weights
        features = self.encoder.encode_features(texts)
        dense, sparse = features['dense'], features.get('sparse')
        if (not isinstance(dense, np.ndarray) or dense.ndim != 2 or dense.shape[0] != len(texts)
                or not np.isfinite(dense).all()):
            raise ValueError('Invalid joint retrieval feature dimensions')
        if getattr(self.encoder, 'sparse_enabled', False):
            if not isinstance(sparse, list) or len(sparse) != len(texts):
                raise ValueError('Invalid joint retrieval feature dimensions')
            for weights in sparse:
                if not isinstance(weights, dict):
                    raise ValueError('Invalid sparse retrieval feature mapping')
                sparse_weights(list(weights), list(weights.values()))
        if getattr(self.encoder, 'colbert_enabled', False):
            multi = features.get('colbert')
            if not isinstance(multi, list) or len(multi) != len(texts):
                raise ValueError('Invalid ColBERT feature dimensions')
            for vectors in multi:
                if (not isinstance(vectors, np.ndarray) or vectors.ndim != 2
                        or not vectors.shape[0] or vectors.shape[1] != dense.shape[1]
                        or not np.issubdtype(vectors.dtype, np.floating) or not np.isfinite(vectors).all()
                        or not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-4)):
                    raise ValueError('Invalid normalized ColBERT token vectors')
        return features

    def _sparse_lists(self, queries):
        if self.encoder is None or not getattr(self.encoder, 'sparse_enabled', False):
            raise ValueError('Sparse retrieval requires the explicitly enabled fixed BGE sparse head')
        from .embeddings import sparse_similarity
        if self.sparse_vectors is None:
            self._cache_document_features()
        if queries not in self._query_features:
            self._query_features[queries] = self._encode_features(queries)
        rankings = []
        for q in self._query_features[queries]['sparse']:
            scores = [sparse_similarity(d, q) for d in self.sparse_vectors]
            if not all(math.isfinite(s) and s >= 0 for s in scores):
                raise ValueError('Invalid sparse retrieval scores')
            rankings.append(sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: (-scores[i], i)))
        return rankings

    def _colbert_lists(self, queries):
        if self.encoder is None or not getattr(self.encoder, 'colbert_enabled', False):
            raise ValueError('ColBERT retrieval requires the explicitly enabled fixed head')
        from .embeddings import colbert_similarity
        import time
        if self.colbert_vectors is None:
            self._cache_document_features()
        if queries not in self._query_features:
            self._query_features[queries] = self._encode_features(queries)
        if queries not in self._colbert_rankings:
            began = time.monotonic()
            rankings = []
            for query in self._query_features[queries]['colbert']:
                scores = [colbert_similarity(query, doc) for doc in self.colbert_vectors]
                rankings.append(sorted(range(len(scores)), key=lambda i: (-scores[i], i)))
            self._colbert_rankings[queries] = rankings
            self.colbert_receipt['scored_query_chunk_pairs'] += len(queries) * len(self.chunks)
            self.colbert_receipt['seconds'] += time.monotonic() - began
        return self._colbert_rankings[queries]

    @staticmethod
    def _fuse(lists, *, depth=40, query_aggregation='mean', stable_group_mean=False):
        # Default preserves historical averaging. The optional best-query arm
        # treats different fact questions as alternatives within each retriever,
        # so one decisive fact is not diluted by unrelated questions. Matching
        # two retriever families still supplies two independent rank votes.
        if query_aggregation not in {'mean', 'best'}:
            raise ValueError('Unknown factual-query aggregation')
        if stable_group_mean and query_aggregation == 'mean':
            contributions = {}
            for family in lists:
                ranks = {}
                for ranking in family:
                    for rank, i in enumerate(ranking[:depth], 1):
                        ranks.setdefault(i, Counter())[rank] += 1
                for i, counts in ranks.items():
                    # Normalize multiplicities before floating-point addition:
                    # 24 identical rank votes have the same mass as one vote.
                    value = math.fsum((count / max(1, len(family))) / (60 + rank)
                                      for rank, count in sorted(counts.items()))
                    contributions.setdefault(i, []).append(value)
            score = {i: math.fsum(values) for i, values in contributions.items()}
            return sorted(score, key=lambda i: (-score[i], i))
        score = {}
        for family in lists:
            family_score = {}
            for ranking in family:
                for rank, i in enumerate(ranking[:depth], 1):
                    if query_aggregation == 'mean':
                        # Keep the original addition order and float rounding.
                        score[i] = score.get(i, 0.) + 1. / (max(1, len(family)) * (60 + rank))
                    else:
                        family_score[i] = max(family_score.get(i, 0.), 1. / (60 + rank))
            for i, value in family_score.items():
                score[i] = score.get(i, 0.) + value
        return sorted(score, key=lambda i: (-score[i], i))

    def search(self, items, *, token_budget, method='hybrid', queries=None, expand_context=True,
               selection_policy='rrf', required_ranges=(), query_aggregation='mean', query_groups=None):
        _validate_source_budget(token_budget)
        items = tuple(items)
        try:
            required_ranges = tuple(required_ranges)
        except TypeError as exc:
            raise ValueError('Required source ranges must be iterable') from exc
        factual_queries(items)  # Validate even when callers supply their own queries.
        groups = None
        if query_groups is not None:
            if queries is not None or not isinstance(query_groups, Mapping) or not query_groups:
                raise ValueError('Use either factual queries or nonempty named query groups')
            groups = {}
            for name, questions in query_groups.items():
                if (not isinstance(name, str) or not name.strip() or
                        not isinstance(questions, (tuple, list)) or not questions or
                        any(not isinstance(q, str) or not q.strip() for q in questions)):
                    raise ValueError('Each query group needs a name and a list of nonempty questions')
                groups[name] = tuple(dict.fromkeys(q.strip() for q in questions))
            # Encode each distinct question once in the current notice. Groups
            # retain their own rank budget even when another group grows.
            queries = tuple(dict.fromkeys(q for questions in groups.values() for q in questions))
        elif isinstance(queries, str):
            raise ValueError('Factual queries must be a collection, not one string')
        custom_queries = queries is not None
        queries = tuple(queries) if queries is not None else factual_queries(items)
        if not items or not queries or any(not isinstance(q, str) or not q.strip() for q in queries):
            raise ValueError('Search needs nonempty items and factual queries')
        # Reject incompatible tool arguments before loading/encoding a model.
        if selection_policy not in {'rrf', 'facet_cover', 'evidence_cover', 'evidence_refill'}:
            raise ValueError('Unknown candidate selection policy')
        if query_aggregation not in {'mean', 'best'}:
            raise ValueError('Unknown factual-query aggregation')
        if selection_policy in {'evidence_cover', 'evidence_refill'} and (not expand_context or query_aggregation != 'mean'):
            raise ValueError('Evidence selection requires expanded context and its own mean-family rank objective')
        if method == 'current':
            if selection_policy != 'rrf' or required_ranges or query_aggregation != 'mean' or groups is not None:
                raise ValueError('Current baseline does not support altered selection policies')
            return self._current(items, token_budget)
        if method not in {'lexical', 'dense', 'hybrid', 'sparse', 'hybrid_sparse', 'colbert', 'hybrid_colbert'}:
            raise ValueError('Unknown retrieval method')
        ranges = merge_ranges(required_ranges, self.rec['docs'])
        if self.token_cost(ranges) > token_budget:
            raise ValueError('Required source witnesses exceed the token budget')
        families = []
        def append_questions(rankings):
            if groups is None:
                families.append(rankings)
            else:
                by_question = dict(zip(queries, rankings))
                families.extend([[by_question[q] for q in questions] for questions in groups.values()])
        if method in {'lexical', 'hybrid', 'hybrid_sparse', 'hybrid_colbert'}:
            family = self._lexical_lists(items)
            if groups is not None:
                families.append(family)
                append_questions(self._query_lexical_lists(queries))
            elif custom_queries:
                family += self._query_lexical_lists(queries)
            if groups is None:
                families.append(family)
        if method in {'dense', 'hybrid', 'hybrid_sparse', 'hybrid_colbert'}:
            append_questions(self._dense_lists(queries))
        if method in {'sparse', 'hybrid_sparse'}:
            append_questions(self._sparse_lists(queries))
        if method in {'colbert', 'hybrid_colbert'}:
            append_questions(self._colbert_lists(queries))
        order = self._fuse(families, query_aggregation=query_aggregation, stable_group_mean=groups is not None)
        selected, skipped, packing = [], [], None
        if selection_policy in {'evidence_cover', 'evidence_refill'}:
            from .evidence_selection import pack_evidence
            ranges, selected, skipped, packing = pack_evidence(
                self, families, order, ranges, token_budget, expand_context,
                refill=selection_policy == 'evidence_refill')
        if selection_policy == 'facet_cover':
            ranges, selected = self._facet_cover(families, order, ranges, token_budget, expand_context)
        # Spend any remaining budget with the unchanged reciprocal-rank order.
        # Every retained word, including mandatory witnesses and context, counts.
        for i in (() if packing is not None else order):
            s = self.chunks[i]
            context = self._contexts[i] if expand_context else ((s.doc_index, s.start, s.end),)
            proposed = merge_ranges([*ranges, *context], self.rec['docs'])
            if self.token_cost(proposed) <= token_budget:
                if proposed != ranges:
                    selected.append(i)
                ranges = proposed
            else:
                skipped.append(i)
        return self._result(ranges, method, token_budget, {
            'queries': list(queries), 'context_expansion': expand_context,
            'heading_context': self.heading_context,
            'table_context': self.table_context,
            'selection_policy': selection_policy, 'required_ranges': list(required_ranges),
            'query_aggregation': query_aggregation,
            'candidate_chunks': len(order), 'selected_candidates': selected,
            'budget_skipped_candidates': skipped, 'rrf_k': 60, 'candidate_depth_per_query': 40,
            **({'query_groups': {name: list(q) for name, q in groups.items()},
                'query_group_rank_budget': 'equal_per_group_and_retriever; item-role lexical is separate'}
               if groups is not None else {}),
            **({'packing': packing} if packing is not None else {})})

    def _facet_cover(self, families, order, ranges, budget, expand):
        """Diminishing rank utility across factual queries, per added source token.

        Repeated hits for an already represented query add no coverage utility.
        This diversifies reading, without interpreting relevance as probability.
        """
        facets = [(ranking[:40], 1. / (len(families) * max(1, len(family))))
                  for family in families for ranking in family]
        support = {i: {} for i in order}
        for j, (ranking, weight) in enumerate(facets):
            for rank, i in enumerate(ranking, 1):
                support[i][j] = weight / rank
        represented = [0.] * len(facets)
        chosen, remaining = [], list(order)
        while remaining:
            cost, best = self.token_cost(ranges), None
            proposals = {}
            for i in remaining:
                s = self.chunks[i]
                context = self._contexts[i] if expand else ((s.doc_index, s.start, s.end),)
                proposed = merge_ranges([*ranges, *context], self.rec['docs'])
                if proposed == ranges:
                    for j, value in support[i].items():
                        represented[j] = max(represented[j], value)
                    continue
                proposals[i] = proposed
            for i, proposed in proposals.items():
                spent = self.token_cost(proposed)
                gain = sum(max(0., v - represented[j]) for j, v in support[i].items())
                if spent <= budget and gain > 0:
                    key = (gain / max(1, spent - cost), gain, -i)
                    if best is None or key > best[0]:
                        best = (key, i, proposed)
            if best is None:
                break
            _, i, ranges = best
            chosen.append(i)
            remaining.remove(i)
            for j, value in support[i].items():
                represented[j] = max(represented[j], value)
        return ranges, chosen

    def refine(self, prior, items, *, queries, required_ranges=(), method='hybrid',
               selection_policy='facet_cover'):
        """Replace a bounded context, preserving named witnesses and reading cost.

        A second read is not free: cumulative unique source tokens and removed
        context are reported separately from the final prompt's source budget.
        """
        from .prompts import verified_search_spans
        old = verified_search_spans(self.rec, prior, self.tokenizer)
        result = self.search(items, token_budget=prior['source_token_budget'], method=method,
            queries=queries, required_ranges=required_ranges, selection_policy=selection_policy)
        previous = [(s.doc_index, s.start, s.end) for s in old]
        current = [(s['doc_index'], s['start'], s['end']) for s in result['spans']]
        result['diagnostics']['refinement'] = {
            'previous_source_tokens': prior['source_tokens'], 'new_source_tokens': result['source_tokens'],
            'cumulative_unique_source_tokens': self.token_cost([*previous, *current]),
            'previous_ranges': previous, 'current_ranges': current,
            'note': 'Final context has the same cap; a multi-round comparison must also match cumulative reading and model-call costs.'}
        return result

    def _current(self, items, budget):
        # Existing evidence_first selector remains byte-for-byte untouched.
        # Calibrate its character allowance against the same actual tokenizer;
        # choose the most filled feasible probe, without looking at annotations.
        low, high, best, spent = 440, sum(len(d['text']) for d in self.rec['docs']) * 2 + 440, (), -1
        probes = []
        while low <= high and len(probes) < 20:
            mid = (low + high) // 2
            spans = self.lexical.select(mid, items=items, mode='evidence_first')
            ranges = merge_ranges([(s.doc_index, s.start, s.end) for s in spans], self.rec['docs'])
            cost = self.token_cost(ranges)
            probes.append({'character_allowance': mid, 'source_tokens': cost})
            if cost <= budget:
                if cost > spent:
                    best, spent = ranges, cost
                low = mid + 1
            else:
                high = mid - 1
        return self._result(best, 'current', budget, {'calibration_probes': probes,
            'note': 'Character selector may be non-monotonic; this is the most filled feasible probed allowance.'})

    def _result(self, ranges, method, budget, diagnostics):
        ranges = merge_ranges(ranges, self.rec['docs'])
        spans = [Span(di, self.rec['docs'][di]['type'], lo, hi, self.rec['docs'][di]['text'][lo:hi])
                 for di, lo, hi in ranges]
        docs = []
        for di, d in enumerate(self.rec['docs']):
            shown = [(lo, hi) for index, lo, hi in ranges if index == di]
            count = sum(hi - lo for lo, hi in shown)
            missing = []
            cursor = 0
            for lo, hi in shown:
                if d['text'][cursor:lo].strip():
                    missing.append([cursor, lo])
                cursor = hi
            if d['text'][cursor:].strip():
                missing.append([cursor, len(d['text'])])
            docs.append({'doc_index': di, 'doc_id': d['doc_id'], 'doc_type': d['type'],
                'doc_sha256': self.doc_hashes[di], 'total_chars': len(d['text']),
                'returned_chars': count, 'returned_ranges': shown, 'unreturned_ranges': missing,
                'all_nonwhitespace_returned': not missing})
        return {'record_id': self.rec['id'], 'method': method, 'source_token_budget': budget,
            'source_tokens': self.token_cost(ranges), 'spans': [asdict(s) for s in spans],
            'documents': docs, 'diagnostics': diagnostics,
            'coverage': {'indexed_document_count': len(docs),
                'returned_document_count': sum(bool(d['returned_ranges']) for d in docs),
                'all_provided_text_returned': all(d['all_nonwhitespace_returned'] for d in docs),
                'returned_chars': sum(d['returned_chars'] for d in docs),
                'provided_chars': sum(d['total_chars'] for d in docs),
                'input_completeness': self.rec.get('input_completeness', {}),
                'dropped_doc_counts': self.rec.get('dropped_doc_counts', {}),
                'referenced_document_completeness': 'not_verified_by_search',
                'reading_order_verified': False, 'absence_verified': False,
                'note': 'Indexed, returned and legally reviewed are distinct; a search miss proves no absence.'}}

    def read(self, ranges, *, token_budget):
        """Explicit follow-up read; never silently clips a requested condition."""
        _validate_source_budget(token_budget)
        merged = merge_ranges(ranges, self.rec['docs'])
        if self.token_cost(merged) > token_budget:
            raise ValueError('Requested source context exceeds token budget')
        return self._result(merged, 'read', token_budget, {'explicit_source_ranges': True})
