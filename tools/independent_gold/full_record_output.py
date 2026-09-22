"""Strict output contract for one-call, 24-item independent annotation.

The v2 wire format contains only source IDs, never copied source text or
coordinates.  A model selects IDs from the organizer-derived registry and
decisions refer to those IDs.  This module reconstructs exact spans from the
organizer record and projects a validated response into a deterministic,
cell-oriented ledger representation.

Nothing in this module imports or consults the competition submission runtime.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

try:
    from tools.independent_gold.packetize import ABSENCE_ITEMS, ITEMS
    from tools.independent_gold import full_record_context
except ModuleNotFoundError:  # Direct ``python tools/.../full_record_output.py``.
    from packetize import ABSENCE_ITEMS, ITEMS  # type: ignore[no-redef]
    import full_record_context  # type: ignore[no-redef]


SCHEMA_VERSION = "dacon.independent.full_record_output.v2"
LEDGER_SCHEMA_VERSION = "dacon.independent.full_record_cell_ledger.v1"
COMPLETENESS_STATES = (
    "sufficient",
    "incomplete_not_material",
    "incomplete_material",
    "unknown",
)
MAX_RATIONALE_CHARS = 500
MAX_ANALYSIS_CHARS = 1_000
MAX_POSITIVE_EVIDENCE_CHARS = 500
MAX_SOURCE_SPAN_CHARS = 4_000
MAX_SOURCE_SPANS = 512
MAX_PREMISE_SPANS_PER_CELL = 64

_SPAN_KEYS = ("span_id", "doc_index", "start", "end", "quote")
_DECISION_KEYS = (
    "label",
    "confidence",
    "rationale",
    "premise_span_ids",
    "exception_analysis",
    "completeness",
    "material_missing_information",
    "positive_evidence_span_id",
)


class FullRecordOutputError(ValueError):
    """Raised when model output violates the full-record annotation contract."""


def _nullable_string_schema(*, max_length: int) -> dict[str, Any]:
    return {
        "anyOf": [
            {"type": "string", "minLength": 1, "maxLength": max_length},
            {"type": "null"},
        ]
    }


def output_schema() -> dict[str, Any]:
    """Return the strict Structured Outputs schema for a full 24-item call.

    The decisions object enumerates and requires ``v1`` through ``v24``.  In
    particular, the schema does not use ``propertyNames``, which is rejected by
    some otherwise compatible Structured Outputs endpoints.
    """

    decision_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": list(_DECISION_KEYS),
        "properties": {
            "label": {
                "anyOf": [
                    {"type": "integer", "enum": [0, 1]},
                    {"type": "string", "enum": ["U"]},
                ]
            },
            "confidence": {"type": "string", "enum": ["H", "M", "L"]},
            "rationale": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_RATIONALE_CHARS,
            },
            "premise_span_ids": {
                "type": "array",
                "maxItems": MAX_PREMISE_SPANS_PER_CELL,
                "items": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            "exception_analysis": _nullable_string_schema(
                max_length=MAX_ANALYSIS_CHARS
            ),
            "completeness": {
                "type": "string",
                "enum": list(COMPLETENESS_STATES),
            },
            "material_missing_information": _nullable_string_schema(
                max_length=MAX_ANALYSIS_CHARS
            ),
            "positive_evidence_span_id": _nullable_string_schema(max_length=128),
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["source_span_ids", "decisions"],
        "properties": {
            "source_span_ids": {
                "type": "array",
                "maxItems": MAX_SOURCE_SPANS,
                "items": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            "decisions": {
                "type": "object",
                "additionalProperties": False,
                "required": list(ITEMS),
                "properties": {item: decision_schema for item in ITEMS},
            },
        },
    }


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FullRecordOutputError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise FullRecordOutputError(f"non-standard JSON constant is forbidden: {value}")


def parse_output(text: str | bytes) -> dict[str, Any]:
    """Parse exactly one JSON object, rejecting duplicate keys and JSON extensions."""

    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FullRecordOutputError("model output is not valid UTF-8") from exc
    if not isinstance(text, str):
        raise FullRecordOutputError("model output must be JSON text or UTF-8 bytes")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except FullRecordOutputError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise FullRecordOutputError(f"model output is not one valid JSON value: {exc}") from exc
    if not isinstance(value, dict):
        raise FullRecordOutputError("model output must be a JSON object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: Sequence[str], *, path: str
) -> None:
    expected_set = set(expected)
    actual_set = set(value)
    missing = sorted(expected_set - actual_set)
    extra = sorted(str(key) for key in actual_set - expected_set)
    if missing or extra:
        raise FullRecordOutputError(
            f"{path}: keys do not match contract (missing={missing}, extra={extra})"
        )


def _require_plain_int(value: Any, *, path: str) -> int:
    if type(value) is not int:
        raise FullRecordOutputError(f"{path}: expected integer")
    return value


def _normalized_narrative(
    value: Any, *, path: str, max_length: int, nullable: bool
) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        kind = "string or null" if nullable else "string"
        raise FullRecordOutputError(f"{path}: expected {kind}")
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized:
        raise FullRecordOutputError(f"{path}: must not be empty or whitespace")
    if len(normalized) > max_length:
        raise FullRecordOutputError(
            f"{path}: exceeds maximum length {max_length}"
        )
    return normalized


def _normalize_span(raw: Any, *, path: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise FullRecordOutputError(f"{path}: source span must be an object")
    _require_exact_keys(raw, _SPAN_KEYS, path=path)
    span_id = raw["span_id"]
    if not isinstance(span_id, str) or not span_id or span_id != span_id.strip():
        raise FullRecordOutputError(
            f"{path}.span_id: must be non-empty text without surrounding whitespace"
        )
    if len(span_id) > 128:
        raise FullRecordOutputError(f"{path}.span_id: exceeds maximum length 128")
    quote = raw["quote"]
    if not isinstance(quote, str):
        raise FullRecordOutputError(f"{path}.quote: expected string")
    # Quotes are never stripped or Unicode-normalized: offsets apply to the
    # organizer's original Python string exactly as supplied.
    # A complete organizer registry may legitimately contain a whitespace-only
    # span.  Keep it losslessly in the registry; selecting it as a premise is
    # rejected at the decision boundary below.
    if not quote:
        raise FullRecordOutputError(f"{path}.quote: must not be empty")
    if len(quote) > MAX_SOURCE_SPAN_CHARS:
        raise FullRecordOutputError(
            f"{path}.quote: exceeds maximum length {MAX_SOURCE_SPAN_CHARS}"
        )
    return {
        "span_id": span_id,
        "doc_index": _require_plain_int(raw["doc_index"], path=f"{path}.doc_index"),
        "start": _require_plain_int(raw["start"], path=f"{path}.start"),
        "end": _require_plain_int(raw["end"], path=f"{path}.end"),
        "quote": quote,
    }


def _normalize_span_id(value: Any, *, path: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 128
    ):
        raise FullRecordOutputError(f"{path}: invalid span ID")
    return value


def _normalize_decision(raw: Any, *, item: str) -> dict[str, Any]:
    path = f"decisions.{item}"
    if not isinstance(raw, Mapping):
        raise FullRecordOutputError(f"{path}: decision must be an object")
    _require_exact_keys(raw, _DECISION_KEYS, path=path)

    label = raw["label"]
    valid_binary = type(label) is int and label in (0, 1)
    valid_unknown = type(label) is str and label == "U"
    if not (valid_binary or valid_unknown):
        raise FullRecordOutputError(f"{path}.label: expected 0, 1, or 'U'")
    confidence = raw["confidence"]
    if confidence not in ("H", "M", "L"):
        raise FullRecordOutputError(f"{path}.confidence: expected H, M, or L")
    rationale = _normalized_narrative(
        raw["rationale"],
        path=f"{path}.rationale",
        max_length=MAX_RATIONALE_CHARS,
        nullable=False,
    )
    premise_ids = raw["premise_span_ids"]
    if not isinstance(premise_ids, list):
        raise FullRecordOutputError(f"{path}.premise_span_ids: expected array")
    if len(premise_ids) > MAX_PREMISE_SPANS_PER_CELL:
        raise FullRecordOutputError(
            f"{path}.premise_span_ids: exceeds {MAX_PREMISE_SPANS_PER_CELL} entries"
        )
    for index, span_id in enumerate(premise_ids):
        if (
            not isinstance(span_id, str)
            or not span_id
            or span_id != span_id.strip()
            or len(span_id) > 128
        ):
            raise FullRecordOutputError(
                f"{path}.premise_span_ids[{index}]: invalid span ID"
            )
    if len(premise_ids) != len(set(premise_ids)):
        raise FullRecordOutputError(f"{path}.premise_span_ids: duplicate reference")

    exception_analysis = _normalized_narrative(
        raw["exception_analysis"],
        path=f"{path}.exception_analysis",
        max_length=MAX_ANALYSIS_CHARS,
        nullable=True,
    )
    completeness = raw["completeness"]
    if completeness not in COMPLETENESS_STATES:
        raise FullRecordOutputError(
            f"{path}.completeness: expected one of {COMPLETENESS_STATES}"
        )
    missing = _normalized_narrative(
        raw["material_missing_information"],
        path=f"{path}.material_missing_information",
        max_length=MAX_ANALYSIS_CHARS,
        nullable=True,
    )
    evidence_id = raw["positive_evidence_span_id"]
    if evidence_id is not None:
        if (
            not isinstance(evidence_id, str)
            or not evidence_id
            or evidence_id != evidence_id.strip()
            or len(evidence_id) > 128
        ):
            raise FullRecordOutputError(
                f"{path}.positive_evidence_span_id: invalid span ID"
            )

    return {
        "label": label,
        "confidence": confidence,
        "rationale": rationale,
        "premise_span_ids": list(premise_ids),
        "exception_analysis": exception_analysis,
        "completeness": completeness,
        "material_missing_information": missing,
        "positive_evidence_span_id": evidence_id,
    }


def normalize_output(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the closed ID-only wire object without changing the raw answer.

    This step checks the closed shape and primitive types.  Source-bound and
    cross-reference checks belong to :func:`validate_output`.
    """

    if not isinstance(raw, Mapping):
        raise FullRecordOutputError("full-record output must be an object")
    _require_exact_keys(raw, ("source_span_ids", "decisions"), path="output")
    raw_span_ids = raw["source_span_ids"]
    if not isinstance(raw_span_ids, list):
        raise FullRecordOutputError("source_span_ids: expected array")
    if len(raw_span_ids) > MAX_SOURCE_SPANS:
        raise FullRecordOutputError(
            f"source_span_ids: exceeds maximum {MAX_SOURCE_SPANS}"
        )
    span_ids = [
        _normalize_span_id(span_id, path=f"source_span_ids[{index}]")
        for index, span_id in enumerate(raw_span_ids)
    ]
    if len(span_ids) != len(set(span_ids)):
        raise FullRecordOutputError("source_span_ids: duplicate span ID")

    raw_decisions = raw["decisions"]
    if not isinstance(raw_decisions, Mapping):
        raise FullRecordOutputError("decisions: expected object")
    _require_exact_keys(raw_decisions, ITEMS, path="decisions")
    decisions = {
        item: _normalize_decision(raw_decisions[item], item=item) for item in ITEMS
    }
    return {"source_span_ids": span_ids, "decisions": decisions}


