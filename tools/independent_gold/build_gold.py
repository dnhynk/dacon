"""Fail-closed final assembler for the independent 20,000-record gold set.

This is deliberately not an annotation merger.  It consumes only a sealed
review-ledger export and replays the ledger, role qualification, continuity,
audit, evidence, and lineage gates before it makes output visible.  The old
primary/verifier JSONL interface remains only as a rejection path.

The CLI is fixed to the organizer's 20,000-record file. ``BuildContract``
exists solely to exercise the same invariants on small synthetic test ledgers;
the CLI exposes no record-count override.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

try:
    from tools.independent_gold import codex_full_dev_gate
    from tools.independent_gold import opaque_alias_canary
    from tools.independent_gold import review_ledger
except ModuleNotFoundError:  # Direct script execution.
    import codex_full_dev_gate  # type: ignore[no-redef]
    import opaque_alias_canary  # type: ignore[no-redef]
    import review_ledger  # type: ignore[no-redef]


ROOT = pathlib.Path(__file__).resolve().parents[2]
OFFICIAL_UNLABELED = ROOT / "data_open" / "train_unlabeled.jsonl.gz"
OFFICIAL_DEV = ROOT / "data_open" / "dev.jsonl.gz"
OFFICIAL_DEV_LABELS = ROOT / "data_open" / "dev_labels.csv"

ITEMS = tuple(f"v{i}" for i in range(1, 25))
ABSENCE_ITEMS = frozenset({"v10", "v11", "v16", "v18", "v20"})
EXPECTED_RECORDS = 20_000
EXPECTED_CELLS = 480_000
MAX_EVIDENCE_CHARS = 500
MIN_AUDIT_PER_STRATUM = 30
MIN_AUDIT_PER_STRATUM_V3 = 300
HEX64 = re.compile(r"^[0-9a-f]{64}$")
EVENT_ID = re.compile(r"^evt-[0-9a-f]{32}$")

BUILD_SCHEMA = "dacon.independent.gold_build.v2"
AUDIT_PLAN_SCHEMA = "dacon.independent.gold_audit_plan.v2"
AUDIT_POLICY_SCHEMA = "dacon.independent.gold_audit_policy.v1"
AUDIT_PLAN_SCHEMA_V3 = "dacon.independent.gold_audit_plan.v3"
AUDIT_POLICY_SCHEMA_V3 = "dacon.independent.gold_audit_policy.v3"
AUDIT_REPORT_SCHEMA = "dacon.independent.gold_audit_report.v3"
AUDIT_REPORT_SCHEMA_V4 = "dacon.independent.gold_audit_report.v4"
AUDIT_SELECTION_SCHEMA = "dacon.independent.gold_audit_selection.v2"
AUDIT_SELECTION_SCHEMA_V3 = "dacon.independent.gold_audit_selection.v3"
AUDIT_RESULT_SCHEMA = "dacon.independent.gold_audit_result.v2"
HUMAN_RECEIPT_SCHEMA = "dacon.independent.human_adjudicator_receipt.v2"

PUBLISHABLE_NAMES = ("gold.csv", "gold_ledger.jsonl", "build_manifest.json")


class GoldBuildError(ValueError):
    """A mandatory final-gold invariant was not proven."""


@dataclass(frozen=True)
class BuildContract:
    expected_records: int = EXPECTED_RECORDS
    expected_cells: int = EXPECTED_CELLS
    require_official_input: bool = True
    minimum_audit_per_stratum: int = MIN_AUDIT_PER_STRATUM

    def __post_init__(self) -> None:
        if self.expected_records < 1:
            raise ValueError("expected_records must be positive")
        if self.expected_cells != self.expected_records * len(ITEMS):
            raise ValueError("expected_cells must equal records times 24")
        if self.minimum_audit_per_stratum < 1:
            raise ValueError("minimum audit sample must be positive")


@dataclass(frozen=True)
class BuildInputs:
    organizer_input: pathlib.Path
    review_ledger_path: pathlib.Path
    review_export_manifest: pathlib.Path
    candidate_qualification: pathlib.Path
    verifier_qualification: pathlib.Path
    audit_plan: pathlib.Path
    audit_report: pathlib.Path
    output_dir: pathlib.Path
    preflight_plan: pathlib.Path | None = None
    prepare_manifest: pathlib.Path | None = None
    adjudicator_qualification: pathlib.Path | None = None
    human_adjudicator_receipt: pathlib.Path | None = None
    continuity_plan: pathlib.Path | None = None
    candidate_continuity_epoch: pathlib.Path | None = None
    verifier_continuity_epoch: pathlib.Path | None = None
    adjudicator_continuity_epoch: pathlib.Path | None = None


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_object(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GoldBuildError(message)


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _require_hash(value: Any, context: str) -> str:
    _require(_is_hash(value), f"{context}: invalid SHA-256")
    return str(value)


def _require_event_id(value: Any, context: str) -> str:
    _require(
        isinstance(value, str) and EVENT_ID.fullmatch(value) is not None,
        f"{context}: invalid review-ledger event ID",
    )
    return str(value)


def _require_exact_keys(value: Mapping[str, Any], expected: Iterable[str], context: str) -> None:
    actual = set(value)
    wanted = set(expected)
    extra = sorted(str(key) for key in actual - wanted)
    missing = sorted(str(key) for key in wanted - actual)
    _require(not extra and not missing, f"{context}: keys differ; extra={extra}, missing={missing}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GoldBuildError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: pathlib.Path, context: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except FileNotFoundError as exc:
        raise GoldBuildError(f"{context}: missing file {path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoldBuildError(f"{context}: invalid UTF-8 JSON: {exc}") from exc
    _require(isinstance(value, dict), f"{context}: expected one JSON object")
    return value


def _read_jsonl(path: pathlib.Path, context: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                _require(bool(line.strip()), f"{context}:{line_number}: blank line")
                try:
                    row = json.loads(line, object_pairs_hook=_reject_duplicate_keys)
                except json.JSONDecodeError as exc:
                    raise GoldBuildError(
                        f"{context}:{line_number}: invalid JSON: {exc.msg}"
                    ) from exc
                _require(isinstance(row, dict), f"{context}:{line_number}: object required")
                rows.append(row)
    except FileNotFoundError as exc:
        raise GoldBuildError(f"{context}: missing file {path}") from exc
    return rows


def _parse_utc(value: Any, context: str) -> datetime:
    _require(isinstance(value, str) and bool(value), f"{context}: UTC timestamp required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GoldBuildError(f"{context}: invalid ISO-8601 timestamp") from exc
    _require(parsed.tzinfo is not None, f"{context}: timezone required")
    _require(parsed.utcoffset() == timezone.utc.utcoffset(parsed), f"{context}: must be UTC")
    return parsed


def _self_hash(value: Mapping[str, Any], field: str, context: str) -> str:
    claimed = _require_hash(value.get(field), f"{context}.{field}")
    calculated = sha256_object({key: child for key, child in value.items() if key != field})
    _require(claimed == calculated, f"{context}: self hash mismatch")
    return claimed


def _identity_token(value: Any) -> str:
    return str(value).strip().casefold()


def _resolve_bound_path(raw: Any, *, manifest_path: pathlib.Path, context: str) -> pathlib.Path:
    _require(isinstance(raw, str) and bool(raw.strip()), f"{context}: path required")
    candidate = pathlib.Path(raw)
    if candidate.is_absolute():
        return candidate.resolve()
    cwd_candidate = (pathlib.Path.cwd() / candidate).resolve()
    sibling_candidate = (manifest_path.parent / candidate).resolve()
    return cwd_candidate if cwd_candidate.exists() else sibling_candidate


def _load_organizer_records(
    path: pathlib.Path, contract: BuildContract
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    resolved = path.resolve()
    if contract.require_official_input:
        _require(
            resolved == OFFICIAL_UNLABELED.resolve(),
            "CLI gold construction accepts only the organizer unlabeled input",
        )
    rows: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    try:
        with gzip.open(resolved, "rt", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                _require(bool(line.strip()), f"organizer input:{line_number}: blank line")
                row = json.loads(line, object_pairs_hook=_reject_duplicate_keys)
                _require(isinstance(row, dict), f"organizer input:{line_number}: object required")
                record_id = str(row.get("id") or "")
                _require(bool(record_id), f"organizer input:{line_number}: missing id")
                _require(record_id not in rows, f"organizer input:{line_number}: duplicate id {record_id}")
                review_ledger.source_descriptor(row)
                rows[record_id] = row
                order.append(record_id)
    except FileNotFoundError as exc:
        raise GoldBuildError(f"organizer input missing: {resolved}") from exc
    _require(
        len(rows) == contract.expected_records,
        f"organizer input must contain exactly {contract.expected_records} IDs, found {len(rows)}",
    )
    return order, rows


def _manifest_paths(
    manifest: Mapping[str, Any], manifest_path: pathlib.Path
) -> dict[str, pathlib.Path]:
    raw_paths = manifest.get("paths")
    hashes = manifest.get("sha256")
    expected = {"primary", "verifier", "adjudication", "resolved"}
    _require(isinstance(raw_paths, Mapping), "review export: paths object required")
    _require(isinstance(hashes, Mapping), "review export: sha256 object required")
    _require(set(raw_paths) == expected, "review export: path keys differ")
    _require(set(hashes) == expected, "review export: hash keys differ")
    result: dict[str, pathlib.Path] = {}
    for name in sorted(expected):
        path = _resolve_bound_path(raw_paths[name], manifest_path=manifest_path, context=name)
        _require(path.is_file(), f"review export: missing {name} artifact {path}")
        _require_hash(hashes[name], f"review export.sha256.{name}")
        _require(file_sha256(path) == hashes[name], f"review export: {name} hash mismatch")
        result[name] = path
    return result


def _resolved_row_from_snapshot(
    event: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> dict[str, Any]:
    role_cells = review_ledger._role_decisions(snapshot)  # type: ignore[attr-defined]
    decisions: dict[str, Any] = {}
    for item in ITEMS:
        resolution = snapshot["resolutions"].get(item)
        _require(isinstance(resolution, Mapping), f"{snapshot['source']['record_id']}:{item}: unresolved")
        chosen_role = "adjudicator" if resolution["route"] == "adjudication" else "candidate"
        chosen = role_cells[chosen_role][item]
        decisions[item] = {
            "label": resolution["final_label"],
            "confidence": chosen["confidence"],
            "evidence": chosen["evidence"],
            "evidence_locations": chosen["evidence_locations"],
            "reason": resolution["reason"],
            "route": resolution["route"],
            "trigger_codes": resolution["trigger_codes"],
            "selected_vote_ids": resolution["selected_vote_ids"],
            "reviewer_provenance": chosen["reviewer_provenance"],
            "vote_provenance": review_ledger._selected_vote_provenance(  # type: ignore[attr-defined]
                role_cells, item, resolution
            ),
        }
    return {
        "schema_version": review_ledger.RESOLVED_SCHEMA,
        "status": "ok",
        "id": str(snapshot["source"]["record_id"]),
        "source_sha256": snapshot["source"]["source_sha256"],
        "snapshot_event_id": event["event_id"],
        "snapshot_event_sha256": event["event_sha256"],
        "decisions": decisions,
    }


def _require_full_record_model_reviews(snapshot: Mapping[str, Any]) -> None:
    """Production gold may only use qualified raw full-record model receipts."""

    record_id = str(snapshot["source"]["record_id"])
    reviews = snapshot.get("reviews")
    _require(isinstance(reviews, list), f"{record_id}: review list missing")
    for role in ("candidate", "verifier"):
        matches = [review for review in reviews if review.get("role") == role]
        _require(len(matches) == 1, f"{record_id}: exactly one {role} review required")
        review = matches[0]
        _require(
            review.get("schema_version") == review_ledger.FULL_RECORD_REVIEW_SCHEMA,
            f"{record_id}: {role} review is not a raw full-record artifact",
        )
        artifact = review.get("annotation_artifact")
        _require(
            isinstance(artifact, Mapping)
            and artifact.get("schema_version") == review_ledger.FULL_RECORD_ARTIFACT_SCHEMA,
            f"{record_id}: {role} annotation artifact missing",
        )


def _verify_review_export(
    *,
    ledger_path: pathlib.Path,
    manifest_path: pathlib.Path,
    records: Mapping[str, Mapping[str, Any]],
    contract: BuildContract,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    manifest = _load_json(manifest_path, "review export manifest")
    _require_exact_keys(
        manifest,
        (
            "schema_version", "ledger_id", "ledger_head_sha256", "records",
            "expected_records", "cells", "expected_cells", "unresolved_cells",
            "independent_consensus", "adjudication", "paths", "sha256",
        ),
        "review export manifest",
    )
    _require(
        manifest.get("schema_version") == "dacon.independent.review_export.v1",
        "unsupported review export schema",
    )
    _require_hash(manifest.get("ledger_head_sha256"), "review export ledger head")
    for key, expected in (
        ("records", contract.expected_records),
        ("expected_records", contract.expected_records),
        ("cells", contract.expected_cells),
        ("expected_cells", contract.expected_cells),
        ("unresolved_cells", 0),
    ):
        _require(type(manifest.get(key)) is int and manifest[key] == expected, f"review export: {key} mismatch")
    _require(
        type(manifest.get("independent_consensus")) is int
        and type(manifest.get("adjudication")) is int
        and manifest["independent_consensus"] + manifest["adjudication"] == contract.expected_cells,
        "review export: route counts do not cover the full population",
    )
    paths = _manifest_paths(manifest, manifest_path)
    exported_rows: dict[str, dict[str, Any]] = {}
    for line_number, row in enumerate(_read_jsonl(paths["resolved"], "resolved review export"), 1):
        record_id = str(row.get("id") or "")
        _require(bool(record_id), f"resolved review export:{line_number}: missing id")
        _require(record_id not in exported_rows, f"resolved review export:{line_number}: duplicate {record_id}")
        exported_rows[record_id] = row
    _require(set(exported_rows) == set(records), "resolved review export has missing or extra organizer IDs")

    try:
        state = review_ledger.materialize_ledger(
            review_ledger.iter_events(ledger_path), records, require_sealed=True
        )
    except (OSError, ValueError) as exc:
        raise GoldBuildError(f"review ledger replay failed: {exc}") from exc
    _require(state.ledger_id == manifest["ledger_id"], "review export uses another ledger id")
    _require(state.head_sha256 == manifest["ledger_head_sha256"], "review export ledger head is stale")
    _require(set(state.active) == set(records), "sealed review ledger omits or adds IDs")
    _require(state.resolved_cells == contract.expected_cells, "sealed review ledger is incomplete")
    _require(state.seal is not None, "review ledger has no seal receipt")
    _require(state.seal.get("scope_items") == list(ITEMS), "review ledger seal is not full-24")
    _require(
        state.seal.get("active_record_count") == contract.expected_records
        and state.seal.get("resolved_cell_count") == contract.expected_cells,
        "review ledger seal scope is incomplete",
    )
    active_event_ids = {entry.event_id for entry in state.active.values()}
    replayed = 0
    first_event_utc: datetime | None = None
    seal_utc: datetime | None = None
    for event in review_ledger.iter_events(ledger_path):
        occurred = _parse_utc(event.get("occurred_utc"), "review ledger event time")
        if first_event_utc is None or occurred < first_event_utc:
            first_event_utc = occurred
        if event.get("event_type") == "ledger_sealed":
            seal_utc = occurred
        if event.get("event_id") not in active_event_ids:
            continue
        if contract.expected_records == EXPECTED_RECORDS:
            _require_full_record_model_reviews(event["payload"])
        expected = _resolved_row_from_snapshot(event, event["payload"])
        _require(
            exported_rows.get(expected["id"]) == expected,
            f"{expected['id']}: export differs from sealed active snapshot",
        )
        replayed += 1
    _require(replayed == contract.expected_records, "not every active snapshot was replayed")
    _require(first_event_utc is not None and seal_utc is not None, "review ledger timing receipt is incomplete")
    receipt = {
        "verification_method": "full_chain_seal_source_and_active_snapshot_replay_v2",
        "review_ledger_sha256": file_sha256(ledger_path),
        "review_export_manifest_sha256": file_sha256(manifest_path),
        "ledger_id": state.ledger_id,
        "ledger_head_sha256": state.head_sha256,
        "sealed_records": len(state.active),
        "sealed_cells": state.resolved_cells,
        "active_state_sha256": state.seal["active_state_sha256"],
        "first_event_utc": first_event_utc.isoformat(),
        "seal_utc": seal_utc.isoformat(),
    }
    return manifest, exported_rows, receipt


QUALIFICATION_KEYS = frozenset(
    {
        "schema_version", "purpose", "is_full_official_dev_evaluation",
        "is_selected_panel", "qualified_for_declared_role",
        "qualified_annotator_role", "qualified_tuple_sha256",
        "qualified_for_gold_generation", "qualified_for_final_gold_generation",
        "final_gold_gate_note", "information_boundary", "topology",
        "records_sha256", "labels_sha256", "identity", "coverage", "integrity",
        "thresholds", "batch_lineages", "counts", "macro_positive_f1",
        "minimum_item_positive_f1", "by_item", "official_dev_role_gate_pass",
        "cells",
    }
)


def _replay_qualification_report(report: Mapping[str, Any]) -> dict[str, Any]:
    manifests: list[pathlib.Path] = []
    checkpoints: list[pathlib.Path] = []
    for index, batch in enumerate(report["batch_lineages"], 1):
        manifest_path = pathlib.Path(batch["run_manifest_path"]).resolve()
        checkpoint_path = pathlib.Path(batch["checkpoint_path"]).resolve()
        _require(manifest_path.is_file(), f"qualification batch {index}: run manifest missing")
        _require(checkpoint_path.is_file(), f"qualification batch {index}: checkpoint missing")
        _require(
            file_sha256(checkpoint_path) == batch["checkpoint_sha256"],
            f"qualification batch {index}: checkpoint hash mismatch",
        )
        manifests.append(manifest_path)
        checkpoints.append(checkpoint_path)
    try:
        return codex_full_dev_gate.full_dev_score(
            records_path=OFFICIAL_DEV,
            labels_path=OFFICIAL_DEV_LABELS,
            run_manifest_path=manifests,
            checkpoint_path=checkpoints,
            topology=report["topology"],
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise GoldBuildError(f"official-dev qualification replay failed: {exc}") from exc


def _validate_metric(metric: Any, expected_total: int, context: str) -> None:
    _require(isinstance(metric, Mapping), f"{context}: metric object required")
    _require_exact_keys(metric, ("tp", "fp", "fn", "tn", "positive_f1"), context)
    counts = []
    for key in ("tp", "fp", "fn", "tn"):
        value = metric.get(key)
        _require(type(value) is int and value >= 0, f"{context}.{key}: nonnegative integer required")
        counts.append(value)
    _require(sum(counts) == expected_total, f"{context}: count total mismatch")
    _require(type(metric.get("positive_f1")) in (int, float), f"{context}: F1 missing")


def _validate_qualification(
    path: pathlib.Path,
    role: str,
    *,
    replay: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    report = _load_json(path, f"{role} qualification")
    _require(set(report) == QUALIFICATION_KEYS, f"{role} qualification: incomplete/extra report keys")
    _require(report.get("schema_version") == codex_full_dev_gate.REPORT_SCHEMA, f"{role}: wrong gate schema")
    _require(
        report.get("purpose") == "complete_official_dev_annotator_role_qualification",
        f"{role}: wrong qualification purpose",
    )
    _require(report.get("is_full_official_dev_evaluation") is True, f"{role}: not full dev")
    _require(report.get("is_selected_panel") is False, f"{role}: selected panel cannot qualify")
    _require(report.get("qualified_for_declared_role") is True, f"{role}: role gate did not pass")
    _require(report.get("official_dev_role_gate_pass") is True, f"{role}: official-dev gate failed")
    _require(report.get("qualified_annotator_role") == role, f"{role}: report qualifies another role")
    _require_hash(report.get("qualified_tuple_sha256"), f"{role}: qualified tuple")
    _require(report.get("qualified_for_gold_generation") is False, f"{role}: invalid self-promotion")
    _require(report.get("qualified_for_final_gold_generation") is False, f"{role}: invalid final self-promotion")
    identity = report.get("identity")
    _require(isinstance(identity, Mapping), f"{role}: identity missing")
    _require_exact_keys(
        identity,
        ("tuple_sha256", "annotator_role", "prompt_profile", "prompt_lineage_sha256", "model_identity"),
        f"{role} identity",
    )
    _require(identity.get("annotator_role") == role, f"{role}: identity role mismatch")
    _require(identity.get("tuple_sha256") == report["qualified_tuple_sha256"], f"{role}: tuple mismatch")
    _require_hash(identity.get("prompt_lineage_sha256"), f"{role}: prompt lineage")
    model_identity = identity.get("model_identity")
    _require(isinstance(model_identity, Mapping), f"{role}: model identity missing")
    _require(
        model_identity.get("mode") in {"opaque_hosted_alias", "pinned_snapshot"},
        f"{role}: unsupported model identity mode",
    )
    _require(
        isinstance(model_identity.get("family"), str) and bool(model_identity["family"].strip()),
        f"{role}: model family missing",
    )
    _require(isinstance(identity.get("prompt_profile"), str) and bool(identity["prompt_profile"].strip()), f"{role}: prompt profile missing")
    coverage = report.get("coverage")
    _require(isinstance(coverage, Mapping), f"{role}: coverage missing")
    for key, value in {
        "unique_ids": 200, "unique_cells": 4_800, "missing_rows": 0,
        "duplicate_rows": 0, "extra_rows": 0, "abstentions": 0,
        "invalid_evidence": 0, "unsafe_tool_events": 0,
    }.items():
        _require(coverage.get(key) == value and type(coverage.get(key)) is int, f"{role}: coverage.{key} mismatch")
    integrity = report.get("integrity")
    required_integrity = {
        "full_200_id_cohort", "single_fixed_topology",
        "partition_plans_disjoint_and_complete", "run_instances_unique",
        "semantic_tuple_role_profile_model_identical", "task_and_receipt_lineage_valid",
        "unsafe_events_absent", "all_cells_binary",
        "all_nonabsence_positive_evidence_exact", "labels_opened_post_validation",
    }
    _require(isinstance(integrity, Mapping) and set(integrity) == required_integrity, f"{role}: integrity receipt incomplete")
    _require(all(value is True for value in integrity.values()), f"{role}: integrity receipt contains failure")
    boundary = report.get("information_boundary")
    _require(isinstance(boundary, Mapping), f"{role}: information boundary missing")
    _require(
        boundary.get("model_invocations") == 0
        and boundary.get("labels_read_after_all_candidate_artifact_validation") is True
        and boundary.get("competition_runtime_outputs_used") is False,
        f"{role}: information boundary failed",
    )
    _require(report.get("records_sha256") == file_sha256(OFFICIAL_DEV), f"{role}: dev source hash mismatch")
    _require(report.get("labels_sha256") == file_sha256(OFFICIAL_DEV_LABELS), f"{role}: dev labels hash mismatch")
    _validate_metric(report.get("counts"), 4_800, f"{role}.counts")
    by_item = report.get("by_item")
    _require(isinstance(by_item, Mapping) and set(by_item) == set(ITEMS), f"{role}: item metrics incomplete")
    for item in ITEMS:
        _validate_metric(by_item[item], 200, f"{role}.{item}")
        _require(by_item[item]["positive_f1"] >= 0.70, f"{role}:{item}: F1 below gate")
    _require(report.get("macro_positive_f1", -1) >= 0.90, f"{role}: macro F1 below gate")
    _require(report.get("minimum_item_positive_f1", -1) >= 0.70, f"{role}: minimum item F1 below gate")
    cells = report.get("cells")
    _require(isinstance(cells, list) and len(cells) == 4_800, f"{role}: dev cell receipt incomplete")
    cell_keys = {(row.get("id"), row.get("item")) for row in cells if isinstance(row, Mapping)}
    _require(len(cell_keys) == 4_800, f"{role}: duplicate or malformed dev cells")
    batches = report.get("batch_lineages")
    _require(isinstance(batches, list) and bool(batches), f"{role}: no batch lineages")
    for index, batch in enumerate(batches, 1):
        _require(isinstance(batch, Mapping), f"{role}: batch {index} malformed")
        for key in ("run_manifest_sha256", "run_instance_sha256", "cohort_plan_sha256", "checkpoint_sha256"):
            _require_hash(batch.get(key), f"{role}: batch {index}.{key}")
    replayed = dict((replay or _replay_qualification_report)(report))
    _require(replayed == report, f"{role}: report differs from independently replayed dev gate")
    return report


def _assert_role_independence(qualifications: Mapping[str, Mapping[str, Any]]) -> None:
    roles = list(qualifications)
    for left_index, left_role in enumerate(roles):
        left = qualifications[left_role]["identity"]
        for right_role in roles[left_index + 1 :]:
            right = qualifications[right_role]["identity"]
            _require(left["tuple_sha256"] != right["tuple_sha256"], f"{left_role}/{right_role}: same frozen tuple")
            _require(left["prompt_lineage_sha256"] != right["prompt_lineage_sha256"], f"{left_role}/{right_role}: same prompt lineage")
            _require(
                _identity_token(left["model_identity"]["family"])
                != _identity_token(right["model_identity"]["family"]),
                f"{left_role}/{right_role}: same model family",
            )


def _validate_continuity(
    *,
    plan_path: pathlib.Path | None,
    epoch_paths: Mapping[str, pathlib.Path | None],
    qualifications: Mapping[str, Mapping[str, Any]],
    organizer_input: pathlib.Path,
    organizer_order: Sequence[str],
) -> dict[str, Any]:
    opaque_roles = [
        role for role, report in qualifications.items()
        if report["identity"]["model_identity"].get("mode") == "opaque_hosted_alias"
    ]
    if not opaque_roles:
        _require(plan_path is None and all(path is None for path in epoch_paths.values()), "continuity artifacts supplied for non-opaque roles")
        return {"required_roles": [], "plan": None, "epochs": {}}
    _require(plan_path is not None, "opaque hosted aliases require a frozen continuity plan")
    try:
        plan = opaque_alias_canary.load_plan(plan_path)
    except (OSError, ValueError) as exc:
        raise GoldBuildError(f"continuity plan invalid: {exc}") from exc
    full = plan["organizer_inputs"]["full_unlabeled"]
    _require(full["sha256"] == file_sha256(organizer_input), "continuity plan uses another workload file")
    _require(full["record_count"] == len(organizer_order), "continuity plan workload count mismatch")
    _require(plan["workload_order"]["unlabeled_full_run"] == list(organizer_order), "continuity plan does not cover exact organizer workload order")
    _require(plan["organizer_inputs"]["dev"]["sha256"] == file_sha256(OFFICIAL_DEV), "continuity plan uses another official-dev cohort")
    receipts: dict[str, Any] = {}
    for role in opaque_roles:
        _require(role in {"candidate", "verifier"}, f"{role}: opaque continuity contract is unavailable; fail closed")
        epoch_path = epoch_paths.get(role)
        _require(epoch_path is not None, f"{role}: missing closed continuity epoch")
        try:
            epoch = opaque_alias_canary.load_epoch(epoch_path, plan=plan)
        except (OSError, ValueError) as exc:
            raise GoldBuildError(f"{role}: continuity epoch invalid: {exc}") from exc
        _require(epoch.get("annotator_role") == role, f"{role}: epoch role mismatch")
        _require(epoch.get("status") == "complete", f"{role}: continuity epoch is not complete")
        _require(epoch.get("continuity_qualified") is True, f"{role}: continuity did not pass")
        _require(epoch.get("invalidation") is None, f"{role}: continuity invalidated work")
        _require(epoch.get("next_required_checkpoint_id") is None, f"{role}: continuity checkpoints remain")
        _require(epoch.get("new_epoch_and_requalification_required") is False, f"{role}: requalification is required")
        identity = epoch["baseline_comparison_projection"]["semantic_identity"]
        qualified = qualifications[role]["identity"]
        _require(identity["tuple_sha256"] == qualified["tuple_sha256"], f"{role}: canary tuple is stale")
        _require(identity["prompt_lineage_sha256"] == qualified["prompt_lineage_sha256"], f"{role}: canary prompt lineage is stale")
        _require(identity["model_identity"] == qualified["model_identity"], f"{role}: canary model identity differs")
        _require(plan["roles"][role]["model_identity"] == qualified["model_identity"], f"{role}: plan model differs")
        receipts[role] = {
            "path": str(epoch_path.resolve()), "file_sha256": file_sha256(epoch_path),
            "manifest_sha256": epoch["manifest_sha256"], "epoch_id": epoch["epoch_id"],
            "checkpoints": len(epoch["checkpoint_history"]), "status": epoch["status"],
        }
    unused = [role for role, path in epoch_paths.items() if path is not None and role not in opaque_roles]
    _require(not unused, f"continuity epochs supplied for roles that do not require them: {unused}")
    return {
        "required_roles": opaque_roles,
        "plan": {
            "path": str(plan_path.resolve()), "file_sha256": file_sha256(plan_path),
            "manifest_sha256": plan["manifest_sha256"], "workload_records": len(organizer_order),
            "orchestration_boundary": (
                "The epoch proves the declared gapless checkpoint sequence; the "
                "orchestrator must separately preserve checkpoint placement around the workload."
            ),
        },
        "epochs": receipts,
    }


def _source_locations(record: Mapping[str, Any], quote: str) -> list[dict[str, Any]]:
    if not quote:
        return []
    evidence_hash = sha256_bytes(quote.encode("utf-8"))
    result: list[dict[str, Any]] = []
    for doc_index, document in enumerate(record["docs"]):
        text = str(document["text"])
        doc_id = str(document["doc_id"])
        doc_hash = sha256_bytes(text.encode("utf-8"))
        start = text.find(quote)
        while start >= 0:
            result.append(
                {
                    "doc_index": doc_index, "doc_id": doc_id, "start": start,
                    "end": start + len(quote), "source_doc_sha256": doc_hash,
                    "evidence_sha256": evidence_hash,
                }
            )
            start = text.find(quote, start + 1)
    return result


def _model_vote_matches_qualification(
    reviewer: Mapping[str, Any], role: str, qualification: Mapping[str, Any], context: str
) -> None:
    identity = qualification["identity"]
    required = {
        "reviewer_id", "kind", "independence_key", "method", "method_version",
        "model_family", "model_name", "prompt_sha256", "request_sha256",
        "response_sha256", "annotator_role", "qualified_tuple_sha256",
        "prompt_lineage_sha256", "model_identity",
    }
    _require(isinstance(reviewer, Mapping) and required <= set(reviewer), f"{context}: incomplete model provenance")
    _require(reviewer.get("kind") == "model", f"{context}: {role} vote is not a model vote")
    _require(reviewer.get("annotator_role") == role, f"{context}: vote role mismatch")
    _require(reviewer.get("qualified_tuple_sha256") == identity["tuple_sha256"], f"{context}: stale qualification tuple")
    _require(reviewer.get("prompt_lineage_sha256") == identity["prompt_lineage_sha256"], f"{context}: stale prompt lineage")
    _require(reviewer.get("model_identity") == identity["model_identity"], f"{context}: model identity mismatch")
    _require(
        _identity_token(reviewer.get("model_family")) == _identity_token(identity["model_identity"]["family"]),
        f"{context}: model family mismatch",
    )
    for key in ("prompt_sha256", "request_sha256", "response_sha256"):
        _require_hash(reviewer.get(key), f"{context}.{key}")


def _audit_stratum(item: str, cell: Mapping[str, Any]) -> str:
    return f"{item}|{cell['route']}|label{cell['label']}"


def _validate_resolved_rows(
    rows: Mapping[str, Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    qualifications: Mapping[str, Mapping[str, Any]],
    contract: BuildContract,
) -> tuple[Counter[str], list[dict[str, Any]], dict[str, set[str]]]:
    strata: Counter[str] = Counter()
    human_scope: list[dict[str, Any]] = []
    reviewer_ids: dict[str, set[str]] = {"candidate": set(), "verifier": set(), "adjudicator": set()}
    cells = 0
    for record_id, record in records.items():
        row = rows[record_id]
        _require_exact_keys(
            row,
            ("schema_version", "status", "id", "source_sha256", "snapshot_event_id", "snapshot_event_sha256", "decisions"),
            f"{record_id} resolved row",
        )
        _require(row["schema_version"] == review_ledger.RESOLVED_SCHEMA, f"{record_id}: wrong resolved schema")
        _require(row["status"] == "ok" and row["id"] == record_id, f"{record_id}: invalid row identity/status")
        _require(row["source_sha256"] == review_ledger.source_descriptor(record)["source_sha256"], f"{record_id}: source hash mismatch")
        _require_event_id(row["snapshot_event_id"], f"{record_id}: snapshot event id")
        _require_hash(row["snapshot_event_sha256"], f"{record_id}: snapshot event hash")
        decisions = row.get("decisions")
        _require(isinstance(decisions, Mapping) and set(decisions) == set(ITEMS), f"{record_id}: decisions must be exact full-24")
        for item in ITEMS:
            cell = decisions[item]
            context = f"{record_id}:{item}"
            _require(isinstance(cell, Mapping), f"{context}: decision object required")
            _require_exact_keys(
                cell,
                (
                    "label", "confidence", "evidence", "evidence_locations", "reason",
                    "route", "trigger_codes", "selected_vote_ids",
                    "reviewer_provenance", "vote_provenance",
                ),
                context,
            )
            label = cell.get("label")
            _require(type(label) is int and label in (0, 1), f"{context}: label must be binary integer; U is forbidden")
            _require(cell.get("confidence") in {"high", "medium"}, f"{context}: unresolved/low confidence final cell")
            reason = cell.get("reason")
            _require(isinstance(reason, str) and 0 < len(reason.strip()) <= 4_000, f"{context}: reason required")
            evidence = cell.get("evidence")
            _require(isinstance(evidence, str), f"{context}: evidence must be text")
            _require(len(evidence) <= MAX_EVIDENCE_CHARS, f"{context}: evidence exceeds {MAX_EVIDENCE_CHARS} characters")
            expected_locations = _source_locations(record, evidence)
            if label == 1 and item not in ABSENCE_ITEMS:
                _require(bool(evidence), f"{context}: positive witness evidence missing")
                _require(bool(expected_locations), f"{context}: evidence is not exact source text")
            else:
                _require(evidence == "", f"{context}: output convention requires empty evidence")
                _require(expected_locations == [], f"{context}: empty evidence location defect")
            _require(cell.get("evidence_locations") == expected_locations, f"{context}: evidence coordinates differ")
            route = cell.get("route")
            _require(route in {"independent_consensus", "adjudication"}, f"{context}: unresolved route")
            triggers = cell.get("trigger_codes")
            _require(isinstance(triggers, list) and all(isinstance(code, str) and code for code in triggers), f"{context}: trigger codes malformed")
            provenance = cell.get("vote_provenance")
            expected_roles = {"candidate", "verifier"} if route == "independent_consensus" else {"candidate", "verifier", "adjudicator"}
            _require(isinstance(provenance, Mapping) and set(provenance) == expected_roles, f"{context}: deciding vote provenance incomplete")
            selected = cell.get("selected_vote_ids")
            _require(
                isinstance(selected, list) and len(selected) == len(set(selected))
                and set(selected) == {vote.get("vote_id") for vote in provenance.values()},
                f"{context}: selected vote IDs/provenance mismatch",
            )
            for role in ("candidate", "verifier"):
                vote = provenance[role]
                _require(isinstance(vote, Mapping), f"{context}:{role}: vote object required")
                vote_label = vote.get("label")
                _require(
                    (type(vote_label) is int and vote_label in (0, 1))
                    or (route == "adjudication" and vote_label == "U"),
                    f"{context}:{role}: invalid vote",
                )
                _require(
                    vote.get("confidence") in {"high", "medium", "low"},
                    f"{context}:{role}: vote confidence missing/invalid",
                )
                _model_vote_matches_qualification(vote.get("reviewer"), role, qualifications[role], context)
                reviewer_ids[role].add(str(vote["reviewer"]["reviewer_id"]))
            if route == "independent_consensus":
                _require(provenance["candidate"]["label"] == provenance["verifier"]["label"] == label, f"{context}: claimed consensus is not consensus")
            else:
                adjudicator = provenance["adjudicator"]
                reviewer = adjudicator.get("reviewer")
                _require(adjudicator.get("label") == label, f"{context}: adjudicator did not decide final label")
                _require(isinstance(reviewer, Mapping), f"{context}: adjudicator provenance missing")
                reviewer_ids["adjudicator"].add(str(reviewer.get("reviewer_id") or ""))
                if reviewer.get("kind") == "model":
                    _require("adjudicator" in qualifications, f"{context}: model adjudicator is unqualified")
                    _model_vote_matches_qualification(reviewer, "adjudicator", qualifications["adjudicator"], context)
                elif reviewer.get("kind") == "human":
                    required_human = {
                        "reviewer_id", "kind", "independence_key", "method",
                        "method_version", "review_protocol_version",
                    }
                    _require(required_human <= set(reviewer), f"{context}: incomplete human adjudicator provenance")
                    human_scope.append(
                        {
                            "id": record_id, "item": item, "vote_id": adjudicator["vote_id"],
                            "review_id": adjudicator["review_id"],
                            "review_sha256": adjudicator["review_sha256"],
                            "reviewer_id": reviewer["reviewer_id"], "label": label,
                        }
                    )
                else:
                    raise GoldBuildError(f"{context}: unsupported adjudicator kind")
            strata[_audit_stratum(item, cell)] += 1
            cells += 1
    _require(cells == contract.expected_cells, f"resolved cell count must be {contract.expected_cells}")
    return strata, human_scope, reviewer_ids


ROSTER_KEYS = frozenset(
    {
        "reviewer_id", "identity_provider", "identity_subject", "independence_key",
        "protocol_version", "attestation_path", "attestation_sha256",
    }
)


def _validate_human_roster(
    roster: Any, *, manifest_path: pathlib.Path, context: str
) -> dict[str, dict[str, Any]]:
    _require(isinstance(roster, list) and bool(roster), f"{context}: non-empty roster required")
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(roster, 1):
        _require(isinstance(raw, Mapping) and set(raw) == ROSTER_KEYS, f"{context}:{index}: roster keys differ")
        reviewer_id = str(raw.get("reviewer_id") or "")
        _require(bool(reviewer_id) and reviewer_id not in result, f"{context}:{index}: duplicate/empty reviewer")
        for key in ("identity_provider", "identity_subject", "independence_key", "protocol_version"):
            _require(isinstance(raw.get(key), str) and bool(raw[key].strip()), f"{context}:{index}.{key} required")
        attestation = _resolve_bound_path(raw.get("attestation_path"), manifest_path=manifest_path, context=context)
        _require(attestation.is_file(), f"{context}:{reviewer_id}: attestation missing")
        _require_hash(raw.get("attestation_sha256"), f"{context}:{reviewer_id}: attestation hash")
        _require(file_sha256(attestation) == raw["attestation_sha256"], f"{context}:{reviewer_id}: attestation hash mismatch")
        result[reviewer_id] = dict(raw)
    return result


def _validate_human_adjudication(
    receipt_path: pathlib.Path | None,
    human_scope: Sequence[Mapping[str, Any]],
    rows: Mapping[str, Mapping[str, Any]],
    export_manifest_path: pathlib.Path,
    export_manifest: Mapping[str, Any],
    reviewer_ids: Mapping[str, set[str]],
) -> dict[str, Any] | None:
    if not human_scope:
        _require(receipt_path is None, "human adjudicator receipt supplied but no human decided a cell")
        return None
    _require(receipt_path is not None, "human adjudication requires a strict provenance receipt")
    receipt = _load_json(receipt_path, "human adjudicator receipt")
    _require_exact_keys(
        receipt,
        (
            "schema_version", "status", "review_export_manifest_sha256",
            "review_ledger_head_sha256", "adjudication_scope_sha256",
            "adjudicated_cells", "completed_utc", "reviewer_roster", "receipt_sha256",
        ),
        "human adjudicator receipt",
    )
    _require(receipt.get("schema_version") == HUMAN_RECEIPT_SCHEMA, "wrong human receipt schema")
    _require(receipt.get("status") == "PASS", "human adjudication receipt did not pass")
    _self_hash(receipt, "receipt_sha256", "human adjudicator receipt")
    _require(receipt.get("review_export_manifest_sha256") == file_sha256(export_manifest_path), "human receipt is bound to another review export")
    _require(receipt.get("review_ledger_head_sha256") == export_manifest["ledger_head_sha256"], "human receipt is bound to another ledger head")
    scope = sorted((dict(value) for value in human_scope), key=lambda row: (row["id"], row["item"]))
    _require(receipt.get("adjudication_scope_sha256") == sha256_object(scope), "human adjudication scope mismatch")
    _require(receipt.get("adjudicated_cells") == len(scope), "human adjudication count mismatch")
    _parse_utc(receipt.get("completed_utc"), "human receipt completion")
    roster = _validate_human_roster(receipt.get("reviewer_roster"), manifest_path=receipt_path, context="human adjudicators")
    scope_ids = {str(row["reviewer_id"]) for row in scope}
    _require(set(roster) == scope_ids, "human roster differs from deciding reviewers")
    prohibited_ids = reviewer_ids["candidate"] | reviewer_ids["verifier"]
    _require(not (scope_ids & prohibited_ids), "human adjudicator reuses candidate/verifier identity")
    prohibited_keys: set[str] = set()
    for row in rows.values():
        for cell in row["decisions"].values():
            for role in ("candidate", "verifier"):
                reviewer = cell["vote_provenance"][role]["reviewer"]
                prohibited_keys.add(_identity_token(reviewer["independence_key"]))
            if cell["route"] == "adjudication":
                reviewer = cell["vote_provenance"]["adjudicator"]["reviewer"]
                if reviewer.get("kind") == "human":
                    roster_row = roster[str(reviewer["reviewer_id"])]
                    _require(
                        reviewer["review_protocol_version"] == roster_row["protocol_version"]
                        and _identity_token(reviewer["independence_key"])
                        == _identity_token(roster_row["independence_key"]),
                        "human deciding vote differs from roster provenance",
                    )
    _require(
        all(_identity_token(row["independence_key"]) not in prohibited_keys for row in roster.values()),
        "human adjudicator is not independent from candidate/verifier",
    )
    return {
        "path": str(receipt_path.resolve()), "file_sha256": file_sha256(receipt_path),
        "receipt_sha256": receipt["receipt_sha256"], "adjudicated_cells": len(scope),
    }


def _rank_audit_cell(seed: str, record_id: str, item: str, snapshot_hash: str) -> str:
    return sha256_bytes(f"{seed}\0{record_id}\0{item}\0{snapshot_hash}".encode("utf-8"))


def _audit_completeness_stratum(record: Mapping[str, Any]) -> str:
    completeness = record.get("input_completeness")
    dropped = record.get("dropped_doc_counts")
    fully_observed = (
        isinstance(completeness, Mapping)
        and bool(completeness)
        and all(value is True for value in completeness.values())
        and (not isinstance(dropped, Mapping) or not any(dropped.values()))
    )
    return "complete" if fully_observed else "incomplete"


def _audit_selection_category(cell: Mapping[str, Any]) -> tuple[str, bool]:
    """Return the protocol risk category and whether every cell is mandatory."""

    if cell["route"] == "adjudication":
        return "mandatory_adjudication", True
    provenance = cell["vote_provenance"]
    confidences = {
        str(provenance[role]["confidence"])
        for role in ("candidate", "verifier")
    }
    if cell["label"] == 0 and "medium" in confidences:
        # All available decision-boundary negatives are reviewed, which is
        # stronger than taking only the nearest 30.
        return "agreement_boundary_negative", True
    if cell["label"] == 1 and "medium" in confidences:
        return "mandatory_medium_positive", True
    if cell["label"] == 1:
        return "agreement_positive", False
    return "agreement_remaining_negative", False


def _expected_audit_selection(
    rows: Mapping[str, Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    *,
    seed: str,
    per_stratum: int,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    populations: Counter[str] = Counter()
    candidates: dict[str, list[dict[str, Any]]] = {}
    for record_id, row in rows.items():
        completeness = _audit_completeness_stratum(records[record_id])
        for item in ITEMS:
            cell = row["decisions"][item]
            category, mandatory = _audit_selection_category(cell)
            stratum = f"{item}|{category}|{completeness}"
            populations[stratum] += 1
            rank = _rank_audit_cell(seed, record_id, item, row["snapshot_event_sha256"])
            candidates.setdefault(stratum, []).append(
                {
                    "schema_version": AUDIT_SELECTION_SCHEMA, "id": record_id,
                    "item": item, "stratum": stratum, "source_sha256": row["source_sha256"],
                    "snapshot_event_sha256": row["snapshot_event_sha256"],
                    "selection_category": category,
                    "completeness_stratum": completeness,
                    "mandatory": mandatory,
                    "rank_sha256": rank,
                }
            )
    selected: list[dict[str, Any]] = []
    for stratum in sorted(candidates):
        ranked = sorted(candidates[stratum], key=lambda row: (row["rank_sha256"], row["id"], row["item"]))
        if ranked[0]["mandatory"]:
            selected.extend(ranked)
        else:
            selected.extend(ranked[: min(per_stratum, len(ranked))])
    return selected, populations


def _rank_audit_cell_v3(seed: str, record_id: str, item: str, source_hash: str) -> str:
    """Rank only organizer-fixed identity/source, never annotation events or votes."""

    return sha256_bytes(
        f"dacon-independent-audit-v3\0{seed}\0{record_id}\0{item}\0{source_hash}".encode("utf-8")
    )


def _expected_audit_selection_v3(
    rows: Mapping[str, Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    *,
    seed: str,
    per_stratum: int,
    family_by_id: Mapping[str, str],
    recurring_family_per_stratum: int,
) -> tuple[list[dict[str, Any]], Counter[str], Counter[str]]:
    """Census risk cells, sample each primary stratum, cover recurring families.

    Family coverage is an *additional* minimum for recurring source families,
    not a replacement for item/category/completeness sampling.  The family
    assignment must be independently replayed from organizer inputs by the
    caller before invoking this function.
    """

    _require(per_stratum >= MIN_AUDIT_PER_STRATUM_V3, "v3 audit sample is below 300")
    _require(recurring_family_per_stratum >= 1, "v3 recurring family minimum must be positive")
    _require(set(family_by_id) == set(records), "v3 family assignment omits organizer records")
    _require(set(rows) == set(records), "v3 audit rows differ from organizer records")
    family_sizes = Counter(family_by_id.values())
    _require(
        all(isinstance(value, str) and bool(value.strip()) for value in family_by_id.values()),
        "v3 family assignment contains an empty/nontext family ID",
    )

    populations: Counter[str] = Counter()
    recurring_populations: Counter[str] = Counter()
    primary: dict[str, list[dict[str, Any]]] = {}
    recurring: dict[str, list[dict[str, Any]]] = {}
    for record_id, record in records.items():
        completeness = _audit_completeness_stratum(record)
        family_id = family_by_id[record_id]
        source_hash = review_ledger.source_descriptor(record)["source_sha256"]
        row = rows[record_id]
        _require(row["source_sha256"] == source_hash, f"{record_id}: v3 audit source hash mismatch")
        for item in ITEMS:
            cell = row["decisions"][item]
            category, mandatory = _audit_selection_category(cell)
            # Incomplete organizer views cannot be accepted on consensus alone.
            _require(
                completeness == "complete" or category == "mandatory_adjudication",
                f"{record_id}:{item}: incomplete source escaped mandatory adjudication",
            )
            stratum = f"{item}|{category}|{completeness}"
            family_stratum = f"{stratum}|family={family_id}"
            populations[stratum] += 1
            candidate = {
                "schema_version": AUDIT_SELECTION_SCHEMA_V3,
                "id": record_id,
                "item": item,
                "stratum": stratum,
                "family_stratum": family_stratum,
                "family_id": family_id,
                "source_sha256": source_hash,
                "snapshot_event_sha256": row["snapshot_event_sha256"],
                "selection_category": category,
                "completeness_stratum": completeness,
                "mandatory": mandatory,
                "rank_sha256": _rank_audit_cell_v3(seed, record_id, item, source_hash),
            }
            primary.setdefault(stratum, []).append(candidate)
            if family_sizes[family_id] >= 2 and not mandatory:
                recurring_populations[family_stratum] += 1
                recurring.setdefault(family_stratum, []).append(candidate)

    reasons: dict[tuple[str, str], set[str]] = {}
    cells: dict[tuple[str, str], dict[str, Any]] = {}

    def select(values: Sequence[dict[str, Any]], count: int, reason: str) -> None:
        for value in sorted(values, key=lambda row: (row["rank_sha256"], row["id"], row["item"]))[:count]:
            key = (value["id"], value["item"])
            cells[key] = value
            reasons.setdefault(key, set()).add(reason)

    for stratum in sorted(primary):
        values = primary[stratum]
        if values[0]["mandatory"]:
            select(values, len(values), "mandatory")
        else:
            select(values, min(per_stratum, len(values)), "primary_stratum")
    for family_stratum in sorted(recurring):
        values = recurring[family_stratum]
        select(values, min(recurring_family_per_stratum, len(values)), "recurring_family")

    selected: list[dict[str, Any]] = []
    for key, value in cells.items():
        selected.append({**value, "selection_reasons": sorted(reasons[key])})
    selected.sort(key=lambda row: (row["stratum"], row["rank_sha256"], row["id"], row["item"]))
    return selected, populations, recurring_populations


def _verified_v3_family_assignments(
    *, policy: Mapping[str, Any], policy_path: pathlib.Path, organizer_input: pathlib.Path,
    expected_records: int = EXPECTED_RECORDS,
) -> dict[str, str]:
    family_path = _resolve_bound_path(
        policy.get("family_manifest_path"), manifest_path=policy_path,
        context="v3 organizer-only family manifest",
    )
    _require(family_path.is_file(), "v3 family manifest is missing")
    _require_hash(policy.get("family_manifest_sha256"), "v3 family manifest hash")
    _require(
        file_sha256(family_path) == policy["family_manifest_sha256"],
        "v3 family manifest changed after policy freeze",
    )
    manifest = _load_json(family_path, "v3 family manifest")
    try:
        from tools.independent_gold import notice_families
    except ModuleNotFoundError as exc:
        raise GoldBuildError("v3 organizer-only family verifier is unavailable") from exc
    try:
        notice_families.verify_family_manifest(
            manifest, organizer_input, expected_records=expected_records
        )
    except ValueError as exc:
        raise GoldBuildError(f"v3 family assignment replay failed: {exc}") from exc
    family_rows = manifest.get("records")
    _require(isinstance(family_rows, list), "v3 family manifest has no records")
    result: dict[str, str] = {}
    for row in family_rows:
        _require(isinstance(row, Mapping), "v3 family manifest row is not an object")
        record_id, family_id = row.get("record_id"), row.get("family_id")
        _require(isinstance(record_id, str) and bool(record_id), "v3 family record ID invalid")
        _require(isinstance(family_id, str) and bool(family_id), "v3 family ID invalid")
        _require(record_id not in result, "v3 family manifest repeats an organizer ID")
        result[record_id] = family_id
    return result


def _v3_audit_power_metadata(
    plan: Mapping[str, Any], selection: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Describe zero-defect *sampling* limits, never gold/production accuracy."""

    populations = plan["population_strata"]
    _require(isinstance(populations, Mapping), "v3 audit population strata missing")
    selected_by_stratum = Counter(str(row["stratum"]) for row in selection)
    mandatory_categories = {
        "agreement_boundary_negative", "mandatory_adjudication", "mandatory_medium_positive"
    }
    noncensus = [
        stratum for stratum, population in populations.items()
        if stratum.split("|")[1] not in mandatory_categories
        and population > plan["per_stratum"]
    ]
    strata: dict[str, dict[str, Any]] = {}
    for stratum, population in sorted(populations.items()):
        mandatory = stratum.split("|")[1] in mandatory_categories
        primary_n = population if mandatory else min(population, plan["per_stratum"])
        census = primary_n == population
        strata[stratum] = {
            "population": population,
            "primary_random_n": 0 if mandatory else primary_n,
            "audited_selected_n": selected_by_stratum[stratum],
            "census": census,
            "zero_defect_upper_95_one_sided": (
                None if census else 1 - 0.05 ** (1 / primary_n)
            ),
            "zero_defect_upper_95_simultaneous": (
                None if census else 1 - (0.05 / len(noncensus)) ** (1 / primary_n)
            ),
        }
    return {
        "interpretation": (
            "Conditional bounds apply only to the primary random item/category/completeness "
            "samples if the seed stayed secret until ledger seal, freeze timestamps are authentic, "
            "all selected blind judgments have zero defects, and independent auditors are "
            "source-correct. Recurring-family minimum is coverage, not a per-family error "
            "bound. This does not guarantee all 480000 labels or superiority to production."
        ),
        "confidence_level": 0.95,
        "recurring_family_per_stratum": plan["recurring_family_per_stratum"],
        "noncensus_strata": len(noncensus),
        "population_cells": sum(populations.values()),
        "selected_cells": len(selection),
        "strata": strata,
    }


