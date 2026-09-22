"""Turn the label-free clause bank into bounded independent-review packets.

The packetizer is intentionally a review scheduler, not a decision engine.
Every clause-bank occurrence is assigned to exactly one packet, and every
raw-quote variant referenced by that packet is included verbatim.  A cluster
representative is only a reading aid: neither its interpretation nor any
future decision may be copied to another occurrence.

Only the source-derived clause-bank schema is accepted.  Unknown fields,
decision/model artifacts, coordinate/hash inconsistencies, duplicate JSON
keys, and non-canonical JSONL rows fail before the publication manifest is
replaced.  The compact review index is ordered by explicit risk, variant, and
cluster-size strata; the packet JSONL itself remains in canonical source order.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import tempfile
import unicodedata
from typing import Any, Iterable, Iterator, Mapping, Sequence

try:
    from tools.independent_gold import clause_bank
except ModuleNotFoundError:  # Direct execution from this directory.
    import clause_bank  # type: ignore[no-redef]


ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKET_SCHEMA_VERSION = "dacon.independent.clause_review_packet.v1"
INDEX_SCHEMA_VERSION = "dacon.independent.clause_review_index.v1"
MANIFEST_SCHEMA_VERSION = "dacon.independent.clause_review_manifest.v1"
GROUPS: dict[str, tuple[str, ...]] = dict(clause_bank.GROUPS)

RISK_ORDER = {"critical": 0, "high": 1, "standard": 2}
KNOWN_RISK_FLAGS = frozenset(
    {
        "no_relevant_clause_retrieved",
        "one_or_more_clauses_clipped",
        "source_not_fully_observed",
        "absence_group_with_incomplete_source",
        "mixed_phase_clause",
        "explicit_exception_language",
    }
)
CATALOG_GROUPS = frozenset({"v10-13", "v14-19"})
SHA256_LENGTH = 64

# These are legitimate, negative safety-contract fields produced by the bank.
ALLOWED_SAFETY_KEYS = frozenset(
    {
        "automatic_label_or_decision_propagation_allowed",
        "labels_predictions_or_model_outputs_consumed",
    }
)
FORBIDDEN_KEY_MARKERS = tuple(
    value
    for value in clause_bank.template_clusters.FORBIDDEN_SOURCE_KEY_MARKERS
    if value
) + (
    "production",
    "submission",
    "gemma",
    "answer_key",
    "ground_truth",
    "candidate_decision",
    "verifier_decision",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_object(value: Any) -> str:
    return sha256_text(canonical_json(value))


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_or_absolute(path: pathlib.Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _normalized_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    return re.sub(r"[\s.\\/:-]+", "_", normalized)


def _assert_no_forbidden_keys(value: Any, *, location: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = _normalized_key(key)
            if key not in ALLOWED_SAFETY_KEYS and any(
                marker in normalized for marker in FORBIDDEN_KEY_MARKERS
            ):
                raise ValueError(f"{location}.{key}: decision/model field is forbidden")
            if isinstance(child, (Mapping, list)):
                _assert_no_forbidden_keys(child, location=f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, (Mapping, list)):
                _assert_no_forbidden_keys(child, location=f"{location}[{index}]")


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _loads_object(text: str, *, location: str) -> dict[str, Any]:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{location}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{location}: expected a JSON object")
    return value


def _expect_keys(value: Any, expected: Iterable[str], *, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{location}: expected object")
    expected_set = set(expected)
    actual = set(value)
    if actual != expected_set:
        missing = sorted(expected_set - actual)
        extra = sorted(str(key) for key in actual - expected_set)
        raise ValueError(f"{location}: schema keys differ; missing={missing}, extra={extra}")
    return value


def _expect_list(value: Any, *, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{location}: expected list")
    return value


def _expect_int(value: Any, *, location: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{location}: expected integer >= {minimum}")
    return value


def _expect_sha(value: Any, *, location: str) -> str:
    if not isinstance(value, str) or len(value) != SHA256_LENGTH:
        raise ValueError(f"{location}: expected SHA-256 hex")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{location}: expected SHA-256 hex") from exc
    if value != value.lower():
        raise ValueError(f"{location}: SHA-256 must be lowercase")
    return value


def _validate_source_completeness(value: Any, *, location: str) -> dict[str, Any]:
    row = _expect_keys(
        value, {"fully_observed", "dropped_doc_types"}, location=location
    )
    if type(row["fully_observed"]) is not bool:
        raise ValueError(f"{location}.fully_observed: expected Boolean")
    dropped = _expect_list(row["dropped_doc_types"], location=f"{location}.dropped_doc_types")
    if not all(isinstance(item, str) for item in dropped) or dropped != sorted(set(dropped)):
        raise ValueError(f"{location}.dropped_doc_types: expected sorted unique strings")
    return dict(row)


def _validate_variable_signature(value: Any, *, location: str) -> None:
    row = _expect_keys(
        value,
        {
            "anon",
            "product_codes",
            "industry_codes",
            "dates",
            "money",
            "money_pair_bands",
            "percentages",
        },
        location=location,
    )
    for index, anon in enumerate(_expect_list(row["anon"], location=f"{location}.anon")):
        _expect_keys(anon, {"kind", "unit", "subtype"}, location=f"{location}.anon[{index}]")
    for key in ("product_codes", "industry_codes", "money_pair_bands"):
        values = _expect_list(row[key], location=f"{location}.{key}")
        if not all(isinstance(item, str) for item in values):
            raise ValueError(f"{location}.{key}: expected string list")
    dates = _expect_keys(
        row["dates"],
        {"count", "roles", "has_time", "gaps_days", "invalid", "partial_exact"},
        location=f"{location}.dates",
    )
    _expect_int(dates["count"], location=f"{location}.dates.count")
    _expect_int(dates["invalid"], location=f"{location}.dates.invalid")
    for key in ("roles", "has_time", "gaps_days", "partial_exact"):
        _expect_list(dates[key], location=f"{location}.dates.{key}")
    for index, money in enumerate(_expect_list(row["money"], location=f"{location}.money")):
        _expect_keys(
            money,
            {"band", "to_budget", "to_estimated"},
            location=f"{location}.money[{index}]",
        )
    for index, percent in enumerate(
        _expect_list(row["percentages"], location=f"{location}.percentages")
    ):
        _expect_keys(percent, {"band"}, location=f"{location}.percentages[{index}]")


def _validate_clause_facets(value: Any, *, location: str) -> dict[str, Any]:
    row = _expect_keys(
        value,
        {"phases", "actors", "logic_sequence", "product_name_hashes"},
        location=location,
    )
    for key in ("phases", "actors", "logic_sequence", "product_name_hashes"):
        values = _expect_list(row[key], location=f"{location}.{key}")
        if not all(isinstance(item, str) for item in values):
            raise ValueError(f"{location}.{key}: expected string list")
    for index, value in enumerate(row["product_name_hashes"]):
        _expect_sha(value, location=f"{location}.product_name_hashes[{index}]")
    return dict(row)


def _validate_fingerprint_identity(
    value: Any, *, group_name: str, fingerprint: str, location: str
) -> dict[str, Any]:
    identity = _expect_keys(
        value,
        {
            "group",
            "doc_type",
            "normalized_text",
            "variable_signature",
            "facets",
            "anchors",
            "clipped",
        },
        location=location,
    )
    if identity["group"] != group_name:
        raise ValueError(f"{location}.group: group mismatch")
    if not isinstance(identity["doc_type"], str) or not isinstance(
        identity["normalized_text"], str
    ):
        raise ValueError(f"{location}: doc_type and normalized_text must be strings")
    if type(identity["clipped"]) is not bool:
        raise ValueError(f"{location}.clipped: expected Boolean")
    anchors = _expect_list(identity["anchors"], location=f"{location}.anchors")
    if not all(isinstance(item, str) for item in anchors) or anchors != sorted(set(anchors)):
        raise ValueError(f"{location}.anchors: expected sorted unique strings")
    _validate_variable_signature(
        identity["variable_signature"], location=f"{location}.variable_signature"
    )
    _validate_clause_facets(identity["facets"], location=f"{location}.facets")
    if sha256_object(identity) != fingerprint:
        raise ValueError(f"{location}: clause fingerprint mismatch")
    return dict(identity)


def _validate_catalog_provenance(value: Any, *, location: str) -> dict[str, Any]:
    row = _expect_keys(value, {"path", "sha256", "rows"}, location=location)
    if not isinstance(row["path"], str):
        raise ValueError(f"{location}.path: expected string")
    _expect_sha(row["sha256"], location=f"{location}.sha256")
    _expect_int(row["rows"], location=f"{location}.rows")
    return dict(row)


def _validate_catalog_scope(value: Any, *, location: str) -> None:
    scope = _expect_keys(
        value, {"summary_decision", "summary_reason_code", "codes"}, location=location
    )
    codes = _expect_list(scope["codes"], location=f"{location}.codes")
    for index, code in enumerate(codes):
        _expect_keys(
            code,
            {"code", "decision", "reason_code", "condition_kind"},
            location=f"{location}.codes[{index}]",
        )


def _expected_risk_tier(flags: Sequence[str]) -> str:
    if "absence_group_with_incomplete_source" in flags or "no_relevant_clause_retrieved" in flags:
        return "critical"
    if flags:
        return "high"
    return "standard"


def validate_clause_bank_row(
    value: Mapping[str, Any],
    *,
    manifest_catalog_provenance: Mapping[str, Any],
    location: str,
) -> dict[str, Any]:
    """Validate one complete clause-bank row and return a plain dictionary."""

    _assert_no_forbidden_keys(value, location=location)
    row = _expect_keys(
        value,
        {
            "schema_version",
            "row_kind",
            "group",
            "target_items",
            "cluster_id",
            "clause_fingerprint",
            "fingerprint_identity",
            "occurrence_count",
            "member_record_count",
            "member_record_ids",
            "raw_quote_variant_count",
            "raw_quote_variants",
            "risk_facets",
            "catalog_provenance",
            "catalog_provenance_sha256",
            "catalog_scope_variants",
            "occurrences",
            "review_contract",
        },
        location=location,
    )
    if row["schema_version"] != clause_bank.SCHEMA_VERSION:
        raise ValueError(f"{location}: unsupported clause-bank schema")
    if row["row_kind"] != "group_clause_fingerprint_bank":
        raise ValueError(f"{location}: unsupported row kind")
    group_name = row["group"]
    if group_name not in GROUPS:
        raise ValueError(f"{location}.group: unknown group")
    if row["target_items"] != list(GROUPS[group_name]):
        raise ValueError(f"{location}.target_items: group target mismatch")
    fingerprint = _expect_sha(
        row["clause_fingerprint"], location=f"{location}.clause_fingerprint"
    )
    if row["cluster_id"] != f"C-{group_name}-{fingerprint[:16]}":
        raise ValueError(f"{location}.cluster_id: cluster identity mismatch")
    identity = _validate_fingerprint_identity(
        row["fingerprint_identity"],
        group_name=group_name,
        fingerprint=fingerprint,
        location=f"{location}.fingerprint_identity",
    )

    quote_variants: dict[str, dict[str, Any]] = {}
    occurrence_count = _expect_int(
        row["occurrence_count"], location=f"{location}.occurrence_count", minimum=1
    )
    member_record_count = _expect_int(
        row["member_record_count"],
        location=f"{location}.member_record_count",
        minimum=1,
    )
    raw_quote_variant_count = _expect_int(
        row["raw_quote_variant_count"],
        location=f"{location}.raw_quote_variant_count",
        minimum=1,
    )
    quote_rows = _expect_list(
        row["raw_quote_variants"], location=f"{location}.raw_quote_variants"
    )
    for index, raw in enumerate(quote_rows):
        variant = _expect_keys(
            raw,
            {
                "variant_id",
                "text_sha256",
                "chars",
                "occurrence_count",
                "member_record_count",
                "raw_quote",
            },
            location=f"{location}.raw_quote_variants[{index}]",
        )
        text_sha = _expect_sha(
            variant["text_sha256"],
            location=f"{location}.raw_quote_variants[{index}].text_sha256",
        )
        variant_id = f"Q-{text_sha}"
        if variant["variant_id"] != variant_id or variant_id in quote_variants:
            raise ValueError(f"{location}: invalid/duplicate raw quote variant")
        quote = variant["raw_quote"]
        if not isinstance(quote, str) or sha256_text(quote) != text_sha:
            raise ValueError(f"{location}: raw quote hash mismatch")
        chars = _expect_int(
            variant["chars"],
            location=f"{location}.raw_quote_variants[{index}].chars",
        )
        if chars != len(quote):
            raise ValueError(f"{location}: raw quote character count mismatch")
        _expect_int(variant["occurrence_count"], location=f"{location}.quote_count", minimum=1)
        _expect_int(variant["member_record_count"], location=f"{location}.quote_members", minimum=1)
        quote_variants[variant_id] = dict(variant)
    if [item["variant_id"] for item in quote_rows] != sorted(quote_variants):
        raise ValueError(f"{location}.raw_quote_variants: non-canonical order")
    if raw_quote_variant_count != len(quote_rows):
        raise ValueError(f"{location}.raw_quote_variant_count: count mismatch")

    catalog_variants: dict[str, dict[str, Any]] = {}
    catalog_rows = _expect_list(
        row["catalog_scope_variants"], location=f"{location}.catalog_scope_variants"
    )
    if group_name in CATALOG_GROUPS:
        provenance = _validate_catalog_provenance(
            row["catalog_provenance"], location=f"{location}.catalog_provenance"
        )
        if provenance != dict(manifest_catalog_provenance):
            raise ValueError(f"{location}.catalog_provenance: manifest mismatch")
        provenance_sha = sha256_object(provenance)
        if row["catalog_provenance_sha256"] != provenance_sha:
            raise ValueError(f"{location}.catalog_provenance_sha256: hash mismatch")
    else:
        provenance_sha = ""
        if row["catalog_provenance"] is not None or row["catalog_provenance_sha256"] is not None:
            raise ValueError(f"{location}: unexpected catalog provenance")
        if catalog_rows:
            raise ValueError(f"{location}: unexpected catalog scope variants")
    for index, raw in enumerate(catalog_rows):
        scope_row = _expect_keys(
            raw,
            {
                "variant_id",
                "scope_sha256",
                "occurrence_count",
                "member_record_count",
                "catalog_scope",
            },
            location=f"{location}.catalog_scope_variants[{index}]",
        )
        scope_sha = _expect_sha(
            scope_row["scope_sha256"],
            location=f"{location}.catalog_scope_variants[{index}].scope_sha256",
        )
        variant_id = f"K-{scope_sha}"
        if scope_row["variant_id"] != variant_id or variant_id in catalog_variants:
            raise ValueError(f"{location}: invalid/duplicate catalog scope variant")
        _validate_catalog_scope(
            scope_row["catalog_scope"],
            location=f"{location}.catalog_scope_variants[{index}].catalog_scope",
        )
        expected_sha = sha256_object(
            {
                "catalog_provenance_sha256": provenance_sha,
                "scope": scope_row["catalog_scope"],
            }
        )
        if expected_sha != scope_sha:
            raise ValueError(f"{location}: catalog scope hash mismatch")
        _expect_int(scope_row["occurrence_count"], location=f"{location}.scope_count", minimum=1)
        _expect_int(scope_row["member_record_count"], location=f"{location}.scope_members", minimum=1)
        catalog_variants[variant_id] = dict(scope_row)
    if [item["variant_id"] for item in catalog_rows] != sorted(catalog_variants):
        raise ValueError(f"{location}.catalog_scope_variants: non-canonical order")

    occurrences = _expect_list(row["occurrences"], location=f"{location}.occurrences")
    if not occurrences:
        raise ValueError(f"{location}.occurrences: empty clause cluster")
    seen_occurrences: set[str] = set()
    tier_counts: collections.Counter[str] = collections.Counter()
    flag_counts: collections.Counter[str] = collections.Counter()
    completeness_counts: collections.Counter[str] = collections.Counter()
    quote_counts: collections.Counter[str] = collections.Counter()
    quote_members: dict[str, set[str]] = collections.defaultdict(set)
    scope_counts: collections.Counter[str] = collections.Counter()
    scope_members: dict[str, set[str]] = collections.defaultdict(set)
    member_ids: set[str] = set()
    occurrence_order: list[tuple[Any, ...]] = []
    for index, raw in enumerate(occurrences):
        occ_location = f"{location}.occurrences[{index}]"
        occurrence = _expect_keys(
            raw,
            {
                "occurrence_id",
                "record_id",
                "source_sha256",
                "coordinate",
                "raw_quote_variant_id",
                "catalog_scope_variant_id",
                "source_completeness",
                "risk_flags",
                "risk_tier",
                "coordinate_sha256",
            },
            location=occ_location,
        )
        occurrence_id = _expect_sha(
            occurrence["occurrence_id"], location=f"{occ_location}.occurrence_id"
        )
        if occurrence_id in seen_occurrences:
            raise ValueError(f"{occ_location}: duplicate occurrence id")
        seen_occurrences.add(occurrence_id)
        record_id = occurrence["record_id"]
        if not isinstance(record_id, str) or not record_id:
            raise ValueError(f"{occ_location}.record_id: expected non-empty string")
        source_sha = _expect_sha(
            occurrence["source_sha256"], location=f"{occ_location}.source_sha256"
        )
        coordinate = _expect_keys(
            occurrence["coordinate"],
            {
                "doc_index",
                "doc_id",
                "doc_type",
                "source_doc_sha256",
                "start",
                "end",
                "text_sha256",
            },
            location=f"{occ_location}.coordinate",
        )
        doc_index = _expect_int(
            coordinate["doc_index"], location=f"{occ_location}.coordinate.doc_index"
        )
        start = _expect_int(coordinate["start"], location=f"{occ_location}.coordinate.start")
        end = _expect_int(coordinate["end"], location=f"{occ_location}.coordinate.end", minimum=1)
        if end <= start:
            raise ValueError(f"{occ_location}.coordinate: end must exceed start")
        if not isinstance(coordinate["doc_id"], str) or not coordinate["doc_id"]:
            raise ValueError(f"{occ_location}.coordinate.doc_id: invalid")
        if coordinate["doc_type"] != identity["doc_type"]:
            raise ValueError(f"{occ_location}.coordinate.doc_type: fingerprint mismatch")
        _expect_sha(
            coordinate["source_doc_sha256"],
            location=f"{occ_location}.coordinate.source_doc_sha256",
        )
        text_sha = _expect_sha(
            coordinate["text_sha256"], location=f"{occ_location}.coordinate.text_sha256"
        )
        if occurrence["coordinate_sha256"] != sha256_object(coordinate):
            raise ValueError(f"{occ_location}.coordinate_sha256: hash mismatch")
        quote_id = occurrence["raw_quote_variant_id"]
        if quote_id not in quote_variants or quote_id != f"Q-{text_sha}":
            raise ValueError(f"{occ_location}.raw_quote_variant_id: unresolved/mismatched")
        scope_id = occurrence["catalog_scope_variant_id"]
        if scope_id is not None and scope_id not in catalog_variants:
            raise ValueError(f"{occ_location}.catalog_scope_variant_id: unresolved")
        if group_name not in CATALOG_GROUPS and scope_id is not None:
            raise ValueError(f"{occ_location}: catalog scope outside catalog group")
        completeness = _validate_source_completeness(
            occurrence["source_completeness"],
            location=f"{occ_location}.source_completeness",
        )
        flags = _expect_list(occurrence["risk_flags"], location=f"{occ_location}.risk_flags")
        if (
            not all(isinstance(item, str) and item in KNOWN_RISK_FLAGS for item in flags)
            or flags != sorted(set(flags))
        ):
            raise ValueError(f"{occ_location}.risk_flags: unknown or non-canonical")
        risk_tier = occurrence["risk_tier"]
        if risk_tier != _expected_risk_tier(flags):
            raise ValueError(f"{occ_location}.risk_tier: inconsistent with risk flags")
        identity_payload = {
            "group": group_name,
            "clause_fingerprint": fingerprint,
            "record_id": record_id,
            "source_sha256": source_sha,
            "doc_index": doc_index,
            "doc_id": coordinate["doc_id"],
            "source_doc_sha256": coordinate["source_doc_sha256"],
            "start": start,
            "end": end,
            "text_sha256": text_sha,
        }
        if sha256_object(identity_payload) != occurrence_id:
            raise ValueError(f"{occ_location}.occurrence_id: identity hash mismatch")
        occurrence_order.append((record_id, doc_index, start, end, occurrence_id))
        member_ids.add(record_id)
        tier_counts[risk_tier] += 1
        flag_counts.update(flags)
        completeness_counts[canonical_json(completeness)] += 1
        quote_counts[quote_id] += 1
        quote_members[quote_id].add(record_id)
        if scope_id is not None:
            scope_counts[scope_id] += 1
            scope_members[scope_id].add(record_id)
    if occurrence_order != sorted(occurrence_order):
        raise ValueError(f"{location}.occurrences: non-canonical order")

    if occurrence_count != len(occurrences):
        raise ValueError(f"{location}.occurrence_count: count mismatch")
    if row["member_record_ids"] != sorted(member_ids):
        raise ValueError(f"{location}.member_record_ids: membership mismatch")
    if member_record_count != len(member_ids):
        raise ValueError(f"{location}.member_record_count: count mismatch")
    for variant_id, variant in quote_variants.items():
        if variant["occurrence_count"] != quote_counts[variant_id] or variant[
            "member_record_count"
        ] != len(quote_members[variant_id]):
            raise ValueError(f"{location}: raw quote variant census mismatch")
    for variant_id, variant in catalog_variants.items():
        if variant["occurrence_count"] != scope_counts[variant_id] or variant[
            "member_record_count"
        ] != len(scope_members[variant_id]):
            raise ValueError(f"{location}: catalog scope variant census mismatch")

    facets = _expect_keys(
        row["risk_facets"],
        {
            "risk_tiers",
            "risk_flags",
            "source_completeness_variants",
            "clause_facets",
            "clipped",
        },
        location=f"{location}.risk_facets",
    )
    if facets["risk_tiers"] != dict(sorted(tier_counts.items())):
        raise ValueError(f"{location}.risk_facets.risk_tiers: census mismatch")
    if facets["risk_flags"] != dict(sorted(flag_counts.items())):
        raise ValueError(f"{location}.risk_facets.risk_flags: census mismatch")
    expected_completeness = [
        {"source_completeness": json.loads(key), "occurrence_count": count}
        for key, count in sorted(completeness_counts.items())
    ]
    if facets["source_completeness_variants"] != expected_completeness:
        raise ValueError(f"{location}.risk_facets.source_completeness_variants: mismatch")
    if facets["clause_facets"] != identity["facets"] or facets["clipped"] != identity["clipped"]:
        raise ValueError(f"{location}.risk_facets: fingerprint facet mismatch")
    expected_contract = {
        "safe_for": "shared_clause_reading_and_occurrence_consistency_audit_only",
        "automatic_label_or_decision_propagation_allowed": False,
        "semantic_equivalence_claimed": False,
        "each_occurrence_context_must_be_checked": True,
    }
    if row["review_contract"] != expected_contract:
        raise ValueError(f"{location}.review_contract: unsafe or unsupported contract")
    return dict(row)


def _validate_source_manifest(value: Mapping[str, Any], *, location: str) -> dict[str, Any]:
    _assert_no_forbidden_keys(value, location=location)
    manifest = _expect_keys(
        value,
        {
            "schema_version",
            "clause_row_schema_version",
            "generated_at_utc",
            "semantic_role",
            "review_only_contract",
            "input",
            "catalog_provenance",
            "normalization_provenance",
            "outputs",
            "statistics",
            "groups",
            "content_sha256",
        },
        location=location,
    )
    if manifest["schema_version"] != clause_bank.MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"{location}: unsupported clause-bank manifest schema")
    if manifest["clause_row_schema_version"] != clause_bank.SCHEMA_VERSION:
        raise ValueError(f"{location}: clause row schema mismatch")
    if (
        manifest["semantic_role"]
        != "label_free_shared_clause_review_and_occurrence_consistency_audit"
    ):
        raise ValueError(f"{location}: unsupported semantic role")
    expected_contract = {
        "automatic_label_or_decision_propagation_allowed": False,
        "semantic_equivalence_claimed": False,
        "one_review_replaces_occurrence_context_audit": False,
        "every_occurrence_must_be_checked_against_context": True,
        "labels_predictions_or_model_outputs_consumed": False,
    }
    if manifest["review_only_contract"] != expected_contract:
        raise ValueError(f"{location}.review_only_contract: unsafe contract")
    committed = {
        key: value
        for key, value in manifest.items()
        if key not in {"generated_at_utc", "content_sha256"}
    }
    if manifest["content_sha256"] != sha256_object(committed):
        raise ValueError(f"{location}.content_sha256: manifest commitment mismatch")
    provenance = _validate_catalog_provenance(
        manifest["catalog_provenance"], location=f"{location}.catalog_provenance"
    )
    source_input = _expect_keys(
        manifest["input"],
        {"path", "sha256", "records", "limit"},
        location=f"{location}.input",
    )
    if source_input["path"] is not None and not isinstance(source_input["path"], str):
        raise ValueError(f"{location}.input.path: expected string or null")
    if source_input["sha256"] is not None:
        _expect_sha(source_input["sha256"], location=f"{location}.input.sha256")
    _expect_int(source_input["records"], location=f"{location}.input.records")
    if source_input["limit"] is not None:
        _expect_int(source_input["limit"], location=f"{location}.input.limit", minimum=1)
    normalization = _expect_keys(
        manifest["normalization_provenance"],
        {
            "module",
            "module_sha256",
            "template_profile_schema_version",
            "coordinate_validation",
        },
        location=f"{location}.normalization_provenance",
    )
    if not all(
        isinstance(normalization[key], str)
        for key in ("module", "template_profile_schema_version", "coordinate_validation")
    ):
        raise ValueError(f"{location}.normalization_provenance: expected string values")
    _expect_sha(
        normalization["module_sha256"],
        location=f"{location}.normalization_provenance.module_sha256",
    )
    outputs = _expect_keys(
        manifest["outputs"], {"clauses_jsonl", "summary_markdown"}, location=f"{location}.outputs"
    )
    clauses = _expect_keys(
        outputs["clauses_jsonl"], {"path", "sha256", "bytes", "rows"}, location=f"{location}.outputs.clauses_jsonl"
    )
    if not isinstance(clauses["path"], str):
        raise ValueError(f"{location}.outputs.clauses_jsonl.path: expected string")
    _expect_sha(clauses["sha256"], location=f"{location}.outputs.clauses_jsonl.sha256")
    _expect_int(clauses["bytes"], location=f"{location}.outputs.clauses_jsonl.bytes")
    _expect_int(clauses["rows"], location=f"{location}.outputs.clauses_jsonl.rows")
    statistics = manifest["statistics"]
    if not isinstance(statistics, Mapping):
        raise ValueError(f"{location}.statistics: expected object")
    for key in ("records", "occurrences", "unique_clause_fingerprints"):
        _expect_int(statistics.get(key), location=f"{location}.statistics.{key}")
    if set(manifest["groups"]) != set(GROUPS):
        raise ValueError(f"{location}.groups: group census mismatch")
    result = dict(manifest)
    result["catalog_provenance"] = provenance
    return result


def _cluster_size_band(count: int) -> str:
    if count == 1:
        return "singleton"
    if count <= 4:
        return "2_to_4"
    if count <= 16:
        return "5_to_16"
    if count <= 64:
        return "17_to_64"
    return "65_plus"


def _variant_count_band(count: int) -> str:
    if count == 1:
        return "one"
    if count <= 4:
        return "2_to_4"
    if count <= 16:
        return "5_to_16"
    return "17_plus"


def _cluster_risk_tier(row: Mapping[str, Any]) -> str:
    tiers = row["risk_facets"]["risk_tiers"]
    return min((str(tier) for tier in tiers), key=lambda tier: RISK_ORDER[tier])


def _stratum(row: Mapping[str, Any]) -> dict[str, Any]:
    risk_flags = sorted(row["risk_facets"]["risk_flags"])
    clause_facets = row["risk_facets"]["clause_facets"]
    facet_axes = {
        "phases": clause_facets["phases"],
        "actors": clause_facets["actors"],
        "logic_classes": sorted(set(clause_facets["logic_sequence"])),
        "has_product_name_reference": bool(clause_facets["product_name_hashes"]),
    }
    facet_payload = {
        "facet_axes": facet_axes,
        "clipped": row["risk_facets"]["clipped"],
        "risk_flags": risk_flags,
    }
    axes = {
        "group": row["group"],
        "highest_risk_tier": _cluster_risk_tier(row),
        "cluster_size_band": _cluster_size_band(row["occurrence_count"]),
        "raw_quote_variant_band": _variant_count_band(row["raw_quote_variant_count"]),
        "facet_signature_sha256": sha256_object(facet_payload),
        "facet_axes": facet_axes,
        "clipped": row["risk_facets"]["clipped"],
        "risk_flags": risk_flags,
    }
    return {"stratum_id": f"S-{sha256_object(axes)[:20]}", **axes}


def _occurrence_priority(occurrence: Mapping[str, Any]) -> tuple[Any, ...]:
    coordinate = occurrence["coordinate"]
    return (
        RISK_ORDER[str(occurrence["risk_tier"])],
        tuple(occurrence["risk_flags"]),
        str(occurrence["record_id"]),
        int(coordinate["doc_index"]),
        int(coordinate["start"]),
        int(coordinate["end"]),
        str(occurrence["occurrence_id"]),
    )


def _interleave_variants(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    counts = {
        variant["variant_id"]: int(variant["occurrence_count"])
        for variant in row["raw_quote_variants"]
    }
    buckets: dict[str, collections.deque[dict[str, Any]]] = {}
    for occurrence in row["occurrences"]:
        buckets.setdefault(occurrence["raw_quote_variant_id"], collections.deque()).append(
            dict(occurrence)
        )
    for variant_id, values in list(buckets.items()):
        buckets[variant_id] = collections.deque(sorted(values, key=_occurrence_priority))
    variant_order = sorted(buckets, key=lambda value: (-counts[value], value))
    ordered: list[dict[str, Any]] = []
    active = collections.deque(variant_order)
    while active:
        variant_id = active.popleft()
        ordered.append(buckets[variant_id].popleft())
        if buckets[variant_id]:
            active.append(variant_id)
    return ordered


def _packet_id(
    *, source_bank_sha256: str, row_sha256: str, occurrence_ids: Sequence[str]
) -> str:
    return "P-" + sha256_object(
        {
            "source_clause_bank_sha256": source_bank_sha256,
            "source_row_sha256": row_sha256,
            "occurrence_ids": list(occurrence_ids),
        }
    )


def _build_packet(
    row: Mapping[str, Any],
    occurrences: Sequence[Mapping[str, Any]],
    *,
    source_bank_sha256: str,
    source_manifest_sha256: str,
    source_manifest_content_sha256: str,
    source_row_sha256: str,
    packet_index: int,
    packet_count: int,
    quote_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    scope_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    cluster_counts: Mapping[str, int] | None = None,
    stratum: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    quote_by_id = quote_by_id or {
        value["variant_id"]: value for value in row["raw_quote_variants"]
    }
    scope_by_id = scope_by_id or {
        value["variant_id"]: value for value in row["catalog_scope_variants"]
    }
    quote_ids = sorted({str(value["raw_quote_variant_id"]) for value in occurrences})
    scope_ids = sorted(
        {
            str(value["catalog_scope_variant_id"])
            for value in occurrences
            if value["catalog_scope_variant_id"] is not None
        }
    )
    cluster_counts = cluster_counts or {
        value["variant_id"]: int(value["occurrence_count"])
        for value in row["raw_quote_variants"]
    }
    representative_rows: list[dict[str, str]] = []
    for variant_id in sorted(quote_ids, key=lambda value: (-cluster_counts[value], value)):
        candidates = [
            value for value in occurrences if value["raw_quote_variant_id"] == variant_id
        ]
        representative = min(candidates, key=_occurrence_priority)
        representative_rows.append(
            {
                "raw_quote_variant_id": variant_id,
                "occurrence_id": str(representative["occurrence_id"]),
            }
        )
    occurrence_ids = [str(value["occurrence_id"]) for value in occurrences]
    packet_id = _packet_id(
        source_bank_sha256=source_bank_sha256,
        row_sha256=source_row_sha256,
        occurrence_ids=occurrence_ids,
    )
    stratum = dict(stratum or _stratum(row))
    risk_counts = collections.Counter(str(value["risk_tier"]) for value in occurrences)
    return {
        "schema_version": PACKET_SCHEMA_VERSION,
        "row_kind": "bounded_clause_occurrence_review_packet",
        "packet_id": packet_id,
        "source_provenance": {
            "clause_bank_sha256": source_bank_sha256,
            "clause_bank_manifest_sha256": source_manifest_sha256,
            "clause_bank_manifest_content_sha256": source_manifest_content_sha256,
            "clause_bank_row_sha256": source_row_sha256,
        },
        "group": row["group"],
        "target_items": row["target_items"],
        "cluster": {
            "cluster_id": row["cluster_id"],
            "clause_fingerprint": row["clause_fingerprint"],
            "fingerprint_identity": row["fingerprint_identity"],
            "occurrence_count": row["occurrence_count"],
            "member_record_count": row["member_record_count"],
            "raw_quote_variant_count": row["raw_quote_variant_count"],
        },
        "partition": {
            "packet_index": packet_index,
            "packet_count": packet_count,
            "occurrences_in_packet": len(occurrences),
            "occurrence_ids_sha256": sha256_object(occurrence_ids),
        },
        "stratum": stratum,
        "packet_risk_tier_counts": dict(sorted(risk_counts.items())),
        "representatives": {
            "selection_rule": "most_frequent_raw_variant_then_highest_risk_occurrence",
            "primary_occurrence_id": representative_rows[0]["occurrence_id"],
            "raw_quote_variant_representatives": representative_rows,
        },
        "raw_quote_variants": [quote_by_id[value] for value in quote_ids],
        "catalog_provenance": row["catalog_provenance"],
        "catalog_provenance_sha256": row["catalog_provenance_sha256"],
        "catalog_scope_variants": [scope_by_id[value] for value in scope_ids],
        "occurrences": list(occurrences),
        "review_contract": {
            "safe_for": "bounded_shared_clause_reading_and_occurrence_consistency_audit_only",
            "automatic_label_or_decision_propagation_allowed": False,
            "semantic_equivalence_claimed": False,
            "representative_decision_applies_to_other_occurrences": False,
            "each_occurrence_context_must_be_checked": True,
            "packet_is_a_final_record_decision": False,
        },
    }


def _split_bounded_packets(
    row: Mapping[str, Any],
    *,
    max_occurrences_per_packet: int,
    max_packet_bytes: int,
    source_bank_sha256: str,
    source_manifest_sha256: str,
    source_manifest_content_sha256: str,
    source_row_sha256: str,
) -> list[list[dict[str, Any]]]:
    ordered = _interleave_variants(row)
    pending = collections.deque(
        ordered[index : index + max_occurrences_per_packet]
        for index in range(0, len(ordered), max_occurrences_per_packet)
    )
    quote_by_id = {
        value["variant_id"]: value for value in row["raw_quote_variants"]
    }
    scope_by_id = {
        value["variant_id"]: value for value in row["catalog_scope_variants"]
    }
    cluster_counts = {
        value["variant_id"]: int(value["occurrence_count"])
        for value in row["raw_quote_variants"]
    }
    stratum = _stratum(row)
    bounded: list[list[dict[str, Any]]] = []
    while pending:
        chunk = pending.popleft()
        probe = _build_packet(
            row,
            chunk,
            source_bank_sha256=source_bank_sha256,
            source_manifest_sha256=source_manifest_sha256,
            source_manifest_content_sha256=source_manifest_content_sha256,
            source_row_sha256=source_row_sha256,
            packet_index=999_999_999,
            packet_count=999_999_999,
            quote_by_id=quote_by_id,
            scope_by_id=scope_by_id,
            cluster_counts=cluster_counts,
            stratum=stratum,
        )
        encoded_bytes = len((canonical_json(probe) + "\n").encode("utf-8"))
        if encoded_bytes <= max_packet_bytes:
            bounded.append(chunk)
            continue
        if len(chunk) == 1:
            raise ValueError(
                f"single occurrence packet exceeds max_packet_bytes={max_packet_bytes}: "
                f"{row['cluster_id']}:{chunk[0]['occurrence_id']} ({encoded_bytes} bytes)"
            )
        split_at = (len(chunk) + 1) // 2
        pending.appendleft(chunk[split_at:])
        pending.appendleft(chunk[:split_at])
    return bounded


def _priority_key(row: Mapping[str, Any], packet_index: int) -> str:
    stratum = _stratum(row)
    cluster_rank = {
        "65_plus": 0,
        "17_to_64": 1,
        "5_to_16": 2,
        "2_to_4": 3,
        "singleton": 4,
    }[stratum["cluster_size_band"]]
    variant_rank = {
        "17_plus": 0,
        "5_to_16": 1,
        "2_to_4": 2,
        "one": 3,
    }[stratum["raw_quote_variant_band"]]
    group_rank = list(GROUPS).index(str(row["group"]))
    clipped_rank = 0 if stratum["clipped"] else 1
    return (
        f"{RISK_ORDER[stratum['highest_risk_tier']]:02d}|{clipped_rank:02d}|"
        f"{variant_rank:02d}|{cluster_rank:02d}|{group_rank:02d}|"
        f"{row['clause_fingerprint']}|{packet_index:09d}"
    )


def _temporary_path(destination: pathlib.Path, *, suffix: str = ".tmp") -> pathlib.Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.",
        suffix=suffix,
        dir=destination.parent,
        delete=False,
    )
    handle.close()
    return pathlib.Path(handle.name)


def _write_text_temp(destination: pathlib.Path, text: str) -> pathlib.Path:
    temporary = _temporary_path(destination)
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    return temporary


def _connect_spool(path: pathlib.Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=FILE")
    connection.executescript(
        """
        CREATE TABLE review_index (
            priority_key TEXT NOT NULL,
            packet_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE INDEX review_index_by_priority
            ON review_index(priority_key, packet_id);
        CREATE TABLE occurrences (
            occurrence_id TEXT PRIMARY KEY,
            packet_id TEXT NOT NULL
        ) WITHOUT ROWID;
        """
    )
    return connection


def _render_summary(manifest: Mapping[str, Any]) -> str:
    statistics = manifest["statistics"]
    lines = [
        "# 독립 clause review packets",
        "",
        "> fingerprint 대표는 읽기 보조일 뿐이다. 대표 판정의 자동 전파, 의미 동치 간주, 최종 record 정답 사용은 금지된다.",
        "",
        f"- 입력 clause fingerprint: {statistics['clusters']:,}개",
        f"- 검토 패킷: {statistics['packets']:,}개",
        f"- occurrence: {statistics['occurrences']:,}개 (각 1회 배정)",
        f"- 패킷당 최대 occurrence: {manifest['configuration']['max_occurrences_per_packet']:,}개",
        f"- 패킷 JSONL 행 최대 바이트: {statistics['largest_packet_bytes']:,} bytes",
        "",
        "| 그룹 | clusters | packets | occurrences | critical | high | standard |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for group_name in GROUPS:
        row = manifest["groups"][group_name]
        risks = row["occurrence_risk_tiers"]
        lines.append(
            f"| {group_name} | {row['clusters']:,} | {row['packets']:,} | "
            f"{row['occurrences']:,} | {risks.get('critical', 0):,} | "
            f"{risks.get('high', 0):,} | {risks.get('standard', 0):,} |"
        )
    lines.extend(
        [
            "",
            "## 강제 검토 경계",
            "",
            "- 모든 occurrence는 정확히 한 패킷에만 배정된다.",
            "- 패킷은 참조하는 모든 raw quote variant와 variant별 대표 occurrence를 보존한다.",
            "- occurrence의 record/source/document/coordinate/text hash와 catalog provenance는 원 bank 값 그대로다.",
            "- review index는 critical → high → standard, clipped, 변형 수, cluster 크기 순으로 정렬한 작업 대기열이다.",
            "- 어떤 대표 검토도 다른 occurrence의 판정을 대체하지 않는다.",
            "",
            f"Packets SHA-256: `{manifest['outputs']['packets_jsonl']['sha256']}`",
            f"Review index SHA-256: `{manifest['outputs']['review_index_jsonl']['sha256']}`",
            "",
        ]
    )
    return "\n".join(lines)


def build_review_packets(
    *,
    clause_bank_path: pathlib.Path,
    clause_bank_manifest_path: pathlib.Path,
    packets_output: pathlib.Path,
    review_index_output: pathlib.Path,
    manifest_output: pathlib.Path,
    summary_output: pathlib.Path | None = None,
    max_occurrences_per_packet: int = 24,
    max_packet_bytes: int = 262_144,
) -> dict[str, Any]:
    """Validate, packetize, and atomically publish a clause review queue."""

    if max_occurrences_per_packet < 2:
        raise ValueError("max_occurrences_per_packet must be >= 2")
    if max_packet_bytes <= 0:
        raise ValueError("max_packet_bytes must be positive")
    inputs = {clause_bank_path.resolve(), clause_bank_manifest_path.resolve()}
    destinations = {
        packets_output.resolve(),
        review_index_output.resolve(),
        manifest_output.resolve(),
    }
    if summary_output is not None:
        destinations.add(summary_output.resolve())
    expected_destination_count = 4 if summary_output is not None else 3
    if len(destinations) != expected_destination_count:
        raise ValueError("packet, index, manifest, and summary destinations must differ")
    if inputs & destinations:
        raise ValueError("input and output paths must differ")

    manifest_bytes = clause_bank_manifest_path.read_bytes()
    try:
        manifest_text = manifest_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("clause-bank manifest is not UTF-8") from exc
    source_manifest = _validate_source_manifest(
        _loads_object(manifest_text, location=str(clause_bank_manifest_path)),
        location=str(clause_bank_manifest_path),
    )
    source_manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    source_descriptor = source_manifest["outputs"]["clauses_jsonl"]
    expected_bank_sha = source_descriptor["sha256"]

    for destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
    packet_temp = _temporary_path(packets_output)
    index_temp: pathlib.Path | None = None
    summary_temp: pathlib.Path | None = None
    manifest_temp: pathlib.Path | None = None
    spool_path = _temporary_path(review_index_output, suffix=".sqlite3")
    connection: sqlite3.Connection | None = None
    packet_digest = hashlib.sha256()
    bank_digest = hashlib.sha256()
    packet_rows = 0
    cluster_rows = 0
    occurrence_rows = 0
    input_bytes = 0
    largest_packet_bytes = 0
    previous_row_key: tuple[int, str] | None = None
    group_stats: dict[str, dict[str, Any]] = {
        group_name: {
            "clusters": 0,
            "packets": 0,
            "occurrences": 0,
            "occurrence_risk_tiers": collections.Counter(),
        }
        for group_name in GROUPS
    }
    strata_clusters: collections.Counter[str] = collections.Counter()
    strata_packets: collections.Counter[str] = collections.Counter()
    strata_occurrences: collections.Counter[str] = collections.Counter()
    strata_payloads: dict[str, dict[str, Any]] = {}
    try:
        connection = _connect_spool(spool_path)
        with clause_bank_path.open("rb") as source, packet_temp.open("wb") as packet_handle:
            for line_number, raw_line in enumerate(source, 1):
                input_bytes += len(raw_line)
                bank_digest.update(raw_line)
                if not raw_line or raw_line in {b"\n", b"\r\n"}:
                    raise ValueError(f"{clause_bank_path}:{line_number}: blank row is forbidden")
                try:
                    text_line = raw_line.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError(
                        f"{clause_bank_path}:{line_number}: row is not UTF-8"
                    ) from exc
                if not text_line.endswith("\n") or text_line.endswith("\r\n"):
                    raise ValueError(
                        f"{clause_bank_path}:{line_number}: expected canonical LF-terminated row"
                    )
                row = validate_clause_bank_row(
                    _loads_object(
                        text_line[:-1], location=f"{clause_bank_path}:{line_number}"
                    ),
                    manifest_catalog_provenance=source_manifest["catalog_provenance"],
                    location=f"{clause_bank_path}:{line_number}",
                )
                if (canonical_json(row) + "\n").encode("utf-8") != raw_line:
                    raise ValueError(
                        f"{clause_bank_path}:{line_number}: non-canonical JSON encoding"
                    )
                row_key = (list(GROUPS).index(row["group"]), row["clause_fingerprint"])
                if previous_row_key is not None and row_key <= previous_row_key:
                    raise ValueError(
                        f"{clause_bank_path}:{line_number}: duplicate or non-canonical cluster order"
                    )
                previous_row_key = row_key
                row_sha = sha256_text(canonical_json(row))
                chunks = _split_bounded_packets(
                    row,
                    max_occurrences_per_packet=max_occurrences_per_packet,
                    max_packet_bytes=max_packet_bytes,
                    source_bank_sha256=expected_bank_sha,
                    source_manifest_sha256=source_manifest_sha,
                    source_manifest_content_sha256=source_manifest["content_sha256"],
                    source_row_sha256=row_sha,
                )
                stratum = _stratum(row)
                quote_by_id = {
                    value["variant_id"]: value
                    for value in row["raw_quote_variants"]
                }
                scope_by_id = {
                    value["variant_id"]: value
                    for value in row["catalog_scope_variants"]
                }
                cluster_counts = {
                    value["variant_id"]: int(value["occurrence_count"])
                    for value in row["raw_quote_variants"]
                }
                strata_payloads[stratum["stratum_id"]] = stratum
                strata_clusters[stratum["stratum_id"]] += 1
                group = group_stats[row["group"]]
                group["clusters"] += 1
                group["occurrences"] += row["occurrence_count"]
                for occurrence in row["occurrences"]:
                    group["occurrence_risk_tiers"][occurrence["risk_tier"]] += 1

                for packet_index, chunk in enumerate(chunks):
                    packet = _build_packet(
                        row,
                        chunk,
                        source_bank_sha256=expected_bank_sha,
                        source_manifest_sha256=source_manifest_sha,
                        source_manifest_content_sha256=source_manifest["content_sha256"],
                        source_row_sha256=row_sha,
                        packet_index=packet_index,
                        packet_count=len(chunks),
                        quote_by_id=quote_by_id,
                        scope_by_id=scope_by_id,
                        cluster_counts=cluster_counts,
                        stratum=stratum,
                    )
                    encoded = (canonical_json(packet) + "\n").encode("utf-8")
                    if len(encoded) > max_packet_bytes:
                        raise AssertionError("bounded packet exceeded configured byte limit")
                    packet_sha = hashlib.sha256(encoded[:-1]).hexdigest()
                    packet_handle.write(encoded)
                    packet_digest.update(encoded)
                    largest_packet_bytes = max(largest_packet_bytes, len(encoded))
                    packet_rows += 1
                    group["packets"] += 1
                    strata_packets[stratum["stratum_id"]] += 1
                    strata_occurrences[stratum["stratum_id"]] += len(chunk)
                    index_payload = {
                        "schema_version": INDEX_SCHEMA_VERSION,
                        "row_kind": "stratified_clause_review_queue_entry",
                        "packet_id": packet["packet_id"],
                        "packet_sha256": packet_sha,
                        "packet_line_number": packet_rows,
                        "group": row["group"],
                        "target_items": row["target_items"],
                        "cluster_id": row["cluster_id"],
                        "clause_fingerprint": row["clause_fingerprint"],
                        "packet_index": packet_index,
                        "packet_count": len(chunks),
                        "occurrence_count": len(chunk),
                        "primary_representative_occurrence_id": packet["representatives"][
                            "primary_occurrence_id"
                        ],
                        "raw_quote_variant_ids": [
                            value["variant_id"] for value in packet["raw_quote_variants"]
                        ],
                        "stratum": stratum,
                        "review_priority": {
                            "order": "risk_then_clipped_then_variant_diversity_then_cluster_size",
                            "priority_key": _priority_key(row, packet_index),
                        },
                        "source_clause_bank_row_sha256": row_sha,
                    }
                    try:
                        connection.execute(
                            "INSERT INTO review_index VALUES (?, ?, ?)",
                            (
                                index_payload["review_priority"]["priority_key"],
                                packet["packet_id"],
                                canonical_json(index_payload),
                            ),
                        )
                        for occurrence in chunk:
                            connection.execute(
                                "INSERT INTO occurrences VALUES (?, ?)",
                                (occurrence["occurrence_id"], packet["packet_id"]),
                            )
                    except sqlite3.IntegrityError as exc:
                        raise ValueError(
                            f"duplicate packet or occurrence identity at {row['cluster_id']}"
                        ) from exc
                    occurrence_rows += len(chunk)
                cluster_rows += 1
                if cluster_rows % 1_000 == 0:
                    connection.commit()
            packet_handle.flush()
            os.fsync(packet_handle.fileno())
        connection.commit()

        actual_bank_sha = bank_digest.hexdigest()
        if actual_bank_sha != expected_bank_sha:
            raise ValueError(
                f"clause-bank SHA-256 mismatch: manifest={expected_bank_sha}, actual={actual_bank_sha}"
            )
        if input_bytes != source_descriptor["bytes"]:
            raise ValueError("clause-bank byte count differs from manifest")
        if cluster_rows != source_descriptor["rows"]:
            raise ValueError("clause-bank row count differs from manifest")
        if cluster_rows != source_manifest["statistics"]["unique_clause_fingerprints"]:
            raise ValueError("clause-bank cluster census differs from manifest")
        if occurrence_rows != source_manifest["statistics"]["occurrences"]:
            raise ValueError("clause-bank occurrence census differs from manifest")
        assigned = int(connection.execute("SELECT COUNT(*) FROM occurrences").fetchone()[0])
        if assigned != occurrence_rows:
            raise ValueError("not every occurrence was assigned exactly once")

        index_temp = _temporary_path(review_index_output)
        index_digest = hashlib.sha256()
        index_rows = 0
        with index_temp.open("wb") as index_handle:
            for payload_json, in connection.execute(
                "SELECT payload_json FROM review_index ORDER BY priority_key, packet_id"
            ):
                payload = json.loads(payload_json)
                index_rows += 1
                payload["queue_position"] = index_rows
                encoded = (canonical_json(payload) + "\n").encode("utf-8")
                index_handle.write(encoded)
                index_digest.update(encoded)
            index_handle.flush()
            os.fsync(index_handle.fileno())
        if index_rows != packet_rows:
            raise ValueError("packet/review-index row count mismatch")

        strata = []
        for stratum_id in sorted(strata_payloads):
            strata.append(
                {
                    **strata_payloads[stratum_id],
                    "clusters": strata_clusters[stratum_id],
                    "packets": strata_packets[stratum_id],
                    "occurrences": strata_occurrences[stratum_id],
                }
            )
        normalized_group_stats: dict[str, dict[str, Any]] = {}
        for group_name in GROUPS:
            row = group_stats[group_name]
            normalized_group_stats[group_name] = {
                "clusters": row["clusters"],
                "packets": row["packets"],
                "occurrences": row["occurrences"],
                "occurrence_risk_tiers": dict(sorted(row["occurrence_risk_tiers"].items())),
            }
        manifest: dict[str, Any] = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "packet_schema_version": PACKET_SCHEMA_VERSION,
            "index_schema_version": INDEX_SCHEMA_VERSION,
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "semantic_role": "bounded_stratified_independent_clause_occurrence_review_queue",
            "source_boundary": {
                "only_source_derived_clause_bank_fields_consumed": True,
                "labels_predictions_or_model_outputs_consumed": False,
                "external_notice_or_legal_data_consumed": False,
            },
            "review_only_contract": {
                "automatic_label_or_decision_propagation_allowed": False,
                "semantic_equivalence_claimed": False,
                "representative_decision_applies_to_other_occurrences": False,
                "one_review_replaces_occurrence_context_audit": False,
                "every_occurrence_assigned_exactly_once": True,
                "every_occurrence_context_must_be_checked": True,
                "packets_are_final_record_decisions": False,
            },
            "configuration": {
                "max_occurrences_per_packet": max_occurrences_per_packet,
                "max_packet_bytes": max_packet_bytes,
                "cluster_size_bands": {
                    "singleton": [1, 1],
                    "2_to_4": [2, 4],
                    "5_to_16": [5, 16],
                    "17_to_64": [17, 64],
                    "65_plus": [65, None],
                },
                "raw_quote_variant_bands": {
                    "one": [1, 1],
                    "2_to_4": [2, 4],
                    "5_to_16": [5, 16],
                    "17_plus": [17, None],
                },
                "review_queue_order": [
                    "highest_risk_tier: critical, high, standard",
                    "clipped clause first",
                    "higher raw-quote variant-count band first",
                    "larger occurrence-count band first",
                    "group, clause fingerprint, packet index",
                ],
            },
            "input": {
                "clause_bank": {
                    "path": _relative_or_absolute(clause_bank_path),
                    "sha256": actual_bank_sha,
                    "bytes": input_bytes,
                    "rows": cluster_rows,
                    "occurrences": occurrence_rows,
                },
                "clause_bank_manifest": {
                    "path": _relative_or_absolute(clause_bank_manifest_path),
                    "sha256": source_manifest_sha,
                    "content_sha256": source_manifest["content_sha256"],
                },
                "organizer_source": source_manifest["input"],
                "catalog_provenance": source_manifest["catalog_provenance"],
                "normalization_provenance": source_manifest["normalization_provenance"],
            },
            "outputs": {
                "packets_jsonl": {
                    "path": str(packets_output),
                    "sha256": packet_digest.hexdigest(),
                    "bytes": packet_temp.stat().st_size,
                    "rows": packet_rows,
                },
                "review_index_jsonl": {
                    "path": str(review_index_output),
                    "sha256": index_digest.hexdigest(),
                    "bytes": index_temp.stat().st_size,
                    "rows": index_rows,
                },
                "summary_markdown": None,
            },
            "statistics": {
                "clusters": cluster_rows,
                "packets": packet_rows,
                "occurrences": occurrence_rows,
                "unique_occurrences_assigned": assigned,
                "largest_packet_bytes": largest_packet_bytes,
                "strata": len(strata),
            },
            "groups": normalized_group_stats,
            "strata": strata,
        }
        if summary_output is not None:
            summary_temp = _write_text_temp(summary_output, _render_summary(manifest))
            manifest["outputs"]["summary_markdown"] = {
                "path": str(summary_output),
                "sha256": file_sha256(summary_temp),
                "bytes": summary_temp.stat().st_size,
            }
        manifest["content_sha256"] = sha256_object(
            {
                key: value
                for key, value in manifest.items()
                if key not in {"generated_at_utc", "content_sha256"}
            }
        )
        manifest_temp = _write_text_temp(
            manifest_output,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

        # The manifest is the publication seal and is therefore replaced last.
        packet_temp.replace(packets_output)
        packet_temp = None
        index_temp.replace(review_index_output)
        index_temp = None
        if summary_output is not None and summary_temp is not None:
            summary_temp.replace(summary_output)
            summary_temp = None
        manifest_temp.replace(manifest_output)
        manifest_temp = None
        return manifest
    finally:
        if connection is not None:
            connection.close()
        for temporary in (
            packet_temp,
            index_temp,
            summary_temp,
            manifest_temp,
            spool_path,
        ):
            if temporary is not None and temporary.exists():
                temporary.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build bounded, stratified, label-free clause review packets."
    )
    parser.add_argument("--clause-bank", type=pathlib.Path, required=True)
    parser.add_argument("--clause-bank-manifest", type=pathlib.Path, required=True)
    parser.add_argument("--packets-jsonl", type=pathlib.Path, required=True)
    parser.add_argument("--review-index-jsonl", type=pathlib.Path, required=True)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--summary-md", type=pathlib.Path)
    parser.add_argument("--max-occurrences-per-packet", type=int, default=24)
    parser.add_argument("--max-packet-bytes", type=int, default=262_144)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = build_review_packets(
        clause_bank_path=args.clause_bank,
        clause_bank_manifest_path=args.clause_bank_manifest,
        packets_output=args.packets_jsonl,
        review_index_output=args.review_index_jsonl,
        manifest_output=args.manifest,
        summary_output=args.summary_md,
        max_occurrences_per_packet=args.max_occurrences_per_packet,
        max_packet_bytes=args.max_packet_bytes,
    )
    print(
        json.dumps(
            {
                "clusters": manifest["statistics"]["clusters"],
                "packets": manifest["statistics"]["packets"],
                "occurrences": manifest["statistics"]["occurrences"],
                "largest_packet_bytes": manifest["statistics"]["largest_packet_bytes"],
                "packets_sha256": manifest["outputs"]["packets_jsonl"]["sha256"],
                "review_index_sha256": manifest["outputs"]["review_index_jsonl"]["sha256"],
                "automatic_label_or_decision_propagation_allowed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "GROUPS",
    "INDEX_SCHEMA_VERSION",
    "MANIFEST_SCHEMA_VERSION",
    "PACKET_SCHEMA_VERSION",
    "build_review_packets",
    "canonical_json",
    "sha256_object",
    "validate_clause_bank_row",
]