def _record_documents(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if not isinstance(record, Mapping):
        raise FullRecordOutputError("record must be an object")
    docs = record.get("docs")
    if not isinstance(docs, list) or not docs:
        raise FullRecordOutputError("record.docs must be a non-empty array")
    for index, doc in enumerate(docs):
        if not isinstance(doc, Mapping):
            raise FullRecordOutputError(f"record.docs[{index}] must be an object")
        if not isinstance(doc.get("text"), str):
            raise FullRecordOutputError(
                f"record.docs[{index}].text must be an exact source string"
            )
    return docs


def _validate_span_against_record(
    span: Mapping[str, Any], docs: Sequence[Mapping[str, Any]], *, path: str
) -> None:
    doc_index = span["doc_index"]
    start = span["start"]
    end = span["end"]
    if doc_index < 0 or doc_index >= len(docs):
        raise FullRecordOutputError(f"{path}.doc_index: outside record.docs")
    text = docs[doc_index]["text"]
    if start < 0 or end <= start or end > len(text):
        raise FullRecordOutputError(
            f"{path}: invalid interval [{start}, {end}) for document length {len(text)}"
        )
    if text[start:end] != span["quote"]:
        raise FullRecordOutputError(
            f"{path}.quote: does not exactly equal record.docs[{doc_index}].text[{start}:{end}]"
        )


def _registry_index(
    allowed_span_registry: Sequence[Mapping[str, Any]]
    | Mapping[str, Mapping[str, Any]],
    docs: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    if isinstance(allowed_span_registry, Mapping):
        raw_entries: list[Mapping[str, Any]] = []
        for registry_id, raw in allowed_span_registry.items():
            if not isinstance(registry_id, str) or not isinstance(raw, Mapping):
                raise FullRecordOutputError(
                    "allowed_span_registry mapping must contain string IDs and span objects"
                )
            entry = dict(raw)
            if "span_id" in entry and entry["span_id"] != registry_id:
                raise FullRecordOutputError(
                    f"allowed_span_registry[{registry_id!r}]: key/span_id mismatch"
                )
            entry.setdefault("span_id", registry_id)
            raw_entries.append(entry)
    elif isinstance(allowed_span_registry, Sequence) and not isinstance(
        allowed_span_registry, (str, bytes)
    ):
        raw_entries = list(allowed_span_registry)
    else:
        raise FullRecordOutputError(
            "allowed_span_registry must be an array or span_id-to-span mapping"
        )

    result: dict[str, dict[str, Any]] = {}
    doc_hashes: dict[int, str] = {}
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, Mapping):
            raise FullRecordOutputError(
                f"allowed_span_registry[{index}]: span must be an object"
            )
        # Registries may already be ledger-enriched with immutable document
        # metadata.  Only the five wire fields are copied by the model.
        missing = [key for key in _SPAN_KEYS if key not in raw]
        if missing:
            raise FullRecordOutputError(
                f"allowed_span_registry[{index}]: missing fields {missing}"
            )
        core = _normalize_span(
            {key: raw[key] for key in _SPAN_KEYS},
            path=f"allowed_span_registry[{index}]",
        )
        span_id = core["span_id"]
        if span_id in result:
            raise FullRecordOutputError(
                f"allowed_span_registry: duplicate span_id {span_id!r}"
            )
        _validate_span_against_record(
            core, docs, path=f"allowed_span_registry[{index}]"
        )
        if "source_doc_sha256" in raw:
            doc_index = core["doc_index"]
            if doc_index not in doc_hashes:
                doc_hashes[doc_index] = hashlib.sha256(
                    docs[doc_index]["text"].encode("utf-8")
                ).hexdigest()
            expected_hash = doc_hashes[doc_index]
            if raw["source_doc_sha256"] != expected_hash:
                raise FullRecordOutputError(
                    f"allowed_span_registry[{index}].source_doc_sha256: mismatch"
                )
        result[span_id] = core
    # The supplied registry is never authoritative on its own.  Even when a
    # caller passes a task context, derive the canonical ID/interval/text map
    # again from the organizer source and reject any edited or partial map.
    expected = full_record_context.source_span_registry_from_record(
        {"docs": list(docs)}
    )
    if set(result) != set(expected):
        raise FullRecordOutputError("allowed_span_registry: canonical ID set mismatch")
    for span_id, core in result.items():
        expected_core = {key: expected[span_id][key] for key in _SPAN_KEYS}
        if core != expected_core:
            raise FullRecordOutputError(
                f"allowed_span_registry[{span_id!r}]: not the canonical organizer span"
            )
    return result


def _missing_information_is_specific(value: str) -> bool:
    compact = "".join(value.split()).casefold().rstrip(".!?")
    vague = {
        "unknown",
        "missing",
        "unspecified",
        "정보부족",
        "정보없음",
        "자료없음",
        "알수없음",
        "미상",
        "누락",
        "불명",
    }
    return len(compact) >= 4 and compact not in vague


def validate_output(
    normalized: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    allowed_span_registry: Sequence[Mapping[str, Any]]
    | Mapping[str, Mapping[str, Any]]
    | None = None,
) -> None:
    """Validate coordinates, references, completeness, and label/evidence rules.

    The registry is rebuilt from the organizer record when omitted.  An
    explicitly supplied registry must match that deterministic complete map.
    Thus the only source authority is the organizer record, never the model.
    """

    # Make the validator safe as a public boundary even if callers skipped the
    # explicit normalization step.  Equality also ensures no silent coercion.
    canonical = normalize_output(normalized)
    if canonical != normalized:
        raise FullRecordOutputError(
            "validate_output requires the canonical object returned by normalize_output"
        )
    docs = _record_documents(record)
    if allowed_span_registry is None:
        allowed_span_registry = full_record_context.source_span_registry_from_record(
            record
        )
    registry = _registry_index(allowed_span_registry, docs)

    span_by_id: dict[str, Mapping[str, Any]] = {}
    for index, span_id in enumerate(canonical["source_span_ids"]):
        span = registry.get(span_id)
        if span is None:
            raise FullRecordOutputError(
                f"source_span_ids[{index}]: span ID {span_id!r} is not in allowed registry"
            )
        if not span["quote"].strip():
            raise FullRecordOutputError(
                f"source_span_ids[{index}]: whitespace-only source is not a premise"
            )
        span_by_id[span_id] = span

    used_span_ids: set[str] = set()
    for item in ITEMS:
        decision = canonical["decisions"][item]
        label = decision["label"]
        premises = decision["premise_span_ids"]
        evidence_id = decision["positive_evidence_span_id"]
        path = f"decisions.{item}"

        unknown_premises = [span_id for span_id in premises if span_id not in span_by_id]
        if unknown_premises:
            raise FullRecordOutputError(
                f"{path}.premise_span_ids: unknown references {unknown_premises}"
            )
        used_span_ids.update(premises)
        if label in (0, 1) and not premises:
            raise FullRecordOutputError(
                f"{path}: binary conclusion requires at least one premise span"
            )

        if evidence_id is not None:
            if evidence_id not in span_by_id:
                raise FullRecordOutputError(
                    f"{path}.positive_evidence_span_id: unknown reference {evidence_id!r}"
                )
            if evidence_id not in premises:
                raise FullRecordOutputError(
                    f"{path}.positive_evidence_span_id: must also appear in premise_span_ids"
                )

        if label == 1 and item not in ABSENCE_ITEMS:
            if evidence_id is None:
                raise FullRecordOutputError(
                    f"{path}: positive non-absence decision requires exact evidence"
                )
            if len(span_by_id[evidence_id]["quote"]) > MAX_POSITIVE_EVIDENCE_CHARS:
                raise FullRecordOutputError(
                    f"{path}: positive evidence exceeds {MAX_POSITIVE_EVIDENCE_CHARS} characters"
                )
        elif evidence_id is not None:
            raise FullRecordOutputError(
                f"{path}: label {label!r} for this item requires null positive evidence"
            )

        completeness = decision["completeness"]
        missing = decision["material_missing_information"]
        if completeness in ("sufficient", "incomplete_not_material"):
            if missing is not None:
                raise FullRecordOutputError(
                    f"{path}: {completeness} requires null material_missing_information"
                )
        else:
            if missing is None or not _missing_information_is_specific(missing):
                raise FullRecordOutputError(
                    f"{path}: {completeness} requires concrete material missing information"
                )

        if label == "U" and completeness not in ("incomplete_material", "unknown"):
            raise FullRecordOutputError(
                f"{path}: U requires incomplete_material or unknown completeness"
            )
        if label in (0, 1) and completeness not in (
            "sufficient",
            "incomplete_not_material",
        ):
            raise FullRecordOutputError(
                f"{path}: a binary conclusion requires sufficient or "
                "incomplete_not_material completeness"
            )

    unused = sorted(set(span_by_id) - used_span_ids)
    if unused:
        raise FullRecordOutputError(f"source_span_ids: unused spans are forbidden: {unused}")


def canonical_ledger_projection(
    output: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    allowed_span_registry: Sequence[Mapping[str, Any]]
    | Mapping[str, Mapping[str, Any]]
    | None = None,
) -> dict[str, Any]:
    """Return a deterministic ledger with exactly 24 cells and hashed sources."""

    normalized = normalize_output(output)
    validate_output(
        normalized, record, allowed_span_registry=allowed_span_registry
    )
    docs = _record_documents(record)
    record_id = record.get("id")
    if not isinstance(record_id, (str, int)) or isinstance(record_id, bool):
        raise FullRecordOutputError("record.id must be a non-empty string or integer")
    record_id = str(record_id)
    if not record_id:
        raise FullRecordOutputError("record.id must not be empty")

    if allowed_span_registry is None:
        allowed_span_registry = full_record_context.source_span_registry_from_record(
            record
        )
    registry = _registry_index(allowed_span_registry, docs)
    ordered_spans = sorted(
        (registry[span_id] for span_id in normalized["source_span_ids"]),
        key=lambda span: (
            span["doc_index"],
            span["start"],
            span["end"],
            span["span_id"],
        ),
    )
    span_rank = {span["span_id"]: index for index, span in enumerate(ordered_spans)}
    enriched_spans: list[dict[str, Any]] = []
    doc_hashes = {
        doc_index: hashlib.sha256(doc["text"].encode("utf-8")).hexdigest()
        for doc_index, doc in enumerate(docs)
    }
    for span in ordered_spans:
        enriched_spans.append(
            {
                **span,
                "source_doc_sha256": doc_hashes[span["doc_index"]],
            }
        )

    cells: list[dict[str, Any]] = []
    for item in ITEMS:
        decision = dict(normalized["decisions"][item])
        decision["premise_span_ids"] = sorted(
            decision["premise_span_ids"], key=span_rank.__getitem__
        )
        cells.append(
            {
                "record_id": record_id,
                "item": item,
                **decision,
            }
        )

    # The assertions guard future edits to packetize.ITEMS from silently
    # changing this v1 ledger contract.
    if tuple(cell["item"] for cell in cells) != ITEMS or len(cells) != 24:
        raise FullRecordOutputError("ledger projection requires exactly v1 through v24")
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "record_id": record_id,
        "source_spans": enriched_spans,
        "cells": cells,
    }


__all__ = [
    "COMPLETENESS_STATES",
    "FullRecordOutputError",
    "LEDGER_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "canonical_ledger_projection",
    "normalize_output",
    "output_schema",
    "parse_output",
    "validate_output",
]
