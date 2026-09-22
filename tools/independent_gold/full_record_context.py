"""Complete, source-grounded context for one-call 24-item annotation.

The organizer's supplied documents are represented exactly once as a gapless
registry of short, immutable spans.  The registry is the full source: document
manifests intentionally do not carry another copy of ``text``.  Existing
independent fact, qualification, and law context builders remain the semantic
layer.  Every child is validated before it is admitted to this context.

This module only reads organizer records and the organizer-supplied law and
catalog snapshots used by the child builders.  It does not read competition
answers, predictions, or model responses.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

try:
    from tools.independent_gold import (
        fact_context,
        law_context,
        qualification_context,
    )
except ModuleNotFoundError:  # Direct execution from this directory.
    import fact_context  # type: ignore[no-redef]
    import law_context  # type: ignore[no-redef]
    import qualification_context  # type: ignore[no-redef]


SCHEMA_VERSION = "dacon.independent.full_record_context.v1"
TARGET_ITEMS = tuple(f"v{number}" for number in range(1, 25))
GROUPS = tuple(fact_context.GROUPS)
QUALIFICATION_GROUPS = tuple(qualification_context.GROUP_FAMILIES)

MAX_SOURCE_CHARS = 100_000
MAX_SOURCE_SPAN_CHARS = 480
MAX_RENDERED_CHARS = 300_000

_EXPECTED_FACT_SCHEMA = "dacon.independent.fact_context.v4"
_EXPECTED_QUALIFICATION_SCHEMA = "dacon.independent.qualification_context.v4"
_EXPECTED_LAW_SCHEMA = "dacon.independent.law_context.v4"
_SEMANTIC_ROLE = "complete_source_context_for_one_call_24_item_annotation"

# Exact data-bearing keys are rejected.  Semantic booleans such as
# ``is_label_decision: false`` in a qualification relation are not answers and
# are therefore deliberately not matched by this exact-key guard.
_FORBIDDEN_DATA_KEYS = frozenset(
    {
        "answer",
        "answers",
        "ground_truth",
        "label",
        "labels",
        "model_output",
        "model_outputs",
        "model_response",
        "model_responses",
        "prediction",
        "predictions",
    }
)

_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "semantic_role",
        "group",
        "target_items",
        "organizer_record",
        "source_completeness",
        "allowed_span_registry",
        "fact_contexts",
        "qualification_contexts",
        "law_contexts",
        "law_reference_registry",
        "child_context_manifest",
        "bounds",
        "context_sha256",
    }
)


class FullRecordContextError(ValueError):
    """Raised when a complete context cannot be built without information loss."""


def canonical_json(value: Any) -> str:
    """Return the one canonical JSON representation used for hashes and bounds."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_object(value: Any) -> str:
    return sha256_text(canonical_json(value))


def _forbidden_key_paths(value: Any, path: str = "record") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if key.casefold() in _FORBIDDEN_DATA_KEYS:
                found.append(child_path)
            found.extend(_forbidden_key_paths(child, child_path))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found.extend(_forbidden_key_paths(child, f"{path}[{index}]"))
    return found


def _record_id(record: Mapping[str, Any]) -> str:
    raw = record.get("id")
    if not isinstance(raw, (str, int)) or isinstance(raw, bool):
        raise FullRecordContextError("record.id must be a non-empty string or integer")
    result = str(raw)
    if not result:
        raise FullRecordContextError("record.id must not be empty")
    return result


def _documents(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_documents = record.get("docs")
    if not isinstance(raw_documents, list) or not raw_documents:
        raise FullRecordContextError("record.docs must be a non-empty array")
    documents: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_documents):
        if not isinstance(raw, Mapping):
            raise FullRecordContextError(f"record.docs[{index}] must be an object")
        text = raw.get("text")
        if not isinstance(text, str):
            raise FullRecordContextError(
                f"record.docs[{index}].text must be an exact source string"
            )
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


def _candidate_boundaries(text: str, start: int, end: int) -> tuple[list[int], ...]:
    """Return newline, sentence, and whitespace boundaries in preference order."""

    newlines: list[int] = []
    sentences: list[int] = []
    whitespace: list[int] = []
    for index in range(start, end):
        char = text[index]
        after = index + 1
        if char == "\n" or (char == "\r" and (after == len(text) or text[after] != "\n")):
            newlines.append(after)
        if char in ".!?。！？":
            sentences.append(after)
        if char.isspace() and (after == len(text) or not text[after].isspace()):
            whitespace.append(after)
    return newlines, sentences, whitespace


