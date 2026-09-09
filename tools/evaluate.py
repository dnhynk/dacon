"""Competition macro-F1 with strict id alignment and item-level error counts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pps.data import ITEMS, read_csv


def evaluate(truth_path, prediction_path):
    truth_rows, pred_rows = read_csv(truth_path), read_csv(prediction_path)
    truth = {r["id"]: r for r in truth_rows}
    pred = {r["id"]: r for r in pred_rows}
    if len(truth) != len(truth_rows) or len(pred) != len(pred_rows) or truth.keys() != pred.keys():
        raise ValueError("Ground truth and predictions must have unique, identical id sets")
    if not truth:
        raise ValueError("Cannot evaluate an empty set")
    metrics, errors = {}, []
    for key in ITEMS:
        tp = fp = fn = tn = 0
        for rid, row in truth.items():
            y, p = row[key], pred[rid][key]
            if y not in {"0", "1"} or p not in {"0", "1"}:
                raise ValueError(f"Invalid label for {rid}/{key}")
            tp += y == p == "1"
            tn += y == p == "0"
            fp += y == "0" and p == "1"
            fn += y == "1" and p == "0"
            if y != p:
                errors.append({"id": rid, "item": key, "truth": int(y), "prediction": int(p)})
        metrics[key] = {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "support": tp + fn,
                        "precision": tp / (tp + fp) if tp + fp else 0.,
                        "recall": tp / (tp + fn) if tp + fn else 0.,
                        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.}
    return {"records": len(truth), "macro_f1": sum(m["f1"] for m in metrics.values()) / 24,
            "per_item": metrics, "errors": errors,
            "zero_support_items": [k for k, m in metrics.items() if m["support"] == 0]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--labels", required=True, type=Path)
    p.add_argument("--predictions", required=True, type=Path)
    p.add_argument("--out", type=Path)
    a = p.parse_args()
    report = evaluate(a.labels, a.predictions)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "errors"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
