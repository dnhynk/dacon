"""Synthetic no-model safety checks for ten-window Claude dev supervision."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import pytest

from tools.independent_gold import claude_dev200_batch_supervisor as supervisor


def _args(root: Path, **changes):
    values = dict(output_root=root, mode="source_lean", batch_size=5,
                  max_budget_usd=3.0, timeout=30.0, claude_bin="fake-claude",
                  max_new_windows=1, execute=False, resume=False)
    values.update(changes)
    return argparse.Namespace(**values)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "runs" / "self_label_20000_20260918"
    root.mkdir(parents=True)
    monkeypatch.setattr(supervisor, "RUN_ROOT", root)
    monkeypatch.setattr(supervisor.pilot.base, "resolve_cli", lambda value: {
        "requested_executable": value,
        "resolved_executable": "fake-claude",
        "executable_sha256": "a" * 64,
        "version_output": "synthetic Claude CLI",
        "version_stderr_sha256": "b" * 64,
    })
    return root


def _fake_success(monkeypatch):
    invoked: list[int] = []
    verified: list[int] = []

    def invoke(argv):
        start = int(argv[argv.index("--start-index") + 1])
        run = Path(argv[argv.index("--output-dir") + 1])
        plan = supervisor._read_json(run.parents[1] / "plan.json")
        run.mkdir(parents=True)
        manifest = {
            "input_sha256": plan["input_sha256"],
            "selected_ids": plan["windows"][start // 20]["selected_ids"],
            "start_index": start, "batch_size": plan["batch_size"],
            "mode": plan["mode"], "max_budget_usd": plan["max_budget_usd"],
            "timeout_seconds": plan["timeout_seconds"], "model": plan["model"],
            "observed_model": plan["observed_model"],
            "rubric_sha256": plan["rubric_sha256"],
            "rubric_source_sha256": plan["rubric_source_sha256"],
            "system_sha256": plan["system_sha256"],
            "source_bundle": plan["source_bundle"],
            "pilot_source_sha256": plan["source_hashes"]["pilot"],
            "cli": plan["cli"], "execute": True, "qualification": "none_not_gold",
        }
        manifest["manifest_sha256"] = supervisor.pilot.base.sha256_object(manifest)
        supervisor._write_new(run / "run_manifest.json", manifest)
        invoked.append(start)
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    def archive(path, manifest):
        assert manifest.is_file()
        path.mkdir(parents=True)
        supervisor._write_new(path / "manifest.json", {"synthetic": True})

    def verify(run, archive):
        assert archive.joinpath("manifest.json").is_file()
        manifest = supervisor._read_json(run / "run_manifest.json")
        start = manifest["start_index"]
        verified.append(start)
        return {
            "status": "executed_rows_structurally_verified_not_gold",
            "gold_qualification": False, "model_calls_by_verifier": 0,
            "selected_records": 20, "records_ok": 20,
            "records_content_error": 0, "safety_errors": 0,
            "start_index": start, "run_manifest_sha256": manifest["manifest_sha256"],
        }

    monkeypatch.setattr(supervisor, "_invoke_pilot", invoke)
    monkeypatch.setattr(supervisor.archiver, "archive", archive)
    monkeypatch.setattr(supervisor.verifier, "verify", verify)
    return invoked, verified


def test_read_only_plan_never_writes_or_invokes_model(setup, monkeypatch):
    root = setup / "dry-plan"
    monkeypatch.setattr(supervisor, "_invoke_pilot", lambda argv: pytest.fail("model invoked"))
    result = supervisor.run(_args(root))
    assert result["status"] == "read_only_plan_no_model_calls"
    assert result["completed_windows"] == 0
    assert len(result["windows"]) == 10
    assert not root.exists()


def test_session_failure_is_not_automatically_retried(setup, monkeypatch):
    root = setup / "session-failure"
    calls = []

    def fail(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="session limit")

    monkeypatch.setattr(supervisor, "_invoke_pilot", fail)
    with pytest.raises(supervisor.SupervisorError, match="no automatic retry"):
        supervisor.run(_args(root, execute=True))
    assert len(calls) == 1
    assert (root / "attempts" / "window-000-019.json").is_file()
    assert (root / "failures" / "window-000.json").is_file()
    with pytest.raises(supervisor.SupervisorError, match="manual reconciliation"):
        supervisor.run(_args(root, execute=True, resume=True))
    assert len(calls) == 1


def test_resume_verified_prefix_and_reject_tamper(setup, monkeypatch):
    root = setup / "resumable"
    invoked, verified = _fake_success(monkeypatch)
    first = supervisor.run(_args(root, execute=True))
    assert first["completed_windows"] == 1
    assert invoked == [0]
    second = supervisor.run(_args(root, execute=True, resume=True))
    assert second["completed_windows"] == 2
    assert invoked == [0, 20]
    assert verified.count(0) >= 2
    (root / "runs" / "window-000-019" / "run_manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises((supervisor.SupervisorError, ValueError)):
        supervisor.run(_args(root, execute=True, resume=True))
    assert invoked == [0, 20]


def test_score_only_after_all_ten_verified_windows(setup, monkeypatch):
    root = setup / "all-ten"
    invoked, _ = _fake_success(monkeypatch)
    scored = []
    labels = setup / "synthetic_dev_labels.csv"
    labels.write_text("synthetic labels", encoding="utf-8")
    monkeypatch.setattr(supervisor.scorer, "DEV_LABELS", labels)

    def score(pairs):
        assert len(pairs) == 10
        scored.append(tuple(pairs))
        return {"status": "metrics_only_pending_quality_policy_not_gold",
                "macro_positive_f1": 0.9, "unresolved_cells": [],
                "qualified_for_gold_generation": False,
                "quality_threshold_applied": False, "model_calls_by_scorer": 0,
                "records": 200, "cells_verified": 4800,
                "official_dev_labels_sha256": supervisor._sha_file(labels)}

    monkeypatch.setattr(supervisor.scorer, "score_windows", score)
    result = supervisor.run(_args(root, execute=True, max_new_windows=10))
    assert invoked == list(supervisor.WINDOW_STARTS)
    assert len(scored) == 1
    assert result["status"] == "dev200_scored_not_gold"
    assert (root / "score_report.json").is_file()
    again = supervisor.run(_args(root, execute=True, resume=True, max_new_windows=10))
    assert again["completed_windows"] == 10
    assert len(scored) == 1
    labels.write_text("changed synthetic labels", encoding="utf-8")
    with pytest.raises(supervisor.SupervisorError, match="official dev labels changed"):
        supervisor.run(_args(root, execute=True, resume=True, max_new_windows=10))
    assert len(scored) == 1


def test_frozen_argument_change_refuses_resume(setup, monkeypatch):
    root = setup / "frozen"
    _fake_success(monkeypatch)
    supervisor.run(_args(root, execute=True))
    with pytest.raises(supervisor.SupervisorError, match="frozen plan"):
        supervisor.run(_args(root, resume=True, execute=True, mode="full_shared"))


def test_nested_root_cannot_corrupt_another_supervisor_artifact_tree(setup, monkeypatch):
    parent = setup / "parent"
    _fake_success(monkeypatch)
    supervisor.run(_args(parent, execute=True))
    nested = parent / "runs" / "window-000-019" / "nested-supervisor"
    with pytest.raises(supervisor.SupervisorError, match="nested inside another supervisor"):
        supervisor.run(_args(nested))
    assert not nested.exists()