def _next_span_end(text: str, start: int) -> int:
    hard_end = min(len(text), start + MAX_SOURCE_SPAN_CHARS)
    if hard_end == len(text):
        return hard_end

    # Prefer a natural boundary in the final third of the permitted span.  If
    # none exists there, any earlier natural boundary is still preferable to a
    # mid-token cut.  The hard coordinate is the deterministic last resort.
    preferred_start = max(start + 1, hard_end - MAX_SOURCE_SPAN_CHARS // 3)
    for search_start in (preferred_start, start):
        for candidates in _candidate_boundaries(text, search_start, hard_end):
            if candidates:
                return candidates[-1]
    return hard_end


def _source_registry(
    documents: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    manifest: list[dict[str, Any]] = []
    registry: dict[str, dict[str, Any]] = {}
    for document in documents:
        doc_index = int(document["doc_index"])
        text = str(document["text"])
        span_ids: list[str] = []
        start = 0
        ordinal = 0
        while start < len(text):
            end = _next_span_end(text, start)
            if not (start < end <= min(len(text), start + MAX_SOURCE_SPAN_CHARS)):
                raise FullRecordContextError(
                    f"internal span splitter failure for document {doc_index} at {start}"
                )
            span_id = f"SRC-D{doc_index:04d}-S{ordinal:06d}"
            if span_id in registry:
                raise FullRecordContextError(f"duplicate source span id: {span_id}")
            quote = text[start:end]
            registry[span_id] = {
                "span_id": span_id,
                "doc_index": doc_index,
                "doc_id": document["doc_id"],
                "doc_type": document["doc_type"],
                "start": start,
                "end": end,
                "quote": quote,
                "source_doc_sha256": document["sha256"],
                "text_sha256": sha256_text(quote),
            }
            span_ids.append(span_id)
            start = end
            ordinal += 1
        manifest.append(
            {
                "doc_index": doc_index,
                "doc_id": document["doc_id"],
                "doc_type": document["doc_type"],
                "chars": document["chars"],
                "sha256": document["sha256"],
                # An empty supplied document is represented explicitly here
                # with an empty span list; no zero-length citation is invented.
                "span_ids": span_ids,
            }
        )
    return manifest, registry


def _law_source_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    source = row.get("source") or {}
    return {
        "relative_path": source.get("relative_path"),
        "source_sha256": source.get("source_sha256"),
        "start": source.get("start"),
        "end": source.get("end"),
        "quote_sha256": source.get("quote_sha256"),
    }


def _law_registry_id(identity: Mapping[str, Any]) -> str:
    return f"LAW-{sha256_object(identity)}"


def _ordered_union(values: set[str], order: Sequence[str]) -> list[str]:
    rank = {value: index for index, value in enumerate(order)}
    return sorted(values, key=lambda value: (rank.get(value, len(rank)), value))


def _build_law_bundle() -> tuple[dict[str, Any], dict[str, Any]]:
    """Build/validate all v4 children and safely deduplicate their source text."""

    raw_contexts: dict[str, dict[str, Any]] = {}
    for group in GROUPS:
        child = law_context.build_law_context(group)
        errors = law_context.validate_law_context(child)
        if errors:
            raise FullRecordContextError(
                f"law context {group} failed validation: {errors[:5]}"
            )
        if child.get("schema_version") != _EXPECTED_LAW_SCHEMA:
            raise FullRecordContextError(
                f"law context {group} does not use required v4 schema"
            )
        raw_contexts[group] = child

    registry_work: dict[str, dict[str, Any]] = {}
    group_descriptors: dict[str, dict[str, Any]] = {}
    for group in GROUPS:
        child = raw_contexts[group]
        links: list[dict[str, Any]] = []
        for row in child.get("references") or []:
            identity = _law_source_identity(row)
            registry_id = _law_registry_id(identity)
            source = row.get("source") or {}
            source_without_anchor = {
                key: copy.deepcopy(source.get(key))
                for key in (
                    "relative_path",
                    "source_sha256",
                    "start",
                    "end",
                    "quote",
                    "quote_sha256",
                )
            }
            anchor = {
                key: copy.deepcopy(source.get(key))
                for key in ("anchor", "anchor_start", "anchor_end")
            }
            existing = registry_work.get(registry_id)
            if existing is None:
                existing = {
                    "registry_id": registry_id,
                    "source_identity": identity,
                    "source": source_without_anchor,
                    "anchors": {},
                    "reference_ids": set(),
                    "topics": set(),
                    "priorities": set(),
                    "applicable_groups": set(),
                    "applicable_items": set(),
                }
                registry_work[registry_id] = existing
            elif (
                existing["source_identity"] != identity
                or existing["source"] != source_without_anchor
            ):
                raise FullRecordContextError(
                    f"unsafe law source identity collision: {registry_id}"
                )
            anchor_key = canonical_json(anchor)
            existing["anchors"][anchor_key] = anchor
            existing["reference_ids"].add(str(row.get("reference_id") or ""))
            existing["topics"].add(str(row.get("topic") or ""))
            existing["priorities"].add(int(row.get("priority", 0)))
            existing["applicable_groups"].add(group)
            existing["applicable_items"].update(
                str(item) for item in row.get("support_items") or []
            )
            links.append(
                {
                    "registry_id": registry_id,
                    "reference_id": row.get("reference_id"),
                    "topic": row.get("topic"),
                    "support_items": copy.deepcopy(row.get("support_items") or []),
                    "priority": row.get("priority"),
                    **anchor,
                }
            )

        group_descriptors[group] = {
            "schema_version": child.get("schema_version"),
            "semantic_role": child.get("semantic_role"),
            "group": child.get("group"),
            "target_items": copy.deepcopy(child.get("target_items") or []),
            "law_root": child.get("law_root"),
            "reference_links": links,
            "omissions": copy.deepcopy(child.get("omissions") or {}),
            "bounds": copy.deepcopy(child.get("bounds") or {}),
            "child_context_sha256": child.get("context_sha256"),
        }

    registry: dict[str, Any] = {}
    for registry_id in sorted(registry_work):
        row = registry_work[registry_id]
        registry[registry_id] = {
            "registry_id": registry_id,
            "source_identity": row["source_identity"],
            "source": row["source"],
            "anchors": [row["anchors"][key] for key in sorted(row["anchors"])],
            "reference_ids": sorted(row["reference_ids"]),
            "topics": sorted(row["topics"]),
            "priorities": sorted(row["priorities"]),
            "applicable_groups": _ordered_union(row["applicable_groups"], GROUPS),
            "applicable_items": _ordered_union(row["applicable_items"], TARGET_ITEMS),
        }
    return group_descriptors, registry


@functools.lru_cache(maxsize=1)
def _cached_law_bundle_json() -> str:
    """Cache immutable organizer law context across a 20,000-record run."""

    children, registry = _build_law_bundle()
    return canonical_json({"children": children, "registry": registry})


def _law_bundle() -> tuple[dict[str, Any], dict[str, Any]]:
    # Parse the cached canonical representation so no returned object can
    # mutate the process-wide cache through a built context.
    payload = json.loads(_cached_law_bundle_json())
    children = {group: payload["children"][group] for group in GROUPS}
    registry = {
        registry_id: payload["registry"][registry_id]
        for registry_id in sorted(payload["registry"])
    }
    return children, registry


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
    for _ in range(8):
        length = len(canonical_json(context))
        if context["bounds"]["rendered_chars"] == length:
            break
        context["bounds"]["rendered_chars"] = length
    return context


def _child_manifest(
    fact_children: Mapping[str, Mapping[str, Any]],
    qualification_children: Mapping[str, Mapping[str, Any]],
    law_children: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "fact_contexts": {
            group: {
                "schema_version": child.get("schema_version"),
                "context_sha256": child.get("context_sha256"),
                "omissions": copy.deepcopy(child.get("omissions") or {}),
                "source_completeness": copy.deepcopy(
                    child.get("source_completeness") or {}
                ),
            }
            for group, child in fact_children.items()
        },
        "qualification_contexts": {
            group: {
                "schema_version": child.get("schema_version"),
                "context_sha256": child.get("context_sha256"),
                "omissions": copy.deepcopy(child.get("omissions") or {}),
                "source_completeness": copy.deepcopy(
                    child.get("source_completeness") or {}
                ),
            }
            for group, child in qualification_children.items()
        },
        "law_contexts": {
            group: {
                "schema_version": child.get("schema_version"),
                "context_sha256": child.get("child_context_sha256"),
                "omissions": copy.deepcopy(child.get("omissions") or {}),
            }
            for group, child in law_children.items()
        },
    }


def build_full_record_context(
    record: Mapping[str, Any],
    *,
    catalog_index: Any | None = None,
    qualification_catalog: Any | None = None,
) -> dict[str, Any]:
    """Build a lossless, deterministic context for all 24 items.

    No budget-driven truncation occurs at this layer.  More than 100,000
    organizer source characters or a canonical rendering above 300,000
    characters is a hard error.
    """

    if not isinstance(record, Mapping):
        raise FullRecordContextError("record must be an object")
    forbidden = _forbidden_key_paths(record)
    if forbidden:
        raise FullRecordContextError(
            f"record contains forbidden answer/prediction data: {forbidden[:5]}"
        )
    if fact_context.SCHEMA_VERSION != _EXPECTED_FACT_SCHEMA:
        raise FullRecordContextError("fact context builder is not required v4")
    if qualification_context.SCHEMA_VERSION != _EXPECTED_QUALIFICATION_SCHEMA:
        raise FullRecordContextError("qualification context builder is not required v4")
    if law_context.SCHEMA_VERSION != _EXPECTED_LAW_SCHEMA:
        raise FullRecordContextError("law context builder is not required v4")

    record_id = _record_id(record)
    documents = _documents(record)
    source_chars = sum(int(document["chars"]) for document in documents)
    if source_chars > MAX_SOURCE_CHARS:
        raise FullRecordContextError(
            f"source_chars={source_chars} exceeds hard limit {MAX_SOURCE_CHARS}"
        )
    document_manifest, span_registry = _source_registry(documents)

    catalog_index = catalog_index or fact_context.catalog_facts.CatalogIndex.load()
    fact_prepared = fact_context.prepare_fact_inputs(
        record, catalog_index=catalog_index
    )
    fact_children: dict[str, dict[str, Any]] = {}
    for group in GROUPS:
        child = fact_context.build_fact_context(
            record,
            group,
            prepared=fact_prepared,
            catalog_index=catalog_index,
        )
        errors = fact_context.validate_fact_context(
            record, child, catalog_index=catalog_index
        )
        if errors:
            raise FullRecordContextError(
                f"fact context {group} failed validation: {errors[:5]}"
            )
        if child.get("schema_version") != _EXPECTED_FACT_SCHEMA:
            raise FullRecordContextError(
                f"fact context {group} does not use required v4 schema"
            )
        fact_children[group] = child

    qualification_catalog = (
        qualification_catalog
        or qualification_context.qualification_facts.CatalogReference.load()
    )
    qualification_prepared = qualification_context.prepare_qualification_input(
        record, catalog=qualification_catalog
    )
    qualification_children: dict[str, dict[str, Any]] = {}
    for group in QUALIFICATION_GROUPS:
        child = qualification_context.build_qualification_context(
            record,
            group,
            prepared=qualification_prepared,
            catalog=qualification_catalog,
        )
        if child is None:
            raise FullRecordContextError(
                f"qualification context {group} unexpectedly absent"
            )
        errors = qualification_context.validate_qualification_context(record, child)
        if errors:
            raise FullRecordContextError(
                f"qualification context {group} failed validation: {errors[:5]}"
            )
        if child.get("schema_version") != _EXPECTED_QUALIFICATION_SCHEMA:
            raise FullRecordContextError(
                f"qualification context {group} does not use required v4 schema"
            )
        qualification_children[group] = child

    law_children, law_registry = _law_bundle()
    organizer_fields = {
        str(key): copy.deepcopy(value)
        for key, value in record.items()
        if key not in {"id", "docs"}
    }
    context: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "semantic_role": _SEMANTIC_ROLE,
        "group": "v1-24",
        "target_items": list(TARGET_ITEMS),
        "organizer_record": {
            "id": record_id,
            "record_sha256": sha256_object(record),
            "fields": organizer_fields,
            "documents": document_manifest,
        },
        "source_completeness": {
            "full_supplied_document_text_visible": True,
            "source_chars": source_chars,
            "input_completeness": copy.deepcopy(
                record.get("input_completeness") or {}
            ),
            "dropped_doc_counts": copy.deepcopy(record.get("dropped_doc_counts") or {}),
        },
        "allowed_span_registry": span_registry,
        "fact_contexts": fact_children,
        "qualification_contexts": qualification_children,
        "law_contexts": law_children,
        "law_reference_registry": law_registry,
        "child_context_manifest": _child_manifest(
            fact_children, qualification_children, law_children
        ),
        "bounds": {
            "source_chars": source_chars,
            "max_source_chars": MAX_SOURCE_CHARS,
            "source_span_count": len(span_registry),
            "max_source_span_chars": MAX_SOURCE_SPAN_CHARS,
            "max_rendered_chars": MAX_RENDERED_CHARS,
            "rendered_chars": 0,
            "truncated": False,
        },
    }
    _finalize(context)
    rendered_chars = len(canonical_json(context))
    if rendered_chars > MAX_RENDERED_CHARS:
        raise FullRecordContextError(
            f"canonical context is {rendered_chars} chars; hard limit is "
            f"{MAX_RENDERED_CHARS}; truncation is forbidden"
        )
    errors = validate_full_record_context(
        record, context, catalog_index=catalog_index
    )
    if errors:
        raise FullRecordContextError(
            f"full record context failed validation: {errors[:10]}"
        )
    return context


def allowed_span_registry(context: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return a detached span-id mapping suitable for output validation."""

    raw = context.get("allowed_span_registry")
    if not isinstance(raw, Mapping):
        raise FullRecordContextError("context has no allowed_span_registry mapping")
    result: dict[str, dict[str, Any]] = {}
    for raw_id, raw_span in raw.items():
        if not isinstance(raw_id, str) or not isinstance(raw_span, Mapping):
            raise FullRecordContextError("allowed_span_registry is malformed")
        result[raw_id] = copy.deepcopy(dict(raw_span))
    return result


def source_span_registry_from_record(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Rebuild the canonical span registry from organizer source, without labels.

    This is the same deterministic splitter used by the full-record context.
    It lets downstream receipt reviewers reconstruct an ID-only model answer
    without trusting copied coordinates or quotes in the model response.
    """

    documents = _documents(record)
    source_chars = sum(int(document["chars"]) for document in documents)
    if source_chars > MAX_SOURCE_CHARS:
        raise FullRecordContextError(
            f"source_chars={source_chars} exceeds hard limit {MAX_SOURCE_CHARS}"
        )
    _, registry = _source_registry(documents)
    return registry


def render_full_record_context(context: Mapping[str, Any]) -> str:
    """Render canonical JSON, failing rather than truncating above 300k chars."""

    if not isinstance(context, Mapping):
        raise FullRecordContextError("context must be an object")
    rendered = canonical_json(context)
    bounds = context.get("bounds") or {}
    if bounds.get("max_rendered_chars") != MAX_RENDERED_CHARS:
        raise FullRecordContextError("context lacks the explicit 300k hard cap")
    if bounds.get("truncated") is not False:
        raise FullRecordContextError("full record context must never be truncated")
    if bounds.get("rendered_chars") != len(rendered):
        raise FullRecordContextError("rendered character count mismatch")
    if len(rendered) > MAX_RENDERED_CHARS:
        raise FullRecordContextError(
            f"canonical context exceeds hard limit {MAX_RENDERED_CHARS}"
        )
    if context.get("context_sha256") != sha256_object(_hashable_context(context)):
        raise FullRecordContextError("context hash mismatch")
    return rendered


def _validate_registry(
    documents: Sequence[Mapping[str, Any]],
    manifest: Any,
    raw_registry: Any,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(raw_registry, Mapping):
        return ["allowed_span_registry_not_mapping"]
    registry = raw_registry
    expected_manifest, expected_registry = _source_registry(documents)
    if manifest != expected_manifest:
        errors.append("document_manifest_mismatch")
    if set(registry) != set(expected_registry):
        errors.append("source_span_id_set_mismatch")

    by_document: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
    for raw_id, raw_span in registry.items():
        prefix = f"span:{raw_id}"
        if not isinstance(raw_id, str) or not isinstance(raw_span, Mapping):
            errors.append(f"{prefix}:invalid_entry")
            continue
        expected = expected_registry.get(raw_id)
        if expected is None:
            errors.append(f"{prefix}:unexpected_id")
            continue
        if dict(raw_span) != expected:
            errors.append(f"{prefix}:content_mismatch")
        if raw_span.get("span_id") != raw_id:
            errors.append(f"{prefix}:key_id_mismatch")
        doc_index = raw_span.get("doc_index")
        start, end = raw_span.get("start"), raw_span.get("end")
        if not all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (doc_index, start, end)
        ):
            errors.append(f"{prefix}:invalid_coordinates")
            continue
        if not (0 <= doc_index < len(documents)):
            errors.append(f"{prefix}:invalid_doc_index")
            continue
        document = documents[doc_index]
        text = str(document["text"])
        if not (0 <= start < end <= len(text)):
            errors.append(f"{prefix}:invalid_range")
            continue
        quote = text[start:end]
        if len(quote) > MAX_SOURCE_SPAN_CHARS:
            errors.append(f"{prefix}:span_too_long")
        if raw_span.get("quote") != quote:
            errors.append(f"{prefix}:quote_mismatch")
        if raw_span.get("text_sha256") != sha256_text(quote):
            errors.append(f"{prefix}:text_hash_mismatch")
        if raw_span.get("source_doc_sha256") != document["sha256"]:
            errors.append(f"{prefix}:document_hash_mismatch")
        if raw_span.get("doc_id") != document["doc_id"]:
            errors.append(f"{prefix}:doc_id_mismatch")
        if raw_span.get("doc_type") != document["doc_type"]:
            errors.append(f"{prefix}:doc_type_mismatch")
        by_document[doc_index].append((start, end, raw_id))

    for document in documents:
        doc_index = int(document["doc_index"])
        intervals = sorted(by_document.get(doc_index, []))
        cursor = 0
        for start, end, span_id in intervals:
            if start != cursor:
                errors.append(
                    f"document:{doc_index}:gap_or_overlap_before:{span_id}:{cursor}:{start}"
                )
            cursor = end
        if cursor != int(document["chars"]):
            errors.append(
                f"document:{doc_index}:coverage_end_mismatch:{cursor}:{document['chars']}"
            )
    return errors


def validate_full_record_context(
    record: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    catalog_index: Any | None = None,
) -> list[str]:
    """Return all detectable provenance, coordinate, child, hash, and cap errors."""

    errors: list[str] = []
    if not isinstance(record, Mapping):
        return ["record_not_mapping"]
    if not isinstance(context, Mapping):
        return ["context_not_mapping"]
    try:
        record_id = _record_id(record)
        documents = _documents(record)
    except FullRecordContextError as exc:
        return [f"invalid_record:{exc}"]

    forbidden_record = _forbidden_key_paths(record)
    if forbidden_record:
        errors.append("forbidden_record_data")
    if set(context) != _TOP_LEVEL_KEYS:
        errors.append("top_level_keys_mismatch")
    if context.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    if context.get("semantic_role") != _SEMANTIC_ROLE:
        errors.append("semantic_role_mismatch")
    if context.get("group") != "v1-24":
        errors.append("group_mismatch")
    if context.get("target_items") != list(TARGET_ITEMS):
        errors.append("target_items_mismatch")

    organizer = context.get("organizer_record") or {}
    if organizer.get("id") != record_id:
        errors.append("record_id_mismatch")
    if organizer.get("record_sha256") != sha256_object(record):
        errors.append("record_hash_mismatch")
    expected_fields = {
        str(key): copy.deepcopy(value)
        for key, value in record.items()
        if key not in {"id", "docs"}
    }
    if organizer.get("fields") != expected_fields:
        errors.append("organizer_fields_mismatch")

    source_chars = sum(int(document["chars"]) for document in documents)
    if source_chars > MAX_SOURCE_CHARS:
        errors.append("source_chars_exceeded")
    completeness = context.get("source_completeness") or {}
    expected_completeness = {
        "full_supplied_document_text_visible": True,
        "source_chars": source_chars,
        "input_completeness": copy.deepcopy(record.get("input_completeness") or {}),
        "dropped_doc_counts": copy.deepcopy(record.get("dropped_doc_counts") or {}),
    }
    if completeness != expected_completeness:
        errors.append("source_completeness_mismatch")
    errors.extend(
        _validate_registry(
            documents,
            organizer.get("documents"),
            context.get("allowed_span_registry"),
        )
    )

    fact_children = context.get("fact_contexts")
    if not isinstance(fact_children, Mapping) or set(fact_children) != set(GROUPS):
        errors.append("fact_context_groups_mismatch")
        fact_children = {}
    catalog_index = catalog_index or fact_context.catalog_facts.CatalogIndex.load()
    for group in GROUPS:
        child = fact_children.get(group)
        if not isinstance(child, Mapping):
            errors.append(f"fact:{group}:missing_or_invalid")
            continue
        child_errors = fact_context.validate_fact_context(
            record, child, catalog_index=catalog_index
        )
        errors.extend(f"fact:{group}:{error}" for error in child_errors)
        if child.get("schema_version") != _EXPECTED_FACT_SCHEMA:
            errors.append(f"fact:{group}:required_schema_mismatch")

    qualification_children = context.get("qualification_contexts")
    if not isinstance(qualification_children, Mapping) or set(
        qualification_children
    ) != set(QUALIFICATION_GROUPS):
        errors.append("qualification_context_groups_mismatch")
        qualification_children = {}
    for group in QUALIFICATION_GROUPS:
        child = qualification_children.get(group)
        if not isinstance(child, Mapping):
            errors.append(f"qualification:{group}:missing_or_invalid")
            continue
        child_errors = qualification_context.validate_qualification_context(record, child)
        errors.extend(f"qualification:{group}:{error}" for error in child_errors)
        if child.get("schema_version") != _EXPECTED_QUALIFICATION_SCHEMA:
            errors.append(f"qualification:{group}:required_schema_mismatch")

    try:
        expected_law_children, expected_law_registry = _law_bundle()
    except (FullRecordContextError, OSError, ValueError) as exc:
        errors.append(f"law_bundle_rebuild_failed:{exc}")
        expected_law_children, expected_law_registry = {}, {}
    if context.get("law_contexts") != expected_law_children:
        errors.append("law_contexts_mismatch")
    if context.get("law_reference_registry") != expected_law_registry:
        errors.append("law_reference_registry_mismatch")

    law_children = (
        context.get("law_contexts")
        if isinstance(context.get("law_contexts"), Mapping)
        else {}
    )
    expected_child_manifest = _child_manifest(
        fact_children, qualification_children, law_children
    )
    if context.get("child_context_manifest") != expected_child_manifest:
        errors.append("child_context_manifest_mismatch")

    bounds = context.get("bounds") or {}
    registry = context.get("allowed_span_registry")
    registry_count = len(registry) if isinstance(registry, Mapping) else -1
    expected_bound_values = {
        "source_chars": source_chars,
        "max_source_chars": MAX_SOURCE_CHARS,
        "source_span_count": registry_count,
        "max_source_span_chars": MAX_SOURCE_SPAN_CHARS,
        "max_rendered_chars": MAX_RENDERED_CHARS,
        "truncated": False,
    }
    for key, expected in expected_bound_values.items():
        if bounds.get(key) != expected:
            errors.append(f"bounds:{key}:mismatch")
    rendered = canonical_json(context)
    if bounds.get("rendered_chars") != len(rendered):
        errors.append("rendered_chars_mismatch")
    if len(rendered) > MAX_RENDERED_CHARS:
        errors.append("max_rendered_chars_exceeded")
    if context.get("context_sha256") != sha256_object(_hashable_context(context)):
        errors.append("context_hash_mismatch")
    return errors


# Short aliases make the public build/render/validate surface explicit while
# retaining descriptive names consistent with the neighboring modules.
build = build_full_record_context
render = render_full_record_context
validate = validate_full_record_context


__all__ = [
    "FullRecordContextError",
    "GROUPS",
    "MAX_RENDERED_CHARS",
    "MAX_SOURCE_CHARS",
    "MAX_SOURCE_SPAN_CHARS",
    "QUALIFICATION_GROUPS",
    "SCHEMA_VERSION",
    "TARGET_ITEMS",
    "allowed_span_registry",
    "build",
    "build_full_record_context",
    "canonical_json",
    "render",
    "render_full_record_context",
    "source_span_registry_from_record",
    "sha256_object",
    "sha256_text",
    "validate",
    "validate_full_record_context",
]
