"""Tamper-evident review ledger for independent gold construction.

The ledger is deliberately a *consumer* of source-grounded review decisions.
It makes no model calls and has no dependency on the competition runtime.  A
record snapshot contains every candidate, verifier, and (when required)
adjudicator vote needed to reproduce each resolved cell.  Snapshots are linked
by SHA-256, revisions supersede rather than overwrite prior snapshots, and a
seal commits to the complete active state.

Only :func:`export_build_gold_inputs` emits the annotation-row shape consumed
by ``build_gold.py``.  Export is a hard gate: the chain must be sealed, every
active record must have all 24 cells resolved, source hashes and exact evidence
coordinates must verify against the organizer record, and reviewer/blinding
independence must pass.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import heapq
import json
import math
import os
import pathlib
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Mapping, NamedTuple, Sequence


ITEMS = tuple(f"v{i}" for i in range(1, 25))
ABSENCE_ITEMS = frozenset({"v10", "v11", "v16", "v18", "v20"})
ROLES = ("candidate", "verifier", "adjudicator")
EVENT_SCHEMA = "dacon.independent.review_event.v1"
SNAPSHOT_SCHEMA = "dacon.independent.review_snapshot.v1"
REVIEW_SCHEMA = "dacon.independent.review_session.v1"
FULL_RECORD_REVIEW_SCHEMA = "dacon.independent.review_session.full_record.v2"
FULL_RECORD_ARTIFACT_SCHEMA = "dacon.independent.full_record_review_artifact.v1"
FULL_RECORD_ADAPTER_VERSION = "dacon.independent.full_record_review_adapter.v1"
HUMAN_ADJUDICATION_REVIEW_SCHEMA = (
    "dacon.independent.review_session.human_adjudication.v1"
)
HUMAN_ADJUDICATION_ARTIFACT_SCHEMA = (
    "dacon.independent.human_adjudication_artifact.v1"
)
HUMAN_JUDGMENT_SCHEMA = "dacon.independent.human_adjudication_judgment.v1"
SEAL_SCHEMA = "dacon.independent.review_seal.v1"
RESOLVED_SCHEMA = "dacon.independent.resolved_row.v1"
ZERO_HASH = "0" * 64
HEX64 = re.compile(r"^[0-9a-f]{64}$")
EVENT_ID_PATTERN = re.compile(r"^evt-[0-9a-f]{32}$")
ORGANIZER_RECORD_KEYS = frozenset(
    {
        "id",
        "docs",
        "dropped_doc_counts",
        "input_completeness",
        "assembly_policy_version",
        "meta",
        "anon_applied",
    }
)
ORGANIZER_DOC_KEYS = frozenset({"doc_id", "type", "text", "n_chars", "src_ext"})
FORBIDDEN_SOURCE_KEY_MARKERS = (
    "label",
    "prediction",
    "saved" + "_response",
    "model_output",
    "production_output",
    "gemma_output",
    "submission_output",
    "정답",
    "라벨",
    "예측",
    "모델_응답",
    "운영_출력",
)

# These markers are forbidden only in reviewer/model provenance and declared
# input artifacts.  Their presence indicates that a supposedly independent
# vote was derived from the production system or its saved model responses.
FORBIDDEN_REVIEW_INPUT_MARKERS = (
    "gemma",
    "젬마",
    "sub" + "mission/",
    "sub" + "mission\\",
    "saved" + "_response",
    "production_prediction",
    "production-response",
    "운영_예측",
    "운영예측",
)

MODEL_IDENTITY_KEYS = frozenset(
    {
        "mode",
        "provider",
        "family",
        "family_source",
        "requested_model",
        "requested_revision",
        "resolved_revision",
        "official_snapshot_pin_available",
        "attestation",
        "attestation_verified",
        "qualification_eligible_from_identity_alone",
        "drift_canary_required",
    }
)
QUALIFIED_MODEL_KEYS = frozenset(
    {
        "annotator_role",
        "qualified_tuple_sha256",
        "prompt_lineage_sha256",
        "model_identity",
        "qualification_report_object_sha256",
        "annotation_receipt_sha256",
        "annotation_ledger_sha256",
        "run_manifest_sha256",
        "task_manifest_sha256",
        "event_stream_sha256",
        "content_sha256",
    }
)


class LedgerValidationError(ValueError):
    """Raised when an event chain or a semantic review invariant is broken."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_object(value: Any) -> str:
    return sha256_text(canonical_json(value))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LedgerValidationError(message)


def _require_hash(value: Any, field_name: str) -> str:
    _require(isinstance(value, str) and HEX64.fullmatch(value) is not None, f"{field_name}: invalid sha256")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: Iterable[str], context: str) -> None:
    actual = set(value)
    wanted = set(expected)
    extra = sorted(str(key) for key in actual - wanted)
    missing = sorted(str(key) for key in wanted - actual)
    _require(not extra and not missing, f"{context}: schema keys differ; extra={extra}, missing={missing}")


def _identity_token(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value)).strip().casefold()


def _validate_source_key_boundary(value: Any, *, path: str) -> None:
    """Reject hidden label/prediction fields while never scanning notice text."""

    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = _identity_token(key).replace("-", "_").replace(" ", "_")
            _require(
                not any(marker in normalized for marker in FORBIDDEN_SOURCE_KEY_MARKERS),
                f"{path}.{key}: prohibited label/prediction field in organizer source",
            )
            _validate_source_key_boundary(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, (Mapping, list)):
                _validate_source_key_boundary(child, path=f"{path}[{index}]")


def _validate_organizer_record(record: Mapping[str, Any]) -> None:
    """Enforce the supplied-input boundary before any review can be recorded."""

    record_id = str(record.get("id") or "?")
    extra = sorted(str(key) for key in set(record) - set(ORGANIZER_RECORD_KEYS))
    _require(not extra, f"{record_id}: non-organizer top-level fields are forbidden: {extra}")
    for required in ("id", "docs", "meta", "input_completeness", "dropped_doc_counts"):
        _require(required in record, f"{record_id}: organizer source lacks {required}")
    _validate_source_key_boundary(record, path=record_id)

    docs = record.get("docs")
    _require(isinstance(docs, list) and bool(docs), f"{record_id}: docs must be a non-empty list")
    seen_doc_ids: set[str] = set()
    for index, raw in enumerate(docs):
        _require(isinstance(raw, Mapping), f"{record_id}: document {index} is not an object")
        extra_doc = sorted(str(key) for key in set(raw) - set(ORGANIZER_DOC_KEYS))
        _require(not extra_doc, f"{record_id}: document {index} has non-organizer fields: {extra_doc}")
        for required in ("doc_id", "type", "text"):
            _require(required in raw, f"{record_id}: document {index} lacks {required}")
        doc_id = str(raw["doc_id"])
        _require(bool(doc_id) and doc_id not in seen_doc_ids, f"{record_id}: duplicate/empty doc_id {doc_id!r}")
        seen_doc_ids.add(doc_id)
        _require(isinstance(raw["type"], str), f"{record_id}:{doc_id}: document type must be text")
        _require(isinstance(raw["text"], str), f"{record_id}:{doc_id}: document text must be text")
        if "n_chars" in raw:
            _require(raw["n_chars"] == len(raw["text"]), f"{record_id}:{doc_id}: n_chars mismatch")

    meta = record.get("meta")
    completeness = record.get("input_completeness")
    dropped = record.get("dropped_doc_counts")
    _require(isinstance(meta, Mapping), f"{record_id}: meta must be an object")
    _require(
        isinstance(completeness, Mapping)
        and bool(completeness)
        and all(isinstance(key, str) and type(value) is bool for key, value in completeness.items()),
        f"{record_id}: input_completeness must be a non-empty boolean map",
    )
    _require(
        isinstance(dropped, Mapping)
        and all(isinstance(key, str) and type(value) is int and value >= 1 for key, value in dropped.items()),
        f"{record_id}: dropped_doc_counts must contain positive integer counts",
    )


