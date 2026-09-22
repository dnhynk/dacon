"""Export synthetic notices as pipeline input and score a submission CSV against the constructed cells.

``export`` writes organizer-shaped records only (no label, no bookkeeping) plus a separate answer
key.  ``score`` reads a 49-column submission and reports, per item and operation, recall on the
cells the edits made positive and the firing rate on the remaining cells.  Those remaining cells
come from unvetted natural notices, so their firing rate is an upper bound on false positives,
not a false-positive rate.  This module never imports the production package.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import pathlib
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "dacon.independent.cell_score.v1"
ITEMS = tuple(f"v{number}" for number in range(1, 25))


def _rank(*parts: str) -> str:
    import hashlib

    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _rows(path: pathlib.Path, split: str | None = None) -> list[dict[str, Any]]:
    exclusions_path = path.with_name("price_exclusions.json")
    exclusions = json.loads(exclusions_path.read_text(encoding="utf-8")) if exclusions_path.exists() else {}
    bad_hosts = set(exclusions.get("invalid_host_ids", []))
    price_items = set(exclusions.get("price_premised_items", []))
    def excluded(row):
        if row.get("host_id") not in bad_hosts:
            return False
        cells = [(row["target_item"], row["operation"])] + [
            (extra["target_item"], extra["operation"]) for extra in row.get("extra_targets", [])]
        return any(item in price_items or (item == "v24" and operation != "swap_meta_license")
                   for item, operation in cells)
    with path.open(encoding="utf-8") as handle:
        rows = (json.loads(line) for line in handle if line.strip()
                and (split is None or re.search(r'"split"\s*:\s*"' + re.escape(split) + r'"', line)))
        selected = [row for row in rows if not excluded(row)]
    apply_scoring_exclusions(selected, path.with_name("scoring_exclusions.json"))
    return selected


def apply_scoring_exclusions(rows: Sequence[dict[str, Any]], path: pathlib.Path) -> None:
    """Overlay operation quarantine without rewriting sealed records or selecting by ID.

    Exclusions affect scoring only: export still contains every selected record.
    The same operation/item/mode rules apply mechanically to both splits.
    """
    if not path.exists():
        return
    policy = json.loads(path.read_text(encoding="utf-8"))
    rules = policy["excluded_from_scoring"]
    for rule in rules:
        if (set(rule) != {"operation", "target_item", "mode", "reason"}
                or rule["target_item"] not in ITEMS
                or rule["mode"] not in {"violation", "near_miss"}
                or not rule["operation"] or not rule["reason"]):
            raise ValueError("invalid scoring exclusion rule")
    for row in rows:
        mode = "near_miss" if row.get("near_miss_items") else "violation"
        reasons = [rule["reason"] for rule in rules
                   if (row.get("operation"), row.get("target_item"), mode)
                   == (rule["operation"], rule["target_item"], rule["mode"])]
        if reasons:
            row["excluded_from_scoring"] = True
            row["scoring_exclusion_reasons"] = reasons


def targets_of(row: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(item, operation) for every cell the row's edits made positive."""

    cells = [] if row.get("near_miss_items") else [(row["target_item"], row["operation"].split(":")[0])]
    cells.extend((extra["target_item"], extra["operation"]) for extra in row.get("extra_targets", []))
    return cells


def select(rows: Sequence[Mapping[str, Any]], split: str, per_item: int | None,
           *, include_all_near_written: bool = False, near_per_item: int | None = None,
           include_all_written: bool = False) -> list[Mapping[str, Any]]:
    by_item: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["split"] == split and row["kind"] in {"edited", "transplanted"}:
            by_item[row["target_item"] + (":near_miss" if row.get("near_miss_items") else "")].append(row)
    chosen = []
    for item in sorted(by_item, key=lambda name: (int(name.split(":")[0][1:]), name)):
        ordered = sorted(by_item[item], key=lambda row: _rank("measure", row["plant_id"]))
        if include_all_written:
            chosen.extend(row for row in ordered if row["operation"] == "written")
            ordered = [row for row in ordered if row["operation"] != "written"]
        if include_all_near_written:
            chosen.extend(row for row in ordered if row.get("near_miss_items") or row["operation"] == "written")
            ordered = [row for row in ordered if not row.get("near_miss_items") and row["operation"] != "written"]
        limit = near_per_item if ":near_miss" in item and near_per_item is not None else per_item
        chosen.extend(ordered if limit is None else ordered[:limit])
    # The pipeline sees a shuffled order, not the items in sequence.
    return sorted(chosen, key=lambda row: _rank("order", row["plant_id"]))


