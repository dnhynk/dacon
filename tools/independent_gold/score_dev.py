from __future__ import annotations

import argparse
import csv
import json
import pathlib
import statistics
import sys
from typing import Any


ITEMS = tuple(f"v{i}" for i in range(1, 25))
DEFAULT_GATE = 0.90


def f1_binary(gold: list[int], pred: list[int]) -> dict[str, Any]:
    tp = sum(g == 1 and p == 1 for g, p in zip(gold, pred))
    fp = sum(g == 0 and p == 1 for g, p in zip(gold, pred))
    fn = sum(g == 1 and p == 0 for g, p in zip(gold, pred))
    denom = 2 * tp + fp + fn
    return {"tp": tp, "fp": fp, "fn": fn, "f1": 0.0 if denom == 0 else 2 * tp / denom}


def load_annotations(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("status") == "ok":
                result[str(row["id"])] = row
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotations", type=pathlib.Path, required=True)
    parser.add_argument(
        "--labels", type=pathlib.Path, default=pathlib.Path("data_open/dev_labels.csv")
    )
    parser.add_argument("--gate", type=float, default=DEFAULT_GATE)
    parser.add_argument("--report", type=pathlib.Path)
    parser.add_argument(
        "--unresolved-policy",
        choices=("fail", "zero"),
        default="fail",
        help="Gold qualification uses fail. zero is diagnostic only.",
    )
    args = parser.parse_args()

    annotations = load_annotations(args.annotations)
    with args.labels.open("r", encoding="utf-8-sig", newline="") as handle:
        labels = list(csv.DictReader(handle))
    missing = [row["id"] for row in labels if row["id"] not in annotations]
    unresolved: list[tuple[str, str]] = []
    evidence_errors: list[tuple[str, str]] = []
    metrics: dict[str, Any] = {}
    for item in ITEMS:
        gold: list[int] = []
        pred: list[int] = []
        for row in labels:
            annotation = annotations.get(row["id"])
            if annotation is None:
                continue
            value = annotation["decisions"][item]["label"]
            if value == "U":
                unresolved.append((row["id"], item))
                # Report a conservative binary diagnostic while independently
                # making any abstention disqualifying for gold generation.
                value = 0
            gold.append(int(row[item]))
            pred.append(int(value))
        metrics[item] = f1_binary(gold, pred)
    for record_id, annotation in annotations.items():
        for error in annotation.get("evidence_errors") or []:
            evidence_errors.append((record_id, error))
    macro = statistics.mean(metric["f1"] for metric in metrics.values())
    qualified = (
        not missing
        and (not unresolved or args.unresolved_policy != "fail")
        and not evidence_errors
        and macro >= args.gate
        and min(metric["f1"] for metric in metrics.values()) >= 0.70
    )
    report = {
        "schema_version": "dacon.independent.dev_gate.v1",
        "annotations": str(args.annotations),
        "label_rows": len(labels),
        "annotation_rows": len(annotations),
        "macro_positive_f1": macro,
        "required_macro_positive_f1": args.gate,
        "required_min_item_f1": 0.70,
        "qualified_for_gold_generation": qualified,
        "metrics": metrics,
        "missing_ids": missing,
        "unresolved": unresolved,
        "evidence_errors": evidence_errors,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if qualified else 2


if __name__ == "__main__":
    sys.exit(main())
