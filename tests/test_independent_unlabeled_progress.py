"""Synthetic, metadata-only tests for the read-only unlabeled progress report."""

from __future__ import annotations

import json
import builtins
from pathlib import Path
import subprocess
import sys
import types

from tools.independent_gold import unlabeled_progress as progress


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _manifest(root: Path, name: str, provider: str, ids: list[str], bundle: str = "bundle-a") -> Path:
    run = root / name
    run.mkdir(parents=True)
    common = {"phase": "unlabeled_20000"}
    if provider == "sol":
        common.update({
            "cohort_plan": {"selected_ids": ids},
            "record_input": {"path": "D:\\data\\train_unlabeled.jsonl.gz", "sha256": "input-a"},
            "imported_source_bundle": {"bundle_sha256": bundle},
        })
    else:
        common.update({
            "selected_ids": ids, "input_path": "/data/train_unlabeled.jsonl.gz",
            "input_sha256": "input-a", "source_bundle": {"bundle_sha256": bundle},
            "execute": True,
        })
    _write(run / "run_manifest.json", common)
    return run


def _receipt(run: Path, provider: str, record_id: str, kind: str, attempt: int = 1) -> None:
    if provider == "sol":
        value = {"id": record_id, "status": "ok" if kind == "ok" else "error",
                 "validation_errors": ["invalid_full_record_output: missing premise"] if kind == "content" else
                 (["tool_or_nonmessage_event_observed"] if kind == "safety" else []),
                 "event_audit": {"unsafe_items": []}}
    else:
        value = {"record_id": record_id, "status": "ok" if kind == "ok" else "error",
                 "error_class": kind if kind != "ok" else None}
    _write(run / "tasks" / record_id / "v1-24" / "attempts" / f"attempt-{attempt:03d}" / "receipt.json", value)


def _checkpoint(run: Path, record_id: str, *, changed: bool = False) -> None:
    cells = [{"item": f"v{i}", "label": "1" if changed and i == 1 else "0"} for i in range(1, 25)]
    path = run / "full_records.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"id": record_id, "status": "provisional_unqualified",
                                 "ledger": {"cells": cells}}) + "\n")


def test_provider_counts_conflicts_and_dev_exclusion(tmp_path: Path) -> None:
    sol = _manifest(tmp_path, "provisional_sol_shard000_of200_v1", "sol", ["a", "b"])
    _receipt(sol, "sol", "a", "ok")
    _receipt(sol, "sol", "b", "content")
    _checkpoint(sol, "a")
    claude = _manifest(tmp_path, "provisional_claude_shard000_of200_v1", "claude", ["a", "b"])
    _receipt(claude, "claude", "a", "ok")
    _receipt(claude, "claude", "b", "safety")
    _checkpoint(claude, "a", changed=True)
    _write(claude / "run_summary.json", {"records_selected": 2, "calls_attempted": 2,
                                          "tasks_ok": 1, "safety_errors": 1})
    dev = _manifest(tmp_path, "claude_dev200_full_v1", "claude", ["dev"])
    _receipt(dev, "claude", "dev", "ok")
    _checkpoint(dev, "dev")

    result = progress.collect(tmp_path)
    assert result["providers"]["sol"]["records"] == {
        "attempted_unique": 2, "ok_unique": 1,
        "ids_with_content_error_receipt": 1, "ids_with_safety_error_receipt": 0,
    }
    assert result["providers"]["claude"]["records"]["ids_with_safety_error_receipt"] == 1
    assert result["providers"]["claude"]["shards"]["partial"] == 1
    assert result["providers"]["sol"]["shards"]["partial"] == 1
    assert result["coverage"]["attempted_unique_any"] == 2
    assert result["coverage"]["ok_unique_any"] == 1
    assert result["coverage"]["decision_conflict_ids_cross_provider"] == 1
    assert result["excluded_non_unlabeled_directories"] == 1
    assert "dev" not in json.dumps(result)


