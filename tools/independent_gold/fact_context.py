"""Bounded, source-coordinate fact context for independent annotation.

The builder consumes an organizer record and the outputs of ``legal_facts``
and ``catalog_facts``.  It does not make any of the 24 item decisions.  Long
source excerpts are pooled into exact, hash-checked segments so several facts
can point at one copy of the text.  If a context exceeds its character budget,
whole facts are omitted (and counted) rather than cutting a quotation or
silently changing a coordinate.

Typical integration::

    prepared = prepare_fact_inputs(record, catalog_index=catalog)
    context = build_fact_context(
        record, "v1-4", prepared=prepared, catalog_index=catalog
    )
    prompt_fragment = render_fact_context(context)

``prepare_fact_inputs`` should be called once per record and its result reused
for all eight groups.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

try:
    from tools.independent_gold import catalog_facts, legal_facts
except ModuleNotFoundError:  # Direct execution from this directory.
    import catalog_facts  # type: ignore[no-redef]
    import legal_facts  # type: ignore[no-redef]


SCHEMA_VERSION = "dacon.independent.fact_context.v4"
DEFAULT_MAX_CHARS = 9_000

GROUPS: dict[str, tuple[str, ...]] = {
    "v1-4": ("v1", "v2", "v3", "v4"),
    "v5-8": ("v5", "v6", "v7", "v8"),
    "v9": ("v9",),
    "v10-13": ("v10", "v11", "v12", "v13"),
    "v14-19": ("v14", "v15", "v16", "v17", "v18", "v19"),
    "v20": ("v20",),
    "v21-23": ("v21", "v22", "v23"),
    "v24": ("v24",),
}
# v5 also needs the organizer-supplied competitive-product catalog.  In a
# local goods/general-service procurement, an exact applicable competitive
# product that is operatively tied to the current procurement can establish
# the Local Contracts Act Article 5(1)(2) exclusion branch.  The context keeps
# the exact mention surroundings so the annotator can distinguish that link
# from a historical example or an unrelated code occurrence.
CATALOG_ITEMS = frozenset(("v5", *(f"v{index}" for index in range(10, 19))))

# Organizer metadata is retained only where it can be a premise for the group.
ITEM_METADATA: dict[str, tuple[str, ...]] = {
    "v1": ("contract_law", "work_type", "industry_codes", "industry_restricted"),
    "v2": ("contract_law", "work_type", "contract_method", "budget_won", "estimated_price_won"),
    "v3": ("contract_law", "work_type", "budget_won", "estimated_price_won"),
    "v4": ("contract_law", "work_type"),
    "v5": ("contract_law", "work_type", "contract_method", "estimated_price_won", "region_codes", "region_restricted", "catalog_codes"),
    "v6": ("contract_law", "work_type", "contract_method", "estimated_price_won", "region_codes", "region_restricted"),
    "v7": ("contract_law", "work_type", "contract_method", "estimated_price_won", "region_codes", "region_restricted"),
    "v8": ("contract_law", "work_type", "contract_method", "estimated_price_won", "region_codes", "region_restricted"),
    "v9": ("work_type", "catalog_codes"),
    "v10": ("contract_law", "work_type", "contract_method", "budget_won", "estimated_price_won", "catalog_codes"),
    "v11": ("contract_law", "work_type", "contract_method", "budget_won", "estimated_price_won", "catalog_codes"),
    "v12": ("contract_law", "work_type", "contract_method", "budget_won", "estimated_price_won", "catalog_codes"),
    "v13": ("contract_law", "work_type", "contract_method", "budget_won", "estimated_price_won", "catalog_codes"),
    "v14": ("contract_law", "work_type", "contract_method", "budget_won", "estimated_price_won", "catalog_codes"),
    "v15": ("contract_law", "work_type", "contract_method", "budget_won", "estimated_price_won", "catalog_codes"),
    "v16": ("contract_law", "work_type", "contract_method", "budget_won", "estimated_price_won", "catalog_codes"),
    "v17": ("contract_law", "work_type", "contract_method", "budget_won", "estimated_price_won", "catalog_codes"),
    "v18": ("contract_law", "work_type", "contract_method", "budget_won", "estimated_price_won", "catalog_codes"),
    "v19": ("work_type",),
    "v20": ("work_type", "information_project", "budget_won", "estimated_price_won"),
    "v21": ("contract_law", "work_type", "joint_method", "budget_won", "estimated_price_won"),
    "v22": ("award_method", "contract_method", "posted_date", "opening_date"),
    "v23": ("contract_law", "award_method", "contract_method", "estimated_price_won", "posted_date", "opening_date", "urgent"),
    "v24": tuple(legal_facts.META_FIELDS),
}

# Relevance is intentionally a little wider than the extractor's original
# support_items.  For example, a joint-contract source fact is also necessary
# when v24 compares the notice with organizer metadata.
CATEGORY_ITEMS: dict[str, frozenset[str]] = {
    "institution_qualification_restrictions": frozenset(("v1",)),
    "performance_requirements": frozenset(("v2", "v3", "v4", "v8")),
    "headquarters_region_requirements": frozenset(("v5", "v6", "v7", "v8", "v24")),
    "specific_model_candidates": frozenset(("v9",)),
    "third_party_pledges": frozenset(("v19",)),
    "joint_contract_terms": frozenset(("v21", "v24")),
    "briefings": frozenset(("v22", "v23")),
    "law_mentions": frozenset(
        ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17", "v18", "v21", "v23")
    ),
    "document_amount_claims": frozenset(
        ("v2", "v3", "v5", "v6", "v7", "v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17", "v18", "v20", "v23", "v24")
    ),
    "document_contract_method_claims": frozenset(("v24",)),
    "document_award_method_claims": frozenset(("v22", "v23", "v24")),
    "document_work_type_claims": frozenset(
        ("v2", "v3", "v4", "v5", "v6", "v7", "v8", "v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17", "v18", "v20", "v24")
    ),
    "document_industry_claims": frozenset(("v24",)),
}

# Lower numbers are retained first when the hard budget requires omissions.
CATEGORY_PRIORITY: dict[str, int] = {
    "institution_qualification_restrictions": 0,
    "performance_requirements": 0,
    "headquarters_region_requirements": 0,
    "specific_model_candidates": 0,
    "third_party_pledges": 0,
    "joint_contract_terms": 0,
    "briefings": 0,
    "document_contract_method_claims": 0,
    "document_award_method_claims": 0,
    "document_industry_claims": 0,
    "document_amount_claims": 1,
    "document_work_type_claims": 2,
    "law_mentions": 3,
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_object(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _documents(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for index, raw in enumerate(record.get("docs") or []):
        text = str(raw.get("text") or "")
        documents.append(
            {
                "doc_index": index,
                "doc_id": str(raw.get("doc_id") or f"D{index}"),
                "doc_type": str(raw.get("type") or "unknown"),
                "chars": len(text),
                "sha256": sha256_text(text),
                "text": text,
            }
        )
    return documents


def prepare_fact_inputs(
    record: Mapping[str, Any],
    *,
    catalog_index: catalog_facts.CatalogIndex | None = None,
) -> dict[str, Any]:
    """Extract both fact families once for reuse across all item groups."""

    catalog_index = catalog_index or catalog_facts.CatalogIndex.load()
    return {
        "legal": legal_facts.extract_legal_facts(record),
        "catalog": catalog_facts.resolve_record(record, catalog_index),
    }


def _meta_value(record: Mapping[str, Any], field: str) -> str:
    meta = record.get("meta") or {}
    if not isinstance(meta, Mapping):
        return ""
    return str(meta.get(field) if field in meta else "")


def validate_catalog_coordinates(
    record: Mapping[str, Any],
    result: Mapping[str, Any],
    catalog_index: catalog_facts.CatalogIndex,
) -> list[str]:
    """Validate all organizer and supplied-catalog coordinates in a result."""

    errors: list[str] = []
    documents = _documents(record)
    if result.get("id") != str(record.get("id") or ""):
        errors.append("catalog_record_id_mismatch")
    if result.get("source_sha256") != sha256_object(record):
        errors.append("catalog_record_hash_mismatch")
    if (result.get("catalog") or {}).get("sha256") != catalog_index.sha256:
        errors.append("catalog_source_hash_mismatch")

    def check_mention(mention: Mapping[str, Any], prefix: str) -> None:
        source_kind = mention.get("source_kind")
        start, end = mention.get("start"), mention.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            errors.append(f"{prefix}:invalid_range_type")
            return
        if source_kind == "document":
            index = mention.get("doc_index")
            if not isinstance(index, int) or not (0 <= index < len(documents)):
                errors.append(f"{prefix}:invalid_doc_index")
                return
            document = documents[index]
            text = document["text"]
            if not (0 <= start <= end <= len(text)):
                errors.append(f"{prefix}:invalid_range")
                return
            if text[start:end] != mention.get("raw"):
                errors.append(f"{prefix}:quote_mismatch")
            if mention.get("doc_sha256") != document["sha256"]:
                errors.append(f"{prefix}:document_hash_mismatch")
            context_start = mention.get("context_start")
            context_end = mention.get("context_end")
            if not (
                isinstance(context_start, int)
                and isinstance(context_end, int)
                and 0 <= context_start <= start <= end <= context_end <= len(text)
            ):
                errors.append(f"{prefix}:invalid_context_range")
            elif text[context_start:context_end] != mention.get("context"):
                errors.append(f"{prefix}:context_mismatch")
        elif source_kind == "meta":
            field = mention.get("field")
            if not isinstance(field, str):
                errors.append(f"{prefix}:invalid_meta_field")
                return
            value = _meta_value(record, field)
            if not (0 <= start <= end <= len(value)):
                errors.append(f"{prefix}:invalid_meta_range")
            elif value[start:end] != mention.get("raw"):
                errors.append(f"{prefix}:meta_quote_mismatch")
        else:
            errors.append(f"{prefix}:unknown_source_kind")

    for code_index, code_fact in enumerate(result.get("codes") or []):
        code = str(code_fact.get("code") or "")
        for mention_index, mention in enumerate(code_fact.get("mentions") or []):
            check_mention(mention, f"code:{code_index}:mention:{mention_index}")
        for clause_index, clause in enumerate(
            (code_fact.get("condition_evaluation") or {}).get("clause_evaluations") or []
        ):
            for evidence_index, evidence in enumerate(clause.get("nonprice_evidence") or []):
                check_mention(
                    evidence,
                    f"code:{code_index}:condition:{clause_index}:evidence:{evidence_index}",
                )
        row = code_fact.get("catalog_row")
        if row is None:
            if code in catalog_index:
                errors.append(f"code:{code}:missing_registered_row")
            continue
        expected = catalog_index.get(code)
        if expected is None:
            errors.append(f"code:{code}:unexpected_catalog_row")
            continue
        if row.get("catalog_coordinate") != expected.get("catalog_coordinate"):
            errors.append(f"code:{code}:catalog_coordinate_mismatch")
        for key in catalog_facts.CATALOG_COLUMNS:
            if row.get(key) != expected.get(key):
                errors.append(f"code:{code}:catalog_value_mismatch:{key}")
        coordinate = (row.get("condition") or {}).get("source_coordinate")
        if coordinate:
            note = str(expected.get("특이사항") or "")
            if coordinate.get("quote") != note:
                errors.append(f"code:{code}:catalog_note_quote_mismatch")
            if coordinate.get("start") != 0 or coordinate.get("end") != len(note):
                errors.append(f"code:{code}:catalog_note_range_mismatch")

    for token_index, token in enumerate(result.get("ambiguous_ten_digit_tokens") or []):
        check_mention(token, f"ambiguous:{token_index}")
    return errors


def _requested_metadata(
    target_items: Sequence[str], legal_result: Mapping[str, Any]
) -> dict[str, Any]:
    names: list[str] = []
    for semantic_name in legal_facts.META_FIELDS:
        if any(semantic_name in ITEM_METADATA[item] for item in target_items):
            names.append(semantic_name)
    source = legal_result.get("metadata_facts") or {}
    return {name: copy.deepcopy(source[name]) for name in names if name in source}


def _fact_sort_key(fact: Mapping[str, Any]) -> tuple[Any, ...]:
    span = fact.get("span") or {}
    scope_rank = 0 if fact.get("scope") == "bid_qualification" else 1
    ambiguity_rank = 0 if not fact.get("ambiguity") else 1
    return (
        scope_rank,
        ambiguity_rank,
        int(span.get("doc_index", 10**9)),
        int(span.get("start", 10**9)),
        int(span.get("end", 10**9)) - int(span.get("start", 0)),
        str(fact.get("fact_id") or ""),
    )


def _compact_legal_fact(
    category: str, fact: Mapping[str, Any], target_items: Sequence[str]
) -> dict[str, Any]:
    span = fact.get("span") or {}
    attributes = {
        key: copy.deepcopy(value)
        for key, value in fact.items()
        if key not in {"fact_id", "kind", "support_items", "span", "subject_text"}
    }
    relevant = sorted(set(target_items) & CATEGORY_ITEMS[category])
    return {
        "fact_id": str(fact.get("fact_id") or ""),
        "category": category,
        "kind": str(fact.get("kind") or ""),
        "relevant_items": relevant,
        "source_coordinate": {
            "doc_index": span.get("doc_index"),
            "doc_id": span.get("doc_id"),
            "doc_type": span.get("doc_type"),
            "start": span.get("start"),
            "end": span.get("end"),
            "quote_sha256": span.get("text_sha256"),
            "source_doc_sha256": span.get("source_doc_sha256"),
            "matched_by": copy.deepcopy(span.get("matched_by") or []),
        },
        "attributes": attributes,
    }


def _legal_buckets(
    target_items: Sequence[str], legal_result: Mapping[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    requested = set(target_items)
    categories = legal_result.get("facts") or {}
    buckets: dict[str, list[dict[str, Any]]] = {}
    for category, relevant_items in CATEGORY_ITEMS.items():
        if not requested.intersection(relevant_items):
            continue
        facts = sorted(categories.get(category) or [], key=_fact_sort_key)
        buckets[category] = [
            _compact_legal_fact(category, fact, target_items) for fact in facts
        ]
    return buckets


def _compact_catalog_mention(mention: Mapping[str, Any]) -> dict[str, Any]:
    common = {
        "code": mention.get("code"),
        "raw": mention.get("raw"),
        "mention_form": mention.get("mention_form"),
        "identification_basis": mention.get("identification_basis"),
        "source_kind": mention.get("source_kind"),
    }
    if mention.get("source_kind") == "document":
        common["source_coordinate"] = {
            "doc_index": mention.get("doc_index"),
            "doc_id": mention.get("doc_id"),
            "doc_type": mention.get("doc_type"),
            "start": mention.get("start"),
            "end": mention.get("end"),
            "quote_sha256": sha256_text(str(mention.get("raw") or "")),
            "source_doc_sha256": mention.get("doc_sha256"),
            "context_start": mention.get("context_start"),
            "context_end": mention.get("context_end"),
            "context_sha256": sha256_text(str(mention.get("context") or "")),
        }
    else:
        value = str(mention.get("value") or "")
        common["source_coordinate"] = {
            "field": mention.get("field"),
            "path": mention.get("path"),
            "start": mention.get("start"),
            "end": mention.get("end"),
            "quote": mention.get("raw"),
            "value_sha256": sha256_text(value),
        }
    return common


def _compact_catalog_code(code_fact: Mapping[str, Any]) -> dict[str, Any]:
    row = code_fact.get("catalog_row") or {}
    condition = row.get("condition")
    condition_evaluation = copy.deepcopy(code_fact.get("condition_evaluation"))
    for clause in (condition_evaluation or {}).get("clause_evaluations") or []:
        clause["nonprice_evidence"] = [
            _compact_catalog_mention(evidence)
            for evidence in clause.get("nonprice_evidence") or []
        ]
    return {
        "code": code_fact.get("code"),
        "applicability": code_fact.get("decision"),
        "reason_code": code_fact.get("reason_code"),
        "catalog_registered": bool(code_fact.get("catalog_registered")),
        "catalog_identity": (
            {
                "major_category_number": row.get("대분류번호"),
                "major_category": row.get("대분류"),
                "product_number": row.get("제품명번호"),
                "product": row.get("제품명"),
                "detail_number": row.get("세부품명번호"),
                "detail": row.get("세부품명"),
                "direct_purchase_material": row.get("공사용자재직접구매"),
                "catalog_coordinate": copy.deepcopy(row.get("catalog_coordinate")),
            }
            if row
            else None
        ),
        "special_condition": copy.deepcopy(condition),
        "condition_evaluation": condition_evaluation,
        "mentions": [
            _compact_catalog_mention(mention)
            for mention in code_fact.get("mentions") or []
        ],
    }


def _compact_ambiguous_token(token: Mapping[str, Any]) -> dict[str, Any]:
    compact = _compact_catalog_mention(token)
    compact["ambiguity"] = "ten_digit_token_without_safe_product_code_basis"
    return compact


def _catalog_index_summary(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "code": code.get("code"),
            "applicability": code.get("decision"),
            "reason_code": code.get("reason_code"),
            "catalog_registered": bool(code.get("catalog_registered")),
            "mention_count": len(code.get("mentions") or []),
        }
        for code in result.get("codes") or []
    ]


def _candidate_sequence(
    legal_buckets: Mapping[str, Sequence[dict[str, Any]]],
    catalog_result: Mapping[str, Any] | None,
) -> list[tuple[str, str, dict[str, Any]]]:
    buckets: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    priorities: dict[str, int] = {}
    for category, facts in legal_buckets.items():
        buckets[category] = [("legal", category, fact) for fact in facts]
        priorities[category] = CATEGORY_PRIORITY[category]
    if catalog_result is not None:
        buckets["catalog_codes"] = [
            ("catalog_code", "catalog_codes", _compact_catalog_code(code))
            for code in catalog_result.get("codes") or []
        ]
        buckets["catalog_ambiguous"] = [
            ("catalog_ambiguous", "catalog_ambiguous", _compact_ambiguous_token(token))
            for token in catalog_result.get("ambiguous_ten_digit_tokens") or []
        ]
        priorities["catalog_codes"] = 0
        priorities["catalog_ambiguous"] = 4

    sequence: list[tuple[str, str, dict[str, Any]]] = []
    for priority in sorted(set(priorities.values())):
        names = sorted(
            (name for name, value in priorities.items() if value == priority),
            key=lambda name: (name.startswith("catalog_ambiguous"), name),
        )
        depth = 0
        while True:
            added = False
            for name in names:
                if depth < len(buckets[name]):
                    sequence.append(buckets[name][depth])
                    added = True
            if not added:
                break
            depth += 1
    return sequence


def _source_segments(
    documents: Sequence[Mapping[str, Any]],
    legal_rows: Sequence[dict[str, Any]],
    catalog_rows: Sequence[dict[str, Any]],
    ambiguous_rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    coordinate_refs: list[tuple[dict[str, Any], int, int, int]] = []
    for fact in legal_rows:
        coordinate = fact["source_coordinate"]
        coordinate_refs.append(
            (coordinate, int(coordinate["doc_index"]), int(coordinate["start"]), int(coordinate["end"]))
        )
    for row in (*catalog_rows, *ambiguous_rows):
        mentions = row.get("mentions") if "mentions" in row else (row,)
        for mention in mentions or []:
            if mention.get("source_kind") != "document":
                continue
            coordinate = mention["source_coordinate"]
            coordinate_refs.append(
                (
                    coordinate,
                    int(coordinate["doc_index"]),
                    int(coordinate["context_start"]),
                    int(coordinate["context_end"]),
                )
            )
        for clause in (row.get("condition_evaluation") or {}).get("clause_evaluations") or []:
            for evidence in clause.get("nonprice_evidence") or []:
                if evidence.get("source_kind") != "document":
                    continue
                coordinate = evidence["source_coordinate"]
                coordinate_refs.append(
                    (
                        coordinate,
                        int(coordinate["doc_index"]),
                        int(coordinate["context_start"]),
                        int(coordinate["context_end"]),
                    )
                )

    merged: list[dict[str, int]] = []
    for _, doc_index, start, end in sorted(coordinate_refs, key=lambda row: row[1:]):
        if (
            merged
            and merged[-1]["doc_index"] == doc_index
            and start <= merged[-1]["end"] + 32
            and max(end, merged[-1]["end"]) - merged[-1]["start"] <= 2_000
        ):
            merged[-1]["end"] = max(end, merged[-1]["end"])
        else:
            merged.append({"doc_index": doc_index, "start": start, "end": end})

    segments: list[dict[str, Any]] = []
    for bounds in merged:
        document = documents[bounds["doc_index"]]
        quote = str(document["text"])[bounds["start"] : bounds["end"]]
        segment_id = f"D{bounds['doc_index']}:{bounds['start']}:{bounds['end']}"
        segments.append(
            {
                "segment_id": segment_id,
                "doc_index": bounds["doc_index"],
                "doc_id": document["doc_id"],
                "doc_type": document["doc_type"],
                "start": bounds["start"],
                "end": bounds["end"],
                "quote": quote,
                "quote_sha256": sha256_text(quote),
                "source_doc_sha256": document["sha256"],
            }
        )

    for coordinate, doc_index, start, end in coordinate_refs:
        containing = next(
            segment
            for segment in segments
            if segment["doc_index"] == doc_index
            and segment["start"] <= start
            and end <= segment["end"]
        )
        coordinate["segment_id"] = containing["segment_id"]
    return segments


def _hashable_context(context: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(context))
    payload.pop("context_sha256", None)
    bounds = payload.get("bounds")
    if isinstance(bounds, dict):
        bounds.pop("rendered_chars", None)
    return payload


def _finalize_context(context: dict[str, Any]) -> dict[str, Any]:
    context["context_sha256"] = sha256_object(_hashable_context(context))
    context["bounds"]["rendered_chars"] = 0
    for _ in range(6):
        rendered_chars = len(canonical_json(context))
        if context["bounds"]["rendered_chars"] == rendered_chars:
            break
        context["bounds"]["rendered_chars"] = rendered_chars
    return context


def _build_selected_context(
    record: Mapping[str, Any],
    group_name: str,
    target_items: Sequence[str],
    legal_result: Mapping[str, Any],
    catalog_result: Mapping[str, Any] | None,
    catalog_index: catalog_facts.CatalogIndex | None,
    legal_buckets: Mapping[str, Sequence[dict[str, Any]]],
    candidates: Sequence[tuple[str, str, dict[str, Any]]],
    selected_count: int,
    max_chars: int,
) -> dict[str, Any]:
    documents = _documents(record)
    selected = candidates[:selected_count]
    selected_legal = [copy.deepcopy(row) for kind, _, row in selected if kind == "legal"]
    selected_catalog = [
        copy.deepcopy(row) for kind, _, row in selected if kind == "catalog_code"
    ]
    selected_ambiguous = [
        copy.deepcopy(row) for kind, _, row in selected if kind == "catalog_ambiguous"
    ]
    source_segments = _source_segments(
        documents, selected_legal, selected_catalog, selected_ambiguous
    )

    included_by_bucket: dict[str, int] = defaultdict(int)
    for _, bucket, _ in selected:
        included_by_bucket[bucket] += 1
    legal_omissions = {
        category: {
            "available": len(rows),
            "included": included_by_bucket.get(category, 0),
            "omitted": len(rows) - included_by_bucket.get(category, 0),
        }
        for category, rows in legal_buckets.items()
    }

    source_manifest = [
        {key: document[key] for key in ("doc_index", "doc_id", "doc_type", "chars", "sha256")}
        for document in documents
    ]
    catalog_requested = catalog_result is not None
    catalog_available = len((catalog_result or {}).get("codes") or [])
    ambiguous_available = len(
        (catalog_result or {}).get("ambiguous_ten_digit_tokens") or []
    )
    catalog_payload: dict[str, Any]
    if catalog_requested:
        catalog_payload = {
            "status": "included",
            "interpretation_boundary": (
                "catalog applicability evaluates the supplied catalog row only; "
                "it does not by itself establish that a code is operatively tied "
                "to the current procurement. Determine that link from each exact "
                "mention and its source segment."
            ),
            "summary": {
                "applicability": (catalog_result.get("summary") or {}).get("decision"),
                "reason_code": (catalog_result.get("summary") or {}).get("reason_code"),
                "mixed_product_codes": bool(
                    (catalog_result.get("summary") or {}).get("mixed_product_codes")
                ),
                "unique_code_count": (catalog_result.get("summary") or {}).get("unique_code_count"),
            },
            "code_index": _catalog_index_summary(catalog_result),
            "code_details": selected_catalog,
            "ambiguous_ten_digit_tokens": selected_ambiguous,
        }
    else:
        catalog_payload = {"status": "not_relevant_to_target_items"}

    context: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "semantic_role": "source_grounded_facts_only",
        "record_id": str(record.get("id") or ""),
        "group": group_name,
        "target_items": list(target_items),
        "organizer_source": {
            "record_sha256": sha256_object(record),
            "documents": source_manifest,
        },
        "provenance": {
            "legal_extractor": {
                "schema_version": legal_result.get("schema_version"),
                "output_sha256": sha256_object(legal_result),
            },
            "catalog_extractor": (
                {
                    "schema_version": catalog_result.get("schema_version"),
                    "output_sha256": sha256_object(catalog_result),
                    "catalog_path": (catalog_result.get("catalog") or {}).get("path"),
                    "catalog_sha256": (catalog_result.get("catalog") or {}).get("sha256"),
                }
                if catalog_requested
                else {"status": "not_relevant_to_target_items"}
            ),
        },
        "source_completeness": copy.deepcopy(legal_result.get("source_completeness") or {}),
        "extractor_ambiguities": copy.deepcopy(legal_result.get("ambiguities") or []),
        "metadata_facts": _requested_metadata(target_items, legal_result),
        "legal_facts": selected_legal,
        "catalog_facts": catalog_payload,
        "source_segments": source_segments,
        "omissions": {
            "legal": legal_omissions,
            "catalog": {
                "available_code_details": catalog_available,
                "included_code_details": len(selected_catalog),
                "omitted_code_details": catalog_available - len(selected_catalog),
                "available_ambiguous_tokens": ambiguous_available,
                "included_ambiguous_tokens": len(selected_ambiguous),
                "omitted_ambiguous_tokens": ambiguous_available - len(selected_ambiguous),
            },
            "context_char_budget_truncated": selected_count < len(candidates),
            "omitted_relevant_fact_count": len(candidates) - selected_count,
        },
        "bounds": {
            "max_chars": max_chars,
            "rendered_chars": 0,
            "selected_atomic_facts": selected_count,
            "available_atomic_facts": len(candidates),
        },
    }
    # Keep this assertion near construction: accidentally adding a decision
    # vector to the context should fail before it can reach an annotator.
    if _contains_forbidden_key(context, "labels"):
        raise ValueError("fact context unexpectedly contains a labels key")
    return _finalize_context(context)


def _contains_forbidden_key(value: Any, forbidden: str) -> bool:
    if isinstance(value, Mapping):
        return forbidden in value or any(
            _contains_forbidden_key(child, forbidden) for child in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_key(child, forbidden) for child in value)
    return False


def build_fact_context(
    record: Mapping[str, Any],
    group_name: str,
    *,
    prepared: Mapping[str, Any] | None = None,
    legal_result: Mapping[str, Any] | None = None,
    catalog_result: Mapping[str, Any] | None = None,
    catalog_index: catalog_facts.CatalogIndex | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """Build one deterministic, bounded context for an annotation group.

    ``prepared`` is the JSON-serializable result of :func:`prepare_fact_inputs`.
    Callers may instead pass the two extractor outputs separately.  A context
    never truncates a source quote: it drops complete low-priority facts and
    reports their counts in ``omissions``.
    """

    if group_name not in GROUPS:
        raise ValueError(f"unknown group: {group_name}")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
        raise ValueError("max_chars must be a positive integer")
    if prepared is not None and (legal_result is not None or catalog_result is not None):
        raise ValueError("pass prepared or individual extractor outputs, not both")
    if prepared is not None:
        legal_result = prepared.get("legal")
        catalog_result = prepared.get("catalog")
    if legal_result is None:
        legal_result = legal_facts.extract_legal_facts(record)

    record_hash = sha256_object(record)
    if legal_result.get("record_id") != str(record.get("id") or ""):
        raise ValueError("legal fact record id does not match organizer record")
    if legal_result.get("source_sha256") != record_hash:
        raise ValueError("legal fact source hash does not match organizer record")
    legal_coordinate_errors = legal_facts.validate_fact_coordinates(record, legal_result)
    if legal_coordinate_errors:
        raise ValueError(f"invalid legal fact coordinates: {legal_coordinate_errors[:3]}")

    target_items = GROUPS[group_name]
    uses_catalog = bool(set(target_items) & CATALOG_ITEMS)
    if uses_catalog:
        catalog_index = catalog_index or catalog_facts.CatalogIndex.load()
        if catalog_result is None:
            catalog_result = catalog_facts.resolve_record(record, catalog_index)
        catalog_errors = validate_catalog_coordinates(record, catalog_result, catalog_index)
        if catalog_errors:
            raise ValueError(f"invalid catalog fact coordinates: {catalog_errors[:3]}")
    else:
        catalog_result = None

    legal_buckets = _legal_buckets(target_items, legal_result)
    candidates = _candidate_sequence(legal_buckets, catalog_result)

    def assemble(count: int) -> dict[str, Any]:
        return _build_selected_context(
            record,
            group_name,
            target_items,
            legal_result,
            catalog_result,
            catalog_index,
            legal_buckets,
            candidates,
            count,
            max_chars,
        )

    minimal = assemble(0)
    if len(canonical_json(minimal)) > max_chars:
        raise ValueError(
            f"max_chars={max_chars} is smaller than the mandatory context "
            f"({len(canonical_json(minimal))} chars)"
        )
    complete = assemble(len(candidates))
    if len(canonical_json(complete)) <= max_chars:
        return complete

    low, high = 0, len(candidates)
    best = minimal
    while low <= high:
        middle = (low + high) // 2
        candidate = assemble(middle)
        if len(canonical_json(candidate)) <= max_chars:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def render_fact_context(context: Mapping[str, Any]) -> str:
    """Render canonical JSON and enforce the context's declared hard bound."""

    rendered = canonical_json(context)
    max_chars = (context.get("bounds") or {}).get("max_chars")
    if not isinstance(max_chars, int) or len(rendered) > max_chars:
        raise ValueError("fact context exceeds or lacks its declared max_chars")
    return rendered


