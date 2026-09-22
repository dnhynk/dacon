"""Source-grounded qualification and software facts for independent gold review.

This module extracts observable premises for v10-v18 and v20.  It deliberately
does *not* turn those premises into competition labels.  Its inputs are limited
to an organizer record and the organizer-supplied competitive-product catalog.
It has no dependency on the submission runtime, saved model responses, or
existing predictions.

The extractor is intentionally conservative about three distinctions that are
easy to erase in a label-first pipeline:

* a direct-production certificate tied to this bid object versus a generic law
  reference, a document-list entry, or a post-award obligation;
* cumulative qualification requirements versus explicit alternative paths,
  including the timing and legal basis of enterprise-size exceptions; and
* software that is itself supplied/developed/operated versus software words in
  security boilerplate, inventories, training, or incidental tool use.

Every document-derived fact retains a verbatim absolute span, document identity,
and SHA-256 hashes.  Heuristic role names are routing aids for human/LLM review,
not findings of compliance or violation; unresolved relations stay explicit.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import pathlib
import re
from collections import Counter
from typing import Any, Iterable, Iterator, Mapping, Sequence


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_PATH = (
    ROOT
    / "data_open"
    / "data"
    / "법령패키지"
    / "중기부고시"
    / "중기부고시_경쟁제품_세부품명.csv"
)
SCHEMA_VERSION = "dacon.independent.qualification_facts.v4"
TARGET_ITEMS = tuple([f"v{number}" for number in range(10, 19)] + ["v20"])

CATALOG_COLUMNS = (
    "제품명번호",
    "제품명",
    "세부품명번호",
    "세부품명",
    "특이사항",
    "공사용자재직접구매",
)

META_FIELDS: dict[str, str] = {
    "contract_law": "적용계약법",
    "work_type": "업무구분",
    "contract_method": "계약방법",
    "award_method": "낙찰방법",
    "budget_won": "배정예산금액",
    "estimated_price_won": "입찰추정가격",
    "information_project": "정보화사업여부",
    "catalog_codes": "세부품명번호목록",
    "industry_codes": "면허업종제한목록",
    "industry_restricted": "업종제한여부",
    "article_summary": "조항호내용",
    "posted_date": "공고게시일자",
    "opening_date": "개찰예정일자",
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_object(value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(rendered)


def _relative_path(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_jsonl_gz(path: pathlib.Path | str) -> Iterator[dict[str, Any]]:
    source = pathlib.Path(path)
    with gzip.open(source, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict) or not value.get("id"):
                raise ValueError(f"{source}:{line_number}: invalid organizer record")
            yield value


class CatalogReference:
    """Small validated view of the supplied competitive-product catalog.

    The full condition parser lives in ``catalog_facts.py``.  This view keeps
    only immutable catalog identity and exact source coordinates so qualification
    facts can be emitted without importing any competition runtime.
    """

    def __init__(
        self,
        path: pathlib.Path,
        rows: Mapping[str, Mapping[str, Any]],
        *,
        sha256: str,
        total_rows: int,
    ):
        self.path = path
        self.rows = {str(key): dict(value) for key, value in rows.items()}
        self.sha256 = sha256
        self.total_rows = int(total_rows)
        self.by_exact_name: dict[str, list[str]] = {}
        for code, row in self.rows.items():
            for field in ("detail_name", "product_name"):
                name = str(row.get(field) or "").strip()
                if len(name) >= 4:
                    self.by_exact_name.setdefault(name, []).append(code)
        exact_names = sorted(self.by_exact_name, key=lambda value: (-len(value), value))
        self.exact_name_pattern = (
            re.compile("|".join(re.escape(name) for name in exact_names))
            if exact_names
            else re.compile(r"(?!x)x")
        )

    @classmethod
    def load(
        cls, path: pathlib.Path | str = DEFAULT_CATALOG_PATH
    ) -> "CatalogReference":
        source = pathlib.Path(path)
        raw = source.read_bytes()
        rows: dict[str, dict[str, Any]] = {}
        total_rows = 0
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = set(CATALOG_COLUMNS) - set(reader.fieldnames or ())
            if missing:
                raise ValueError(f"catalog missing columns: {sorted(missing)}")
            for row_number, raw_row in enumerate(reader, 2):
                total_rows += 1
                code = str(raw_row.get("세부품명번호") or "").strip()
                if not code:
                    continue
                if not re.fullmatch(r"\d{10}", code):
                    raise ValueError(f"catalog row {row_number}: invalid code {code!r}")
                if code in rows:
                    raise ValueError(f"catalog row {row_number}: duplicate code {code}")
                note = str(raw_row.get("특이사항") or "").strip()
                rows[code] = {
                    "code": code,
                    "product_number": str(raw_row.get("제품명번호") or "").strip(),
                    "product_name": str(raw_row.get("제품명") or "").strip(),
                    "detail_name": str(raw_row.get("세부품명") or "").strip(),
                    "special_note": note,
                    "special_note_sha256": sha256_text(note),
                    "construction_material_direct_purchase": str(
                        raw_row.get("공사용자재직접구매") or ""
                    ).strip(),
                    "source_coordinate": {
                        "source_kind": "catalog_csv",
                        "path": _relative_path(source),
                        "row_number": row_number,
                        "code_column": "세부품명번호",
                        "note_column": "특이사항",
                    },
                }
        return cls(
            source,
            rows,
            sha256=hashlib.sha256(raw).hexdigest(),
            total_rows=total_rows,
        )

    def get(self, code: str) -> dict[str, Any] | None:
        value = self.rows.get(str(code))
        return dict(value) if value is not None else None

    def __contains__(self, code: object) -> bool:
        return code in self.rows

    def __len__(self) -> int:
        return len(self.rows)

    def codes_for_exact_name(self, name: str) -> list[str]:
        return sorted(set(self.by_exact_name.get(name, ())))


def _documents(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for index, raw in enumerate(record.get("docs") or []):
        text = str(raw.get("text") or "")
        documents.append(
            {
                "doc_index": index,
                "doc_id": str(raw.get("doc_id") or f"D{index}"),
                "doc_type": str(raw.get("type") or "unknown"),
                "text": text,
                "sha256": sha256_text(text),
            }
        )
    return documents


def _span(
    document: Mapping[str, Any],
    start: int,
    end: int,
    *,
    matched_by: Iterable[str] = (),
) -> dict[str, Any]:
    text = str(document["text"])
    start = max(0, min(int(start), len(text)))
    end = max(start, min(int(end), len(text)))
    exact = text[start:end]
    return {
        "source_kind": "document",
        "doc_index": int(document["doc_index"]),
        "doc_id": str(document["doc_id"]),
        "doc_type": str(document["doc_type"]),
        "start": start,
        "end": end,
        "text": exact,
        "text_sha256": sha256_text(exact),
        "source_doc_sha256": str(document["sha256"]),
        "matched_by": sorted(set(str(item) for item in matched_by if item)),
    }


def _meta_span(field: str, value: Any, start: int, end: int) -> dict[str, Any]:
    rendered = str(value or "")
    start = max(0, min(start, len(rendered)))
    end = max(start, min(end, len(rendered)))
    exact = rendered[start:end]
    return {
        "source_kind": "meta",
        "path": f"meta.{field}",
        "start": start,
        "end": end,
        "text": exact,
        "text_sha256": sha256_text(exact),
        "source_value_sha256": sha256_text(rendered),
    }


def _fact(
    kind: str,
    support_items: Iterable[str],
    span: Mapping[str, Any],
    **values: Any,
) -> dict[str, Any]:
    location = (
        f"{span.get('doc_index')}:{span.get('start')}:{span.get('end')}"
        if span.get("source_kind") == "document"
        else f"{span.get('path')}:{span.get('start')}:{span.get('end')}"
    )
    result = {
        "fact_id": f"{kind}:{location}",
        "kind": kind,
        "support_items": sorted(set(support_items)),
        "span": dict(span),
    }
    result.update(values)
    return result


_BLANK_BREAK = re.compile(r"\n[ \t]*\n")
_HEADING_LINE = re.compile(
    r"(?m)^[ \t]*(?:\d{1,2}[.\)]|[IVXⅠ-Ⅹ]+[.\)]|[가-하][.\)]|[①-⑳]|"
    r"[■□◆◇○●▶▷※〇▣])\s*[^\n]{0,80}$"
)


def _window_bounds(
    text: str,
    start: int,
    end: int,
    *,
    before: int = 420,
    after: int = 780,
    max_chars: int = 1_600,
) -> tuple[int, int]:
    """Create a bounded, line-aligned source window that always contains a hit."""

    left = max(0, start - before)
    right = min(len(text), end + after)
    blank_left = text.rfind("\n\n", left, start)
    if blank_left >= 0:
        left = blank_left + 2
    else:
        line_left = text.rfind("\n", left, start)
        if line_left >= 0:
            previous_line = text.rfind("\n", left, line_left)
            previous_previous_line = (
                text.rfind("\n", left, previous_line) if previous_line >= 0 else -1
            )
            # Include up to two short preceding lines because one frequently carries
            # the semantic heading (for example, "제출서류" or "입찰참가자격").
            left = (
                previous_previous_line + 1
                if previous_previous_line >= 0
                else 0
            )
    blank_right = text.find("\n\n", end, right)
    if blank_right >= 0:
        right = blank_right
    else:
        line_right = text.find("\n", end, right)
        if line_right >= 0 and line_right - left >= 180:
            right = line_right
    if right - left > max_chars:
        left = max(left, start - max_chars // 3)
        right = min(right, left + max_chars)
        if right < end:
            right = end
            left = max(0, right - max_chars)
    return left, right


def _dedupe_facts(facts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: dict[tuple[str, str, int, int], dict[str, Any]] = {}
    for fact in facts:
        span = fact["span"]
        source_id = str(span.get("doc_index", span.get("path", "")))
        key = (fact["kind"], source_id, int(span["start"]), int(span["end"]))
        if key not in chosen:
            chosen[key] = fact
            continue
        previous = chosen[key]
        previous["support_items"] = sorted(
            set(previous.get("support_items", ())) | set(fact.get("support_items", ()))
        )
        previous["span"]["matched_by"] = sorted(
            set(previous["span"].get("matched_by", ()))
            | set(fact["span"].get("matched_by", ()))
        )
    return sorted(
        chosen.values(),
        key=lambda row: (
            0 if row["span"].get("source_kind") == "document" else 1,
            int(row["span"].get("doc_index", 10**9)),
            str(row["span"].get("path", "")),
            int(row["span"]["start"]),
            row["kind"],
        ),
    )


def _dedupe_exception_facts(
    facts: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    chosen: dict[tuple[str, int, int, int], dict[str, Any]] = {}
    for fact in facts:
        trigger = fact["trigger_span"]
        key = (
            str(fact["exception_type"]),
            int(trigger["doc_index"]),
            int(trigger["start"]),
            int(trigger["end"]),
        )
        chosen.setdefault(key, fact)
    return sorted(
        chosen.values(),
        key=lambda row: (
            row["trigger_span"]["doc_index"],
            row["trigger_span"]["start"],
            row["exception_type"],
        ),
    )


def _merge_hit_windows(
    document: Mapping[str, Any],
    matches: Iterable[tuple[int, int, str]],
    *,
    before: int = 360,
    after: int = 620,
    max_chars: int = 1_500,
) -> list[tuple[int, int, list[str]]]:
    text = str(document["text"])
    windows: list[tuple[int, int, set[str]]] = []
    for start, end, marker in sorted(matches):
        left, right = _window_bounds(
            text, start, end, before=before, after=after, max_chars=max_chars
        )
        if windows and left <= windows[-1][1] and right - windows[-1][0] <= max_chars:
            old_left, old_right, markers = windows[-1]
            windows[-1] = (old_left, max(old_right, right), markers | {marker})
        else:
            windows.append((left, right, {marker}))
    return [(left, right, sorted(markers)) for left, right, markers in windows]


STRICT_CODE_RE = re.compile(r"(?<!\d)\d{10}(?!\d)")
FRAGMENTED_CODE_RE = re.compile(
    r"(?<!\d)(?P<a>\d{1,9})(?:[ \t\u00a0]+)(?P<b>\d{1,9})(?!\d)"
)
CODE_CONTEXT_RE = re.compile(
    r"(?:세부\s*품명(?:\s*번호)?|물품\s*분류\s*번호|품명\s*번호|"
    r"G2B\s*(?:물품)?\s*분류\s*번호)",
    re.IGNORECASE,
)


def _code_mentions_in_document(
    document: Mapping[str, Any], catalog: CatalogReference
) -> list[dict[str, Any]]:
    text = str(document["text"])
    found: list[tuple[int, int, str, str]] = []
    occupied: set[tuple[int, int]] = set()
    for match in STRICT_CODE_RE.finditer(text):
        found.append((match.start(), match.end(), match.group(0), "exact_10_digit"))
        occupied.add((match.start(), match.end()))
    for match in FRAGMENTED_CODE_RE.finditer(text):
        normalized = match.group("a") + match.group("b")
        if len(normalized) != 10:
            continue
        if any(not (match.end() <= left or match.start() >= right) for left, right in occupied):
            continue
        local = text[max(0, match.start() - 80) : min(len(text), match.end() + 36)]
        if not CODE_CONTEXT_RE.search(local):
            continue
        found.append(
            (match.start(), match.end(), normalized, "whitespace_normalized_10_digit")
        )
    facts: list[dict[str, Any]] = []
    for start, end, code, form in sorted(found):
        local_start, local_end = _window_bounds(
            text, start, end, before=100, after=120, max_chars=360
        )
        local = text[local_start:local_end]
        explicit = bool(CODE_CONTEXT_RE.search(local))
        if code not in catalog and not explicit:
            # Keep price-like ten-digit numbers out of the product-code fact pool.
            continue
        if re.search(r"(?:금액|가격|예산|사업비)\s*[:：]?\s*$", text[max(0, start - 28) : start]):
            continue
        # Product labels and their values are often separated by a blank line in
        # the normalized organizer text.  ``_window_bounds`` intentionally stops
        # at that boundary for evidence compactness, so use a second, short
        # structural look-back for role classification only.
        role_context = text[max(0, start - 260) : local_end]
        role = _classify_product_code_role(role_context)
        catalog_row = catalog.get(code)
        facts.append(
            _fact(
                "bid_product_code_mention",
                ("v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17", "v18"),
                _span(document, start, end, matched_by=(form,)),
                code=code,
                mention_form=form,
                source_role=role,
                catalog_registered=catalog_row is not None,
                catalog_identity=catalog_row,
                context_span=_span(document, local_start, local_end, matched_by=("code_context",)),
            )
        )
    return facts


def _classify_product_code_role(context: str) -> str:
    folded = re.sub(r"\s+", " ", context)
    if re.search(r"직접\s*생산", folded):
        return "direct_production_clause"
    if re.search(r"입찰\s*참가\s*자격|참가\s*자격|등록한\s*자|등록한\s*업체", folded):
        return "bid_registration_requirement"
    if re.search(
        r"세부\s*품명|품명\s*번호|물품\s*분류\s*번호|규격|구매\s*내역|"
        r"납품|입찰에\s*부치는\s*사항|사업\s*내용",
        folded,
    ):
        return "procurement_object_candidate"
    if re.search(r"관련\s*법령|참고|예시|목록", folded):
        return "reference_or_list_candidate"
    return "unresolved_document_role"


def _meta_code_facts(
    record: Mapping[str, Any], catalog: CatalogReference
) -> list[dict[str, Any]]:
    meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
    field = "세부품명번호목록"
    value = str(meta.get(field) or "")
    facts: list[dict[str, Any]] = []
    for match in STRICT_CODE_RE.finditer(value):
        code = match.group(0)
        catalog_row = catalog.get(code)
        facts.append(
            _fact(
                "bid_product_code_mention",
                ("v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17", "v18"),
                _meta_span(field, value, match.start(), match.end()),
                code=code,
                mention_form="exact_10_digit",
                source_role="organizer_bid_product_metadata",
                catalog_registered=catalog_row is not None,
                catalog_identity=catalog_row,
            )
        )
    return facts


# These are deliberately broad *name-family candidates*, not catalog matches.
# Each family is anchored to actual detail names in the supplied catalog and is
# retained with ambiguity so an adjudicator can decide whether the mentioned
# work is the bid object and which exact ten-digit row, if any, applies.
CATALOG_NAME_FAMILIES: tuple[tuple[str, re.Pattern[str], tuple[str, ...]], ...] = (
    (
        "event_planning_or_operation_service",
        re.compile(
            r"(?:행사(?!\s*하(?:는|여|고|지|면|거나|도록))|공연|박람회|포럼|축제|전시회|회의)"
            r"[^.\n]{0,45}(?:기획|운영|대행)|"
            r"(?:기획|운영)[^.\n]{0,35}(?:행사|공연)[^.\n]{0,25}대행",
            re.IGNORECASE,
        ),
        ("8014190201", "8014198801", "8014198901", "8014199001"),
    ),
    (
        "information_system_development_or_maintenance_service",
        re.compile(
            r"(?:정보\s*시스템|전산\s*시스템|학습\s*분석\s*시스템|"
            r"경영\s*정보\s*시스템|소프트웨어|S\s*/?\s*W)"
            r"(?:(?!업무\s*공간)[^.\n]){0,55}"
            r"(?:개발|구축|도입|유지\s*관리|유지\s*보수|기술\s*지원)|"
            r"(?:개발|구축|유지\s*관리|유지\s*보수)[^.\n]{0,45}"
            r"(?:정보\s*시스템|전산\s*시스템|소프트웨어|S\s*/?\s*W)",
            re.IGNORECASE,
        ),
        ("8111159801", "8111159901", "8111189901", "8111229901"),
    ),
)
DECLARED_PROCUREMENT_NAME_RE = re.compile(
    r"(?:세부\s*품명|품\s*명)\s*[:：]\s*(?P<name>[가-힣A-Za-z][^\n()\[\]]{1,60})",
    re.IGNORECASE,
)


PROCUREMENT_OBJECT_SCOPE_RE = re.compile(
    r"사업\s*명|건\s*명|용\s*역\s*명|구매\s*내역|"
    r"(?<!세부)품\s*명\s*[:：]|입찰에\s*부치는\s*사항|"
    r"과업\s*(?:명|목적|내용)",
    re.IGNORECASE,
)
BID_PRODUCT_REGISTRATION_RE = re.compile(
    r"입찰\s*참가\s*자격\s*등록|참가\s*자격\s*등록|"
    r"(?:공급|제조)\s*물품으로\s*(?:입찰\s*)?참가\s*등록|"
    r"나라장터[^.\n]{0,100}(?:등록한|등록된|등록을)",
    re.IGNORECASE,
)


def _catalog_name_role(
    context: str, *, anchor_start: int, anchor_end: int
) -> str:
    # A product name quoted inside an eligibility/direct-production clause is
    # the *target of that qualification*, not independent evidence that the
    # same product is the principal procurement object.  Check this before the
    # broad ``품명`` heading pattern: ``세부품명`` inside a direct-production
    # certificate otherwise contains that substring and was incorrectly
    # promoted to ``procurement_object_candidate``.
    immediate = context[max(0, anchor_start - 180) : min(len(context), anchor_end + 140)]
    if DIRECT_PRODUCTION_RE.search(immediate) or BID_PRODUCT_REGISTRATION_RE.search(immediate):
        return "bid_qualification_object_candidate"
    if PROCUREMENT_OBJECT_SCOPE_RE.search(immediate):
        return "procurement_object_candidate"
    if QUALIFICATION_SCOPE_RE.search(immediate):
        return "bid_qualification_object_candidate"
    if PROCUREMENT_OBJECT_SCOPE_RE.search(context):
        return "procurement_object_candidate"
    if QUALIFICATION_SCOPE_RE.search(context):
        return "bid_qualification_object_candidate"
    if re.search(r"법령|참고|예시|목록|현황", context):
        return "reference_or_list_candidate"
    return "unresolved_document_role"


def _extract_catalog_name_mentions(
    document: Mapping[str, Any], catalog: CatalogReference
) -> list[dict[str, Any]]:
    text = str(document["text"])
    candidates: list[tuple[int, int, str, list[str], str]] = []
    for match in catalog.exact_name_pattern.finditer(text):
        raw_name = match.group(0)
        codes = catalog.codes_for_exact_name(raw_name)
        if codes:
            candidates.append(
                (match.start(), match.end(), "exact_catalog_name", codes, raw_name)
            )
    for family, pattern, codes in CATALOG_NAME_FAMILIES:
        catalog_codes = [code for code in codes if code in catalog]
        if not catalog_codes:
            continue
        for match in pattern.finditer(text):
            candidates.append(
                (
                    match.start(),
                    match.end(),
                    f"catalog_name_family:{family}",
                    catalog_codes,
                    family,
                )
            )
    for match in DECLARED_PROCUREMENT_NAME_RE.finditer(text):
        raw_name = match.group("name").strip().rstrip(".,;:·ㆍ ")
        if len(raw_name) < 3 or catalog.codes_for_exact_name(raw_name):
            continue
        name_start = match.start("name")
        name_end = name_start + len(raw_name)
        candidates.append(
            (
                name_start,
                name_end,
                "declared_procurement_name_without_exact_catalog_match",
                [],
                raw_name,
            )
        )
    facts: list[dict[str, Any]] = []
    for start, end, basis, codes, name_or_family in sorted(candidates):
        local_start, local_end = _window_bounds(
            text, start, end, before=160, after=220, max_chars=520
        )
        context = text[local_start:local_end]
        exact = basis == "exact_catalog_name"
        declared_without_match = (
            basis == "declared_procurement_name_without_exact_catalog_match"
        )
        facts.append(
            _fact(
                "catalog_product_name_candidate",
                ("v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17", "v18"),
                _span(document, start, end, matched_by=(basis,)),
                identification_basis=basis,
                matched_name=name_or_family if exact or declared_without_match else None,
                name_family=None if exact or declared_without_match else name_or_family,
                candidate_catalog_codes=sorted(set(codes)),
                catalog_identities=[catalog.get(code) for code in sorted(set(codes))],
                source_role=(
                    "procurement_object_candidate"
                    if declared_without_match
                    else _catalog_name_role(
                        context,
                        anchor_start=start - local_start,
                        anchor_end=end - local_start,
                    )
                ),
                exact_catalog_identity=exact and len(set(codes)) == 1,
                catalog_comparison_scope=(
                    "all_exact_names_in_supplied_catalog"
                    if declared_without_match
                    else None
                ),
                exact_catalog_match_count=(0 if declared_without_match else len(set(codes))),
                ambiguities=(
                    []
                    if exact and len(set(codes)) == 1
                    else ["declared_name_has_no_exact_catalog_match"]
                    if declared_without_match
                    else ["name_family_does_not_select_one_exact_catalog_row"]
                ),
                context_span=_span(
                    document, local_start, local_end, matched_by=("catalog_name_context",)
                ),
            )
        )
    return _dedupe_facts(facts)


DIRECT_PRODUCTION_RE = re.compile(
    r"직접\s*생산(?:\s*확인)?(?:\s*증명서|\s*확인서|\s*증명)?",
    re.IGNORECASE,
)
QUALIFICATION_SCOPE_RE = re.compile(
    r"입찰\s*참가\s*자격|견적서\s*제출\s*참가\s*자격|"
    r"참가\s*자격|자격\s*요건|소지한\s*자|소지한\s*업체|"
    r"갖춘\s*자|참여할\s*수\s*있는|참가할\s*수\s*있는|"
    r"참가\s*가능|업체이어야|자이어야|업체로서",
    re.IGNORECASE,
)
SUBMISSION_LIST_RE = re.compile(
    r"제출\s*서류|구비\s*서류|제안서\s*제출\s*서류|차순위\s*서류",
    re.IGNORECASE,
)
POST_AWARD_RE = re.compile(
    r"낙찰(?:자|결정)?\s*(?:후|이후)|계약\s*(?:체결|이행)\s*(?:후|시|까지)|"
    r"적격\s*심사\s*대상자|낙찰자만",
    re.IGNORECASE,
)
GENERIC_REFERENCE_RE = re.compile(
    r"관련\s*법령|준수하여야|법령\s*및\s*규정|일반\s*조건|"
    r"중소기업제품\s*구매촉진\s*및\s*판로지원에\s*관한\s*법률",
    re.IGNORECASE,
)

TIMING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "before_bid_or_quote_deadline",
        re.compile(
            r"(?:입찰서|전자입찰서|견적서|제안서)\s*제출\s*"
            r"(?:마감일시|마감일|마감)\s*(?:전일|까지|이전)",
            re.IGNORECASE,
        ),
    ),
    (
        "by_bid_registration_deadline",
        re.compile(r"입찰\s*참가\s*자격\s*등록\s*마감(?:일시)?\s*까지", re.IGNORECASE),
    ),
    (
        "valid_at_bid_deadline",
        re.compile(r"제출\s*마감일\s*기준\s*유효|유효\s*기간\s*내|계속\s*유지", re.IGNORECASE),
    ),
    (
        "evaluation_document_deadline",
        re.compile(r"적격\s*심사\s*서류\s*제출\s*마감일\s*까지", re.IGNORECASE),
    ),
    (
        "by_contract_execution",
        re.compile(r"계약\s*체결\s*(?:시|까지|이전)", re.IGNORECASE),
    ),
    (
        "after_award_or_contract",
        re.compile(r"낙찰(?:자|결정)?\s*(?:후|이후)|계약\s*체결\s*후", re.IGNORECASE),
    ),
)


def _timing_mentions(
    document: Mapping[str, Any], start: int, end: int
) -> list[dict[str, Any]]:
    text = str(document["text"])
    local = text[start:end]
    mentions: list[dict[str, Any]] = []
    for timing, pattern in TIMING_PATTERNS:
        for match in pattern.finditer(local):
            mentions.append(
                {
                    "timing": timing,
                    "span": _span(
                        document,
                        start + match.start(),
                        start + match.end(),
                        matched_by=(timing,),
                    ),
                }
            )
    return sorted(
        mentions,
        key=lambda row: (row["span"]["start"], row["span"]["end"], row["timing"]),
    )


def _classify_direct_production_scope(context: str) -> tuple[str, list[str], str, str]:
    ambiguities: list[str] = []
    has_qualification = bool(QUALIFICATION_SCOPE_RE.search(context))
    has_list = bool(SUBMISSION_LIST_RE.search(context))
    has_post = bool(POST_AWARD_RE.search(context))
    has_requirement = bool(
        re.search(r"소지|발급|제출|확인|갖춘|유효|필수|의무", context)
    )
    has_bound_requirement = bool(
        re.search(
            r"직접\s*생산(?:\s*확인)?(?:\s*증명서|\s*확인서|\s*증명)?"
            r"[^.\n]{0,130}(?:보유한\s*업체|소지한\s*(?:업체|자)|"
            r"갖춘\s*(?:업체|자)|업체로서|"
            r"확인되지\s*않을\s*경우\s*입찰\s*참가\s*자격)|"
            r"(?:보유|소지|갖춘)[^.\n]{0,90}직접\s*생산(?:\s*확인)?",
            context,
            re.IGNORECASE,
        )
    )
    has_proof_list = bool(
        re.search(r"입찰\s*참가\s*등록\s*신청\s*서류|제출\s*서류|구비\s*서류", context)
        and re.search(
            r"직접\s*생산(?:\s*확인)?(?:\s*증명서|\s*확인서|\s*증명)?"
            r"[^\n]{0,60}(?:각\s*)?1\s*부",
            context,
            re.IGNORECASE,
        )
    )
    if has_bound_requirement:
        role = "operative_bid_qualification_candidate"
        binding_scope = "principal_or_explicit_product_qualification"
        eligibility_effect = "mandatory_possession_or_verification"
    elif has_proof_list:
        role = "proof_document_submission_list"
        binding_scope = "unbound_proof_document"
        eligibility_effect = "document_submission_only"
    elif has_post and not has_qualification:
        role = "post_award_or_contract_candidate"
        binding_scope = "post_award_or_contract"
        eligibility_effect = "not_bid_qualification"
    elif has_list and not has_qualification:
        role = "document_list_candidate"
        binding_scope = "unbound_document_list"
        eligibility_effect = "document_submission_only"
    elif has_qualification and has_requirement:
        role = "operative_bid_qualification_candidate"
        binding_scope = "qualification_context_candidate"
        eligibility_effect = "effect_requires_adjudication"
    elif GENERIC_REFERENCE_RE.search(context) and not has_requirement:
        role = "generic_legal_reference_candidate"
        binding_scope = "generic_reference"
        eligibility_effect = "reference_only"
    else:
        role = "scope_unresolved"
        binding_scope = "unresolved"
        eligibility_effect = "effect_unresolved"
        ambiguities.append("direct_production_scope_not_explicit")
    if has_post and has_qualification:
        ambiguities.append("qualification_and_postaward_language_cooccur")
    if has_list and has_qualification and not has_proof_list:
        ambiguities.append("qualification_and_submission_list_language_cooccur")
    return role, ambiguities, binding_scope, eligibility_effect


def _codes_within(
    code_facts: Sequence[Mapping[str, Any]], doc_index: int, start: int, end: int
) -> list[str]:
    codes = {
        str(fact.get("code"))
        for fact in code_facts
        if fact.get("span", {}).get("source_kind") == "document"
        and fact.get("span", {}).get("doc_index") == doc_index
        and start <= int(fact.get("span", {}).get("start", -1))
        and int(fact.get("span", {}).get("end", -1)) <= end
    }
    return sorted(code for code in codes if code)


def _extract_direct_production(
    document: Mapping[str, Any],
    code_facts: Sequence[Mapping[str, Any]],
    name_facts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    text = str(document["text"])
    hits = [
        (match.start(), match.end(), "direct_production_term")
        for match in DIRECT_PRODUCTION_RE.finditer(text)
    ]
    facts: list[dict[str, Any]] = []
    for start, end, markers in _merge_hit_windows(
        document, hits, before=480, after=850, max_chars=1_800
    ):
        context = text[start:end]
        role, ambiguities, binding_scope, eligibility_effect = _classify_direct_production_scope(
            context
        )
        qualification_constitutive = (
            False
            if (
                role == "proof_document_submission_list"
                and binding_scope == "unbound_proof_document"
                and eligibility_effect == "document_submission_only"
            )
            else True
            if (
                role == "operative_bid_qualification_candidate"
                and eligibility_effect == "mandatory_possession_or_verification"
            )
            else None
        )
        timing = _timing_mentions(document, start, end)
        codes = _codes_within(code_facts, int(document["doc_index"]), start, end)
        name_bindings = [
            fact
            for fact in name_facts
            if fact.get("span", {}).get("source_kind") == "document"
            and fact.get("span", {}).get("doc_index") == int(document["doc_index"])
            and start <= int(fact.get("span", {}).get("start", -1))
            and int(fact.get("span", {}).get("end", -1)) <= end
        ]
        if qualification_constitutive is False:
            # A checklist requests evidence; it does not itself bind the
            # certificate to a procurement object or create an eligibility
            # premise. Keep product mentions in their own fact family, not in
            # the checklist candidate's ``bound_*`` fields.
            codes = []
            name_bindings = []
        name_candidate_codes = sorted(
            {
                str(code)
                for fact in name_bindings
                for code in fact.get("candidate_catalog_codes", ())
            }
        )
        if qualification_constitutive is not False:
            if (
                not codes
                and not name_bindings
                and role
                not in {
                    "document_list_candidate",
                    "post_award_or_contract_candidate",
                    "generic_legal_reference_candidate",
                }
            ):
                ambiguities.append("direct_production_product_binding_not_explicit")
            elif len(codes) > 1:
                ambiguities.append("multiple_product_codes_in_direct_production_context")
            if name_bindings and not any(
                fact.get("exact_catalog_identity") for fact in name_bindings
            ):
                ambiguities.append("direct_production_bound_only_to_ambiguous_name_family")
        term_mentions = [
            {
                "span": _span(
                    document,
                    start + match.start(),
                    start + match.end(),
                    matched_by=("direct_production_term",),
                )
            }
            for match in DIRECT_PRODUCTION_RE.finditer(context)
        ]
        facts.append(
            _fact(
                "direct_production_requirement_candidate",
                ("v10", "v12"),
                _span(document, start, end, matched_by=markers),
                clause_role=role,
                binding_scope=binding_scope,
                eligibility_effect=eligibility_effect,
                qualification_constitutive=qualification_constitutive,
                term_mentions=term_mentions,
                timing_mentions=timing,
                bound_product_codes=codes,
                bound_product_name_fact_ids=[fact["fact_id"] for fact in name_bindings],
                name_candidate_catalog_codes=name_candidate_codes,
                ambiguities=sorted(set(ambiguities)),
            )
        )
    return facts


ENTERPRISE_TERM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("small_and_medium_enterprise", re.compile(r"중[·ㆍ•\s-]*소기업(?:자)?|중소기업(?:자)?")),
    ("small_enterprise", re.compile(r"(?<!중)소기업(?:자)?")),
    ("micro_enterprise", re.compile(r"소상공인")),
    ("medium_enterprise", re.compile(r"중기업(?:자)?")),
    ("mid_sized_enterprise", re.compile(r"중견기업(?:자)?")),
    ("large_enterprise", re.compile(r"대기업(?:인)?")),
    ("nonprofit_entity", re.compile(r"비영리(?:법인|단체)?")),
    ("special_act_entity", re.compile(r"특별법인")),
    ("eligible_cooperative", re.compile(r"적격\s*조합|중소기업\s*협동조합")),
    ("venture_enterprise", re.compile(r"벤처기업")),
    ("startup", re.compile(r"창업자|창업기업")),
)
ENTERPRISE_ANY_RE = re.compile(
    "|".join(f"(?:{pattern.pattern})" for _, pattern in ENTERPRISE_TERM_PATTERNS),
    re.IGNORECASE,
)
LOGIC_MARKER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("explicit_or", re.compile(r"또는|혹은|중\s*(?:어느\s*)?하나|이거나|\bOR\b", re.IGNORECASE)),
    ("explicit_and", re.compile(r"및|그리고|동시에|각각|\bAND\b", re.IGNORECASE)),
    ("all_requirements", re.compile(r"모두\s*(?:갖춘|충족|해당|구비)|아래의?\s*요건을\s*모두|다음\s*각\s*호를\s*모두", re.IGNORECASE)),
    ("exception_branch", re.compile(r"다만|예외|불구하고|경우에는|제외", re.IGNORECASE)),
)


def _term_mentions(
    document: Mapping[str, Any], start: int, end: int
) -> list[dict[str, Any]]:
    text = str(document["text"])
    local = text[start:end]
    mentions: list[dict[str, Any]] = []
    occupied: set[tuple[int, int, str]] = set()
    for normalized, pattern in ENTERPRISE_TERM_PATTERNS:
        for match in pattern.finditer(local):
            key = (match.start(), match.end(), normalized)
            if key in occupied:
                continue
            occupied.add(key)
            mentions.append(
                {
                    "normalized_scope": normalized,
                    "span": _span(
                        document,
                        start + match.start(),
                        start + match.end(),
                        matched_by=(normalized,),
                    ),
                }
            )
    return sorted(
        mentions,
        key=lambda row: (
            row["span"]["start"],
            -(row["span"]["end"] - row["span"]["start"]),
            row["normalized_scope"],
        ),
    )


def _logic_mentions(
    document: Mapping[str, Any], start: int, end: int
) -> list[dict[str, Any]]:
    text = str(document["text"])
    local = text[start:end]
    output: list[dict[str, Any]] = []
    for relation, pattern in LOGIC_MARKER_PATTERNS:
        for match in pattern.finditer(local):
            output.append(
                {
                    "relation": relation,
                    "span": _span(
                        document,
                        start + match.start(),
                        start + match.end(),
                        matched_by=(relation,),
                    ),
                }
            )
    return sorted(output, key=lambda row: (row["span"]["start"], row["relation"]))


def _enterprise_clause_role(context: str) -> tuple[str, list[str]]:
    ambiguities: list[str] = []
    qualification = bool(QUALIFICATION_SCOPE_RE.search(context))
    document_only = bool(SUBMISSION_LIST_RE.search(context))
    post = bool(POST_AWARD_RE.search(context))
    evaluation = bool(re.search(r"평가\s*(?:항목|점수|기준)|가점|적격\s*심사\s*배점", context))
    law_title_only = bool(
        re.search(r"중소기업제품\s*구매촉진\s*및\s*판로지원에\s*관한\s*법률", context)
        and not re.search(r"중소기업\s*확인서|소기업\s*[·ㆍ•·,\s]*소상공인|중소기업자", context)
    )
    if qualification:
        role = "operative_bid_qualification_candidate"
    elif evaluation:
        role = "evaluation_or_scoring_candidate"
    elif post and document_only:
        role = "postaward_document_candidate"
    elif document_only:
        role = "document_list_candidate"
    elif law_title_only or GENERIC_REFERENCE_RE.search(context):
        role = "generic_statutory_reference_candidate"
    else:
        role = "scope_unresolved"
        ambiguities.append("enterprise_size_role_not_explicit")
    if qualification and post:
        ambiguities.append("qualification_and_postaward_language_cooccur")
    return role, ambiguities


def _eligibility_effect(context: str, role: str) -> str:
    if re.search(r"참가할\s*수\s*없|참여할\s*수\s*없|제외|제한된다|배제", context):
        return "excluded_or_prohibited_candidate"
    if re.search(r"참가\s*가능|참가할\s*수\s*있|참여할\s*수\s*있|자이어야|업체이어야|소지한\s*자", context):
        return "allowed_or_required_qualification_candidate"
    if role in {"document_list_candidate", "postaward_document_candidate"}:
        return "document_submission_candidate"
    if role == "generic_statutory_reference_candidate":
        return "reference_only_candidate"
    return "effect_unresolved"


def _local_logic(mentions: Sequence[Mapping[str, Any]]) -> str:
    relations = {str(row.get("relation")) for row in mentions}
    if "all_requirements" in relations:
        if "explicit_or" in relations:
            return "cumulative_section_with_internal_or_candidate"
        return "explicit_cumulative_candidate"
    if "explicit_or" in relations and "explicit_and" in relations:
        return "mixed_and_or_requires_parse"
    if "explicit_or" in relations:
        return "explicit_alternative_candidate"
    if "explicit_and" in relations:
        return "explicit_conjunction_candidate"
    return "connective_not_explicit"


def _extract_enterprise_clauses(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = str(document["text"])
    hits = [
        (match.start(), match.end(), "enterprise_term")
        for match in ENTERPRISE_ANY_RE.finditer(text)
        if not (
            text[match.end() : match.end() + 2].lstrip().startswith("제품")
            or re.search(
                r"중소기업자?\s*간\s*경쟁제품",
                text[max(0, match.start() - 8) : min(len(text), match.end() + 24)],
            )
        )
    ]
    facts: list[dict[str, Any]] = []
    for start, end, markers in _merge_hit_windows(
        document, hits, before=430, after=760, max_chars=1_700
    ):
        context = text[start:end]
        terms = _term_mentions(document, start, end)
        role, ambiguities = _enterprise_clause_role(context)
        logic = _logic_mentions(document, start, end)
        timing = _timing_mentions(document, start, end)
        if role == "operative_bid_qualification_candidate" and not logic:
            ambiguities.append("cross_clause_and_or_relation_not_explicit")
        facts.append(
            _fact(
                "enterprise_size_clause_candidate",
                ("v11", "v13", "v14", "v15", "v16", "v17", "v18"),
                _span(document, start, end, matched_by=markers),
                clause_role=role,
                eligibility_effect=_eligibility_effect(context, role),
                enterprise_terms=terms,
                logic_mentions=logic,
                local_logic=_local_logic(logic),
                timing_mentions=timing,
                ambiguities=sorted(set(ambiguities)),
            )
        )
    return facts


GLOBAL_LOGIC_RE = re.compile(
    r"아래의?\s*(?:요건|자격)\s*(?:을|를)?\s*모두\s*(?:갖춘|충족|구비)|"
    r"다음\s*각\s*호의?\s*(?:요건|자격)\s*(?:을|를)?\s*모두|"
    r"다음\s*중\s*(?:어느\s*)?하나에\s*해당|"
    r"아래\s*중\s*(?:어느\s*)?하나",
    re.IGNORECASE,
)

QUALIFICATION_CLOSURE_RE = re.compile(
    r"(?:다음|아래)\s*(?:각\s*)?(?:사항|조건|요건|자격)\s*(?:을|를)?\s*"
    r"모두\s*(?:충족(?:한|하여야|해야)?|갖춘|구비(?:한|하여야)?)",
    re.IGNORECASE,
)
QUALIFICATION_ENUMERATED_SECTION_RE = re.compile(
    r"^[ \t]*(?:\d+\s*[.)]\s*)?입찰\s*참가\s*자격\s*(?:[:：])?[ \t]*\n"
    r"(?:[ \t]*\n)*[ \t]*(?:[①-⑳]|[가-하]\s*[.)]|\d+\s*[.)])",
    re.IGNORECASE | re.MULTILINE,
)
QUALIFICATION_DELEGATION_RE = re.compile(
    r"(?:입찰\s*참가\s*자격|참가\s*자격|자격\s*요건|참가\s*요건)"
    r"[^\n.]{0,120}(?:제안\s*요청서|과업\s*지시서|규격서)"
    r"[^\n.]{0,60}(?:참조|따른다|따름|의한다|의함)|"
    r"(?:제안\s*요청서|과업\s*지시서|규격서)"
    r"[^\n.]{0,80}(?:참가\s*자격|자격\s*요건)[^\n.]{0,40}(?:참조|따른다|따름|의한다|의함)",
    re.IGNORECASE,
)
NONQUALIFICATION_EXTERNAL_REFERENCE_RE = re.compile(
    r"(?:사업\s*(?:안내|내용|범위|기간|일정|완료)|"
    r"과업\s*(?:내용|범위|기간|일정|완료)|"
    r"용역\s*(?:기간|일정|완료(?:일)?)|납기|제출\s*서류|"
    r"평가\s*(?:기준|방법))[^\n.]{0,120}"
    r"(?:제안\s*요청서|과업\s*지시서|규격서)[^\n.]{0,50}"
    r"(?:참조|따른다|따름|의한다|의함)|"
    r"(?:제안\s*요청서|과업\s*지시서|규격서)[^\n.]{0,100}"
    r"(?:사업\s*(?:안내|내용|범위|기간|일정|완료)|"
    r"과업\s*(?:내용|범위|기간|일정|완료)|"
    r"용역\s*(?:기간|일정|완료(?:일)?)|납기|제출\s*서류|"
    r"평가\s*(?:기준|방법))[^\n.]{0,40}(?:참조|따른다|따름|의한다|의함)",
    re.IGNORECASE,
)


def _extract_logic_markers(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = str(document["text"])
    facts: list[dict[str, Any]] = []
    for match in GLOBAL_LOGIC_RE.finditer(text):
        raw = match.group(0)
        relation = "explicit_alternative_paths" if re.search(r"하나", raw) else "explicit_cumulative_requirements"
        facts.append(
            _fact(
                "qualification_logic_marker",
                ("v11", "v13", "v14", "v15", "v16", "v17", "v18"),
                _span(document, match.start(), match.end(), matched_by=(relation,)),
                relation=relation,
            )
        )
    return facts


def _extract_qualification_source_markers(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Record explicit closure/delegation language without deciding a label."""

    text = str(document["text"])
    facts: list[dict[str, Any]] = []
    patterns = (
        (
            "closed_cumulative_qualification_list",
            "bid_qualification",
            QUALIFICATION_CLOSURE_RE,
        ),
        (
            "closed_enumerated_qualification_section",
            "bid_qualification",
            QUALIFICATION_ENUMERATED_SECTION_RE,
        ),
        (
            "external_qualification_delegation",
            "bid_qualification",
            QUALIFICATION_DELEGATION_RE,
        ),
        (
            "external_nonqualification_reference",
            "business_task_submission_or_evaluation_details",
            NONQUALIFICATION_EXTERNAL_REFERENCE_RE,
        ),
    )
    qualification_delegation_ranges = [
        (match.start(), match.end())
        for match in QUALIFICATION_DELEGATION_RE.finditer(text)
    ]
    for marker_type, routed_scope, pattern in patterns:
        for match in pattern.finditer(text):
            if marker_type == "external_nonqualification_reference" and any(
                not (match.end() <= start or match.start() >= end)
                for start, end in qualification_delegation_ranges
            ):
                # A qualification delegation remains qualification-scoped even
                # when it also contains a generic word such as "details".
                continue
            facts.append(
                _fact(
                    "qualification_source_marker",
                    (
                        "v10",
                        "v11",
                        "v12",
                        "v13",
                        "v14",
                        "v15",
                        "v16",
                        "v17",
                        "v18",
                        "v20",
                    ),
                    _span(document, match.start(), match.end(), matched_by=(marker_type,)),
                    marker_type=marker_type,
                    routed_scope=routed_scope,
                    is_label_decision=False,
                )
            )
    return facts


EXCEPTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "nonprofit_eligibility_path",
        re.compile(r"비영리(?:법인|단체)?.{0,180}(?:참가|참여|제외|적용\s*받지|사업자\s*등록증)", re.IGNORECASE | re.DOTALL),
    ),
    (
        "special_act_entity_path",
        re.compile(r"특별법인.{0,180}(?:참가|참여|인정|간주|서류)", re.IGNORECASE | re.DOTALL),
    ),
    (
        "eligible_cooperative_path",
        re.compile(r"(?:적격\s*조합|중소기업\s*협동조합).{0,220}(?:참가|배정|조합원사|가능)", re.IGNORECASE | re.DOTALL),
    ),
    (
        "failed_competition_or_rebid",
        re.compile(r"(?:유찰|재공고|선행\s*입찰\s*실패).{0,180}(?:제한|예외|참가)", re.IGNORECASE | re.DOTALL),
    ),
    (
        "limited_eligible_small_suppliers",
        re.compile(r"(?:소기업|소상공인).{0,100}(?:3\s*인|삼\s*인).{0,100}(?:미만|이하|없)", re.IGNORECASE | re.DOTALL),
    ),
    (
        "statutory_article_2_3_reference",
        re.compile(r"(?:판로지원법\s*)?시행령\s*제\s*2\s*조의\s*3", re.IGNORECASE),
    ),
    (
        "statutory_article_7_2_reference",
        re.compile(r"(?:판로지원법|중소기업제품\s*구매촉진\s*및\s*판로지원에\s*관한\s*법률).{0,30}제\s*7\s*조의\s*2", re.IGNORECASE | re.DOTALL),
    ),
    (
        "statutory_article_33_reference",
        re.compile(r"(?:판로지원법|중소기업제품\s*구매촉진\s*및\s*판로지원에\s*관한\s*법률).{0,30}제\s*33\s*조", re.IGNORECASE | re.DOTALL),
    ),
)


