import json
import zipfile

import pytest

from tools.ingest_results import extract_results, inspect


@pytest.mark.parametrize("name", ["../escape.txt", "/absolute.txt", "C:/escape.txt", "safe/../../escape.txt"])
def test_result_archive_cannot_escape(tmp_path, name):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(name, "invalid")
    with pytest.raises(ValueError, match="Unsafe"):
        extract_results(archive, tmp_path / "output")
    assert not (tmp_path / "escape.txt").exists()


def test_preflight_failure_is_not_inference_success(tmp_path):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("artifacts/colab_preflight.json", json.dumps({"issues": ["Driver incompatible"]}))
        z.writestr("tools/untrusted.py", "raise RuntimeError('must not execute')")
    target = extract_results(archive, tmp_path / "output")
    report = inspect(target)
    assert not report["prediction_validation_passed"]
    assert not report["fixed_model_environment_reported"]
    assert report["gpu_checks"]["artifacts/colab_preflight.json"]["issues"]


def test_extract_results_never_overwrites_existing_evidence(tmp_path):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("response.json", "new response")
    destination = tmp_path / "output"
    destination.mkdir()
    existing = destination / "response.json"
    existing.write_text("preserved response", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Preserve prior results"):
        extract_results(archive, destination)
    assert existing.read_text(encoding="utf-8") == "preserved response"


def test_result_inspection_does_not_open_holdout_by_default(tmp_path, monkeypatch):
    prediction = tmp_path / "holdout" / "submission.csv"
    prediction.parent.mkdir()
    prediction.write_text("not opened", encoding="utf-8")

    def forbidden_read(*args, **kwargs):
        raise AssertionError("Do not read withheld input or labels by default")

    monkeypatch.setattr("tools.ingest_results.records", forbidden_read)
    monkeypatch.setattr("tools.ingest_results.evaluate", forbidden_read)
    report = inspect(tmp_path)
    assert not report["verified_predictions"]
    assert report["skipped_predictions"] == [{
        "path": "holdout/submission.csv", "reason": "holdout_not_explicitly_enabled",
    }]