def _walk_raw_coordinates(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        if {"start", "end", "raw"} <= set(value):
            yield value
        for child in value.values():
            yield from _walk_raw_coordinates(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_raw_coordinates(child)


def validate_fact_context(
    record: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    catalog_index: catalog_facts.CatalogIndex | None = None,
) -> list[str]:
    """Return exact-coordinate, provenance, hash, and size validation errors."""

    errors: list[str] = []
    documents = _documents(record)
    if context.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    if context.get("record_id") != str(record.get("id") or ""):
        errors.append("record_id_mismatch")
    if (context.get("organizer_source") or {}).get("record_sha256") != sha256_object(record):
        errors.append("record_hash_mismatch")
    if _contains_forbidden_key(context, "labels"):
        errors.append("forbidden_labels_key")

    manifest = (context.get("organizer_source") or {}).get("documents") or []
    expected_manifest = [
        {key: doc[key] for key in ("doc_index", "doc_id", "doc_type", "chars", "sha256")}
        for doc in documents
    ]
    if manifest != expected_manifest:
        errors.append("document_manifest_mismatch")

    segments = {row.get("segment_id"): row for row in context.get("source_segments") or []}
    for segment_id, segment in segments.items():
        index, start, end = segment.get("doc_index"), segment.get("start"), segment.get("end")
        if not isinstance(index, int) or not (0 <= index < len(documents)):
            errors.append(f"segment:{segment_id}:invalid_doc_index")
            continue
        text = documents[index]["text"]
        if not (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start <= end <= len(text)
        ):
            errors.append(f"segment:{segment_id}:invalid_range")
            continue
        quote = text[start:end]
        if quote != segment.get("quote"):
            errors.append(f"segment:{segment_id}:quote_mismatch")
        if sha256_text(quote) != segment.get("quote_sha256"):
            errors.append(f"segment:{segment_id}:quote_hash_mismatch")
        if documents[index]["sha256"] != segment.get("source_doc_sha256"):
            errors.append(f"segment:{segment_id}:document_hash_mismatch")

    for fact in context.get("legal_facts") or []:
        coordinate = fact.get("source_coordinate") or {}
        index, start, end = coordinate.get("doc_index"), coordinate.get("start"), coordinate.get("end")
        prefix = f"legal:{fact.get('fact_id')}"
        if not isinstance(index, int) or not (0 <= index < len(documents)):
            errors.append(f"{prefix}:invalid_doc_index")
            continue
        text = documents[index]["text"]
        if not (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start <= end <= len(text)
        ):
            errors.append(f"{prefix}:invalid_range")
            continue
        quote = text[start:end]
        if sha256_text(quote) != coordinate.get("quote_sha256"):
            errors.append(f"{prefix}:quote_hash_mismatch")
        if documents[index]["sha256"] != coordinate.get("source_doc_sha256"):
            errors.append(f"{prefix}:document_hash_mismatch")
        segment = segments.get(coordinate.get("segment_id"))
        if not segment or not (
            segment.get("doc_index") == index
            and segment.get("start") <= start
            and end <= segment.get("end")
        ):
            errors.append(f"{prefix}:segment_reference_mismatch")
        for nested in _walk_raw_coordinates(fact.get("attributes") or {}):
            nested_start, nested_end = nested.get("start"), nested.get("end")
            if not (
                isinstance(nested_start, int)
                and isinstance(nested_end, int)
                and 0 <= nested_start <= nested_end <= len(text)
                and text[nested_start:nested_end] == nested.get("raw")
            ):
                errors.append(f"{prefix}:nested_coordinate_mismatch")

    for detail in (context.get("catalog_facts") or {}).get("code_details") or []:
        code = str(detail.get("code") or "")
        for mention_index, mention in enumerate(detail.get("mentions") or []):
            coordinate = mention.get("source_coordinate") or {}
            prefix = f"catalog:{code}:mention:{mention_index}"
            if mention.get("source_kind") == "document":
                index, start, end = coordinate.get("doc_index"), coordinate.get("start"), coordinate.get("end")
                if not isinstance(index, int) or not (0 <= index < len(documents)):
                    errors.append(f"{prefix}:invalid_doc_index")
                    continue
                text = documents[index]["text"]
                if not (
                    isinstance(start, int)
                    and isinstance(end, int)
                    and 0 <= start <= end <= len(text)
                ):
                    errors.append(f"{prefix}:invalid_range")
                    continue
                quote = text[start:end]
                if quote != mention.get("raw") or sha256_text(quote) != coordinate.get("quote_sha256"):
                    errors.append(f"{prefix}:quote_mismatch")
                segment = segments.get(coordinate.get("segment_id"))
                if not segment or not (
                    segment.get("doc_index") == index
                    and segment.get("start") <= coordinate.get("context_start")
                    and coordinate.get("context_end") <= segment.get("end")
                ):
                    errors.append(f"{prefix}:segment_reference_mismatch")
            else:
                field = coordinate.get("field")
                value = _meta_value(record, str(field))
                start, end = coordinate.get("start"), coordinate.get("end")
                if not (
                    isinstance(start, int)
                    and isinstance(end, int)
                    and 0 <= start <= end <= len(value)
                    and value[start:end] == coordinate.get("quote")
                    and sha256_text(value) == coordinate.get("value_sha256")
                ):
                    errors.append(f"{prefix}:meta_coordinate_mismatch")

        if detail.get("catalog_registered"):
            catalog_index = catalog_index or catalog_facts.CatalogIndex.load()
            expected = catalog_index.get(code)
            identity = detail.get("catalog_identity") or {}
            if expected is None:
                errors.append(f"catalog:{code}:missing_catalog_row")
            elif identity.get("catalog_coordinate") != expected.get("catalog_coordinate"):
                errors.append(f"catalog:{code}:catalog_coordinate_mismatch")
            condition = detail.get("special_condition") or {}
            coordinate = condition.get("source_coordinate")
            if coordinate and expected is not None:
                note = str(expected.get("특이사항") or "")
                if coordinate.get("quote") != note or not (
                    coordinate.get("start") == 0 and coordinate.get("end") == len(note)
                ):
                    errors.append(f"catalog:{code}:catalog_note_coordinate_mismatch")

    expected_hash = sha256_object(_hashable_context(context))
    if context.get("context_sha256") != expected_hash:
        errors.append("context_hash_mismatch")
    rendered = canonical_json(context)
    bounds = context.get("bounds") or {}
    if bounds.get("rendered_chars") != len(rendered):
        errors.append("rendered_chars_mismatch")
    if not isinstance(bounds.get("max_chars"), int) or len(rendered) > bounds.get("max_chars", -1):
        errors.append("max_chars_exceeded")
    return errors


__all__ = [
    "DEFAULT_MAX_CHARS",
    "GROUPS",
    "SCHEMA_VERSION",
    "build_fact_context",
    "canonical_json",
    "prepare_fact_inputs",
    "render_fact_context",
    "sha256_object",
    "validate_catalog_coordinates",
    "validate_fact_context",
]
