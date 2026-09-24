"""Bounded reference retrieval, not a governing-law or violation classifier.

Only the supplied in-memory law texts and item table are used. Excerpts are
complete structural units, with source offsets; no generated legal thresholds.
The legacy Knowledge.legal_context path is deliberately independent of this one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from .law_declarations import clean_scopes, declarations


_SCOPE_NAMES = {"national": "국가", "local": "지방", "unknown": "미확정", "conflict": "충돌"}


def resolve_scope(rec):
    """Keep metadata and explicit operative declarations as separate evidence.

    A citation for eligibility, guarantees, analogical application, or an example
    is not itself an operative governing-law declaration. Unrecognized wording
    remains unknown; the evidence is not an assertion of legal applicability.
    """
    meta = rec.get("meta")
    raw = meta.get("적용계약법") if isinstance(meta, dict) else None
    state = ("missing" if not isinstance(meta, dict) or "적용계약법" not in meta
             else "null" if raw is None else "present")
    # Metadata is an explicit field, but arbitrary prose in it is not a clean
    # declaration (e.g. '국가계약법 미적용'). Retain unrecognized values verbatim.
    scopes = clean_scopes(raw)
    signals, ignored = [], []
    if scopes:
        signals.append({"source": "meta.적용계약법", "text": raw, "scopes": scopes,
                        "kind": "affirmed"})
    elif state == "present":
        state = "unrecognized"
    for doc_index, doc in enumerate(rec.get("docs") or []):
        for observation in declarations(doc.get("text") or ""):
            signal = {"source": "document", "doc_index": doc_index, "doc_type": doc.get('type'),
                      "doc_id": doc.get("doc_id"), **observation}
            (ignored if observation['ignored_reason'] else signals).append(signal)
    all_found = {scope for signal in signals if signal["kind"] == "affirmed" for scope in signal["scopes"]}
    all_excluded = {scope for signal in signals if signal["kind"] == "excluded" for scope in signal["scopes"]}
    notice = [signal for signal in signals if signal.get('doc_type') == '공고문']
    notice_affirmed = any(signal['kind'] == 'affirmed' for signal in notice)
    notice_unresolved = any(signal['kind'] == 'unresolved' for signal in notice)
    # Official notice priority concerns an actual governing-law declaration,
    # not ordinary law citations. Preserve every other signal for inspection.
    selected = notice if notice_affirmed or notice_unresolved else signals
    found = {scope for signal in selected if signal["kind"] == "affirmed" for scope in signal["scopes"]}
    excluded = {scope for signal in selected if signal["kind"] == "excluded" for scope in signal["scopes"]}
    unresolved = any(signal['kind'] == 'unresolved' for signal in selected)
    status = ("conflict" if len(found) > 1 or found.intersection(excluded)
              else "unknown" if unresolved
              else next(iter(found)) if found else "unknown")
    container_state = ("missing" if "meta" not in rec else "null" if meta is None
                       else "object" if isinstance(meta, dict) else "invalid")
    return {"status": status, "metadata_state": state, "metadata_value": raw,
            "metadata_container_state": container_state,
            "excluded_scopes": sorted(excluded),
            "signals": signals, "ignored_declarations": ignored, "alternatives": ["national", "local"]
            if status in ("unknown", "conflict") else [status],
            "source_conflict": len(all_found) > 1 or bool(all_found.intersection(all_excluded)),
            "effective_source": ("notice_declaration" if notice_affirmed else "unresolved_notice_declaration"
                                 if notice_unresolved else "available_declarations_and_metadata"),
            "selected_signal_indices": [i for i, signal in enumerate(signals) if signal in selected],
            "legal_applicability_determined": False}


def applicable_law(rec):
    """One shared law value for CPU rules; never rewrite original registration."""
    return {'national': '국가계약법', 'local': '지방계약법'}.get(resolve_scope(rec)['status'])


@dataclass(frozen=True)
class Fragment:
    alias: str
    reference: str
    spans: tuple


def _article_span(text, article):
    match = re.search(r"^\s*" + re.escape(article) + r"\(", text, re.M)
    if not match:
        return None
    # Do not include subsequent articles, amendments, annexes, or another chapter.
    following = re.search(r"^\s*(?:제\d+조(?:의\d+)?\(|부칙(?:\s|[<〈(])|"
                          r"\[별(?:표|지)|제\d+장\s)", text[match.end():], re.M)
    end = match.end() + following.start() if following else len(text)
    return match.start(), end


def _article(laws, alias, article):
    span = _article_span(laws.get(alias, ""), article)
    return Fragment(alias, article, (span,)) if span else None


def _between(laws, alias, reference, start_pattern, end_pattern):
    text = laws.get(alias, "")
    start = re.search(start_pattern, text, re.M)
    if not start:
        return None
    end = re.search(end_pattern, text[start.end():], re.M)
    if not end:
        return None  # A broken/missing closing boundary is not a complete unit.
    return Fragment(alias, reference, ((start.start(), start.end() + end.start()),))


def _national_joint(laws):
    full = _article(laws, "공동계약", "제9조")
    if not full:
        return None
    text = laws[full.alias]
    start, end = full.spans[0]
    body = text[start:end]
    heading = re.match(r"\s*제9조\([^\n)]*\)", body)
    paragraph = re.search(r"^\s*⑤\s", body, re.M)
    # Keep the related regional exceptions (6) and qualification (7) with (5).
    if not heading or not paragraph or not all(re.search(r"^\s*" + c, body, re.M) for c in "⑥⑦"):
        return None
    return Fragment(full.alias, "제9조 제5항~제7항", (
        (start + heading.start(), start + heading.end()), (start + paragraph.start(), end)))


def _local_joint(laws):
    part = _between(laws, "지방집행기준", "제6장 공동계약 / 나. 구성원 수 등 2)~4) 본문",
                    r"^나\.\s*구성원 수 등\s*$", r"^\(예시\)|^5\)\s*주계약자 관리방식")
    if not part:
        return None
    text = laws[part.alias]
    start, end = part.spans[0]
    body = text[start:end]
    second = re.search(r"^2\)\s*구성원별 계약참여 최소지분율", body, re.M)
    if not second or not all(re.search(r"^" + n + r"\)", body, re.M) for n in ("3", "4")):
        return None
    heading_end = text.find("\n", start)
    return Fragment(part.alias, part.reference, ((start, heading_end), (start + second.start(), end)))


def _sw_annex(laws):
    annex = _between(laws, "SW지침", "별표1 (제2조·제3조 관련; 테두리선 제외)",
                     r"^\[별표\s*1\][^\n]*", r"^\[별표\s*2\]")
    if not annex:
        return None
    text = laws[annex.alias]
    start, end = annex.spans[0]
    spans, cursor, run_start = [], start, start
    for line in text[start:end].splitlines(keepends=True):
        # Only empty box-drawing borders are decorative. Keep every table cell,
        # wrapped qualification, heading and numeric band at its source offset.
        if re.fullmatch(r"[\s\u2500-\u257f]+", line):
            if run_start < cursor:
                spans.append((run_start, cursor))
            run_start = cursor + len(line)
        cursor += len(line)
    if run_start < end:
        spans.append((run_start, end))
    return Fragment(annex.alias, annex.reference, tuple(spans))


def _normalize(text):
    # Whitespace only; retain all words, numbers, table cells and amendment notes.
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip()
                     for line in text.splitlines() if line.strip())


def _fragment_text(fragment, laws, aliases):
    body = "\n".join(_normalize(laws[fragment.alias][start:end]) for start, end in fragment.spans)
    return f"[{fragment.alias} / {fragment.reference}]\n{body}"


def _table_articles(table, items, scope):
    """Read article links from the official table, including its spacing variants."""
    field = "국가계약법" if scope == "national" else "지방계약법"
    for item in items:
        linked = re.sub(r"\s+", "", table.get(f"v{item}", {}).get(field, ""))
        pattern = re.compile(
            r"(국가계약법시행령|국가계약법시행규칙|지방계약법시행령|지방계약법시행규칙|"
            r"중소기업제품구매촉진및판로지원에관한법률시행령|"
            r"중소기업제품구매촉진및판로지원에관한법률)"
            r"((?:제\d+조(?:의\d+)?(?:제\d+항)?[,，]?)+)"
        )
        names = {"국가계약법시행령": "국가계약법 시행령", "국가계약법시행규칙": "국가계약법 시행규칙",
                 "지방계약법시행령": "지방계약법 시행령", "지방계약법시행규칙": "지방계약법 시행규칙",
                 "중소기업제품구매촉진및판로지원에관한법률시행령": "판로지원법 시행령",
                 "중소기업제품구매촉진및판로지원에관한법률": "판로지원법"}
        for match in pattern.finditer(linked):
            for article in re.findall(r"제\d+조(?:의\d+)?", match[2]):
                yield item, names[match[1]], article


def build_legal_context(rec, items, max_chars, laws, table, aliases):
    """Return bounded text plus provenance and omissions (diagnostics are unbounded).

    Unknown/conflicting scope alternatives are packed together, never one alone.
    Mandatory scope/coverage notes and separators count towards max_chars. If even
    a note cannot fit, text is empty and the packet still explains the omission.
    """
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 0:
        raise ValueError("max_chars must be a nonnegative integer")
    items = sorted(set(items))
    if any(isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 24 for k in items):
        raise ValueError("items must contain integers from 1 through 24")
    scope = resolve_scope(rec)
    alternatives = scope["alternatives"]
    ambiguous = len(alternatives) == 2
    groups, missing, outside = [], [], []

    def add(key, linked_items, fragments, priority, required=True):
        if not fragments or any(fragment is None for fragment in fragments):
            missing.append({"group": key, "items": sorted(linked_items),
                            "reason": "required_source_or_structure_missing"})
            return
        national = any(f.alias.startswith("국가계약법") or f.alias in ("공동계약", "집행기준")
                       for f in fragments)
        local = any(f.alias.startswith("지방") for f in fragments)
        groups.append({"id": key, "items": sorted(linked_items), "fragments": fragments,
                       "priority": priority,
                       "required_alternatives": required and ambiguous and national and local})

    # Direct linked units precede general articles regardless of other item queries.
    # Do not gate v20/v21 on notice keywords: absence detection and full-scope
    # requests must still be able to retrieve their defining law.
    if 21 in items:
        add("joint_share", {21}, [_national_joint(laws) if s == "national" else _local_joint(laws)
                                  for s in alternatives], 0)
    if 20 in items:
        annex = _sw_annex(laws)
        add("sw_floor", {20}, [_article(laws, "SW지침", "제2조"), annex], 1, False)
        add("sw_calculation", {20}, [_article(laws, "SW지침", "제3조")], 2, False)
        # Exemption procedures remain distinct complete units, not invented rules.
        add("sw_exemptions", {20}, [_article(laws, "SW지침", "제4조"),
                                    _article(laws, "SW지침", "제5조")], 5, False)
        outside.append({"items": [20], "reference": "소프트웨어진흥법 및 SW지침 별표2·별표3",
                        "reason": "not_expanded_by_this_bounded_retriever"})
    if 23 in items and "local" in alternatives:
        add("local_briefing", {23}, [_between(laws, "지방낙찰기준",
            "제7장 제3절 2. 제안요청서의 교부 다. (각호 포함)",
            r"^다\. 계약담당자는 계약의 성질.*제안요청서 설명은 제안서 제출마감일",
            r"^라\. 계약담당자는 제안요청서에")], 3, False)

    # The table's unnumbered guidance links require structural source anchors.
    specific = {2, 4, 9, 19}.intersection(items)
    if specific:
        branches = []
        if "national" in alternatives:
            if specific.intersection({4, 9}):
                branches.append(_article(laws, "집행기준", "제5조"))
            if 19 in specific:
                branches.append(_article(laws, "집행기준", "제5조의3"))
        if "local" in alternatives:
            branches.append(_between(laws, "지방집행기준", "제1장 제1절 7. 계약담당자 주의사항",
                                     r"^7\. 계약담당자 주의사항\s*$", r"^8\. 계약정보의 공개"))
        if branches:
            add("contract_guidance", specific, branches, 3)
    if 3 in items and "national" in alternatives:
        # Local counterpart is the rule/decree pair below, not national guidance.
        add("national_performance", {3}, [_article(laws, "집행기준", "제5조")], 4, False)
    if {6, 7, 8}.intersection(items) and "local" in alternatives:
        add("local_small_quotes", {6, 7, 8}.intersection(items), [_between(
            laws, "지방집행기준", "제5장 제3절 1. 나. 수의계약 요령 1)~7)",
            r"^나\. 수의계약 요령\s*$", r"^8\) 계약담당자는|^8\) 수의계약 안내공고")], 4, False)
    if 5 in items and "national" in alternatives:
        outside.append({"items": [5], "reference": "고시금액",
                        "reason": "institution_specific_amount_notice_not_expanded"})
    if {10, 11, 12}.intersection(items):
        outside.append({"items": sorted({10, 11, 12}.intersection(items)), "reference": "경쟁제품 고시",
                        "reason": "use_existing_product_facts_separately"})

    # Group corresponding national/local linked articles by role, not shared
    # keywords from the union of items. Common SME law is deduplicated.
    refs = {}
    for branch in alternatives:
        for item, alias, article in _table_articles(table, items, branch):
            role = (alias.replace("국가계약법", "계약법").replace("지방계약법", "계약법"),
                    {"제12조": "qualification", "제13조": "qualification",
                     "제21조": "restriction", "제20조": "restriction"}.get(article, article)
                    if "시행령" in alias and "계약법" in alias else article)
            if alias == "판로지원법 시행령" and article in ("제2조의2", "제2조의3"):
                # The official table explicitly links the preference and its
                # exception; never spend the remaining budget on only one.
                role = (alias, "제2조의2·제2조의3")
            entry = refs.setdefault(role, {"items": set(), "refs": []})
            entry["items"].add(item)
            if (alias, article) not in entry["refs"]:
                entry["refs"].append((alias, article))
    for role, entry in refs.items():
        # Spend the budget on an existing same-law dependency bundle before
        # independent table articles. Jurisdiction alternatives alone are not
        # dependencies; retain their existing rank and atomic selection.
        dependent = len(entry["refs"]) > 1 and len({a for a, _ in entry["refs"]}) == 1
        priority = 2 if dependent else 3
        add("table:" + ":".join(role), entry["items"],
            [_article(laws, a, r) for a, r in entry["refs"]], priority)

    label = _SCOPE_NAMES[scope["status"]]
    note = (f"[적용법:{label}; 국가·지방 대안, 적용범위 확인 필요]" if ambiguous
            else f"[적용법:{label}; 명시 근거에 따른 참고 범위]")
    note += "\n[법령 참고발췌; 생략 가능·위반판정 아님]"
    # Always reserve the same coverage note so adding it cannot break the cap.
    coverage = "\n[일부 법령 생략됨]"
    selected, omitted, blocks, emitted = [], [], [], set()
    available = max_chars - len(note) - len(coverage)
    for group in sorted(groups, key=lambda g: (g["priority"], g["id"])):
        fragments = [f for f in group["fragments"] if f not in emitted]
        block = "\n\n".join(_fragment_text(f, laws, aliases) for f in fragments)
        extra = len(block) + (2 if block else 0)
        details = {"group": group["id"], "items": group["items"],
                   "paired_alternatives": group["required_alternatives"],
                   "sources": [{"alias": f.alias, "file": aliases.get(f.alias, f.alias),
                                "reference": f.reference, "spans": [list(span) for span in f.spans]}
                               for f in group["fragments"]]}
        if extra <= available:
            selected.append(details)
            if block:
                blocks.append(block)
                available -= extra
                emitted.update(fragments)
        else:
            omitted.append({**details, "reason": "atomic_group_exceeds_remaining_budget",
                            "required_chars": extra})
    incomplete = bool(omitted or missing or outside)
    text = note + (coverage if incomplete else "")
    if blocks:
        text += "\n\n" + "\n\n".join(blocks)
    if len(text) > max_chars:
        text = f"[적용법:{label}; 문맥 생략]"
        if len(text) > max_chars:
            text = ""
    return {"text": text, "max_chars": max_chars, "used_chars": len(text), "scope": scope,
            "items": items, "selected": selected, "omitted": omitted, "missing": missing,
            "unexpanded_references": outside, "incomplete": incomplete or not bool(text),
            "source_kind": "supplied_law_and_item_table_only", "version": "legal_context_v2"}
