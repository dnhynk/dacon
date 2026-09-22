"""CPU-only checks for post-inference batch-pilot diagnostics."""

import json

import pytest

from tools.independent_gold import claude_batch_pilot as pilot
from tools.independent_gold import diagnose_batch_dev as diagnostic


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_scores_only_receipted_rows_and_rejects_tampering(tmp_path):
    selected, total = pilot.select_dev_window(37, 2)
    assert total == 200
    _write_json(tmp_path / "run_manifest.json", {
        "schema_version": pilot.SCHEMA_VERSION,
        "phase": "blind_dev_batch_diagnostic_only",
        "input_sha256": diagnostic.scoring._sha256(pilot.DEV_INPUT),
        "selected_ids": selected,
        "start_index": 37,
        "batch_size": 2,
        "mode": "source_lean",
    })
    _write_json(tmp_path / "run_summary.json", {"safety_errors": 0, "records_content_error": 0})
    batch = tmp_path / "batch-000"
    batch.mkdir()
    stdout = b"test raw model envelope"
    (batch / "stdout.bin").write_bytes(stdout)
    row = {"id": selected[0], "ledger": {"cells": [
        {"item": item, "label": 0} for item in diagnostic.scoring.ITEMS
    ]}}
    _write_json(batch / f"{selected[0]}.row.json", row)
    _write_json(batch / "receipt.json", {
        "status": "partial_content_error",
        "stdout_sha256": pilot.base.sha256_bytes(stdout),
        "row_hashes": {selected[0]: pilot.base.sha256_object(row)},
    })
    result = diagnostic.diagnose(tmp_path)
    assert result["selected_records"] == 2
    assert result["start_index"] == 37
    assert result["scored_records"] == 1
    assert result["compared_cells"] == 24
    assert not result["complete"]
    row["ledger"]["cells"][0]["label"] = 1
    _write_json(batch / f"{selected[0]}.row.json", row)
    with pytest.raises(ValueError, match="row hash"):
        diagnostic.diagnose(tmp_path)


def test_mismatched_or_missing_dev_window_is_rejected_before_labels(tmp_path, monkeypatch):
    selected, _ = pilot.select_dev_window(37, 2)
    manifest = {
        "schema_version": pilot.SCHEMA_VERSION,
        "phase": "blind_dev_batch_diagnostic_only",
        "input_sha256": diagnostic.scoring._sha256(pilot.DEV_INPUT),
        "selected_ids": selected, "start_index": 38,
    }
    _write_json(tmp_path / "run_manifest.json", manifest)
    with monkeypatch.context() as patch:
        patch.setattr(diagnostic.scoring, "DEV_LABELS", tmp_path / "labels_should_not_be_opened.csv")
        with pytest.raises(ValueError, match="exact organizer dev window"):
            diagnostic.diagnose(tmp_path)
    manifest.pop("start_index")
    _write_json(tmp_path / "run_manifest.json", manifest)
    with pytest.raises(ValueError, match="invalid pilot dev window"):
        diagnostic.diagnose(tmp_path)


def test_wrong_input_hash_is_rejected_before_scoring(tmp_path):
    _write_json(tmp_path / "run_manifest.json", {
        "schema_version": pilot.SCHEMA_VERSION,
        "phase": "blind_dev_batch_diagnostic_only",
        "input_sha256": "0" * 64,
    })
    with pytest.raises(ValueError, match="input hash"):
        diagnostic.diagnose(tmp_path)
