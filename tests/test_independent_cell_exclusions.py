"""CPU-only quarantine, export and stale-key invariants on invented records."""
import argparse
import gzip
import json

import pytest

from tools.independent_gold import cell_score as s


def policy(path):
    path.write_text(json.dumps({"excluded_from_scoring": [{
        "operation": "retarget_procurement:v10", "target_item": "v10",
        "mode": "violation", "reason": "unclosed object scope",
    }]}), encoding="utf-8")


def plant(split, number):
    return {"plant_id": str(number), "split": split, "kind": "edited",
            "operation": "retarget_procurement:v10", "target_item": "v10",
            "expected_zero_items": ["v2"], "near_miss_items": [],
            "record": {"id": str(number), "meta": {}, "docs": [{"text": "invented"}]}}


def test_quarantine_applies_equally_without_changing_inputs_or_sealed_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "ROOT", tmp_path)
    pool = tmp_path / "plants.jsonl"
    pool.write_text("".join(json.dumps(plant(split, n)) + "\n" for n, split in enumerate(["dev", "holdout"])), encoding="utf-8")
    before = pool.read_bytes()
    policy(pool.with_name("scoring_exclusions.json"))
    for n, split in enumerate(["dev", "holdout"]):
        out = tmp_path / "runs" / split
        manifest = s.export(argparse.Namespace(plants=pool, output_dir=out, split=split, per_item=None))
        assert manifest["records"] == manifest["excluded_from_scoring_records"] == 1
        with gzip.open(out / "input.jsonl.gz", "rt", encoding="utf-8") as f:
            assert json.loads(f.read()) == plant(split, n)["record"]
        key = json.loads((out / "key.json").read_text(encoding="utf-8"))
        assert key[str(n)]["excluded_from_scoring"] is True
        if split == "holdout":
            with pytest.raises(ValueError, match="ledger-guarded"):
                s.score_submission(key, {})
    assert pool.read_bytes() == before


def test_every_denominator_omits_quarantine_but_complete_predictions_still_required():
    key = {"x": {"targets": [{"item": "v10", "operation": "retarget_procurement"}],
                 "near_miss_items": ["v5"], "expected_zero_items": ["v2"],
                 "evidence_spans": {"v10": [{"doc_index": 0, "start": 0, "end": 1}]},
                 "excluded_from_scoring": True, "scoring_exclusion_reasons": ["scope"]}}
    prediction = {item: "1" for item in s.ITEMS}
    result = s.score_submission(key, {"x": prediction})
    assert result["records"] == result["excluded_from_scoring_records"] == 1
    assert result["scored_records"] == result["near_miss_cells"] == result["expected_zero_cells"] == 0
    assert result["items"]["v10"]["targets"] == result["items"]["v10"]["evidence_targets"] == 0
    assert result["mean_item_recall"] is None and result["by_operation"] == {}
    with pytest.raises(ValueError, match="lacks"):
        s.score_submission(key, {})
    with pytest.raises(ValueError, match="nonbinary"):
        s.score_submission(key, {"x": {**prediction, "v1": "U"}})


def test_old_key_cannot_silently_ignore_new_policy(tmp_path):
    pool = tmp_path / "plants.jsonl"
    policy(pool.with_name("scoring_exclusions.json"))
    (tmp_path / "manifest.json").write_text(json.dumps({"plants": str(pool)}), encoding="utf-8")
    with pytest.raises(ValueError, match="scoring exclusions changed"):
        s.score(argparse.Namespace(key=tmp_path / "key.json"))


def test_rule_is_operation_item_mode_bound_and_cannot_select_ids(tmp_path):
    path = tmp_path / "scoring_exclusions.json"
    policy(path)
    rows = [plant("dev", i) for i in range(3)]
    rows[1]["near_miss_items"] = ["v10"]
    rows[2]["operation"] = "written"
    s.apply_scoring_exclusions(rows, path)
    assert [bool(r.get("excluded_from_scoring")) for r in rows] == [True, False, False]
    bad = json.loads(path.read_text())
    bad["excluded_from_scoring"][0]["record_id"] = "0"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid scoring exclusion"):
        s.apply_scoring_exclusions(rows, path)
