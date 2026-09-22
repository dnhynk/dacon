"""Synthetic no-Claude execution tests for one sealed 20 x 5 shard."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest import mock

import pytest

from tools.independent_gold import claude_unlabeled_batch_executor as executor

REAL_VERIFIED_GATE = executor._verified_gate


def _fixture(tmp_path: Path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    stage_root = runs / "stage"
    stage_root.mkdir()
    (stage_root / "stage.bin").write_bytes(b"sealed synthetic stage")
    score = runs / "dev_gate" / "score_report.json"
    score.parent.mkdir()
    score.write_bytes(b"synthetic score report")
    output = runs / "new_shard"
    args = argparse.Namespace(staged_root=stage_root, shard_index=17,
                              score_report=score, output_dir=output, execute=False)
    monkeypatch.setattr(executor, "RUNS_ROOT", runs)
    monkeypatch.setattr(executor, "_source_snapshot", lambda: {"executor": "frozen"})
    stage = {
        "manifest_sha256": "stage-hash", "selected_ids_sha256": "selected-hash",
        "source_admission": {
            "status": "pass",
            "report_file_sha256": executor.base.file_sha256(executor.preparer.DEFAULT_PREFLIGHT),
            "record_rows_sha256": executor.base.file_sha256(
                executor.preparer.DEFAULT_PREFLIGHT.parent / "records.jsonl"),
        },
        "input_sha256": executor.base.file_sha256(score),
        "source_bundle": executor.base.source_bundle(), "mode": "source_lean",
        "rubric_source_sha256": executor.base.file_sha256(executor.base.RUBRIC_PATH),
        "rubric_projection_sha256": "rubric-projection",
        "system_sha256": "system-hash", "output_schema_sha256": "schema-hash",
        "batch_pilot_source_sha256": "pilot-hash", "preparer_source_sha256": "preparer-hash",
    }
    batches = []
    for index in range(20):
        ids = [f"R-{index:02d}-{number}" for number in range(5)]
        records = [{"id": record_id} for record_id in ids]
        contexts = [{"context_sha256": f"context-{record_id}"} for record_id in ids]
        batches.append({
            "index": index, "ids": ids, "records": records, "contexts": contexts,
            "prompt": f"prompt-{index}", "system": "system", "schema": "{}",
            "stage_manifest": {
                "manifest_sha256": f"stage-batch-{index}",
                "system_sha256": "system-hash", "prompt_sha256": f"prompt-hash-{index}",
                "output_schema_sha256": "schema-hash",
                "full_context_sha256_by_id": {record_id: f"context-{record_id}" for record_id in ids},
            },
        })
    frozen = {"max_budget_usd": 3.0, "timeout_seconds": 30.0}
    cli_path = runs / "synthetic_claude.exe"
    cli_path.write_bytes(b"synthetic executable; never launched")
    cli = {"resolved_executable": str(cli_path),
           "executable_sha256": executor.base.file_sha256(cli_path)}
    monkeypatch.setattr(executor, "_verified_stage", lambda *_: (stage, batches))
    monkeypatch.setattr(executor, "_verified_gate", lambda *_: (
        {"status": "gate1_minimum_dev_and_structural_source_pass_not_gold"}, frozen, cli,
    ))
    monkeypatch.setattr(executor.preparer, "INPUT", score)
    return args, batches


def _assert_receipt(batch_dir: Path, status: str):
    marker = json.loads((batch_dir / "start_marker.json").read_text(encoding="utf-8"))
    receipt = json.loads((batch_dir / "receipt.json").read_text(encoding="utf-8"))
    assert marker["marker_sha256"] == executor.base.sha256_object({
        key: value for key, value in marker.items() if key != "marker_sha256"
    })
    assert receipt["receipt_sha256"] == executor.base.sha256_object({
        key: value for key, value in receipt.items() if key != "receipt_sha256"
    })
    assert receipt["start_marker_sha256"] == marker["marker_sha256"]
    assert receipt["source_archive_manifest_sha256"] == marker["source_archive_manifest_sha256"]
    assert receipt["status"] == status
    assert executor.base.file_sha256(batch_dir / "stdout.bin") == receipt["stdout_sha256"]
    assert executor.base.file_sha256(batch_dir / "stderr.bin") == receipt["stderr_sha256"]
    return receipt


def test_default_is_plan_only_with_zero_model_calls(tmp_path, monkeypatch):
    args, _ = _fixture(tmp_path, monkeypatch)
    with mock.patch.object(executor.base, "_invoke", side_effect=AssertionError("model called")):
        result = executor.run(args)
    assert result["status"] == "gate_checked_plan_only_no_model_call"
    assert result["model_calls"] == 0
    assert not args.output_dir.exists()


def test_gate_failure_refuses_before_any_output_or_model_call(tmp_path, monkeypatch):
    args, _ = _fixture(tmp_path, monkeypatch)
    args.execute = True
    monkeypatch.setattr(executor, "_verified_gate", lambda *_: (_ for _ in ()).throw(
        executor.BatchExecutionError("dev200 gate failed")))
    with mock.patch.object(executor.base, "_invoke", side_effect=AssertionError("model called")):
        with pytest.raises(executor.BatchExecutionError, match="gate failed"):
            executor.run(args)
    assert not args.output_dir.exists()


def test_output_must_not_be_inside_staged_or_gate_tree(tmp_path, monkeypatch):
    args, _ = _fixture(tmp_path, monkeypatch)
    args.output_dir = args.staged_root / "new_attempt"
    with mock.patch.object(executor.base, "_invoke", side_effect=AssertionError("model called")):
        with pytest.raises(executor.BatchExecutionError, match="trees must be disjoint"):
            executor.run(args)
    assert not args.output_dir.exists()


def test_safety_error_stops_after_one_started_batch_and_never_resumes(tmp_path, monkeypatch):
    args, _ = _fixture(tmp_path, monkeypatch)
    args.execute = True
    calls = []
    def failure(**kwargs):
        calls.append(kwargs)
        return 1, b'{"is_error":true,"terminal_reason":"api_error"}', b"session limit", None
    monkeypatch.setattr(executor.base, "_invoke", failure)
    result = executor.run(args)
    assert result["status"] == "stopped_safety_not_gold"
    assert result["batches_attempted"] == len(calls) == 1
    batch = args.output_dir / "batches" / "batch-00"
    receipt = _assert_receipt(batch, "safety_error")
    assert receipt["reported_cost_usd"] is None
    assert not (args.output_dir / "batches" / "batch-01").exists()
    with pytest.raises(executor.BatchExecutionError, match="fresh child"):
        executor.run(args)
    assert len(calls) == 1


def test_batch_content_error_stops_without_recalling_or_padding(tmp_path, monkeypatch):
    args, batches = _fixture(tmp_path, monkeypatch)
    args.execute = True
    invoked = []
    def synthetic(**kwargs):
        invoked.append(kwargs)
        return 0, b"synthetic CLI success", b"", None
    monkeypatch.setattr(executor.base, "_invoke", synthetic)
    monkeypatch.setattr(executor.base, "audit_envelope", lambda *_: {
        "total_cost_usd": 1.25, "usage": {"input_tokens": 1},
        "modelUsage": {executor.base.OBSERVED_MODEL: {}},
        "structured_output": {"records": [
            {"record_id": record_id, "annotation": {}}
            for record_id in batches[0]["ids"]
        ], "unexpected": True},
    })
    result = executor.run(args)
    assert result["status"] == "stopped_content_not_gold"
    assert result["records_content_error"] == 5
    assert len(invoked) == 1
    receipt = _assert_receipt(args.output_dir / "batches" / "batch-00", "batch_content_error")
    assert receipt["row_hashes"] == {}
    assert receipt["reported_cost_usd"] == 1.25


def test_source_archive_failure_prevents_first_start_marker_and_model_call(tmp_path, monkeypatch):
    args, _ = _fixture(tmp_path, monkeypatch)
    args.execute = True
    monkeypatch.setattr(executor, "_archive_sources", lambda *_: (_ for _ in ()).throw(
        executor.BatchExecutionError("source archive drift")))
    with mock.patch.object(executor.base, "_invoke", side_effect=AssertionError("model called")):
        with pytest.raises(executor.BatchExecutionError, match="archive drift"):
            executor.run(args)
    assert (args.output_dir / "run_manifest.json").is_file()
    assert not (args.output_dir / "batches").exists()


def test_twenty_synthetic_successes_complete_100_rows_and_receipts(tmp_path, monkeypatch):
    args, batches = _fixture(tmp_path, monkeypatch)
    args.execute = True
    calls = []
    def synthetic(**kwargs):
        calls.append(kwargs)
        return 0, f"synthetic-{len(calls)}".encode(), b"", None
    monkeypatch.setattr(executor.base, "_invoke", synthetic)
    def audited(stdout):
        index = int(stdout.decode().split("-")[1]) - 1
        return {
            "total_cost_usd": 0.1, "usage": {"input_tokens": 1},
            "modelUsage": {executor.base.OBSERVED_MODEL: {}},
            "structured_output": {"records": [
                {"record_id": record_id, "annotation": {"synthetic": True}}
                for record_id in batches[index]["ids"]
            ]},
        }
    monkeypatch.setattr(executor.base, "audit_envelope", audited)
    monkeypatch.setattr(executor.base, "validate_content", lambda _, record, __: (
        {"cells": [{"item": f"v{item}",
                    "label": "U" if item == 1 and record["id"] == batches[0]["ids"][0] else 0}
                   for item in range(1, 25)]},
        {"decisions": {}}, {"warning": None},
    ))
    result = executor.run(args)
    assert result["status"] == "complete_provisional_unqualified_not_gold"
    assert result["batches_attempted"] == len(calls) == 20
    assert result["records_ok"] == 100
    assert result["records_content_error"] == result["safety_errors"] == 0
    assert result["unresolved_cells"] == 1
    assert len(list(args.output_dir.glob("batches/batch-*/*.row.json"))) == 100
    archive = json.loads((args.output_dir / "source_archive" / "manifest.json").read_text(encoding="utf-8"))
    assert archive["archive_manifest_sha256"] == executor.base.sha256_object({
        key: value for key, value in archive.items() if key != "archive_manifest_sha256"
    })
    archived_names = {entry["path"] for entry in archive["files"]}
    assert {
        "tools/independent_gold/claude_batch_pilot.py",
        "tools/independent_gold/claude_unlabeled_batch_prepare.py",
        "tools/independent_gold/claude_unlabeled_batch_executor.py",
        "tools/independent_gold/claude_batch_dev_gate.py",
        "tools/independent_gold/rubric_v1.md",
    } <= archived_names
    for index in range(20):
        receipt = _assert_receipt(args.output_dir / "batches" / f"batch-{index:02d}", "ok")
        assert len(receipt["row_hashes"]) == 5
        assert receipt["unresolved_cells"] == (1 if index == 0 else 0)
    summary = json.loads((args.output_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["summary_sha256"] == executor.base.sha256_object({
        key: value for key, value in summary.items() if key != "summary_sha256"
    })


@pytest.mark.parametrize("drift", ["cli", "source_extra", "archive"])
def test_drift_after_first_success_blocks_second_model_call(tmp_path, monkeypatch, drift):
    args, batches = _fixture(tmp_path, monkeypatch)
    args.execute = True
    changed = {"after_first": False}
    original_entries = executor._source_archive_files
    if drift == "source_extra":
        def changed_entries(bundle):
            entries = original_entries(bundle)
            if changed["after_first"]:
                entries[-1] = {**entries[-1], "sha256": "0" * 64}
            return entries
        monkeypatch.setattr(executor, "_source_archive_files", changed_entries)
    calls = []
    def first_only(**kwargs):
        calls.append(kwargs)
        changed["after_first"] = True
        if drift == "cli":
            cli_path = Path(kwargs["executable"])
            cli_path.write_bytes(cli_path.read_bytes() + b" changed")
        elif drift == "archive":
            archived = (args.output_dir / "source_archive" / "tools" /
                        "independent_gold" / "claude_batch_pilot.py")
            archived.write_bytes(archived.read_bytes() + b" changed")
        return 0, b"first-success", b"", None
    monkeypatch.setattr(executor.base, "_invoke", first_only)
    monkeypatch.setattr(executor.base, "audit_envelope", lambda *_: {
        "total_cost_usd": 0.1, "usage": {"input_tokens": 1},
        "modelUsage": {executor.base.OBSERVED_MODEL: {}},
        "structured_output": {"records": [
            {"record_id": record_id, "annotation": {}}
            for record_id in batches[0]["ids"]
        ]},
    })
    monkeypatch.setattr(executor.base, "validate_content", lambda _, record, __: (
        {"cells": [{"item": "v1", "label": 0}]}, {"decisions": {}}, {},
    ))
    with pytest.raises(executor.BatchExecutionError, match="changed|archive"):
        executor.run(args)
    assert len(calls) == 1
    _assert_receipt(args.output_dir / "batches" / "batch-00", "ok")
    assert not (args.output_dir / "batches" / "batch-01").exists()
    assert not (args.output_dir / "run_summary.json").exists()


def test_gate_tuple_mismatch_is_rejected_before_cli_identity_probe(tmp_path, monkeypatch):
    args, _ = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(executor, "_verified_gate", REAL_VERIFIED_GATE)
    stage = {
        "mode": "source_lean", "rubric_projection_sha256": "rubric-projection",
        "rubric_source_sha256": "rubric-source", "system_sha256": "system-hash",
        "source_bundle": {"bundle_sha256": "bundle-hash"},
        "batch_pilot_source_sha256": "pilot-hash",
    }
    (args.score_report.parent / "plan.json").write_bytes(b"{}\n")
    frozen = {key: "wrong" for key in executor.dev_gate.scorer.FROZEN_FIELDS}
    frozen.update({"cli_executable_sha256": "a" * 64, "cli_version_output": "2.1.276 (Claude Code)"})
    monkeypatch.setattr(executor.dev_gate, "evaluate", lambda *_: {
        "candidate_gate1_minimum_pass": True,
        "status": "gate1_minimum_dev_and_structural_source_pass_not_gold",
        "qualified_for_gold_generation": False,
    })
    monkeypatch.setattr(executor.dev_gate, "_frozen_tuple_from_plan", lambda *_: frozen)
    with mock.patch.object(executor.base, "resolve_cli", side_effect=AssertionError("CLI probe")):
        with pytest.raises(executor.BatchExecutionError, match="tuple differs"):
            executor._verified_gate(args.score_report, stage)


def test_exact_dev_tuple_and_cli_identity_can_be_verified_without_model_call(tmp_path, monkeypatch):
    args, _ = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(executor, "_verified_gate", REAL_VERIFIED_GATE)
    stage = {
        "mode": "source_lean", "rubric_projection_sha256": "projected-rubric",
        "rubric_source_sha256": "source-rubric", "system_sha256": "system-hash",
        "source_bundle": executor.base.source_bundle(),
        "batch_pilot_source_sha256": executor.base.file_sha256(Path(executor.pilot.__file__)),
    }
    cli = {"requested_executable": "claude", "resolved_executable": "synthetic-no-model",
           "executable_sha256": "a" * 64, "version_output": "2.1.276 (Claude Code)"}
    (args.score_report.parent / "plan.json").write_bytes(
        (executor.base.canonical_json({"cli": cli}) + "\n").encode("utf-8"))
    frozen = {
        "mode": stage["mode"], "batch_size": 5,
        "model": executor.base.REQUESTED_MODEL,
        "observed_model": executor.base.OBSERVED_MODEL,
        "rubric_sha256": stage["rubric_projection_sha256"],
        "rubric_source_sha256": stage["rubric_source_sha256"],
        "system_sha256": stage["system_sha256"],
        "input_sha256": executor.base.file_sha256(executor.pilot.DEV_INPUT),
        "source_bundle": stage["source_bundle"],
        "pilot_source_sha256": stage["batch_pilot_source_sha256"],
        "max_budget_usd": 3.0, "timeout_seconds": 30.0,
        "cli_executable_sha256": cli["executable_sha256"],
        "cli_version_output": cli["version_output"],
    }
    proof = {
        "candidate_gate1_minimum_pass": True,
        "status": "gate1_minimum_dev_and_structural_source_pass_not_gold",
        "qualified_for_gold_generation": False,
    }
    monkeypatch.setattr(executor.dev_gate, "evaluate", lambda *_: proof)
    monkeypatch.setattr(executor.dev_gate, "_frozen_tuple_from_plan", lambda *_: frozen)
    with mock.patch.object(executor.base, "resolve_cli", return_value=cli) as probe:
        observed_proof, observed_tuple, observed_cli = REAL_VERIFIED_GATE(args.score_report, stage)
    probe.assert_called_once_with("claude")
    assert (observed_proof, observed_tuple, observed_cli) == (proof, frozen, cli)
