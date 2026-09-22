"""Two-stage, source-first human audit workflow for independent gold.

The sampling policy is frozen before annotation starts.  After the review
ledger is sealed, selection is derived mechanically from that policy and a
blind packet is emitted without labels, routes, votes, or rationales.  A final
PASS report can be compiled only from complete blind commitments with zero
defects and zero unresolved cells.  ``build_gold.py`` independently replays all
of these bindings before publishing gold.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import secrets
import tempfile
from typing import Any, Mapping, Sequence

try:
    from tools.independent_gold import build_gold as gold
except ModuleNotFoundError:  # Direct execution from this directory.
    import build_gold as gold  # type: ignore[no-redef]


BLIND_PACKET_SCHEMA = "dacon.independent.gold_blind_audit_packet.v1"
AUDIT_SESSION_SCHEMA = "dacon.independent.gold_audit_session.v1"
BLIND_JUDGMENT_SCHEMA = "dacon.independent.gold_blind_audit_judgment.v1"
CHECK_KEYS = {
    "source_match",
    "evidence_exact",
    "label_supported",
    "rule_application",
    "provenance_valid",
}
SELECTION_METHOD_V2 = "mandatory_risk_plus_sha256_rank_per_item_category_completeness_v2"
SELECTION_METHOD_V3 = "mandatory_risk_plus_source_rank_per_item_category_completeness_family_v3"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class AuditWorkflowError(ValueError):
    """The audit workflow cannot honestly produce the requested artifact."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditWorkflowError(message)


def _self_hash(value: Mapping[str, Any], field: str) -> str:
    claimed = value.get(field)
    _require(isinstance(claimed, str), f"{field} is required")
    expected = gold.sha256_object({key: child for key, child in value.items() if key != field})
    _require(claimed == expected, f"{field} mismatch")
    return claimed


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


def _atomic_jsonl(path: pathlib.Path, rows: Sequence[Mapping[str, Any]]) -> None:
    payload = b"".join(
        (gold.canonical_json(dict(row)) + "\n").encode("utf-8") for row in rows
    )
    _atomic_bytes(path, payload)


def _refuse_existing(*paths: pathlib.Path) -> None:
    existing = [str(path) for path in paths if path.exists()]
    _require(not existing, f"refusing to overwrite frozen audit artifacts: {existing}")


def _load_policy(path: pathlib.Path) -> dict[str, Any]:
    policy = gold._load_json(path, "gold audit policy")  # type: ignore[attr-defined]
    if policy.get("schema_version") == gold.AUDIT_POLICY_SCHEMA_V3:
        expected_v3 = {
            "schema_version", "policy_id", "frozen_at_utc",
            "organizer_input_sha256", "selection_method",
            "seed_commitment_sha256", "per_stratum",
            "recurring_family_per_stratum", "mandatory_categories",
            "stratify_by_completeness", "family_manifest_path",
            "family_manifest_sha256", "zero_defect_required", "policy_sha256",
        }
        _require(set(policy) == expected_v3, "v3 gold audit policy keys differ")
        _self_hash(policy, "policy_sha256")
        gold._parse_utc(policy["frozen_at_utc"], "audit policy freeze")  # type: ignore[attr-defined]
        _require(policy["selection_method"] == SELECTION_METHOD_V3, "wrong v3 selection method")
        _require(
            type(policy["per_stratum"]) is int
            and policy["per_stratum"] >= gold.MIN_AUDIT_PER_STRATUM_V3,
            "v3 audit sample size is below 300",
        )
        _require(
            type(policy["recurring_family_per_stratum"]) is int
            and policy["recurring_family_per_stratum"] >= 1,
            "v3 recurring family sample size must be positive",
        )
        _require(
            isinstance(policy["seed_commitment_sha256"], str)
            and HEX64.fullmatch(policy["seed_commitment_sha256"]) is not None,
            "v3 seed commitment must be a SHA-256 digest",
        )
        _require(
            isinstance(policy["family_manifest_sha256"], str)
            and HEX64.fullmatch(policy["family_manifest_sha256"]) is not None,
            "v3 family manifest digest is invalid",
        )
        _require(
            isinstance(policy["family_manifest_path"], str)
            and bool(policy["family_manifest_path"]),
            "v3 family manifest path is missing",
        )
        family_path = gold._resolve_bound_path(  # type: ignore[attr-defined]
            policy["family_manifest_path"], manifest_path=path,
            context="v3 family manifest",
        )
        _require(family_path.is_file(), "v3 family manifest preflight dependency is missing")
        _require(
            gold.file_sha256(family_path) == policy["family_manifest_sha256"],
            "v3 family manifest preflight dependency changed",
        )
        _require(
            policy["mandatory_categories"]
            == ["agreement_boundary_negative", "mandatory_adjudication", "mandatory_medium_positive"],
            "v3 mandatory audit categories differ",
        )
        _require(policy["stratify_by_completeness"] is True, "v3 completeness strata required")
        _require(policy["zero_defect_required"] is True, "v3 zero-defect rule required")
        return policy
    expected = {
        "schema_version",
        "policy_id",
        "frozen_at_utc",
        "organizer_input_sha256",
        "selection_method",
        "selection_seed",
        "per_stratum",
        "mandatory_categories",
        "stratify_by_completeness",
        "zero_defect_required",
        "policy_sha256",
    }
    _require(set(policy) == expected, "gold audit policy keys differ")
    _require(policy["schema_version"] == gold.AUDIT_POLICY_SCHEMA, "wrong policy schema")
    _self_hash(policy, "policy_sha256")
    gold._parse_utc(policy["frozen_at_utc"], "audit policy freeze")  # type: ignore[attr-defined]
    _require(
        policy["selection_method"]
        == SELECTION_METHOD_V2,
        "unsupported audit selection method",
    )
    _require(
        type(policy["per_stratum"]) is int
        and policy["per_stratum"] >= gold.MIN_AUDIT_PER_STRATUM,
        "audit sample size is below the required floor",
    )
    _require(
        policy["mandatory_categories"]
        == [
            "agreement_boundary_negative",
            "mandatory_adjudication",
            "mandatory_medium_positive",
        ],
        "mandatory audit categories differ",
    )
    _require(policy["stratify_by_completeness"] is True, "completeness strata required")
    _require(policy["zero_defect_required"] is True, "zero-defect stopping rule required")
    return policy


