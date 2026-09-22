"""CPU-only invariants for exporting synthetic notices and scoring a submission against them."""

import argparse
import gzip
import json

import pytest

from tools.independent_gold import cell_score as scoring


def _row(number, item, split="dev", extra=None):
    return {
        "plant_id": f"p{number}", "kind": "edited", "operation": "delete:x", "target_item": item, "split": split,
        "extra_targets": extra or [], "expected_zero_items": ["v1", "v9"],
        "record": {"id": f"PPS-D-{number:06d}", "docs": [{"type": "공고문", "text": "본문"}], "meta": {}},
        "provenance": {"secret": "must not leak"},
    }


def test_export_writes_records_only_and_a_separate_key(tmp_path, monkeypatch):
    monkeypatch.setattr(scoring, "ROOT", tmp_path)
    plants = tmp_path / "plants.jsonl"
    rows = [_row(1, "v18"), _row(2, "v18"), _row(3, "v18"), _row(4, "v2", split="holdout"),
            _row(5, "v6", extra=[{"target_item": "v21", "operation": "rewrite_min_share", "provenance": {}}])]
    plants.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    output = tmp_path / "runs" / "measure"
    manifest = scoring.export(argparse.Namespace(plants=plants, output_dir=output, split="dev", per_item=2))
    assert manifest["records_by_item"] == {"v6": 1, "v18": 2}
    with gzip.open(output / "input.jsonl.gz", "rt", encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle]
    # The pipeline input is the organizer record and nothing else.
    assert all(set(record) == {"id", "docs", "meta"} for record in records)
    assert "must not leak" not in (output / "input.jsonl.gz").read_bytes().decode("latin-1")
    key = json.loads((output / "key.json").read_text(encoding="utf-8"))
    assert set(key) == {record["id"] for record in records} and "PPS-D-000004" not in key
    assert key["PPS-D-000005"]["targets"] == [
        {"item": "v6", "operation": "delete"}, {"item": "v21", "operation": "rewrite_min_share"}]
    with pytest.raises(ValueError, match="fresh"):
        scoring.export(argparse.Namespace(plants=plants, output_dir=output, split="dev", per_item=2))


def test_score_counts_recall_on_targets_and_firing_on_the_other_cells():
    key = {
        "A": {"targets": [{"item": "v18", "operation": "delete"}], "expected_zero_items": ["v1", "v9"]},
        "B": {"targets": [{"item": "v18", "operation": "delete"}, {"item": "v21", "operation": "rewrite_min_share"}],
              "expected_zero_items": ["v1"]},
    }
    blank = {item: "0" for item in scoring.ITEMS}
    predictions = {"A": {**blank, "v18": "1", "v9": "1"}, "B": {**blank, "v21": "1"}}
    result = scoring.score_submission(key, predictions)
    assert result["items"]["v18"]["recall"] == 0.5 and result["items"]["v21"]["recall"] == 1.0
    assert result["items"]["v9"]["fired_rate"] == 1.0 and result["items"]["v1"]["fired_rate"] == 0.0
    assert (result["expected_zero_fired"], result["expected_zero_cells"]) == (1, 3)
    assert result["by_operation"]["v18:delete"] == {"targets": 2, "recall": 0.5}
    assert result["mean_item_recall"] == 0.75
    low, high = result["items"]["v18"]["recall_ci95"]
    assert 0.0 < low < 0.5 < high < 1.0
    with pytest.raises(ValueError, match="lacks 1 records"):
        scoring.score_submission(key, {"A": blank})
