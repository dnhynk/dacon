"""Fresh, restartable supervisor for the 200 x 100 organizer-only Claude run.

Default is read-only planning. --execute creates a new supervisor root; --resume
reuses only a root with the exact frozen plan. A partial shard is never recalled:
it requires manual reconciliation because an unreceipted CLI call may have run.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any

try:
    from tools.independent_gold import claude_full_record_annotator as runner
    from tools.independent_gold import context_preflight
except ModuleNotFoundError:  # Direct script invocation.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from tools.independent_gold import claude_full_record_annotator as runner  # type: ignore[no-redef]
    from tools.independent_gold import context_preflight  # type: ignore[no-redef]


ROOT = pathlib.Path(__file__).resolve().parents[2]
INPUT = ROOT / "data_open" / "train_unlabeled.jsonl.gz"
RUNNER = pathlib.Path(runner.__file__).resolve()
TOTAL_SHARDS = 200
RECORDS_PER_SHARD = 100
EXTERNAL_SHARD_ROOT = ROOT / "runs" / "self_label_20000_20260918"
PLAN_SCHEMA = "dacon.independent.claude_shard_supervisor_plan.v1"
EVENT_SCHEMA = "dacon.independent.claude_shard_supervisor_event.v1"


class SupervisorError(ValueError):
    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _write_new(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write((runner.canonical_json(value) + "\n").encode("utf-8"))


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SupervisorError(f"expected JSON object: {path}")
    return value


def _fresh_root(path: pathlib.Path) -> pathlib.Path:
    resolved = path.resolve()
    runs_root = (ROOT / "runs").resolve()
    if runs_root not in resolved.parents or resolved.exists():
        raise SupervisorError("new output root must be a fresh directory below runs/")
    return resolved


def _resume_root(path: pathlib.Path) -> pathlib.Path:
    resolved = path.resolve()
    runs_root = (ROOT / "runs").resolve()
    if runs_root not in resolved.parents or not resolved.is_dir():
        raise SupervisorError("resume root must be an existing supervisor directory below runs/")
    if not (resolved / "plan.json").is_file():
        raise SupervisorError("resume root has no frozen supervisor plan")
    return resolved


def _ids_by_shard() -> list[list[str]]:
    ids, count = runner.select_ids(INPUT, [], shard_index=0, shard_count=1, limit=None)
    if count != TOTAL_SHARDS * RECORDS_PER_SHARD or len(ids) != count:
        raise SupervisorError(f"expected exactly 20,000 unique organizer records, got {count}")
    shards = [ids[index::TOTAL_SHARDS] for index in range(TOTAL_SHARDS)]
    if any(len(shard) != RECORDS_PER_SHARD for shard in shards):
        raise SupervisorError("200 x 100 selection invariant failed")
    return shards


def _validated_admission_report(path: pathlib.Path, ids: Sequence[str]) -> dict[str, Any]:
    """Bind a complete PASS preflight to this exact organizer file and code."""

    resolved = path.resolve(strict=True)
    if (ROOT / "runs").resolve() not in resolved.parents:
        raise SupervisorError("source admission report must be under runs/")
    report = _read_json(resolved)
    expected_count = TOTAL_SHARDS * RECORDS_PER_SHARD
    counts = report.get("counts")
    source = report.get("input")
    if (report.get("schema_version") != context_preflight.SCHEMA_VERSION
            or report.get("semantic_role") != "organizer_source_admission_only_not_labels_or_model_staging"
            or report.get("status") != "pass"
            or not isinstance(counts, Mapping)
            or not isinstance(source, Mapping)):
        raise SupervisorError("source admission report is not a valid PASS report")
    if any(counts.get(key) != expected_count for key in
           ("expected_records", "processed", "unique_ids", "passed")) or counts.get("failed") != 0:
        raise SupervisorError("source admission did not pass every organizer record")
    if report.get("errors") != [] or report.get("fatal_error") is not None:
        raise SupervisorError("source admission has recorded errors")
    source_sha = runner.file_sha256(INPUT)
    if (source.get("path") != str(INPUT.resolve()) or source.get("bytes") != INPUT.stat().st_size
            or source.get("sha256_before") != source_sha or source.get("sha256_after") != source_sha):
        raise SupervisorError("source admission input differs from current organizer source")
    digest = hashlib.sha256()
    for record_id in ids:
        digest.update((record_id + "\n").encode("utf-8"))
    if report.get("id_order_sha256") != digest.hexdigest():
        raise SupervisorError("source admission ID order differs from organizer source")
    if report.get("context_schema_version") != runner.full_record_context.SCHEMA_VERSION:
        raise SupervisorError("source admission context schema changed")
    expected_software = {
        "context_preflight": pathlib.Path(context_preflight.__file__).resolve(),
        "full_record_context": pathlib.Path(runner.full_record_context.__file__).resolve(),
        "fact_context": pathlib.Path(runner.full_record_context.fact_context.__file__).resolve(),
        "qualification_context": pathlib.Path(runner.full_record_context.qualification_context.__file__).resolve(),
        "law_context": pathlib.Path(runner.full_record_context.law_context.__file__).resolve(),
    }
    software = report.get("software")
    if not isinstance(software, Mapping) or set(software) != set(expected_software):
        raise SupervisorError("source admission software manifest is incomplete")
    for name, current_path in expected_software.items():
        entry = software[name]
        current_sha = runner.file_sha256(current_path)
        if (not isinstance(entry, Mapping) or entry.get("path") != str(current_path)
                or entry.get("sha256_before") != current_sha or entry.get("sha256_after") != current_sha):
            raise SupervisorError(f"source admission software changed: {name}")
    rows = report.get("record_rows")
    expected_rows_path = resolved.parent / "records.jsonl"
    if (not isinstance(rows, Mapping) or rows.get("path") != str(expected_rows_path)
            or rows.get("sha256") != runner.file_sha256(expected_rows_path)):
        raise SupervisorError("source admission row ledger hash/path differs")
    with expected_rows_path.open("rt", encoding="utf-8") as handle:
        observed = [json.loads(line) for line in handle if line.strip()]
    if not all(isinstance(row, Mapping) for row in observed):
        raise SupervisorError("source admission row ledger contains a non-object")
    if len(observed) != expected_count or [row.get("id") for row in observed] != list(ids):
        raise SupervisorError("source admission rows do not match all organizer IDs")
    if any(row.get("status") != "pass" for row in observed):
        raise SupervisorError("source admission row ledger contains a failure")
    return {
        "path": str(resolved),
        "report_file_sha256": runner.file_sha256(resolved),
        "record_rows_sha256": rows["sha256"],
        "id_order_sha256": digest.hexdigest(),
        "status": "pass",
    }


def build_plan(args: argparse.Namespace, root: pathlib.Path) -> tuple[dict[str, Any], list[list[str]]]:
    if not 0 <= args.first_shard_index <= args.last_shard_index < TOTAL_SHARDS:
        raise SupervisorError("invalid inclusive shard index range")
    if args.max_new_shards is not None and args.max_new_shards < 1:
        raise SupervisorError("max_new_shards must be positive")
    if not math.isfinite(args.poll_seconds) or args.poll_seconds < 1:
        raise SupervisorError("poll_seconds must be at least one")
    if not 0 < args.max_budget_usd <= 100:
        raise SupervisorError("max_budget_usd must be positive and at most 100")
    shards = _ids_by_shard()
    ordered_ids = [
        shards[index][ordinal]
        for ordinal in range(RECORDS_PER_SHARD)
        for index in range(TOTAL_SHARDS)
    ]
    admission = _validated_admission_report(args.admission_report, ordered_ids)
    original_rubric = runner.RUBRIC_PATH.read_text(encoding="utf-8")
    projected_rubric = runner.annotation_rubric(original_rubric)
    cli = runner.resolve_cli(args.claude_bin)
    source_bundle = runner.source_bundle()
    plan: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA,
        "output_root": str(root),
        "input_path": str(INPUT.resolve()),
        "input_sha256": runner.file_sha256(INPUT),
        "source_admission": admission,
        "record_count": TOTAL_SHARDS * RECORDS_PER_SHARD,
        "total_shards": TOTAL_SHARDS,
        "records_per_shard": RECORDS_PER_SHARD,
        "first_shard_index": args.first_shard_index,
        "last_shard_index": args.last_shard_index,
        "context_mode": args.context_mode,
        "max_budget_usd_per_call": args.max_budget_usd,
        "runner_source_bundle": source_bundle,
        "supervisor_source_sha256": runner.file_sha256(pathlib.Path(__file__)),
        "rubric_source_sha256": runner.sha256_text(original_rubric),
        "rubric_projection_sha256": runner.sha256_text(projected_rubric),
        "output_schema_sha256": runner.sha256_object(runner.full_record_output.output_schema()),
        "cli": cli,
        "phase": "unlabeled_20000",
        "continue_on_content_error": True,
        "allow_external_overlap": bool(args.allow_external_overlap),
        "shards": [
            {
                "index": index,
                "count": RECORDS_PER_SHARD,
                "selected_ids_sha256": runner.sha256_object(shards[index]),
                "relative_output": f"shards/shard-{index:03d}-of-{TOTAL_SHARDS}",
            }
            for index in range(args.first_shard_index, args.last_shard_index + 1)
        ],
    }
    plan["plan_sha256"] = runner.sha256_object(plan)
    return plan, shards


def _verify_plan_file(root: pathlib.Path, expected: Mapping[str, Any]) -> None:
    stored = _read_json(root / "plan.json")
    digest = stored.pop("plan_sha256", None)
    if digest != runner.sha256_object(stored):
        raise SupervisorError("stored supervisor plan hash mismatch")
    stored["plan_sha256"] = digest
    if stored != expected:
        raise SupervisorError("current source, CLI, input, rubric or arguments differ from frozen plan")


@contextlib.contextmanager
def _exclusive_lock(root: pathlib.Path):
    """Kernel-owned lock; a crashed supervisor releases it automatically."""

    path = root / "supervisor.lock"
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SupervisorError("another supervisor owns this output root") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _event(root: pathlib.Path, plan: Mapping[str, Any], kind: str, **fields: Any) -> None:
    entry = {
        "schema_version": EVENT_SCHEMA,
        "plan_sha256": plan["plan_sha256"],
        "utc": _now(),
        "kind": kind,
        **fields,
    }
    with (root / "events.jsonl").open("ab") as handle:
        handle.write((runner.canonical_json(entry) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


def _shard_path(root: pathlib.Path, index: int) -> pathlib.Path:
    return root / "shards" / f"shard-{index:03d}-of-{TOTAL_SHARDS}"


def _external_overlaps(plan: Mapping[str, Any]) -> list[int]:
    return [
        shard["index"]
        for shard in plan["shards"]
        if any(EXTERNAL_SHARD_ROOT.glob(f"provisional_claude_shard{shard['index']:03d}*"))
    ]


def _read_rows(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise SupervisorError("checkpoint contains a non-object row")
    return rows


def verify_completed_shard(
    path: pathlib.Path, plan: Mapping[str, Any], index: int, selected_ids: Sequence[str]
) -> dict[str, Any]:
    """Accept a shard only after all 100 immutable raw receipts are audited."""

    manifest_path = path / "run_manifest.json"
    summary_path = path / "run_summary.json"
    if not manifest_path.is_file() or not summary_path.is_file():
        raise SupervisorError(f"partial shard; never recall uncertain CLI calls: {path}")
    manifest = _read_json(manifest_path)
    digest = manifest.pop("manifest_sha256", None)
    if digest != runner.sha256_object(manifest):
        raise SupervisorError(f"runner manifest hash mismatch: {path}")
    manifest["manifest_sha256"] = digest
    expected_fields = {
        "input_path": plan["input_path"],
        "input_sha256": plan["input_sha256"],
        "selected_ids": list(selected_ids),
        "selected_ids_sha256": runner.sha256_object(list(selected_ids)),
        "shard_index": index,
        "shard_count": TOTAL_SHARDS,
        "context_mode": plan["context_mode"],
        "max_budget_usd_per_call": plan["max_budget_usd_per_call"],
        "phase": "unlabeled_20000",
        "continue_on_content_error": True,
        "execute": True,
        "source_bundle": plan["runner_source_bundle"],
        "cli": plan["cli"],
        "rubric_source_sha256": plan["rubric_source_sha256"],
        "rubric_projection_sha256": plan["rubric_projection_sha256"],
        "schema_sha256": plan["output_schema_sha256"],
    }
    for key, value in expected_fields.items():
        if manifest.get(key) != value:
            raise SupervisorError(f"runner manifest {key} differs from frozen plan: {path}")
    summary = _read_json(summary_path)
    for key in ("records_selected", "tasks_prepared", "calls_attempted"):
        if summary.get(key) != RECORDS_PER_SHARD:
            raise SupervisorError(f"shard is not fully attempted ({key}): {path}")
    if summary.get("run_manifest_sha256") != digest or summary.get("safety_errors") != 0:
        raise SupervisorError(f"shard summary has safety or lineage failure: {path}")
    if summary.get("tasks_ok", -1) + summary.get("content_errors", -1) != RECORDS_PER_SHARD:
        raise SupervisorError(f"shard summary outcome counts differ: {path}")
    if summary.get("tasks_error") != summary.get("content_errors"):
        raise SupervisorError(f"shard has non-content errors: {path}")
    if summary.get("continued_content_errors") != summary.get("content_errors"):
        raise SupervisorError(f"shard content errors were not continued: {path}")
    success_ids: list[str] = []
    success_receipts: dict[str, str] = {}
    reported_cost = 0.0
    expected_task_dirs = {runner._slug(record_id) for record_id in selected_ids}
    observed_task_dirs = {child.name for child in (path / "tasks").iterdir() if child.is_dir()}
    if observed_task_dirs != expected_task_dirs:
        raise SupervisorError(f"task directory set differs from frozen shard IDs: {path}")
    for record_id in selected_ids:
        task_dir = path / "tasks" / runner._slug(record_id) / "v1-24"
        task = _read_json(task_dir / "task_manifest.json")
        task_hash = task.pop("manifest_sha256", None)
        if task_hash != runner.sha256_object(task):
            raise SupervisorError(f"task manifest hash mismatch: {record_id}")
        task["manifest_sha256"] = task_hash
        if task.get("record_id") != record_id or task.get("run_manifest_sha256") != digest:
            raise SupervisorError(f"task identity mismatch: {record_id}")
        attempt = task_dir / "attempts" / "attempt-001"
        attempts = {child.name for child in (task_dir / "attempts").iterdir() if child.is_dir()}
        if attempts != {"attempt-001"}:
            raise SupervisorError(f"unexpected repeat/extra attempt: {record_id}")
        receipt = _read_json(attempt / "receipt.json")
        receipt_hash = receipt.pop("receipt_sha256", None)
        if receipt_hash != runner.sha256_object(receipt):
            raise SupervisorError(f"receipt hash mismatch: {record_id}")
        receipt["receipt_sha256"] = receipt_hash
        if receipt.get("task_manifest_sha256") != task_hash or receipt.get("record_id") != record_id:
            raise SupervisorError(f"receipt identity mismatch: {record_id}")
        for name in ("stdout", "stderr"):
            if runner.file_sha256(attempt / f"{name}.bin") != receipt.get(f"{name}_sha256"):
                raise SupervisorError(f"raw {name} hash mismatch: {record_id}")
        if receipt.get("returncode") != 0 or receipt.get("total_cost_usd") is None:
            raise SupervisorError(f"receipt has transport or usage failure: {record_id}")
        cost = receipt["total_cost_usd"]
        if type(cost) not in (int, float) or not 0 <= cost <= plan["max_budget_usd_per_call"]:
            raise SupervisorError(f"receipt exceeds per-call cap: {record_id}")
        reported_cost += cost
        if receipt.get("status") == "ok":
            if receipt.get("error_class") is not None or receipt.get("errors"):
                raise SupervisorError(f"success receipt contains error: {record_id}")
            success_ids.append(record_id)
            success_receipts[record_id] = receipt_hash
        elif receipt.get("status") == "error":
            if receipt.get("error_class") != "content" or receipt.get("continued_after_content_error") is not True:
                raise SupervisorError(f"non-content error receipt: {record_id}")
        else:
            raise SupervisorError(f"unknown receipt status: {record_id}")
    rows = _read_rows(path / "full_records.jsonl")
    if [row.get("id") for row in rows] != success_ids:
        raise SupervisorError(f"successful checkpoint rows differ from receipts: {path}")
    if len(rows) != summary["tasks_ok"]:
        raise SupervisorError(f"successful checkpoint count differs: {path}")
    for row in rows:
        if row.get("receipt_sha256") != success_receipts[row["id"]] or row.get("status") != "provisional_unqualified":
            raise SupervisorError(f"successful checkpoint lineage differs: {row['id']}")
    return {
        "shard_index": index,
        "records": RECORDS_PER_SHARD,
        "success": len(success_ids),
        "content_errors": summary["content_errors"],
        "reported_cost_usd": reported_cost,
        "run_manifest_sha256": digest,
        "run_summary_sha256": runner.file_sha256(summary_path),
    }


def _runner_argv(args: argparse.Namespace, path: pathlib.Path, index: int) -> list[str]:
    return [
        sys.executable, "-B", str(RUNNER),
        "--input", str(INPUT),
        "--output-dir", str(path),
        "--phase", "unlabeled_20000",
        "--context-mode", args.context_mode,
        "--shard-index", str(index),
        "--shard-count", str(TOTAL_SHARDS),
        "--max-budget-usd", str(args.max_budget_usd),
        "--claude-bin", args.claude_bin,
        "--continue-on-content-error",
        "--execute",
    ]


def _execute_shard(
    root: pathlib.Path, plan: Mapping[str, Any], args: argparse.Namespace,
    index: int, selected_ids: Sequence[str],
) -> dict[str, Any]:
    path = _shard_path(root, index)
    if path.exists():
        raise SupervisorError(f"fresh-only shard output already exists: {path}")
    logs = root / "process_logs"
    logs.mkdir(parents=True, exist_ok=True)
    launch = 1
    while any((logs / f"shard-{index:03d}.launch-{launch:03d}.{kind}.bin").exists()
              for kind in ("stdout", "stderr")):
        launch += 1
    stdout_path = logs / f"shard-{index:03d}.launch-{launch:03d}.stdout.bin"
    stderr_path = logs / f"shard-{index:03d}.launch-{launch:03d}.stderr.bin"
    _event(root, plan, "shard_started", shard_index=index, launch=launch, output=str(path))
    with stdout_path.open("xb") as out, stderr_path.open("xb") as err:
        try:
            process = subprocess.Popen(
                _runner_argv(args, path, index), cwd=ROOT, shell=False,
                stdout=out, stderr=err,
            )
        except OSError as exc:
            _event(root, plan, "shard_stopped", shard_index=index, launch=launch,
                   reason=f"spawn_error: {exc}")
            raise
        previous_receipts = -1
        while process.poll() is None:
            count = sum(1 for _ in path.glob("tasks/*/v1-24/attempts/attempt-001/receipt.json"))
            if count != previous_receipts:
                _event(root, plan, "shard_progress", shard_index=index, receipts=count)
                previous_receipts = count
            time.sleep(args.poll_seconds)
        returncode = process.returncode
    try:
        result = verify_completed_shard(path, plan, index, selected_ids)
    except (OSError, ValueError) as exc:
        _event(root, plan, "shard_stopped", shard_index=index, launch=launch,
               runner_returncode=returncode,
               reason=str(exc))
        raise
    if returncode not in (0, 1):
        raise SupervisorError(f"runner process exited unexpectedly: {returncode}")
    if returncode != (1 if result["content_errors"] else 0):
        raise SupervisorError(f"runner exit status disagrees with verified content errors: {index}")
    _event(root, plan, "shard_completed", **result, launch=launch, runner_returncode=returncode)
    return result


def supervise(args: argparse.Namespace) -> dict[str, Any]:
    root = _resume_root(args.output_root) if args.resume else _fresh_root(args.output_root)
    plan, shards = build_plan(args, root)
    overlaps = _external_overlaps(plan)
    if args.resume:
        _verify_plan_file(root, plan)
    if not args.execute:
        return {
            "dry_run": True, "output_root": str(root),
            "plan_sha256": plan["plan_sha256"],
            "shards_selected": len(plan["shards"]),
            "records_selected": len(plan["shards"]) * RECORDS_PER_SHARD,
            "model_calls": 0,
            "external_overlap_indices": overlaps,
        }
    if overlaps and not args.allow_external_overlap:
        raise SupervisorError(
            f"selected shard indices already have external output; choose a disjoint range: {overlaps}"
        )
    if not args.resume:
        root.mkdir(parents=True, exist_ok=False)
        _write_new(root / "plan.json", plan)
    completed: list[dict[str, Any]] = []
    new_shards = 0
    with _exclusive_lock(root):
        _verify_plan_file(root, plan)
        for shard in plan["shards"]:
            index = shard["index"]
            path = _shard_path(root, index)
            if path.exists():
                result = verify_completed_shard(path, plan, index, shards[index])
                completed.append(result)
                continue  # Never recall any completed shard.
            if args.max_new_shards is not None and new_shards >= args.max_new_shards:
                break
            if index in _external_overlaps(plan) and not args.allow_external_overlap:
                raise SupervisorError(f"external shard appeared before launch: {index}")
            # Refuse to start a new shard if a source, CLI, or input changed.
            current, _ = build_plan(args, root)
            if current != plan:
                raise SupervisorError("frozen source, CLI, rubric or input changed before next shard")
            result = _execute_shard(root, plan, args, index, shards[index])
            completed.append(result)
            new_shards += 1
    return {
        "dry_run": False,
        "plan_sha256": plan["plan_sha256"],
        "output_root": str(root),
        "shards_selected": len(plan["shards"]),
        "shards_verified_complete": len(completed),
        "new_shards_executed": new_shards,
        "successful_records": sum(row["success"] for row in completed),
        "content_errors": sum(row["content_errors"] for row in completed),
        "reported_cost_usd": sum(row["reported_cost_usd"] for row in completed),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    parser.add_argument("--admission-report", type=pathlib.Path, required=True,
                        help="Fresh 20,000-record source_context_preflight PASS report.json")
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--context-mode", choices=("full", "compact"), default="compact")
    parser.add_argument("--first-shard-index", type=int, default=0)
    parser.add_argument("--last-shard-index", type=int, default=TOTAL_SHARDS - 1)
    parser.add_argument("--max-budget-usd", type=float, default=runner.DEFAULT_MAX_BUDGET_USD)
    parser.add_argument("--max-new-shards", type=int)
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--allow-external-overlap", action="store_true",
        help="Explicitly permit rerunning indices with separate external shard evidence.",
    )
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = supervise(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Claude shard supervisor stopped: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
