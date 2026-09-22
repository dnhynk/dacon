"""CPU-only invariants for the teacher label check: selection, anchors and verdict arithmetic."""

import argparse
import json

from tools.independent_gold import cell_vet as vet


def _row(plant_id, item, split="dev", kind="edited"):
    return {"plant_id": plant_id, "target_item": item, "split": split, "kind": kind}


def test_selection_is_deterministic_dev_only_and_covers_transplants():
    rows = [_row(f"p{number}", "v18") for number in range(6)]
    rows += [_row("h1", "v18", split="holdout"), _row("t1", "v2", kind="transplanted"), _row("c1", "v18", kind="control")]
    chosen = vet.select(rows, targets_per_item=3, hosts_per_item=1)
    assert [row["target_item"] for row in chosen["target"]] == ["v2", "v18", "v18", "v18"]
    assert {row["plant_id"] for row in chosen["target"]}.isdisjoint({"h1", "c1"})
    assert chosen == vet.select(list(reversed(rows)), targets_per_item=3, hosts_per_item=1)
    assert {row["plant_id"] for row in chosen["host"]} <= {row["plant_id"] for row in chosen["target"]}


def test_anchors_are_official_positives_of_the_synthesized_items_only():
    labels = {
        "A": {"v18": "1", "v24": "0", "v11": "0"},
        "B": {"v18": "0", "v24": "1", "v11": "0"},
        "C": {"v18": "0", "v24": "0", "v11": "0"},
    }
    pairs = vet.anchor_candidates({"v10-18": ["v11", "v18"], "v24": ["v24"]}, labels)
    assert pairs == [("A", "v10-18"), ("B", "v24")]


def _answer(output, task_id, record_id, labels):
    attempt = output / "tasks" / task_id / "attempts" / "attempt-001"
    attempt.mkdir(parents=True)
    structured = {"records": [{"record_id": record_id, "annotation": {
        "source_span_ids": [], "decisions": {item: {"label": label} for item, label in labels.items()}}}]}
    (attempt / "structured_output.json").write_text(json.dumps(structured), encoding="utf-8")


def test_verdict_compares_the_target_rate_with_the_official_anchor(tmp_path):
    # PPS-DEV-29 is officially positive for v24 only.
    plan = [
        {"task_id": "t1", "kind": "target", "group": "v24", "items": ["v24"], "record_id": "H1", "target_item": "v24"},
        {"task_id": "t2", "kind": "target", "group": "v24", "items": ["v24"], "record_id": "H2", "target_item": "v24"},
        {"task_id": "t3", "kind": "target", "group": "v24", "items": ["v24"], "record_id": "H3", "target_item": "v24"},
        {"task_id": "a1", "kind": "anchor", "group": "v24", "items": ["v24"], "record_id": "PPS-DEV-29", "dev_id": "PPS-DEV-29"},
        {"task_id": "h1", "kind": "host", "group": "v19", "items": ["v19"], "record_id": "H1",
         "target_item": "v24", "expected_zero_items": ["v19"]},
        {"task_id": "unanswered", "kind": "target", "group": "v24", "items": ["v24"], "record_id": "H4", "target_item": "v24"},
    ]
    (tmp_path / "plan.json").write_text(json.dumps({"tasks": plan}), encoding="utf-8")
    _answer(tmp_path, "t1", "H1", {"v24": 1})
    _answer(tmp_path, "t2", "H2", {"v24": "U"})
    _answer(tmp_path, "t3", "H3", {"v24": 0})
    _answer(tmp_path, "a1", "PPS-DEV-29", {"v24": 1})
    _answer(tmp_path, "h1", "H1", {"v19": 1})
    result = vet.score(argparse.Namespace(output_dir=tmp_path))
    verdict = result["target"]["v24"]
    assert (verdict["n"], verdict["U"], verdict["anchor_n"], verdict["anchor_recall"]) == (3, 1, 1, 1.0)
    assert abs(verdict["teacher_one_rate"] - 1 / 3) < 1e-9 and verdict["valid"] is False
    assert result["host"] == {"cells": 1, "fired": 1, "U": 0, "fired_rate": 1.0, "assumption_holds": False}
    assert result["answered"] == {"target": 3, "anchor": 1, "host": 1}
    kinds = sorted(entry["kind"] for entry in result["disagreements"])
    assert kinds == ["host", "target", "target"]
