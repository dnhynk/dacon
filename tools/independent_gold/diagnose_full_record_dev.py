"""Post-inference official-dev diagnostic for independent full-record runs.

This reader never supplies dev answers to a model. Partial runs are previews,
not quality qualifications or a replacement for the organizer answer key.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import statistics
from typing import Any


ITEMS = tuple(f"v{i}" for i in range(1, 25))
ROOT = pathlib.Path(__file__).resolve().parents[2]
DEV_INPUT = ROOT / "data_open" / "dev.jsonl.gz"
DEV_LABELS = ROOT / "data_open" / "dev_labels.csv"


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def diagnose(run_dir: pathlib.Path) -> dict[str, Any]:
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("phase") not in {"official_dev", "development_diagnostic"}:
        raise ValueError("not a dev diagnostic run")
    if pathlib.Path(manifest["input_path"]).resolve() != DEV_INPUT.resolve():
        raise ValueError("run did not use organizer dev input")
    if manifest["input_sha256"] != _sha256(DEV_INPUT):
        raise ValueError("organizer dev input changed since inference")
    with DEV_LABELS.open("r", encoding="utf-8-sig", newline="") as source:
        official = {row["id"]: row for row in csv.DictReader(source)}
    selected = manifest["selected_ids"]
    if len(selected) != len(set(selected)) or any(record_id not in official for record_id in selected):
        raise ValueError("run has invalid or duplicate dev IDs")
    rows: dict[str, dict[str, Any]] = {}
    path = run_dir / "full_records.jsonl"
    if path.exists():
        with path.open("r", encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                row = json.loads(line)
                record_id = row["id"]
                if record_id not in selected or record_id in rows:
                    raise ValueError("unexpected or duplicate output row")
                cells = row["ledger"]["cells"]
                if len(cells) != 24 or {cell["item"] for cell in cells} != set(ITEMS):
                    raise ValueError(f"{record_id}: incomplete decision set")
                if any(cell["label"] not in (0, 1, "U") for cell in cells):
                    raise ValueError(f"{record_id}: invalid decision label")
                rows[record_id] = {cell["item"]: cell["label"] for cell in cells}
    errors: list[dict[str, Any]] = []
    counts = {item: {"tp": 0, "tn": 0, "fp": 0, "fn": 0, "u": 0} for item in ITEMS}
    correct = 0
    compared = 0
    abstentions = 0
    for record_id in selected:
        if record_id not in rows:
            continue
        for item in ITEMS:
            gold = int(official[record_id][item])
            pred = rows[record_id][item]
            compared += 1
            if pred == "U":
                abstentions += 1
                counts[item]["u"] += 1
                errors.append({"id": record_id, "item": item, "official": gold, "teacher": "U"})
                continue
            if pred == gold:
                correct += 1
                counts[item]["tp" if gold else "tn"] += 1
            else:
                counts[item]["fp" if pred else "fn"] += 1
                errors.append({"id": record_id, "item": item, "official": gold, "teacher": pred})
    f1 = {}
    for item, entry in counts.items():
        denominator = 2 * entry["tp"] + entry["fp"] + entry["fn"]
        f1[item] = 2 * entry["tp"] / denominator if denominator else None
    nonempty_f1 = [value for value in f1.values() if value is not None]
    summary_path = run_dir / "run_summary.json"
    run_summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else None
    complete = len(rows) == len(selected) and run_summary is not None and run_summary.get("tasks_error") == 0
    return {
        "schema_version": "dacon.independent.full_record_dev_diagnostic.v1",
        "run_dir": str(run_dir.resolve()),
        "qualification_status": "diagnostic_only_not_gold",
        "complete": complete,
        "selected_records": len(selected),
        "scored_records": len(rows),
        "missing_records": [record_id for record_id in selected if record_id not in rows],
        "compared_cells": compared,
        "correct_binary_cells": correct,
        "abstention_cells": abstentions,
        "cell_accuracy_including_abstentions_as_incorrect": correct / compared if compared else None,
        "macro_f1_defined_items_preview": statistics.mean(nonempty_f1) if nonempty_f1 else None,
        "per_item_counts": counts,
        "per_item_f1_preview": f1,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=pathlib.Path, required=True)
    parser.add_argument("--errors-limit", type=int, default=20)
    args = parser.parse_args()
    report = diagnose(args.run_dir.resolve())
    if args.errors_limit < 0:
        raise ValueError("errors-limit must be nonnegative")
    report["error_count"] = len(report["errors"])
    report["errors"] = report["errors"][: args.errors_limit]
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
