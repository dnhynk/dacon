"""Read-only proof that a sealed review ledger begins with the verified raw runs.

The review ledger's hash chain alone cannot prove that its initial model votes
came from the preserved run directories.  This verifier replays both complete
raw-run trees, regenerates the frozen preflight identity, and reconstructs each
initial ledger event from its raw candidate/verifier receipts.  Later human
revisions and the seal are allowed, but never substitute for an initial vote.
"""

from __future__ import annotations

import hashlib
import pathlib
from typing import Any, Iterator, Mapping

try:
    from tools.independent_gold import review_ledger as ledger
    from tools.independent_gold import review_ledger_workflow as workflow
except ModuleNotFoundError:  # Direct execution from this directory.
    import review_ledger as ledger  # type: ignore[no-redef]
    import review_ledger_workflow as workflow  # type: ignore[no-redef]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise workflow.ReviewWorkflowError(message)


def _bound_path(value: Any, *, context: str) -> pathlib.Path:
    _require(isinstance(value, str) and bool(value.strip()), f"{context}: path missing")
    path = pathlib.Path(value)
    _require(path.is_absolute(), f"{context}: path is not absolute")
    return path.resolve()


def _canonical_ledger_events(path: pathlib.Path) -> Iterator[dict[str, Any]]:
    """Stream the entire ledger, including its seal, without silently skipping lines."""

    with path.open("rb") as handle:
        for line_number, raw in enumerate(handle, 1):
            _require(raw.endswith(b"\n"), f"ledger line {line_number}: partial line")
            _require(bool(raw.strip()), f"ledger line {line_number}: blank line")
            event = workflow._strict_json_bytes(raw, f"ledger line {line_number}")
            _require(
                raw == (ledger.canonical_json(event) + "\n").encode("utf-8"),
                f"ledger line {line_number}: noncanonical event bytes",
            )
            yield event