def _validate_audit(
    *,
    plan_path: pathlib.Path,
    report_path: pathlib.Path,
    rows: Mapping[str, Mapping[str, Any]],
    organizer_input: pathlib.Path,
    review_export_manifest_path: pathlib.Path,
    review_export_manifest: Mapping[str, Any],
    reviewer_ids: Mapping[str, set[str]],
    records: Mapping[str, Mapping[str, Any]],
    review_started_utc: str,
    contract: BuildContract,
) -> dict[str, Any]:
    plan = _load_json(plan_path, "gold audit plan")
    is_v3 = plan.get("schema_version") == AUDIT_PLAN_SCHEMA_V3
    v3_plan_keys = (
        "seed_commitment_sha256", "family_manifest_path", "family_manifest_sha256",
        "recurring_family_per_stratum", "population_family_strata",
    ) if is_v3 else ()
    _require_exact_keys(
        plan,
        (
            "schema_version", "plan_id", "frozen_at_utc", "organizer_input_sha256",
            "review_export_manifest_sha256", "review_ledger_head_sha256",
            "policy_path", "policy_sha256",
            "population_records", "population_cells", "population_strata",
            "selection_method", "selection_seed", "per_stratum", "selection_path",
            "selection_sha256", "selected_cells", "zero_defect_required", "plan_sha256",
        ) + v3_plan_keys,
        "gold audit plan",
    )
    _require(
        plan.get("schema_version") == (AUDIT_PLAN_SCHEMA_V3 if is_v3 else AUDIT_PLAN_SCHEMA),
        "wrong audit plan schema",
    )
    _self_hash(plan, "plan_sha256", "gold audit plan")
    frozen_at = _parse_utc(plan.get("frozen_at_utc"), "audit plan freeze")
    policy_path = _resolve_bound_path(
        plan.get("policy_path"), manifest_path=plan_path, context="gold audit policy"
    )
    _require(policy_path.is_file(), "gold audit policy file missing")
    _require_hash(plan.get("policy_sha256"), "gold audit policy file hash")
    _require(file_sha256(policy_path) == plan["policy_sha256"], "gold audit policy file hash mismatch")
    policy = _load_json(policy_path, "gold audit policy")
    v3_policy_keys = (
        "seed_commitment_sha256", "recurring_family_per_stratum",
        "family_manifest_path", "family_manifest_sha256",
    ) if is_v3 else ("selection_seed",)
    _require_exact_keys(
        policy,
        (
            "schema_version", "policy_id", "frozen_at_utc",
            "organizer_input_sha256", "selection_method",
            "per_stratum", "mandatory_categories", "stratify_by_completeness",
            "zero_defect_required", "policy_sha256",
        ) + v3_policy_keys,
        "gold audit policy",
    )
    _require(
        policy.get("schema_version") == (AUDIT_POLICY_SCHEMA_V3 if is_v3 else AUDIT_POLICY_SCHEMA),
        "wrong audit policy schema",
    )
    _self_hash(policy, "policy_sha256", "gold audit policy")
    policy_frozen_at = _parse_utc(policy.get("frozen_at_utc"), "audit policy freeze")
    _require(
        policy_frozen_at <= _parse_utc(review_started_utc, "review ledger start"),
        "audit policy was not frozen before annotation/review began",
    )
    _require(policy_frozen_at <= frozen_at, "audit selection plan predates its policy")
    _require(plan.get("organizer_input_sha256") == file_sha256(organizer_input), "audit plan uses another organizer input")
    _require(policy.get("organizer_input_sha256") == plan["organizer_input_sha256"], "audit policy uses another organizer input")
    _require(plan.get("review_export_manifest_sha256") == file_sha256(review_export_manifest_path), "audit plan uses another review export")
    _require(plan.get("review_ledger_head_sha256") == review_export_manifest["ledger_head_sha256"], "audit plan uses another ledger head")
    _require(plan.get("population_records") == contract.expected_records, "audit population record count mismatch")
    _require(plan.get("population_cells") == contract.expected_cells, "audit population cell count mismatch")
    _require(
        plan.get("selection_method")
        == (
            "mandatory_risk_plus_source_rank_per_item_category_completeness_family_v3"
            if is_v3 else "mandatory_risk_plus_sha256_rank_per_item_category_completeness_v2"
        ),
        "unsupported audit selection method",
    )
    _require(plan.get("selection_method") == policy.get("selection_method"), "audit plan changed the frozen selection method")
    seed = plan.get("selection_seed")
    _require(isinstance(seed, str) and bool(seed), "audit selection seed required")
    if is_v3:
        _require(
            HEX64.fullmatch(seed) is not None,
            "v3 audit seed reveal must be 64 lowercase hex characters",
        )
        _require_hash(policy.get("seed_commitment_sha256"), "v3 seed commitment")
        commitment = sha256_bytes(
            f"dacon-independent-audit-v3-seed\0{seed}".encode("ascii")
        )
        _require(
            commitment == policy["seed_commitment_sha256"]
            == plan.get("seed_commitment_sha256"),
            "v3 seed reveal does not match pre-annotation commitment",
        )
    else:
        _require(seed == policy.get("selection_seed"), "audit plan changed the frozen selection seed")
    per_stratum = plan.get("per_stratum")
    minimum = max(contract.minimum_audit_per_stratum, MIN_AUDIT_PER_STRATUM_V3) if is_v3 else contract.minimum_audit_per_stratum
    _require(
        type(per_stratum) is int and per_stratum >= minimum,
        f"audit requires at least {minimum} samples per nonempty stratum",
    )
    _require(per_stratum == policy.get("per_stratum"), "audit plan changed the frozen sample size")
    _require(
        policy.get("mandatory_categories")
        == ["agreement_boundary_negative", "mandatory_adjudication", "mandatory_medium_positive"],
        "audit policy mandatory categories differ",
    )
    _require(policy.get("stratify_by_completeness") is True, "audit policy omits completeness strata")
    _require(policy.get("zero_defect_required") is True, "audit policy does not require zero defects")
    if is_v3:
        _require(
            plan.get("family_manifest_path") == policy.get("family_manifest_path")
            and plan.get("family_manifest_sha256") == policy.get("family_manifest_sha256"),
            "v3 family manifest differs between plan and policy",
        )
        family_by_id = _verified_v3_family_assignments(
            policy=policy, policy_path=policy_path, organizer_input=organizer_input,
            expected_records=contract.expected_records,
        )
        recurring_minimum = policy.get("recurring_family_per_stratum")
        _require(
            type(recurring_minimum) is int and recurring_minimum >= 1
            and plan.get("recurring_family_per_stratum") == recurring_minimum,
            "v3 recurring family minimum differs",
        )
        expected_selection, populations, family_populations = _expected_audit_selection_v3(
            rows, records, seed=seed, per_stratum=per_stratum,
            family_by_id=family_by_id,
            recurring_family_per_stratum=recurring_minimum,
        )
        _require(
            plan.get("population_family_strata") == dict(sorted(family_populations.items())),
            "v3 recurring family population differs",
        )
    else:
        expected_selection, populations = _expected_audit_selection(
            rows, records, seed=seed, per_stratum=per_stratum,
        )
    _require(plan.get("population_strata") == dict(sorted(populations.items())), "audit plan strata differ from population")
    selection_path = _resolve_bound_path(plan.get("selection_path"), manifest_path=plan_path, context="audit selection")
    _require(selection_path.is_file(), "audit selection file missing")
    _require_hash(plan.get("selection_sha256"), "audit selection hash")
    _require(file_sha256(selection_path) == plan["selection_sha256"], "audit selection hash mismatch")
    selection = _read_jsonl(selection_path, "audit selection")
    _require(selection == expected_selection, "audit selection is incomplete, stale, or cherry-picked")
    _require(plan.get("selected_cells") == len(selection), "audit selected-cell count mismatch")
    _require(plan.get("zero_defect_required") is True, "audit plan does not require zero defects")

    report = _load_json(report_path, "gold audit report")
    v3_report_keys = ("audit_power",) if is_v3 else ()
    _require_exact_keys(
        report,
        (
            "schema_version", "status", "audit_plan_sha256", "audit_plan_file_sha256",
            "selection_sha256", "audit_session_path", "audit_session_file_sha256",
            "audit_session_sha256", "blind_packets_path", "blind_packets_sha256",
            "judgments_path", "judgments_sha256", "started_utc", "completed_utc", "audited_cells",
            "defect_cells", "unresolved_cells", "results_path", "results_sha256",
            "auditor_roster", "report_sha256",
        ) + v3_report_keys,
        "gold audit report",
    )
    _require(
        report.get("schema_version") == (AUDIT_REPORT_SCHEMA_V4 if is_v3 else AUDIT_REPORT_SCHEMA),
        "wrong audit report schema",
    )
    _require(report.get("status") == "PASS", "audit report did not pass")
    _self_hash(report, "report_sha256", "gold audit report")
    if is_v3:
        _require(
            report.get("audit_power") == _v3_audit_power_metadata(plan, selection),
            "v3 audit power metadata differs from selected population",
        )
    _require(report.get("audit_plan_sha256") == plan["plan_sha256"], "audit report uses another plan")
    _require(report.get("audit_plan_file_sha256") == file_sha256(plan_path), "audit report plan-file hash mismatch")
    _require(report.get("selection_sha256") == plan["selection_sha256"], "audit report selection mismatch")
    started = _parse_utc(report.get("started_utc"), "audit start")
    completed = _parse_utc(report.get("completed_utc"), "audit completion")
    _require(frozen_at <= started <= completed, "audit plan was not frozen before execution")
    _require(report.get("audited_cells") == len(selection), "audit is partial")
    _require(report.get("defect_cells") == 0 and report.get("unresolved_cells") == 0, "audit found defects/unresolved cells")
    roster = _validate_human_roster(report.get("auditor_roster"), manifest_path=report_path, context="gold auditors")
    prohibited_ids = reviewer_ids["candidate"] | reviewer_ids["verifier"] | reviewer_ids["adjudicator"]
    _require(not (set(roster) & prohibited_ids), "gold auditor also cast an annotation vote")
    results_path = _resolve_bound_path(report.get("results_path"), manifest_path=report_path, context="audit results")
    _require(results_path.is_file(), "audit results file missing")
    _require_hash(report.get("results_sha256"), "audit results hash")
    _require(file_sha256(results_path) == report["results_sha256"], "audit results hash mismatch")
    results = _read_jsonl(results_path, "audit results")
    _require(len(results) == len(selection), "audit result rows are partial")
    try:
        from tools.independent_gold import gold_audit
    except ModuleNotFoundError:  # Direct script execution.
        import gold_audit  # type: ignore[no-redef]
    session_path = _resolve_bound_path(
        report.get("audit_session_path"), manifest_path=report_path, context="audit session"
    )
    _require(session_path.is_file(), "audit session file missing")
    _require_hash(report.get("audit_session_file_sha256"), "audit session file hash")
    _require(file_sha256(session_path) == report["audit_session_file_sha256"], "audit session file hash mismatch")
    session = _load_json(session_path, "gold audit session")
    _require_hash(report.get("audit_session_sha256"), "audit session self-hash")
    _self_hash(session, "session_sha256", "gold audit session")
    _require(session["session_sha256"] == report["audit_session_sha256"], "audit session self-hash mismatch")
    _require(session.get("status") == "OPEN", "audit session was not open")
    _require(session.get("audit_plan_sha256") == plan["plan_sha256"], "audit session uses another plan")
    _require(session.get("audit_plan_file_sha256") == file_sha256(plan_path), "audit session plan hash mismatch")
    _require(session.get("selection_sha256") == plan["selection_sha256"], "audit session selection mismatch")
    _require(session.get("source_first_blind_commitment_required") is True, "audit session was not source-first blind")
    _require(session.get("started_utc") == report["started_utc"], "audit session start differs from report")
    _require(session.get("auditor_roster") == report["auditor_roster"], "audit session roster differs from report")
    try:
        packet_path, packet_sha256 = gold_audit.verify_session_blind_packets(
            session=session, session_path=session_path, selection=selection, records=records
        )
    except (OSError, ValueError) as exc:
        raise GoldBuildError(f"audit blind packet replay failed: {exc}") from exc
    _require(
        packet_path == _resolve_bound_path(
            report.get("blind_packets_path"), manifest_path=report_path, context="audit blind packets"
        )
        and packet_sha256 == report.get("blind_packets_sha256"),
        "audit report blind packet binding mismatch",
    )
    judgments_path = _resolve_bound_path(
        report.get("judgments_path"), manifest_path=report_path, context="audit judgments"
    )
    _require(judgments_path.is_file(), "audit judgment file missing")
    _require_hash(report.get("judgments_sha256"), "audit judgment file hash")
    _require(file_sha256(judgments_path) == report["judgments_sha256"], "audit judgment file hash mismatch")
    judgments = _read_jsonl(judgments_path, "blind audit judgments")
    try:
        replayed_results, replayed_defects, replayed_unresolved = gold_audit.replay_blind_judgments(
            selection=selection,
            rows=rows,
            records=records,
            judgments=judgments,
            roster=roster,
            started_utc=report["started_utc"],
            completed_utc=report["completed_utc"],
        )
    except (OSError, ValueError) as exc:
        raise GoldBuildError(f"audit judgment replay failed: {exc}") from exc
    _require(replayed_defects == 0 and replayed_unresolved == 0, "audit judgment replay found defects")
    _require(results == replayed_results, "audit result rows differ from raw blind judgments")
    expected_by_key = {(row["id"], row["item"]): row for row in selection}
    seen: set[tuple[str, str]] = set()
    check_keys = {"source_match", "evidence_exact", "label_supported", "rule_application", "provenance_valid"}
    for index, result in enumerate(results, 1):
        _require_exact_keys(
            result,
            (
                "schema_version", "id", "item", "source_sha256",
                "snapshot_event_sha256", "outcome", "checks", "auditor_id",
                "reviewed_utc", "review_note",
            ),
            f"audit result {index}",
        )
        _require(result.get("schema_version") == AUDIT_RESULT_SCHEMA, f"audit result {index}: wrong schema")
        key = (str(result.get("id") or ""), str(result.get("item") or ""))
        _require(key in expected_by_key and key not in seen, f"audit result {index}: extra/duplicate cell")
        seen.add(key)
        selected = expected_by_key[key]
        _require(
            result.get("source_sha256") == selected["source_sha256"]
            and result.get("snapshot_event_sha256") == selected["snapshot_event_sha256"],
            f"audit result {index}: stale cell binding",
        )
        _require(result.get("outcome") == "PASS", f"audit result {index}: defect")
        checks = result.get("checks")
        _require(isinstance(checks, Mapping) and set(checks) == check_keys, f"audit result {index}: checks incomplete")
        _require(all(value is True for value in checks.values()), f"audit result {index}: a check failed")
        _require(result.get("auditor_id") in roster, f"audit result {index}: unknown auditor")
        reviewed = _parse_utc(result.get("reviewed_utc"), f"audit result {index} review time")
        _require(started <= reviewed <= completed, f"audit result {index}: timestamp outside audit window")
        _require(isinstance(result.get("review_note"), str) and bool(result["review_note"].strip()), f"audit result {index}: note required")
    _require(seen == set(expected_by_key), "audit did not cover every frozen selected cell")
    return {
        "plan_path": str(plan_path.resolve()), "plan_file_sha256": file_sha256(plan_path),
        "plan_sha256": plan["plan_sha256"], "report_path": str(report_path.resolve()),
        "report_file_sha256": file_sha256(report_path), "report_sha256": report["report_sha256"],
        "audit_session_file_sha256": report["audit_session_file_sha256"],
        "blind_packets_sha256": report["blind_packets_sha256"],
        "judgments_sha256": report["judgments_sha256"],
        "selected_cells": len(selection), "audited_cells": len(results), "defects": 0,
    }