def _require_utc_timestamp(value: Any, field_name: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{field_name}: timestamp required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LedgerValidationError(f"{field_name}: invalid ISO-8601 timestamp") from exc
    _require(parsed.tzinfo is not None, f"{field_name}: timezone is required")
    _require(parsed.utcoffset() == timezone.utc.utcoffset(parsed), f"{field_name}: timestamp must be UTC")
    return value


def _record_id(record: Mapping[str, Any]) -> str:
    value = str(record.get("id") or "")
    _require(bool(value), "source record has no id")
    return value


def source_descriptor(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable source commitment embedded in a snapshot."""

    _validate_organizer_record(record)
    documents: list[dict[str, Any]] = []
    for index, raw in enumerate(record.get("docs") or []):
        _require(isinstance(raw, Mapping), f"{_record_id(record)}: document {index} is not an object")
        text = str(raw.get("text") or "")
        documents.append(
            {
                "doc_index": index,
                "doc_id": str(raw.get("doc_id") or f"D{index}"),
                "doc_type": str(raw.get("type") or "unknown"),
                "chars": len(text),
                "source_doc_sha256": sha256_text(text),
            }
        )
    return {
        "record_id": _record_id(record),
        "source_sha256": sha256_object(record),
        "documents": documents,
        "input_completeness": dict(record.get("input_completeness") or {}),
        "dropped_doc_counts": dict(record.get("dropped_doc_counts") or {}),
    }


def exact_evidence_locations(record: Mapping[str, Any], quote: str) -> list[dict[str, Any]]:
    """Locate every exact occurrence of ``quote`` in the original source."""

    if not quote:
        return []
    evidence_hash = sha256_text(quote)
    locations: list[dict[str, Any]] = []
    for index, raw in enumerate(record.get("docs") or []):
        text = str(raw.get("text") or "")
        doc_id = str(raw.get("doc_id") or f"D{index}")
        doc_hash = sha256_text(text)
        start = text.find(quote)
        while start >= 0:
            locations.append(
                {
                    "doc_index": index,
                    "doc_id": doc_id,
                    "start": start,
                    "end": start + len(quote),
                    "source_doc_sha256": doc_hash,
                    "evidence_sha256": evidence_hash,
                }
            )
            start = text.find(quote, start + 1)
    return locations


def make_decision(
    record: Mapping[str, Any],
    item: str,
    *,
    label: int | str,
    confidence: str,
    reason: str,
    evidence: str = "",
    uncertainty_codes: Sequence[str] = (),
) -> dict[str, Any]:
    """Create a coordinate-complete decision without inferring a label."""

    _require(item in ITEMS, f"unknown item: {item}")
    quote = str(evidence)
    if label != 1 or item in ABSENCE_ITEMS:
        quote = ""
    decision = {
        "label": label,
        "confidence": confidence,
        "reason": str(reason),
        "uncertainty_codes": sorted({str(value) for value in uncertainty_codes}),
        "evidence": {
            "quote": quote,
            "locations": exact_evidence_locations(record, quote),
        },
    }
    _validate_decision(record, item, decision, context="decision")
    return decision


def _validate_decision(
    record: Mapping[str, Any], item: str, decision: Mapping[str, Any], *, context: str
) -> None:
    _require(item in ITEMS, f"{context}: unknown item {item}")
    _require_exact_keys(
        decision,
        ("label", "confidence", "reason", "uncertainty_codes", "evidence"),
        context,
    )
    label = decision.get("label")
    _require(not isinstance(label, bool) and label in (0, 1, "U"), f"{context}: invalid label")
    confidence = decision.get("confidence")
    _require(confidence in ("high", "medium", "low"), f"{context}: invalid confidence")
    reason = decision.get("reason")
    _require(isinstance(reason, str) and 0 < len(reason.strip()) <= 4000, f"{context}: reason required")
    uncertainty = decision.get("uncertainty_codes")
    _require(isinstance(uncertainty, list), f"{context}: uncertainty_codes must be a list")
    _require(
        all(isinstance(value, str) and value.strip() for value in uncertainty),
        f"{context}: invalid uncertainty code",
    )
    _require(len(uncertainty) == len(set(uncertainty)), f"{context}: duplicate uncertainty code")
    _require(uncertainty == sorted(uncertainty), f"{context}: uncertainty_codes must be sorted")
    evidence = decision.get("evidence")
    _require(isinstance(evidence, Mapping), f"{context}: evidence must be an object")
    _require_exact_keys(evidence, ("quote", "locations"), f"{context}: evidence")
    quote = evidence.get("quote")
    locations = evidence.get("locations")
    _require(isinstance(quote, str), f"{context}: evidence quote must be text")
    _require(len(quote) <= 500, f"{context}: evidence quote exceeds 500 characters")
    _require(isinstance(locations, list), f"{context}: evidence locations must be a list")
    for index, location in enumerate(locations):
        _require(isinstance(location, Mapping), f"{context}: evidence location {index} must be an object")
        _require_exact_keys(
            location,
            (
                "doc_index",
                "doc_id",
                "start",
                "end",
                "source_doc_sha256",
                "evidence_sha256",
            ),
            f"{context}: evidence location {index}",
        )

    if label == 1 and item not in ABSENCE_ITEMS:
        _require(bool(quote), f"{context}: positive decision lacks evidence")
        expected = exact_evidence_locations(record, quote)
        _require(bool(expected), f"{context}: positive evidence is not verbatim source text")
        _require(locations == expected, f"{context}: evidence coordinates are not exact/all-occurrence")
    else:
        _require(quote == "" and locations == [], f"{context}: non-witness decision carries evidence")


def _json_clone(value: Any) -> Any:
    """Return a JSON-only deep copy using the ledger's canonical serializer."""

    return json.loads(canonical_json(value))


def _validate_model_identity(identity: Mapping[str, Any], *, context: str) -> None:
    """Validate the identity claim without turning an alias into a revision.

    The full-record runner deliberately records hosted aliases as opaque.  In
    that mode both revision fields and the attestation must remain null; a
    drift-canary obligation is part of the identity rather than an optional
    note.  Pinned snapshots retain their explicit requested revision, but this
    ledger never upgrades it to a service-attested resolved revision.
    """

    _require_exact_keys(identity, MODEL_IDENTITY_KEYS, f"{context}: model_identity")
    mode = identity.get("mode")
    _require(mode in ("opaque_hosted_alias", "pinned_snapshot"), f"{context}: invalid identity mode")
    for key in ("provider", "family", "family_source", "requested_model"):
        _require(
            isinstance(identity.get(key), str) and bool(str(identity[key]).strip()),
            f"{context}: model_identity.{key} is required",
        )
    for key in (
        "official_snapshot_pin_available",
        "attestation_verified",
        "qualification_eligible_from_identity_alone",
        "drift_canary_required",
    ):
        _require(type(identity.get(key)) is bool, f"{context}: model_identity.{key} must be boolean")
    _require(
        identity.get("resolved_revision") is None,
        f"{context}: reviewer cannot claim an unattested resolved revision",
    )
    _require(
        identity.get("attestation_verified") is False,
        f"{context}: reviewer cannot self-attest model identity",
    )
    _require(
        identity.get("qualification_eligible_from_identity_alone") is False,
        f"{context}: identity alone cannot qualify a reviewer",
    )
    _require(
        identity.get("drift_canary_required") is True,
        f"{context}: model continuity canary is mandatory",
    )

    attestation = identity.get("attestation")
    if attestation is not None:
        _require(isinstance(attestation, Mapping), f"{context}: invalid model attestation")
        _require_exact_keys(attestation, ("path", "sha256"), f"{context}: model attestation")
        _require(
            isinstance(attestation.get("path"), str) and bool(attestation["path"].strip()),
            f"{context}: model attestation path is required",
        )
        _require_hash(attestation.get("sha256"), f"{context}: model attestation hash")

    if mode == "opaque_hosted_alias":
        _require(identity.get("requested_revision") is None, f"{context}: opaque alias claims a revision")
        _require(attestation is None, f"{context}: opaque alias claims an attestation")
        _require(
            identity.get("official_snapshot_pin_available") is False,
            f"{context}: opaque alias claims snapshot availability",
        )
    else:
        _require(
            isinstance(identity.get("requested_revision"), str)
            and bool(str(identity["requested_revision"]).strip()),
            f"{context}: pinned snapshot requires an explicit revision",
        )
        _require(
            identity.get("official_snapshot_pin_available") is True,
            f"{context}: pinned snapshot availability is not declared",
        )


def _full_record_dependencies() -> tuple[Any, Any, Any]:
    """Load independent topology-B contracts lazily.

    Keeping this import lazy preserves the review ledger's lightweight generic
    use while making the adapter consume the canonical runner/output contracts
    rather than copying a drifting list of receipt fields.
    """

    try:
        from tools.independent_gold import codex_cli_annotator as base
        from tools.independent_gold import codex_full_record_annotator as runner
        from tools.independent_gold import full_record_output as output
    except ModuleNotFoundError:  # Direct execution from tools/independent_gold.
        import codex_cli_annotator as base  # type: ignore[no-redef]
        import codex_full_record_annotator as runner  # type: ignore[no-redef]
        import full_record_output as output  # type: ignore[no-redef]
    return base, runner, output


def _validate_aware_timestamp(value: Any, field_name: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{field_name}: timestamp required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LedgerValidationError(f"{field_name}: invalid ISO-8601 timestamp") from exc
    _require(parsed.tzinfo is not None, f"{field_name}: timezone is required")
    return value


def _full_record_reason(cell: Mapping[str, Any]) -> str:
    """Keep material analysis in the decision while the artifact keeps spans."""

    premise_ids = cell.get("premise_span_ids")
    _require(isinstance(premise_ids, list), "full-record cell premise_span_ids must be a list")
    value = {
        "rationale": cell.get("rationale"),
        "exception_analysis": cell.get("exception_analysis"),
        "completeness": cell.get("completeness"),
        "material_missing_information": cell.get("material_missing_information"),
        "premise_span_count": len(premise_ids),
        "premise_span_ids_sha256": sha256_object(premise_ids),
    }
    rendered = canonical_json(value)
    _require(len(rendered) <= 4000, "full-record material analysis exceeds review reason limit")
    return rendered


def _decisions_from_full_record_receipt(
    record: Mapping[str, Any], receipt: Mapping[str, Any], *, role: str
) -> dict[str, dict[str, Any]]:
    """Validate one topology-B receipt and project its 24 source-grounded votes."""

    base, runner, output = _full_record_dependencies()
    context = f"{_record_id(record)}:{role}:full-record receipt"
    _require(role in ("candidate", "verifier"), f"{context}: invalid first-pass role")
    _require_exact_keys(receipt, runner.RECEIPT_KEYS, context)
    _require(
        receipt.get("schema_version") == runner.RESULT_SCHEMA_VERSION
        and receipt.get("runner_receipt_schema_version") == runner.RECEIPT_SCHEMA_VERSION,
        f"{context}: wrong receipt schema",
    )
    _require(receipt.get("status") == "ok", f"{context}: receipt status is not ok")
    _require(receipt.get("error") is None, f"{context}: successful receipt carries an error")
    _require(receipt.get("validation_errors") == [], f"{context}: receipt has validation errors")
    _require(
        receipt.get("id") == _record_id(record)
        and receipt.get("group") == runner.GROUP_NAME
        and receipt.get("target_items") == list(ITEMS),
        f"{context}: record/group/item identity mismatch",
    )
    _require(receipt.get("annotator_role") == role, f"{context}: annotator role mismatch")
    _require(receipt.get("pass_kind") == "blind_first_pass", f"{context}: pass is not blind")
    _require(
        receipt.get("phase") == "unlabeled_20000",
        f"{context}: only the frozen unlabeled_20000 phase can enter the gold ledger",
    )
    _require(
        receipt.get("source_sha256") == sha256_object(record),
        f"{context}: organizer source hash mismatch",
    )
    _require(
        receipt.get("full_record_context_schema_version")
        == runner.full_record_context.SCHEMA_VERSION,
        f"{context}: wrong full-record context schema",
    )
    bounds = receipt.get("full_record_context_bounds")
    _require(isinstance(bounds, Mapping), f"{context}: full-record bounds missing")
    expected_source_chars = sum(len(str(doc.get("text") or "")) for doc in record.get("docs") or [])
    _require(bounds.get("truncated") is False, f"{context}: truncated source context")
    _require(bounds.get("source_chars") == expected_source_chars, f"{context}: source-char bound mismatch")
    _require(
        bounds.get("max_source_chars") == runner.full_record_context.MAX_SOURCE_CHARS
        and bounds.get("max_source_span_chars")
        == runner.full_record_context.MAX_SOURCE_SPAN_CHARS
        and bounds.get("max_rendered_chars")
        == runner.full_record_context.MAX_RENDERED_CHARS,
        f"{context}: full-record context limits differ from the reviewed runner",
    )

    for name in (
        "run_key",
        "run_manifest_sha256",
        "task_manifest_sha256",
        "source_sha256",
        "full_record_context_sha256",
        "rubric_sha256",
        "system_prompt_sha256",
        "prompt_sha256",
        "output_schema_sha256",
        "request_sha256",
        "raw_response_sha256",
        "event_stream_sha256",
        "stderr_sha256",
        "content_sha256",
        "raw_final_sha256",
        "tuple_sha256",
        "run_instance_sha256",
        "cohort_plan_sha256",
        "prompt_lineage_sha256",
        "imported_source_bundle_sha256",
    ):
        _require_hash(receipt.get(name), f"{context}: {name}")
    _require(receipt.get("run_key") == receipt.get("run_instance_sha256"), f"{context}: run key mismatch")
    _require(
        receipt.get("raw_response_sha256") == receipt.get("event_stream_sha256"),
        f"{context}: event-stream hash mismatch",
    )
    _require(
        receipt.get("system_prompt_sha256") == receipt.get("rubric_sha256"),
        f"{context}: system/rubric hash mismatch",
    )
    _require(
        receipt.get("output_schema_sha256") == base.sha256_object(output.output_schema()),
        f"{context}: output schema hash mismatch",
    )

    raw_content = receipt.get("raw_content")
    _require(isinstance(raw_content, str) and bool(raw_content.strip()), f"{context}: raw response missing")
    content_hash = sha256_text(raw_content)
    _require(
        receipt.get("content_sha256") == content_hash
        and receipt.get("raw_final_sha256") == content_hash,
        f"{context}: raw-content hash mismatch",
    )
    event_audit = receipt.get("event_audit")
    _require(isinstance(event_audit, Mapping), f"{context}: event audit missing")
    _require_exact_keys(
        event_audit,
        ("event_count", "parse_errors", "unsafe_items", "service_errors", "turn_completed"),
        f"{context}: event audit",
    )
    _require(
        type(event_audit.get("event_count")) is int and event_audit["event_count"] > 0,
        f"{context}: empty event stream",
    )
    _require(event_audit.get("parse_errors") == [], f"{context}: event parse errors")
    _require(event_audit.get("unsafe_items") == [], f"{context}: unsafe tool/non-message events")
    _require(event_audit.get("service_errors") == [], f"{context}: service errors")
    _require(event_audit.get("turn_completed") is True, f"{context}: turn did not complete")
    _require(isinstance(receipt.get("usage"), Mapping), f"{context}: usage receipt missing")
    attempt = receipt.get("attempt")
    _require(type(attempt) is int and attempt >= 1, f"{context}: invalid attempt")
    elapsed = receipt.get("elapsed_seconds")
    _require(
        type(elapsed) in (int, float) and math.isfinite(float(elapsed)) and float(elapsed) >= 0,
        f"{context}: invalid elapsed time",
    )
    _validate_aware_timestamp(receipt.get("completed_utc"), f"{context}: completed_utc")

    peer_visibility = receipt.get("peer_visibility")
    _require(peer_visibility == base.blind_peer_visibility(), f"{context}: peer visibility is not blind")
    model_identity = receipt.get("model_identity")
    _require(isinstance(model_identity, Mapping), f"{context}: model identity missing")
    _validate_model_identity(model_identity, context=context)
    provenance = receipt.get("model_provenance")
    _require(isinstance(provenance, Mapping), f"{context}: model provenance missing")
    _require(
        provenance.get("endpoint_kind") == "codex_cli_exec"
        and provenance.get("requested_model") == model_identity.get("requested_model")
        and provenance.get("resolved_model") is None,
        f"{context}: model endpoint/alias provenance mismatch",
    )
    _require(
        provenance.get("session_ephemeral") is True
        and provenance.get("sandbox") == "read-only"
        and provenance.get("ignore_user_config") is True
        and provenance.get("ignore_rules") is True,
        f"{context}: execution-isolation provenance mismatch",
    )
    thread_ids = provenance.get("thread_ids")
    _require(
        isinstance(thread_ids, list)
        and len(thread_ids) == 1
        and isinstance(thread_ids[0], str)
        and bool(thread_ids[0]),
        f"{context}: model thread lineage is not singular",
    )

    try:
        parsed = output.parse_output(raw_content)
        normalized = output.normalize_output(parsed)
        organizer_registry = runner.full_record_context.source_span_registry_from_record(record)
        rebuilt_ledger = output.canonical_ledger_projection(
            normalized, record, allowed_span_registry=organizer_registry
        )
    except (TypeError, ValueError, output.FullRecordOutputError) as exc:
        raise LedgerValidationError(f"{context}: invalid full-record model output: {exc}") from exc
    _require(receipt.get("ledger") == rebuilt_ledger, f"{context}: ledger shadow mismatch")

    span_by_id = {str(span["span_id"]): span for span in rebuilt_ledger["source_spans"]}
    cells = rebuilt_ledger.get("cells")
    _require(
        isinstance(cells, list)
        and len(cells) == len(ITEMS)
        and tuple(cell.get("item") for cell in cells) == ITEMS,
        f"{context}: ledger is not exact ordered v1-v24",
    )
    confidence_map = {"H": "high", "M": "medium", "L": "low"}
    decisions: dict[str, dict[str, Any]] = {}
    for cell in cells:
        item = str(cell["item"])
        label = cell.get("label")
        confidence = cell.get("confidence")
        _require(label in (0, 1, "U") and not isinstance(label, bool), f"{context}:{item}: invalid label")
        _require(confidence in confidence_map, f"{context}:{item}: invalid confidence")
        evidence = ""
        evidence_id = cell.get("positive_evidence_span_id")
        if label == 1 and item not in ABSENCE_ITEMS:
            _require(
                isinstance(evidence_id, str) and evidence_id in span_by_id,
                f"{context}:{item}: positive evidence span missing",
            )
            evidence = str(span_by_id[evidence_id]["quote"])
        else:
            _require(evidence_id is None, f"{context}:{item}: forbidden positive evidence span")
        uncertainty: set[str] = set()
        if label == "U":
            uncertainty.add("model_abstention")
        if confidence == "L":
            uncertainty.add("model_low_confidence")
        completeness = cell.get("completeness")
        if completeness in ("incomplete_material", "unknown"):
            uncertainty.add(f"source_{completeness}")
        if cell.get("material_missing_information") is not None:
            uncertainty.add("material_missing_information")
        decisions[item] = make_decision(
            record,
            item,
            label=label,
            confidence=confidence_map[str(confidence)],
            reason=_full_record_reason(cell),
            evidence=evidence,
            uncertainty_codes=sorted(uncertainty),
        )
    return decisions


def _validate_qualification_for_receipt(
    report: Mapping[str, Any], receipt: Mapping[str, Any], *, role: str
) -> str:
    """Bind a full-run receipt to a passed official-dev role qualification.

    The final builder independently replays the complete report.  This adapter
    checks the competence claims and exact semantic identity early so an
    unqualified or stale tuple cannot even enter a review snapshot.
    """

    context = f"{role}: qualification"
    _require(
        report.get("schema_version") == "dacon.independent.codex_full_dev_gate.v1",
        f"{context}: wrong report schema",
    )
    _require(
        report.get("purpose") == "complete_official_dev_annotator_role_qualification",
        f"{context}: wrong report purpose",
    )
    _require(report.get("is_full_official_dev_evaluation") is True, f"{context}: not full official dev")
    _require(report.get("is_selected_panel") is False, f"{context}: selected panel cannot qualify")
    _require(report.get("qualified_for_declared_role") is True, f"{context}: role gate failed")
    _require(report.get("official_dev_role_gate_pass") is True, f"{context}: official-dev gate failed")
    _require(report.get("qualified_annotator_role") == role, f"{context}: role mismatch")
    _require(report.get("topology") == "full_record_24", f"{context}: topology differs from full-record run")
    qualified_tuple = _require_hash(report.get("qualified_tuple_sha256"), f"{context}: tuple")
    _require(qualified_tuple == receipt.get("tuple_sha256"), f"{context}: stale/different tuple")
    _require(report.get("qualified_for_gold_generation") is False, f"{context}: invalid self-promotion")
    _require(
        report.get("qualified_for_final_gold_generation") is False,
        f"{context}: invalid final self-promotion",
    )
    identity = report.get("identity")
    _require(isinstance(identity, Mapping), f"{context}: identity missing")
    _require_exact_keys(
        identity,
        ("tuple_sha256", "annotator_role", "prompt_profile", "prompt_lineage_sha256", "model_identity"),
        f"{context}: identity",
    )
    _require(identity.get("tuple_sha256") == qualified_tuple, f"{context}: identity tuple mismatch")
    _require(identity.get("annotator_role") == role, f"{context}: identity role mismatch")
    _require(identity.get("prompt_profile") == receipt.get("prompt_profile"), f"{context}: prompt profile mismatch")
    _require(
        identity.get("prompt_lineage_sha256") == receipt.get("prompt_lineage_sha256"),
        f"{context}: prompt lineage mismatch",
    )
    model_identity = identity.get("model_identity")
    _require(isinstance(model_identity, Mapping), f"{context}: model identity missing")
    _validate_model_identity(model_identity, context=context)
    _require(model_identity == receipt.get("model_identity"), f"{context}: model identity mismatch")
    coverage = report.get("coverage")
    _require(isinstance(coverage, Mapping), f"{context}: coverage missing")
    for key, expected in {
        "unique_ids": 200,
        "unique_cells": 4800,
        "missing_rows": 0,
        "duplicate_rows": 0,
        "extra_rows": 0,
        "abstentions": 0,
        "invalid_evidence": 0,
        "unsafe_tool_events": 0,
    }.items():
        _require(coverage.get(key) == expected, f"{context}: coverage.{key} mismatch")
    macro = report.get("macro_positive_f1")
    minimum = report.get("minimum_item_positive_f1")
    _require(type(macro) in (int, float) and float(macro) >= 0.90, f"{context}: macro F1 below gate")
    _require(type(minimum) in (int, float) and float(minimum) >= 0.70, f"{context}: item F1 below gate")
    by_item = report.get("by_item")
    _require(isinstance(by_item, Mapping) and set(by_item) == set(ITEMS), f"{context}: item metrics incomplete")
    for item in ITEMS:
        metric = by_item[item]
        _require(
            isinstance(metric, Mapping)
            and type(metric.get("positive_f1")) in (int, float)
            and float(metric["positive_f1"]) >= 0.70,
            f"{context}:{item}: F1 below gate",
        )
    boundary = report.get("information_boundary")
    _require(
        isinstance(boundary, Mapping)
        and boundary.get("model_invocations") == 0
        and boundary.get("labels_read_after_all_candidate_artifact_validation") is True
        and boundary.get("competition_runtime_outputs_used") is False,
        f"{context}: information boundary failed",
    )
    integrity = report.get("integrity")
    _require(
        isinstance(integrity, Mapping) and bool(integrity) and all(value is True for value in integrity.values()),
        f"{context}: integrity gate failed",
    )
    return sha256_object(report)


def _receipt_reviewer_projection(
    receipt: Mapping[str, Any], *, role: str
) -> dict[str, Any]:
    identity = receipt["model_identity"]
    tuple_sha = str(receipt["tuple_sha256"])
    reviewer = {
        "reviewer_id": f"qualified-{role}-{tuple_sha[:24]}",
        "kind": "model",
        "independence_key": f"qualified-tuple:{tuple_sha}",
        "method": "independent full-record source review",
        "method_version": FULL_RECORD_ADAPTER_VERSION,
        "model_family": identity["family"],
        "model_name": identity["requested_model"],
        "prompt_sha256": receipt["prompt_sha256"],
        "request_sha256": receipt["request_sha256"],
        "response_sha256": receipt["content_sha256"],
        "annotator_role": role,
        "qualified_tuple_sha256": tuple_sha,
        "prompt_lineage_sha256": receipt["prompt_lineage_sha256"],
        "model_identity": _json_clone(identity),
        "annotation_receipt_sha256": sha256_object(receipt),
        "annotation_ledger_sha256": sha256_object(receipt["ledger"]),
        "run_manifest_sha256": receipt["run_manifest_sha256"],
        "task_manifest_sha256": receipt["task_manifest_sha256"],
        "event_stream_sha256": receipt["event_stream_sha256"],
        "content_sha256": receipt["content_sha256"],
    }
    if identity.get("mode") == "pinned_snapshot":
        reviewer["model_revision"] = identity["requested_revision"]
    return reviewer


def qualified_model_reviewer_from_full_record_receipt(
    receipt: Mapping[str, Any],
    qualification_report: Mapping[str, Any],
    *,
    role: str,
) -> dict[str, Any]:
    """Create honest build-v2 reviewer provenance for a qualified receipt."""

    qualification_hash = _validate_qualification_for_receipt(
        qualification_report, receipt, role=role
    )
    reviewer = _receipt_reviewer_projection(receipt, role=role)
    reviewer["qualification_report_object_sha256"] = qualification_hash
    _validate_reviewer(reviewer, role=role, context=f"{role}: qualified reviewer")
    return reviewer


def _validate_full_record_artifact(
    record: Mapping[str, Any],
    review: Mapping[str, Any],
    artifact: Mapping[str, Any],
    *,
    context: str,
) -> None:
    _require_exact_keys(
        artifact,
        ("schema_version", "receipt_sha256", "receipt"),
        f"{context}: annotation_artifact",
    )
    _require(
        artifact.get("schema_version") == FULL_RECORD_ARTIFACT_SCHEMA,
        f"{context}: wrong annotation artifact schema",
    )
    receipt = artifact.get("receipt")
    _require(isinstance(receipt, Mapping), f"{context}: annotation receipt missing")
    _require_hash(artifact.get("receipt_sha256"), f"{context}: annotation receipt hash")
    _require(
        artifact.get("receipt_sha256") == sha256_object(receipt),
        f"{context}: annotation receipt hash mismatch",
    )
    role = str(review.get("role") or "")
    expected_decisions = _decisions_from_full_record_receipt(record, receipt, role=role)
    _require(review.get("decisions") == expected_decisions, f"{context}: decisions differ from receipt")
    reviewer = review.get("reviewer")
    _require(isinstance(reviewer, Mapping), f"{context}: reviewer missing")
    expected_reviewer = _receipt_reviewer_projection(receipt, role=role)
    for key, expected in expected_reviewer.items():
        _require(reviewer.get(key) == expected, f"{context}: reviewer.{key} differs from receipt")
    _require_hash(
        reviewer.get("qualification_report_object_sha256"),
        f"{context}: reviewer qualification report hash",
    )


def make_full_record_review_session(
    record: Mapping[str, Any],
    *,
    role: str,
    receipt: Mapping[str, Any],
    qualification_report: Mapping[str, Any],
    review_id: str | None = None,
) -> dict[str, Any]:
    """Adapt one validated topology-B receipt into a self-contained review.

    The receipt (including its complete premise-span ledger) is embedded in the
    immutable review event.  Only the role qualification report is referenced
    by object hash because the final builder replays that shared report once.
    """

    decisions = _decisions_from_full_record_receipt(record, receipt, role=role)
    reviewer = qualified_model_reviewer_from_full_record_receipt(
        receipt, qualification_report, role=role
    )
    blinding = {
        "production_outputs_hidden": True,
        "production_code_hidden": True,
        "prohibited_inputs_confirmed_absent": True,
        "peer_votes_hidden": True,
        "visible_review_ids": [],
        "attestation": (
            "Validated blind-first-pass full-record receipt; peer visibility is empty and "
            "the frozen tuple forbids production/runtime/label inputs."
        ),
    }
    artifact = {
        "schema_version": FULL_RECORD_ARTIFACT_SCHEMA,
        "receipt_sha256": sha256_object(receipt),
        "receipt": _json_clone(receipt),
    }
    return make_review_session(
        record,
        role=role,
        reviewer=reviewer,
        blinding=blinding,
        decisions=decisions,
        review_id=review_id,
        full_record_visible=True,
        annotation_artifact=artifact,
    )


def _organizer_source_complete(record: Mapping[str, Any]) -> bool:
    completeness = record.get("input_completeness")
    dropped = record.get("dropped_doc_counts")
    return bool(
        isinstance(completeness, Mapping)
        and completeness
        and all(value is True for value in completeness.values())
        and isinstance(dropped, Mapping)
        and not dropped
    )


def _human_judgment_without_hash(judgment: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in judgment.items() if key != "judgment_sha256"}


def _validate_human_judgment(
    record: Mapping[str, Any], judgment: Mapping[str, Any], *, context: str
) -> None:
    _require_exact_keys(
        judgment,
        (
            "schema_version",
            "record_id",
            "item",
            "source_sha256",
            "packet_sha256",
            "initial_snapshot_event_id",
            "initial_snapshot_event_sha256",
            "reviewer_id",
            "reviewed_utc",
            "blind_to_peer_votes",
            "production_outputs_hidden",
            "production_code_hidden",
            "prohibited_inputs_confirmed_absent",
            "full_record_visible",
            "source_match_confirmed",
            "rule_application_confirmed",
            "label",
            "confidence",
            "reason",
            "premise_quotes",
            "evidence",
            "uncertainty_codes",
            "source_completeness",
            "dropped_doc_types_reviewed",
            "material_missing_information",
            "missing_document_impact",
            "judgment_sha256",
        ),
        context,
    )
    _require(
        judgment.get("schema_version") == HUMAN_JUDGMENT_SCHEMA,
        f"{context}: wrong human judgment schema",
    )
    record_id = _record_id(record)
    _require(judgment.get("record_id") == record_id, f"{context}: record id mismatch")
    item = judgment.get("item")
    _require(item in ITEMS, f"{context}: invalid item")
    _require(
        judgment.get("source_sha256") == source_descriptor(record)["source_sha256"],
        f"{context}: source hash mismatch",
    )
    _require_hash(judgment.get("packet_sha256"), f"{context}: packet hash")
    _require(
        isinstance(judgment.get("initial_snapshot_event_id"), str)
        and EVENT_ID_PATTERN.fullmatch(str(judgment["initial_snapshot_event_id"]))
        is not None,
        f"{context}: invalid initial snapshot event id",
    )
    _require_hash(
        judgment.get("initial_snapshot_event_sha256"),
        f"{context}: initial snapshot event hash",
    )
    _require(
        isinstance(judgment.get("reviewer_id"), str)
        and bool(str(judgment["reviewer_id"]).strip()),
        f"{context}: reviewer id required",
    )
    _require_utc_timestamp(judgment.get("reviewed_utc"), f"{context}: reviewed_utc")
    for key in (
        "blind_to_peer_votes",
        "production_outputs_hidden",
        "production_code_hidden",
        "prohibited_inputs_confirmed_absent",
        "full_record_visible",
        "source_match_confirmed",
        "rule_application_confirmed",
    ):
        _require(judgment.get(key) is True, f"{context}: {key} must be true")
    label = judgment.get("label")
    _require(type(label) is int and label in (0, 1), f"{context}: binary label required")
    _require(
        judgment.get("confidence") in ("high", "medium"),
        f"{context}: final human confidence must be high or medium",
    )
    reason = judgment.get("reason")
    _require(
        isinstance(reason, str) and 0 < len(reason.strip()) <= 4000,
        f"{context}: reason required",
    )
    premises = judgment.get("premise_quotes")
    _require(
        isinstance(premises, list)
        and 1 <= len(premises) <= 8
        and all(isinstance(value, str) and 0 < len(value) <= 500 for value in premises)
        and premises == sorted(set(premises)),
        f"{context}: premise_quotes must contain 1-8 sorted unique exact quotes",
    )
    for quote in premises:
        _require(
            bool(exact_evidence_locations(record, quote)),
            f"{context}: premise quote is not exact organizer source text",
        )
    evidence = judgment.get("evidence")
    _require(isinstance(evidence, str), f"{context}: evidence must be text")
    if label == 1 and item not in ABSENCE_ITEMS:
        _require(
            bool(evidence)
            and len(evidence) <= 500
            and evidence in premises
            and bool(exact_evidence_locations(record, evidence)),
            f"{context}: positive evidence must be an exact premise quote",
        )
    else:
        _require(evidence == "", f"{context}: non-witness decision must have empty evidence")
    _require(
        judgment.get("uncertainty_codes") == [],
        f"{context}: unresolved uncertainty cannot be sealed as gold",
    )

    completeness = judgment.get("source_completeness")
    complete = _organizer_source_complete(record)
    dropped_types = sorted(str(value) for value in (record.get("dropped_doc_counts") or {}))
    _require(
        judgment.get("dropped_doc_types_reviewed") == dropped_types,
        f"{context}: dropped document types were not exactly reviewed",
    )
    _require(
        judgment.get("material_missing_information") is False,
        f"{context}: materially missing information leaves the cell unresolved",
    )
    impact = judgment.get("missing_document_impact")
    if complete:
        _require(
            completeness == "organizer_complete" and impact is None,
            f"{context}: complete organizer source has inconsistent completeness assessment",
        )
    else:
        _require(
            completeness == "organizer_scope_sufficient",
            f"{context}: incomplete organizer source cannot be called complete",
        )
        _require(
            isinstance(impact, str) and 20 <= len(impact.strip()) <= 2000,
            f"{context}: incomplete source requires a concrete missing-document impact analysis",
        )
        if item in ABSENCE_ITEMS:
            _require(
                len(reason.strip()) >= 40,
                f"{context}: incomplete-source absence item needs a reasoned scope decision",
            )

    _require_hash(judgment.get("judgment_sha256"), f"{context}: judgment hash")
    _require(
        judgment.get("judgment_sha256")
        == sha256_object(_human_judgment_without_hash(judgment)),
        f"{context}: judgment self-hash mismatch",
    )


def make_human_judgment(
    record: Mapping[str, Any],
    item: str,
    *,
    packet_sha256: str,
    initial_snapshot_event_id: str,
    initial_snapshot_event_sha256: str,
    reviewer_id: str,
    reviewed_utc: str,
    label: int,
    confidence: str,
    reason: str,
    premise_quotes: Sequence[str],
    evidence: str = "",
    source_completeness: str = "organizer_complete",
    missing_document_impact: str | None = None,
) -> dict[str, Any]:
    """Create one fully bound, blind human adjudication commitment."""

    judgment: dict[str, Any] = {
        "schema_version": HUMAN_JUDGMENT_SCHEMA,
        "record_id": _record_id(record),
        "item": item,
        "source_sha256": source_descriptor(record)["source_sha256"],
        "packet_sha256": packet_sha256,
        "initial_snapshot_event_id": initial_snapshot_event_id,
        "initial_snapshot_event_sha256": initial_snapshot_event_sha256,
        "reviewer_id": reviewer_id,
        "reviewed_utc": reviewed_utc,
        "blind_to_peer_votes": True,
        "production_outputs_hidden": True,
        "production_code_hidden": True,
        "prohibited_inputs_confirmed_absent": True,
        "full_record_visible": True,
        "source_match_confirmed": True,
        "rule_application_confirmed": True,
        "label": label,
        "confidence": confidence,
        "reason": reason,
        "premise_quotes": sorted(set(str(value) for value in premise_quotes)),
        "evidence": evidence,
        "uncertainty_codes": [],
        "source_completeness": source_completeness,
        "dropped_doc_types_reviewed": sorted(
            str(value) for value in (record.get("dropped_doc_counts") or {})
        ),
        "material_missing_information": False,
        "missing_document_impact": missing_document_impact,
    }
    judgment["judgment_sha256"] = sha256_object(judgment)
    _validate_human_judgment(record, judgment, context=f"{_record_id(record)}:{item}")
    return judgment


def _decision_from_human_judgment(
    record: Mapping[str, Any], judgment: Mapping[str, Any]
) -> dict[str, Any]:
    return make_decision(
        record,
        str(judgment["item"]),
        label=int(judgment["label"]),
        confidence=str(judgment["confidence"]),
        reason=str(judgment["reason"]),
        evidence=str(judgment["evidence"]),
        uncertainty_codes=(),
    )


def _validate_human_adjudication_artifact(
    record: Mapping[str, Any],
    review: Mapping[str, Any],
    artifact: Mapping[str, Any],
    *,
    context: str,
) -> None:
    _require_exact_keys(
        artifact,
        (
            "schema_version",
            "prepare_manifest_file_sha256",
            "judgments_file_sha256",
            "judgments_sha256",
            "judgments",
        ),
        f"{context}: annotation_artifact",
    )
    _require(
        artifact.get("schema_version") == HUMAN_ADJUDICATION_ARTIFACT_SCHEMA,
        f"{context}: wrong human adjudication artifact schema",
    )
    for key in ("prepare_manifest_file_sha256", "judgments_file_sha256", "judgments_sha256"):
        _require_hash(artifact.get(key), f"{context}: {key}")
    judgments = artifact.get("judgments")
    _require(isinstance(judgments, list) and bool(judgments), f"{context}: judgments required")
    _require(
        artifact.get("judgments_sha256") == sha256_object(judgments),
        f"{context}: judgments hash mismatch",
    )
    reviewer = review.get("reviewer")
    _require(isinstance(reviewer, Mapping), f"{context}: reviewer missing")
    decisions: dict[str, dict[str, Any]] = {}
    item_numbers: list[int] = []
    for index, judgment in enumerate(judgments):
        _require(isinstance(judgment, Mapping), f"{context}: judgment {index} is not an object")
        _validate_human_judgment(record, judgment, context=f"{context}: judgment {index}")
        _require(
            judgment.get("reviewer_id") == reviewer.get("reviewer_id"),
            f"{context}: judgment reviewer differs from review provenance",
        )
        item = str(judgment["item"])
        _require(item not in decisions, f"{context}: duplicate judgment item {item}")
        decisions[item] = _decision_from_human_judgment(record, judgment)
        item_numbers.append(int(item[1:]))
    _require(item_numbers == sorted(item_numbers), f"{context}: judgments are not in item order")
    _require(review.get("decisions") == decisions, f"{context}: decisions differ from human judgments")


def make_human_adjudication_review_session(
    record: Mapping[str, Any],
    *,
    reviewer: Mapping[str, Any],
    judgments: Sequence[Mapping[str, Any]],
    prepare_manifest_file_sha256: str,
    judgments_file_sha256: str,
    review_id: str | None = None,
) -> dict[str, Any]:
    """Embed blind source-first human commitments in an adjudicator review."""

    normalized = sorted(
        (_json_clone(value) for value in judgments),
        key=lambda value: int(str(value.get("item") or "v0")[1:]),
    )
    decisions = {
        str(value["item"]): _decision_from_human_judgment(record, value)
        for value in normalized
    }
    artifact = {
        "schema_version": HUMAN_ADJUDICATION_ARTIFACT_SCHEMA,
        "prepare_manifest_file_sha256": prepare_manifest_file_sha256,
        "judgments_file_sha256": judgments_file_sha256,
        "judgments_sha256": sha256_object(normalized),
        "judgments": normalized,
    }
    blinding = {
        "production_outputs_hidden": True,
        "production_code_hidden": True,
        "prohibited_inputs_confirmed_absent": True,
        "peer_votes_hidden": True,
        "visible_review_ids": [],
        "attestation": (
            "Blind source-first adjudication; candidate/verifier labels, confidence, "
            "rationales, routes, and trigger direction were not visible."
        ),
    }
    return make_review_session(
        record,
        role="adjudicator",
        reviewer=reviewer,
        blinding=blinding,
        decisions=decisions,
        review_id=review_id,
        full_record_visible=True,
        annotation_artifact=artifact,
    )


def _validate_reviewer(
    reviewer: Mapping[str, Any], *, role: str | None = None, context: str
) -> None:
    required_text = ("reviewer_id", "kind", "independence_key", "method", "method_version")
    for key in required_text:
        _require(
            isinstance(reviewer.get(key), str) and bool(str(reviewer[key]).strip()),
            f"{context}: reviewer.{key} is required",
        )
    kind = reviewer["kind"]
    _require(kind in ("model", "human", "rules_engine"), f"{context}: invalid reviewer kind")

    rendered = canonical_json(reviewer).casefold()
    for marker in FORBIDDEN_REVIEW_INPUT_MARKERS:
        _require(marker not in rendered, f"{context}: forbidden review provenance marker {marker!r}")

    if kind == "model":
        for key in ("model_family", "model_name"):
            _require(
                isinstance(reviewer.get(key), str) and bool(str(reviewer[key]).strip()),
                f"{context}: model reviewer.{key} is required",
            )
        for key in ("prompt_sha256", "request_sha256", "response_sha256"):
            _require_hash(reviewer.get(key), f"{context}: reviewer.{key}")
        lineaged = "model_identity" in reviewer or bool(QUALIFIED_MODEL_KEYS & set(reviewer))
        if lineaged:
            missing = sorted(QUALIFIED_MODEL_KEYS - set(reviewer))
            _require(not missing, f"{context}: qualified model provenance is incomplete: {missing}")
            reviewer_role = reviewer.get("annotator_role")
            _require(reviewer_role in ROLES, f"{context}: invalid qualified annotator role")
            if role is not None:
                _require(reviewer_role == role, f"{context}: reviewer role differs from review role")
            for key in (
                "qualified_tuple_sha256",
                "prompt_lineage_sha256",
                "qualification_report_object_sha256",
                "annotation_receipt_sha256",
                "annotation_ledger_sha256",
                "run_manifest_sha256",
                "task_manifest_sha256",
                "event_stream_sha256",
                "content_sha256",
            ):
                _require_hash(reviewer.get(key), f"{context}: reviewer.{key}")
            identity = reviewer.get("model_identity")
            _require(isinstance(identity, Mapping), f"{context}: reviewer.model_identity is required")
            _validate_model_identity(identity, context=f"{context}: reviewer")
            _require(
                _identity_token(reviewer.get("model_family"))
                == _identity_token(identity.get("family")),
                f"{context}: reviewer model family differs from identity",
            )
            _require(
                reviewer.get("model_name") == identity.get("requested_model"),
                f"{context}: reviewer model name differs from requested alias",
            )
            _require(
                reviewer.get("independence_key")
                == f"qualified-tuple:{reviewer['qualified_tuple_sha256']}",
                f"{context}: independence key is not the qualified tuple",
            )
            if identity.get("mode") == "opaque_hosted_alias":
                _require(
                    "model_revision" not in reviewer or reviewer.get("model_revision") is None,
                    f"{context}: opaque hosted alias must not claim model_revision",
                )
            else:
                _require(
                    reviewer.get("model_revision") == identity.get("requested_revision"),
                    f"{context}: pinned reviewer revision differs from identity",
                )
        else:
            _require(
                isinstance(reviewer.get("model_revision"), str)
                and bool(str(reviewer["model_revision"]).strip()),
                f"{context}: legacy model reviewer.model_revision is required",
            )
    elif kind == "human":
        _require(
            isinstance(reviewer.get("review_protocol_version"), str)
            and bool(str(reviewer["review_protocol_version"]).strip()),
            f"{context}: human review protocol is required",
        )
    else:
        _require_hash(reviewer.get("implementation_sha256"), f"{context}: reviewer.implementation_sha256")


def _validate_blinding(blinding: Mapping[str, Any], role: str, *, context: str) -> None:
    _require_exact_keys(
        blinding,
        (
            "production_outputs_hidden",
            "production_code_hidden",
            "prohibited_inputs_confirmed_absent",
            "peer_votes_hidden",
            "visible_review_ids",
            "attestation",
        ),
        f"{context}: blinding",
    )
    rendered = canonical_json(blinding).casefold()
    for marker in FORBIDDEN_REVIEW_INPUT_MARKERS:
        _require(marker not in rendered, f"{context}: forbidden blinding provenance marker {marker!r}")
    for key in (
        "production_outputs_hidden",
        "production_code_hidden",
        "prohibited_inputs_confirmed_absent",
        "peer_votes_hidden",
    ):
        _require(isinstance(blinding.get(key), bool), f"{context}: blinding.{key} must be boolean")
    _require(blinding["production_outputs_hidden"], f"{context}: production outputs were visible")
    _require(blinding["production_code_hidden"], f"{context}: production code was visible")
    _require(
        blinding["prohibited_inputs_confirmed_absent"],
        f"{context}: prohibited input absence was not attested",
    )
    visible = blinding.get("visible_review_ids")
    _require(
        isinstance(visible, list)
        and all(isinstance(value, str) and value for value in visible)
        and len(visible) == len(set(visible)),
        f"{context}: invalid visible_review_ids",
    )
    _require(
        isinstance(blinding.get("attestation"), str) and bool(blinding["attestation"].strip()),
        f"{context}: blinding attestation is required",
    )
    if role in ("candidate", "verifier"):
        _require(blinding["peer_votes_hidden"], f"{context}: peer vote was visible")
        _require(visible == [], f"{context}: blinded review names visible peer reviews")


def _validate_source_access(
    access: Mapping[str, Any], source: Mapping[str, Any], *, context: str
) -> None:
    _require_exact_keys(
        access,
        ("source_sha256", "full_record_visible", "absence_safe_items"),
        f"{context}: source_access",
    )
    _require_hash(access.get("source_sha256"), f"{context}: source_access.source_sha256")
    _require(
        access.get("source_sha256") == source.get("source_sha256"),
        f"{context}: review used a different source",
    )
    _require(isinstance(access.get("full_record_visible"), bool), f"{context}: full_record_visible required")
    safe_items = access.get("absence_safe_items")
    _require(
        isinstance(safe_items, list)
        and all(value in ABSENCE_ITEMS for value in safe_items)
        and len(safe_items) == len(set(safe_items)),
        f"{context}: invalid absence_safe_items",
    )
    if access["full_record_visible"]:
        _require(set(safe_items) == set(ABSENCE_ITEMS), f"{context}: full source must mark all absence items safe")


def _review_without_hash(review: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in review.items() if key != "review_sha256"}


def make_review_session(
    record: Mapping[str, Any],
    *,
    role: str,
    reviewer: Mapping[str, Any],
    blinding: Mapping[str, Any],
    decisions: Mapping[str, Mapping[str, Any]],
    review_id: str | None = None,
    full_record_visible: bool = True,
    absence_safe_items: Iterable[str] | None = None,
    annotation_artifact: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bundle one source/reviewer pass over any disjoint subset of items."""

    _require(role in ROLES, f"invalid review role: {role}")
    artifact_schema = (
        annotation_artifact.get("schema_version")
        if isinstance(annotation_artifact, Mapping)
        else None
    )
    if annotation_artifact is None:
        review_schema = REVIEW_SCHEMA
    elif artifact_schema == FULL_RECORD_ARTIFACT_SCHEMA:
        review_schema = FULL_RECORD_REVIEW_SCHEMA
    elif artifact_schema == HUMAN_ADJUDICATION_ARTIFACT_SCHEMA:
        review_schema = HUMAN_ADJUDICATION_REVIEW_SCHEMA
    else:
        raise LedgerValidationError("unsupported annotation artifact schema")
    source = source_descriptor(record)
    safe = set(absence_safe_items or ())
    if full_record_visible:
        safe = set(ABSENCE_ITEMS)
    normalized_decisions = {str(item): dict(value) for item, value in decisions.items()}
    review: dict[str, Any] = {
        "schema_version": review_schema,
        "review_id": str(review_id or "pending"),
        "role": role,
        "reviewer": dict(reviewer),
        "blinding": dict(blinding),
        "source_access": {
            "source_sha256": source["source_sha256"],
            "full_record_visible": bool(full_record_visible),
            "absence_safe_items": sorted(safe, key=lambda item: int(item[1:])),
        },
        "decisions": normalized_decisions,
    }
    if annotation_artifact is not None:
        review["annotation_artifact"] = _json_clone(annotation_artifact)
    if review_id is None:
        digest_seed = dict(review)
        digest_seed["review_id"] = ""
        review["review_id"] = f"{role}-{sha256_object(digest_seed)[:24]}"
    review["review_sha256"] = sha256_object(_review_without_hash(review))
    _validate_review_session(record, source, review)
    return review


def _validate_review_session(
    record: Mapping[str, Any], source: Mapping[str, Any], review: Mapping[str, Any]
) -> None:
    context = f"{source['record_id']}:{review.get('review_id', '?')}"
    schema = review.get("schema_version")
    _require(
        schema
        in (
            REVIEW_SCHEMA,
            FULL_RECORD_REVIEW_SCHEMA,
            HUMAN_ADJUDICATION_REVIEW_SCHEMA,
        ),
        f"{context}: wrong review schema",
    )
    expected_keys = {
        "schema_version",
        "review_id",
        "role",
        "reviewer",
        "blinding",
        "source_access",
        "decisions",
        "review_sha256",
    }
    if schema in (FULL_RECORD_REVIEW_SCHEMA, HUMAN_ADJUDICATION_REVIEW_SCHEMA):
        expected_keys.add("annotation_artifact")
    _require_exact_keys(
        review,
        expected_keys,
        context,
    )
    _require(
        isinstance(review.get("review_id"), str) and bool(review["review_id"]),
        f"{context}: review_id required",
    )
    role = review.get("role")
    _require(role in ROLES, f"{context}: invalid role")
    reviewer = review.get("reviewer")
    blinding = review.get("blinding")
    access = review.get("source_access")
    decisions = review.get("decisions")
    _require(isinstance(reviewer, Mapping), f"{context}: reviewer required")
    _require(isinstance(blinding, Mapping), f"{context}: blinding required")
    _require(isinstance(access, Mapping), f"{context}: source_access required")
    _require(isinstance(decisions, Mapping) and bool(decisions), f"{context}: decisions required")
    _validate_reviewer(reviewer, role=str(role), context=context)
    _validate_blinding(blinding, str(role), context=context)
    _validate_source_access(access, source, context=context)
    for item, decision in decisions.items():
        _require(isinstance(decision, Mapping), f"{context}:{item}: decision must be an object")
        _validate_decision(record, str(item), decision, context=f"{context}:{item}")
    if schema == FULL_RECORD_REVIEW_SCHEMA:
        artifact = review.get("annotation_artifact")
        _require(isinstance(artifact, Mapping), f"{context}: annotation artifact required")
        _validate_full_record_artifact(record, review, artifact, context=context)
    elif schema == HUMAN_ADJUDICATION_REVIEW_SCHEMA:
        artifact = review.get("annotation_artifact")
        _require(isinstance(artifact, Mapping), f"{context}: annotation artifact required")
        _require(role == "adjudicator", f"{context}: human artifact must be adjudication")
        _require(
            reviewer.get("kind") == "human",
            f"{context}: human artifact requires a human reviewer",
        )
        _validate_human_adjudication_artifact(
            record, review, artifact, context=context
        )
    expected_hash = sha256_object(_review_without_hash(review))
    _require(review.get("review_sha256") == expected_hash, f"{context}: review hash mismatch")


def _vote_id(review: Mapping[str, Any], item: str) -> str:
    return sha256_object(
        {
            "review_id": review["review_id"],
            "review_sha256": review["review_sha256"],
            "item": item,
            "decision": review["decisions"][item],
        }
    )


def _review_is_absence_safe(review: Mapping[str, Any], item: str) -> bool:
    access = review["source_access"]
    return bool(access["full_record_visible"] or item in access["absence_safe_items"])


def _independent(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    left, right = a["reviewer"], b["reviewer"]
    if _identity_token(left["reviewer_id"]) == _identity_token(right["reviewer_id"]):
        return False
    if _identity_token(left["independence_key"]) == _identity_token(right["independence_key"]):
        return False
    if left["kind"] == right["kind"] == "model":
        if _identity_token(left["model_family"]) == _identity_token(right["model_family"]):
            return False
        if left["prompt_sha256"] == right["prompt_sha256"]:
            return False
    if left["kind"] == right["kind"] == "rules_engine":
        if left["implementation_sha256"] == right["implementation_sha256"]:
            return False
    return True


def _collect_votes(
    reviews: Sequence[Mapping[str, Any]], record_id: str
) -> dict[str, dict[str, Mapping[str, Any]]]:
    votes: dict[str, dict[str, Mapping[str, Any]]] = {item: {} for item in ITEMS}
    review_ids: set[str] = set()
    for review in reviews:
        review_id = str(review["review_id"])
        _require(review_id not in review_ids, f"{record_id}: duplicate review_id {review_id}")
        review_ids.add(review_id)
        role = str(review["role"])
        for item in review["decisions"]:
            _require(role not in votes[item], f"{record_id}:{item}: duplicate {role} vote")
            votes[item][role] = review

    for review in reviews:
        if review["role"] != "adjudicator":
            continue
        visible = set(review["blinding"]["visible_review_ids"])
        allowed = {
            peer["review_id"]
            for item in review["decisions"]
            for role, peer in votes[item].items()
            if role in ("candidate", "verifier")
        }
        _require(visible <= allowed, f"{record_id}:{review['review_id']}: names unrelated visible reviews")
        if review["blinding"]["peer_votes_hidden"]:
            _require(not visible, f"{record_id}:{review['review_id']}: hidden peers listed as visible")
        else:
            _require(
                visible == allowed,
                f"{record_id}:{review['review_id']}: visible peer review set is incomplete",
            )
    return votes


def _route_cell(
    source: Mapping[str, Any], item: str, by_role: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    record_id = str(source["record_id"])
    candidate = by_role.get("candidate")
    verifier = by_role.get("verifier")
    adjudicator = by_role.get("adjudicator")
    triggers: list[str] = []
    blocking: list[str] = []
    if candidate is None:
        blocking.append("missing_candidate")
    if verifier is None:
        blocking.append("missing_verifier")
    if blocking:
        return {"status": "unresolved", "route": "review_required", "trigger_codes": blocking}

    assert candidate is not None and verifier is not None
    if not _independent(candidate, verifier):
        blocking.append("non_independent_verifier")
    c_decision = candidate["decisions"][item]
    v_decision = verifier["decisions"][item]
    c_label, v_label = c_decision["label"], v_decision["label"]
    if c_label == "U" or v_label == "U":
        triggers.append("abstention")
    if c_label in (0, 1) and v_label in (0, 1) and c_label != v_label:
        triggers.append("disagreement")
    if c_decision["confidence"] == "low" or v_decision["confidence"] == "low":
        triggers.append("low_confidence")
    if c_decision["uncertainty_codes"] or v_decision["uncertainty_codes"]:
        triggers.append("uncertainty_flag")
    if item in ABSENCE_ITEMS and (
        not _review_is_absence_safe(candidate, item) or not _review_is_absence_safe(verifier, item)
    ):
        triggers.append("incomplete_source_view")
    completeness = source.get("input_completeness") or {}
    dropped = source.get("dropped_doc_counts") or {}
    if (
        not completeness
        or any(value is not True for value in completeness.values())
        or bool(dropped)
    ):
        triggers.append("organizer_source_incomplete")
    if blocking:
        return {
            "status": "unresolved",
            "route": "review_required",
            "trigger_codes": sorted(set(blocking + triggers)),
        }

    c_vote_id, v_vote_id = _vote_id(candidate, item), _vote_id(verifier, item)
    if not triggers and c_label == v_label and c_label in (0, 1):
        chosen = c_decision
        return {
            "status": "resolved",
            "route": "independent_consensus",
            "trigger_codes": [],
            "final_label": c_label,
            "evidence_vote_id": c_vote_id,
            "selected_vote_ids": [c_vote_id, v_vote_id],
            "evidence": chosen["evidence"],
            "reason": chosen["reason"],
            "blind_status": {
                "production_blind": True,
                "candidate_verifier_mutually_blind": True,
                "adjudicator_peer_visible": None,
            },
        }

    if adjudicator is None:
        return {
            "status": "unresolved",
            "route": "adjudication_required",
            "trigger_codes": sorted(set(triggers)),
        }
    if not _independent(adjudicator, candidate) or not _independent(adjudicator, verifier):
        return {
            "status": "unresolved",
            "route": "adjudication_required",
            "trigger_codes": sorted(set(triggers + ["non_independent_adjudicator"])),
        }
    decision = adjudicator["decisions"][item]
    adjudication_triggers: list[str] = []
    if decision["label"] == "U":
        adjudication_triggers.append("adjudicator_abstention")
    if decision["confidence"] == "low":
        adjudication_triggers.append("adjudicator_low_confidence")
    if decision["uncertainty_codes"]:
        adjudication_triggers.append("adjudicator_uncertainty")
    if item in ABSENCE_ITEMS and not _review_is_absence_safe(adjudicator, item):
        adjudication_triggers.append("adjudicator_incomplete_source_view")
    if adjudication_triggers:
        return {
            "status": "unresolved",
            "route": "adjudication_required",
            "trigger_codes": sorted(set(triggers + adjudication_triggers)),
        }
    a_vote_id = _vote_id(adjudicator, item)
    return {
        "status": "resolved",
        "route": "adjudication",
        "trigger_codes": sorted(set(triggers)),
        "final_label": decision["label"],
        "evidence_vote_id": a_vote_id,
        "selected_vote_ids": [c_vote_id, v_vote_id, a_vote_id],
        "evidence": decision["evidence"],
        "reason": decision["reason"],
        "blind_status": {
            "production_blind": True,
            "candidate_verifier_mutually_blind": True,
            "adjudicator_peer_visible": not adjudicator["blinding"]["peer_votes_hidden"],
        },
    }


def derive_routing(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Derive every cell route from the snapshot; stored resolutions are ignored."""

    source = snapshot["source"]
    votes = _collect_votes(snapshot["reviews"], str(source["record_id"]))
    return {item: _route_cell(source, item, votes[item]) for item in ITEMS}


def build_snapshot(
    record: Mapping[str, Any],
    reviews: Sequence[Mapping[str, Any]],
    *,
    supersedes_event_id: str | None = None,
    audit_tags: Sequence[str] = (),
) -> dict[str, Any]:
    """Create a full immutable record snapshot and its derived resolutions."""

    source = source_descriptor(record)
    normalized_reviews = [dict(review) for review in reviews]
    for review in normalized_reviews:
        _validate_review_session(record, source, review)
    snapshot: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA,
        "source": source,
        "supersedes_event_id": supersedes_event_id,
        "reviews": normalized_reviews,
        "routing": {},
        "resolutions": {},
        "audit_tags": sorted({str(value) for value in audit_tags if str(value)}),
    }
    snapshot["routing"] = derive_routing(snapshot)
    snapshot["resolutions"] = {
        item: route for item, route in snapshot["routing"].items() if route["status"] == "resolved"
    }
    snapshot["snapshot_sha256"] = sha256_object(
        {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    )
    return snapshot


def _validate_source_descriptor(record: Mapping[str, Any], supplied: Mapping[str, Any]) -> None:
    expected = source_descriptor(record)
    _require(supplied == expected, f"{expected['record_id']}: source descriptor/hash mismatch")


def validate_snapshot(
    snapshot: Mapping[str, Any], record: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Validate one snapshot and return all derived routes."""

    _require(snapshot.get("schema_version") == SNAPSHOT_SCHEMA, "wrong snapshot schema")
    _require_exact_keys(
        snapshot,
        (
            "schema_version",
            "source",
            "supersedes_event_id",
            "reviews",
            "routing",
            "resolutions",
            "audit_tags",
            "snapshot_sha256",
        ),
        "snapshot",
    )
    source = snapshot.get("source")
    _require(isinstance(source, Mapping), "snapshot source is required")
    _validate_source_descriptor(record, source)
    supersedes = snapshot.get("supersedes_event_id")
    _require(supersedes is None or isinstance(supersedes, str), "invalid supersedes_event_id")
    reviews = snapshot.get("reviews")
    _require(isinstance(reviews, list), f"{source['record_id']}: reviews must be a list")
    for review in reviews:
        _require(isinstance(review, Mapping), f"{source['record_id']}: invalid review object")
        _validate_review_session(record, source, review)
    tags = snapshot.get("audit_tags")
    _require(
        isinstance(tags, list)
        and all(isinstance(value, str) and value for value in tags)
        and len(tags) == len(set(tags))
        and tags == sorted(tags),
        f"{source['record_id']}: invalid audit_tags",
    )
    derived = derive_routing(snapshot)
    _require(
        snapshot.get("routing") == derived,
        f"{source['record_id']}: stored conflict/uncertainty routing does not match votes",
    )
    expected_resolutions = {
        item: route for item, route in derived.items() if route["status"] == "resolved"
    }
    _require(
        snapshot.get("resolutions") == expected_resolutions,
        f"{source['record_id']}: stored resolution does not match votes/routing",
    )
    expected_hash = sha256_object(
        {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    )
    _require(snapshot.get("snapshot_sha256") == expected_hash, f"{source['record_id']}: snapshot hash mismatch")
    return derived


def _event_without_hash(event: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key != "event_sha256"}


def make_event(
    *,
    ledger_id: str,
    seq: int,
    prev_event_sha256: str,
    event_type: str,
    payload: Mapping[str, Any],
    occurred_utc: str | None = None,
) -> dict[str, Any]:
    _require(bool(ledger_id), "ledger_id required")
    _require(seq >= 0, "event seq must be non-negative")
    _require_hash(prev_event_sha256, "prev_event_sha256")
    _require(event_type in ("record_snapshot", "ledger_sealed"), f"unknown event type: {event_type}")
    core = {
        "schema_version": EVENT_SCHEMA,
        "ledger_id": ledger_id,
        "seq": seq,
        "prev_event_sha256": prev_event_sha256,
        "event_type": event_type,
        "occurred_utc": occurred_utc or utc_now(),
        "payload": dict(payload),
    }
    _require_utc_timestamp(core["occurred_utc"], "occurred_utc")
    event_id_seed = sha256_object(core)
    core["event_id"] = f"evt-{event_id_seed[:32]}"
    core["event_sha256"] = sha256_object(core)
    return core


def _validate_event_envelope(
    event: Mapping[str, Any],
    *,
    expected_seq: int,
    ledger_id: str | None,
    previous: str,
    sealed: bool,
    seen_ids: set[str],
) -> tuple[str, str, bool]:
    _require(not sealed, f"event {expected_seq}: data appended after seal")
    _require_exact_keys(
        event,
        (
            "schema_version",
            "ledger_id",
            "seq",
            "prev_event_sha256",
            "event_type",
            "occurred_utc",
            "payload",
            "event_id",
            "event_sha256",
        ),
        f"event {expected_seq}",
    )
    _require(event.get("schema_version") == EVENT_SCHEMA, f"event {expected_seq}: wrong schema")
    current_ledger = event.get("ledger_id")
    _require(
        isinstance(current_ledger, str) and bool(current_ledger),
        f"event {expected_seq}: ledger_id required",
    )
    effective_ledger = ledger_id or current_ledger
    _require(current_ledger == effective_ledger, f"event {expected_seq}: ledger_id changed")
    _require(event.get("seq") == expected_seq, f"event {expected_seq}: sequence mismatch")
    _require(event.get("prev_event_sha256") == previous, f"event {expected_seq}: broken previous hash")
    event_id = event.get("event_id")
    _require(
        isinstance(event_id, str) and event_id not in seen_ids,
        f"event {expected_seq}: duplicate/invalid id",
    )
    seen_ids.add(event_id)
    _require_utc_timestamp(event.get("occurred_utc"), f"event {expected_seq}: occurred_utc")
    id_seed = {
        key: value for key, value in event.items() if key not in ("event_id", "event_sha256")
    }
    _require(
        event_id == f"evt-{sha256_object(id_seed)[:32]}",
        f"event {expected_seq}: event id mismatch",
    )
    expected_hash = sha256_object(_event_without_hash(event))
    _require(event.get("event_sha256") == expected_hash, f"event {expected_seq}: event hash mismatch")
    event_type = event.get("event_type")
    _require(
        event_type in ("record_snapshot", "ledger_sealed"),
        f"event {expected_seq}: unknown type",
    )
    return effective_ledger, expected_hash, event_type == "ledger_sealed"


def verify_event_chain(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Verify envelope hashes/order without interpreting review semantics."""

    ledger_id: str | None = None
    previous = ZERO_HASH
    count = 0
    sealed = False
    seen_ids: set[str] = set()
    for expected_seq, event in enumerate(events):
        ledger_id, previous, sealed = _validate_event_envelope(
            event,
            expected_seq=expected_seq,
            ledger_id=ledger_id,
            previous=previous,
            sealed=sealed,
            seen_ids=seen_ids,
        )
        count += 1
    return {
        "ledger_id": ledger_id,
        "event_count": count,
        "head_sha256": previous,
        "sealed": sealed,
    }


def iter_events(path: pathlib.Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LedgerValidationError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            _require(isinstance(event, dict), f"{path}:{line_number}: event must be an object")
            yield event


def read_events(path: pathlib.Path) -> list[dict[str, Any]]:
    return list(iter_events(path))


class ActiveRecord(NamedTuple):
    event_id: str
    event_sha256: str
    snapshot_sha256: str
    source_sha256: str
    resolved_items: tuple[str, ...]


class LedgerState:
    def __init__(
        self,
        ledger_id: str | None = None,
        event_count: int = 0,
        head_sha256: str = ZERO_HASH,
        active: Mapping[str, ActiveRecord] | None = None,
        sealed: bool = False,
        seal: Mapping[str, Any] | None = None,
    ) -> None:
        self.ledger_id = ledger_id
        self.event_count = event_count
        self.head_sha256 = head_sha256
        self.active = dict(active or {})
        self.sealed = sealed
        self.seal = dict(seal) if seal is not None else None

    @property
    def resolved_cells(self) -> int:
        return sum(len(record.resolved_items) for record in self.active.values())


def _state_rows(state: LedgerState) -> list[dict[str, Any]]:
    return [
        {
            "record_id": record_id,
            "event_id": value.event_id,
            "event_sha256": value.event_sha256,
            "snapshot_sha256": value.snapshot_sha256,
            "source_sha256": value.source_sha256,
            "resolved_items": list(value.resolved_items),
        }
        for record_id, value in sorted(state.active.items())
    ]


def make_seal_payload(
    state: LedgerState,
    *,
    expected_record_ids: Iterable[str],
    scope_items: Sequence[str] = ITEMS,
) -> dict[str, Any]:
    raw_ids = [str(value) for value in expected_record_ids]
    ids = sorted(raw_ids)
    _require(ids and len(ids) == len(set(ids)), "expected_record_ids must be non-empty and unique")
    normalized_items = tuple(scope_items)
    _require(
        normalized_items
        and len(normalized_items) == len(set(normalized_items))
        and all(item in ITEMS for item in normalized_items),
        "invalid seal scope_items",
    )
    rows = _state_rows(state)
    return {
        "schema_version": SEAL_SCHEMA,
        "preseal_event_count": state.event_count,
        "preseal_head_sha256": state.head_sha256,
        "expected_record_ids": ids,
        "expected_record_ids_sha256": sha256_object(ids),
        "scope_items": list(normalized_items),
        "active_record_count": len(state.active),
        "resolved_cell_count": state.resolved_cells,
        "active_state_sha256": sha256_object(rows),
    }


def _validate_seal(payload: Mapping[str, Any], state: LedgerState) -> None:
    _require_exact_keys(
        payload,
        (
            "schema_version",
            "preseal_event_count",
            "preseal_head_sha256",
            "expected_record_ids",
            "expected_record_ids_sha256",
            "scope_items",
            "active_record_count",
            "resolved_cell_count",
            "active_state_sha256",
        ),
        "seal",
    )
    _require(payload.get("schema_version") == SEAL_SCHEMA, "wrong seal schema")
    ids = payload.get("expected_record_ids")
    scope = payload.get("scope_items")
    _require(isinstance(ids, list) and ids == sorted(set(ids)) and bool(ids), "invalid seal record ids")
    _require(
        isinstance(scope, list)
        and bool(scope)
        and len(scope) == len(set(scope))
        and all(item in ITEMS for item in scope),
        "invalid seal scope",
    )
    _require(payload.get("preseal_event_count") == state.event_count, "seal event count mismatch")
    _require(payload.get("preseal_head_sha256") == state.head_sha256, "seal pre-head mismatch")
    _require(payload.get("expected_record_ids_sha256") == sha256_object(ids), "seal id hash mismatch")
    _require(set(ids) == set(state.active), "seal active records differ from expected records")
    _require(payload.get("active_record_count") == len(state.active), "seal record count mismatch")
    _require(payload.get("resolved_cell_count") == state.resolved_cells, "seal resolved count mismatch")
    _require(payload.get("active_state_sha256") == sha256_object(_state_rows(state)), "seal state hash mismatch")
    required = set(scope)
    for record_id, active in state.active.items():
        missing = required - set(active.resolved_items)
        _require(not missing, f"{record_id}: seal has unresolved scope items {sorted(missing)}")


def materialize_ledger(
    events: Iterable[Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    *,
    require_sealed: bool = False,
) -> LedgerState:
    """Fully verify a chain, source evidence, revisions, resolutions, and seal."""

    state = LedgerState()
    envelope_ledger_id: str | None = None
    envelope_previous = ZERO_HASH
    envelope_sealed = False
    seen_event_ids: set[str] = set()
    for expected_seq, event in enumerate(events):
        envelope_ledger_id, envelope_previous, envelope_sealed = _validate_event_envelope(
            event,
            expected_seq=expected_seq,
            ledger_id=envelope_ledger_id,
            previous=envelope_previous,
            sealed=envelope_sealed,
            seen_ids=seen_event_ids,
        )
        if state.ledger_id is None:
            state.ledger_id = envelope_ledger_id
        event_type = event["event_type"]
        payload = event["payload"]
        _require(isinstance(payload, Mapping), f"event {event['seq']}: payload must be an object")
        if event_type == "record_snapshot":
            source = payload.get("source")
            _require(isinstance(source, Mapping), f"event {event['seq']}: snapshot source required")
            record_id = str(source.get("record_id") or "")
            _require(record_id in records, f"event {event['seq']}: unknown source record {record_id}")
            routes = validate_snapshot(payload, records[record_id])
            prior = state.active.get(record_id)
            supersedes = payload.get("supersedes_event_id")
            if prior is None:
                _require(supersedes is None, f"{record_id}: initial snapshot claims a predecessor")
            else:
                _require(
                    supersedes == prior.event_id,
                    f"{record_id}: revision does not supersede the active snapshot",
                )
                _require(
                    source["source_sha256"] == prior.source_sha256,
                    f"{record_id}: revision changed immutable source",
                )
            resolved = tuple(item for item in ITEMS if routes[item]["status"] == "resolved")
            state.active[record_id] = ActiveRecord(
                event_id=str(event["event_id"]),
                event_sha256=str(event["event_sha256"]),
                snapshot_sha256=str(payload["snapshot_sha256"]),
                source_sha256=str(source["source_sha256"]),
                resolved_items=resolved,
            )
        else:
            _validate_seal(payload, state)
            state.sealed = True
            state.seal = dict(payload)
        state.event_count += 1
        state.head_sha256 = str(event["event_sha256"])
    if require_sealed:
        _require(state.sealed, "ledger is not sealed (tail truncation or unfinished review)")
    return state


class LedgerWriter:
    """Append validated snapshots and a final seal, including safe restart."""

    def __init__(
        self,
        path: pathlib.Path,
        ledger_id: str,
        *,
        create: bool = True,
        records: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.path = path
        self.ledger_id = ledger_id
        _require(bool(ledger_id), "ledger_id required")
        if create:
            _require(not path.exists() or path.stat().st_size == 0, f"refusing to overwrite ledger: {path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            self.state = LedgerState(ledger_id=ledger_id)
        else:
            _require(path.exists(), f"ledger does not exist: {path}")
            _require(records is not None, "safe resume requires organizer source records")
            self.state = materialize_ledger(iter_events(path), records, require_sealed=False)
            _require(self.state.ledger_id == ledger_id, "resume ledger_id mismatch")
            _require(not self.state.sealed, "cannot resume a sealed ledger")

    def _append(self, event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        _require(not self.state.sealed, "ledger is already sealed")
        event = make_event(
            ledger_id=self.ledger_id,
            seq=self.state.event_count,
            prev_event_sha256=self.state.head_sha256,
            event_type=event_type,
            payload=payload,
        )
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(event) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.state.event_count += 1
        self.state.head_sha256 = str(event["event_sha256"])
        return event

    def append_snapshot(self, snapshot: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
        routes = validate_snapshot(snapshot, record)
        record_id = _record_id(record)
        prior = self.state.active.get(record_id)
        supersedes = snapshot.get("supersedes_event_id")
        if prior is None:
            _require(supersedes is None, f"{record_id}: initial snapshot claims predecessor")
        else:
            _require(supersedes == prior.event_id, f"{record_id}: wrong superseded event")
            _require(
                snapshot["source"]["source_sha256"] == prior.source_sha256,
                f"{record_id}: revision changed immutable source",
            )
        event = self._append("record_snapshot", snapshot)
        self.state.active[record_id] = ActiveRecord(
            event_id=str(event["event_id"]),
            event_sha256=str(event["event_sha256"]),
            snapshot_sha256=str(snapshot["snapshot_sha256"]),
            source_sha256=str(snapshot["source"]["source_sha256"]),
            resolved_items=tuple(item for item in ITEMS if routes[item]["status"] == "resolved"),
        )
        return event

    def seal(
        self, *, expected_record_ids: Iterable[str], scope_items: Sequence[str] = ITEMS
    ) -> dict[str, Any]:
        payload = make_seal_payload(
            self.state, expected_record_ids=expected_record_ids, scope_items=scope_items
        )
        _validate_seal(payload, self.state)
        event = self._append("ledger_sealed", payload)
        self.state.sealed = True
        self.state.seal = payload
        return event


def read_records_gz(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            record_id = _record_id(row)
            _require(record_id not in rows, f"{path}:{line_number}: duplicate id {record_id}")
            rows[record_id] = row
    return rows


def _active_snapshots(
    events: Iterable[Mapping[str, Any]], state: LedgerState
) -> Iterator[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    active_ids = {value.event_id for value in state.active.values()}
    for event in events:
        if event.get("event_id") in active_ids:
            yield event, event["payload"]


def _role_decisions(snapshot: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {role: {} for role in ROLES}
    for review in snapshot["reviews"]:
        role = review["role"]
        for item, decision in review["decisions"].items():
            cell = dict(decision)
            cell["evidence"] = decision["evidence"]["quote"]
            cell["evidence_locations"] = decision["evidence"]["locations"]
            cell["review_id"] = review["review_id"]
            cell["review_sha256"] = review["review_sha256"]
            cell["vote_id"] = _vote_id(review, item)
            cell["role"] = role
            cell["reviewer_provenance"] = review["reviewer"]
            result[role][item] = cell
    return result


def _selected_vote_provenance(
    role_cells: Mapping[str, Mapping[str, Mapping[str, Any]]],
    item: str,
    resolution: Mapping[str, Any],
) -> dict[str, Any]:
    selected = set(resolution["selected_vote_ids"])
    provenance: dict[str, Any] = {}
    for role in ROLES:
        cell = role_cells[role].get(item)
        if cell is None or cell["vote_id"] not in selected:
            continue
        provenance[role] = {
            "review_id": cell["review_id"],
            "review_sha256": cell["review_sha256"],
            "vote_id": cell["vote_id"],
            "label": cell["label"],
            "confidence": cell["confidence"],
            "reviewer": cell["reviewer_provenance"],
        }
    _require(
        {value["vote_id"] for value in provenance.values()} == selected,
        f"{item}: selected vote provenance is incomplete",
    )
    return provenance


def iter_resolved_rows(
    events: Sequence[Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    *,
    require_all_items: bool = True,
) -> Iterator[dict[str, Any]]:
    """Yield rich, fully validated rows suitable for downstream gold export."""

    state = materialize_ledger(events, records, require_sealed=True)
    if require_all_items:
        _require(state.seal is not None and state.seal["scope_items"] == list(ITEMS), "seal is not full-24")
        _require(set(state.active) == set(records), "sealed ledger does not cover every supplied source record")
        _require(
            state.resolved_cells == len(records) * len(ITEMS),
            "sealed ledger has unresolved cells in the supplied source scope",
        )
    by_id: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    for event, snapshot in _active_snapshots(events, state):
        by_id[str(snapshot["source"]["record_id"])] = (event, snapshot)
    for record_id in sorted(by_id):
        event, snapshot = by_id[record_id]
        role_cells = _role_decisions(snapshot)
        decisions: dict[str, Any] = {}
        for item in ITEMS:
            resolution = snapshot["resolutions"].get(item)
            if resolution is None:
                if require_all_items:
                    raise LedgerValidationError(f"{record_id}:{item}: unresolved during export")
                continue
            chosen_role = "adjudicator" if resolution["route"] == "adjudication" else "candidate"
            chosen = role_cells[chosen_role][item]
            decisions[item] = {
                "label": int(resolution["final_label"]),
                "confidence": chosen["confidence"],
                "evidence": chosen["evidence"],
                "evidence_locations": chosen["evidence_locations"],
                "reason": resolution["reason"],
                "route": resolution["route"],
                "trigger_codes": resolution["trigger_codes"],
                "selected_vote_ids": resolution["selected_vote_ids"],
                "reviewer_provenance": chosen["reviewer_provenance"],
                "vote_provenance": _selected_vote_provenance(role_cells, item, resolution),
            }
        yield {
            "schema_version": RESOLVED_SCHEMA,
            "status": "ok",
            "id": record_id,
            "source_sha256": snapshot["source"]["source_sha256"],
            "snapshot_event_id": event["event_id"],
            "snapshot_event_sha256": event["event_sha256"],
            "decisions": decisions,
        }


def export_build_gold_inputs(
    ledger_path: pathlib.Path,
    records: Mapping[str, Mapping[str, Any]],
    output_dir: pathlib.Path,
) -> dict[str, Any]:
    """Write validated primary/verifier/adjudication JSONL for build_gold.py."""

    state = materialize_ledger(iter_events(ledger_path), records, require_sealed=True)
    _require(state.seal is not None and state.seal["scope_items"] == list(ITEMS), "build export requires full-24 seal")
    _require(set(state.active) == set(records), "build export requires every supplied source record")
    _require(
        state.resolved_cells == len(records) * len(ITEMS),
        "build export requires zero unresolved cells in the supplied source scope",
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "primary": output_dir / "primary.jsonl",
        "verifier": output_dir / "verifier.jsonl",
        "adjudication": output_dir / "adjudication.jsonl",
        "resolved": output_dir / "resolved_rows.jsonl",
    }
    handles = {name: path.open("w", encoding="utf-8", newline="\n") for name, path in paths.items()}
    counts = Counter()
    try:
        for event, snapshot in _active_snapshots(iter_events(ledger_path), state):
            record_id = str(snapshot["source"]["record_id"])
            role_cells = _role_decisions(snapshot)
            primary = {"status": "ok", "id": record_id, "decisions": role_cells["candidate"]}
            verifier = {"status": "ok", "id": record_id, "decisions": role_cells["verifier"]}
            adjudicated_items = {
                item: role_cells["adjudicator"][item]
                for item, resolution in snapshot["resolutions"].items()
                if resolution["route"] == "adjudication"
            }
            handles["primary"].write(canonical_json(primary) + "\n")
            handles["verifier"].write(canonical_json(verifier) + "\n")
            if adjudicated_items:
                handles["adjudication"].write(
                    canonical_json({"status": "ok", "id": record_id, "decisions": adjudicated_items}) + "\n"
                )
            resolved_decisions: dict[str, Any] = {}
            for item, resolution in snapshot["resolutions"].items():
                chosen_role = "adjudicator" if resolution["route"] == "adjudication" else "candidate"
                chosen = role_cells[chosen_role][item]
                resolved_decisions[item] = {
                    "label": resolution["final_label"],
                    "confidence": chosen["confidence"],
                    "evidence": chosen["evidence"],
                    "evidence_locations": chosen["evidence_locations"],
                    "reason": resolution["reason"],
                    "route": resolution["route"],
                    "trigger_codes": resolution["trigger_codes"],
                    "selected_vote_ids": resolution["selected_vote_ids"],
                    "reviewer_provenance": chosen["reviewer_provenance"],
                    "vote_provenance": _selected_vote_provenance(role_cells, item, resolution),
                }
                counts[resolution["route"]] += 1
            handles["resolved"].write(
                canonical_json(
                    {
                        "schema_version": RESOLVED_SCHEMA,
                        "status": "ok",
                        "id": record_id,
                        "source_sha256": snapshot["source"]["source_sha256"],
                        "snapshot_event_id": event["event_id"],
                        "snapshot_event_sha256": event["event_sha256"],
                        "decisions": resolved_decisions,
                    }
                )
                + "\n"
            )
            counts["records"] += 1
    finally:
        for handle in handles.values():
            handle.close()
    manifest = {
        "schema_version": "dacon.independent.review_export.v1",
        "ledger_id": state.ledger_id,
        "ledger_head_sha256": state.head_sha256,
        "records": counts["records"],
        "expected_records": len(records),
        "cells": counts["independent_consensus"] + counts["adjudication"],
        "expected_cells": len(records) * len(ITEMS),
        "unresolved_cells": 0,
        "independent_consensus": counts["independent_consensus"],
        "adjudication": counts["adjudication"],
        "paths": {name: str(path) for name, path in paths.items()},
        "sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()},
    }
    (output_dir / "review_export_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def _audit_risk(item: str, resolution: Mapping[str, Any]) -> str:
    if item in ABSENCE_ITEMS and resolution["final_label"] == 1:
        return "absence_positive"
    if resolution["route"] == "adjudication":
        triggers = set(resolution["trigger_codes"])
        return "adjudicated_disagreement" if "disagreement" in triggers else "adjudicated_uncertainty"
    return "consensus_positive" if resolution["final_label"] == 1 else "consensus_negative"


def stratified_audit_sample(
    events: Sequence[Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    *,
    per_stratum: int = 5,
    seed: str = "independent-gold-audit-v1",
) -> dict[str, Any]:
    """Deterministically sample every item × risk stratum for human audit."""

    _require(per_stratum >= 1, "per_stratum must be positive")
    state = materialize_ledger(events, records, require_sealed=True)
    populations: Counter[str] = Counter()
    heaps: dict[str, list[tuple[int, str, str, dict[str, Any]]]] = {}
    for event, snapshot in _active_snapshots(events, state):
        record_id = str(snapshot["source"]["record_id"])
        role_cells = _role_decisions(snapshot)
        for item, resolution in snapshot["resolutions"].items():
            risk = _audit_risk(item, resolution)
            stratum = f"{item}|{risk}"
            populations[stratum] += 1
            rank_hex = sha256_text(f"{seed}\0{record_id}\0{item}\0{event['event_sha256']}")
            rank = int(rank_hex, 16)
            sample = {
                "stratum": stratum,
                "record_id": record_id,
                "item": item,
                "source_sha256": snapshot["source"]["source_sha256"],
                "snapshot_event_id": event["event_id"],
                "snapshot_event_sha256": event["event_sha256"],
                "rank_sha256": rank_hex,
                "resolution": resolution,
                "votes": {
                    role: {
                        "label": cells[item]["label"],
                        "confidence": cells[item]["confidence"],
                        "uncertainty_codes": cells[item]["uncertainty_codes"],
                        "review_id": cells[item]["review_id"],
                        "reviewer_provenance": cells[item]["reviewer_provenance"],
                    }
                    for role, cells in role_cells.items()
                    if item in cells
                },
            }
            heap = heaps.setdefault(stratum, [])
            entry = (-rank, record_id, item, sample)
            if len(heap) < per_stratum:
                heapq.heappush(heap, entry)
            elif rank < -heap[0][0]:
                heapq.heapreplace(heap, entry)
    samples = [
        sample
        for stratum in sorted(heaps)
        for _, _, _, sample in sorted(heaps[stratum], key=lambda pair: (-pair[0], pair[1], pair[2]))
    ]
    return {
        "schema_version": "dacon.independent.audit_sample.v1",
        "ledger_id": state.ledger_id,
        "ledger_head_sha256": state.head_sha256,
        "seed": seed,
        "per_stratum": per_stratum,
        "population_cells": sum(populations.values()),
        "strata": dict(sorted(populations.items())),
        "sample_cells": len(samples),
        "samples": samples,
    }


def _validate_command(args: argparse.Namespace) -> int:
    records = read_records_gz(args.input)
    state = materialize_ledger(
        iter_events(args.ledger), records, require_sealed=args.require_sealed
    )
    print(
        json.dumps(
            {
                "ledger_id": state.ledger_id,
                "events": state.event_count,
                "head_sha256": state.head_sha256,
                "records": len(state.active),
                "resolved_cells": state.resolved_cells,
                "sealed": state.sealed,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _export_command(args: argparse.Namespace) -> int:
    records = read_records_gz(args.input)
    manifest = export_build_gold_inputs(args.ledger, records, args.output_dir)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _sample_command(args: argparse.Namespace) -> int:
    records = read_records_gz(args.input)
    report = stratified_audit_sample(
        read_events(args.ledger), records, per_stratum=args.per_stratum, seed=args.seed
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"sample_cells": report["sample_cells"], "output": str(args.output)}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="verify chain, sources, votes, routes, and seal")
    validate.add_argument("--ledger", type=pathlib.Path, required=True)
    validate.add_argument("--input", type=pathlib.Path, required=True)
    validate.add_argument("--require-sealed", action="store_true")
    validate.set_defaults(func=_validate_command)
    export = commands.add_parser("export-build-inputs", help="emit build_gold-compatible inputs")
    export.add_argument("--ledger", type=pathlib.Path, required=True)
    export.add_argument("--input", type=pathlib.Path, required=True)
    export.add_argument("--output-dir", type=pathlib.Path, required=True)
    export.set_defaults(func=_export_command)
    sample = commands.add_parser("sample", help="make a deterministic stratified audit queue")
    sample.add_argument("--ledger", type=pathlib.Path, required=True)
    sample.add_argument("--input", type=pathlib.Path, required=True)
    sample.add_argument("--output", type=pathlib.Path, required=True)
    sample.add_argument("--per-stratum", type=int, default=5)
    sample.add_argument("--seed", default="independent-gold-audit-v1")
    sample.set_defaults(func=_sample_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except LedgerValidationError as exc:
        print(f"review ledger validation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