def _extract_statutory_exceptions(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = str(document["text"])
    raw_hits: list[tuple[int, int, str]] = []
    for exception_type, pattern in EXCEPTION_PATTERNS:
        for match in pattern.finditer(text):
            raw_hits.append((match.start(), match.end(), exception_type))
    facts: list[dict[str, Any]] = []
    for hit_start, hit_end, exception_type in sorted(raw_hits):
        start, end = _window_bounds(
            text, hit_start, hit_end, before=220, after=360, max_chars=900
        )
        context = text[start:end]
        financial_note = bool(
            exception_type == "nonprofit_eligibility_path"
            and re.search(
                r"원가\s*계산|용역\s*원가|일반\s*관리비|이윤|부가\s*가치세",
                context,
                re.IGNORECASE,
            )
            and not QUALIFICATION_SCOPE_RE.search(context)
        )
        if financial_note:
            role, ambiguities = "financial_calculation_note", []
            establishment_status = "excluded_noneligibility_context"
        else:
            role, ambiguities = _enterprise_clause_role(context)
            establishment_status = "not_adjudicated_candidate_only"
        conditional = bool(re.search(r"다만|경우|해당하는|요건|제외", context))
        facts.append(
            _fact(
                "statutory_exception_candidate",
                ("v11", "v13", "v14", "v15", "v16", "v17", "v18"),
                _span(document, start, end, matched_by=(exception_type,)),
                exception_type=exception_type,
                clause_role=role,
                conditional_language_present=conditional,
                establishment_status=establishment_status,
                trigger_span=_span(
                    document,
                    hit_start,
                    hit_end,
                    matched_by=(exception_type,),
                ),
                timing_mentions=_timing_mentions(document, start, end),
                ambiguities=ambiguities,
            )
        )
    chosen: dict[tuple[str, int, int, int], dict[str, Any]] = {}
    for fact in facts:
        trigger = fact["trigger_span"]
        key = (
            str(fact["exception_type"]),
            int(trigger["doc_index"]),
            int(trigger["start"]),
            int(trigger["end"]),
        )
        chosen.setdefault(key, fact)
    return sorted(
        chosen.values(),
        key=lambda row: (
            row["trigger_span"]["doc_index"],
            row["trigger_span"]["start"],
            row["exception_type"],
        ),
    )


SOFTWARE_OBJECT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("software", re.compile(r"소프트웨어|S\s*/?\s*W\b", re.IGNORECASE)),
    ("software_license", re.compile(r"라이선스|사용\s*권한", re.IGNORECASE)),
    ("information_system", re.compile(r"정보\s*시스템|전산\s*시스템|경영\s*정보\s*시스템", re.IGNORECASE)),
    ("named_system", re.compile(r"(?<!조달)(?<!계약)(?<!입찰)(?<!전자)시스템", re.IGNORECASE)),
    ("program", re.compile(r"응용\s*프로그램|컴퓨터\s*프로그램|프로그램\s*소스\s*코드", re.IGNORECASE)),
    ("web_or_app", re.compile(r"홈페이지|웹\s*사이트|모바일\s*앱|앱\s*플리케이션", re.IGNORECASE)),
    ("database", re.compile(r"데이터\s*베이스|\bDB\b", re.IGNORECASE)),
    ("operating_system", re.compile(r"운영\s*체제|\bOS\b", re.IGNORECASE)),
    ("information_security_system", re.compile(r"정보\s*보호\s*시스템|보안\s*시스템", re.IGNORECASE)),
)
SOFTWARE_TASK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("develop", re.compile(r"개발(?:하|업무|용역|작업|범위)?", re.IGNORECASE)),
    ("build", re.compile(r"구축(?:하|사업|용역|작업)?", re.IGNORECASE)),
    ("supply_or_purchase", re.compile(r"공급|구매|납품|도입", re.IGNORECASE)),
    ("install", re.compile(r"설치|세팅", re.IGNORECASE)),
    ("configure", re.compile(r"구성|설정", re.IGNORECASE)),
    ("integrate", re.compile(r"연계|통합", re.IGNORECASE)),
    ("operate", re.compile(r"운영|운용", re.IGNORECASE)),
    ("maintain", re.compile(r"유지\s*관리|유지\s*보수|하자\s*보수", re.IGNORECASE)),
    ("patch_or_update", re.compile(r"패치|업\s*그레이드|업데이트", re.IGNORECASE)),
    ("technical_support", re.compile(r"기술\s*지원|운영\s*지원", re.IGNORECASE)),
    ("renew_or_subscribe", re.compile(r"갱신|구독|사용\s*연장", re.IGNORECASE)),
)
SOFTWARE_SIGNAL_RE = re.compile(
    "|".join(
        [
            f"(?:{pattern.pattern})"
            for name, pattern in SOFTWARE_OBJECT_PATTERNS
            if name != "named_system"
        ]
        + [
            r"소프트웨어\s*사업자",
            r"컴퓨터\s*관련\s*서비스",
            r"정보\s*시스템\s*유지\s*관리\s*서비스",
        ]
    ),
    re.IGNORECASE,
)


