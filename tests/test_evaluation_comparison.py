"""Fixed paired measurements must not convert recovery of old answers into gains."""
import csv
from pathlib import Path
import subprocess
import sys

import pytest

from tools.evaluate import compare


def predictions(tmp_path, name, v1):
    path = tmp_path / (name + ".csv")
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", *[f"v{k}" for k in range(1, 25)],
                                                  *[f"e{k}" for k in range(1, 25)]])
        writer.writeheader()
        for i, value in enumerate(v1):
            writer.writerow({"id": str(i), **{f"v{k}": value if k == 1 else 0 for k in range(1, 25)},
                             **{f"e{k}": "" for k in range(1, 25)}})
    return path


def test_recovered_fresh_answer_may_already_be_correct_in_best(tmp_path):
    truth = predictions(tmp_path, "truth", [1, 1, 0])
    best = predictions(tmp_path, "best", [1, 0, 0])
    fresh = predictions(tmp_path, "fresh", [0, 0, 0])
    candidate = predictions(tmp_path, "candidate", [1, 1, 1])
    report = compare(truth, candidate, [best, fresh])
    assert report["comparisons"][str(best)]["recoveries"] == 1
    assert report["comparisons"][str(fresh)]["recoveries"] == 2
    assert all(x["new_errors"] == 1 for x in report["comparisons"].values())
    assert report["comparisons"][str(best)]["delta_macro_f1"] == pytest.approx((0.8 - 2 / 3) / 24)


def test_incomplete_reference_is_rejected(tmp_path):
    truth = predictions(tmp_path, "truth", [1, 0])
    incomplete = predictions(tmp_path, "partial", [1])
    with pytest.raises(ValueError, match="identical id"):
        compare(truth, truth, [incomplete])


def test_cli_cannot_overwrite_an_existing_score(tmp_path):
    output = tmp_path / "saved.json"
    output.write_text("protected", encoding="utf-8")
    runner = Path(__file__).resolve().parents[1] / "tools/evaluate.py"
    result = subprocess.run([sys.executable, str(runner), "--labels", "unused.csv",
                             "--predictions", "unused.csv", "--out", str(output)],
                            capture_output=True, text=True)
    assert result.returncode != 0 and "Preserve the existing evaluation" in result.stderr
    assert output.read_text(encoding="utf-8") == "protected"
