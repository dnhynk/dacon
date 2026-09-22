"""Blind teacher check of synthetic notice labels against an official-dev anchor; diagnostic, never gold.

Three task kinds share one item-group prompt (``cell_prompts``):

- ``target``: an edited pool notice, asked for the item group holding the edited
  cell.  Measures how often the teacher independently finds the constructed violation.
- ``host``: the same kind of notice, asked for v1-v8, which the edit cannot touch.
  Measures how often an unvetted natural host already fires.
- ``anchor``: official dev notices with a positive in a synthesized item.  The
  teacher's agreement with official labels under the same prompt is the yardstick
  for reading the ``target`` rate.

``prepare`` stages prompts and freezes the acceptance criteria before any call;
``run`` needs --execute; ``score`` reads dev labels only after responses exist.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import pathlib
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

try:
    from tools.independent_gold import cell_prompts, cell_runner
    from tools.independent_gold import claude_full_record_annotator as base
except ModuleNotFoundError:  # Direct script invocation.
    import cell_prompts  # type: ignore[no-redef]
    import cell_runner  # type: ignore[no-redef]
    import claude_full_record_annotator as base  # type: ignore[no-redef]


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEV_INPUT = ROOT / "data_open" / "dev.jsonl.gz"
DEV_LABELS = ROOT / "data_open" / "dev_labels.csv"
SCHEMA_VERSION = "dacon.independent.cell_vet.v1"
HOST_GROUP = "v1-8"
OTHER_HOST_GROUP = "v10-18"
CRITERIA = {
    "target_valid_if": "teacher_one_rate >= 0.70 and teacher_one_rate >= anchor_recall - 0.15",
    "host_zero_assumption_holds_if": "host_fired_rate <= 0.02",
    "unresolved_U_counts_as": "not one (target, anchor recall) / not fired (host), reported separately",
    "on_failure": "read the disagreeing cells before using that item's synthetic labels",
}


def _rank(*parts: str) -> str:
    return base.sha256_text("\0".join(parts))


def _plant_rows(path: pathlib.Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _dev_records() -> dict[str, dict[str, Any]]:
    with gzip.open(DEV_INPUT, "rt", encoding="utf-8") as handle:
        return {row["id"]: row for row in map(json.loads, filter(str.strip, handle))}


def anchor_candidates(item_groups: Mapping[str, Sequence[str]], label_rows: Mapping[str, Mapping[str, str]]) -> list[tuple[str, str]]:
    """(dev id, group) for every dev notice officially positive in a synthesized item of that group."""

    pairs = []
    for record_id, row in label_rows.items():
        for group, items in item_groups.items():
            if any(row[item] == "1" for item in items):
                pairs.append((record_id, group))
    return sorted(pairs)


def select(rows: Sequence[Mapping[str, Any]], targets_per_item: int, hosts_per_item: int) -> dict[str, list[Mapping[str, Any]]]:
    # One sample per (item, operation): an item reached by several operations is checked for each.
    by_item: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["kind"] in {"edited", "transplanted"} and row["split"] == "dev":
            by_item[(row["target_item"], row.get("operation", "").split(":")[0])].append(row)
    chosen: dict[str, list[Mapping[str, Any]]] = {"target": [], "host": []}
    for (item, _), candidates in sorted(by_item.items(), key=lambda pair: (int(pair[0][0][1:]), pair[0][1])):
        ordered = sorted(candidates, key=lambda row: _rank("vet", item, row["plant_id"]))
        chosen["target"].extend(ordered[:targets_per_item])
        chosen["host"].extend(ordered[:hosts_per_item])
    return chosen


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output_dir.resolve()
    if output.exists() or ROOT / "runs" not in output.parents:
        raise ValueError("vet output must be fresh under repository runs/")
    rows = _plant_rows(args.plants)
    if args.items:
        rows = [row for row in rows if row["target_item"] in args.items]
    if args.operations:
        rows = [row for row in rows if any(name in row.get("operation", "") for name in args.operations)]
    chosen = select(rows, args.targets_per_item, args.hosts_per_item)
    synthesized = sorted({row["target_item"] for row in chosen["target"]}, key=lambda name: int(name[1:]))
    item_groups: dict[str, list[str]] = defaultdict(list)
    for item in synthesized:
        item_groups[cell_prompts.group_of(item)].append(item)
    with DEV_LABELS.open(encoding="utf-8", newline="") as handle:
        label_rows = {row["id"]: row for row in csv.DictReader(handle)}
    anchors = [
        pair for group in item_groups
        for pair in sorted(
            (pair for pair in anchor_candidates(item_groups, label_rows) if pair[1] == group),
            key=lambda pair: _rank("anchor", *pair),
        )[:args.max_anchors_per_group]
    ]
    del label_rows  # Labels choose anchor notices only; no label reaches a prompt or the plan.
    dev = _dev_records()
    rubric = base.annotation_rubric(base.RUBRIC_PATH.read_text(encoding="utf-8"))
    plan: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []

    def add(kind: str, key: str, record: Mapping[str, Any], group: str, binding: dict[str, Any]) -> None:
        items = cell_prompts.GROUPS[group]
        call = cell_prompts.build_group_call(record, items, rubric=rubric)
        task_id = f"{kind}-{group}-{_rank(kind, group, key)[:16]}"
        entry = {"task_id": task_id, "kind": kind, "group": group, "items": list(items),
                 "record_id": record["id"], **binding}
        plan.append(entry)
        tasks.append({
            "task_id": task_id, "system": call["system"], "prompt": call["prompt"],
            "schema_json": call["schema_json"], "prompt_argument": call["prompt_argument"],
            "binding": {**entry, "context_sha256": call["context_sha256"]},
        })

    for row in chosen["target"]:
        add("target", row["plant_id"], row["record"], cell_prompts.group_of(row["target_item"]),
            {"plant_id": row["plant_id"], "target_item": row["target_item"], "operation": row["operation"]})
    for row in chosen["host"]:
        # Ask about a predicate family the edit cannot touch.
        group = OTHER_HOST_GROUP if cell_prompts.group_of(row["target_item"]) == HOST_GROUP else HOST_GROUP
        add("host", row["plant_id"], row["record"], group,
            {"plant_id": row["plant_id"], "target_item": row["target_item"],
             "expected_zero_items": [item for item in cell_prompts.GROUPS[group] if item in row["expected_zero_items"]]})
    for record_id, group in anchors:
        add("anchor", record_id, dev[record_id], group, {"dev_id": record_id})
    summary = cell_runner.run_tasks(tasks, output, executable=None)
    manifest = {
        "schema_version": SCHEMA_VERSION, "plants": str(args.plants), "criteria": CRITERIA,
        "plants_sha256": base.file_sha256(args.plants),
        "rubric_sha256": base.sha256_text(rubric),
        "tasks_by_kind": dict(Counter(entry["kind"] for entry in plan)),
        "prompt_bytes": sum(len(task["prompt"].encode("utf-8")) for task in tasks),
        "plan_sha256": base.sha256_object(plan),
    }
    base._write_json_new(output / "plan.json", {"tasks": plan})
    base._write_json_new(output / "manifest.json", manifest)
    return {**manifest, "staged": summary}


def _staged_tasks(output: pathlib.Path) -> list[dict[str, Any]]:
    plan = base.full_record_output.parse_output((output / "plan.json").read_bytes())["tasks"]
    tasks = []
    for entry in plan:
        task_dir = output / "tasks" / entry["task_id"]
        manifest = base.full_record_output.parse_output((task_dir / "task.json").read_bytes())
        tasks.append({
            "task_id": entry["task_id"], "binding": manifest["binding"],
            "prompt_argument": manifest["prompt_argument"],
            "system": (task_dir / "system_prompt.txt").read_text(encoding="utf-8"),
            "prompt": (task_dir / "prompt.txt").read_text(encoding="utf-8"),
            "schema_json": (task_dir / "output_schema.json").read_text(encoding="utf-8"),
        })
    return tasks


def run(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output_dir.resolve()
    tasks = _staged_tasks(output)
    if args.limit is not None:
        tasks = tasks[:args.limit]
    executable = None
    if args.execute:
        executable = cell_runner.resolve_cli(args.claude_bin, args.reviewed_cli_version)["resolved_executable"]
    return cell_runner.run_tasks(
        tasks, output, executable=executable, workers=args.workers, timeout=args.timeout,
        max_budget_usd=args.max_budget_usd, retry_failed=args.retry_failed,
    )


def _labels_of(output: pathlib.Path, entry: Mapping[str, Any]) -> dict[str, Any] | None:
    attempts = sorted((output / "tasks" / entry["task_id"] / "attempts").glob("attempt-*/structured_output.json"))
    if not attempts:
        return None
    structured = base.full_record_output.parse_output(attempts[-1].read_bytes())
    return cell_prompts.decisions_of(structured, entry["record_id"], entry["items"])


def score(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output_dir.resolve()
    plan = base.full_record_output.parse_output((output / "plan.json").read_bytes())["tasks"]
    with DEV_LABELS.open(encoding="utf-8", newline="") as handle:
        official = {row["id"]: row for row in csv.DictReader(handle)}
    target: dict[str, Counter[str]] = defaultdict(Counter)
    by_operation: dict[str, Counter[str]] = defaultdict(Counter)
    anchor_pos: dict[str, Counter[str]] = defaultdict(Counter)
    anchor_zero: Counter[str] = Counter()
    host: Counter[str] = Counter()
    disagreements: list[dict[str, Any]] = []
    answered = Counter()
    for entry in plan:
        labels = _labels_of(output, entry)
        if labels is None:
            continue
        answered[entry["kind"]] += 1
        if entry["kind"] == "target":
            label = labels[entry["target_item"]]
            target[entry["target_item"]][str(label)] += 1
            by_operation[f"{entry['target_item']}:{entry.get('operation', '').split(':')[0]}"][str(label)] += 1
            if label != 1:
                disagreements.append({"kind": "target", "task_id": entry["task_id"], "item": entry["target_item"], "teacher": label})
        elif entry["kind"] == "host":
            for item in entry["expected_zero_items"]:
                host[str(labels[item])] += 1
                if labels[item] == 1:
                    disagreements.append({"kind": "host", "task_id": entry["task_id"], "item": item, "teacher": 1})
        else:
            for item in entry["items"]:
                gold = official[entry["dev_id"]][item]
                if gold == "1":
                    anchor_pos[item][str(labels[item])] += 1
                else:
                    anchor_zero[str(labels[item])] += 1
                if str(labels[item]) != gold:
                    disagreements.append({"kind": "anchor", "task_id": entry["task_id"], "item": item,
                                          "teacher": labels[item], "official": int(gold)})
    verdicts = {}
    for item, counts in sorted(target.items(), key=lambda pair: int(pair[0][1:])):
        total = sum(counts.values())
        anchor_total = sum(anchor_pos[item].values())
        one_rate = counts["1"] / total
        anchor_recall = anchor_pos[item]["1"] / anchor_total if anchor_total else None
        verdicts[item] = {
            "n": total, "teacher_one_rate": one_rate, "U": counts["U"],
            "anchor_n": anchor_total, "anchor_recall": anchor_recall,
            "valid": one_rate >= 0.70 and (anchor_recall is None or one_rate >= anchor_recall - 0.15),
        }
    host_total = sum(host.values())
    result = {
        "answered": dict(answered), "target": verdicts,
        "target_by_operation": {name: dict(counts) for name, counts in sorted(by_operation.items())},
        "anchor_official_zero_cells": dict(anchor_zero),
        "host": {"cells": host_total, "fired": host["1"], "U": host["U"],
                 "fired_rate": host["1"] / host_total if host_total else None,
                 "assumption_holds": bool(host_total) and host["1"] / host_total <= 0.02},
        "disagreements": disagreements,
    }
    (output / "score.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    staged = commands.add_parser("prepare")
    staged.add_argument("--plants", type=pathlib.Path, required=True)
    staged.add_argument("--output-dir", type=pathlib.Path, required=True)
    staged.add_argument("--targets-per-item", type=int, default=10)
    staged.add_argument("--hosts-per-item", type=int, default=4)
    staged.add_argument("--max-anchors-per-group", type=int, default=12)
    staged.add_argument("--operations", type=lambda value: value.split(","), default=None,
                        help="comma-separated operation name fragments to check")
    staged.add_argument("--items", type=lambda value: value.split(","), default=None,
                        help="comma-separated target items to check (default: every item in the plants file)")
    runner = commands.add_parser("run")
    runner.add_argument("--output-dir", type=pathlib.Path, required=True)
    runner.add_argument("--execute", action="store_true")
    runner.add_argument("--workers", type=int, default=2)
    runner.add_argument("--limit", type=int, default=None)
    runner.add_argument("--timeout", type=float, default=1800)
    runner.add_argument("--max-budget-usd", type=float, default=5.0)
    runner.add_argument("--retry-failed", action="store_true")
    runner.add_argument("--claude-bin", default="claude")
    runner.add_argument("--reviewed-cli-version", action="append", default=[],
                        help="admit a CLI version newer than the annotator pin after reviewing its flags")
    scorer = commands.add_parser("score")
    scorer.add_argument("--output-dir", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = {"prepare": prepare, "run": run, "score": score}[args.command](args)
    except (OSError, ValueError, KeyError, base.full_record_context.FullRecordContextError) as exc:
        print(f"cell vet refused: {exc}", file=sys.stderr)
        return 2
    printable = {key: value for key, value in result.items() if key != "disagreements"}
    print(json.dumps(printable, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