def test_retries_duplicate_runs_and_verified_completion(tmp_path: Path) -> None:
    first = _manifest(tmp_path, "provisional_claude_shard001_of200_v1", "claude", ["a", "b"])
    _receipt(first, "claude", "a", "content")
    _receipt(first, "claude", "a", "ok", attempt=2)
    _receipt(first, "claude", "b", "ok")
    _checkpoint(first, "a")
    _checkpoint(first, "b")
    _write(first / "run_summary.json", {"records_selected": 2, "calls_attempted": 2,
                                         "tasks_ok": 2, "safety_errors": 0})
    second = _manifest(tmp_path, "provisional_claude_shard001_of200_v2", "claude", ["a", "b"], "bundle-b")
    _receipt(second, "claude", "a", "ok")
    _checkpoint(second, "a", changed=True)
    result = progress.collect(tmp_path)
    claude = result["providers"]["claude"]
    assert claude["runs"] == 2
    assert claude["receipt_attempts"] == {"ok": 3, "content": 1, "safety": 0}
    assert claude["records"]["attempted_unique"] == 2
    assert claude["shards"] == {"complete_verified": 1, "complete_inferred": 0, "partial": 1}
    assert result["coverage"]["selected_duplicate_ids_within_provider"]["claude"] == 2
    assert result["coverage"]["attempted_duplicate_ids_across_runs"]["claude"] == 1
    assert result["coverage"]["decision_conflict_ids_within_provider"]["claude"] == 1
    assert len(claude["source_bundles"]) == 2


def test_supervisor_shards_and_quota_opt_in(tmp_path: Path, monkeypatch) -> None:
    supervisor = tmp_path / "future_supervisor"
    _write(supervisor / "plan.json", {"schema_version": progress.SUPERVISOR_SCHEMA})
    shard = _manifest(supervisor / "shards", "shard-008-of-200", "claude", ["x"])
    _receipt(shard, "claude", "x", "ok")
    _checkpoint(shard, "x")
    _write(shard / "run_summary.json", {"records_selected": 1, "calls_attempted": 1,
                                         "tasks_ok": 1, "safety_errors": 0})
    import tools.independent_gold.codex_quota_read as quota
    calls: list[int] = []
    monkeypatch.setattr(quota, "read_weekly_quota", lambda: calls.append(1) or {"remaining": 50})
    result = progress.collect(tmp_path)
    assert calls == []
    assert result["providers"]["claude"]["shards"]["complete_verified"] == 1
    assert "codex_quota" not in result
    with_quota = progress.collect(tmp_path, with_codex_quota=True)
    assert calls == [1]
    assert with_quota["codex_quota"] == {"remaining": 50}


def test_fresh_recovery_run_is_counted_but_only_once_in_coverage(tmp_path: Path) -> None:
    first = _manifest(tmp_path, "provisional_claude_shard004_of200_v1", "claude", ["x"])
    _receipt(first, "claude", "x", "safety")
    recovery = _manifest(tmp_path, "recovery_claude_shard004_after_reset_v1", "claude", ["x"])
    _receipt(recovery, "claude", "x", "ok")
    _checkpoint(recovery, "x")
    result = progress.collect(tmp_path)
    assert result["providers"]["claude"]["runs"] == 2
    assert result["coverage"]["attempted_unique_any"] == 1
    assert result["coverage"]["ok_unique_any"] == 1
    assert result["coverage"]["attempted_duplicate_ids_across_runs"]["claude"] == 1


def test_invalid_source_manifest_is_excluded(tmp_path: Path) -> None:
    run = _manifest(tmp_path, "provisional_sol_shard000_of200_v1", "sol", ["x"])
    manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    manifest["record_input"]["path"] = "/data/dev_labels.jsonl"
    _write(run / "run_manifest.json", manifest)
    result = progress.collect(tmp_path)
    assert result["providers"]["sol"]["runs"] == 0
    assert result["coverage"]["attempted_unique_any"] == 0
    assert result["anomalies"]["count"] == 1


def test_direct_script_import_fallback_for_optional_quota(tmp_path: Path, monkeypatch) -> None:
    direct = subprocess.run(
        [sys.executable, "-B", str(Path(progress.__file__)), "--root", str(tmp_path)],
        capture_output=True, text=True, check=True,
    )
    assert "codex_quota" not in json.loads(direct.stdout)

    # Emulate direct-script sys.path, where the `tools` package is unavailable
    # but the sibling codex_quota_read.py remains importable.
    original_import = builtins.__import__
    def script_import(name, *args, **kwargs):
        if name == "tools.independent_gold.codex_quota_read":
            raise ModuleNotFoundError("No module named 'tools'", name="tools")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", script_import)
    quota_stub = types.ModuleType("codex_quota_read")
    quota_stub.read_weekly_quota = lambda: {"leftPercent": 61}
    monkeypatch.setitem(sys.modules, "codex_quota_read", quota_stub)
    assert progress.collect(tmp_path, with_codex_quota=True)["codex_quota"] == {"leftPercent": 61}