def v3_seed_commitment(seed: str) -> str:
    """Domain-separated commitment; the 256-bit seed stays outside policy."""

    _require(isinstance(seed, str) and HEX64.fullmatch(seed) is not None, "v3 seed must be 64 lowercase hex characters")
    return gold.sha256_bytes(f"dacon-independent-audit-v3-seed\0{seed}".encode("ascii"))


def _family_assignment(
    *, family_manifest_path: pathlib.Path, organizer_input: pathlib.Path,
    expected_records: int = gold.EXPECTED_RECORDS,
) -> dict[str, str]:
    """Fail closed until the family artifact can be replayed from organizer input."""

    _require(family_manifest_path.is_file(), "v3 organizer-only family manifest is missing")
    try:
        from tools.independent_gold import notice_families
    except ModuleNotFoundError as exc:
        raise AuditWorkflowError("v3 organizer-only family verifier is unavailable") from exc
    manifest = gold._load_json(family_manifest_path, "v3 family manifest")  # type: ignore[attr-defined]
    try:
        notice_families.verify_family_manifest(
            manifest, organizer_input, expected_records=expected_records
        )
    except ValueError as exc:
        raise AuditWorkflowError(f"v3 family manifest fails organizer-only replay: {exc}") from exc
    rows = manifest.get("records")
    _require(isinstance(rows, list), "v3 family manifest records are missing")
    return {str(row["record_id"]): str(row["family_id"]) for row in rows}


def freeze_policy_v3(
    *,
    organizer_input: pathlib.Path,
    family_manifest_path: pathlib.Path,
    output: pathlib.Path,
    seed_commitment_sha256: str,
    per_stratum: int = gold.MIN_AUDIT_PER_STRATUM_V3,
    recurring_family_per_stratum: int = 1,
    frozen_at_utc: str | None = None,
    expected_records: int = gold.EXPECTED_RECORDS,
) -> dict[str, Any]:
    """Precommit a v3 audit without publishing the still-secret selection seed."""

    _refuse_existing(output)
    _require(organizer_input.is_file(), "organizer input is missing")
    _require(
        type(per_stratum) is int and per_stratum >= gold.MIN_AUDIT_PER_STRATUM_V3,
        "v3 per_stratum is below 300",
    )
    _require(
        type(recurring_family_per_stratum) is int and recurring_family_per_stratum >= 1,
        "v3 recurring family minimum must be positive",
    )
    _require(
        isinstance(seed_commitment_sha256, str)
        and HEX64.fullmatch(seed_commitment_sha256) is not None,
        "v3 seed commitment is invalid",
    )
    family_manifest_path = family_manifest_path.resolve()
    _family_assignment(
        family_manifest_path=family_manifest_path, organizer_input=organizer_input,
        expected_records=expected_records,
    )
    frozen = frozen_at_utc or utc_now()
    gold._parse_utc(frozen, "v3 audit policy freeze")  # type: ignore[attr-defined]
    identity = {
        "organizer_input_sha256": gold.file_sha256(organizer_input),
        "selection_method": SELECTION_METHOD_V3,
        "seed_commitment_sha256": seed_commitment_sha256,
        "per_stratum": per_stratum,
        "recurring_family_per_stratum": recurring_family_per_stratum,
        "family_manifest_path": str(family_manifest_path),
        "family_manifest_sha256": gold.file_sha256(family_manifest_path),
    }
    policy: dict[str, Any] = {
        "schema_version": gold.AUDIT_POLICY_SCHEMA_V3,
        "policy_id": gold.sha256_object(identity),
        "frozen_at_utc": frozen,
        **identity,
        "mandatory_categories": [
            "agreement_boundary_negative", "mandatory_adjudication", "mandatory_medium_positive"
        ],
        "stratify_by_completeness": True,
        "zero_defect_required": True,
    }
    policy["policy_sha256"] = gold.sha256_object(policy)
    _atomic_json(output, policy)
    return policy