def _software_mentions(
    document: Mapping[str, Any],
    start: int,
    end: int,
    patterns: Sequence[tuple[str, re.Pattern[str]]],
    key: str,
) -> list[dict[str, Any]]:
    text = str(document["text"])
    local = text[start:end]
    output: list[dict[str, Any]] = []
    for normalized, pattern in patterns:
        for match in pattern.finditer(local):
            output.append(
                {
                    key: normalized,
                    "span": _span(
                        document,
                        start + match.start(),
                        start + match.end(),
                        matched_by=(normalized,),
                    ),
                }
            )
    return sorted(output, key=lambda row: (row["span"]["start"], row[key]))


def _software_relation_role(
    context: str,
    objects: Sequence[Mapping[str, Any]],
    tasks: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str]]:
    ambiguities: list[str] = []
    training = bool(re.search(r"교육|교재|수강생|강의|코딩\s*실습|원고|영상\s*제작", context))
    inventory = bool(re.search(r"요구\s*사항|목록|보안\s*점검|유출\s*금지|접근\s*권한|현황", context))
    incidental = bool(re.search(r"도구를?\s*(?:사용|활용)|이용하여|작성\s*도구", context))
    registration = bool(re.search(r"소프트웨어\s*사업자.{0,80}(?:등록|신고)|업종\s*코드", context, re.DOTALL))
    procurement_title = bool(
        re.search(
            r"(?:공\s*고|사\s*업|용\s*역|과\s*업|구\s*매|계\s*약\s*건)\s*명\s*[:：]|"
            r"\((?:일반|제한|지명)?\s*경쟁\s*[·,/|]\s*\d+(?:\.\d+)?\s*억원\s*"
            r"(?:미만|이상)?\)",
            context,
            re.IGNORECASE,
        )
    )
    contract_scope = bool(
        re.search(r"사업\s*(?:명|내용|목적|범위)|과업\s*(?:내용|범위|목적)|"
                  r"납품|구매|구축\s*사업|유지\s*(?:관리|보수)\s*용역|"
                  r"계약\s*상대자|수행\s*업무|소프트웨어(?:의)?\s*설계\s*(?:및|·)\s*개발", context)
    ) or procurement_title
    if training and not contract_scope:
        role = "training_or_content_context"
    elif incidental and not contract_scope:
        role = "incidental_tool_use_context"
    elif inventory and not contract_scope:
        role = "requirements_inventory_or_security_reference"
    elif registration and not tasks:
        role = "bidder_registration_only_candidate"
    elif objects and tasks and contract_scope:
        role = "contract_software_object_task_candidate"
    elif objects and tasks:
        role = "same_context_software_object_task_unresolved"
        ambiguities.append("contractual_role_not_explicit")
    elif objects:
        role = "software_object_without_bound_task"
        ambiguities.append("software_task_not_bound")
    else:
        role = "software_signal_without_object_relation"
        ambiguities.append("software_object_not_explicit")
    if inventory and contract_scope:
        ambiguities.append("operative_and_boilerplate_signals_cooccur")
    return role, ambiguities


