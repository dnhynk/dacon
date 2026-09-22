"""No-model synthetic checks for the ten-window offline quality metric boundary."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from tools.independent_gold import claude_batch_pilot as pilot
from tools.independent_gold import score_verified_batch_dev200 as score


def _canonical(value) -> bytes:
    return (pilot.base.canonical_json(value) + "\n").encode("utf-8")


@pytest.fixture
def case(tmp_path: Path, monkeypatch):
    ids = [f"SYN-DEV-{index:03d}" for index in range(200)]
    source = tmp_path / "dev.jsonl.gz"
    with gzip.open(source, "wt", encoding="utf-8", newline="\n") as handle:
        for record_id in ids:
            handle.write(json.dumps({"id": record_id}) + "\n")
    input_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    labels = tmp_path / "dev_labels.csv"
    with labels.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", *score.ITEMS])
        writer.writeheader()
        for record_id in ids:
            writer.writerow({"id": record_id, **{item: 1 for item in score.ITEMS}})
    monkeypatch.setattr(score, "DEV_INPUT", source)
    monkeypatch.setattr(score, "DEV_LABELS", labels)
    monkeypatch.setattr(score, "RUNS_ROOT", tmp_path)
    windows = []
    for start in score.WINDOW_STARTS:
        run = tmp_path / f"window-{start:03d}"
        archive = tmp_path / f"archive-{start:03d}"
        run.mkdir()
        archive.mkdir()
        (archive / "manifest.json").write_bytes(_canonical({"synthetic": True}))
        manifest = {
            "schema_version": pilot.SCHEMA_VERSION,
            "phase": "blind_dev_batch_diagnostic_only",
            "start_index": start,
            "selected_ids": ids[start:start + 20],
            "input_sha256": input_sha,
            "batch_size": 5,
            "mode": "source_lean",
            "model": "synthetic-requested",
            "observed_model": "synthetic-observed",
            "rubric_sha256": "a" * 64,
            "rubric_source_sha256": "b" * 64,
            "system_sha256": "c" * 64,
            "source_bundle": {"bundle_sha256": "d" * 64, "files": []},
            "pilot_source_sha256": "e" * 64,
            "max_budget_usd": 3.0,
            "timeout_seconds": 30.0,
            "cli": {"executable_sha256": "f" * 64, "version_output": "synthetic"},
            "execute": True,
        }
        manifest["manifest_sha256"] = pilot.base.sha256_object(manifest)
        (run / "run_manifest.json").write_bytes(_canonical(manifest))
        for local_index, record_id in enumerate(manifest["selected_ids"]):
            batch = run / f"batch-{local_index // 5:03d}"
            batch.mkdir(exist_ok=True)
            row = {
                "id": record_id,
                "status": "provisional_unqualified",
                "batch_index": local_index // 5,
                "ledger": {"cells": [{"item": item, "label": 1} for item in score.ITEMS]},
            }
            (batch / f"{record_id}.row.json").write_bytes(_canonical(row))
        windows.append((run, archive))
    calls = []

    def fake_verify(run: Path, archive: Path):
        assert (run, archive) in windows
        assert labels.is_file()
        calls.append(run)
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        return {
            "status": "executed_rows_structurally_verified_not_gold",
            "gold_qualification": False,
            "model_calls_by_verifier": 0,
            "selected_records": 20,
            "records_ok": 20,
            "records_content_error": 0,
            "safety_errors": 0,
            "run_manifest_sha256": manifest["manifest_sha256"],
            "start_index": manifest["start_index"],
            "archive": {"synthetic_raw_replay": True},
        }

    monkeypatch.setattr(score.verifier, "verify", fake_verify)
    return {"windows": windows, "ids": ids, "calls": calls, "labels": labels}


def _edit_manifest(run: Path, update) -> None:
    path = run / "run_manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    update(value)
    value["manifest_sha256"] = pilot.base.sha256_object({
        key: child for key, child in value.items() if key != "manifest_sha256"
    })
    path.write_bytes(_canonical(value))


def test_all_ten_raw_verifications_precede_label_open_and_metrics_have_no_threshold(case, monkeypatch):
    original = score._load_official_labels
    opened_after = []

    def checked_load(ids):
        opened_after.append(len(case["calls"]))
        return original(ids)

    monkeypatch.setattr(score, "_load_official_labels", checked_load)
    before = {path: path.read_bytes() for run, archive in case["windows"]
              for root in (run, archive) for path in root.rglob("*") if path.is_file()}

    report = score.score_windows(case["windows"])

    assert opened_after == [10]
    assert report["records"] == 200 and report["cells_verified"] == 4800
    assert report["macro_positive_f1"] == 1.0
    assert report["per_item"]["v1"]["tp"] == 200
    assert report["status"] == "metrics_only_pending_quality_policy_not_gold"
    assert report["quality_threshold_applied"] is False
    assert report["qualified_for_gold_generation"] is False
    assert report["verification_code_sha256"] == {
        "scorer_sha256": hashlib.sha256(Path(score.__file__).read_bytes()).hexdigest(),
        "verifier_sha256": hashlib.sha256(Path(score.verifier.__file__).read_bytes()).hexdigest(),
    }
    assert {path: path.read_bytes() for path in before} == before


def test_u_stays_unresolved_and_never_becomes_zero(case):
    run = case["windows"][0][0]
    path = run / "batch-000" / f"{case['ids'][0]}.row.json"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["ledger"]["cells"][0]["label"] = "U"
    path.write_bytes(_canonical(row))

    report = score.score_windows(case["windows"])

    assert report["status"] == "failed_unresolved_cells"
    assert report["macro_positive_f1"] is None
    assert report["per_item"]["v1"]["u"] == 1
    assert report["per_item"]["v1"]["u_gold_positive"] == 1
    assert report["per_item"]["v1"]["fn"] == 0
    assert report["per_item"]["v1"]["positive_f1"] is None
    assert report["qualified_for_gold_generation"] is False


def test_missing_row_fails_before_official_labels_are_opened(case, monkeypatch):
    run = case["windows"][2][0]
    (run / "batch-000" / f"{case['ids'][40]}.row.json").unlink()
    monkeypatch.setattr(score, "_load_official_labels", lambda _: pytest.fail("labels opened early"))

    with pytest.raises((score.BatchDevScoreError, score.verifier.BatchVerificationError)):
        score.score_windows(case["windows"])
    assert len(case["calls"]) == 3


def test_duplicate_window_selection_and_frozen_tuple_drift_fail_before_labels(case, monkeypatch):
    monkeypatch.setattr(score, "_load_official_labels", lambda _: pytest.fail("labels opened early"))
    duplicate = list(case["windows"])
    duplicate[1] = duplicate[0]
    with pytest.raises(score.BatchDevScoreError, match="scan trees overlap"):
        score.score_windows(duplicate)

    run = case["windows"][4][0]
    _edit_manifest(run, lambda manifest: manifest.update(mode="full_shared"))
    with pytest.raises(score.BatchDevScoreError, match="frozen mode"):
        score.score_windows(case["windows"])


def test_verifier_status_is_hard_boundary_before_labels(case, monkeypatch):
    original = score.verifier.verify

    def rejected(run, archive):
        report = original(run, archive)
        if run == case["windows"][5][0]:
            report["status"] = "failed_content_or_incomplete"
            report["records_ok"] = 19
        return report

    monkeypatch.setattr(score.verifier, "verify", rejected)
    monkeypatch.setattr(score, "_load_official_labels", lambda _: pytest.fail("labels opened early"))

    with pytest.raises(score.BatchDevScoreError, match="executed raw replay"):
        score.score_windows(case["windows"])
    assert len(case["calls"]) == 6


def test_label_containing_archive_is_rejected_before_any_tree_snapshot(case, monkeypatch):
    dangerous = case["windows"][0][1]
    labels_inside = dangerous / "dev_labels.csv"
    labels_inside.write_bytes(case["labels"].read_bytes())
    monkeypatch.setattr(score, "DEV_LABELS", labels_inside)
    monkeypatch.setattr(score, "_tree_snapshot", lambda _: pytest.fail("scanned labels early"))

    with pytest.raises(score.BatchDevScoreError, match="labels cannot be inside"):
        score.score_windows(case["windows"])
    assert case["calls"] == []


def test_outside_runs_path_is_rejected_before_snapshot(case, tmp_path, monkeypatch):
    outside = tmp_path.parent / "outside_runs_boundary"
    altered = list(case["windows"])
    altered[0] = (altered[0][0], outside)
    monkeypatch.setattr(score, "_tree_snapshot", lambda _: pytest.fail("scanned outside runs"))

    with pytest.raises(score.BatchDevScoreError, match="child directory of repository runs"):
        score.score_windows(altered)
    assert case["calls"] == []


def test_raw_symlink_archive_is_rejected_before_resolution(case, tmp_path, monkeypatch):
    alias = tmp_path / "archive_alias"
    try:
        alias.symlink_to(case["windows"][0][1], target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this test environment")
    altered = list(case["windows"])
    altered[0] = (altered[0][0], alias)
    monkeypatch.setattr(score, "_tree_snapshot", lambda _: pytest.fail("scanned symlink"))

    with pytest.raises(score.BatchDevScoreError, match="linked run/archive path"):
        score.score_windows(altered)
    assert case["calls"] == []
