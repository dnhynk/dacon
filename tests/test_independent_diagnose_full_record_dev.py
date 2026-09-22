"""The dev diagnostic remains post-inference and never qualifies gold."""

import csv
import json

import pytest

from tools.independent_gold import diagnose_full_record_dev as diagnostic


def _fixture(tmp_path, monkeypatch):
    source = tmp_path / "dev.jsonl.gz"
    source.write_bytes(b"sealed organizer input bytes")
    labels = tmp_path / "dev_labels.csv"
    with labels.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", *diagnostic.ITEMS])
        writer.writeheader()
        writer.writerow({"id": "PPS-DEV-01", **{item: int(item == "v1") for item in diagnostic.ITEMS}})
        writer.writerow({"id": "PPS-DEV-02", **{item: 0 for item in diagnostic.ITEMS}})
    monkeypatch.setattr(diagnostic, "DEV_INPUT", source)
    monkeypatch.setattr(diagnostic, "DEV_LABELS", labels)
    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text(json.dumps({
        "phase": "official_dev", "input_path": str(source),
        "input_sha256": diagnostic._sha256(source),
        "selected_ids": ["PPS-DEV-01", "PPS-DEV-02"],
    }), encoding="utf-8")
    row = {"id": "PPS-DEV-01", "ledger": {"cells": [
        {"item": item, "label": "U" if item == "v2" else int(item == "v1")}
        for item in diagnostic.ITEMS
    ]}}
    (run / "full_records.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    return run, source


def test_partial_dev_report_counts_abstentions_and_never_qualifies(tmp_path, monkeypatch):
    run, _ = _fixture(tmp_path, monkeypatch)
    report = diagnostic.diagnose(run)
    assert report["complete"] is False
    assert report["scored_records"] == 1
    assert report["compared_cells"] == 24
    assert report["correct_binary_cells"] == 23
    assert report["abstention_cells"] == 1
    assert report["missing_records"] == ["PPS-DEV-02"]
    assert report["qualification_status"] == "diagnostic_only_not_gold"


def test_changed_dev_input_refused(tmp_path, monkeypatch):
    run, source = _fixture(tmp_path, monkeypatch)
    source.write_bytes(b"changed organizer input bytes")
    with pytest.raises(ValueError, match="changed"):
        diagnostic.diagnose(run)
