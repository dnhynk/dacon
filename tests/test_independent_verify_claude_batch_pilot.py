"""No-model, temporary-artifact tests for exact Claude batch pilot replay."""

from __future__ import annotations

import argparse
import copy
import itertools
import json
from pathlib import Path

import pytest

from tools.independent_gold import archive_source_bundle as archiver
from tools.independent_gold import claude_batch_pilot as pilot
from tools.independent_gold import verify_claude_batch_pilot as verifier


def _stdout(record_id: str, span_id: str, *, bad_wrapper: bool = False, cost: float = 1.0) -> bytes:
    decision = {
        "label": 0, "confidence": "H", "rationale": "제공된 공고문과 항목 구성요건을 대조했다",
        "premise_span_ids": [span_id], "exception_analysis": None,
        "completeness": "sufficient", "material_missing_information": None,
        "positive_evidence_span_id": None,
    }
    annotation = {
        "source_span_ids": [span_id],
        "decisions": {item: copy.deepcopy(decision) for item in pilot.base.ITEMS},
    }
    wire = {"records": [{"record_id": record_id, "annotation": annotation}]}
    if bad_wrapper:
        wire["unexpected"] = "schema violation"
    envelope = {
        "type": "result", "subtype": "success", "is_error": False,
        "result": "", "permission_denials": [],
        "usage": {"input_tokens": 2, "output_tokens": 4,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                  "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0}},
        "subagent_stats": {"spawned": 0},
        "modelUsage": {pilot.base.OBSERVED_MODEL: {
            "canonicalModel": pilot.base.OBSERVED_MODEL, "provider": "firstParty",
            "inputTokens": 2, "outputTokens": 4,
            "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
        }},
        "total_cost_usd": cost, "structured_output": wire,
    }
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@pytest.fixture
def artifacts(tmp_path: Path, monkeypatch):
    def make(*, execute: bool = False, bad_wrapper: bool = False, safety: bool = False,
             cost_exceed: bool = False, start_index: int = 0):
        run = tmp_path / "runs" / ("executed" if execute else "dry")
        record = next(itertools.islice(pilot.base._records(pilot.DEV_INPUT), start_index, None))
        context = pilot.base.full_record_context.build_full_record_context(record)
        span_id = next(span_id for span_id, entry in context["allowed_span_registry"].items() if entry["quote"].strip())
        stdout = _stdout(record["id"], span_id, bad_wrapper=bad_wrapper,
                         cost=4.0 if cost_exceed else 1.0)
        with monkeypatch.context() as patch:
            patch.setattr(pilot, "ROOT", tmp_path)
            patch.setattr(pilot.base, "resolve_cli", lambda _: {
                "requested_executable": "claude", "resolved_executable": "synthetic-no-model",
                "executable_sha256": "a" * 64, "version_output": "2.1.276 (Claude Code)",
                "version_stderr_sha256": "b" * 64,
            })
            if execute:
                patch.setattr(pilot.base, "_invoke", lambda **_: (1, b"session cap", b"", None)
                              if safety else (0, stdout, b"", None))
            pilot.run(argparse.Namespace(
                input=pilot.DEV_INPUT, output_dir=run, mode="source_lean",
                start_index=start_index, limit=1, batch_size=1,
                claude_bin="claude", timeout=30.0,
                max_budget_usd=3.0, execute=execute,
            ))
        archive = tmp_path / "runs" / ("archive_executed" if execute else "archive_dry")
        with monkeypatch.context() as patch:
            patch.setattr(archiver, "RUN_ROOT", tmp_path / "runs")
            archiver.archive(archive, run / "run_manifest.json")
        monkeypatch.setattr(verifier, "RUNS_ROOT", tmp_path / "runs")
        return run, archive
    return make


def test_dry_is_input_verified_but_not_model_quality(artifacts) -> None:
    run, archive = artifacts()
    report = verifier.verify(run, archive)
    assert report["status"] == "input_prepared_only_model_quality_unverified"
    assert report["selected_records"] == 1
    assert report["calls_attempted"] == 0
    assert report["archive"]["files_verified"] == 12


def test_nonzero_dev_window_is_rebuilt_from_exact_organizer_order(artifacts) -> None:
    run, archive = artifacts(start_index=37)
    manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    expected, _ = pilot.select_dev_window(37, 1)
    assert manifest["start_index"] == 37
    assert manifest["selected_ids"] == expected
    report = verifier.verify(run, archive)
    assert report["start_index"] == 37
    assert report["status"] == "input_prepared_only_model_quality_unverified"


def test_window_end_and_missing_start_are_rejected_before_replay(artifacts) -> None:
    run, _ = artifacts(start_index=199)
    path = run / "run_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["start_index"] == 199
    manifest["start_index"] = 200
    manifest["manifest_sha256"] = pilot.base.sha256_object({
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    })
    path.write_bytes((pilot.base.canonical_json(manifest) + "\n").encode("utf-8"))
    with pytest.raises(verifier.BatchVerificationError, match="dev window"):
        verifier._verify_manifest(run)
    manifest.pop("start_index")
    manifest["manifest_sha256"] = pilot.base.sha256_object({
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    })
    path.write_bytes((pilot.base.canonical_json(manifest) + "\n").encode("utf-8"))
    with pytest.raises(verifier.BatchVerificationError, match="manifest shape"):
        verifier._verify_manifest(run)


def test_missing_or_tampered_archive_is_rejected(artifacts, tmp_path: Path) -> None:
    run, archive = artifacts()
    with pytest.raises(verifier.BatchVerificationError, match="source archive missing"):
        verifier.verify(run, tmp_path / "not_an_archive")
    pilot_copy = archive / "tools" / "independent_gold" / "claude_batch_pilot.py"
    pilot_copy.write_bytes(pilot_copy.read_bytes() + b"\n# tampered")
    with pytest.raises(verifier.BatchVerificationError, match="archived source bytes differ"):
        verifier.verify(run, archive)


def test_archive_extra_pilot_source_is_mandatory(artifacts) -> None:
    run, archive = artifacts()
    manifest_path = archive / "manifest.json"
    frozen = json.loads(manifest_path.read_text(encoding="utf-8"))
    frozen.pop("extra_sources")
    manifest_path.write_bytes((pilot.base.canonical_json(frozen) + "\n").encode("utf-8"))
    with pytest.raises(verifier.BatchVerificationError, match="archive manifest shape"):
        verifier.verify(run, archive)


def test_prompt_mutation_is_rejected(artifacts) -> None:
    run, archive = artifacts()
    prompt = run / "batch-000" / "prompt.txt"
    prompt.write_bytes(prompt.read_bytes() + b"\nchanged")
    with pytest.raises(verifier.BatchVerificationError, match="stored batch input differs"):
        verifier.verify(run, archive)


def test_executed_raw_stdout_rows_and_receipt_replay(artifacts) -> None:
    run, archive = artifacts(execute=True)
    report = verifier.verify(run, archive)
    assert report["status"] == "executed_rows_structurally_verified_not_gold"
    assert report["records_ok"] == 1
    row = next((run / "batch-000").glob("*.row.json"))
    row.write_bytes(row.read_bytes() + b" ")
    with pytest.raises(verifier.BatchVerificationError, match="stored batch input differs"):
        verifier.verify(run, archive)


def test_raw_stdout_and_receipt_lineage_tampering_is_rejected(artifacts) -> None:
    run, archive = artifacts(execute=True)
    batch = run / "batch-000"
    stdout = batch / "stdout.bin"
    original = stdout.read_bytes()
    stdout.write_bytes(original.replace(b'"total_cost_usd":1.0', b'"total_cost_usd":2.0'))
    with pytest.raises(verifier.BatchVerificationError, match="receipt differs"):
        verifier.verify(run, archive)
    stdout.write_bytes(original)
    receipt = batch / "receipt.json"
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["prompt_sha256"] = "0" * 64
    receipt.write_bytes((pilot.base.canonical_json(value) + "\n").encode("utf-8"))
    with pytest.raises(verifier.BatchVerificationError, match="receipt differs"):
        verifier.verify(run, archive)


def test_content_error_and_safety_are_not_quality_passes(artifacts) -> None:
    content_run, content_archive = artifacts(execute=True, bad_wrapper=True)
    content = verifier.verify(content_run, content_archive)
    assert content["status"] == "failed_content_or_incomplete"
    assert content["records_content_error"] == 1


def test_safety_receipt_stays_failed(artifacts) -> None:
    run, archive = artifacts(execute=True, safety=True)
    report = verifier.verify(run, archive)
    assert report["status"] == "failed_safety"
    assert report["safety_errors"] == 1


def test_reported_cost_cap_safety_is_replayed(artifacts) -> None:
    run, archive = artifacts(execute=True, cost_exceed=True)
    report = verifier.verify(run, archive)
    assert report["status"] == "failed_safety"
    assert report["safety_errors"] == 1
