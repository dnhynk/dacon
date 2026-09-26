"""Synthetic status-contract checks: no contest data or model required."""
import hashlib
import importlib.util
import json
import os
import re
import shutil
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


def test_current_registry_is_coherent():
    state = status.read_json(ROOT / "docs/STATE.json")
    assert state["schema_version"] == 2
    ledger = state["official"]["ledger"]
    assert len({entry["name"] for entry in ledger}) == len(ledger)
    scored = [entry for entry in ledger if entry["score"] is not None]
    assert all(0 < entry["score"] < 1 for entry in scored)
    best = state["official"]["best"]
    top = max(scored, key=lambda entry: entry["score"])
    assert (best["name"], best["score"]) == (top["name"], top["score"])
    for key in ("entry", "package", "switches", "build"):
        assert status.repo_path(ROOT, state["runtime"][key]).exists()


def test_current_document_navigation_has_no_missing_targets():
    files = [ROOT / "README.md", ROOT / "START_HERE.md"]
    files += [p for p in (ROOT / "docs").glob("*.md") if p.name != "LOCAL_HANDOFF.md"]
    for file in files:
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", file.read_text(encoding="utf-8")):
            if target.startswith(("https://", "http://", "#")):
                continue
            assert (file.parent / target.split("#", 1)[0]).resolve().exists(), (file, target)


def minimal_state(next_action="n"):
    return {"competition": "c", "updated_utc": "u", "goal_official_macro_f1": 0.8,
            "official": {"best": {"name": "b", "score": 0.5}, "ledger": [{"row": "01", "name": "b", "score": 0.5}]},
            "task": {"status": "s", "next_action": next_action},
            "runtime": {"entry": "script.py", "package": "p/", "switches": "p/s.py"}}


def run_copy(tmp_path, state, *args, encoding="utf-8"):
    (tmp_path / "tools").mkdir()
    (tmp_path / "docs").mkdir()
    shutil.copyfile(ROOT / "tools/project_status.py", tmp_path / "tools/project_status.py")
    (tmp_path / "docs/STATE.json").write_text(json.dumps(state), encoding="utf-8")
    return subprocess.run([sys.executable, "-B", str(tmp_path / "tools/project_status.py"), *args],
                          capture_output=True, env=dict(os.environ, PYTHONIOENCODING=encoding))


def test_summary_survives_a_console_that_cannot_encode_the_state(tmp_path):
    run = run_copy(tmp_path, minimal_state("9/28\u20139/29"), encoding="cp949")
    assert run.returncode == 0 and b"Next: 9/28?9/29" in run.stdout


def test_verify_without_the_local_manifest_is_not_success(tmp_path):
    run = run_copy(tmp_path, minimal_state(), "--verify")
    assert run.returncode == 1 and b"missing_local_artifact: runs/evidence_manifest.json" in run.stdout
