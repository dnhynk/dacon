"""Exact source archival for a dry organizer-only unlabeled batch manifest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.independent_gold import archive_source_bundle as archiver
from tools.independent_gold import claude_full_record_annotator as claude


PILOT = "tools/independent_gold/claude_batch_pilot.py"
PREPARER = "tools/independent_gold/claude_unlabeled_batch_prepare.py"


def _sealed_manifest(run_dir: Path, **changes) -> Path:
    manifest = {
        "schema_version": "dacon.independent.claude_unlabeled_batch_prepare.v1",
        "phase": "unlabeled_batch_input_dry_only",
        "source_bundle": claude.source_bundle(),
        "rubric_source_sha256": archiver.file_sha256(claude.RUBRIC_PATH),
        "batch_pilot_source_sha256": archiver.file_sha256(archiver.ROOT / PILOT),
        "preparer_source_sha256": archiver.file_sha256(archiver.ROOT / PREPARER),
    }
    manifest.update(changes)
    manifest["manifest_sha256"] = claude.sha256_object(manifest)
    run_dir.mkdir(parents=True)
    path = run_dir / "run_manifest.json"
    path.write_bytes((claude.canonical_json(manifest) + "\n").encode("utf-8"))
    return path


@pytest.fixture
def run_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "runs" / "self_label_20000_20260918"
    root.mkdir(parents=True)
    monkeypatch.setattr(archiver, "RUN_ROOT", root)
    return root


def test_unlabeled_dry_archive_contains_both_exact_source_bytes(run_root: Path) -> None:
    run_manifest = _sealed_manifest(run_root / "dry")
    output = run_root / "source_bundle_dry"
    frozen = archiver.archive(output, run_manifest)
    run = json.loads(run_manifest.read_text(encoding="utf-8"))
    assert frozen["run_manifest_sha256"] == run["manifest_sha256"]
    assert frozen["bundle_sha256"] == run["source_bundle"]["bundle_sha256"]
    assert frozen["extra_sources"] == [
        {"path": PILOT, "sha256": run["batch_pilot_source_sha256"]},
        {"path": PREPARER, "sha256": run["preparer_source_sha256"]},
    ]
    for relative in (PILOT, PREPARER):
        assert (output / relative).read_bytes() == (archiver.ROOT / relative).read_bytes()
    assert json.loads((output / "manifest.json").read_text(encoding="utf-8")) == frozen


@pytest.mark.parametrize("field", ("batch_pilot_source_sha256", "preparer_source_sha256"))
def test_unlabeled_dry_archive_refuses_source_hash_drift(run_root: Path, field: str) -> None:
    run_manifest = _sealed_manifest(run_root / f"dry-{field}", **{field: "0" * 64})
    with pytest.raises(ValueError, match="source changed before archival"):
        archiver.archive(run_root / f"source_bundle_{field}", run_manifest)


@pytest.mark.parametrize("field", ("batch_pilot_source_sha256", "preparer_source_sha256"))
def test_unlabeled_dry_archive_refuses_one_sided_source_reference(run_root: Path, field: str) -> None:
    run_manifest = _sealed_manifest(run_root / f"dry-missing-{field}")
    value = json.loads(run_manifest.read_text(encoding="utf-8"))
    del value[field]
    value["manifest_sha256"] = claude.sha256_object({key: child for key, child in value.items()
                                                     if key != "manifest_sha256"})
    run_manifest.write_bytes((claude.canonical_json(value) + "\n").encode("utf-8"))
    output = run_root / f"source_bundle_missing_{field}"
    with pytest.raises(ValueError, match="incomplete or ambiguous"):
        archiver.archive(output, run_manifest)
    assert not output.exists()


def test_dev_pilot_extra_source_behavior_is_unchanged(run_root: Path) -> None:
    manifest = _sealed_manifest(run_root / "dev")
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["schema_version"] = "dacon.independent.claude_batch_pilot.v1"
    value["phase"] = "blind_dev_batch_diagnostic_only"
    value["pilot_source_sha256"] = value.pop("batch_pilot_source_sha256")
    del value["preparer_source_sha256"]
    value["manifest_sha256"] = claude.sha256_object({key: child for key, child in value.items()
                                                     if key != "manifest_sha256"})
    manifest.write_bytes((claude.canonical_json(value) + "\n").encode("utf-8"))
    frozen = archiver.archive(run_root / "source_bundle_dev", manifest)
    assert frozen["extra_sources"] == [{"path": PILOT, "sha256": value["pilot_source_sha256"]}]
