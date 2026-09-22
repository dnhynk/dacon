"""Synthetic metadata-only tests; no organizer data or model calls."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from tools.independent_gold import claude_recovery_plan as recovery


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_canonical(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((recovery.bridge._canonical(value) + "\n").encode("utf-8"))


def _run(root: Path, name: str, ids: list[str], ok: list[str], errors: list[str], *, summary: bool = True) -> Path:
    run = root / name
    _write(run / "run_manifest.json", {
        "phase": "unlabeled_20000", "execute": True, "selected_ids": ids,
        "input_path": "/organizer/train_unlabeled.jsonl.gz", "input_sha256": "sourcehash",
        "context_mode": "compact", "max_budget_usd_per_call": 3,
        "source_bundle": {"bundle_sha256": "bundlehash"},
    })
    for record_id in ok + errors:
        attempt = run / "tasks" / record_id / "v1-24" / "attempts" / "attempt-001"
        attempt.mkdir(parents=True, exist_ok=True)
        stdout, stderr = b"mock stdout", b"mock stderr"
        (attempt / "stdout.bin").write_bytes(stdout)
        (attempt / "stderr.bin").write_bytes(stderr)
        _write(attempt / "receipt.json", {
            "record_id": record_id, "status": "ok" if record_id in ok else "error",
            "error_class": None if record_id in ok else "safety",
            "receipt_sha256": f"receipt-{record_id}" if record_id in ok else None,
            "stdout_sha256": sha256(stdout).hexdigest(), "stderr_sha256": sha256(stderr).hexdigest(),
            "usage": {"input_tokens": 0, "output_tokens": 0} if record_id in errors else {},
            "total_cost_usd": 0.0 if record_id in errors else 1.0,
        })
    if ok:
        path = run / "full_records.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for record_id in ok:
                handle.write(json.dumps({"id": record_id, "status": "provisional_unqualified",
                                         "receipt_sha256": f"receipt-{record_id}", "ledger": {"cells": [
                    {"item": f"v{i}", "label": "0"} for i in range(1, 25)]}}) + "\n")
    if summary:
        _write(run / "run_summary.json", {"records_selected": len(ids),
                                          "calls_attempted": len(ok) + len(errors), "tasks_ok": len(ok),
                                          "tasks_error": len(errors), "content_errors": 0,
                                          "safety_errors": len(errors)})
    return run


@pytest.fixture
def archive_gate(tmp_path: Path, monkeypatch) -> Path:
    archive = tmp_path / "source_archive"
    archive.mkdir()
    def verify_stub(run: Path, candidate: Path, snapshot: dict) -> dict:
        assert candidate == archive.resolve()
        return {"archive_path": str(archive.resolve()), "archive_binding": "exact_run",
                "verified_success_rows": len(snapshot["success_ids"]),
                "context_prompt_reconstructed": True}
    monkeypatch.setattr(recovery, "_verify_archived_successes", verify_stub)
    return archive


def test_recovery_excludes_only_archive_verified_success_and_verifies(tmp_path: Path, archive_gate: Path) -> None:
    source = _run(tmp_path, "provisional_claude_shard001_of200_v1", ["a", "b", "c", "d"], ["a"], ["b"])
    _run(tmp_path, "provisional_claude_shard001_of200_v2", ["b"], ["b"], [])
    output = tmp_path / "recovery_claude_new"
    plan = recovery.build(tmp_path, source, output, archive_gate)
    assert plan["success_excluded_count"] == 1
    assert plan["failed_attempt_ids"] == ["b"]
    assert plan["unattempted_ids"] == ["c", "d"]
    assert plan["recovery_ids"] == ["b", "c", "d"]
    assert plan["manual_execution_argv"][-1] == "--execute"  # not invoked by planner
    assert plan["manual_execution_argv"].count("--record-id") == 3
    plan_path = tmp_path / "frozen_plan.json"
    _write(plan_path, plan)
    assert recovery.verify(plan_path)["valid"] is True

    _run(tmp_path, "recovery_claude_shard001_after_reset_v1", ["c"], ["c"], [])
    assert recovery.verify(plan_path)["valid"] is True  # unrelated run is not reuse evidence
    failed_receipt = next(source.glob("tasks/b/v1-24/attempts/*/receipt.json"))
    changed = json.loads(failed_receipt.read_text(encoding="utf-8"))
    changed["note"] = "changed"
    _write(failed_receipt, changed)
    with pytest.raises(recovery.RecoveryPlanError, match="stale"):
        recovery.verify(plan_path)


def test_failed_attempt_and_usage_zero_preserved_as_source_only(tmp_path: Path, archive_gate: Path) -> None:
    source = _run(tmp_path, "provisional_claude_shard004_of200_v1", ["a", "b"], ["a"], ["b"])
    plan = recovery.build(tmp_path, source, tmp_path / "recovery_claude_new", archive_gate)
    assert plan["failed_attempt_ids"] == ["b"]
    assert plan["unattempted_ids"] == []
    receipt = next(source.glob("tasks/b/v1-24/attempts/*/receipt.json"))
    assert json.loads(receipt.read_text(encoding="utf-8"))["total_cost_usd"] == 0.0
    assert receipt.relative_to(source).as_posix() in plan["snapshots"][0]["files_sha256"]
    raw = receipt.parent / "stdout.bin"
    assert raw.relative_to(source).as_posix() in plan["snapshots"][0]["files_sha256"]
    plan_path = tmp_path / "plan.json"
    _write(plan_path, plan)
    raw.write_bytes(b"tampered")
    with pytest.raises(recovery.RecoveryPlanError, match="raw CLI output"):
        recovery.verify(plan_path)


def test_fail_closed_on_active_source_ambiguous_success_and_existing_output(tmp_path: Path, archive_gate: Path) -> None:
    active = _run(tmp_path, "provisional_claude_shard001_of200_v1", ["a", "b"], ["a"], [], summary=False)
    with pytest.raises(recovery.RecoveryPlanError, match="terminal summary"):
        recovery.build(tmp_path, active, tmp_path / "recovery_claude_fresh", archive_gate)
    _write(active / "run_summary.json", {"records_selected": 2, "calls_attempted": 1,
                                          "tasks_ok": 1, "tasks_error": 0})
    (tmp_path / "recovery_claude_fresh").mkdir()
    with pytest.raises(recovery.RecoveryPlanError, match="must be fresh"):
        recovery.build(tmp_path, active, tmp_path / "recovery_claude_fresh", archive_gate)
    (tmp_path / "recovery_claude_fresh").rmdir()
    receipt = next(active.glob("tasks/a/v1-24/attempts/*/receipt.json"))
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["status"] = "error"
    _write(receipt, value)
    with pytest.raises(recovery.RecoveryPlanError, match="invalid or duplicate checkpoint row"):
        recovery.build(tmp_path, active, tmp_path / "recovery_claude_fresh", archive_gate)


def test_dev_or_non_organizer_source_is_rejected(tmp_path: Path, archive_gate: Path) -> None:
    run = _run(tmp_path, "claude_dev200_full_v1", ["dev"], [], ["dev"])
    with pytest.raises(recovery.RecoveryPlanError, match="admitted named shard"):
        recovery.build(tmp_path, run, tmp_path / "recovery_claude_fresh", archive_gate)
    run = _run(tmp_path, "provisional_claude_shard001_of200_v1", ["x"], [], ["x"])
    manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    manifest["input_path"] = "/data/dev_labels.jsonl"
    _write(run / "run_manifest.json", manifest)
    with pytest.raises(recovery.RecoveryPlanError, match="admitted named shard"):
        recovery.build(tmp_path, run, tmp_path / "recovery_claude_fresh", archive_gate)


def test_archive_is_mandatory_and_shared_archive_proof_is_rejected(tmp_path: Path, monkeypatch) -> None:
    source = _run(tmp_path, "provisional_claude_shard004_of200_v1", ["a", "b"], ["a"], ["b"])
    output = tmp_path / "recovery_claude_fresh"
    with pytest.raises(recovery.RecoveryPlanError, match="source-archive is mandatory"):
        recovery.build(tmp_path, source, output)
    archive = tmp_path / "archive"
    archive.mkdir()
    monkeypatch.setattr(recovery, "_verify_archived_successes", lambda *_: {
        "archive_binding": "shared_source_bytes", "verified_success_rows": 1,
        "context_prompt_reconstructed": True,
    })
    with pytest.raises(recovery.RecoveryPlanError, match="not exact"):
        recovery.build(tmp_path, source, output, archive)


def test_completed_recovery_checks_exact_planned_subset(tmp_path: Path, archive_gate: Path) -> None:
    source = _run(tmp_path, "provisional_claude_shard004_of200_v1", ["a", "b", "c"], ["a"], ["b"])
    output = tmp_path / "recovery_claude_shard004_v1"
    plan = recovery.build(tmp_path, source, output, archive_gate)
    plan_path = tmp_path / "plan.json"
    _write(plan_path, plan)
    output.mkdir()
    manifest = {
        "phase": "unlabeled_20000", "execute": True,
        "selected_ids": ["b", "c"], "selected_ids_sha256": recovery.bridge._sha_object(["b", "c"]),
        "input_path": "/organizer/train_unlabeled.jsonl.gz", "input_sha256": "sourcehash",
        "shard_index": 0, "shard_count": 1,
    }
    manifest["manifest_sha256"] = recovery.bridge._sha_object(manifest)
    _write_canonical(output / "run_manifest.json", manifest)
    _write_canonical(output / "run_summary.json", {
        "run_manifest_sha256": manifest["manifest_sha256"],
        "records_selected": 2, "calls_attempted": 1,
    })
    proof = recovery.verify_for_completed_recovery(plan_path, output)
    assert proof["valid"] is True
    assert proof["recovery_ids"] == ["b", "c"]
    manifest["selected_ids"] = ["b"]
    manifest["selected_ids_sha256"] = recovery.bridge._sha_object(["b"])
    manifest["manifest_sha256"] = recovery.bridge._sha_object({k: v for k, v in manifest.items() if k != "manifest_sha256"})
    _write_canonical(output / "run_manifest.json", manifest)
    with pytest.raises(recovery.RecoveryPlanError, match="manifest differs"):
        recovery.verify_for_completed_recovery(plan_path, output)
