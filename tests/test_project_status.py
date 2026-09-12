"""Synthetic status-contract checks: no contest data or model required."""
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("project_status", ROOT / "tools/project_status.py")
status = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(status)


def fixture_state(tmp_path):
    (tmp_path / "saved.csv").write_text("synthetic\n", encoding="utf-8")
    (tmp_path / "metrics.json").write_text(json.dumps({"metric": 0.5}), encoding="utf-8")
    return {"records": [{"prediction": "saved.csv", "sha256": status.sha256(tmp_path / "saved.csv"),
                         "metrics": "metrics.json", "metric_key": ["metric"], "macro_f1": 0.5}]}


def test_verify_read_only(tmp_path):
    state = fixture_state(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = status.verify_state(tmp_path, state)
    assert result["ok"] and result["passed"] == 2
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize("relative", ["../outside", "/tmp/outside", "C:/Windows/file", "..\\outside"])
def test_reject_outside_repository(tmp_path, relative):
    with pytest.raises(ValueError):
        status.repo_path(tmp_path, relative)


def test_missing_private_artifact_is_not_success(tmp_path):
    state = fixture_state(tmp_path)
    state["records"][0]["prediction"] = "absent.csv"
    result = status.verify_state(tmp_path, state)
    assert not result["ok"]
    assert result["failures"][0]["status"] == "missing_local_artifact"


def test_modified_reference_is_not_success(tmp_path):
    state = fixture_state(tmp_path)
    state["records"][0]["sha256"] = hashlib.sha256(b"different").hexdigest()
    result = status.verify_state(tmp_path, state)
    assert not result["ok"] and result["failures"][0]["status"] == "hash_mismatch"


def test_score_mismatch_is_not_success(tmp_path):
    state = fixture_state(tmp_path)
    state["records"][0]["macro_f1"] = 0.6
    result = status.verify_state(tmp_path, state)
    assert not result["ok"] and result["failures"][0]["status"] == "score_mismatch"


def test_nested_source_paths_are_verified(tmp_path):
    state = fixture_state(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "frozen.py").write_text("# synthetic\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"files": {"frozen.py": status.sha256(source / "frozen.py")}}), encoding="utf-8")
    state["source_manifests"] = [{"path": "manifest.json", "sha256": status.sha256(manifest),
                                  "source_root": "source", "map_key": "files"}]
    assert status.verify_state(tmp_path, state)["ok"]


def test_current_public_registry_is_coherent():
    state = status.read_json(ROOT / "docs/STATE.json")
    assert state["schema_version"] == 1
    assert len({r["id"] for r in state["records"]}) == len(state["records"])
    for record in state["records"]:
        assert 0 <= record["macro_f1"] <= 1
        assert len(record["sha256"]) == 64
    assert status.repo_path(ROOT, state["entrypoints"]["current_input_b4"]).is_file()


def test_current_document_navigation_has_no_missing_targets():
    files = [ROOT / "README.md", ROOT / "START_HERE.md", ROOT / "experiments/README.md"]
    files += [p for p in (ROOT / "docs").glob("*.md") if p.name != "LOCAL_HANDOFF.md"]
    for file in files:
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", file.read_text(encoding="utf-8")):
            if target.startswith(("https://", "http://", "#")):
                continue
            assert (file.parent / target.split("#", 1)[0]).resolve().exists(), (file, target)


def test_replay_command_help_does_not_start_work():
    run = subprocess.run([sys.executable, "-B", str(ROOT / "tools/replay_preserved_reference.py"), "--help"],
                         capture_output=True, text=True)
    assert run.returncode == 0 and "--output" in run.stdout


def test_replay_refuses_output_outside_its_archive(tmp_path):
    output = tmp_path / "must_not_create"
    run = subprocess.run([sys.executable, "-B", str(ROOT / "tools/replay_preserved_reference.py"),
                          "--output", str(output)], capture_output=True, text=True)
    assert run.returncode != 0 and not output.exists()
