"""No-model source and raw-receipt checks for the 100-record batch verifier."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.independent_gold import verify_claude_unlabeled_batch as verify
from tools.independent_gold import claude_unlabeled_batch_executor as executor
from tests.test_independent_claude_unlabeled_batch_executor import _fixture as executor_fixture


def test_direct_script_cli_imports_from_repository_root() -> None:
    script = Path(verify.__file__)
    result = subprocess.run(
        [sys.executable, "-B", str(script), "--help"],
        cwd=script.parent.parent.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--staged-root" in result.stdout


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((verify._canonical(value) + "\n").encode("utf-8"))


def _sealed(value: dict, key: str) -> dict:
    value[key] = verify._sha_object(value)
    return value


def _attempt_fixture(tmp_path: Path, monkeypatch, *, transport_error: bool = False):
    directory = tmp_path / "batch-00"
    directory.mkdir()
    ids = [f"SYN-{index}" for index in range(5)]
    records = [{"id": record_id} for record_id in ids]
    contexts = [{"context_sha256": f"context-{index}"} for index in range(5)]
    staged = {
        "manifest_sha256": "1" * 64,
        "system_sha256": "2" * 64,
        "prompt_sha256": "3" * 64,
        "output_schema_sha256": "4" * 64,
        "full_context_sha256_by_id": {
            record_id: context["context_sha256"] for record_id, context in zip(ids, contexts)
        },
    }
    material = {"index": 0, "ids": ids, "records": records, "contexts": contexts,
                "stage_manifest": staged}
    plan = {"manifest_sha256": "5" * 64, "max_budget_usd": 1.0}
    archive_sha = "6" * 64
    marker = _sealed({
        "schema_version": "dacon.independent.claude_unlabeled_batch_start.v1",
        "status": "started_unresolved_until_receipt",
        "run_manifest_sha256": plan["manifest_sha256"],
        "source_archive_manifest_sha256": archive_sha,
        "batch_index": 0, "batch_ids": ids,
        "staged_batch_manifest_sha256": staged["manifest_sha256"],
        "system_sha256": staged["system_sha256"],
        "prompt_sha256": staged["prompt_sha256"],
        "output_schema_sha256": staged["output_schema_sha256"],
        "context_sha256_by_id": staged["full_context_sha256_by_id"],
        "started_utc": "2026-09-19T00:00:00+00:00",
    }, "marker_sha256")
    _write(directory / "start_marker.json", marker)
    stdout = b"synthetic raw CLI output"
    stderr = b""
    (directory / "stdout.bin").write_bytes(stdout)
    (directory / "stderr.bin").write_bytes(stderr)
    rows = []
    if not transport_error:
        envelope = {
            "structured_output": {"records": [
                {"record_id": record_id, "annotation": {"synthetic": record_id}}
                for record_id in ids
            ]},
            "total_cost_usd": 0.5, "usage": {"input_tokens": 1},
            "modelUsage": {"synthetic": {"outputTokens": 1}},
        }
        monkeypatch.setattr(verify.base, "audit_envelope", lambda raw: envelope)

        def validated(envelope_input, record, context):
            cells = [{"item": item,
                      "label": "U" if record["id"] == ids[0] and item == "v1" else 0}
                     for item in (f"v{index}" for index in range(1, 25))]
            return {"cells": cells}, envelope_input["structured_output"], {"changed": False}

        monkeypatch.setattr(verify.base, "validate_content", validated)
        for index, (record, context) in enumerate(zip(records, contexts)):
            row = {
                "id": record["id"], "status": "provisional_unqualified",
                "ledger": validated({"structured_output": {"synthetic": record["id"]}}, record, context)[0],
                "structured_output": {"synthetic": record["id"]},
                "normalization": {"changed": False},
                "source_sha256": verify._sha_object(record),
                "full_context_sha256": context["context_sha256"],
                "batch_index": 0,
            }
            rows.append(row)
            _write(directory / f"{record['id']}.row.json", row)
        status, returncode, transport = "ok", 0, None
        cost, usage, model_usage = 0.5, envelope["usage"], envelope["modelUsage"]
    else:
        status, returncode, transport = "safety_error", 1, "transport_failure"
        cost = usage = model_usage = None
    receipt = _sealed({
        "schema_version": "dacon.independent.claude_unlabeled_batch_receipt.v1",
        "status": status,
        "run_manifest_sha256": plan["manifest_sha256"],
        "source_archive_manifest_sha256": archive_sha,
        "start_marker_sha256": marker["marker_sha256"],
        "batch_index": 0, "batch_ids": ids,
        "staged_batch_manifest_sha256": staged["manifest_sha256"],
        "system_sha256": staged["system_sha256"],
        "prompt_sha256": staged["prompt_sha256"],
        "output_schema_sha256": staged["output_schema_sha256"],
        "context_sha256_by_id": staged["full_context_sha256_by_id"],
        "returncode": returncode, "transport_error": transport,
        "stdout_sha256": verify._sha_bytes(stdout),
        "stderr_sha256": verify._sha_bytes(stderr),
        "reported_cost_usd": cost,
        "usage": usage, "model_usage": model_usage,
        "errors": {} if not transport_error else {"batch": "transport or nonzero Claude CLI return"},
        "row_hashes": {row["id"]: verify._sha_object(row) for row in rows},
        "unresolved_cells": 0 if transport_error else 1,
    }, "receipt_sha256")
    _write(directory / "receipt.json", receipt)
    return directory, plan, material, marker, archive_sha


def test_raw_success_replay_preserves_U_and_provisional_status(tmp_path, monkeypatch):
    directory, plan, material, marker, archive_sha = _attempt_fixture(tmp_path, monkeypatch)
    assert verify._started_marker(directory, plan, material, archive_sha) == marker
    result = verify._replay_attempt(directory, plan, material, marker, archive_sha)
    assert result["status"] == "ok"
    assert result["rows"] == 5
    assert result["u_cells"] == 1
    assert result["content_error"] == 0


def test_changed_raw_bytes_fail_before_content_replay(tmp_path, monkeypatch):
    directory, plan, material, marker, archive_sha = _attempt_fixture(tmp_path, monkeypatch)
    (directory / "stdout.bin").write_bytes(b"changed raw CLI bytes")
    with pytest.raises(verify.BatchVerificationError, match="stdout/stderr hash"):
        verify._replay_attempt(directory, plan, material, marker, archive_sha)


def test_changed_provisional_row_fails_raw_replay(tmp_path, monkeypatch):
    directory, plan, material, marker, archive_sha = _attempt_fixture(tmp_path, monkeypatch)
    path = directory / "SYN-0.row.json"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["ledger"]["cells"][0]["label"] = 0
    _write(path, row)
    with pytest.raises(verify.BatchVerificationError, match="replayed provisional row"):
        verify._replay_attempt(directory, plan, material, marker, archive_sha)


def test_transport_failure_receipt_is_safety_stop_not_zero(tmp_path, monkeypatch):
    directory, plan, material, marker, archive_sha = _attempt_fixture(
        tmp_path, monkeypatch, transport_error=True,
    )
    result = verify._replay_attempt(directory, plan, material, marker, archive_sha)
    assert result["status"] == "safety_error"
    assert result["rows"] == 0
    assert result["content_error"] == 0
    assert result["safety_error"] == 1


def test_source_reconstruction_detects_missing_or_altered_supplied_text():
    record = {"id": "R1", "docs": [{"doc_id": "D0", "type": "notice", "text": "first second"}],
              "input_completeness": {"complete": False}, "dropped_doc_counts": {"task": 1}}
    context = {
        "allowed_span_registry": {
            "S1": {"quote": "first "}, "S2": {"quote": "second"},
        },
        "organizer_record": {
            "record_sha256": verify._sha_object(record),
            "documents": [{"doc_index": 0, "doc_id": "D0", "doc_type": "notice",
                           "chars": 12, "sha256": verify._sha_text("first second"),
                           "span_ids": ["S1", "S2"]}],
        },
        "source_completeness": {"input_completeness": record["input_completeness"],
                                "dropped_doc_counts": record["dropped_doc_counts"]},
    }
    verify._reconstruct_docs(record, context)
    context["allowed_span_registry"]["S2"]["quote"] = "other"
    with pytest.raises(verify.BatchVerificationError, match="not reconstructed losslessly"):
        verify._reconstruct_docs(record, context)


def test_execution_source_archive_requires_all_exact_frozen_bytes(tmp_path):
    bundle = verify.base.source_bundle()
    entries = verify._source_archive_files(bundle)
    paths = {entry["path"] for entry in entries}
    assert "tools/independent_gold/claude_batch_pilot.py" in paths
    assert "tools/independent_gold/claude_unlabeled_batch_prepare.py" in paths
    assert "tools/independent_gold/claude_unlabeled_batch_executor.py" in paths
    root = tmp_path / "execution"
    archive = root / "source_archive"
    for entry in entries:
        relative = Path(*entry["path"].split("/"))
        source = verify.ROOT / relative
        frozen = archive / relative
        frozen.parent.mkdir(parents=True, exist_ok=True)
        frozen.write_bytes(source.read_bytes())
    plan = {"manifest_sha256": "a" * 64, "source_bundle": bundle,
            "source_archive_files": entries}
    manifest = _sealed({
        "schema_version": "dacon.independent.claude_unlabeled_batch_source_archive.v1",
        "archive_kind": "exact_source_bytes_before_first_model_call",
        "run_manifest_sha256": plan["manifest_sha256"],
        "source_bundle_sha256": bundle["bundle_sha256"], "files": entries,
    }, "archive_manifest_sha256")
    _write(archive / "manifest.json", manifest)
    assert verify._verify_source_archive(root, plan) == manifest["archive_manifest_sha256"]
    damaged = archive / Path(*entries[0]["path"].split("/"))
    damaged.write_bytes(b"damaged")
    with pytest.raises(verify.BatchVerificationError, match="archived source byte mismatch"):
        verify._verify_source_archive(root, plan)


def _prefix_fixture(tmp_path, monkeypatch, *, receipts: int, attempted: int,
                    stopped_safety: bool = False):
    runs = tmp_path / "runs"
    root = runs / "execution"
    stage_root = runs / "stage"
    stage_root.mkdir(parents=True)
    (stage_root / "stage.txt").write_text("frozen stage", encoding="utf-8")
    root.mkdir(parents=True)
    _write(root / "run_manifest.json", {"synthetic": True})
    _write(root / "source_archive" / "manifest.json", {"synthetic": True})
    score_path = runs / "score" / "score_report.json"
    _write(score_path, {"synthetic": True})
    for index in range(attempted):
        batch = root / "batches" / f"batch-{index:02d}"
        _write(batch / "start_marker.json", {"synthetic": True})
        if index < receipts:
            _write(batch / "receipt.json", {"synthetic": True})
    plan = {"manifest_sha256": "a" * 64,
            "score_report_path": str(score_path),
            "score_report_sha256": verify._sha_file(score_path),
            "gate_tree_sha256": verify._tree_hash(score_path.parent),
            "source_code_sha256": {"synthetic": "frozen"},
            "source_bundle": {"synthetic": True},
            "source_archive_files": []}
    stage = {"staged_root": str(stage_root),
             "staged_tree_sha256": verify._tree_hash(stage_root),
             "staged_run_manifest_sha256": "c" * 64,
             "shard_index": 0, "mode": "source_lean",
             "organizer_declared_incomplete": 1, "records_with_dropped_docs": 1}
    materials = [{"index": index} for index in range(20)]
    monkeypatch.setattr(verify, "RUNS", runs)
    monkeypatch.setattr(verify, "_verify_staged_with_material", lambda _: (stage, materials))
    monkeypatch.setattr(verify, "_execution_plan", lambda *_: plan)
    monkeypatch.setattr(verify, "_verify_source_archive", lambda *_: "b" * 64)
    monkeypatch.setattr(verify, "_source_snapshot", lambda: {"synthetic": "frozen"})
    monkeypatch.setattr(verify, "_source_archive_files", lambda _: [])
    monkeypatch.setattr(verify, "_started_marker", lambda *_: {"marker_sha256": "d" * 64})

    def replay(_, __, material, ___, ____):
        index = material["index"]
        if stopped_safety and index == receipts - 1:
            return {"status": "safety_error", "rows": 0, "content_error": 0,
                    "safety_error": 1, "reported_cost_usd": 0.0, "u_cells": 0}
        return {"status": "ok", "rows": 5, "content_error": 0,
                "safety_error": 0, "reported_cost_usd": 0.5,
                "u_cells": 1 if index == 0 else 0}

    monkeypatch.setattr(verify, "_replay_attempt", replay)
    return root, stage_root, plan


def test_unreceipted_attempt_keeps_verified_prefix_and_missing_cells_distinct(tmp_path, monkeypatch):
    root, stage_root, _ = _prefix_fixture(tmp_path, monkeypatch, receipts=1, attempted=2)
    result = verify.verify_execution(stage_root, root)
    assert result["status"] == "interrupted_unsealed_attempt_not_gold"
    assert result["verified_prefix_batches"] == 1
    assert result["verified_prefix_records"] == 5
    assert result["u_cells_in_verified_prefix"] == 1
    assert result["unreceipted_attempt_batch"] == 1
    assert result["unverified_planned_records"] == 95
    assert result["unverified_planned_cells"] == 95 * 24
    assert result["gold_qualification"] is False


def test_safety_stop_summary_must_bind_U_and_archive_without_gold(tmp_path, monkeypatch):
    root, stage_root, plan = _prefix_fixture(
        tmp_path, monkeypatch, receipts=2, attempted=2, stopped_safety=True,
    )
    summary = _sealed({
        "schema_version": "dacon.independent.claude_unlabeled_batch_summary.v1",
        "status": "stopped_safety_not_gold",
        "run_manifest_sha256": plan["manifest_sha256"],
        "source_archive_manifest_sha256": "b" * 64,
        "shard_index": 0, "batches_planned": 20, "batches_attempted": 2,
        "records_planned": 100, "records_ok": 5,
        "records_content_error": 0, "safety_errors": 1,
        "unresolved_cells": 1,
        "reported_cost_usd": 0.5,
        "resume_policy": "disabled_manual_reconciliation_required",
    }, "summary_sha256")
    path = root / "run_summary.json"
    _write(path, summary)
    result = verify.verify_execution(stage_root, root)
    assert result["status"] == "stopped_safety_with_verified_prefix_not_gold"
    assert result["verified_prefix_records"] == 5
    assert result["u_cells_in_verified_prefix"] == 1
    summary["unresolved_cells"] = 0
    _write(path, _sealed({key: value for key, value in summary.items()
                          if key != "summary_sha256"}, "summary_sha256"))
    with pytest.raises(verify.BatchVerificationError, match="summary differs"):
        verify.verify_execution(stage_root, root)


def test_complete_100_rows_with_U_are_structural_provisional_not_binary_gold(tmp_path, monkeypatch):
    root, stage_root, plan = _prefix_fixture(tmp_path, monkeypatch,
                                              receipts=20, attempted=20)
    summary = _sealed({
        "schema_version": "dacon.independent.claude_unlabeled_batch_summary.v1",
        "status": "complete_provisional_unqualified_not_gold",
        "run_manifest_sha256": plan["manifest_sha256"],
        "source_archive_manifest_sha256": "b" * 64,
        "shard_index": 0, "batches_planned": 20, "batches_attempted": 20,
        "records_planned": 100, "records_ok": 100,
        "records_content_error": 0, "safety_errors": 0,
        "unresolved_cells": 1,
        "reported_cost_usd": 10.0,
        "resume_policy": "disabled_manual_reconciliation_required",
    }, "summary_sha256")
    _write(root / "run_summary.json", summary)
    result = verify.verify_execution(stage_root, root)
    assert result["status"] == "executed_100_provisional_rows_with_U_not_gold"
    assert result["structural_full_shard_verified"] is True
    assert result["provisional_pass_rows_eligible"] is True
    assert result["u_cells_in_structurally_replayed_rows"] == 1
    assert result["unverified_planned_cells"] == 0
    assert result["gold_qualification"] is False


def _cross_verify_executor_output(args, batches, monkeypatch):
    plan = verify._json_file(args.output_dir / "run_manifest.json")
    stage = {
        "staged_root": str(args.staged_root),
        "staged_tree_sha256": plan["staged_tree_sha256"],
        "staged_run_manifest_sha256": plan["staged_run_manifest_sha256"],
        "shard_index": plan["shard_index"], "mode": plan["mode"],
        "organizer_declared_incomplete": 0,
        "records_with_dropped_docs": 0,
    }
    monkeypatch.setattr(verify, "RUNS", args.output_dir.parent)
    monkeypatch.setattr(verify, "_verify_staged_with_material", lambda *_: (stage, batches))
    # Synthetic executor fixture has no real dev200 gate or organizer records;
    # raw stdout, archive, markers, receipts, rows, summary and U are genuine
    # executor-produced bytes and are all replayed by the verifier below.
    monkeypatch.setattr(verify, "_execution_plan", lambda *_: plan)
    monkeypatch.setattr(verify, "_source_snapshot", lambda: plan["source_code_sha256"])
    return verify.verify_execution(args.staged_root, args.output_dir)


def _raw_claude_envelope(structured_output):
    """Synthetic CLI bytes, audited by the real transport/model checker."""
    return verify._canonical({
        "type": "result", "subtype": "success", "is_error": False,
        "permission_denials": [], "subagent_stats": {"spawned": 0},
        "usage": {
            "input_tokens": 10, "output_tokens": 5,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "server_tool_use": {"web_search_requests": 0},
        },
        "modelUsage": {
            executor.base.OBSERVED_MODEL: {
                "canonicalModel": executor.base.OBSERVED_MODEL,
                "provider": "firstParty", "inputTokens": 10,
                "outputTokens": 5, "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
            },
        },
        "total_cost_usd": 0.1,
        "structured_output": structured_output,
    }).encode("utf-8")


def test_synthetic_executor_to_verifier_complete_U_preserved(tmp_path, monkeypatch):
    args, batches = executor_fixture(tmp_path, monkeypatch)
    args.execute = True
    calls = []

    def raw_call(**kwargs):
        calls.append(kwargs)
        index = len(calls) - 1
        return 0, _raw_claude_envelope({"records": [
                {"record_id": record_id, "annotation": {"synthetic": True}}
                for record_id in batches[index]["ids"]
            ]}), b"", None

    def content(_, record, __):
        return ({"cells": [
            {"item": f"v{item}", "label": "U" if item == 1
             and record["id"] == batches[0]["ids"][0] else 0}
            for item in range(1, 25)
        ]}, {"decisions": {}}, {"warning": None})

    monkeypatch.setattr(executor.base, "_invoke", raw_call)
    monkeypatch.setattr(executor.base, "validate_content", content)
    assert executor.run(args)["batches_attempted"] == 20
    result = _cross_verify_executor_output(args, batches, monkeypatch)
    assert len(calls) == 20
    assert result["status"] == "executed_100_provisional_rows_with_U_not_gold"
    assert result["verified_prefix_records"] == 100
    assert result["u_cells_in_structurally_replayed_rows"] == 1
    assert result["provisional_pass_rows_eligible"] is True
    assert result["gold_qualification"] is False


@pytest.mark.parametrize("stop", ["safety", "content"])
def test_synthetic_executor_to_verifier_first_stop_has_zero_prefix_not_zero_labels(
    tmp_path, monkeypatch, stop,
):
    args, batches = executor_fixture(tmp_path, monkeypatch)
    args.execute = True
    calls = []

    def raw_call(**kwargs):
        calls.append(kwargs)
        return (1, b"CLI failed", b"session limit", None) if stop == "safety" else (
            0, _raw_claude_envelope({"records": [
                {"record_id": record_id, "annotation": {}}
                for record_id in batches[0]["ids"]
            ], "unexpected": True}), b"", None,
        )

    monkeypatch.setattr(executor.base, "_invoke", raw_call)
    executor.run(args)
    result = _cross_verify_executor_output(args, batches, monkeypatch)
    assert len(calls) == 1
    assert result["verified_prefix_records"] == 0
    assert result["unverified_planned_cells"] == 2400
    assert result["structural_full_shard_verified"] is False
    assert result["provisional_pass_rows_eligible"] is False
    assert result["gold_qualification"] is False
    assert result["status"] == (
        "stopped_safety_with_verified_prefix_not_gold" if stop == "safety"
        else "stopped_content_with_verified_prefix_not_gold"
    )