def verify_sealed_raw_lineage(
    *,
    organizer_input: pathlib.Path,
    preflight_plan: pathlib.Path,
    prepare_manifest: pathlib.Path,
    ledger_path: pathlib.Path,
    expected_records: int = workflow.gold.EXPECTED_RECORDS,
) -> dict[str, Any]:
    """Reverify every raw artifact and initial event without changing any file.

    Run directories, qualifications and audit policy are extracted from the
    frozen preflight plan, then independently rediscovered and compared.  The
    returned hashes are a diagnostic summary, not a substitute for rerunning
    this function before accepting an export as gold.
    """

    organizer_input = organizer_input.resolve()
    preflight_plan = preflight_plan.resolve()
    prepare_manifest = prepare_manifest.resolve()
    ledger_path = ledger_path.resolve()
    order, records = workflow._strict_records(
        organizer_input, expected_records=expected_records
    )
    plan = workflow._load_json(preflight_plan, "raw-lineage preflight plan")
    _require(
        set(plan)
        == {
            "schema_version",
            "status",
            "frozen_at_utc",
            "identity",
            "prepare_identity_sha256",
            "ledger_id",
            "plan_sha256",
        },
        "raw-lineage preflight keys differ",
    )
    _require(plan["schema_version"] == workflow.PREFLIGHT_SCHEMA, "wrong preflight schema")
    _require(plan["status"] == "FROZEN", "preflight plan is not frozen")
    workflow._self_hash(plan, "plan_sha256", "raw-lineage preflight plan")
    identity = plan.get("identity")
    _require(isinstance(identity, Mapping), "preflight identity missing")
    input_descriptor = identity.get("organizer_input")
    _require(isinstance(input_descriptor, Mapping), "preflight organizer identity missing")
    _require(
        _bound_path(input_descriptor.get("path"), context="preflight organizer")
        == organizer_input,
        "preflight organizer path differs",
    )
    policy_descriptor = identity.get("audit_policy")
    _require(isinstance(policy_descriptor, Mapping), "preflight audit policy missing")
    policy_path = _bound_path(policy_descriptor.get("path"), context="preflight policy")
    policy, policy_time = workflow._validate_policy(policy_path, organizer_input)
    preflight_time = workflow._utc(plan.get("frozen_at_utc"), "preflight freeze")
    _require(preflight_time >= policy_time, "preflight predates frozen audit policy")

    role_descriptors = identity.get("roles")
    _require(
        isinstance(role_descriptors, Mapping)
        and set(role_descriptors) == {"candidate", "verifier"},
        "preflight role set differs",
    )
    qualification_paths: dict[str, pathlib.Path] = {}
    qualifications: dict[str, dict[str, Any]] = {}
    indexes: dict[str, workflow.RoleRunIndex] = {}
    for role in ("candidate", "verifier"):
        role_descriptor = role_descriptors[role]
        _require(isinstance(role_descriptor, Mapping), f"{role}: preflight role missing")
        qualification_descriptor = role_descriptor.get("qualification")
        _require(
            isinstance(qualification_descriptor, Mapping),
            f"{role}: qualification descriptor missing",
        )
        qualification_path = _bound_path(
            qualification_descriptor.get("path"), context=f"{role} qualification"
        )
        qualification_paths[role] = qualification_path
        qualifications[role] = workflow._load_qualification(qualification_path, role)
        runs = role_descriptor.get("runs")
        _require(isinstance(runs, list) and bool(runs), f"{role}: run descriptors missing")
        run_dirs: list[pathlib.Path] = []
        for index, run in enumerate(runs, 1):
            _require(isinstance(run, Mapping), f"{role}: run descriptor {index} missing")
            run_dirs.append(
                _bound_path(run.get("run_dir"), context=f"{role} run {index}")
            )
        indexes[role] = workflow._verify_role_runs(
            run_dirs,
            role=role,
            organizer_input=organizer_input,
            records=records,
            policy_frozen_utc=policy["frozen_at_utc"],
        )
        _require(
            set(indexes[role].locations) == set(records),
            f"{role}: raw receipts do not cover every organizer record",
        )
    try:
        workflow.gold._assert_role_independence(qualifications)  # type: ignore[attr-defined]
    except (ValueError, KeyError, TypeError) as exc:
        raise workflow.ReviewWorkflowError(
            f"candidate/verifier qualification independence failed: {exc}"
        ) from exc

    recomputed_identity = workflow._preflight_identity(
        organizer_input=organizer_input,
        ordered_ids=order,
        audit_policy=policy_path,
        policy=policy,
        qualifications={
            role: (qualification_paths[role], qualifications[role])
            for role in ("candidate", "verifier")
        },
        indexes=indexes,
    )
    _require(recomputed_identity == identity, "preflight identity differs from raw runs")
    identity_hash = ledger.sha256_object(recomputed_identity)
    _require(
        plan["prepare_identity_sha256"] == identity_hash,
        "preflight identity hash mismatch",
    )
    _require(
        plan["ledger_id"] == f"independent-gold-review-{identity_hash[:32]}",
        "preflight ledger id mismatch",
    )

    manifest = workflow._load_prepared_manifest(prepare_manifest)
    _require(
        _bound_path(manifest["preflight_plan_path"], context="prepare preflight")
        == preflight_plan,
        "prepare manifest uses another preflight path",
    )
    _require(
        manifest["preflight_plan_file_sha256"] == workflow.gold.file_sha256(preflight_plan)
        and manifest["preflight_plan_sha256"] == plan["plan_sha256"],
        "prepare manifest preflight commitment differs",
    )
    _require(
        manifest["organizer_input_sha256"] == workflow.gold.file_sha256(organizer_input),
        "prepare manifest organizer input differs",
    )
    _require(
        _bound_path(manifest["ledger_path"], context="prepare ledger") == ledger_path
        and manifest["ledger_id"] == plan["ledger_id"],
        "prepare manifest ledger binding differs",
    )
    _require(manifest["records"] == len(records), "prepare record count differs")
    _require(
        manifest["cells"] == len(records) * len(ledger.ITEMS),
        "prepare cell count differs",
    )
    prefix = workflow._verify_prepare_prefix(ledger_path, records, manifest)
    try:
        final_state = ledger.materialize_ledger(
            _canonical_ledger_events(ledger_path), records, require_sealed=True
        )
    except (OSError, ValueError) as exc:
        raise workflow.ReviewWorkflowError(f"sealed ledger replay failed: {exc}") from exc
    _require(final_state.ledger_id == plan["ledger_id"], "sealed ledger id differs")
    _require(set(final_state.active) == set(records), "sealed ledger scope differs")
    _require(
        final_state.resolved_cells == manifest["cells"],
        "sealed ledger does not resolve every cell",
    )
    queue_path = _bound_path(manifest["blind_queue_path"], context="prepare blind queue")
    _, queue = workflow._queue_index(queue_path, manifest=manifest, records=records)

    payload_digest = hashlib.sha256()
    expected_previous = ledger.ZERO_HASH
    consensus_cells = 0
    unresolved_cells = 0
    queued_records: set[str] = set()
    first_event_time = None
    last_event_time = None
    try:
        with ledger_path.open("rb") as handle:
            for ordinal, record_id in enumerate(order):
                raw = handle.readline()
                _require(bool(raw), f"initial ledger event missing for {record_id}")
                event = workflow._strict_json_bytes(raw, f"initial event {ordinal}")
                _require(
                    event.get("event_type") == "record_snapshot"
                    and event.get("payload", {}).get("source", {}).get("record_id") == record_id,
                    f"initial event {ordinal}: organizer order or type differs",
                )
                record = records[record_id]
                candidate_receipt = indexes["candidate"].load(record_id)
                verifier_receipt = indexes["verifier"].load(record_id)
                receipt_completions = [
                    workflow._utc(receipt.get("completed_utc"), f"{record_id}:{role} completion")
                    for role, receipt in (
                        ("candidate", candidate_receipt),
                        ("verifier", verifier_receipt),
                    )
                ]
                _require(
                    all(policy_time <= completed <= preflight_time for completed in receipt_completions),
                    f"{record_id}: raw receipt completed outside policy-to-preflight window",
                )
                try:
                    reviews = [
                        ledger.make_full_record_review_session(
                            record,
                            role=role,
                            receipt=receipt,
                            qualification_report=qualifications[role],
                        )
                        for role, receipt in (
                            ("candidate", candidate_receipt),
                            ("verifier", verifier_receipt),
                        )
                    ]
                    expected_snapshot = ledger.build_snapshot(
                        record, reviews, audit_tags=("blind-model-pair-v1",)
                    )
                    expected_event = ledger.make_event(
                        ledger_id=str(plan["ledger_id"]),
                        seq=ordinal,
                        prev_event_sha256=expected_previous,
                        event_type="record_snapshot",
                        payload=expected_snapshot,
                        occurred_utc=event["occurred_utc"],
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise workflow.ReviewWorkflowError(
                        f"{record_id}: cannot reconstruct initial raw-run event: {exc}"
                    ) from exc
                expected_raw = (ledger.canonical_json(expected_event) + "\n").encode("utf-8")
                _require(
                    raw == expected_raw,
                    f"{record_id}: initial event bytes differ from verified raw receipts",
                )
                expected_previous = expected_event["event_sha256"]
                payload_digest.update(
                    (ledger.canonical_json(expected_snapshot) + "\n").encode("utf-8")
                )
                event_time = workflow._utc(event["occurred_utc"], f"initial event {ordinal}")
                _require(
                    event_time >= preflight_time and event_time >= max(receipt_completions),
                    f"{record_id}: initial event predates preflight or raw receipt completion",
                )
                if last_event_time is not None:
                    _require(event_time >= last_event_time, "initial event time moved backwards")
                first_event_time = event_time if first_event_time is None else first_event_time
                last_event_time = event_time
                unresolved = [
                    item for item in ledger.ITEMS
                    if expected_snapshot["routing"][item]["status"] == "unresolved"
                ]
                consensus_cells += len(ledger.ITEMS) - len(unresolved)
                unresolved_cells += len(unresolved)
                if unresolved:
                    queued_records.add(record_id)
                    packet = queue.get(record_id)
                    _require(packet is not None, f"{record_id}: blind queue omits unresolved record")
                    _require(
                        packet == workflow._blind_packet(
                            record,
                            ledger_id=str(plan["ledger_id"]),
                            event=expected_event,
                            items=unresolved,
                        ),
                        f"{record_id}: blind queue differs from initial route",
                    )
    except OSError as exc:
        raise workflow.ReviewWorkflowError(f"cannot read initial ledger events: {exc}") from exc

    _require(expected_previous == prefix.head_sha256, "initial prefix head differs")
    _require(set(queue) == queued_records, "blind queue scope differs from initial routes")
    _require(
        consensus_cells == manifest["independent_consensus_cells"]
        and unresolved_cells == manifest["unresolved_cells"]
        and len(queued_records) == manifest["unresolved_records"],
        "prepare route counts differ from raw-run reconstruction",
    )
    _require(
        first_event_time is not None
        and first_event_time.isoformat() == manifest["first_event_utc"]
        and last_event_time is not None
        and last_event_time.isoformat() == manifest["last_event_utc"],
        "prepare first/last event times differ",
    )
    return {
        "schema_version": "dacon.independent.sealed_raw_lineage_verification.v1",
        "status": "PASS",
        "records": len(records),
        "cells": manifest["cells"],
        "preflight_plan_sha256": plan["plan_sha256"],
        "prepare_manifest_sha256": manifest["manifest_sha256"],
        "initial_event_count": prefix.event_count,
        "initial_prefix_head_sha256": prefix.head_sha256,
        "initial_snapshot_payloads_sha256": payload_digest.hexdigest(),
        "sealed_event_count": final_state.event_count,
        "sealed_ledger_head_sha256": final_state.head_sha256,
        "roles": {
            role: {
                "qualification_object_sha256": ledger.sha256_object(qualifications[role]),
                "run_descriptors_sha256": ledger.sha256_object(
                    [dict(value) for value in indexes[role].descriptors]
                ),
                "runs": len(indexes[role].descriptors),
            }
            for role in ("candidate", "verifier")
        },
    }


__all__ = ["verify_sealed_raw_lineage"]
