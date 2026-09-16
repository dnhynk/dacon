"""Per-notice lexical retrieval. Corpus statistics never use other test notices."""
from __future__ import annotations

import math
import re
from bisect import bisect_left
from collections import Counter, deque
from dataclasses import dataclass

# Vocabulary comes from the official item table and development notices.
QUERIES = {
    1: ("참가자격", "참여가능", "한정", "대학", "산학협력단", "공공기관", "비영리법인", "연구기관", "특정기관"),
    2: ("실적", "수행실적", "납품실적", "이행실적", "최근", "이상", "추정가격", "수의계약"),
    3: ("실적", "단일", "배수", "이상", "규모", "추정가격", "사업예산", "기초금액"),
    4: ("실적", "발주", "국가기관", "공공기관", "대학병원", "특정", "단일"),
    5: ("지역제한", "소재지", "영업소", "본점", "본사", "추정가격", "고시금액"),
    6: ("지역제한", "소재지", "영업소", "본점", "단위=기초", "소액수의", "견적"),
    7: ("지역제한", "소재지", "영업소", "인접", "관할구역", "10인", "본점"),
    8: ("실적", "지역제한", "영업소", "소재지", "본점", "중복제한"),
    9: ("모델", "모델명", "제조사", "동등", "동급", "품명", "규격", "브랜드", "Chipset"),
    10: ("직접생산", "생산확인", "세부품명", "경쟁제품", "참가자격", "증명서"),
    11: ("중소기업", "중기업", "소기업", "소상공인", "경쟁제품", "확인서", "참가자격"),
    12: ("직접생산", "생산확인", "세부품명", "경쟁제품", "확인증명서"),
    13: ("소기업", "소상공인", "중기업", "경쟁제품", "확인서"),
    14: ("중소기업", "중기업", "소기업", "소상공인", "확인서", "예외", "판로지원"),
    15: ("소기업", "소상공인", "확인서", "추정가격", "예외"),
    16: ("중소기업", "중기업", "소기업", "소상공인", "비영리", "예외", "2조의3", "참가자격"),
    17: ("중소기업", "중기업", "소기업", "소상공인", "확인서", "예외"),
    18: ("소기업", "소상공인", "중소기업", "비영리", "예외", "2조의3", "참가자격"),
    19: ("공급확약", "기술지원", "제조사", "확약서", "협약서", "발급", "낙찰자", "계약체결"),
    20: ("소프트웨어", "대기업", "상호출자", "사업금액", "참여제한", "사업자", "정보화"),
    21: ("공동수급", "공동이행", "분담이행", "지분", "출자비율", "참여비율", "구성원", "공동계약"),
    22: ("설명회", "현장설명", "사업설명", "참석", "참가자격", "협상"),
    23: ("설명회", "현장설명", "사업설명", "공고기간", "공고일", "제안서", "일시", "긴급"),
    24: ("기초금액", "사업금액", "추정가격", "사업예산", "지역제한", "계약방법", "입찰방법", "업종", "낙찰하한율", "공동"),
}
COMPACT_QUERIES = {k: tuple(re.sub(r"\s+", "", x).lower() for x in v) for k, v in QUERIES.items()}


@dataclass(frozen=True)
class Span:
    doc_index: int
    doc_type: str
    start: int
    end: int
    text: str