def freeze_policy(
    *,
    organizer_input: pathlib.Path,
    output: pathlib.Path,
    selection_seed: str | None = None,
    per_stratum: int = gold.MIN_AUDIT_PER_STRATUM,
    frozen_at_utc: str | None = None,
) -> dict[str, Any]:
    _refuse_existing(output)
    _require(organizer_input.is_file(), "organizer input is missing")
    _require(per_stratum >= gold.MIN_AUDIT_PER_STRATUM, "per_stratum is below 30")
    seed = selection_seed or secrets.token_hex(32)
    _require(bool(seed.strip()), "selection seed is required")
    frozen = frozen_at_utc or utc_now()
    gold._parse_utc(frozen, "audit policy freeze")  # type: ignore[attr-defined]
    identity = {
        "organizer_input_sha256": gold.file_sha256(organizer_input),
        "selection_method": "mandatory_risk_plus_sha256_rank_per_item_category_completeness_v2",
        "selection_seed": seed,
        "per_stratum": per_stratum,
    }
    policy: dict[str, Any] = {
        "schema_version": gold.AUDIT_POLICY_SCHEMA,
        "policy_id": gold.sha256_object(identity),
        "frozen_at_utc": frozen,
        **identity,
        "mandatory_categories": [
            "agreement_boundary_negative",
            "mandatory_adjudication",
            "mandatory_medium_positive",
        ],
        "stratify_by_completeness": True,
        "zero_defect_required": True,
    }
    policy["policy_sha256"] = gold.sha256_object(policy)
    _atomic_json(output, policy)
    return policy


