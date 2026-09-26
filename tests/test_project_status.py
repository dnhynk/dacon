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


def test_current_public_registry_is_coherent():
    state = status.read_json(ROOT / "docs/STATE.json")
    assert state["schema_version"] == 1
    assert len({r["id"] for r in state["records"]}) == len(state["records"])
    for record in state["records"]:
        assert 0 <= record["macro_f1"] <= 1
        assert len(record["sha256"]) == 64
    assert status.repo_path(ROOT, state["entrypoints"]["current_input_b4"]).is_file()


def test_new_comparison_does_not_silently_replace_the_standalone_reference():
    records = [
        {'id': 'standalone', 'kind': 'fresh_model_inference', 'macro_f1': .4},
        {'id': 'old-grid-best', 'kind': 'fresh_whole_cohort_policy_comparison',
         'comparison_round': 'old', 'policy': 'candidate', 'macro_f1': .9},
        {'id': 'new-grid-control', 'kind': 'fresh_whole_cohort_policy_comparison',
         'comparison_round': 'new', 'policy': 'current', 'macro_f1': .6},
        {'id': 'new-grid-best', 'kind': 'fresh_whole_cohort_policy_comparison',
         'comparison_round': 'new', 'policy': 'candidate', 'macro_f1': .7},
        {'id': 'new-grid-last', 'kind': 'fresh_whole_cohort_policy_comparison',
         'comparison_round': 'new', 'policy': 'another', 'macro_f1': .5},
    ]
    displayed = status.summarized_measurements({'records': records})
    assert [row['id'] for label, row in displayed] == [
        'standalone', 'new-grid-control', 'new-grid-best']


def test_current_document_navigation_has_no_missing_targets():
    files = [ROOT / "README.md", ROOT / "START_HERE.md"]
    files += [p for p in (ROOT / "docs").glob("*.md") if p.name != "LOCAL_HANDOFF.md"]
    for file in files:
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", file.read_text(encoding="utf-8")):
            if target.startswith(("https://", "http://", "#")):
                continue
            assert (file.parent / target.split("#", 1)[0]).resolve().exists(), (file, target)


def test_standalone_normal_can_be_the_best_in_its_whole_comparison():
    records = [
        {'id': 'normal', 'kind': 'fresh_model_inference', 'macro_f1': .8,
         'comparison_round': 'new', 'policy': 'normal'},
        {'id': 'diagnostic', 'kind': 'fresh_whole_cohort_policy_comparison',
         'comparison_round': 'new', 'policy': 'alternative', 'macro_f1': .7},
    ]
    displayed = status.summarized_measurements({'records': records})
    assert [r['id'] for _, r in displayed] == ['normal', 'normal']
    assert displayed[-1][1]['kind'] == 'fresh_model_inference'


def test_newer_standalone_hides_an_older_completed_comparison():
    records = [
        {'id': 'old-grid', 'kind': 'fresh_whole_cohort_policy_comparison',
         'comparison_round': 'old', 'policy': 'candidate', 'macro_f1': .7},
        {'id': 'new-standalone', 'kind': 'fresh_model_inference', 'macro_f1': .8},
    ]
    displayed = status.summarized_measurements({'records': records})
    assert [(label, row['id']) for label, row in displayed] == [
        ('Latest standalone whole fresh', 'new-standalone')
    ]


def test_summary_survives_a_console_that_cannot_encode_the_state(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "docs").mkdir()
    shutil.copyfile(ROOT / "tools/project_status.py", tmp_path / "tools/project_status.py")
    state = {"updated_utc": "u", "official_score": 0.5, "target_official_macro_f1": 0.85, "records": [],
             "active_task": {"status": "s", "next_action": "9/28–9/29"},
             "entrypoints": {"current_input_b4": "script.py", "b4_validation": "v"}}
    (tmp_path / "docs/STATE.json").write_text(json.dumps(state), encoding="utf-8")
    run = subprocess.run([sys.executable, "-B", str(tmp_path / "tools/project_status.py")], capture_output=True,
                         env=dict(os.environ, PYTHONIOENCODING="cp949"))
    assert run.returncode == 0 and b"Next: 9/28?9/29" in run.stdout
