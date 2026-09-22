"""Post-hoc diagnostic comparison of a blind report with provisional votes.

This never supplies peer votes to the blind reviewer, calls a model, or
qualifies any consensus as gold. It reads only the named independent runs.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from collections import Counter
from typing import Any


CELL_RE = re.compile(r"^\|\s*(PPS-D-\d+)\s+(v(?:[1-9]|1\d|2[0-4]))\s*\|\s*([01U])\s*\|")


def review_cells(path: pathlib.Path) -> dict[tuple[str, str], str]:
    cells: dict[tuple[str, str], str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = CELL_RE.match(line)
        if match is None:
            continue
        key = (match.group(1), match.group(2))
        if key in cells:
            raise ValueError(f"duplicate blind cell: {key}")
        cells[key] = match.group(3)
    if not cells:
        raise ValueError("no blind-review cell table found")
    return cells


def provider_votes(paths: list[pathlib.Path], ids: set[str]) -> dict[str, dict[str, Any]]:
    votes: dict[str, dict[str, Any]] = {}
    for path in paths:
        with (path / "full_records.jsonl").open("r", encoding="utf-8") as source:
            for line in source:
                row = json.loads(line)
                record_id = row["id"]
                if record_id not in ids:
                    continue
                cells = row["ledger"]["cells"]
                if len(cells) != 24 or len({cell["item"] for cell in cells}) != 24:
                    raise ValueError(f"incomplete peer ledger: {path.name}/{record_id}")
                labels = {cell["item"]: str(cell["label"]) for cell in cells}
                if any(value not in {"0", "1", "U"} for value in labels.values()):
                    raise ValueError(f"invalid peer label: {path.name}/{record_id}")
                entry = {"labels": labels, "source_sha256": row["source_sha256"], "run": path.name}
                previous = votes.get(record_id)
                if previous is not None and (
                    previous["labels"] != entry["labels"] or previous["source_sha256"] != entry["source_sha256"]
                ):
                    raise ValueError(f"conflicting same-provider votes: {record_id}")
                votes[record_id] = entry
    return votes


def compare(report: pathlib.Path, sol_runs: list[pathlib.Path], claude_runs: list[pathlib.Path]) -> dict[str, Any]:
    cells = review_cells(report)
    ids = {record_id for record_id, _ in cells}
    sol = provider_votes(sol_runs, ids)
    claude = provider_votes(claude_runs, ids)
    rows = []
    counts: Counter[str] = Counter()
    for (record_id, item), blind in cells.items():
        a = sol.get(record_id)
        b = claude.get(record_id)
        if a is None or b is None:
            raise ValueError(f"missing peer vote: {record_id}")
        if a["source_sha256"] != b["source_sha256"]:
            raise ValueError(f"peer organizer source hash differs: {record_id}")
        sol_label, claude_label = a["labels"][item], b["labels"][item]
        if sol_label == claude_label:
            relation = "peer_agreement"
        elif blind == sol_label:
            relation = "matches_sol"
        elif blind == claude_label:
            relation = "matches_claude"
        else:
            relation = "neither_or_unresolved"
        counts[relation] += 1
        counts[f"blind_{blind}"] += 1
        rows.append({"id": record_id, "item": item, "blind": blind,
                     "sol": sol_label, "claude": claude_label,
                     "relation": relation, "source_sha256": a["source_sha256"],
                     "sol_run": a["run"], "claude_run": b["run"]})
    return {"schema_version": "dacon.independent.blind_posthoc_compare.v1",
            "status": "diagnostic_only_not_gold", "report": str(report.resolve()),
            "cells": len(rows), "counts": dict(sorted(counts.items())), "rows": rows,
            "limitations": ["peer rows are provisional, not gold",
                            "agreement is not correctness",
                            "old Claude source archives may be unavailable; inspect each run lineage before release"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=pathlib.Path, required=True)
    parser.add_argument("--sol-run", type=pathlib.Path, action="append", required=True)
    parser.add_argument("--claude-run", type=pathlib.Path, action="append", required=True)
    args = parser.parse_args()
    print(json.dumps(compare(args.report, args.sol_run, args.claude_run), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