def _extract_software_work(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = str(document["text"])
    hits = [
        (match.start(), match.end(), "software_signal")
        for match in SOFTWARE_SIGNAL_RE.finditer(text)
    ]
    facts: list[dict[str, Any]] = []
    for start, end, markers in _merge_hit_windows(
        document, hits, before=360, after=620, max_chars=1_450
    ):
        objects = _software_mentions(
            document, start, end, SOFTWARE_OBJECT_PATTERNS, "object_type"
        )
        tasks = _software_mentions(document, start, end, SOFTWARE_TASK_PATTERNS, "task_type")
        role, ambiguities = _software_relation_role(text[start:end], objects, tasks)
        facts.append(
            _fact(
                "software_object_task_candidate",
                ("v20",),
                _span(document, start, end, matched_by=markers),
                relation_role=role,
                software_objects=objects,
                task_actions=tasks,
                ambiguities=ambiguities,
            )
        )
    return facts


LARGE_RESTRICTION_RE = re.compile(
    r"대기업(?:인)?\s*(?:소프트웨어\s*사업자)?.{0,180}(?:참여|참가|제한|배제|불가)|"
    r"상호\s*출자\s*제한\s*기업\s*집단.{0,180}(?:참여|참가|제한|배제|불가)|"
    r"공공\s*소프트웨어\s*사업\s*대기업\s*참여\s*제한|"
    r"소프트웨어\s*진흥법\s*제\s*48\s*조",
    re.IGNORECASE | re.DOTALL,
)
RESTRICTION_WORD_RE = re.compile(r"참여할\s*수\s*없|참가할\s*수\s*없|제한|배제|불가|참여\s*가능", re.IGNORECASE)
MONEY_IN_CONTEXT_RE = re.compile(
    r"(?<![\d,])(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>억원|천만원|백만원|만원|원)(?![\d원])"
)
TURNOVER_RE = re.compile(r"매출액?.{0,30}(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>천억원|억원|원)", re.IGNORECASE)
UNIT_MULTIPLIER = {
    "원": 1,
    "만원": 10_000,
    "백만원": 1_000_000,
    "천만원": 10_000_000,
    "억원": 100_000_000,
    "천억원": 100_000_000_000,
}


def _money_mentions(
    document: Mapping[str, Any], start: int, end: int
) -> list[dict[str, Any]]:
    local = str(document["text"])[start:end]
    values: list[dict[str, Any]] = []
    for match in MONEY_IN_CONTEXT_RE.finditer(local):
        unit = match.group("unit")
        won = int(round(float(match.group("number")) * UNIT_MULTIPLIER[unit]))
        values.append(
            {
                "won": won,
                "semantic_role": "amount_threshold_or_claim_unresolved",
                "span": _span(
                    document,
                    start + match.start(),
                    start + match.end(),
                    matched_by=("money",),
                ),
            }
        )
    return values


def _extract_large_enterprise_restrictions(
    document: Mapping[str, Any]
) -> list[dict[str, Any]]:
    text = str(document["text"])
    facts: list[dict[str, Any]] = []
    for match in LARGE_RESTRICTION_RE.finditer(text):
        start, end = _window_bounds(
            text, match.start(), match.end(), before=300, after=500, max_chars=1_200
        )
        context = text[start:end]
        operative = bool(
            re.search(r"본\s*사업|본\s*입찰|입찰\s*참가|참여할\s*수\s*없|참가할\s*수\s*없", context)
        )
        generic = bool(GENERIC_REFERENCE_RE.search(context)) and not operative
        if re.search(r"참여할\s*수\s*없|참가할\s*수\s*없|배제|불가", context):
            effect = "participation_prohibited_candidate"
        elif re.search(r"참여\s*가능|참가\s*가능", context):
            effect = "conditional_participation_candidate"
        elif RESTRICTION_WORD_RE.search(context):
            effect = "restriction_language_candidate"
        else:
            effect = "effect_unresolved"
        role = (
            "operative_bid_restriction_candidate"
            if operative
            else "generic_statutory_reference_candidate"
            if generic
            else "scope_unresolved"
        )
        turnover = []
        for turnover_match in TURNOVER_RE.finditer(context):
            unit = turnover_match.group("unit")
            turnover.append(
                {
                    "won": int(
                        round(
                            float(turnover_match.group("number"))
                            * UNIT_MULTIPLIER[unit]
                        )
                    ),
                    "span": _span(
                        document,
                        start + turnover_match.start(),
                        start + turnover_match.end(),
                        matched_by=("turnover_threshold",),
                    ),
                }
            )
        facts.append(
            _fact(
                "large_enterprise_software_restriction_candidate",
                ("v20",),
                _span(document, start, end, matched_by=("large_enterprise_restriction",)),
                clause_role=role,
                restriction_effect=effect,
                amount_mentions=_money_mentions(document, start, end),
                turnover_mentions=turnover,
                trigger_span=_span(
                    document,
                    match.start(),
                    match.end(),
                    matched_by=("large_enterprise_restriction",),
                ),
                ambiguities=[] if role != "scope_unresolved" else ["restriction_scope_not_explicit"],
            )
        )
    return _dedupe_facts(facts)


def _metadata_facts(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
    output: dict[str, dict[str, Any]] = {}
    for semantic_name, source_key in META_FIELDS.items():
        value = meta.get(source_key)
        present = source_key in meta and value not in (None, "", "미입력")
        if semantic_name == "article_summary":
            semantic_role = "search_or_classification_summary_only"
            operative_bid_qualification = False
            establishes_statutory_exception = False
        elif semantic_name in {"catalog_codes", "industry_codes", "industry_restricted"}:
            semantic_role = "organizer_bid_constraint_metadata"
            operative_bid_qualification = None
            establishes_statutory_exception = False
        elif semantic_name in {"budget_won", "estimated_price_won"}:
            semantic_role = "organizer_numeric_metadata"
            operative_bid_qualification = None
            establishes_statutory_exception = False
        else:
            semantic_role = "organizer_descriptive_metadata"
            operative_bid_qualification = None
            establishes_statutory_exception = False
        output[semantic_name] = {
            "source_kind": "meta",
            "path": f"meta.{source_key}",
            "source_key": source_key,
            "value": value,
            "value_sha256": sha256_text(
                json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            ),
            "status": "present" if present else "missing_or_unentered",
            "semantic_role": semantic_role,
            "operative_bid_qualification": operative_bid_qualification,
            "establishes_statutory_exception": establishes_statutory_exception,
        }
    return output


def _direct_production_bindings(
    direct_facts: Sequence[Mapping[str, Any]],
    code_facts: Sequence[Mapping[str, Any]],
    name_facts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    meta_codes = {
        str(fact.get("code"))
        for fact in code_facts
        if fact.get("span", {}).get("source_kind") == "meta"
    }
    bid_object_codes = {
        str(fact.get("code"))
        for fact in code_facts
        if fact.get("span", {}).get("source_kind") == "document"
        and fact.get("source_role")
        in {"procurement_object_candidate", "bid_registration_requirement"}
    }
    output: list[dict[str, Any]] = []
    for fact in direct_facts:
        bound = set(str(code) for code in fact.get("bound_product_codes", ()) if code)
        bound_name_codes = set(
            str(code)
            for code in fact.get("name_candidate_catalog_codes", ())
            if code
        )
        # Only an independently declared procurement object may corroborate a
        # direct-production clause's product identity.  A catalog name found
        # in the qualification clause itself is circular evidence and must not
        # be reported as overlap with the bid object.
        procurement_name_codes = {
            str(code)
            for name_fact in name_facts
            if name_fact.get("source_role") == "procurement_object_candidate"
            for code in name_fact.get("candidate_catalog_codes", ())
        }
        declared_procurement_names = sorted(
            {
                str(name_fact.get("matched_name"))
                for name_fact in name_facts
                if name_fact.get("source_role") == "procurement_object_candidate"
                and name_fact.get("matched_name")
            }
        )
        qualification_constitutive = fact.get("qualification_constitutive")
        if qualification_constitutive is False:
            relation = "binding_not_applicable"
            binding_applicability = "not_applicable"
        elif bound and meta_codes and bound & meta_codes:
            relation = "exact_code_overlap_with_bid_metadata"
            binding_applicability = "applicable"
        elif bound and bid_object_codes and bound & bid_object_codes:
            relation = "exact_code_overlap_with_declared_bid_object"
            binding_applicability = "applicable"
        elif bound and meta_codes and not bound & meta_codes:
            relation = "exact_code_mismatch_with_bid_metadata"
            binding_applicability = "applicable"
        elif bound_name_codes and procurement_name_codes and bound_name_codes & procurement_name_codes:
            relation = "catalog_name_candidate_overlap_with_bid_object"
            binding_applicability = "applicable"
        elif bound:
            if declared_procurement_names:
                relation = "clause_code_without_independent_bid_object_match"
                binding_applicability = "requires_object_scope_adjudication"
            else:
                relation = "explicit_clause_code_without_bid_metadata_code"
                binding_applicability = "applicable"
        elif bound_name_codes:
            relation = "name_bound_clause_without_independent_bid_object_match"
            binding_applicability = "applicable"
        else:
            relation = "product_binding_unresolved"
            binding_applicability = (
                "applicable"
                if qualification_constitutive is True
                else "requires_scope_adjudication"
            )
        output.append(
            {
                "direct_production_fact_id": fact["fact_id"],
                "clause_codes": sorted(bound),
                "clause_name_candidate_codes": sorted(bound_name_codes),
                "bid_metadata_codes": sorted(meta_codes),
                "declared_bid_object_codes": sorted(bid_object_codes),
                "bid_object_name_candidate_codes": sorted(procurement_name_codes),
                "declared_bid_object_names": declared_procurement_names,
                "relation": relation,
                "binding_applicability": binding_applicability,
                "qualification_constitutive": qualification_constitutive,
                "is_label_decision": False,
            }
        )
    return output


def _product_code_inventory(
    code_facts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return a compact, lossless index of exact product codes.

    The bounded context may omit duplicate fact bodies, but an exact code must
    never disappear merely because a different premise family consumed the
    character budget. Fact IDs and compact source coordinates keep every
    inventory entry traceable to the fully validated extractor result.
    """

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for fact in code_facts:
        code = str(fact.get("code") or "")
        if code:
            grouped.setdefault(code, []).append(fact)
    output: list[dict[str, Any]] = []
    for code, facts in sorted(grouped.items()):
        mentions: list[dict[str, Any]] = []
        for fact in facts:
            span = fact.get("span") if isinstance(fact.get("span"), Mapping) else {}
            mention = {
                "fact_id": fact.get("fact_id"),
                "source_role": fact.get("source_role"),
            }
            if span.get("source_kind") == "document":
                mention["document_coordinate"] = [
                    span.get("doc_index"),
                    span.get("start"),
                    span.get("end"),
                ]
            elif span.get("source_kind") == "meta":
                mention["metadata_coordinate"] = [
                    span.get("path"),
                    span.get("start"),
                    span.get("end"),
                ]
            mentions.append(mention)
        output.append(
            {
                "code": code,
                "catalog_registered": any(
                    bool(fact.get("catalog_registered")) for fact in facts
                ),
                "mentions": mentions,
                "is_label_decision": False,
            }
        )
    return output


def _qualification_path_logic(
    enterprise: Sequence[Mapping[str, Any]],
    logic_markers: Sequence[Mapping[str, Any]],
    exceptions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    operative = [
        fact
        for fact in enterprise
        if fact.get("clause_role") == "operative_bid_qualification_candidate"
    ]
    relations = Counter(
        str(marker.get("relation"))
        for fact in operative
        for marker in fact.get("logic_mentions", ())
    )
    relations.update(str(fact.get("relation")) for fact in logic_markers)
    if relations.get("explicit_cumulative_requirements") and relations.get(
        "explicit_alternative_paths"
    ):
        status = "explicit_mixed_and_or_requires_adjudication"
    elif relations.get("explicit_cumulative_requirements"):
        status = "explicit_cumulative_marker_present"
    elif relations.get("explicit_alternative_paths") or relations.get("explicit_or"):
        status = "explicit_alternative_marker_present"
    elif operative:
        status = "operative_clauses_present_but_cross_clause_logic_unresolved"
    else:
        status = "no_operative_enterprise_clause_extracted"
    return {
        "status": status,
        "operative_clause_fact_ids": [fact["fact_id"] for fact in operative],
        "global_logic_marker_fact_ids": [fact["fact_id"] for fact in logic_markers],
        "alternative_exception_fact_ids": [
            fact["fact_id"]
            for fact in exceptions
            if fact.get("clause_role") == "operative_bid_qualification_candidate"
        ],
        "observed_relation_counts": dict(sorted(relations.items())),
        "is_label_decision": False,
    }


def _software_restriction_linkage(
    software: Sequence[Mapping[str, Any]],
    restrictions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    operative_software = [
        fact["fact_id"]
        for fact in software
        if fact.get("relation_role") == "contract_software_object_task_candidate"
    ]
    operative_restrictions = [
        fact["fact_id"]
        for fact in restrictions
        if fact.get("clause_role") == "operative_bid_restriction_candidate"
    ]
    if operative_software and operative_restrictions:
        status = "both_operative_candidate_types_observed"
    elif operative_software:
        status = "software_work_observed_restriction_not_extracted"
    elif operative_restrictions:
        status = "restriction_observed_software_work_not_resolved"
    else:
        status = "operative_linkage_unresolved"
    return {
        "status": status,
        "software_work_fact_ids": operative_software,
        "restriction_fact_ids": operative_restrictions,
        "is_label_decision": False,
    }


def _qualification_source_closure(
    markers: Sequence[Mapping[str, Any]], documents: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    closure_ids = [
        fact["fact_id"]
        for fact in markers
        if fact.get("marker_type")
        in {
            "closed_cumulative_qualification_list",
            "closed_enumerated_qualification_section",
        }
    ]
    delegation_ids = [
        fact["fact_id"]
        for fact in markers
        if fact.get("marker_type") == "external_qualification_delegation"
    ]
    other_scope_ids = [
        fact["fact_id"]
        for fact in markers
        if fact.get("marker_type") == "external_nonqualification_reference"
    ]
    if delegation_ids:
        status = "external_qualification_delegation_observed"
        qualification_materiality = "potentially_material_to_bid_qualification_scope"
        qualification_basis = "explicit_external_qualification_delegation"
    elif closure_ids:
        status = "closed_qualification_list_without_detected_external_delegation"
        qualification_materiality = "not_material_to_closed_bid_qualification_scope"
        qualification_basis = "explicit_closed_list_and_no_qualification_delegation"
    else:
        status = "qualification_source_closure_not_established"
        qualification_materiality = "unresolved_requires_item_specific_review"
        qualification_basis = "qualification_source_closure_not_established"
    return {
        "status": status,
        "closed_list_marker_fact_ids": closure_ids,
        "delegation_marker_fact_ids": delegation_ids,
        "other_scope_reference_marker_fact_ids": other_scope_ids,
        "scope_assessments": {
            "bid_qualification": {
                "source_status": status,
                "missing_document_materiality": qualification_materiality,
                "basis": qualification_basis,
                "basis_fact_ids": sorted(set(closure_ids + delegation_ids)),
            },
            "business_task_submission_or_evaluation_details": {
                "source_status": (
                    "external_reference_observed"
                    if other_scope_ids
                    else "no_external_reference_observed"
                ),
                "missing_document_materiality": (
                    "potentially_material_within_this_nonqualification_scope_only"
                    if other_scope_ids
                    else "not_assessed"
                ),
                "basis_fact_ids": other_scope_ids,
            },
        },
        "provided_documents_scanned": len(documents),
        "semantic_scope": "provided_source_routing_only",
        "is_label_decision": False,
    }


def extract_qualification_facts(
    record: Mapping[str, Any], *, catalog: CatalogReference | None = None
) -> dict[str, Any]:
    """Extract v10-v18/v20 premises without assigning any final value."""

    catalog = catalog or CatalogReference.load()
    documents = _documents(record)
    code_facts = _meta_code_facts(record, catalog)
    name_facts: list[dict[str, Any]] = []
    for document in documents:
        code_facts.extend(_code_mentions_in_document(document, catalog))
        name_facts.extend(_extract_catalog_name_mentions(document, catalog))
    code_facts = _dedupe_facts(code_facts)
    name_facts = _dedupe_facts(name_facts)

    direct: list[dict[str, Any]] = []
    enterprise: list[dict[str, Any]] = []
    logic: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    software: list[dict[str, Any]] = []
    restrictions: list[dict[str, Any]] = []
    source_markers: list[dict[str, Any]] = []
    for document in documents:
        direct.extend(_extract_direct_production(document, code_facts, name_facts))
        enterprise.extend(_extract_enterprise_clauses(document))
        logic.extend(_extract_logic_markers(document))
        exceptions.extend(_extract_statutory_exceptions(document))
        software.extend(_extract_software_work(document))
        restrictions.extend(_extract_large_enterprise_restrictions(document))
        source_markers.extend(_extract_qualification_source_markers(document))
    direct = _dedupe_facts(direct)
    enterprise = _dedupe_facts(enterprise)
    logic = _dedupe_facts(logic)
    exceptions = _dedupe_exception_facts(exceptions)
    software = _dedupe_facts(software)
    restrictions = _dedupe_facts(restrictions)
    source_markers = _dedupe_facts(source_markers)

    dropped = record.get("dropped_doc_counts") or {}
    completeness = record.get("input_completeness") or {}
    ambiguities: list[dict[str, Any]] = []
    if isinstance(dropped, Mapping) and any(
        isinstance(value, (int, float)) and value > 0 for value in dropped.values()
    ):
        ambiguities.append(
            {
                "code": "source_documents_dropped",
                "detail": dict(dropped),
                "effect": "item-specific source authority and delegation must be reviewed; no automatic abstention",
                "automatic_abstention": False,
                "requires_item_specific_materiality_review": True,
            }
        )
    if isinstance(completeness, Mapping) and any(
        value is False for value in completeness.values()
    ):
        ambiguities.append(
            {
                "code": "input_not_fully_observed",
                "detail": dict(completeness),
                "effect": "item-specific source authority and missing-materiality must be reviewed; no automatic abstention",
                "automatic_abstention": False,
                "requires_item_specific_materiality_review": True,
            }
        )

    facts = {
        "bid_product_code_mentions": code_facts,
        "catalog_product_name_candidates": name_facts,
        "direct_production_requirements": direct,
        "enterprise_size_clauses": enterprise,
        "qualification_logic_markers": logic,
        "statutory_exception_clauses": exceptions,
        "software_object_task_relations": software,
        "large_enterprise_software_restrictions": restrictions,
        "qualification_source_markers": source_markers,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "record_id": str(record.get("id") or ""),
        "source_sha256": sha256_object(record),
        "label_decisions_present": False,
        "semantic_role": "source_grounded_facts_only",
        "target_items": list(TARGET_ITEMS),
        "reference_sources": {
            "competitive_product_catalog": {
                "path": _relative_path(catalog.path),
                "sha256": catalog.sha256,
                "coded_rows": len(catalog),
                "total_rows": catalog.total_rows,
            }
        },
        "metadata_facts": _metadata_facts(record),
        "source_completeness": {
            "document_count": len(documents),
            "input_completeness": dict(completeness)
            if isinstance(completeness, Mapping)
            else completeness,
            "dropped_doc_counts": dict(dropped) if isinstance(dropped, Mapping) else dropped,
        },
        "facts": facts,
        "relations": {
            "exact_product_code_inventory": _product_code_inventory(code_facts),
            "direct_production_target_bindings": _direct_production_bindings(
                direct, code_facts, name_facts
            ),
            "qualification_path_logic": _qualification_path_logic(
                enterprise, logic, exceptions
            ),
            "software_restriction_linkage": _software_restriction_linkage(
                software, restrictions
            ),
            "qualification_source_closure": _qualification_source_closure(
                source_markers, documents
            ),
        },
        "ambiguities": ambiguities,
        "fact_counts": {key: len(value) for key, value in facts.items()},
    }


# A concise alias for callers that already know which independent fact module is
# in use.  It remains a fact extractor, not an annotator.
extract_facts = extract_qualification_facts


def _walk_spans(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        required = {"source_kind", "start", "end", "text", "text_sha256"}
        if required <= set(value):
            yield value
        for child in value.values():
            yield from _walk_spans(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_spans(child)


def validate_fact_coordinates(
    record: Mapping[str, Any], result: Mapping[str, Any]
) -> list[str]:
    documents = _documents(record)
    meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
    errors: list[str] = []
    for span in _walk_spans(result.get("facts") or {}):
        source_kind = span.get("source_kind")
        start, end = span.get("start"), span.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            errors.append(f"invalid_range_type:{source_kind}:{start}:{end}")
            continue
        if source_kind == "document":
            index = span.get("doc_index")
            if not isinstance(index, int) or not (0 <= index < len(documents)):
                errors.append(f"invalid_doc_index:{index}")
                continue
            source = documents[index]["text"]
            source_hash = documents[index]["sha256"]
            if not (0 <= start <= end <= len(source)):
                errors.append(f"invalid_document_range:{index}:{start}:{end}")
                continue
            exact = source[start:end]
            prefix = f"document:{index}:{start}:{end}"
            if span.get("source_doc_sha256") != source_hash:
                errors.append(f"source_hash_mismatch:{prefix}")
        elif source_kind == "meta":
            path = str(span.get("path") or "")
            field = path[5:] if path.startswith("meta.") else ""
            if field not in meta:
                errors.append(f"missing_meta_path:{path}")
                continue
            source = str(meta.get(field) or "")
            if not (0 <= start <= end <= len(source)):
                errors.append(f"invalid_meta_range:{path}:{start}:{end}")
                continue
            exact = source[start:end]
            prefix = f"meta:{field}:{start}:{end}"
            if span.get("source_value_sha256") != sha256_text(source):
                errors.append(f"source_hash_mismatch:{prefix}")
        else:
            errors.append(f"unknown_source_kind:{source_kind}")
            continue
        if span.get("text") != exact:
            errors.append(f"text_mismatch:{prefix}")
        if span.get("text_sha256") != sha256_text(exact):
            errors.append(f"text_hash_mismatch:{prefix}")
    return errors


def _coverage_signal(result: Mapping[str, Any], item: str) -> bool:
    facts = result.get("facts") or {}
    meta = result.get("metadata_facts") or {}
    code = bool(facts.get("bid_product_code_mentions")) or bool(
        facts.get("catalog_product_name_candidates")
    )
    direct = bool(facts.get("direct_production_requirements"))
    enterprise = bool(facts.get("enterprise_size_clauses"))
    price = meta.get("estimated_price_won", {}).get("status") == "present"
    software = bool(facts.get("software_object_task_relations"))
    mapping = {
        "v10": code,
        "v11": code,
        "v12": direct,
        "v13": code and enterprise,
        "v14": price and enterprise,
        "v15": price and enterprise,
        "v16": price,
        "v17": price and enterprise,
        "v18": price,
        "v20": software,
    }
    return bool(mapping[item])


def _empty_role_counters() -> dict[str, Counter[str]]:
    return {
        "direct": Counter(),
        "enterprise": Counter(),
        "software": Counter(),
        "restrictions": Counter(),
        "catalog_names": Counter(),
    }


def _accumulate_role_counts(
    counters: Mapping[str, Counter[str]], result: Mapping[str, Any]
) -> None:
    facts = result.get("facts") or {}
    for fact in facts.get("direct_production_requirements") or []:
        counters["direct"][str(fact.get("clause_role") or "missing")] += 1
    for fact in facts.get("enterprise_size_clauses") or []:
        counters["enterprise"][str(fact.get("clause_role") or "missing")] += 1
    for fact in facts.get("software_object_task_relations") or []:
        counters["software"][str(fact.get("relation_role") or "missing")] += 1
    for fact in facts.get("large_enterprise_software_restrictions") or []:
        counters["restrictions"][str(fact.get("clause_role") or "missing")] += 1
    for fact in facts.get("catalog_product_name_candidates") or []:
        basis = (
            "exact_single_catalog_identity"
            if fact.get("exact_catalog_identity")
            else "ambiguous_name_family_or_multirow"
        )
        counters["catalog_names"][basis] += 1


def _render_role_counts(counters: Mapping[str, Counter[str]]) -> dict[str, Any]:
    return {
        "meaning": (
            "Counts of operative versus generic/list/post-award/ambiguous candidates. "
            "Non-operative and unresolved counts are explicit false-positive risks "
            "if a downstream consumer naively treats keyword presence as a finding."
        ),
        "direct_production_clause_roles": dict(sorted(counters["direct"].items())),
        "enterprise_clause_roles": dict(sorted(counters["enterprise"].items())),
        "software_relation_roles": dict(sorted(counters["software"].items())),
        "large_enterprise_restriction_roles": dict(
            sorted(counters["restrictions"].items())
        ),
        "catalog_name_identification_strength": dict(
            sorted(counters["catalog_names"].items())
        ),
    }


def _review_risk_role_counts(
    extracted: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize why raw keyword presence must not be promoted to a label."""

    counters = _empty_role_counters()
    for result in extracted:
        _accumulate_role_counts(counters, result)
    return _render_role_counts(counters)


def audit_dev_coverage(
    dev_path: pathlib.Path | str,
    labels_path: pathlib.Path | str,
    *,
    catalog: CatalogReference | None = None,
) -> dict[str, Any]:
    """Audit positive premise coverage and negative review-candidate rates.

    Labels are read only here, never by :func:`extract_qualification_facts`.
    ``negative_candidate_rate`` is a review-load/false-positive-risk proxy, not
    the false-positive rate of a classifier because this module makes no label
    decision.
    """

    catalog = catalog or CatalogReference.load()
    records = {row["id"]: row for row in read_jsonl_gz(dev_path)}
    with pathlib.Path(labels_path).open("r", encoding="utf-8-sig", newline="") as handle:
        labels = list(csv.DictReader(handle))

    by_item: dict[str, dict[str, Any]] = {}
    coordinate_errors: list[dict[str, Any]] = []
    extracted: dict[str, dict[str, Any]] = {}
    for record_id, record in records.items():
        result = extract_qualification_facts(record, catalog=catalog)
        extracted[record_id] = result
        errors = validate_fact_coordinates(record, result)
        if errors:
            coordinate_errors.append({"id": record_id, "errors": errors})

    for item in TARGET_ITEMS:
        positives = [row["id"] for row in labels if row.get(item) == "1"]
        negatives = [row["id"] for row in labels if row.get(item) == "0"]
        covered = [rid for rid in positives if _coverage_signal(extracted[rid], item)]
        negative_candidates = [
            rid for rid in negatives if _coverage_signal(extracted[rid], item)
        ]
        by_item[item] = {
            "official_positive_records": len(positives),
            "positive_premise_covered": len(covered),
            "positive_coverage": len(covered) / len(positives) if positives else 1.0,
            "missed_positive_ids": sorted(set(positives) - set(covered)),
            "official_negative_records": len(negatives),
            "negative_review_candidates": len(negative_candidates),
            "negative_candidate_rate": len(negative_candidates) / len(negatives)
            if negatives
            else 0.0,
            "negative_candidate_rate_meaning": "review-load/false-positive-risk proxy; no label was predicted",
        }
    return {
        "schema_version": f"{SCHEMA_VERSION}.dev_audit",
        "records": len(records),
        "labels_rows": len(labels),
        "target_items": list(TARGET_ITEMS),
        "by_item": by_item,
        "all_positive_records": sum(
            values["official_positive_records"] for values in by_item.values()
        ),
        "all_positive_premises_covered": sum(
            values["positive_premise_covered"] for values in by_item.values()
        ),
        "coordinate_error_records": coordinate_errors,
        "review_risk_role_counts": _review_risk_role_counts(extracted.values()),
        "label_values_used_for_extraction": False,
    }


def audit_records(
    records: Iterable[Mapping[str, Any]],
    *,
    catalog: CatalogReference | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Label-free smoke statistics for an arbitrary organizer-record stream."""

    catalog = catalog or CatalogReference.load()
    count = 0
    records_with: Counter[str] = Counter()
    total_facts: Counter[str] = Counter()
    ambiguities: Counter[str] = Counter()
    coordinate_error_records = 0
    role_counters = _empty_role_counters()
    for record in records:
        if limit is not None and count >= limit:
            break
        result = extract_qualification_facts(record, catalog=catalog)
        _accumulate_role_counts(role_counters, result)
        count += 1
        if validate_fact_coordinates(record, result):
            coordinate_error_records += 1
        for name, value in (result.get("fact_counts") or {}).items():
            total_facts[name] += int(value)
            if int(value) > 0:
                records_with[name] += 1
        for ambiguity in result.get("ambiguities") or []:
            ambiguities[str(ambiguity.get("code"))] += 1
    return {
        "schema_version": f"{SCHEMA_VERSION}.record_audit",
        "records": count,
        "records_with_fact_family": dict(sorted(records_with.items())),
        "total_facts": dict(sorted(total_facts.items())),
        "record_ambiguities": dict(sorted(ambiguities.items())),
        "coordinate_error_records": coordinate_error_records,
        "review_risk_role_counts": _render_role_counts(role_counters),
        "catalog_sha256": catalog.sha256,
        "label_values_read": False,
    }


def _extract_cli(args: argparse.Namespace) -> int:
    catalog = CatalogReference.load(args.catalog)
    output = pathlib.Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for record in read_jsonl_gz(args.input):
            if args.limit is not None and written >= args.limit:
                break
            result = extract_qualification_facts(record, catalog=catalog)
            errors = validate_fact_coordinates(record, result)
            if errors:
                raise ValueError(f"{record.get('id')}: coordinate errors: {errors[:5]}")
            handle.write(
                json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            )
            written += 1
    print(json.dumps({"written": written, "output": str(output)}, ensure_ascii=False))
    return 0


def _audit_dev_cli(args: argparse.Namespace) -> int:
    report = audit_dev_coverage(args.input, args.labels, catalog=CatalogReference.load(args.catalog))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = pathlib.Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


def _smoke_cli(args: argparse.Namespace) -> int:
    report = audit_records(
        read_jsonl_gz(args.input),
        catalog=CatalogReference.load(args.catalog),
        limit=args.limit,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = pathlib.Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    extract = subparsers.add_parser("extract", help="write independent facts as JSONL")
    extract.add_argument("--input", type=pathlib.Path, required=True)
    extract.add_argument("--output", type=pathlib.Path, required=True)
    extract.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG_PATH)
    extract.add_argument("--limit", type=int)
    extract.set_defaults(handler=_extract_cli)

    audit = subparsers.add_parser("audit-dev", help="audit official-dev premise coverage")
    audit.add_argument("--input", type=pathlib.Path, required=True)
    audit.add_argument("--labels", type=pathlib.Path, required=True)
    audit.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG_PATH)
    audit.add_argument("--output", type=pathlib.Path)
    audit.set_defaults(handler=_audit_dev_cli)

    smoke = subparsers.add_parser("smoke", help="label-free extraction statistics")
    smoke.add_argument("--input", type=pathlib.Path, required=True)
    smoke.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG_PATH)
    smoke.add_argument("--limit", type=int, default=1_000)
    smoke.add_argument("--output", type=pathlib.Path)
    smoke.set_defaults(handler=_smoke_cli)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CatalogReference",
    "DEFAULT_CATALOG_PATH",
    "SCHEMA_VERSION",
    "TARGET_ITEMS",
    "audit_dev_coverage",
    "audit_records",
    "extract_facts",
    "extract_qualification_facts",
    "main",
    "read_jsonl_gz",
    "sha256_object",
    "sha256_text",
    "validate_fact_coordinates",
]