def export(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output_dir.resolve()
    if output.exists() or ROOT / "runs" not in output.parents:
        raise ValueError("export output must be fresh under repository runs/")
    chosen = select(_rows(args.plants, args.split), args.split, args.per_item,
                    include_all_near_written=getattr(args, "include_all_near_written", False),
                    near_per_item=getattr(args, "near_per_item", None),
                    include_all_written=getattr(args, "include_all_written", False))
    ids = [row["record"]["id"] for row in chosen]
    if len(set(ids)) != len(ids):
        raise ValueError("record ids are not unique")
    output.mkdir(parents=True)
    with gzip.open(output / "input.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in chosen:
            handle.write(json.dumps(row["record"], ensure_ascii=False) + "\n")
    key = {
        row["record"]["id"]: {
            "plant_id": row["plant_id"], "split": row["split"],
            "targets": [{"item": item, "operation": operation} for item, operation in targets_of(row)],
            "expected_zero_items": row["expected_zero_items"],
            "near_miss_items": row.get("near_miss_items", []),
            "evidence_spans": row.get("evidence_spans", {}),
            "documents": [doc["text"] for doc in row["record"]["docs"]],
            "host_id": row.get("host_id", row["record"]["id"]),
            "operation": row["operation"], "writer_model": row.get("writer_model"),
            "writer_family": row.get("writer_family", "deterministic"),
            **({"excluded_from_scoring": True,
                "scoring_exclusion_reasons": row.get("scoring_exclusion_reasons", [])}
               if row.get("excluded_from_scoring") else {}),
        }
        for row in chosen
    }
    manifest = {
        "schema_version": SCHEMA_VERSION, "plants": str(args.plants), "split": args.split,
        "per_item": args.per_item, "records": len(chosen),
        "near_per_item": getattr(args, "near_per_item", None),
        "include_all_written": getattr(args, "include_all_written", False),
        "scoring_exclusions_sha256": (hashlib.sha256(args.plants.with_name("scoring_exclusions.json").read_bytes()).hexdigest()
                                      if args.plants.with_name("scoring_exclusions.json").exists() else None),
        "excluded_from_scoring_records": sum(bool(row.get("excluded_from_scoring")) for row in chosen),
        "price_exclusions_applied": args.plants.with_name("price_exclusions.json").exists(),
        "price_exclusions_sha256": (hashlib.sha256(args.plants.with_name("price_exclusions.json").read_bytes()).hexdigest()
                                    if args.plants.with_name("price_exclusions.json").exists() else None),
        "records_by_item": dict(sorted(Counter(row["target_item"] for row in chosen).items(),
                                       key=lambda pair: int(pair[0][1:]))),
    }
    (output / "key.json").write_text(json.dumps(key, ensure_ascii=False), encoding="utf-8")
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def wilson(successes: int, total: int) -> tuple[float, float] | None:
    if total == 0:
        return None
    z = 1.959963984540054
    centre = (successes + z * z / 2) / (total + z * z)
    half = z * math.sqrt(successes * (total - successes) / total + z * z / 4) / (total + z * z)
    return max(0.0, centre - half), min(1.0, centre + half)


def evidence_match(quote: str, documents: Sequence[str], spans: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Exact quote occurrence; max character overlap / gold span length, end exclusive."""
    quote = quote.strip()
    if not quote:
        return {"source_substring": 0.0, "gold_overlap": 0.0}
    found, overlap = False, 0.0
    for index, document in enumerate(documents):
        start = document.find(quote)
        while start >= 0:
            found = True
            for span in spans:
                if span["doc_index"] == index:
                    size = span["end"] - span["start"]
                    if size > 0:
                        overlap = max(overlap, max(0, min(start + len(quote), span["end"]) - max(start, span["start"])) / size)
            start = document.find(quote, start + 1)
    return {"source_substring": float(found), "gold_overlap": overlap}


def score_submission(key: Mapping[str, Mapping[str, Any]], predictions: Mapping[str, Mapping[str, str]],
                     *, holdout_authorized: bool = False) -> dict[str, Any]:
    if any(entry.get("split", "dev") != "dev" for entry in key.values()) and not holdout_authorized:
        raise ValueError("holdout requires the ledger-guarded score command")
    missing = sorted(set(key) - set(predictions))
    if missing:
        raise ValueError(f"submission lacks {len(missing)} records")
    recall: dict[str, Counter[str]] = defaultdict(Counter)
    by_operation: dict[str, Counter[str]] = defaultdict(Counter)
    zero: dict[str, Counter[str]] = defaultdict(Counter)
    near: dict[str, Counter[str]] = defaultdict(Counter)
    evidence: dict[str, Counter[str]] = defaultdict(Counter)
    excluded: Counter[str] = Counter()
    for record_id, entry in key.items():
        row = predictions[record_id]
        if any(row.get(item) not in {"0", "1"} for item in ITEMS):
            raise ValueError("submission contains missing or nonbinary values")
        if entry.get("excluded_from_scoring"):
            excluded.update(entry.get("scoring_exclusion_reasons") or ["explicit exclusion"])
            continue
        for item in entry.get("near_miss_items", []):
            if item in {target["item"] for target in entry["targets"]}:
                raise ValueError("a cell cannot be both positive and near-miss")
            near[item]["fired" if row[item] == "1" else "quiet"] += 1
        for target in entry["targets"]:
            hit = "hit" if row[target["item"]] == "1" else "miss"
            recall[target["item"]][hit] += 1
            by_operation[f"{target['item']}:{target['operation']}"][hit] += 1
            spans = entry.get("evidence_spans", {}).get(target["item"], [])
            if spans:
                item = target["item"]
                evidence[item]["targets"] += 1
                match = evidence_match(row.get("e" + item[1:], ""), entry.get("documents", []), spans)
                evidence[item].update(match)
                evidence[item]["hit_and_overlap"] += int(hit == "hit" and match["gold_overlap"] > 0)
        for item in entry["expected_zero_items"]:
            zero[item]["fired" if row[item] == "1" else "quiet"] += 1
    items = {}
    for item in ITEMS:
        hits, misses = recall[item]["hit"], recall[item]["miss"]
        fired, quiet = zero[item]["fired"], zero[item]["quiet"]
        items[item] = {
            "targets": hits + misses, "recall": hits / (hits + misses) if hits + misses else None,
            "recall_ci95": wilson(hits, hits + misses),
            "expected_zero_cells": fired + quiet,
            "fired_rate": fired / (fired + quiet) if fired + quiet else None,
            "near_miss_cells": near[item]["fired"] + near[item]["quiet"],
            "near_miss_fired": near[item]["fired"],
            "near_miss_firing_rate": near[item]["fired"] / sum(near[item].values()) if near[item] else None,
            "near_miss_ci95": wilson(near[item]["fired"], sum(near[item].values())),
            "evidence_targets": evidence[item]["targets"],
            "evidence_source_substring_rate": evidence[item]["source_substring"] / evidence[item]["targets"] if evidence[item]["targets"] else None,
            "evidence_mean_gold_overlap": evidence[item]["gold_overlap"] / evidence[item]["targets"] if evidence[item]["targets"] else None,
            "evidence_hit_and_overlap_rate": evidence[item]["hit_and_overlap"] / evidence[item]["targets"] if evidence[item]["targets"] else None,
        }
    scored = [value["recall"] for value in items.values() if value["recall"] is not None]
    return {
        "records": len(key), "items": items,
        "scored_records": sum(not entry.get("excluded_from_scoring", False) for entry in key.values()),
        "excluded_from_scoring_records": sum(bool(entry.get("excluded_from_scoring")) for entry in key.values()),
        "scoring_exclusion_reasons": dict(sorted(excluded.items())),
        "mean_item_recall": sum(scored) / len(scored) if scored else None,
        "by_operation": {
            name: {"targets": counts["hit"] + counts["miss"],
                   "recall": counts["hit"] / (counts["hit"] + counts["miss"])}
            for name, counts in sorted(by_operation.items(), key=lambda pair: (int(pair[0].split(":")[0][1:]), pair[0]))
        },
        "expected_zero_fired": sum(counts["fired"] for counts in zero.values()),
        "expected_zero_cells": sum(counts["fired"] + counts["quiet"] for counts in zero.values()),
        "near_miss_fired": sum(counts["fired"] for counts in near.values()),
        "near_miss_cells": sum(sum(counts.values()) for counts in near.values()),
    }


def score(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = args.key.parent / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("plants"):
            exclusions = pathlib.Path(manifest["plants"]).with_name("price_exclusions.json")
            current = hashlib.sha256(exclusions.read_bytes()).hexdigest() if exclusions.exists() else None
            if current != manifest.get("price_exclusions_sha256"):
                raise ValueError("price exclusions changed; re-export the key before scoring")
            exclusions = pathlib.Path(manifest["plants"]).with_name("scoring_exclusions.json")
            current = hashlib.sha256(exclusions.read_bytes()).hexdigest() if exclusions.exists() else None
            if current != manifest.get("scoring_exclusions_sha256"):
                raise ValueError("scoring exclusions changed; re-export the key before scoring")
    key = json.loads(args.key.read_text(encoding="utf-8"))
    with args.submission.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        predictions = {row["id"]: row for row in rows}
    if len(rows) != len(predictions):
        raise ValueError("duplicate prediction IDs")
    holdout = any(entry.get("split", "dev") != "dev" for entry in key.values())
    if holdout:
        reserve_holdout_score(args)
    result = score_submission(key, predictions, holdout_authorized=holdout)
    if args.output is not None:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def reserve_holdout_score(args: argparse.Namespace) -> None:
    """Reserve before evaluating; failed attempts also consume the round. Atomic round lock."""
    import datetime
    import hashlib

    ledger = getattr(args, "holdout_ledger", None)
    round_id = getattr(args, "round_id", None)
    fingerprint = getattr(args, "source_fingerprint", None)
    purpose = getattr(args, "purpose", None)
    if not ledger or not round_id or not purpose or not re.fullmatch(r"[a-f0-9]{64}", fingerprint or ""):
        raise ValueError("holdout requires ledger, round-id, source-fingerprint (SHA256), and purpose")
    if not ledger.exists():
        raise ValueError("sealed holdout ledger does not exist")
    # Bind each exported holdout key to the seal's canonical ledger, not a user-selected empty file.
    manifest_path = args.key.parent / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        canonical = pathlib.Path(manifest["plants"]).resolve().parent / "HOLDOUT_LEDGER.md"
        if canonical.resolve() != ledger.resolve():
            raise ValueError("ledger differs from the sealed pool ledger")
    safe_round = hashlib.sha256(round_id.encode()).hexdigest()
    lock = ledger.parent / (".holdout_round_" + safe_round)
    try:
        with lock.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps({"round": round_id, "source_fingerprint": fingerprint}))
    except FileExistsError as exc:
        raise ValueError("holdout already scored or reserved this round") from exc
    clean = lambda value: str(value).replace("\n", " ").replace("\r", " ").replace("|", "/")
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(f"| {datetime.datetime.now(datetime.timezone.utc).isoformat()} | {clean(round_id)} | {fingerprint} | {clean(purpose)} |\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    exporter = commands.add_parser("export")
    exporter.add_argument("--plants", type=pathlib.Path, required=True)
    exporter.add_argument("--output-dir", type=pathlib.Path, required=True)
    exporter.add_argument("--split", choices=("dev", "holdout"), required=True)
    exporter.add_argument("--per-item", type=int, default=None)
    exporter.add_argument("--include-all-near-written", action="store_true")
    exporter.add_argument("--include-all-written", action="store_true")
    exporter.add_argument("--near-per-item", type=int, default=None)
    scorer = commands.add_parser("score")
    scorer.add_argument("--key", type=pathlib.Path, required=True)
    scorer.add_argument("--submission", type=pathlib.Path, required=True)
    scorer.add_argument("--output", type=pathlib.Path, default=None)
    scorer.add_argument("--holdout-ledger", type=pathlib.Path)
    scorer.add_argument("--round-id")
    scorer.add_argument("--source-fingerprint")
    scorer.add_argument("--purpose")
    args = parser.parse_args(argv)
    try:
        result = {"export": export, "score": score}[args.command](args)
    except (OSError, ValueError, KeyError) as exc:
        print(f"cell score refused: {exc}", file=sys.stderr)
        return 2
    if args.command == "score":
        print("item  targets recall  [95% CI]      zero-cells fired")
        for item, value in result["items"].items():
            if value["targets"] or value["expected_zero_cells"]:
                ci = value["recall_ci95"]
                recall = "   -" if value["recall"] is None else f"{value['recall']:.2f}"
                band = "      -     " if ci is None else f"[{ci[0]:.2f}, {ci[1]:.2f}]"
                fired = "-" if value["fired_rate"] is None else f"{value['fired_rate']:.3f}"
                print(f"{item:5s} {value['targets']:6d}  {recall}  {band}  {value['expected_zero_cells']:8d}  {fired}")
        print(f"mean item recall {result['mean_item_recall']} | expected-zero fired "
              f"{result['expected_zero_fired']}/{result['expected_zero_cells']}")
        print(f"near-miss fired {result['near_miss_fired']}/{result['near_miss_cells']}; per-item Wilson/evidence metrics in JSON")
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