def _make_blind_packets(
    selection: Sequence[Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    for selected in selection:
        core: dict[str, Any] = {
            "schema_version": BLIND_PACKET_SCHEMA,
            "id": selected["id"],
            "item": selected["item"],
            "source_sha256": selected["source_sha256"],
            "snapshot_event_sha256": selected["snapshot_event_sha256"],
            "source_record": records[str(selected["id"])],
            "annotation_answer_visible": False,
            "peer_votes_visible": False,
        }
        core["packet_sha256"] = gold.sha256_object(core)
        packets.append(core)
    return packets


def freeze_selection(
    *,
    organizer_input: pathlib.Path,
    ledger_path: pathlib.Path,
    review_export_manifest: pathlib.Path,
    policy_path: pathlib.Path,
    selection_output: pathlib.Path,
    plan_output: pathlib.Path,
    blind_packets_output: pathlib.Path,
    contract: gold.BuildContract = gold.BuildContract(),
    frozen_at_utc: str | None = None,
    seed_reveal: str | None = None,
) -> dict[str, Any]:
    _refuse_existing(selection_output, plan_output, blind_packets_output)
    policy = _load_policy(policy_path)
    _require(
        policy["organizer_input_sha256"] == gold.file_sha256(organizer_input),
        "policy is bound to another organizer input",
    )
    _, records = gold._load_organizer_records(organizer_input, contract)  # type: ignore[attr-defined]
    export, rows, ledger_receipt = gold._verify_review_export(  # type: ignore[attr-defined]
        ledger_path=ledger_path,
        manifest_path=review_export_manifest,
        records=records,
        contract=contract,
    )
    policy_time = gold._parse_utc(policy["frozen_at_utc"], "audit policy freeze")  # type: ignore[attr-defined]
    review_time = gold._parse_utc(ledger_receipt["first_event_utc"], "review start")  # type: ignore[attr-defined]
    _require(policy_time <= review_time, "audit policy was frozen after review began")
    is_v3 = policy["schema_version"] == gold.AUDIT_POLICY_SCHEMA_V3
    if is_v3:
        _require(seed_reveal is not None, "v3 audit requires seed reveal after ledger seal")
        _require(
            v3_seed_commitment(seed_reveal) == policy["seed_commitment_sha256"],
            "v3 seed reveal differs from frozen commitment",
        )
        family_path = gold._resolve_bound_path(  # type: ignore[attr-defined]
            policy["family_manifest_path"], manifest_path=policy_path,
            context="v3 organizer-only family manifest",
        )
        _require(
            gold.file_sha256(family_path) == policy["family_manifest_sha256"],
            "v3 family manifest changed after policy freeze",
        )
        family_by_id = _family_assignment(
            family_manifest_path=family_path, organizer_input=organizer_input,
            expected_records=contract.expected_records,
        )
        selection, populations, family_populations = gold._expected_audit_selection_v3(  # type: ignore[attr-defined]
            rows, records, seed=seed_reveal, per_stratum=policy["per_stratum"],
            family_by_id=family_by_id,
            recurring_family_per_stratum=policy["recurring_family_per_stratum"],
        )
        seed = seed_reveal
    else:
        _require(seed_reveal is None, "v2 audit does not take a seed reveal")
        selection, populations = gold._expected_audit_selection(  # type: ignore[attr-defined]
            rows,
            records,
            seed=policy["selection_seed"],
            per_stratum=policy["per_stratum"],
        )
        family_populations = None
        seed = policy["selection_seed"]
    packets = _make_blind_packets(selection, records)
    _atomic_jsonl(selection_output, selection)
    _atomic_jsonl(blind_packets_output, packets)
    frozen = frozen_at_utc or utc_now()
    _require(
        policy_time <= gold._parse_utc(frozen, "audit selection freeze"),  # type: ignore[attr-defined]
        "selection freeze predates policy",
    )
    plan_identity = {
        "policy_sha256": policy["policy_sha256"],
        "review_ledger_head_sha256": export["ledger_head_sha256"],
        "selection_sha256": gold.file_sha256(selection_output),
    }
    plan: dict[str, Any] = {
        "schema_version": gold.AUDIT_PLAN_SCHEMA_V3 if is_v3 else gold.AUDIT_PLAN_SCHEMA,
        "plan_id": gold.sha256_object(plan_identity),
        "frozen_at_utc": frozen,
        "organizer_input_sha256": gold.file_sha256(organizer_input),
        "review_export_manifest_sha256": gold.file_sha256(review_export_manifest),
        "review_ledger_head_sha256": export["ledger_head_sha256"],
        "policy_path": str(policy_path.resolve()),
        "policy_sha256": gold.file_sha256(policy_path),
        "population_records": contract.expected_records,
        "population_cells": contract.expected_cells,
        "population_strata": dict(sorted(populations.items())),
        "selection_method": policy["selection_method"],
        "selection_seed": seed,
        "per_stratum": policy["per_stratum"],
        "selection_path": str(selection_output.resolve()),
        "selection_sha256": gold.file_sha256(selection_output),
        "selected_cells": len(selection),
        "zero_defect_required": True,
    }
    if is_v3:
        assert family_populations is not None
        plan.update({
            "seed_commitment_sha256": policy["seed_commitment_sha256"],
            "family_manifest_path": policy["family_manifest_path"],
            "family_manifest_sha256": policy["family_manifest_sha256"],
            "recurring_family_per_stratum": policy["recurring_family_per_stratum"],
            "population_family_strata": dict(sorted(family_populations.items())),
        })
    plan["plan_sha256"] = gold.sha256_object(plan)
    _atomic_json(plan_output, plan)
    return plan


def _normalized_roster(roster_path: pathlib.Path) -> list[dict[str, Any]]:
    raw = json.loads(roster_path.read_text(encoding="utf-8"))
    _require(isinstance(raw, list), "auditor roster must be a JSON list")
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(raw, 1):
        _require(isinstance(row, Mapping), f"auditor roster row {index} must be an object")
        item = dict(row)
        attestation = gold._resolve_bound_path(  # type: ignore[attr-defined]
            item.get("attestation_path"),
            manifest_path=roster_path,
            context=f"auditor roster row {index}",
        )
        item["attestation_path"] = str(attestation)
        normalized.append(item)
    validated = gold._validate_human_roster(  # type: ignore[attr-defined]
        normalized,
        manifest_path=roster_path,
        context="gold auditors",
    )
    return [validated[key] for key in sorted(validated)]


def start_session(
    *,
    plan_path: pathlib.Path,
    blind_packets_path: pathlib.Path,
    roster_path: pathlib.Path,
    output: pathlib.Path,
    started_utc: str | None = None,
) -> dict[str, Any]:
    _refuse_existing(output)
    plan = gold._load_json(plan_path, "gold audit plan")  # type: ignore[attr-defined]
    _self_hash(plan, "plan_sha256")
    frozen = gold._parse_utc(plan["frozen_at_utc"], "audit plan freeze")  # type: ignore[attr-defined]
    packets = gold._read_jsonl(blind_packets_path, "blind audit packets")  # type: ignore[attr-defined]
    _require(len(packets) == plan["selected_cells"], "blind packet count differs from plan")
    seen: set[tuple[str, str]] = set()
    for packet in packets:
        _require(packet.get("schema_version") == BLIND_PACKET_SCHEMA, "wrong blind packet schema")
        _self_hash(packet, "packet_sha256")
        _require(packet.get("annotation_answer_visible") is False, "blind packet leaks answer")
        _require(packet.get("peer_votes_visible") is False, "blind packet leaks peer votes")
        forbidden = {"label", "route", "decision", "votes", "rationale", "stratum"}
        _require(not (forbidden & set(packet)), "blind packet contains annotation outcome")
        key = (str(packet.get("id") or ""), str(packet.get("item") or ""))
        _require(key not in seen, "duplicate blind packet")
        seen.add(key)
    roster = _normalized_roster(roster_path)
    started = started_utc or utc_now()
    started_dt = gold._parse_utc(started, "audit start")  # type: ignore[attr-defined]
    _require(frozen <= started_dt, "audit session starts before plan freeze")
    session: dict[str, Any] = {
        "schema_version": AUDIT_SESSION_SCHEMA,
        "status": "OPEN",
        "audit_plan_sha256": plan["plan_sha256"],
        "audit_plan_file_sha256": gold.file_sha256(plan_path),
        "selection_sha256": plan["selection_sha256"],
        "blind_packets_path": str(blind_packets_path.resolve()),
        "blind_packets_sha256": gold.file_sha256(blind_packets_path),
        "blind_packets": len(packets),
        "started_utc": started,
        "auditor_roster": roster,
        "source_first_blind_commitment_required": True,
    }
    session["session_sha256"] = gold.sha256_object(session)
    _atomic_json(output, session)
    return session


def _quote_is_exact(record: Mapping[str, Any], quote: str) -> bool:
    return bool(quote) and any(quote in str(document["text"]) for document in record["docs"])


def verify_session_blind_packets(
    *,
    session: Mapping[str, Any],
    session_path: pathlib.Path,
    selection: Sequence[Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
) -> tuple[pathlib.Path, str]:
    """Re-read the exact session-bound packet bytes and replay their contents."""
    packets_path = gold._resolve_bound_path(  # type: ignore[attr-defined]
        session.get("blind_packets_path"),
        manifest_path=session_path,
        context="session blind packets",
    )
    _require(packets_path.is_file(), "session blind packet file is missing")
    claimed_sha256 = session.get("blind_packets_sha256")
    _require(gold._is_hash(claimed_sha256), "session blind packet SHA-256 is invalid")  # type: ignore[attr-defined]
    actual_sha256 = gold.file_sha256(packets_path)
    _require(actual_sha256 == claimed_sha256, "session blind packet file hash mismatch")
    packets = gold._read_jsonl(packets_path, "session blind packets")  # type: ignore[attr-defined]
    _require(
        gold.file_sha256(packets_path) == actual_sha256,
        "session blind packet file changed during verification",
    )
    expected = _make_blind_packets(selection, records)
    _require(session.get("blind_packets") == len(expected), "session blind packet count mismatch")
    _require(packets == expected, "session blind packets differ from frozen selection/source/snapshot")
    return packets_path, actual_sha256


def replay_blind_judgments(
    *,
    selection: Sequence[Mapping[str, Any]],
    rows: Mapping[str, Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    judgments: Sequence[Mapping[str, Any]],
    roster: Mapping[str, Any],
    started_utc: str,
    completed_utc: str,
) -> tuple[list[dict[str, Any]], int, int]:
    """Recompute result rows without trusting the report or stored result file."""
    started = gold._parse_utc(started_utc, "audit start")  # type: ignore[attr-defined]
    completed = gold._parse_utc(completed_utc, "audit completion")  # type: ignore[attr-defined]
    _require(completed >= started, "audit completion predates start")
    selected_by_key = {(row["id"], row["item"]): row for row in selection}
    _require(len(selected_by_key) == len(selection), "duplicate frozen audit selection")
    judgment_by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, judgment in enumerate(judgments, 1):
        required = {
            "schema_version", "id", "item", "source_sha256",
            "snapshot_event_sha256", "auditor_id", "reviewed_utc",
            "blind_to_annotation_votes", "proposed_label", "confidence",
            "proposed_evidence", "premise_quotes", "rationale",
            "rule_application_confirmed", "source_completeness_confirmed",
        }
        _require(set(judgment) == required, f"blind judgment {index}: keys differ")
        _require(judgment["schema_version"] == BLIND_JUDGMENT_SCHEMA, f"blind judgment {index}: wrong schema")
        key = (str(judgment["id"]), str(judgment["item"]))
        _require(key in selected_by_key and key not in judgment_by_key, f"blind judgment {index}: extra/duplicate cell")
        selected = selected_by_key[key]
        _require(
            judgment["source_sha256"] == selected["source_sha256"]
            and judgment["snapshot_event_sha256"] == selected["snapshot_event_sha256"],
            f"blind judgment {index}: stale binding",
        )
        _require(judgment["auditor_id"] in roster, f"blind judgment {index}: unknown auditor")
        reviewed = gold._parse_utc(judgment["reviewed_utc"], f"blind judgment {index} time")  # type: ignore[attr-defined]
        _require(started <= reviewed <= completed, f"blind judgment {index}: outside audit window")
        _require(judgment["blind_to_annotation_votes"] is True, f"blind judgment {index}: not blind")
        _require(judgment["proposed_label"] in (0, 1, "U"), f"blind judgment {index}: invalid label")
        _require(judgment["confidence"] in {"high", "medium", "low"}, f"blind judgment {index}: invalid confidence")
        _require(isinstance(judgment["rationale"], str) and judgment["rationale"].strip(), f"blind judgment {index}: rationale required")
        _require(type(judgment["rule_application_confirmed"]) is bool, f"blind judgment {index}: rule check required")
        _require(type(judgment["source_completeness_confirmed"]) is bool, f"blind judgment {index}: completeness check required")
        _require(isinstance(judgment["premise_quotes"], list), f"blind judgment {index}: premise quotes required")
        record = records[key[0]]
        for quote in judgment["premise_quotes"]:
            _require(isinstance(quote, str) and _quote_is_exact(record, quote), f"blind judgment {index}: premise quote not exact")
        evidence = judgment["proposed_evidence"]
        _require(isinstance(evidence, str) and len(evidence) <= gold.MAX_EVIDENCE_CHARS, f"blind judgment {index}: evidence invalid")
        if judgment["proposed_label"] == 1 and key[1] not in gold.ABSENCE_ITEMS:
            _require(_quote_is_exact(record, evidence), f"blind judgment {index}: positive evidence not exact")
        else:
            _require(evidence == "", f"blind judgment {index}: evidence convention differs")
        judgment_by_key[key] = judgment
    _require(set(judgment_by_key) == set(selected_by_key), "blind judgments are incomplete")

    results: list[dict[str, Any]] = []
    defects = 0
    unresolved = 0
    for selected in selection:
        key = (selected["id"], selected["item"])
        judgment = judgment_by_key[key]
        cell = rows[key[0]]["decisions"][key[1]]
        record = records[key[0]]
        source_match = rows[key[0]]["source_sha256"] == selected["source_sha256"]
        final_evidence = str(cell["evidence"])
        evidence_exact = (
            bool(gold._source_locations(record, final_evidence))  # type: ignore[attr-defined]
            if cell["label"] == 1 and key[1] not in gold.ABSENCE_ITEMS
            else final_evidence == ""
        )
        label_supported = judgment["proposed_label"] == cell["label"]
        rule_application = judgment["rule_application_confirmed"] is True
        provenance_valid = True  # Caller independently verified the sealed ledger.
        checks = {
            "source_match": source_match,
            "evidence_exact": evidence_exact,
            "label_supported": label_supported,
            "rule_application": rule_application,
            "provenance_valid": provenance_valid,
        }
        is_unresolved = (
            judgment["proposed_label"] == "U"
            or judgment["confidence"] == "low"
            or judgment["source_completeness_confirmed"] is False
        )
        if is_unresolved:
            outcome = "UNRESOLVED"
            unresolved += 1
        elif not all(checks.values()):
            outcome = "DEFECT"
            defects += 1
        else:
            outcome = "PASS"
        results.append(
            {
                "schema_version": gold.AUDIT_RESULT_SCHEMA,
                "id": key[0],
                "item": key[1],
                "source_sha256": selected["source_sha256"],
                "snapshot_event_sha256": selected["snapshot_event_sha256"],
                "outcome": outcome,
                "checks": checks,
                "auditor_id": judgment["auditor_id"],
                "reviewed_utc": judgment["reviewed_utc"],
                "review_note": (
                    f"blind_judgment_sha256={gold.sha256_object(judgment)}; "
                    f"confidence={judgment['confidence']}; rationale={judgment['rationale']}"
                ),
            }
        )
    return results, defects, unresolved


def finalize_report(
    *,
    organizer_input: pathlib.Path,
    ledger_path: pathlib.Path,
    review_export_manifest: pathlib.Path,
    plan_path: pathlib.Path,
    session_path: pathlib.Path,
    judgments_path: pathlib.Path,
    results_output: pathlib.Path,
    report_output: pathlib.Path,
    contract: gold.BuildContract = gold.BuildContract(),
    completed_utc: str | None = None,
) -> dict[str, Any]:
    _refuse_existing(results_output, report_output)
    plan = gold._load_json(plan_path, "gold audit plan")  # type: ignore[attr-defined]
    _self_hash(plan, "plan_sha256")
    session_file_sha256 = gold.file_sha256(session_path)
    session = gold._load_json(session_path, "gold audit session")  # type: ignore[attr-defined]
    _self_hash(session, "session_sha256")
    _require(session.get("status") == "OPEN", "audit session is not open")
    _require(session.get("audit_plan_sha256") == plan["plan_sha256"], "session uses another plan")
    _require(session.get("audit_plan_file_sha256") == gold.file_sha256(plan_path), "session plan hash mismatch")
    _require(session.get("selection_sha256") == plan["selection_sha256"], "session selection mismatch")
    _require(session.get("source_first_blind_commitment_required") is True, "session is not source-first blind")

    _, records = gold._load_organizer_records(organizer_input, contract)  # type: ignore[attr-defined]
    export, rows, _ = gold._verify_review_export(  # type: ignore[attr-defined]
        ledger_path=ledger_path,
        manifest_path=review_export_manifest,
        records=records,
        contract=contract,
    )
    _require(plan["organizer_input_sha256"] == gold.file_sha256(organizer_input), "plan input mismatch")
    _require(plan["review_export_manifest_sha256"] == gold.file_sha256(review_export_manifest), "plan export mismatch")
    _require(plan["review_ledger_head_sha256"] == export["ledger_head_sha256"], "plan ledger head mismatch")
    selection_path = gold._resolve_bound_path(  # type: ignore[attr-defined]
        plan["selection_path"], manifest_path=plan_path, context="audit selection"
    )
    _require(gold.file_sha256(selection_path) == plan["selection_sha256"], "selection hash mismatch")
    selection = gold._read_jsonl(selection_path, "audit selection")  # type: ignore[attr-defined]
    if plan.get("schema_version") == gold.AUDIT_PLAN_SCHEMA_V3:
        policy_path = gold._resolve_bound_path(  # type: ignore[attr-defined]
            plan["policy_path"], manifest_path=plan_path, context="v3 audit policy"
        )
        _require(gold.file_sha256(policy_path) == plan["policy_sha256"], "v3 policy hash mismatch")
        policy = _load_policy(policy_path)
        _require(policy["schema_version"] == gold.AUDIT_POLICY_SCHEMA_V3, "v3 plan uses another policy schema")
        _require(
            v3_seed_commitment(plan["selection_seed"])
            == plan["seed_commitment_sha256"] == policy["seed_commitment_sha256"],
            "v3 revealed seed differs from frozen commitment",
        )
        family_path = gold._resolve_bound_path(  # type: ignore[attr-defined]
            plan["family_manifest_path"], manifest_path=plan_path,
            context="v3 family manifest",
        )
        _require(
            plan["family_manifest_path"] == policy["family_manifest_path"]
            and plan["family_manifest_sha256"] == policy["family_manifest_sha256"]
            and gold.file_sha256(family_path) == policy["family_manifest_sha256"],
            "v3 family manifest differs from frozen policy",
        )
        family_by_id = _family_assignment(
            family_manifest_path=family_path, organizer_input=organizer_input,
            expected_records=contract.expected_records,
        )
        expected_selection, populations, family_populations = gold._expected_audit_selection_v3(  # type: ignore[attr-defined]
            rows, records, seed=plan["selection_seed"], per_stratum=plan["per_stratum"],
            family_by_id=family_by_id,
            recurring_family_per_stratum=plan["recurring_family_per_stratum"],
        )
        _require(plan["population_strata"] == dict(sorted(populations.items())), "v3 primary strata differ")
        _require(
            plan["population_family_strata"] == dict(sorted(family_populations.items())),
            "v3 family strata differ",
        )
    else:
        expected_selection, _ = gold._expected_audit_selection(  # type: ignore[attr-defined]
            rows,
            records,
            seed=plan["selection_seed"],
            per_stratum=plan["per_stratum"],
        )
    _require(selection == expected_selection, "selection is stale or cherry-picked")
    packets_path, packets_sha256 = verify_session_blind_packets(
        session=session, session_path=session_path, selection=selection, records=records
    )

    roster = gold._validate_human_roster(  # type: ignore[attr-defined]
        session.get("auditor_roster"), manifest_path=session_path, context="gold auditors"
    )
    annotation_reviewer_ids: set[str] = set()
    for row in rows.values():
        for cell in row["decisions"].values():
            for vote in cell["vote_provenance"].values():
                annotation_reviewer_ids.add(str(vote["reviewer"]["reviewer_id"]))
    _require(not (set(roster) & annotation_reviewer_ids), "auditor also cast an annotation vote")

    judgments_sha256 = gold.file_sha256(judgments_path)
    judgments = gold._read_jsonl(judgments_path, "blind audit judgments")  # type: ignore[attr-defined]
    _require(
        gold.file_sha256(judgments_path) == judgments_sha256,
        "blind judgments changed during verification",
    )
    completed = completed_utc or utc_now()
    results, defects, unresolved = replay_blind_judgments(
        selection=selection,
        rows=rows,
        records=records,
        judgments=judgments,
        roster=roster,
        started_utc=session["started_utc"],
        completed_utc=completed,
    )
    _require(
        gold.file_sha256(session_path) == session_file_sha256,
        "audit session changed during finalization",
    )
    _require(
        gold.file_sha256(judgments_path) == judgments_sha256,
        "blind judgments changed during finalization",
    )
    _require(
        gold.file_sha256(packets_path) == packets_sha256,
        "blind packets changed during finalization",
    )
    _atomic_jsonl(results_output, results)
    report: dict[str, Any] = {
        "schema_version": (
            gold.AUDIT_REPORT_SCHEMA_V4
            if plan.get("schema_version") == gold.AUDIT_PLAN_SCHEMA_V3
            else gold.AUDIT_REPORT_SCHEMA
        ),
        "status": "PASS" if defects == 0 and unresolved == 0 else "FAIL",
        "audit_plan_sha256": plan["plan_sha256"],
        "audit_plan_file_sha256": gold.file_sha256(plan_path),
        "selection_sha256": plan["selection_sha256"],
        "audit_session_path": str(session_path.resolve()),
        "audit_session_file_sha256": session_file_sha256,
        "audit_session_sha256": session["session_sha256"],
        "blind_packets_path": str(packets_path),
        "blind_packets_sha256": packets_sha256,
        "judgments_path": str(judgments_path.resolve()),
        "judgments_sha256": judgments_sha256,
        "started_utc": session["started_utc"],
        "completed_utc": completed,
        "audited_cells": len(results),
        "defect_cells": defects,
        "unresolved_cells": unresolved,
        "results_path": str(results_output.resolve()),
        "results_sha256": gold.file_sha256(results_output),
        "auditor_roster": session["auditor_roster"],
    }
    if plan.get("schema_version") == gold.AUDIT_PLAN_SCHEMA_V3:
        report["audit_power"] = gold._v3_audit_power_metadata(plan, selection)  # type: ignore[attr-defined]
    report["report_sha256"] = gold.sha256_object(report)
    _atomic_json(report_output, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Freeze and execute the human gold audit.")
    commands = parser.add_subparsers(dest="command", required=True)

    policy = commands.add_parser("freeze-policy")
    policy.add_argument("--input", type=pathlib.Path, default=gold.OFFICIAL_UNLABELED)
    policy.add_argument("--output", type=pathlib.Path, required=True)
    policy.add_argument("--version", choices=("v2", "v3"), default="v2")
    policy.add_argument("--selection-seed")
    policy.add_argument("--seed-commitment-sha256")
    policy.add_argument("--family-manifest", type=pathlib.Path)
    policy.add_argument("--per-stratum", type=int)
    policy.add_argument("--recurring-family-per-stratum", type=int, default=1)

    selection = commands.add_parser("freeze-selection")
    selection.add_argument("--input", type=pathlib.Path, default=gold.OFFICIAL_UNLABELED)
    selection.add_argument("--ledger", type=pathlib.Path, required=True)
    selection.add_argument("--review-export-manifest", type=pathlib.Path, required=True)
    selection.add_argument("--policy", type=pathlib.Path, required=True)
    selection.add_argument("--selection-output", type=pathlib.Path, required=True)
    selection.add_argument("--plan-output", type=pathlib.Path, required=True)
    selection.add_argument("--blind-packets-output", type=pathlib.Path, required=True)
    selection.add_argument("--seed-reveal-file", type=pathlib.Path)

    session = commands.add_parser("start-session")
    session.add_argument("--plan", type=pathlib.Path, required=True)
    session.add_argument("--blind-packets", type=pathlib.Path, required=True)
    session.add_argument("--auditor-roster", type=pathlib.Path, required=True)
    session.add_argument("--output", type=pathlib.Path, required=True)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--input", type=pathlib.Path, default=gold.OFFICIAL_UNLABELED)
    finalize.add_argument("--ledger", type=pathlib.Path, required=True)
    finalize.add_argument("--review-export-manifest", type=pathlib.Path, required=True)
    finalize.add_argument("--plan", type=pathlib.Path, required=True)
    finalize.add_argument("--session", type=pathlib.Path, required=True)
    finalize.add_argument("--judgments", type=pathlib.Path, required=True)
    finalize.add_argument("--results-output", type=pathlib.Path, required=True)
    finalize.add_argument("--report-output", type=pathlib.Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "freeze-policy":
            if args.version == "v3":
                _require(args.selection_seed is None, "v3 policy must not disclose selection seed")
                _require(args.family_manifest is not None, "v3 family manifest is required")
                _require(args.seed_commitment_sha256 is not None, "v3 seed commitment is required")
                result = freeze_policy_v3(
                    organizer_input=args.input,
                    family_manifest_path=args.family_manifest,
                    output=args.output,
                    seed_commitment_sha256=args.seed_commitment_sha256,
                    per_stratum=(
                        gold.MIN_AUDIT_PER_STRATUM_V3
                        if args.per_stratum is None else args.per_stratum
                    ),
                    recurring_family_per_stratum=args.recurring_family_per_stratum,
                )
            else:
                _require(args.seed_commitment_sha256 is None, "v2 policy does not use a seed commitment")
                _require(args.family_manifest is None, "v2 policy does not use a family manifest")
                result = freeze_policy(
                    organizer_input=args.input,
                    output=args.output,
                    selection_seed=args.selection_seed,
                    per_stratum=(
                        gold.MIN_AUDIT_PER_STRATUM
                        if args.per_stratum is None else args.per_stratum
                    ),
                )
        elif args.command == "freeze-selection":
            seed_reveal = (
                args.seed_reveal_file.read_text(encoding="ascii").strip()
                if args.seed_reveal_file is not None else None
            )
            result = freeze_selection(
                organizer_input=args.input,
                ledger_path=args.ledger,
                review_export_manifest=args.review_export_manifest,
                policy_path=args.policy,
                selection_output=args.selection_output,
                plan_output=args.plan_output,
                blind_packets_output=args.blind_packets_output,
                seed_reveal=seed_reveal,
            )
        elif args.command == "start-session":
            result = start_session(
                plan_path=args.plan,
                blind_packets_path=args.blind_packets,
                roster_path=args.auditor_roster,
                output=args.output,
            )
        else:
            result = finalize_report(
                organizer_input=args.input,
                ledger_path=args.ledger,
                review_export_manifest=args.review_export_manifest,
                plan_path=args.plan,
                session_path=args.session,
                judgments_path=args.judgments,
                results_output=args.results_output,
                report_output=args.report_output,
            )
    except (AuditWorkflowError, gold.GoldBuildError, OSError, ValueError) as exc:
        print(f"Gold audit refused: {exc}", file=__import__("sys").stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
