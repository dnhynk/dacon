"""Bounded context for v10-v18 and v20 qualification facts.

``qualification_facts`` deliberately over-retrieves observable premises.  The
raw result can be hundreds of kilobytes because every nested token span is
retained.  This module compacts those facts for an annotator without turning
them into labels: semantic summaries point to pooled, verbatim organizer
source segments, every coordinate and hash remains checkable, and any omitted
fact is counted explicitly.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping, Sequence

try:
    from tools.independent_gold import qualification_facts
except ModuleNotFoundError:  # Direct execution from this directory.
    import qualification_facts  # type: ignore[no-redef]


SCHEMA_VERSION = "dacon.independent.qualification_context.v4"
DEFAULT_MAX_CHARS = 16_000
# A caller that omits max_chars requests the ordinary 16k child budget, with
# lossless growth only when mandatory context itself cannot fit.  Callers that
# explicitly pass an integer retain the strict historical bound.
_AUTO_MANDATORY_BUDGET = object()
GROUP_FAMILIES: dict[str, tuple[str, ...]] = {
    "v10-13": (
        "bid_product_code_mentions",
        "catalog_product_name_candidates",
        "direct_production_requirements",
        "enterprise_size_clauses",
        "qualification_logic_markers",
        "statutory_exception_clauses",
        "qualification_source_markers",
    ),
    "v14-19": (
        "bid_product_code_mentions",
        "catalog_product_name_candidates",
        "direct_production_requirements",
        "enterprise_size_clauses",
        "qualification_logic_markers",
        "statutory_exception_clauses",
        "qualification_source_markers",
    ),
    "v20": (
        "bid_product_code_mentions",
        "catalog_product_name_candidates",
        "software_object_task_relations",
        "large_enterprise_software_restrictions",
        "qualification_source_markers",
    ),
}
GROUP_ITEMS: dict[str, tuple[str, ...]] = {
    "v10-13": ("v10", "v11", "v12", "v13"),
    "v14-19": ("v14", "v15", "v16", "v17", "v18", "v19"),
    "v20": ("v20",),
}

_ROLE_RANK = {
    "operative_bid_qualification_candidate": 0,
    "operative_bid_restriction_candidate": 0,
    "contract_software_object_task_candidate": 0,
    "document_list_candidate": 1,
    "proof_document_submission_list": 1,
    "post_award_or_contract_candidate": 1,
    "postaward_document_candidate": 1,
    "generic_legal_reference_candidate": 1,
    "generic_statutory_reference_candidate": 1,
    "evaluation_or_scoring_candidate": 1,
    "bidder_registration_only_candidate": 1,
    "training_or_content_context": 1,
    "requirements_inventory_or_security_reference": 1,
    "incidental_tool_use_context": 1,
    "same_context_software_object_task_unresolved": 2,
    "software_object_without_bound_task": 2,
    "scope_unresolved": 3,
    "financial_calculation_note": 4,
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_object(value: Any) -> str:
    return sha256_text(canonical_json(value))


def _documents(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(record.get("docs") or []):
        text = str(raw.get("text") or "")
        result.append(
            {
                "doc_index": index,
                "doc_id": str(raw.get("doc_id") or f"D{index}"),
                "doc_type": str(raw.get("type") or "unknown"),
                "chars": len(text),
                "sha256": sha256_text(text),
                "text": text,
            }
        )
    return result


def prepare_qualification_input(
    record: Mapping[str, Any],
    *,
    catalog: qualification_facts.CatalogReference | None = None,
) -> dict[str, Any]:
    catalog = catalog or qualification_facts.CatalogReference.load()
    return qualification_facts.extract_qualification_facts(record, catalog=catalog)


def _main_span(fact: Mapping[str, Any]) -> Mapping[str, Any]:
    span = fact.get("span")
    return span if isinstance(span, Mapping) else {}


def _role(fact: Mapping[str, Any]) -> str:
    return str(
        fact.get("clause_role")
        or fact.get("relation_role")
        or fact.get("source_role")
        or ""
    )


def _priority(family: str, fact: Mapping[str, Any]) -> int:
    role = _role(fact)
    if family in {
        "qualification_logic_markers",
        "statutory_exception_clauses",
        "qualification_source_markers",
    }:
        return 0 if role in {"", "operative_bid_qualification_candidate"} else 1
    if family == "bid_product_code_mentions":
        return 0 if fact.get("catalog_registered") else 2
    if family == "catalog_product_name_candidates":
        return 0 if fact.get("exact_catalog_identity") else 2
    return _ROLE_RANK.get(role, 2)


def _sort_key(family: str, fact: Mapping[str, Any]) -> tuple[Any, ...]:
    span = _main_span(fact)
    ambiguity_rank = 0 if not fact.get("ambiguities") else 1
    binding_rank = (
        0
        if family == "direct_production_requirements"
        and fact.get("bound_product_codes")
        and fact.get("eligibility_effect") == "mandatory_possession_or_verification"
        else 1
    )
    return (
        _priority(family, fact),
        binding_rank,
        int(span.get("doc_index", 10**9)),
        int(span.get("start", 10**9)),
        ambiguity_rank,
        int(span.get("end", 10**9)) - int(span.get("start", 0)),
        str(fact.get("fact_id") or ""),
    )


def _timing_types(fact: Mapping[str, Any]) -> list[str]:
    return sorted(
        {
            str(row.get("timing"))
            for row in fact.get("timing_mentions") or []
            if isinstance(row, Mapping) and row.get("timing")
        }
    )


def _catalog_identities(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "code": row.get("code"),
                "product_name": row.get("product_name"),
                "detail_name": row.get("detail_name"),
                "special_note": row.get("special_note"),
                "special_note_sha256": row.get("special_note_sha256"),
                "source_coordinate": copy.deepcopy(row.get("source_coordinate")),
            }
        )
    return result


def _compact_metadata_fact(fact: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the independently checkable part of a metadata fact.

    ``source_key`` is an exact duplicate of the suffix of ``path``.  Omitting
    that duplicate matters under a hard character budget and does not weaken
    provenance: the value, its canonical hash, status, and organizer path are
    all retained and validated against the record below.
    """

    return {
        key: copy.deepcopy(fact.get(key))
        for key in (
            "source_kind",
            "path",
            "value",
            "value_sha256",
            "status",
            "semantic_role",
            "operative_bid_qualification",
            "establishes_statutory_exception",
        )
        if key in fact
    }


