"""Evaluate current deterministic checks on saved development model predictions."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pps.data import ITEMS, records, validate_csv, write_csv
from pps.rules import apply_rules
from pps.knowledge import Knowledge
from tools.evaluate import evaluate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    source = ROOT / "artifacts/audit/development.jsonl.gz"
    labels = ROOT / "artifacts/audit/development_labels.csv"
    recs = list(records(source))
    baseline = {row["id"]: row for row in validate_csv(args.predictions, recs)}
    rows, changes = [], []
    knowledge = Knowledge(ROOT / "data_open/data")
    started = time.perf_counter()
    for rec in recs:
        original = {**baseline[rec["id"]]}
        original.update({key: int(original[key]) for key in ITEMS})
        result, checks = apply_rules(rec, original, knowledge)
        rows.append(result)
        for key in ITEMS:
            if result[key] != original[key]:
                changes.append({"id": rec["id"], "item": key, "before": original[key],
                                "after": result[key],
                                "checks": [c for c in checks if c["item"] == int(key[1:])]})
    seconds = time.perf_counter() - started
    args.out.mkdir(parents=True, exist_ok=True)
    output = args.out / "submission.csv"
    write_csv(output, rows)
    validate_csv(output, recs)
    metric = evaluate(labels, output)
    # Labels enter only after inference is complete; never pass them to helpers.
    truth = {r["id"]: r for r in validate_csv(labels, recs)}
    for change in changes:
        change["truth"] = int(truth[change["id"]][change["item"]])
    manifest = {"cpu_seconds": seconds, "records": len(recs),
                "source_predictions": str(args.predictions.resolve()),
                "source_predictions_sha256": hashlib.sha256(args.predictions.read_bytes()).hexdigest(),
                "rules_sources": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted((ROOT / "pps").glob("*.py"))}}
    for name, value in (("metrics", metric), ("changes", changes), ("manifest", manifest)):
        (args.out / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"macro_f1": metric["macro_f1"], "cpu_seconds": seconds,
                      "changed_labels": len(changes),
                      "corrections": sum(c["after"] == c["truth"] for c in changes),
                      "regressions": sum(c["after"] != c["truth"] for c in changes)}, indent=2))


if __name__ == "__main__":
    main()
