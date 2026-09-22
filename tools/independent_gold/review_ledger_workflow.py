"""Fail-closed workflow from two blind model passes to a sealed review ledger.

This module deliberately makes no model calls.  It consumes independently
qualified full-record run artifacts, freezes their exact byte lineage before
the first ledger event, creates a label-blind queue for only the cells that
need adjudication, embeds the raw human commitments in immutable revisions,
then seals and exports the ledger.  Every mutable phase is restartable by
replaying and comparing the expected snapshots rather than trusting IDs.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Protocol, Sequence

try:
    from tools.independent_gold import build_gold as gold
    from tools.independent_gold import gold_audit
    from tools.independent_gold import review_ledger as ledger
except ModuleNotFoundError:  # Direct execution from this directory.
    import build_gold as gold  # type: ignore[no-redef]
    import gold_audit  # type: ignore[no-redef]
    import review_ledger as ledger  # type: ignore[no-redef]


PREFLIGHT_SCHEMA = "dacon.independent.review_workflow_preflight.v1"
PREPARE_SCHEMA = "dacon.independent.review_workflow_prepare.v1"
BLIND_PACKET_SCHEMA = "dacon.independent.human_adjudication_packet.v1"
HUMAN_RECEIPT_SCHEMA = gold.HUMAN_RECEIPT_SCHEMA
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ReviewWorkflowError(ValueError):
    """A workflow artifact cannot honestly advance to the next phase."""


class RoleRunIndex(Protocol):
    """Small interface supplied by the strict full-run artifact verifier."""

    descriptors: Sequence[Mapping[str, Any]]
    locations: Mapping[str, Any]

    def load(self, record_id: str) -> dict[str, Any]: ...


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReviewWorkflowError(message)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewWorkflowError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_json_bytes(raw: bytes, context: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewWorkflowError(f"{context}: invalid UTF-8 JSON: {exc}") from exc
    _require(isinstance(value, dict), f"{context}: JSON object required")
    return value


def _load_json(path: pathlib.Path, context: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReviewWorkflowError(f"{context}: cannot read {path}: {exc}") from exc
    return _strict_json_bytes(raw, context)


def _read_jsonl_strict(path: pathlib.Path, context: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("rb") as handle:
            for line_number, raw in enumerate(handle, 1):
                _require(bool(raw.strip()), f"{context}:{line_number}: blank line forbidden")
                rows.append(_strict_json_bytes(raw, f"{context}:{line_number}"))
    except OSError as exc:
        raise ReviewWorkflowError(f"{context}: cannot read {path}: {exc}") from exc
    return rows


def _atomic_bytes(path: pathlib.Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    )
    temporary = pathlib.Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _require(not path.exists(), f"refusing to overwrite frozen artifact: {path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_json(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    _atomic_bytes(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )


def _atomic_jsonl(path: pathlib.Path, rows: Iterable[Mapping[str, Any]]) -> None:
    payload = b"".join(
        (ledger.canonical_json(dict(row)) + "\n").encode("utf-8") for row in rows
    )
    _atomic_bytes(path, payload)


def _self_hash(value: Mapping[str, Any], field: str, context: str) -> str:
    claimed = value.get(field)
    _require(isinstance(claimed, str) and HEX64.fullmatch(claimed), f"{context}: {field} invalid")
    expected = ledger.sha256_object({key: child for key, child in value.items() if key != field})
    _require(claimed == expected, f"{context}: {field} mismatch")
    return claimed


def _utc(value: Any, context: str) -> datetime:
    _require(isinstance(value, str) and bool(value), f"{context}: UTC timestamp required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewWorkflowError(f"{context}: invalid ISO-8601 timestamp") from exc
    _require(parsed.tzinfo is not None, f"{context}: timezone required")
    _require(parsed.utcoffset() == timezone.utc.utcoffset(parsed), f"{context}: must be UTC")
    return parsed


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identity_token(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value)).strip().casefold()


def _strict_records(
    path: pathlib.Path, *, expected_records: int
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    order: list[str] = []
    records: dict[str, dict[str, Any]] = {}
    try:
        with gzip.open(path, "rb") as handle:
            for line_number, raw in enumerate(handle, 1):
                _require(bool(raw.strip()), f"organizer input:{line_number}: blank line")
                row = _strict_json_bytes(raw, f"organizer input:{line_number}")
                record_id = str(row.get("id") or "")
                _require(bool(record_id), f"organizer input:{line_number}: missing id")
                _require(record_id not in records, f"organizer input:{line_number}: duplicate {record_id}")
                ledger.source_descriptor(row)
                records[record_id] = row
                order.append(record_id)
    except OSError as exc:
        raise ReviewWorkflowError(f"organizer input cannot be read: {exc}") from exc
    _require(
        len(records) == expected_records,
        f"organizer input must have exactly {expected_records} records, found {len(records)}",
    )
    return order, records


def _validate_policy(
    path: pathlib.Path, organizer_input: pathlib.Path
) -> tuple[dict[str, Any], datetime]:
    try:
        policy = gold_audit._load_policy(path)  # type: ignore[attr-defined]
    except (ValueError, OSError) as exc:
        raise ReviewWorkflowError(f"invalid frozen audit policy: {exc}") from exc
    _require(
        policy["organizer_input_sha256"] == gold.file_sha256(organizer_input),
        "audit policy is bound to another organizer input",
    )
    return policy, _utc(policy["frozen_at_utc"], "audit policy freeze")


def _load_qualification(path: pathlib.Path, role: str) -> dict[str, Any]:
    try:
        return gold._validate_qualification(path.resolve(), role)  # type: ignore[attr-defined]
    except (OSError, ValueError) as exc:
        raise ReviewWorkflowError(f"{role} qualification failed replay: {exc}") from exc


def _verify_role_runs(
    run_dirs: Sequence[pathlib.Path],
    *,
    role: str,
    organizer_input: pathlib.Path,
    records: Mapping[str, Mapping[str, Any]],
    policy_frozen_utc: str,
) -> RoleRunIndex:
    try:
        from tools.independent_gold import full_run_receipt_verify
    except ModuleNotFoundError:
        import full_run_receipt_verify  # type: ignore[no-redef]
    try:
        return full_run_receipt_verify.verify_role_runs(
            run_dirs,
            role=role,
            organizer_input=organizer_input,
            records=records,
            policy_frozen_utc=policy_frozen_utc,
        )
    except (OSError, ValueError) as exc:
        raise ReviewWorkflowError(f"{role} full-run verification failed: {exc}") from exc


def _qualification_descriptor(
    path: pathlib.Path, report: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "file_sha256": gold.file_sha256(path.resolve()),
        "object_sha256": ledger.sha256_object(report),
        "qualified_tuple_sha256": report["qualified_tuple_sha256"],
        "prompt_lineage_sha256": report["identity"]["prompt_lineage_sha256"],
        "model_family": report["identity"]["model_identity"]["family"],
    }


def _preflight_identity(
    *,
    organizer_input: pathlib.Path,
    ordered_ids: Sequence[str],
    audit_policy: pathlib.Path,
    policy: Mapping[str, Any],
    qualifications: Mapping[str, tuple[pathlib.Path, Mapping[str, Any]]],
    indexes: Mapping[str, RoleRunIndex],
) -> dict[str, Any]:
    return {
        "organizer_input": {
            "path": str(organizer_input.resolve()),
            "sha256": gold.file_sha256(organizer_input.resolve()),
            "records": len(ordered_ids),
            "ordered_ids_sha256": ledger.sha256_object(list(ordered_ids)),
        },
        "audit_policy": {
            "path": str(audit_policy.resolve()),
            "file_sha256": gold.file_sha256(audit_policy.resolve()),
            "policy_sha256": policy["policy_sha256"],
            "frozen_at_utc": policy["frozen_at_utc"],
        },
        "roles": {
            role: {
                "qualification": _qualification_descriptor(path, report),
                "runs": [dict(value) for value in indexes[role].descriptors],
                "records": len(indexes[role].locations),
            }
            for role, (path, report) in sorted(qualifications.items())
        },
        "annotation_topology": "two_mutually_blind_full_record_24_passes",
        "production_runtime_or_outputs_used": False,
    }


def _freeze_or_verify_preflight(
    path: pathlib.Path, identity: Mapping[str, Any]
) -> dict[str, Any]:
    identity_hash = ledger.sha256_object(identity)
    expected_ledger_id = f"independent-gold-review-{identity_hash[:32]}"
    if path.exists():
        plan = _load_json(path, "review workflow preflight")
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
            "preflight plan keys differ",
        )
        _self_hash(plan, "plan_sha256", "preflight plan")
        _require(plan["status"] == "FROZEN", "preflight plan is not frozen")
        _require(plan["identity"] == identity, "preflight inputs changed after freeze")
        _require(plan["prepare_identity_sha256"] == identity_hash, "preflight identity hash mismatch")
        _require(plan["ledger_id"] == expected_ledger_id, "preflight ledger id mismatch")
        return plan
    plan: dict[str, Any] = {
        "schema_version": PREFLIGHT_SCHEMA,
        "status": "FROZEN",
        "frozen_at_utc": _now(),
        "identity": dict(identity),
        "prepare_identity_sha256": identity_hash,
        "ledger_id": expected_ledger_id,
    }
    plan["plan_sha256"] = ledger.sha256_object(plan)
    _atomic_json(path, plan)
    return plan


def _active_event_offsets(
    path: pathlib.Path, state: ledger.LedgerState
) -> dict[str, int]:
    wanted = {entry.event_id: record_id for record_id, entry in state.active.items()}
    offsets: dict[str, int] = {}
    with path.open("rb") as handle:
        line_number = 0
        while True:
            offset = handle.tell()
            raw = handle.readline()
            if not raw:
                break
            line_number += 1
            _require(bool(raw.strip()), f"review ledger:{line_number}: blank line")
            event = _strict_json_bytes(raw, f"review ledger:{line_number}")
            record_id = wanted.get(str(event.get("event_id") or ""))
            if record_id is not None:
                _require(record_id not in offsets, f"duplicate active ledger event for {record_id}")
                offsets[record_id] = offset
    _require(set(offsets) == set(state.active), "active ledger snapshot offsets are incomplete")
    return offsets


def _event_at(path: pathlib.Path, offset: int, context: str) -> dict[str, Any]:
    with path.open("rb") as handle:
        handle.seek(offset)
        raw = handle.readline()
    _require(bool(raw), f"{context}: event offset is beyond EOF")
    return _strict_json_bytes(raw, context)


def _validate_event_times(path: pathlib.Path, *, floor: datetime) -> tuple[str, str]:
    previous: datetime | None = None
    first: datetime | None = None
    last: datetime | None = None
    for index, event in enumerate(ledger.iter_events(path)):
        current = _utc(event.get("occurred_utc"), f"review event {index}")
        _require(current >= floor, f"review event {index} predates frozen audit policy")
        if previous is not None:
            _require(current >= previous, f"review event {index} timestamp moved backwards")
        first = current if first is None else first
        last = current
        previous = current
    _require(first is not None and last is not None, "review ledger has no events")
    return first.isoformat(), last.isoformat()


def _blind_packet(
    record: Mapping[str, Any],
    *,
    ledger_id: str,
    event: Mapping[str, Any],
    items: Sequence[str],
) -> dict[str, Any]:
    core: dict[str, Any] = {
        "schema_version": BLIND_PACKET_SCHEMA,
        "ledger_id": ledger_id,
        "record_id": str(record["id"]),
        "source_sha256": ledger.source_descriptor(record)["source_sha256"],
        "initial_snapshot_event_id": event["event_id"],
        "initial_snapshot_event_sha256": event["event_sha256"],
        "items": list(items),
        "source_record": dict(record),
        "annotation_answer_visible": False,
        "peer_votes_visible": False,
        "routing_visible": False,
        "model_rationales_visible": False,
    }
    core["packet_sha256"] = ledger.sha256_object(core)
    return core


def _validate_blind_packet(
    packet: Mapping[str, Any], record: Mapping[str, Any], *, ledger_id: str
) -> None:
    expected_keys = {
        "schema_version",
        "ledger_id",
        "record_id",
        "source_sha256",
        "initial_snapshot_event_id",
        "initial_snapshot_event_sha256",
        "items",
        "source_record",
        "annotation_answer_visible",
        "peer_votes_visible",
        "routing_visible",
        "model_rationales_visible",
        "packet_sha256",
    }
    _require(set(packet) == expected_keys, "blind adjudication packet keys differ")
    _require(packet["schema_version"] == BLIND_PACKET_SCHEMA, "wrong blind packet schema")
    _require(packet["ledger_id"] == ledger_id, "blind packet uses another ledger")
    _require(packet["record_id"] == record["id"], "blind packet record mismatch")
    _require(packet["source_record"] == record, "blind packet source differs")
    _require(
        packet["source_sha256"] == ledger.source_descriptor(record)["source_sha256"],
        "blind packet source hash mismatch",
    )
    items = packet.get("items")
    _require(
        isinstance(items, list)
        and bool(items)
        and items == sorted(set(items), key=lambda item: int(item[1:]))
        and all(item in ledger.ITEMS for item in items),
        "blind packet items are invalid",
    )
    for key in (
        "annotation_answer_visible",
        "peer_votes_visible",
        "routing_visible",
        "model_rationales_visible",
    ):
        _require(packet[key] is False, f"blind packet leaks {key}")
    _self_hash(packet, "packet_sha256", "blind packet")


def _load_prepared_manifest(path: pathlib.Path) -> dict[str, Any]:
    manifest = _load_json(path, "review workflow prepare manifest")
    expected = {
        "schema_version",
        "status",
        "preflight_plan_path",
        "preflight_plan_file_sha256",
        "preflight_plan_sha256",
        "organizer_input_sha256",
        "ledger_path",
        "ledger_id",
        "preadjudication_event_count",
        "preadjudication_ledger_head_sha256",
        "preadjudication_active_state_sha256",
        "records",
        "cells",
        "independent_consensus_cells",
        "unresolved_records",
        "unresolved_cells",
        "blind_queue_path",
        "blind_queue_sha256",
        "blind_queue_packets",
        "first_event_utc",
        "last_event_utc",
        "prepared_utc",
        "manifest_sha256",
    }
    _require(set(manifest) == expected, "prepare manifest keys differ")
    _require(manifest["schema_version"] == PREPARE_SCHEMA, "wrong prepare manifest schema")
    _require(manifest["status"] == "PREPARED", "prepare manifest is not complete")
    _self_hash(manifest, "manifest_sha256", "prepare manifest")
    return manifest


def prepare_model_passes(
    *,
    organizer_input: pathlib.Path,
    audit_policy: pathlib.Path,
    candidate_run_dirs: Sequence[pathlib.Path],
    verifier_run_dirs: Sequence[pathlib.Path],
    candidate_qualification: pathlib.Path,
    verifier_qualification: pathlib.Path,
    preflight_plan: pathlib.Path,
    ledger_path: pathlib.Path,
    blind_queue: pathlib.Path,
    prepare_manifest: pathlib.Path,
    expected_records: int = gold.EXPECTED_RECORDS,
    _indexes: Mapping[str, RoleRunIndex] | None = None,
    _qualification_reports: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Freeze inputs, append exact model snapshots, and emit the blind queue."""

    organizer_input = organizer_input.resolve()
    order, records = _strict_records(organizer_input, expected_records=expected_records)
    policy, policy_time = _validate_policy(audit_policy.resolve(), organizer_input)
    qualification_paths = {
        "candidate": candidate_qualification.resolve(),
        "verifier": verifier_qualification.resolve(),
    }
    if _qualification_reports is None:
        qualifications = {
            role: _load_qualification(path, role)
            for role, path in qualification_paths.items()
        }
    else:
        qualifications = {
            role: dict(_qualification_reports[role])
            for role in ("candidate", "verifier")
        }
    try:
        gold._assert_role_independence(qualifications)  # type: ignore[attr-defined]
    except (ValueError, KeyError, TypeError) as exc:
        raise ReviewWorkflowError(f"candidate/verifier qualification independence failed: {exc}") from exc

    if _indexes is None:
        indexes: Mapping[str, RoleRunIndex] = {
            "candidate": _verify_role_runs(
                candidate_run_dirs,
                role="candidate",
                organizer_input=organizer_input,
                records=records,
                policy_frozen_utc=policy["frozen_at_utc"],
            ),
            "verifier": _verify_role_runs(
                verifier_run_dirs,
                role="verifier",
                organizer_input=organizer_input,
                records=records,
                policy_frozen_utc=policy["frozen_at_utc"],
            ),
        }
    else:
        indexes = _indexes
    for role in ("candidate", "verifier"):
        _require(set(indexes[role].locations) == set(records), f"{role} receipts do not exactly cover source IDs")

    identity = _preflight_identity(
        organizer_input=organizer_input,
        ordered_ids=order,
        audit_policy=audit_policy.resolve(),
        policy=policy,
        qualifications={
            role: (qualification_paths[role], qualifications[role])
            for role in ("candidate", "verifier")
        },
        indexes=indexes,
    )
    plan = _freeze_or_verify_preflight(preflight_plan.resolve(), identity)
    _require(
        _utc(plan["frozen_at_utc"], "preflight freeze") >= policy_time,
        "review preflight was frozen before its audit policy",
    )

    if prepare_manifest.exists() or blind_queue.exists():
        _require(
            prepare_manifest.exists() and blind_queue.exists(),
            "partial frozen prepare outputs exist; start a new output epoch",
        )
        existing = _load_prepared_manifest(prepare_manifest)
        _require(
            existing["preflight_plan_file_sha256"] == gold.file_sha256(preflight_plan),
            "existing prepare manifest uses another preflight plan",
        )
        _require(
            existing["blind_queue_sha256"] == gold.file_sha256(blind_queue),
            "existing blind queue hash mismatch",
        )
        _require(
            existing["ledger_id"] == plan["ledger_id"]
            and existing["organizer_input_sha256"] == gold.file_sha256(organizer_input),
            "existing prepare manifest uses another ledger or source",
        )
        prefix = _verify_prepare_prefix(ledger_path.resolve(), records, existing)
        full = ledger.materialize_ledger(
            ledger.iter_events(ledger_path.resolve()), records, require_sealed=False
        )
        _require(
            not full.sealed
            and full.event_count == prefix.event_count
            and full.head_sha256 == prefix.head_sha256,
            "existing prepare ledger advanced beyond its frozen blind queue",
        )
        _, queue = _queue_index(
            blind_queue.resolve(), manifest=existing, records=records
        )
        active_events, active_snapshots = _load_active_snapshots(ledger_path.resolve(), prefix)
        expected_queue_records: set[str] = set()
        for record_id in order:
            unresolved = [
                item
                for item in ledger.ITEMS
                if active_snapshots[record_id]["routing"][item]["status"] == "unresolved"
            ]
            if unresolved:
                expected_queue_records.add(record_id)
                packet = queue.get(record_id)
                _require(packet is not None, f"{record_id}: unresolved cells absent from blind queue")
                expected_packet = _blind_packet(
                    records[record_id],
                    ledger_id=str(plan["ledger_id"]),
                    event=active_events[record_id],
                    items=unresolved,
                )
                _require(packet == expected_packet, f"{record_id}: blind queue differs from ledger route")
        _require(set(queue) == expected_queue_records, "blind queue contains unneeded records")
        return existing

    create = not ledger_path.exists() or ledger_path.stat().st_size == 0
    try:
        writer = ledger.LedgerWriter(
            ledger_path,
            str(plan["ledger_id"]),
            create=create,
            records=None if create else records,
        )
    except (OSError, ValueError) as exc:
        raise ReviewWorkflowError(f"cannot create/resume review ledger: {exc}") from exc
    _require(
        writer.state.event_count == len(writer.state.active),
        "prepare resume found revisions or non-initial events in the ledger",
    )
    active_offsets = (
        {} if create else _active_event_offsets(ledger_path, writer.state)
    )

    packets: list[dict[str, Any]] = []
    unresolved_cells = 0
    consensus_cells = 0
    for ordinal, record_id in enumerate(order, 1):
        record = records[record_id]
        candidate_receipt = indexes["candidate"].load(record_id)
        verifier_receipt = indexes["verifier"].load(record_id)
        for role, receipt in (
            ("candidate", candidate_receipt),
            ("verifier", verifier_receipt),
        ):
            completed = _utc(receipt.get("completed_utc"), f"{record_id}:{role} receipt completion")
            _require(completed >= policy_time, f"{record_id}:{role} receipt predates audit policy")
        try:
            candidate_review = ledger.make_full_record_review_session(
                record,
                role="candidate",
                receipt=candidate_receipt,
                qualification_report=qualifications["candidate"],
            )
            verifier_review = ledger.make_full_record_review_session(
                record,
                role="verifier",
                receipt=verifier_receipt,
                qualification_report=qualifications["verifier"],
            )
            snapshot = ledger.build_snapshot(
                record,
                [candidate_review, verifier_review],
                audit_tags=("blind-model-pair-v1",),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ReviewWorkflowError(f"{record_id}: cannot adapt blind model receipts: {exc}") from exc

        unresolved: list[str] = []
        for item in ledger.ITEMS:
            route = snapshot["routing"][item]
            if route["status"] == "resolved":
                _require(
                    route["route"] == "independent_consensus",
                    f"{record_id}:{item}: initial model pass has a non-consensus resolution",
                )
                consensus_cells += 1
            else:
                _require(
                    route["route"] == "adjudication_required",
                    f"{record_id}:{item}: non-adjudicatable model-pass defect {route['trigger_codes']}",
                )
                unresolved.append(item)
                unresolved_cells += 1

        active = writer.state.active.get(record_id)
        if active is None:
            event = writer.append_snapshot(snapshot, record)
        else:
            event = _event_at(
                ledger_path,
                active_offsets[record_id],
                f"active initial snapshot {record_id}",
            )
            _require(
                event.get("payload") == snapshot,
                f"{record_id}: resumed snapshot differs from frozen receipts",
            )
        if unresolved:
            packets.append(
                _blind_packet(
                    record,
                    ledger_id=str(plan["ledger_id"]),
                    event=event,
                    items=unresolved,
                )
            )
        if ordinal % 1000 == 0:
            print(
                json.dumps(
                    {
                        "phase": "prepare_model_passes",
                        "records": ordinal,
                        "unresolved_cells": unresolved_cells,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    _require(set(writer.state.active) == set(records), "initial ledger omits organizer records")
    _require(writer.state.event_count == len(records), "initial ledger must have exactly one event per record")
    first_event, last_event = _validate_event_times(ledger_path, floor=policy_time)
    for packet in packets:
        _validate_blind_packet(packet, records[str(packet["record_id"])], ledger_id=str(plan["ledger_id"]))
    _atomic_jsonl(blind_queue.resolve(), packets)
    manifest: dict[str, Any] = {
        "schema_version": PREPARE_SCHEMA,
        "status": "PREPARED",
        "preflight_plan_path": str(preflight_plan.resolve()),
        "preflight_plan_file_sha256": gold.file_sha256(preflight_plan.resolve()),
        "preflight_plan_sha256": plan["plan_sha256"],
        "organizer_input_sha256": gold.file_sha256(organizer_input),
        "ledger_path": str(ledger_path.resolve()),
        "ledger_id": plan["ledger_id"],
        "preadjudication_event_count": writer.state.event_count,
        "preadjudication_ledger_head_sha256": writer.state.head_sha256,
        "preadjudication_active_state_sha256": ledger.sha256_object(
            ledger._state_rows(writer.state)  # type: ignore[attr-defined]
        ),
        "records": len(records),
        "cells": len(records) * len(ledger.ITEMS),
        "independent_consensus_cells": consensus_cells,
        "unresolved_records": len(packets),
        "unresolved_cells": unresolved_cells,
        "blind_queue_path": str(blind_queue.resolve()),
        "blind_queue_sha256": gold.file_sha256(blind_queue.resolve()),
        "blind_queue_packets": len(packets),
        "first_event_utc": first_event,
        "last_event_utc": last_event,
        "prepared_utc": _now(),
    }
    _require(
        consensus_cells + unresolved_cells == manifest["cells"],
        "prepare route counts do not cover all cells",
    )
    manifest["manifest_sha256"] = ledger.sha256_object(manifest)
    _atomic_json(prepare_manifest.resolve(), manifest)
    return manifest


def _load_roster(path: pathlib.Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewWorkflowError(f"human roster cannot be read: {exc}") from exc
    _require(isinstance(raw, list) and bool(raw), "human roster must be a non-empty JSON list")
    normalized: list[dict[str, Any]] = []
    for index, value in enumerate(raw, 1):
        _require(isinstance(value, Mapping), f"human roster row {index} must be an object")
        row = dict(value)
        attestation = gold._resolve_bound_path(  # type: ignore[attr-defined]
            row.get("attestation_path"),
            manifest_path=path,
            context=f"human roster row {index}",
        )
        row["attestation_path"] = str(attestation.resolve())
        normalized.append(row)
    try:
        validated = gold._validate_human_roster(  # type: ignore[attr-defined]
            normalized, manifest_path=path, context="human adjudicators"
        )
    except (OSError, ValueError) as exc:
        raise ReviewWorkflowError(f"human roster validation failed: {exc}") from exc
    return [validated[key] for key in sorted(validated)]


def _verify_prepare_prefix(
    ledger_path: pathlib.Path,
    records: Mapping[str, Mapping[str, Any]],
    manifest: Mapping[str, Any],
) -> ledger.LedgerState:
    count = manifest["preadjudication_event_count"]
    _require(type(count) is int and count == len(records), "invalid pre-adjudication event count")

    def prefix() -> Iterable[dict[str, Any]]:
        seen = 0
        for event in ledger.iter_events(ledger_path):
            if seen >= count:
                break
            seen += 1
            yield event
        _require(seen == count, "ledger was truncated before the frozen pre-adjudication head")

    try:
        state = ledger.materialize_ledger(prefix(), records, require_sealed=False)
    except (OSError, ValueError) as exc:
        raise ReviewWorkflowError(f"pre-adjudication ledger replay failed: {exc}") from exc
    _require(state.event_count == count, "pre-adjudication event count mismatch")
    _require(
        state.head_sha256 == manifest["preadjudication_ledger_head_sha256"],
        "pre-adjudication ledger head changed",
    )
    _require(
        ledger.sha256_object(ledger._state_rows(state))  # type: ignore[attr-defined]
        == manifest["preadjudication_active_state_sha256"],
        "pre-adjudication active state changed",
    )
    _require(set(state.active) == set(records), "pre-adjudication ledger scope changed")
    return state


def _queue_index(
    path: pathlib.Path,
    *,
    manifest: Mapping[str, Any],
    records: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    _require(gold.file_sha256(path) == manifest["blind_queue_sha256"], "blind queue hash mismatch")
    packets = _read_jsonl_strict(path, "blind adjudication queue")
    _require(len(packets) == manifest["blind_queue_packets"], "blind queue packet count mismatch")
    indexed: dict[str, dict[str, Any]] = {}
    cells = 0
    for packet in packets:
        record_id = str(packet.get("record_id") or "")
        _require(record_id in records and record_id not in indexed, "blind queue has duplicate/unknown record")
        _validate_blind_packet(packet, records[record_id], ledger_id=str(manifest["ledger_id"]))
        indexed[record_id] = packet
        cells += len(packet["items"])
    _require(cells == manifest["unresolved_cells"], "blind queue cell count mismatch")
    _require(len(indexed) == manifest["unresolved_records"], "blind queue record count mismatch")
    return packets, indexed


def _judgment_index(
    path: pathlib.Path,
    *,
    packets: Mapping[str, Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    roster: Mapping[str, Mapping[str, Any]],
    not_before: datetime,
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]], datetime]:
    rows = _read_jsonl_strict(path, "human adjudication judgments")
    expected = {
        (record_id, item)
        for record_id, packet in packets.items()
        for item in packet["items"]
    }
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    latest = not_before
    for line_number, row in enumerate(rows, 1):
        record_id = str(row.get("record_id") or "")
        item = str(row.get("item") or "")
        key = (record_id, item)
        _require(key in expected, f"human judgment line {line_number}: extra/unknown cell {key}")
        _require(key not in indexed, f"human judgment line {line_number}: duplicate cell {key}")
        packet = packets[record_id]
        _require(row.get("packet_sha256") == packet["packet_sha256"], f"{record_id}:{item}: packet hash mismatch")
        _require(
            row.get("initial_snapshot_event_id") == packet["initial_snapshot_event_id"]
            and row.get("initial_snapshot_event_sha256")
            == packet["initial_snapshot_event_sha256"],
            f"{record_id}:{item}: initial snapshot binding mismatch",
        )
        reviewer_id = str(row.get("reviewer_id") or "")
        _require(reviewer_id in roster, f"{record_id}:{item}: reviewer is absent from roster")
        try:
            ledger._validate_human_judgment(  # type: ignore[attr-defined]
                records[record_id], row, context=f"{record_id}:{item}"
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ReviewWorkflowError(f"{record_id}:{item}: invalid human judgment: {exc}") from exc
        reviewed = _utc(row["reviewed_utc"], f"{record_id}:{item}: review time")
        _require(reviewed >= not_before, f"{record_id}:{item}: judgment predates blind queue freeze")
        latest = max(latest, reviewed)
        indexed[key] = row
    missing = sorted(expected - set(indexed))
    _require(not missing, f"human judgments omit {len(missing)} queued cells; first={missing[:5]}")
    return rows, indexed, latest


def _reviewer_from_roster(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "reviewer_id": row["reviewer_id"],
        "kind": "human",
        "independence_key": row["independence_key"],
        "method": "blind source-and-law adjudication",
        "method_version": row["protocol_version"],
        "review_protocol_version": row["protocol_version"],
    }


def _model_identity_sets(
    initial_snapshots: Mapping[str, Mapping[str, Any]]
) -> tuple[set[str], set[str]]:
    reviewer_ids: set[str] = set()
    independence_keys: set[str] = set()
    for snapshot in initial_snapshots.values():
        for review in snapshot["reviews"]:
            if review["role"] not in ("candidate", "verifier"):
                continue
            reviewer_ids.add(_identity_token(review["reviewer"]["reviewer_id"]))
            independence_keys.add(_identity_token(review["reviewer"]["independence_key"]))
    return reviewer_ids, independence_keys


def _load_active_snapshots(
    path: pathlib.Path, state: ledger.LedgerState
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    offsets = _active_event_offsets(path, state)
    events: dict[str, dict[str, Any]] = {}
    snapshots: dict[str, dict[str, Any]] = {}
    for record_id, offset in offsets.items():
        event = _event_at(path, offset, f"active snapshot {record_id}")
        events[record_id] = event
        snapshots[record_id] = event["payload"]
    return events, snapshots


def _replace_json(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    )
    temporary = pathlib.Path(handle.name)
    try:
        with handle:
            handle.write(
                (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                    "utf-8"
                )
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _publish_export(
    *,
    ledger_path: pathlib.Path,
    records: Mapping[str, Mapping[str, Any]],
    output_dir: pathlib.Path,
    contract: gold.BuildContract,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest_path = output_dir / "review_export_manifest.json"
    if output_dir.exists():
        _require(manifest_path.is_file(), "partial review export directory exists; use a new output epoch")
        try:
            manifest, rows, _ = gold._verify_review_export(  # type: ignore[attr-defined]
                ledger_path=ledger_path,
                manifest_path=manifest_path,
                records=records,
                contract=contract,
            )
        except (OSError, ValueError) as exc:
            raise ReviewWorkflowError(f"existing review export is invalid: {exc}") from exc
        return manifest, rows

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = pathlib.Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", suffix=".tmp", dir=output_dir.parent)
    )
    published = False
    try:
        ledger.export_build_gold_inputs(ledger_path, records, stage)
        os.replace(stage, output_dir)
        published = True
        manifest_path = output_dir / "review_export_manifest.json"
        manifest = _load_json(manifest_path, "review export manifest")
        manifest["paths"] = {
            name: str((output_dir / pathlib.Path(raw).name).resolve())
            for name, raw in manifest["paths"].items()
        }
        _replace_json(manifest_path, manifest)
        checked, rows, _ = gold._verify_review_export(  # type: ignore[attr-defined]
            ledger_path=ledger_path,
            manifest_path=manifest_path,
            records=records,
            contract=contract,
        )
        return checked, rows
    except (OSError, ValueError) as exc:
        raise ReviewWorkflowError(f"review export publication failed: {exc}") from exc
    finally:
        if not published and stage.exists():
            shutil.rmtree(stage)


def _write_or_verify_human_receipt(
    *,
    output: pathlib.Path,
    export_manifest_path: pathlib.Path,
    export_manifest: Mapping[str, Any],
    human_scope: Sequence[Mapping[str, Any]],
    roster: Sequence[Mapping[str, Any]],
    completed_utc: str,
) -> dict[str, Any]:
    scope = sorted((dict(value) for value in human_scope), key=lambda row: (row["id"], row["item"]))
    receipt: dict[str, Any] = {
        "schema_version": HUMAN_RECEIPT_SCHEMA,
        "status": "PASS",
        "review_export_manifest_sha256": gold.file_sha256(export_manifest_path),
        "review_ledger_head_sha256": export_manifest["ledger_head_sha256"],
        "adjudication_scope_sha256": ledger.sha256_object(scope),
        "adjudicated_cells": len(scope),
        "completed_utc": completed_utc,
        "reviewer_roster": [dict(value) for value in roster],
    }
    receipt["receipt_sha256"] = ledger.sha256_object(receipt)
    if output.exists():
        existing = _load_json(output, "human adjudicator receipt")
        _require(existing == receipt, "existing human receipt differs from sealed scope")
        return existing
    _atomic_json(output, receipt)
    return receipt


def finalize_human_adjudication(
    *,
    organizer_input: pathlib.Path,
    ledger_path: pathlib.Path,
    preflight_plan: pathlib.Path,
    prepare_manifest: pathlib.Path,
    blind_queue: pathlib.Path,
    judgments_path: pathlib.Path | None,
    roster_path: pathlib.Path | None,
    export_output_dir: pathlib.Path,
    human_receipt_output: pathlib.Path | None,
    expected_records: int = gold.EXPECTED_RECORDS,
    _qualification_reports: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply exact blind judgments, seal the ledger, export, and issue receipt."""

    organizer_input = organizer_input.resolve()
    order, records = _strict_records(organizer_input, expected_records=expected_records)
    plan = _load_json(preflight_plan.resolve(), "review workflow preflight")
    _self_hash(plan, "plan_sha256", "review workflow preflight")
    manifest = _load_prepared_manifest(prepare_manifest.resolve())
    _require(manifest["preflight_plan_file_sha256"] == gold.file_sha256(preflight_plan.resolve()), "prepare manifest preflight hash mismatch")
    _require(manifest["preflight_plan_sha256"] == plan["plan_sha256"], "prepare manifest uses another preflight")
    _require(manifest["organizer_input_sha256"] == gold.file_sha256(organizer_input), "prepare manifest input mismatch")
    _require(pathlib.Path(manifest["ledger_path"]).resolve() == ledger_path.resolve(), "prepare manifest ledger path mismatch")
    _require(manifest["ledger_id"] == plan["ledger_id"], "prepare/preflight ledger id mismatch")
    _require(pathlib.Path(manifest["blind_queue_path"]).resolve() == blind_queue.resolve(), "prepare manifest queue path mismatch")
    prefix_state = _verify_prepare_prefix(ledger_path.resolve(), records, manifest)
    _, packets = _queue_index(
        blind_queue.resolve(), manifest=manifest, records=records
    )

    qualifications: dict[str, dict[str, Any]] = {}
    for role in ("candidate", "verifier"):
        descriptor = plan["identity"]["roles"][role]["qualification"]
        path = pathlib.Path(descriptor["path"])
        _require(gold.file_sha256(path) == descriptor["file_sha256"], f"{role} qualification file changed")
        report = (
            _load_qualification(path, role)
            if _qualification_reports is None
            else dict(_qualification_reports[role])
        )
        _require(ledger.sha256_object(report) == descriptor["object_sha256"], f"{role} qualification object changed")
        qualifications[role] = report
    try:
        gold._assert_role_independence(qualifications)  # type: ignore[attr-defined]
    except (ValueError, KeyError, TypeError) as exc:
        raise ReviewWorkflowError(f"qualification independence replay failed: {exc}") from exc

    initial_events, initial_snapshots = _load_active_snapshots(ledger_path, prefix_state)
    roster_rows: list[dict[str, Any]] = []
    roster: dict[str, dict[str, Any]] = {}
    judgment_rows: list[dict[str, Any]] = []
    judgments: dict[tuple[str, str], dict[str, Any]] = {}
    latest_judgment = _utc(manifest["prepared_utc"], "prepare completion")
    judgments_sha: str | None = None
    if packets:
        _require(judgments_path is not None and judgments_path.is_file(), "queued cells require human judgments")
        _require(roster_path is not None and roster_path.is_file(), "queued cells require human roster")
        _require(human_receipt_output is not None, "human adjudication requires a receipt output")
        roster_rows = _load_roster(roster_path.resolve())
        roster = {str(value["reviewer_id"]): value for value in roster_rows}
        judgment_rows, judgments, latest_judgment = _judgment_index(
            judgments_path.resolve(),
            packets=packets,
            records=records,
            roster=roster,
            not_before=latest_judgment,
        )
        judgments_sha = gold.file_sha256(judgments_path.resolve())
        used_reviewers = {str(value["reviewer_id"]) for value in judgment_rows}
        _require(set(roster) == used_reviewers, "human roster contains unused or missing deciding reviewers")
        model_ids, model_keys = _model_identity_sets(initial_snapshots)
        _require(
            not ({_identity_token(value) for value in used_reviewers} & model_ids),
            "human reviewer reuses a candidate/verifier identity",
        )
        _require(
            not ({_identity_token(value["independence_key"]) for value in roster_rows} & model_keys),
            "human reviewer is not independent from candidate/verifier",
        )
    else:
        _require(judgments_path is None, "judgments supplied although no cells need adjudication")
        _require(roster_path is None, "human roster supplied although no cells need adjudication")
        _require(human_receipt_output is None, "human receipt supplied although no human decided a cell")

    def expected_human_snapshot(record_id: str) -> dict[str, Any]:
        packet = packets[record_id]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in packet["items"]:
            row = judgments[(record_id, item)]
            grouped[str(row["reviewer_id"])].append(row)
        human_reviews = [
            ledger.make_human_adjudication_review_session(
                records[record_id],
                reviewer=_reviewer_from_roster(roster[reviewer_id]),
                judgments=grouped[reviewer_id],
                prepare_manifest_file_sha256=gold.file_sha256(prepare_manifest.resolve()),
                judgments_file_sha256=str(judgments_sha),
            )
            for reviewer_id in sorted(grouped)
        ]
        base_reviews = [
            value
            for value in initial_snapshots[record_id]["reviews"]
            if value["role"] in ("candidate", "verifier")
        ]
        return ledger.build_snapshot(
            records[record_id],
            [*base_reviews, *human_reviews],
            supersedes_event_id=initial_events[record_id]["event_id"],
            audit_tags=("blind-human-adjudication-v1", "blind-model-pair-v1"),
        )

    try:
        full_state = ledger.materialize_ledger(
            ledger.iter_events(ledger_path), records, require_sealed=False
        )
    except (OSError, ValueError) as exc:
        raise ReviewWorkflowError(f"current ledger replay failed: {exc}") from exc
    if full_state.sealed:
        writer = None
    else:
        writer = ledger.LedgerWriter(
            ledger_path, str(plan["ledger_id"]), create=False, records=records
        )
        full_state = writer.state
        current_events, current_snapshots = _load_active_snapshots(ledger_path, full_state)
        for record_id in order:
            packet = packets.get(record_id)
            if packet is None:
                _require(
                    full_state.active[record_id].event_id
                    == prefix_state.active[record_id].event_id,
                    f"{record_id}: consensus-only record was revised",
                )
                continue
            expected_snapshot = expected_human_snapshot(record_id)
            current_event = current_events[record_id]
            if current_event["event_id"] == initial_events[record_id]["event_id"]:
                writer.append_snapshot(expected_snapshot, records[record_id])
            else:
                _require(
                    current_snapshots[record_id] == expected_snapshot,
                    f"{record_id}: resumed human revision differs from frozen judgments",
                )
        _require(
            writer.state.event_count
            == manifest["preadjudication_event_count"] + manifest["unresolved_records"],
            "human revision event count differs from queued record count",
        )
        _require(
            writer.state.resolved_cells == expected_records * len(ledger.ITEMS),
            "human adjudication left unresolved cells",
        )
        writer.seal(expected_record_ids=order, scope_items=ledger.ITEMS)
        full_state = writer.state

    _require(full_state.sealed, "ledger was not sealed")
    _require(full_state.resolved_cells == expected_records * len(ledger.ITEMS), "sealed ledger is incomplete")
    final_events, final_snapshots = _load_active_snapshots(ledger_path, full_state)
    for record_id in order:
        if record_id in packets:
            _require(
                final_snapshots[record_id] == expected_human_snapshot(record_id),
                f"{record_id}: sealed human revision differs from frozen judgments",
            )
        else:
            _require(
                final_events[record_id]["event_id"] == initial_events[record_id]["event_id"],
                f"{record_id}: sealed consensus-only snapshot was revised",
            )
    policy_time = _utc(plan["identity"]["audit_policy"]["frozen_at_utc"], "audit policy freeze")
    _, last_event = _validate_event_times(ledger_path, floor=policy_time)
    contract = gold.BuildContract(
        expected_records=expected_records,
        expected_cells=expected_records * len(ledger.ITEMS),
        require_official_input=False,
    )
    export_manifest, resolved_rows = _publish_export(
        ledger_path=ledger_path.resolve(),
        records=records,
        output_dir=export_output_dir.resolve(),
        contract=contract,
    )
    try:
        _, human_scope, _ = gold._validate_resolved_rows(  # type: ignore[attr-defined]
            resolved_rows, records, qualifications, contract
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ReviewWorkflowError(f"sealed resolved-row validation failed: {exc}") from exc
    _require(len(human_scope) == manifest["unresolved_cells"], "human scope differs from blind queue")

    receipt: dict[str, Any] | None = None
    completion = max(_utc(last_event, "seal time"), latest_judgment).isoformat()
    if human_scope:
        assert human_receipt_output is not None
        receipt = _write_or_verify_human_receipt(
            output=human_receipt_output.resolve(),
            export_manifest_path=(export_output_dir.resolve() / "review_export_manifest.json"),
            export_manifest=export_manifest,
            human_scope=human_scope,
            roster=roster_rows,
            completed_utc=completion,
        )
        try:
            gold._validate_human_adjudication(  # type: ignore[attr-defined]
                human_receipt_output.resolve(),
                human_scope,
                resolved_rows,
                export_output_dir.resolve() / "review_export_manifest.json",
                export_manifest,
                {
                    "candidate": {
                        value["decisions"][item]["vote_provenance"]["candidate"]["reviewer"]["reviewer_id"]
                        for value in resolved_rows.values()
                        for item in ledger.ITEMS
                    },
                    "verifier": {
                        value["decisions"][item]["vote_provenance"]["verifier"]["reviewer"]["reviewer_id"]
                        for value in resolved_rows.values()
                        for item in ledger.ITEMS
                    },
                    "adjudicator": {row["reviewer_id"] for row in human_scope},
                },
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ReviewWorkflowError(f"human receipt self-verification failed: {exc}") from exc
    return {
        "status": "SEALED_AND_EXPORTED",
        "ledger_id": full_state.ledger_id,
        "ledger_head_sha256": full_state.head_sha256,
        "records": expected_records,
        "cells": expected_records * len(ledger.ITEMS),
        "human_adjudicated_cells": len(human_scope),
        "review_export_manifest": str(
            (export_output_dir.resolve() / "review_export_manifest.json")
        ),
        "review_export_manifest_sha256": gold.file_sha256(
            export_output_dir.resolve() / "review_export_manifest.json"
        ),
        "human_receipt": str(human_receipt_output.resolve()) if receipt is not None else None,
    }


def _require_official_input(path: pathlib.Path) -> None:
    _require(
        path.resolve() == gold.OFFICIAL_UNLABELED.resolve(),
        "production review workflow accepts only the official 20,000-record organizer input",
    )


def _prepare_command(args: argparse.Namespace) -> int:
    _require_official_input(args.input)
    result = prepare_model_passes(
        organizer_input=args.input,
        audit_policy=args.audit_policy,
        candidate_run_dirs=args.candidate_run_dir,
        verifier_run_dirs=args.verifier_run_dir,
        candidate_qualification=args.candidate_qualification,
        verifier_qualification=args.verifier_qualification,
        preflight_plan=args.preflight_plan,
        ledger_path=args.ledger,
        blind_queue=args.blind_queue,
        prepare_manifest=args.prepare_manifest,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _finalize_command(args: argparse.Namespace) -> int:
    _require_official_input(args.input)
    result = finalize_human_adjudication(
        organizer_input=args.input,
        ledger_path=args.ledger,
        preflight_plan=args.preflight_plan,
        prepare_manifest=args.prepare_manifest,
        blind_queue=args.blind_queue,
        judgments_path=args.judgments,
        roster_path=args.human_roster,
        export_output_dir=args.export_output_dir,
        human_receipt_output=args.human_receipt,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser(
        "prepare-model-passes",
        help="strictly verify two blind full-record runs and freeze the human queue",
    )
    prepare.add_argument("--input", type=pathlib.Path, required=True)
    prepare.add_argument("--audit-policy", type=pathlib.Path, required=True)
    prepare.add_argument(
        "--candidate-run-dir", type=pathlib.Path, action="append", required=True
    )
    prepare.add_argument(
        "--verifier-run-dir", type=pathlib.Path, action="append", required=True
    )
    prepare.add_argument("--candidate-qualification", type=pathlib.Path, required=True)
    prepare.add_argument("--verifier-qualification", type=pathlib.Path, required=True)
    prepare.add_argument("--preflight-plan", type=pathlib.Path, required=True)
    prepare.add_argument("--ledger", type=pathlib.Path, required=True)
    prepare.add_argument("--blind-queue", type=pathlib.Path, required=True)
    prepare.add_argument("--prepare-manifest", type=pathlib.Path, required=True)
    prepare.set_defaults(func=_prepare_command)

    finalize = commands.add_parser(
        "finalize-human",
        help="apply blind human commitments, seal, and export build inputs",
    )
    finalize.add_argument("--input", type=pathlib.Path, required=True)
    finalize.add_argument("--ledger", type=pathlib.Path, required=True)
    finalize.add_argument("--preflight-plan", type=pathlib.Path, required=True)
    finalize.add_argument("--prepare-manifest", type=pathlib.Path, required=True)
    finalize.add_argument("--blind-queue", type=pathlib.Path, required=True)
    finalize.add_argument("--judgments", type=pathlib.Path)
    finalize.add_argument("--human-roster", type=pathlib.Path)
    finalize.add_argument("--export-output-dir", type=pathlib.Path, required=True)
    finalize.add_argument("--human-receipt", type=pathlib.Path)
    finalize.set_defaults(func=_finalize_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ReviewWorkflowError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"review-ledger workflow refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