def _coordinate(span: Mapping[str, Any]) -> dict[str, Any]:
    source_kind = span.get("source_kind")
    common = {
        "source_kind": source_kind,
        "start": span.get("start"),
        "end": span.get("end"),
        "quote_sha256": span.get("text_sha256"),
    }
    if span.get("matched_by"):
        common["matched_by"] = copy.deepcopy(span["matched_by"])
    if source_kind == "document":
        common.update(
            {
                "doc_index": span.get("doc_index"),
                "doc_id": span.get("doc_id"),
                "doc_type": span.get("doc_type"),
                "source_doc_sha256": span.get("source_doc_sha256"),
            }
        )
    elif source_kind == "meta":
        common.update(
            {
                "path": span.get("path"),
                "source_value_sha256": span.get("source_value_sha256"),
                "quote": span.get("text"),
            }
        )
    return common


def _compact_fact(family: str, fact: Mapping[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "fact_id": fact.get("fact_id"),
        "family": family,
        "support_items": copy.deepcopy(fact.get("support_items") or []),
        "source_coordinate": _coordinate(_main_span(fact)),
    }
    if family == "bid_product_code_mentions":
        compact.update(
            {
                "code": fact.get("code"),
                "mention_form": fact.get("mention_form"),
                "source_role": fact.get("source_role"),
                "catalog_registered": bool(fact.get("catalog_registered")),
                "catalog_identity": (
                    _catalog_identities([fact["catalog_identity"]])[0]
                    if isinstance(fact.get("catalog_identity"), Mapping)
                    else None
                ),
            }
        )
    elif family == "catalog_product_name_candidates":
        compact.update(
            {
                "identification_basis": fact.get("identification_basis"),
                "matched_name": fact.get("matched_name"),
                "name_family": fact.get("name_family"),
                "candidate_catalog_codes": copy.deepcopy(
                    fact.get("candidate_catalog_codes") or []
                ),
                "catalog_identities": _catalog_identities(
                    fact.get("catalog_identities") or []
                ),
                "source_role": fact.get("source_role"),
                "exact_catalog_identity": bool(fact.get("exact_catalog_identity")),
                "catalog_comparison_scope": fact.get("catalog_comparison_scope"),
                "exact_catalog_match_count": fact.get("exact_catalog_match_count"),
                "ambiguities": copy.deepcopy(fact.get("ambiguities") or []),
            }
        )
    elif family == "direct_production_requirements":
        compact.update(
            {
                "clause_role": fact.get("clause_role"),
                "binding_scope": fact.get("binding_scope"),
                "eligibility_effect": fact.get("eligibility_effect"),
                "qualification_constitutive": fact.get(
                    "qualification_constitutive"
                ),
                "timing": _timing_types(fact),
                "bound_product_codes": copy.deepcopy(fact.get("bound_product_codes") or []),
                "name_candidate_catalog_codes": copy.deepcopy(
                    fact.get("name_candidate_catalog_codes") or []
                ),
                "ambiguities": copy.deepcopy(fact.get("ambiguities") or []),
            }
        )
    elif family == "enterprise_size_clauses":
        compact.update(
            {
                "clause_role": fact.get("clause_role"),
                "eligibility_effect": fact.get("eligibility_effect"),
                "enterprise_scopes": sorted(
                    {
                        str(row.get("normalized_scope"))
                        for row in fact.get("enterprise_terms") or []
                        if isinstance(row, Mapping) and row.get("normalized_scope")
                    }
                ),
                "local_logic": fact.get("local_logic"),
                "logic_relations": sorted(
                    {
                        str(row.get("relation"))
                        for row in fact.get("logic_mentions") or []
                        if isinstance(row, Mapping) and row.get("relation")
                    }
                ),
                "timing": _timing_types(fact),
                "ambiguities": copy.deepcopy(fact.get("ambiguities") or []),
            }
        )
    elif family == "qualification_logic_markers":
        compact["relation"] = fact.get("relation")
    elif family == "qualification_source_markers":
        compact.update(
            {
                "marker_type": fact.get("marker_type"),
                "routed_scope": fact.get("routed_scope"),
                "is_label_decision": bool(fact.get("is_label_decision")),
            }
        )
    elif family == "statutory_exception_clauses":
        compact.update(
            {
                "exception_type": fact.get("exception_type"),
                "clause_role": fact.get("clause_role"),
                "establishment_status": fact.get("establishment_status"),
                "conditional_language_present": bool(
                    fact.get("conditional_language_present")
                ),
                "timing": _timing_types(fact),
                "ambiguities": copy.deepcopy(fact.get("ambiguities") or []),
            }
        )
    elif family == "software_object_task_relations":
        compact.update(
            {
                "relation_role": fact.get("relation_role"),
                "software_object_types": sorted(
                    {
                        str(row.get("object_type"))
                        for row in fact.get("software_objects") or []
                        if isinstance(row, Mapping) and row.get("object_type")
                    }
                ),
                "task_types": sorted(
                    {
                        str(row.get("task_type"))
                        for row in fact.get("task_actions") or []
                        if isinstance(row, Mapping) and row.get("task_type")
                    }
                ),
                "ambiguities": copy.deepcopy(fact.get("ambiguities") or []),
            }
        )
    elif family == "large_enterprise_software_restrictions":
        scalar_keys = (
            "clause_role",
            "restriction_effect",
            "business_amount_band",
            "large_enterprise_scope",
        )
        for key in scalar_keys:
            if key in fact:
                compact[key] = copy.deepcopy(fact[key])
        compact["amounts"] = sorted(
            {
                int(row["value_won"])
                for row in fact.get("money_mentions") or []
                if isinstance(row, Mapping) and isinstance(row.get("value_won"), int)
            }
        )
        compact["ambiguities"] = copy.deepcopy(fact.get("ambiguities") or [])
    return compact


def _source_segments(
    documents: Sequence[Mapping[str, Any]], facts: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    ranges: list[tuple[int, int, int]] = []
    for fact in facts:
        coordinate = fact["source_coordinate"]
        if coordinate.get("source_kind") != "document":
            continue
        ranges.append(
            (
                int(coordinate["doc_index"]),
                int(coordinate["start"]),
                int(coordinate["end"]),
            )
        )
    merged: list[dict[str, int]] = []
    for doc_index, start, end in sorted(set(ranges)):
        if (
            merged
            and merged[-1]["doc_index"] == doc_index
            and start <= merged[-1]["end"] + 24
            and max(end, merged[-1]["end"]) - merged[-1]["start"] <= 2_500
        ):
            merged[-1]["end"] = max(merged[-1]["end"], end)
        else:
            merged.append({"doc_index": doc_index, "start": start, "end": end})
    segments: list[dict[str, Any]] = []
    for row in merged:
        document = documents[row["doc_index"]]
        quote = str(document["text"])[row["start"] : row["end"]]
        segment_id = f"Q{row['doc_index']}:{row['start']}:{row['end']}"
        segments.append(
            {
                "segment_id": segment_id,
                "doc_index": row["doc_index"],
                "doc_id": document["doc_id"],
                "doc_type": document["doc_type"],
                "start": row["start"],
                "end": row["end"],
                "quote": quote,
                "quote_sha256": sha256_text(quote),
                "source_doc_sha256": document["sha256"],
            }
        )
    for fact in facts:
        coordinate = fact["source_coordinate"]
        if coordinate.get("source_kind") != "document":
            continue
        containing = next(
            segment
            for segment in segments
            if segment["doc_index"] == coordinate["doc_index"]
            and segment["start"] <= coordinate["start"]
            and coordinate["end"] <= segment["end"]
        )
        coordinate["segment_id"] = containing["segment_id"]
    return segments


def _fact_candidates(
    group_name: str, result: Mapping[str, Any]
) -> list[tuple[str, Mapping[str, Any]]]:
    source = result.get("facts") or {}
    by_family: dict[str, list[Mapping[str, Any]]] = {}
    for family in GROUP_FAMILIES[group_name]:
        rows = list(source.get(family) or [])
        rows.sort(key=lambda fact: _sort_key(family, fact))
        by_family[family] = rows

    # Reserve one best representative from every observed family before
    # globally filling by priority. A global priority queue can otherwise
    # starve an entire premise family (for example product identity after many
    # enterprise clauses). This ordering is label-independent and preserves
    # breadth before duplicate depth under a hard budget.
    sequence: list[tuple[str, Mapping[str, Any]]] = [
        (family, rows[0])
        for family in GROUP_FAMILIES[group_name]
        if (rows := by_family[family])
    ]

    buckets: dict[tuple[int, str], list[Mapping[str, Any]]] = defaultdict(list)
    for family, rows in by_family.items():
        for fact in rows[1:]:
            buckets[(_priority(family, fact), family)].append(fact)
    for (priority, family), facts in buckets.items():
        facts.sort(key=lambda fact: _sort_key(family, fact))
    for priority in sorted({key[0] for key in buckets}):
        families = sorted(family for key_priority, family in buckets if key_priority == priority)
        depth = 0
        while True:
            added = False
            for family in families:
                rows = buckets[(priority, family)]
                if depth < len(rows):
                    sequence.append((family, rows[depth]))
                    added = True
            if not added:
                break
            depth += 1
    return sequence


def _hashable_context(context: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(context))
    payload.pop("context_sha256", None)
    bounds = payload.get("bounds")
    if isinstance(bounds, dict):
        bounds.pop("rendered_chars", None)
    return payload


def _finalize(context: dict[str, Any]) -> dict[str, Any]:
    context["context_sha256"] = sha256_object(_hashable_context(context))
    context["bounds"]["rendered_chars"] = 0
    for _ in range(6):
        length = len(canonical_json(context))
        if context["bounds"]["rendered_chars"] == length:
            break
        context["bounds"]["rendered_chars"] = length
    return context


def _assemble(
    record: Mapping[str, Any],
    group_name: str,
    result: Mapping[str, Any],
    selected: Sequence[tuple[str, Mapping[str, Any]]],
    all_candidates: Sequence[tuple[str, Mapping[str, Any]]],
    max_chars: int,
) -> dict[str, Any]:
    documents = _documents(record)
    compact = [_compact_fact(family, fact) for family, fact in selected]
    segments = _source_segments(documents, compact)
    available = Counter(family for family, _ in all_candidates)
    included = Counter(family for family, _ in selected)
    metadata_names = (
        "contract_law",
        "work_type",
        "contract_method",
        "budget_won",
        "estimated_price_won",
        "information_project",
        "catalog_codes",
        "industry_codes",
        "industry_restricted",
        "article_summary",
    )
    metadata = result.get("metadata_facts") or {}
    context = {
        "schema_version": SCHEMA_VERSION,
        "semantic_role": "source_grounded_qualification_facts_without_decisions",
        "record_id": str(record.get("id") or ""),
        "group": group_name,
        "target_items": list(GROUP_ITEMS[group_name]),
        "organizer_source": {
            "record_sha256": sha256_object(record),
            "documents": [
                {key: document[key] for key in ("doc_index", "doc_id", "doc_type", "chars", "sha256")}
                for document in documents
            ],
        },
        "extractor": {
            "schema_version": result.get("schema_version"),
            "output_sha256": sha256_object(result),
            "reference_sources": copy.deepcopy(result.get("reference_sources") or {}),
        },
        "source_completeness": copy.deepcopy(result.get("source_completeness") or {}),
        "record_ambiguities": copy.deepcopy(result.get("ambiguities") or []),
        "metadata_facts": {
            name: _compact_metadata_fact(metadata[name])
            for name in metadata_names
            if isinstance(metadata.get(name), Mapping)
        },
        "relation_summaries": copy.deepcopy(result.get("relations") or {}),
        "facts": compact,
        "source_segments": segments,
        "omissions": {
            family: {
                "available": available[family],
                "included": included[family],
                "omitted": available[family] - included[family],
            }
            for family in GROUP_FAMILIES[group_name]
        },
        "bounds": {
            "max_chars": max_chars,
            "rendered_chars": 0,
            "available_facts": len(all_candidates),
            "included_facts": len(selected),
            "budget_truncated": len(selected) < len(all_candidates),
        },
    }
    # Relations can be large lists of fact IDs; facts already retain the local
    # roles needed for review.  Keep only compact, label-free relation summaries.
    relations = context["relation_summaries"]
    if isinstance(relations, dict):
        inventory = relations.get("exact_product_code_inventory")
        if isinstance(inventory, list):
            relations["exact_product_code_inventory"] = [
                {
                    key: copy.deepcopy(row.get(key))
                    for key in ("code", "catalog_registered", "mentions")
                }
                for row in inventory
                if isinstance(row, Mapping)
            ]
        bindings = relations.get("direct_production_target_bindings")
        if isinstance(bindings, list):
            relations["direct_production_target_bindings"] = [
                {
                    key: copy.deepcopy(row.get(key))
                    for key in (
                        "direct_production_fact_id",
                        "clause_codes",
                        "clause_name_candidate_codes",
                        "bid_metadata_codes",
                        "declared_bid_object_codes",
                        "bid_object_name_candidate_codes",
                        "declared_bid_object_names",
                        "relation",
                        "binding_applicability",
                        "qualification_constitutive",
                        "is_label_decision",
                    )
                }
                for row in bindings
                if isinstance(row, Mapping)
            ]
        closure = relations.get("qualification_source_closure")
        if isinstance(closure, Mapping):
            compact_closure = {
                key: copy.deepcopy(closure.get(key))
                for key in (
                    "status",
                    "closed_list_marker_fact_ids",
                    "delegation_marker_fact_ids",
                    "other_scope_reference_marker_fact_ids",
                    "scope_assessments",
                    "provided_documents_scanned",
                    "semantic_scope",
                    "is_label_decision",
                )
            }
            scopes = compact_closure.get("scope_assessments")
            if isinstance(scopes, Mapping):
                compact_closure["scope_assessments"] = {
                    str(scope): {
                        key: copy.deepcopy(assessment.get(key))
                        for key in (
                            "source_status",
                            "missing_document_materiality",
                            "basis",
                        )
                        if key in assessment
                    }
                    for scope, assessment in scopes.items()
                    if isinstance(assessment, Mapping)
                }
            relations["qualification_source_closure"] = compact_closure
    return _finalize(context)


def build_qualification_context(
    record: Mapping[str, Any],
    group_name: str,
    *,
    prepared: Mapping[str, Any] | None = None,
    catalog: qualification_facts.CatalogReference | None = None,
    max_chars: int | object = _AUTO_MANDATORY_BUDGET,
) -> dict[str, Any] | None:
    """Return a bounded context, or ``None`` for an unrelated group.

    An omitted budget grows only enough to retain all mandatory context.
    An explicitly supplied integer is always a hard limit, including 16,000.
    This distinction keeps legacy grouped calls strict while allowing the
    full-record caller to preserve source-grounded mandatory qualification
    data.  The enclosing full-record context still enforces its 300k limit.
    """

    if group_name not in GROUP_FAMILIES:
        return None
    grow_for_mandatory = max_chars is _AUTO_MANDATORY_BUDGET
    if grow_for_mandatory:
        max_chars = DEFAULT_MAX_CHARS
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
        raise ValueError("max_chars must be a positive integer")
    result = dict(prepared) if prepared is not None else prepare_qualification_input(record, catalog=catalog)
    if result.get("schema_version") != qualification_facts.SCHEMA_VERSION:
        raise ValueError("qualification fact schema mismatch")
    if result.get("semantic_role") != "source_grounded_facts_only":
        raise ValueError("qualification fact semantic role mismatch")
    if result.get("label_decisions_present") is not False:
        raise ValueError("qualification facts must not contain label decisions")
    if result.get("target_items") != list(qualification_facts.TARGET_ITEMS):
        raise ValueError("qualification fact target items mismatch")
    if result.get("record_id") != str(record.get("id") or ""):
        raise ValueError("qualification fact record id mismatch")
    if result.get("source_sha256") != sha256_object(record):
        raise ValueError("qualification fact source hash mismatch")
    source_facts = result.get("facts")
    if not isinstance(source_facts, Mapping):
        raise ValueError("qualification fact families missing")
    expected_counts = {
        family: len(rows) if isinstance(rows, list) else -1
        for family, rows in source_facts.items()
    }
    if result.get("fact_counts") != expected_counts or any(
        count < 0 for count in expected_counts.values()
    ):
        raise ValueError("qualification fact counts mismatch")
    if catalog is not None:
        reference = (
            (result.get("reference_sources") or {}).get(
                "competitive_product_catalog"
            )
            or {}
        )
        if reference.get("sha256") != catalog.sha256:
            raise ValueError("qualification fact catalog hash mismatch")
    errors = qualification_facts.validate_fact_coordinates(record, result)
    if errors:
        raise ValueError(f"qualification fact coordinates invalid: {errors[:3]}")
    candidates = _fact_candidates(group_name, result)
    minimal = _assemble(record, group_name, result, (), candidates, max_chars)
    if len(canonical_json(minimal)) > max_chars:
        if not grow_for_mandatory:
            raise ValueError(
                f"max_chars={max_chars} is smaller than mandatory qualification context"
            )
        # The bound is part of the rendered object. Reassemble until the
        # declared bound and its own decimal width reach a fixed point.
        for _ in range(8):
            max_chars = len(canonical_json(minimal))
            minimal = _assemble(record, group_name, result, (), candidates, max_chars)
            if len(canonical_json(minimal)) <= max_chars:
                break
        else:
            raise ValueError("mandatory qualification budget did not stabilize")
    selected: list[tuple[str, Mapping[str, Any]]] = []
    for candidate in candidates:
        trial = _assemble(record, group_name, result, [*selected, candidate], candidates, max_chars)
        if len(canonical_json(trial)) <= max_chars:
            selected.append(candidate)
    return _assemble(record, group_name, result, selected, candidates, max_chars)


def render_qualification_context(context: Mapping[str, Any]) -> str:
    rendered = canonical_json(context)
    max_chars = (context.get("bounds") or {}).get("max_chars")
    if not isinstance(max_chars, int) or len(rendered) > max_chars:
        raise ValueError("qualification context exceeds or lacks max_chars")
    return rendered


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, Mapping):
        return key in value or any(_contains_key(child, key) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_key(child, key) for child in value)
    return False


def validate_qualification_context(
    record: Mapping[str, Any], context: Mapping[str, Any]
) -> list[str]:
    errors: list[str] = []
    documents = _documents(record)
    if context.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    if context.get("record_id") != str(record.get("id") or ""):
        errors.append("record_id_mismatch")
    if (context.get("organizer_source") or {}).get("record_sha256") != sha256_object(record):
        errors.append("record_hash_mismatch")
    group_name = context.get("group")
    if group_name not in GROUP_FAMILIES:
        errors.append("invalid_group")
    elif context.get("target_items") != list(GROUP_ITEMS[group_name]):
        errors.append("target_items_mismatch")
    if context.get("semantic_role") != "source_grounded_qualification_facts_without_decisions":
        errors.append("semantic_role_mismatch")
    if _contains_key(context, "labels"):
        errors.append("forbidden_labels_key")

    organizer_documents = (context.get("organizer_source") or {}).get("documents")
    expected_documents = [
        {
            key: document[key]
            for key in ("doc_index", "doc_id", "doc_type", "chars", "sha256")
        }
        for document in documents
    ]
    if organizer_documents != expected_documents:
        errors.append("document_manifest_mismatch")

    meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
    for name, fact in (context.get("metadata_facts") or {}).items():
        if not isinstance(fact, Mapping):
            errors.append(f"metadata:{name}:invalid_fact")
            continue
        path = str(fact.get("path") or "")
        field = path[5:] if path.startswith("meta.") else ""
        if fact.get("source_kind") != "meta" or not field:
            errors.append(f"metadata:{name}:invalid_path")
            continue
        value = meta.get(field)
        if fact.get("value") != value:
            errors.append(f"metadata:{name}:value_mismatch")
        expected_hash = sha256_text(canonical_json(value))
        if fact.get("value_sha256") != expected_hash:
            errors.append(f"metadata:{name}:value_hash_mismatch")
        expected_status = (
            "present"
            if field in meta and value not in (None, "", "미입력")
            else "missing_or_unentered"
        )
        if fact.get("status") != expected_status:
            errors.append(f"metadata:{name}:status_mismatch")

    raw_segments = context.get("source_segments") or []
    if not isinstance(raw_segments, list):
        errors.append("invalid_source_segments")
        raw_segments = []
    segments: dict[Any, Mapping[str, Any]] = {}
    for row in raw_segments:
        if not isinstance(row, Mapping):
            errors.append("invalid_source_segment")
            continue
        segment_id = row.get("segment_id")
        if segment_id in segments:
            errors.append(f"segment:{segment_id}:duplicate_id")
        segments[segment_id] = row
    for segment_id, segment in segments.items():
        index, start, end = segment.get("doc_index"), segment.get("start"), segment.get("end")
        if not isinstance(index, int) or not (0 <= index < len(documents)):
            errors.append(f"segment:{segment_id}:invalid_doc_index")
            continue
        text = documents[index]["text"]
        if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start <= end <= len(text)):
            errors.append(f"segment:{segment_id}:invalid_range")
            continue
        quote = text[start:end]
        expected_segment_id = f"Q{index}:{start}:{end}"
        if segment_id != expected_segment_id:
            errors.append(f"segment:{segment_id}:noncanonical_id")
        if segment.get("doc_id") != documents[index]["doc_id"]:
            errors.append(f"segment:{segment_id}:doc_id_mismatch")
        if segment.get("doc_type") != documents[index]["doc_type"]:
            errors.append(f"segment:{segment_id}:doc_type_mismatch")
        if quote != segment.get("quote"):
            errors.append(f"segment:{segment_id}:quote_mismatch")
        if sha256_text(quote) != segment.get("quote_sha256"):
            errors.append(f"segment:{segment_id}:quote_hash_mismatch")
        if documents[index]["sha256"] != segment.get("source_doc_sha256"):
            errors.append(f"segment:{segment_id}:document_hash_mismatch")
    raw_facts = context.get("facts") or []
    if not isinstance(raw_facts, list):
        errors.append("invalid_facts")
        raw_facts = []
    included_counts: Counter[str] = Counter()
    for index, fact in enumerate(raw_facts):
        if not isinstance(fact, Mapping):
            errors.append(f"fact:{index}:invalid_fact")
            continue
        family = fact.get("family")
        if group_name in GROUP_FAMILIES and family not in GROUP_FAMILIES[group_name]:
            errors.append(f"fact:{index}:invalid_family")
        if isinstance(family, str):
            included_counts[family] += 1
        coordinate = fact.get("source_coordinate") or {}
        if not isinstance(coordinate, Mapping):
            errors.append(f"fact:{index}:invalid_coordinate")
            continue
        source_kind = coordinate.get("source_kind")
        start, end = coordinate.get("start"), coordinate.get("end")
        if source_kind == "document":
            segment = segments.get(coordinate.get("segment_id"))
            if segment is None:
                errors.append(f"fact:{index}:missing_segment")
                continue
            doc_index = coordinate.get("doc_index")
            if doc_index != segment.get("doc_index") or not (
                isinstance(start, int)
                and isinstance(end, int)
                and segment["start"] <= start <= end <= segment["end"]
            ):
                errors.append(f"fact:{index}:coordinate_outside_segment")
                continue
            document = documents[doc_index]
            if coordinate.get("doc_id") != document["doc_id"]:
                errors.append(f"fact:{index}:doc_id_mismatch")
            if coordinate.get("doc_type") != document["doc_type"]:
                errors.append(f"fact:{index}:doc_type_mismatch")
            if coordinate.get("source_doc_sha256") != document["sha256"]:
                errors.append(f"fact:{index}:document_hash_mismatch")
            quote = document["text"][start:end]
            if sha256_text(quote) != coordinate.get("quote_sha256"):
                errors.append(f"fact:{index}:quote_hash_mismatch")
        elif source_kind == "meta":
            path = str(coordinate.get("path") or "")
            field = path[5:] if path.startswith("meta.") else ""
            if field not in meta:
                errors.append(f"fact:{index}:missing_meta_path")
                continue
            source = str(meta.get(field) or "")
            if not (
                isinstance(start, int)
                and isinstance(end, int)
                and 0 <= start <= end <= len(source)
            ):
                errors.append(f"fact:{index}:invalid_meta_range")
                continue
            quote = source[start:end]
            if coordinate.get("quote") != quote:
                errors.append(f"fact:{index}:meta_quote_mismatch")
            if coordinate.get("quote_sha256") != sha256_text(quote):
                errors.append(f"fact:{index}:quote_hash_mismatch")
            if coordinate.get("source_value_sha256") != sha256_text(source):
                errors.append(f"fact:{index}:meta_source_hash_mismatch")
        else:
            errors.append(f"fact:{index}:unknown_source_kind")

    omissions = context.get("omissions") or {}
    available_total = 0
    included_total = 0
    if group_name in GROUP_FAMILIES:
        if set(omissions) != set(GROUP_FAMILIES[group_name]):
            errors.append("omission_families_mismatch")
        for family in GROUP_FAMILIES[group_name]:
            row = omissions.get(family)
            if not isinstance(row, Mapping):
                errors.append(f"omissions:{family}:invalid")
                continue
            available = row.get("available")
            included = row.get("included")
            omitted = row.get("omitted")
            if not all(isinstance(value, int) and not isinstance(value, bool) for value in (available, included, omitted)):
                errors.append(f"omissions:{family}:invalid_counts")
                continue
            if available < included or omitted != available - included:
                errors.append(f"omissions:{family}:inconsistent_counts")
            if included != included_counts[family]:
                errors.append(f"omissions:{family}:included_mismatch")
            available_total += available
            included_total += included

    if context.get("context_sha256") != sha256_object(_hashable_context(context)):
        errors.append("context_hash_mismatch")
    rendered = canonical_json(context)
    bounds = context.get("bounds") or {}
    if bounds.get("available_facts") != available_total:
        errors.append("available_facts_mismatch")
    if bounds.get("included_facts") != included_total or included_total != len(raw_facts):
        errors.append("included_facts_mismatch")
    if bounds.get("budget_truncated") != (included_total < available_total):
        errors.append("budget_truncated_mismatch")
    if bounds.get("rendered_chars") != len(rendered):
        errors.append("rendered_chars_mismatch")
    if not isinstance(bounds.get("max_chars"), int) or len(rendered) > bounds.get("max_chars", 0):
        errors.append("max_chars_exceeded")
    return errors


__all__ = [
    "DEFAULT_MAX_CHARS",
    "GROUP_FAMILIES",
    "GROUP_ITEMS",
    "SCHEMA_VERSION",
    "build_qualification_context",
    "prepare_qualification_input",
    "render_qualification_context",
    "validate_qualification_context",
]
