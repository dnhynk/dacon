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