def _invalidate_publishable_outputs(output_dir: pathlib.Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in PUBLISHABLE_NAMES:
        path = output_dir / name
        if path.exists():
            if not path.is_file():
                raise GoldBuildError(f"publishable output path is not a file: {path}")
            path.unlink()


def _write_outputs(
    *,
    output_dir: pathlib.Path,
    organizer_order: Sequence[str],
    rows: Mapping[str, Mapping[str, Any]],
    manifest_base: Mapping[str, Any],
) -> dict[str, Any]:
    fields = ["id", *ITEMS, *(f"e{i}" for i in range(1, 25))]
    final_paths = {name: output_dir / name for name in PUBLISHABLE_NAMES}
    try:
        with tempfile.TemporaryDirectory(prefix=".gold-build-v2-", dir=output_dir) as raw_stage:
            stage = pathlib.Path(raw_stage)
            ledger_path = stage / "gold_ledger.jsonl"
            csv_path = stage / "gold.csv"
            with ledger_path.open("w", encoding="utf-8", newline="\n") as handle:
                for record_id in organizer_order:
                    handle.write(canonical_json(rows[record_id]) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
                writer.writeheader()
                for record_id in organizer_order:
                    decisions = rows[record_id]["decisions"]
                    csv_row: dict[str, Any] = {"id": record_id}
                    for item in ITEMS:
                        cell = decisions[item]
                        csv_row[item] = cell["label"]
                        csv_row[f"e{item[1:]}"] = cell["evidence"] if cell["label"] == 1 else ""
                    writer.writerow(csv_row)
                handle.flush()
                os.fsync(handle.fileno())
            output_hashes = {"gold.csv": file_sha256(csv_path), "gold_ledger.jsonl": file_sha256(ledger_path)}
            manifest = {
                **dict(manifest_base),
                "outputs": {
                    "gold.csv": {"sha256": output_hashes["gold.csv"], "rows": len(organizer_order), "columns": len(fields)},
                    "gold_ledger.jsonl": {
                        "sha256": output_hashes["gold_ledger.jsonl"], "rows": len(organizer_order),
                        "cells": len(organizer_order) * len(ITEMS),
                    },
                },
                "gold_written": True,
            }
            manifest["manifest_sha256"] = sha256_object(manifest)
            manifest_path = stage / "build_manifest.json"
            with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(ledger_path, final_paths["gold_ledger.jsonl"])
            os.replace(csv_path, final_paths["gold.csv"])
            os.replace(manifest_path, final_paths["build_manifest.json"])
        _require(
            file_sha256(final_paths["gold.csv"]) == manifest["outputs"]["gold.csv"]["sha256"]
            and file_sha256(final_paths["gold_ledger.jsonl"]) == manifest["outputs"]["gold_ledger.jsonl"]["sha256"],
            "post-publication output hash mismatch",
        )
        return manifest
    except Exception:
        _invalidate_publishable_outputs(output_dir)
        raise


def build_gold(inputs: BuildInputs, *, contract: BuildContract = BuildContract()) -> dict[str, Any]:
    output_dir = inputs.output_dir.resolve()
    _invalidate_publishable_outputs(output_dir)
    try:
        organizer_order, records = _load_organizer_records(inputs.organizer_input, contract)
        export_manifest, rows, ledger_receipt = _verify_review_export(
            ledger_path=inputs.review_ledger_path.resolve(),
            manifest_path=inputs.review_export_manifest.resolve(),
            records=records,
            contract=contract,
        )
        qualifications: dict[str, dict[str, Any]] = {
            "candidate": _validate_qualification(inputs.candidate_qualification.resolve(), "candidate"),
            "verifier": _validate_qualification(inputs.verifier_qualification.resolve(), "verifier"),
        }
        raw_lineage_receipt: dict[str, Any] | None = None
        if contract.expected_records == EXPECTED_RECORDS:
            _require(
                inputs.preflight_plan is not None and inputs.prepare_manifest is not None,
                "production gold requires frozen preflight and prepare manifests",
            )
            try:
                from tools.independent_gold import raw_lineage_verify
            except ModuleNotFoundError:  # Direct script execution.
                import raw_lineage_verify  # type: ignore[no-redef]
            try:
                raw_lineage_receipt = raw_lineage_verify.verify_sealed_raw_lineage(
                    organizer_input=inputs.organizer_input.resolve(),
                    preflight_plan=inputs.preflight_plan.resolve(),
                    prepare_manifest=inputs.prepare_manifest.resolve(),
                    ledger_path=inputs.review_ledger_path.resolve(),
                    expected_records=contract.expected_records,
                )
            except (OSError, ValueError) as exc:
                raise GoldBuildError(f"production raw-run lineage failed: {exc}") from exc
            _require(raw_lineage_receipt["status"] == "PASS", "production raw-run lineage did not pass")
            _require(
                raw_lineage_receipt["sealed_ledger_head_sha256"] == export_manifest["ledger_head_sha256"],
                "production raw-run lineage uses another sealed ledger",
            )
            for role in ("candidate", "verifier"):
                _require(
                    raw_lineage_receipt["roles"][role]["qualification_object_sha256"]
                    == sha256_object(qualifications[role]),
                    f"{role} raw-run lineage uses another qualification report",
                )
        has_model_adjudication = any(
            cell["route"] == "adjudication"
            and cell["vote_provenance"]["adjudicator"]["reviewer"].get("kind") == "model"
            for row in rows.values() for cell in row["decisions"].values()
        )
        has_human_adjudication = any(
            cell["route"] == "adjudication"
            and cell["vote_provenance"]["adjudicator"]["reviewer"].get("kind") == "human"
            for row in rows.values() for cell in row["decisions"].values()
        )
        _require(not (has_model_adjudication and has_human_adjudication), "mixed model/human adjudication modes are forbidden")
        if has_model_adjudication:
            _require(inputs.adjudicator_qualification is not None, "model adjudicator qualification missing")
            _require(inputs.human_adjudicator_receipt is None, "human receipt cannot qualify model adjudication")
            qualifications["adjudicator"] = _validate_qualification(inputs.adjudicator_qualification.resolve(), "adjudicator")
        else:
            _require(inputs.adjudicator_qualification is None, "unused adjudicator qualification supplied")
        _assert_role_independence(qualifications)
        continuity_receipt = _validate_continuity(
            plan_path=inputs.continuity_plan.resolve() if inputs.continuity_plan else None,
            epoch_paths={
                "candidate": inputs.candidate_continuity_epoch.resolve() if inputs.candidate_continuity_epoch else None,
                "verifier": inputs.verifier_continuity_epoch.resolve() if inputs.verifier_continuity_epoch else None,
                "adjudicator": inputs.adjudicator_continuity_epoch.resolve() if inputs.adjudicator_continuity_epoch else None,
            },
            qualifications=qualifications,
            organizer_input=inputs.organizer_input.resolve(),
            organizer_order=organizer_order,
        )
        _, human_scope, reviewer_ids = _validate_resolved_rows(rows, records, qualifications, contract)
        if has_human_adjudication:
            _require(len(human_scope) > 0, "human adjudication scope unexpectedly empty")
        human_receipt = _validate_human_adjudication(
            inputs.human_adjudicator_receipt.resolve() if inputs.human_adjudicator_receipt else None,
            human_scope, rows, inputs.review_export_manifest.resolve(), export_manifest, reviewer_ids,
        )
        audit_receipt = _validate_audit(
            plan_path=inputs.audit_plan.resolve(), report_path=inputs.audit_report.resolve(), rows=rows,
            organizer_input=inputs.organizer_input.resolve(),
            review_export_manifest_path=inputs.review_export_manifest.resolve(),
            review_export_manifest=export_manifest,
            reviewer_ids=reviewer_ids,
            records=records,
            review_started_utc=ledger_receipt["first_event_utc"],
            contract=contract,
        )
        role_paths: dict[str, pathlib.Path] = {
            "candidate": inputs.candidate_qualification,
            "verifier": inputs.verifier_qualification,
        }
        if inputs.adjudicator_qualification is not None:
            role_paths["adjudicator"] = inputs.adjudicator_qualification
        qualification_receipts = {
            role: {
                "path": str(path.resolve()), "file_sha256": file_sha256(path.resolve()),
                "qualified_tuple_sha256": qualifications[role]["qualified_tuple_sha256"],
                "model_family": qualifications[role]["identity"]["model_identity"]["family"],
                "prompt_lineage_sha256": qualifications[role]["identity"]["prompt_lineage_sha256"],
                "role_gate_pass": True,
            }
            for role, path in role_paths.items()
        }
        manifest_base = {
            "schema_version": BUILD_SCHEMA,
            "organizer_input": {
                "path": str(inputs.organizer_input.resolve()), "sha256": file_sha256(inputs.organizer_input.resolve()),
                "records": contract.expected_records, "cells": contract.expected_cells,
                "ordered_ids_sha256": sha256_object(list(organizer_order)),
            },
            "review_ledger_verification": ledger_receipt,
            "review_export": {
                "manifest_path": str(inputs.review_export_manifest.resolve()),
                "manifest_sha256": file_sha256(inputs.review_export_manifest.resolve()),
                "resolved_rows_sha256": export_manifest["sha256"]["resolved"], "unresolved_cells": 0,
            },
            "qualifications": qualification_receipts,
            "continuity": continuity_receipt,
            "adjudication": {
                "model_cells_present": has_model_adjudication, "human_cells": len(human_scope),
                "human_receipt": human_receipt,
            },
            "audit": audit_receipt,
            "raw_run_lineage": raw_lineage_receipt,
            "invariants": {
                "exact_organizer_ids": True, "all_cells_binary": True, "unresolved_cells": 0,
                "evidence_exact_and_at_most_500": True, "role_lineages_independent": True,
                "sealed_ledger_replayed": True, "raw_runs_replayed": raw_lineage_receipt is not None,
                "zero_defect_audit": True,
            },
        }
        return _write_outputs(output_dir=output_dir, organizer_order=organizer_order, rows=rows, manifest_base=manifest_base)
    except Exception:
        _invalidate_publishable_outputs(output_dir)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=pathlib.Path)
    parser.add_argument("--review-ledger", type=pathlib.Path)
    parser.add_argument("--review-export-manifest", type=pathlib.Path)
    parser.add_argument("--preflight-plan", type=pathlib.Path)
    parser.add_argument("--prepare-manifest", type=pathlib.Path)
    parser.add_argument("--candidate-qualification", type=pathlib.Path)
    parser.add_argument("--verifier-qualification", type=pathlib.Path)
    parser.add_argument("--adjudicator-qualification", type=pathlib.Path)
    parser.add_argument("--human-adjudicator-receipt", type=pathlib.Path)
    parser.add_argument("--continuity-plan", type=pathlib.Path)
    parser.add_argument("--candidate-continuity-epoch", type=pathlib.Path)
    parser.add_argument("--verifier-continuity-epoch", type=pathlib.Path)
    parser.add_argument("--adjudicator-continuity-epoch", type=pathlib.Path)
    parser.add_argument("--audit-plan", type=pathlib.Path)
    parser.add_argument("--audit-report", type=pathlib.Path)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--primary", type=pathlib.Path, help=argparse.SUPPRESS)
    parser.add_argument("--verifier", type=pathlib.Path, help=argparse.SUPPRESS)
    parser.add_argument("--adjudication", type=pathlib.Path, help=argparse.SUPPRESS)
    return parser


def _require_cli_paths(args: argparse.Namespace) -> BuildInputs:
    if args.primary is not None or args.verifier is not None or args.adjudication is not None:
        raise GoldBuildError(
            "legacy primary/verifier/adjudication JSONL assembly is permanently disabled; "
            "use a sealed review-ledger export"
        )
    required = (
        "input", "review_ledger", "review_export_manifest", "preflight_plan", "prepare_manifest", "candidate_qualification",
        "verifier_qualification", "audit_plan", "audit_report",
    )
    missing = [name.replace("_", "-") for name in required if getattr(args, name) is None]
    _require(not missing, f"missing required final-gold inputs: {missing}")
    return BuildInputs(
        organizer_input=args.input, review_ledger_path=args.review_ledger,
        review_export_manifest=args.review_export_manifest,
        preflight_plan=args.preflight_plan,
        prepare_manifest=args.prepare_manifest,
        candidate_qualification=args.candidate_qualification,
        verifier_qualification=args.verifier_qualification,
        adjudicator_qualification=args.adjudicator_qualification,
        human_adjudicator_receipt=args.human_adjudicator_receipt,
        continuity_plan=args.continuity_plan,
        candidate_continuity_epoch=args.candidate_continuity_epoch,
        verifier_continuity_epoch=args.verifier_continuity_epoch,
        adjudicator_continuity_epoch=args.adjudicator_continuity_epoch,
        audit_plan=args.audit_plan, audit_report=args.audit_report, output_dir=args.output_dir,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = args.output_dir.resolve()
    try:
        _invalidate_publishable_outputs(output_dir)
        manifest = build_gold(_require_cli_paths(args))
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (GoldBuildError, OSError, KeyError, TypeError, ValueError) as exc:
        try:
            _invalidate_publishable_outputs(output_dir)
            failure = {
                "schema_version": "dacon.independent.gold_build_failure.v2",
                "gold_written": False, "error_type": type(exc).__name__, "error": str(exc),
            }
            (output_dir / "build_failure.json").write_text(
                json.dumps(failure, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        print(f"Final gold build refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
