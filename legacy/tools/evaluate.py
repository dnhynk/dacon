"""Competition macro-F1 with strict id alignment and item-level error counts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from submission.pps.data import ITEMS, read_csv


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


def compare(truth_path, prediction_path, baseline_paths):
    """Compare the same complete predictions to every declared reference.

    No candidate construction or item-level answer selection takes place here.
    In particular, a gain over a low fresh baseline is not added to an older score.
    """
    candidate = evaluate(truth_path, prediction_path)
    pred = {r["id"]: r for r in read_csv(prediction_path)}
    truth = {r["id"]: r for r in read_csv(truth_path)}
    baselines = {}
    for path in baseline_paths:
        name = str(path)
        if name in baselines:
            raise ValueError(f"Duplicate baseline: {path}")
        score = evaluate(truth_path, path)
        baseline = {r["id"]: r for r in read_csv(path)}
        changes = [
            {"id": rid, "item": key, "before": int(baseline[rid][key]),
             "after": int(pred[rid][key]), "truth": int(truth[rid][key]),
             "recovery": pred[rid][key] == truth[rid][key]}
            for rid in truth for key in ITEMS if pred[rid][key] != baseline[rid][key]
        ]
        baselines[name] = {
            "macro_f1": score["macro_f1"],
            "delta_macro_f1": candidate["macro_f1"] - score["macro_f1"],
            "recoveries": sum(c["recovery"] for c in changes),
            "new_errors": sum(not c["recovery"] for c in changes),
            "per_item_delta": {k: candidate["per_item"][k]["f1"] - score["per_item"][k]["f1"] for k in ITEMS},
            "changes": changes,
        }
    return {"candidate": candidate, "comparisons": baselines,
            "scope": "Fixed complete predictions on aligned labels; not a new inference or causal attribution."}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--labels", required=True, type=Path)
    p.add_argument("--predictions", required=True, type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--baseline", type=Path, action="append", default=[],
                   help="Repeat to compare both the highest preserved and matched fresh baseline")
    a = p.parse_args()
    if a.out and a.out.exists():
        p.error(f"Preserve the existing evaluation; use a new --out path: {a.out}")
    report = compare(a.labels, a.predictions, a.baseline) if a.baseline else evaluate(a.labels, a.predictions)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        with a.out.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    if a.baseline:
        summary = {"macro_f1": report["candidate"]["macro_f1"],
                   "comparisons": {name: {k: v for k, v in result.items() if k not in {"changes", "per_item_delta"}}
                                   for name, result in report["comparisons"].items()}}
    else:
        summary = {k: v for k, v in report.items() if k != "errors"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
