"""Post-inference official-dev diagnostic for the experimental Claude batch pilot.

Dev answers are read only after inference. This is a quality preview, never an
unlabeled gold release or a source of teacher prompt content.
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
from typing import Any

try:
    from tools.independent_gold import claude_batch_pilot as pilot
    from tools.independent_gold import diagnose_full_record_dev as scoring
    from tools.independent_gold import verify_claude_batch_pilot as verifier
except ModuleNotFoundError:  # Direct script invocation.
    import claude_batch_pilot as pilot  # type: ignore[no-redef]
    import diagnose_full_record_dev as scoring  # type: ignore[no-redef]
    import verify_claude_batch_pilot as verifier  # type: ignore[no-redef]


def diagnose(run_dir: pathlib.Path, source_archive: pathlib.Path | None = None) -> dict[str, Any]:
    verification = None
    if source_archive is not None:
        verification = verifier.verify(run_dir, source_archive)
        if verification["status"] != "executed_rows_structurally_verified_not_gold":
            raise ValueError("batch run has not passed full executed-row verification")
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != pilot.SCHEMA_VERSION or manifest.get("phase") != "blind_dev_batch_diagnostic_only":
        raise ValueError("not a blind dev batch pilot")
    if manifest.get("input_sha256") != scoring._sha256(scoring.DEV_INPUT):
        raise ValueError("organizer dev input hash mismatch")
    selected = manifest["selected_ids"]
    if not selected or len(selected) > 20 or len(selected) != len(set(selected)):
        raise ValueError("invalid selected dev IDs")
    start_index = manifest.get("start_index")
    if type(start_index) is not int or not 0 <= start_index < 200 or start_index + len(selected) > 200:
        raise ValueError("invalid pilot dev window")
    organizer_ids, total = pilot.base.select_ids(
        scoring.DEV_INPUT, [], shard_index=0, shard_count=1, limit=None,
    )
    if total != 200 or selected != organizer_ids[start_index:start_index + len(selected)]:
        raise ValueError("pilot selected IDs do not match exact organizer dev window")
    summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
    with scoring.DEV_LABELS.open("r", encoding="utf-8-sig", newline="") as source:
        official = {row["id"]: row for row in csv.DictReader(source)}
    if any(record_id not in official for record_id in selected):
        raise ValueError("selected ID missing from official labels")

    rows: dict[str, dict[str, Any]] = {}
    batch_size = manifest["batch_size"]
    for start in range(0, len(selected), batch_size):
        batch_dir = run_dir / f"batch-{start // batch_size:03d}"
        receipt_path = batch_dir / "receipt.json"
        if not receipt_path.exists():
            continue
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        stdout_path = batch_dir / "stdout.bin"
        if receipt.get("stdout_sha256") != scoring._sha256(stdout_path):
            raise ValueError("batch raw stdout hash mismatch")
        if receipt.get("status") not in {"ok", "partial_content_error"}:
            continue
        hashes = receipt.get("row_hashes")
        if not isinstance(hashes, dict):
            raise ValueError("batch row-hash ledger missing")
        batch_ids = set(selected[start:start + batch_size])
        if set(hashes) - batch_ids:
            raise ValueError("batch receipt names an unexpected ID")
        for record_id, expected_hash in hashes.items():
            row = json.loads((batch_dir / f"{record_id}.row.json").read_text(encoding="utf-8"))
            if row.get("id") != record_id or pilot.base.sha256_object(row) != expected_hash:
                raise ValueError("batch row hash or ID mismatch")
            cells = row["ledger"]["cells"]
            if len(cells) != 24 or {cell["item"] for cell in cells} != set(scoring.ITEMS):
                raise ValueError("batch row has incomplete decision set")
            if any(cell["label"] not in (0, 1, "U") for cell in cells):
                raise ValueError("batch row has an invalid decision label")
            if record_id in rows:
                raise ValueError("duplicate batch row")
            rows[record_id] = {cell["item"]: cell["label"] for cell in cells}

    counts = {item: {key: 0 for key in ("tp", "tn", "fp", "fn", "u")} for item in scoring.ITEMS}
    errors: list[dict[str, Any]] = []
    correct = compared = abstentions = 0
    for record_id in selected:
        if record_id not in rows:
            continue
        for item in scoring.ITEMS:
            gold = int(official[record_id][item])
            prediction = rows[record_id][item]
            compared += 1
            if prediction == "U":
                abstentions += 1
                counts[item]["u"] += 1
            elif prediction == gold:
                correct += 1
                counts[item]["tp" if gold else "tn"] += 1
            else:
                counts[item]["fp" if prediction else "fn"] += 1
            if prediction != gold:
                errors.append({"id": record_id, "item": item, "official": gold, "teacher": prediction})
    f1 = {}
    for item, entry in counts.items():
        denominator = 2 * entry["tp"] + entry["fp"] + entry["fn"]
        f1[item] = 2 * entry["tp"] / denominator if denominator else None
    defined = [value for value in f1.values() if value is not None]
    return {
        "schema_version": "dacon.independent.batch_dev_diagnostic.v1",
        "run_dir": str(run_dir.resolve()),
        "qualification_status": "diagnostic_only_not_gold",
        "raw_model_replay_verified": verification is not None,
        "verification_status": verification["status"] if verification is not None else None,
        "warning": None if verification is not None else "Row and receipt hashes are checked; a separate archived-source/raw-response replay is required before a quality gate or release.",
        "mode": manifest["mode"],
        "start_index": start_index,
        "complete": len(rows) == len(selected) and summary.get("safety_errors") == 0 and summary.get("records_content_error") == 0,
        "selected_records": len(selected),
        "scored_records": len(rows),
        "missing_records": [record_id for record_id in selected if record_id not in rows],
        "compared_cells": compared,
        "correct_binary_cells": correct,
        "abstention_cells": abstentions,
        "cell_accuracy_including_abstentions_as_incorrect": correct / compared if compared else None,
        "macro_f1_defined_items_preview": sum(defined) / len(defined) if defined else None,
        "per_item_counts": counts,
        "per_item_f1_preview": f1,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=pathlib.Path, required=True)
    parser.add_argument("--source-archive", type=pathlib.Path, required=True)
    parser.add_argument("--errors-limit", type=int, default=20)
    args = parser.parse_args()
    if args.errors_limit < 0:
        raise ValueError("errors-limit must be nonnegative")
    result = diagnose(args.run_dir.resolve(), args.source_archive.resolve())
    result["error_count"] = len(result["errors"])
    result["errors"] = result["errors"][:args.errors_limit]
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