def split_spans(rec, size=440, overlap=100):
    spans = []
    for index, doc in enumerate(rec["docs"]):
        text = doc["text"]
        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            if end < len(text):
                boundaries = [text.rfind("\n\n", start + size // 2, end),
                              text.rfind("\n", start + size * 3 // 4, end)]
                boundary = max(boundaries)
                if boundary > start:
                    end = boundary
            lo, hi = start, end
            while lo < hi and text[lo].isspace():
                lo += 1
            while hi > lo and text[hi - 1].isspace():
                hi -= 1
            if hi > lo:
                spans.append(Span(index, doc["type"], lo, hi, text[lo:hi]))
            if end == len(text):
                break
            start = max(start + 1, end - overlap)
    return spans


def _compact(text):
    return re.sub(r"\s+", "", text).lower()


# These identify source roles, never legal compliance or an item label. In
# particular, permission, negation and obligation are all retrieval candidates.
_FIELD = re.compile(
    r"적용\s*계약법|업무\s*구분|계약\s*방법|입찰\s*(?:방법|방식|추정\s*가격)|"
    r"낙찰\s*(?:방법|하한율)|조달\s*방식|배정\s*예산(?:\s*금액)?|"
    r"사업\s*(?:예산|금액|기간)|예산\s*금액|기초\s*금액|추정\s*가격|"
    r"공고\s*(?:게시\s*일자|게시일|일자|일)|개찰\s*(?:예정\s*일자|일시)|"
    r"(?:제안서|입찰서)\s*(?:제출|접수)\s*(?:기한|마감|일시)|"
    r"(?:현장|사업|제안요청)\s*설명회\s*(?:일시|일자)|"
    r"지역\s*제한|업종\s*제한|면허\s*업종|세부\s*품명(?:\s*번호)?|"
    r"공동\s*(?:도급|수급|이행|계약)(?:\s*구성\s*방식)?")
_ASSIGN = re.compile(r"^\s*(?:[:：=|]|(?:은|는)(?:\s|$))\s*\S")
_PARTICIPANT = re.compile(
    r"입찰|참가|참여|자격|업체|제안사|구성원|공동수급|본점|영업소|"
    r"중소기업|소기업|소상공인|실적|업종|면허")
_QUALIFY = re.compile(
    r"갖춘|갖추|등록한|등록하여|보유한|보유하여|한\s*자(?:만|\s|$)|"
    r"(?:참가|참여|입찰)\s*(?:가능|불가)|"
    r"(?:제한|허용|불허|인정)(?:한다|합니다|하지|하며|할|하여|되는|된다|됩니다|한다는)|"
    r"제한\s*(?:없|하지)|(?:이상|이하|미만|초과)(?:으로|인|의|을|만|\s|$)|"
    r"(?:하여야|해야)\s*(?:한다|합니다)")
_DOCUMENT = re.compile(r"서류|자료|확약서|확인서|증명서|제안서|입찰서|실적|인증서")
_SUBMIT = re.compile(r"제출|발급|보유|작성|첨부|구비")
_MODAL = re.compile(
    r"의무|선택|필수|면제|불필요|불요|가능|필요|요구|하여야|해야|"
    r"(?:제출|발급|보유|작성)(?:한다|하지|할|하여|해야|하며|받아)|"
    r"[0-9]+\s*부(?:\s|$|[.,])")
_SPEC = re.compile(r"모델(?:명)?|제조사|상표|브랜드|규격|제품|물품")
_SPEC_ACTION = re.compile(r"납품|구매|공급|동등|동급|이상|이하|대체|지정|허용|불허")
_CONDITION = re.compile(
    r"^\s*(?:[※*ㆍ·-]\s*)?(?:다만|단\s*[,，:：]|단서|예외|제외|그러나|"
    r"정정|변경|취소|철회|조건|부가(?:가치)?세|VAT|단위)|경우(?:에)?만|때(?:에)?만|"
    r"하지\s*않|필요\s*없|의무(?:가|는)?\s*없|의무(?:\s*사항)?(?:가|는|이)?\s*아니|"
    r"선택\s*(?:사항|이다)")
_HEADING = re.compile(
    r"^\s*(?:\d+(?:[.-]\d+)*[.)]\s*)?(?:입찰\s*참가\s*자격|참가\s*자격|"
    r"자격\s*요건|입찰\s*참가\s*조건|제출\s*서류|구비\s*서류|제출\s*목록|"
    r"사업\s*개요|공동\s*(?:수급|계약)|제품\s*규격|수행\s*조건|입찰\s*일정)\s*[:：]?\s*$")
_ROLES = ("field", "qualification", "submission", "specification")
_SOURCE_SIZE = 440
_SOURCE_OVERHEAD = 40


@dataclass(frozen=True)
class _Candidate:
    doc_index: int
    start: int
    end: int
    roles: tuple
    # Context is an atomic retrieval unit; it can span several evidence spans.
    context_start: int
    context_end: int


class _EvidenceSelection(list):
    """List-compatible selection with bounded, selection-local diagnostics."""
    def __init__(self, spans, diagnostics):
        super().__init__(spans)
        self.diagnostics = diagnostics


def _source_units(text):
    """Nonempty original lines. Never normalize away a value or polarity."""
    units = []
    for match in re.finditer(r"[^\r\n]+", text):
        lo, hi = match.span()
        while lo < hi and text[lo].isspace():
            lo += 1
        while hi > lo and text[hi - 1].isspace():
            hi -= 1
        if hi > lo:
            units.append((lo, hi))
    return units


def _roles(text, heading="", table_header=""):
    roles = []
    if (any(_ASSIGN.search(text[m.end():]) for m in _FIELD.finditer(text))
            or (table_header and _FIELD.search(table_header) and "|" in text)):
        roles.append("field")
    qualified = _PARTICIPANT.search(text + " " + heading)
    if qualified and _QUALIFY.search(text):
        roles.append("qualification")
    if (_DOCUMENT.search(text) and _SUBMIT.search(text + " " + heading)
            and (_MODAL.search(text) or heading and re.search(r"제출|구비", heading))):
        roles.append("submission")
    if _SPEC.search(text) and _SPEC_ACTION.search(text):
        # A lexical list lacks a value, predicate, or alternative permission.
        if (_MODAL.search(text) or re.search(r"(?:모델|규격|제품|물품)\s*[:：]|납품한다|동등\s*(?:이상|제품)|대체\s*(?:가능|불가)", text)):
            roles.append("specification")
    return tuple(roles)


def _merge_ranges(ranges, text=None):
    merged = []
    for lo, hi in sorted(ranges):
        adjacent_whitespace = (merged and text is not None and lo > merged[-1][1]
                               and text[merged[-1][1]:lo].isspace()
                               and _range_cost([(merged[-1][0], hi)])
                               <= _range_cost([merged[-1], (lo, hi)]))
        if merged and (lo <= merged[-1][1] or adjacent_whitespace):
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    return merged


def _range_cost(ranges):
    # Upper bound before whitespace trimming; includes the existing S header
    # allowance. Diagnostics have separately bounded size, as existing metadata.
    return sum(hi - lo + _SOURCE_OVERHEAD * ((hi - lo + _SOURCE_SIZE - 1) // _SOURCE_SIZE)
               for lo, hi in ranges)


class NoticeIndex:
    def __init__(self, rec, overlap=100):
        self.rec = rec
        self.spans = split_spans(rec, overlap=overlap)
        self.compact = [_compact(s.text) for s in self.spans]
        vocab = set(q for qs in COMPACT_QUERIES.values() for q in qs)
        self.counts = [{q: text.count(q) for q in vocab if q in text} for text in self.compact]
        df = Counter(q for row in self.counts for q in row)
        self.idf = {q: math.log(1 + (len(self.spans) - n + .5) / (n + .5)) for q, n in df.items()}
        self.average_length = sum(len(s.text) for s in self.spans) / max(1, len(self.spans))
        self.ranked = {k: self.rank(k) for k in QUERIES}
        self._operative_data = None  # Lazy: old retrieval/head do no extra scanning.

    def rank(self, item):
        out = []
        for i, (span, counts, compact) in enumerate(zip(self.spans, self.counts, self.compact)):
            score = 0.
            for term in COMPACT_QUERIES[item]:
                tf = counts.get(term, 0)
                if tf:
                    score += self.idf[term] * tf * 2.2 / (tf + 1.2 * (.25 + .75 * len(span.text) / self.average_length))
            if item == 9:
                # Alphanumeric model references in specifications; no external brand list.
                refs = re.findall(r"\b(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{4,}\b", span.text)
                score += min(4, len(refs)) * (1.4 if span.doc_type != "공고문" else .2)
            if score:
                if item != 9 and span.doc_type == "공고문":
                    score *= 1.2
                if item == 9 and span.doc_type in {"규격서", "과업지시서"}:
                    score *= 1.4
                out.append((i, score))
        return sorted(out, key=lambda row: (-row[1], row[0]))

    def select(self, char_budget, items=tuple(range(1, 25)), mode="retrieval", *, priority_ranges=()):
        """Select source spans, charging their text plus 40 characters per span.

        evidence_first allocates shared source roles across documents before
        background. It does not infer item labels or use item-frequency scores.
        """
        if char_budget < 440:
            raise ValueError("Document budget is too small")
        if mode == "evidence_first":
            return self._select_evidence_first(char_budget, priority_ranges=priority_ranges)
        selected, used = set(), 0

        def add(i):
            nonlocal used
            cost = len(self.spans[i].text) + 40
            if i not in selected and used + cost <= char_budget:
                selected.add(i)
                used += cost

        if mode == "head":
            for i in range(len(self.spans)):
                add(i)
        else:
            # Preserve document introductions including attachments, then cover each item.
            seen_docs = set()
            for i, s in enumerate(self.spans):
                if s.doc_index not in seen_docs:
                    add(i)
                    seen_docs.add(s.doc_index)
            for depth in range(3):
                for item in items:
                    ranking = self.ranked[item]
                    if len(ranking) > depth:
                        add(ranking[depth][0])
            # Fill with the strongest remaining chunks; max over per-item normalized scores.
            priority = {}
            for item in items:
                ranking = self.ranked[item]
                top = ranking[0][1] if ranking else 1
                for i, score in ranking:
                    priority[i] = max(priority.get(i, 0), score / top)
            for i in sorted(priority, key=lambda i: (-priority[i], i)):
                add(i)
            for i in range(len(self.spans)):
                add(i)
        # Source order avoids decontextualizing clauses; IDs are only local span references.
        return [self.spans[i] for i in sorted(selected)]

    def _operative_candidates(self):
        if self._operative_data is not None:
            return self._operative_data
        units_by_doc, candidates = [], []
        for di, doc in enumerate(self.rec["docs"]):
            text = doc["text"]
            units = _source_units(text)
            units_by_doc.append(units)
            conditional = [bool(_CONDITION.search(text[slice(*unit)])) for unit in units]
            condition_starts = list(range(len(units)))
            condition_ends = list(range(len(units)))
            for i in range(1, len(units)):
                if conditional[i] and conditional[i-1]:
                    condition_starts[i] = condition_starts[i-1]
            for i in range(len(units)-2, -1, -1):
                if conditional[i] and conditional[i+1]:
                    condition_ends[i] = condition_ends[i+1]
            heading_index = None
            for i, (lo, hi) in enumerate(units):
                value = text[lo:hi]
                if _HEADING.fullmatch(value):
                    heading_index = i
                    continue
                # Carry a heading only through its immediately adjacent body.
                heading = (text[slice(*units[heading_index])]
                           if heading_index is not None and i == heading_index + 1 else "")
                previous = text[slice(*units[i-1])] if i else ""
                table_header = previous if "|" in previous and "|" in value else ""
                roles = _roles(value, heading, table_header)
                if not roles:
                    continue
                first = i - 1 if i and (heading or table_header) else i
                if i and conditional[i-1]:
                    first = min(first, condition_starts[i-1])
                last = condition_ends[i+1] if i + 1 < len(units) and conditional[i+1] else i
                # Retain adjacent provisos/negations as a bundle, without
                # silently truncating them when the character budget is small.
                candidates.append(_Candidate(di, lo, hi, roles, units[first][0], units[last][1]))
        # Same text under another heading or in another document is not proof
        # of the same legal scope. Deduplicate only the same original address,
        # never equal text at another occurrence, even within one document.
        groups, keys = [], {}
        for candidate in candidates:
            key = (candidate.doc_index, candidate.roles,
                   candidate.context_start, candidate.context_end)
            if key in keys:
                groups[keys[key]].append(candidate)
            else:
                keys[key] = len(groups)
                groups.append([candidate])
        self._operative_data = units_by_doc, groups
        return self._operative_data

    def _select_evidence_first(self, char_budget, *, priority_ranges=()):
        units_by_doc, groups = self._operative_candidates()
        ranges, used = {}, 0

        def add(di, lo, hi):
            nonlocal used
            old = ranges.get(di, [])
            # Adjacent source lines may be separated only by whitespace. Keep
            # that exact whitespace and share S headers instead of paying one
            # header per short line. Never bridge an omitted word or condition.
            merged = _merge_ranges([*old, (lo, hi)], self.rec['docs'][di]['text'])
            cost = used - _range_cost(old) + _range_cost(merged)
            if cost > char_budget:
                return False
            ranges[di], used = merged, cost
            return True

        # A bounded portion can be reserved for source-grounded comparisons.
        # Preserve whole operative bundles, including adjacent exceptions.
        priority_limit = min(2400, char_budget // 4)
        for di, lo, hi in priority_ranges:
            if not (0 <= di < len(self.rec['docs']) and 0 <= lo < hi <= len(self.rec['docs'][di]['text'])):
                raise ValueError('Invalid priority source range')
            for group in groups:
                for c in group:
                    if c.doc_index == di and c.context_start < hi and c.context_end > lo:
                        lo, hi = min(lo, c.context_start), max(hi, c.context_end)
            if used + _range_cost([(lo, hi)]) <= priority_limit:
                add(di, lo, hi)

        # Round-robin roles and documents, with no frequency/label scoring.
        # A document's tenth candidate does not precede every other document's
        # first candidate. Introductions have no reserved slot ahead of evidence.
        role_queues = []
        for role in _ROLES:
            by_doc = {}
            for gi, group in enumerate(groups):
                c = group[0]
                if role in c.roles:
                    by_doc.setdefault(c.doc_index, deque()).append(gi)
            documents, queue = deque(by_doc), deque()
            while documents:
                di = documents.popleft()
                queue.append(by_doc[di].popleft())
                if by_doc[di]:
                    documents.append(di)
            role_queues.append(queue)
        order, seen = [], set()
        while any(role_queues):
            for queue in role_queues:
                while queue and queue[0] in seen:
                    queue.popleft()
                if queue:
                    gi = queue.popleft()
                    order.append(gi)
                    seen.add(gi)
        for gi in order:
            c = groups[gi][0]
            add(c.doc_index, c.context_start, c.context_end)

        # Background is considered only after every candidate had an allocation
        # opportunity. Never expose a fragment of an unselected candidate bundle
        # through background filling. Repeated lines at other addresses remain
        # candidates: table headers and values can describe different products.
        protected = {}
        for group in groups:
            for c in group:
                protected.setdefault(c.doc_index, []).append((c.context_start, c.context_end))
        protected = {di: _merge_ranges(rs) for di, rs in protected.items()}
        ends = {di: [hi for lo, hi in rs] for di, rs in protected.items()}
        backgrounds = []
        for di, units in enumerate(units_by_doc):
            queue = deque()
            for lo, hi in units:
                j = bisect_left(ends.get(di, []), lo + 1)
                intervals = protected.get(di, [])
                if j < len(intervals) and intervals[j][0] < hi:
                    continue
                queue.extend((di, start, min(start + _SOURCE_SIZE, hi))
                             for start in range(lo, hi, _SOURCE_SIZE))
            if queue:
                backgrounds.append(queue)
        while any(backgrounds):
            for queue in backgrounds:
                if queue:
                    add(*queue.popleft())
        spans = []
        for di, intervals in sorted(ranges.items()):
            doc = self.rec["docs"][di]
            for lo, hi in intervals:
                for start in range(lo, hi, _SOURCE_SIZE):
                    end = min(start + _SOURCE_SIZE, hi)
                    while start < end and doc["text"][start].isspace():
                        start += 1
                    while end > start and doc["text"][end-1].isspace():
                        end -= 1
                    if end > start:
                        spans.append(Span(di, doc["type"], start, end, doc["text"][start:end]))
        represented, by_role, unshown = 0, {role: {"candidates": 0, "unshown": 0} for role in _ROLES}, []
        for group in groups:
            c = group[0]
            shown = any(lo <= c.context_start and hi >= c.context_end
                        for lo, hi in ranges.get(c.doc_index, []))
            represented += int(shown)
            for role in c.roles:
                by_role[role]["candidates"] += 1
                by_role[role]["unshown"] += int(not shown)
            if not shown:
                unshown.append({"doc_index": c.doc_index, "start": c.start, "end": c.end,
                                "context_start": c.context_start, "context_end": c.context_end,
                                "roles": list(c.roles)})
        diagnostics = {"kind": "source_candidates_not_legal_findings", "mode": "evidence_first",
                       "detected_occurrences": sum(map(len, groups)), "unique_candidates": len(groups),
                       "exact_duplicate_occurrences": sum(len(g)-1 for g in groups),
                       "deduplication_scope": "identical_original_address_and_roles_only",
                       "represented_candidates": represented, "unshown_candidates": len(unshown),
                       "by_role": by_role, "unshown_examples": unshown[:8],
                       "unshown_examples_truncated": len(unshown) > 8,
                       "budget_including_span_allowance": char_budget,
                       "charged_characters": used,
                       "note": "Unshown candidates and unrecognized wording cannot prove legal absence."}
        return _EvidenceSelection(spans, diagnostics)

    def coverage(self, selected):
        by_doc = {}
        for span in selected:
            by_doc.setdefault(span.doc_index, []).append((span.start, span.end))
        covered = 0
        merged = {}
        for i, ranges in by_doc.items():
            chunks = []
            for lo, hi in sorted(ranges):
                if chunks and lo <= chunks[-1][1]:
                    chunks[-1][1] = max(chunks[-1][1], hi)
                else:
                    chunks.append([lo, hi])
            covered += sum(hi - lo for lo, hi in chunks)
            merged[i] = chunks
        total = sum(len(d["text"]) for d in self.rec["docs"])
        result = {"total_chars": total, "covered_chars": covered,
                  "fraction": round(covered / max(total, 1), 4), "ranges": merged}
        if isinstance(selected, _EvidenceSelection):
            result["operative_candidates"] = selected.diagnostics
        return result

    def presence_inventory(self, selected):
        selected_compact = [_compact(s.text) for s in selected]
        # These are retrieval diagnostics, not assertions of legal compliance.
        return {str(k): {"matched_spans": len(self.ranked[k]),
                        "shown_matching_spans": sum(any(q in text for q in COMPACT_QUERIES[k]) for text in selected_compact)}
                for k in (10, 11, 16, 18, 20)}
