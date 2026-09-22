"""Fail-closed supervisor for ten independent 20-record Claude dev windows.

Planning is read-only. Only --execute invokes Claude. An attempted but not
fully verified window is never automatically retried: the CLI may have run
without leaving a complete receipt. This tool neither promotes labels nor
applies a teacher-quality threshold.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence

try:
    from tools.independent_gold import archive_source_bundle as archiver
    from tools.independent_gold import claude_batch_pilot as pilot
    from tools.independent_gold import score_verified_batch_dev200 as scorer
    from tools.independent_gold import verify_claude_batch_pilot as verifier
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.independent_gold import archive_source_bundle as archiver  # type: ignore[no-redef]
    from tools.independent_gold import claude_batch_pilot as pilot  # type: ignore[no-redef]
    from tools.independent_gold import score_verified_batch_dev200 as scorer  # type: ignore[no-redef]
    from tools.independent_gold import verify_claude_batch_pilot as verifier  # type: ignore[no-redef]


ROOT = pilot.ROOT
RUN_ROOT = archiver.RUN_ROOT.resolve()
SCHEMA = "dacon.independent.claude_dev200_batch_supervisor.v1"
WINDOW_STARTS = tuple(range(0, 200, 20))
SOURCE_PATHS = {
    "supervisor": Path(__file__),
    "pilot": Path(pilot.__file__),
    "archiver": Path(archiver.__file__),
    "verifier": Path(verifier.__file__),
    "scorer": Path(scorer.__file__),
}


class SupervisorError(ValueError):
    """A safety, provenance, or continuation invariant failed."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise SupervisorError(reason)


def _sha_file(path: Path) -> str:
    _require(path.is_file() and not path.is_symlink(), f"missing or symlinked file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_new(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write((pilot.base.canonical_json(value) + "\n").encode("utf-8"))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return verifier._json_file(path)
    except (OSError, ValueError, TypeError) as exc:
        raise SupervisorError(f"invalid immutable JSON artifact: {path}: {exc}") from exc


def _source_hashes() -> dict[str, str]:
    return {key: _sha_file(value.resolve()) for key, value in SOURCE_PATHS.items()}


def _tree_hash(path: Path) -> str:
    _require(path.is_dir() and not path.is_symlink(), f"missing or symlinked artifact tree: {path}")
    entries: list[tuple[str, str]] = []
    for child in path.rglob("*"):
        _require(not child.is_symlink(), f"symlinked artifact: {child}")
        if child.is_file():
            entries.append((child.relative_to(path).as_posix(), _sha_file(child)))
    _require(bool(entries), f"empty artifact tree: {path}")
    return pilot.base.sha256_object(sorted(entries))


def _root(path: Path, *, resume: bool) -> Path:
    resolved = path.resolve()
    _require(RUN_ROOT in resolved.parents, "output root must be a child of the independent run root")
    for ancestor in resolved.parents:
        if ancestor == RUN_ROOT:
            break
        _require(not (ancestor / "plan.json").exists(),
                 "output root cannot be nested inside another supervisor root")
    if resume:
        _require(resolved.is_dir() and not resolved.is_symlink(), "resume root is missing or symlinked")
    else:
        _require(not resolved.exists(), "new output root already exists; use --resume only for its frozen plan")
    return resolved


def _window_paths(root: Path, start: int) -> tuple[Path, Path, Path, Path]:
    tag = f"window-{start:03d}-{start + 19:03d}"
    return (root / "runs" / tag, root / "archives" / tag,
            root / "attempts" / f"{tag}.json", root / "completed" / f"{tag}.json")


def build_plan(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    _require(args.mode in {"source_lean", "full_shared"}, "invalid projection mode")
    _require(type(args.batch_size) is int and 1 <= args.batch_size <= 5, "batch size must be 1..5")
    _require(math.isfinite(args.timeout) and args.timeout > 0, "invalid per-call timeout")
    _require(math.isfinite(args.max_budget_usd) and args.max_budget_usd > 0, "invalid per-call budget")
    _require(type(args.max_new_windows) is int and 1 <= args.max_new_windows <= 10,
             "max new windows must be 1..10")
    ids, count = pilot.base.select_ids(pilot.DEV_INPUT, [], shard_index=0, shard_count=1, limit=None)
    _require(count == 200 and len(ids) == 200 and len(set(ids)) == 200,
             "organizer dev must contain exactly 200 unique IDs")
    original_rubric = pilot.base.RUBRIC_PATH.read_text(encoding="utf-8")
    rubric = pilot.base.annotation_rubric(original_rubric)
    cli = pilot.base.resolve_cli(args.claude_bin)
    plan: dict[str, Any] = {
        "schema_version": SCHEMA,
        "phase": "blind_dev200_teacher_quality_diagnostic_only_not_gold",
        "output_root": str(root),
        "input_path": str(pilot.DEV_INPUT.resolve()),
        "input_sha256": _sha_file(pilot.DEV_INPUT.resolve()),
        "mode": args.mode,
        "batch_size": args.batch_size,
        "max_budget_usd": args.max_budget_usd,
        "timeout_seconds": args.timeout,
        "claude_bin_argument": args.claude_bin,
        "cli": cli,
        "model": pilot.base.REQUESTED_MODEL,
        "observed_model": pilot.base.OBSERVED_MODEL,
        "rubric_sha256": pilot.base.sha256_text(rubric),
        "rubric_source_sha256": pilot.base.sha256_text(original_rubric),
        "system_sha256": pilot.base.sha256_text(pilot._system(rubric, args.mode)),
        "source_bundle": pilot.base.source_bundle(),
        "source_hashes": _source_hashes(),
        "window_starts": list(WINDOW_STARTS),
        "windows": [
            {"start_index": start, "selected_ids": ids[start:start + 20],
             "selected_ids_sha256": pilot.base.sha256_object(ids[start:start + 20])}
            for start in WINDOW_STARTS
        ],
        "qualification": "none_not_gold",
    }
    plan["plan_sha256"] = pilot.base.sha256_object(plan)
    return plan


def _check_frozen(root: Path, expected: Mapping[str, Any]) -> None:
    stored = _read_json(root / "plan.json")
    digest = stored.get("plan_sha256")
    _require(digest == pilot.base.sha256_object({key: value for key, value in stored.items()
                                                if key != "plan_sha256"}), "stored plan self-hash differs")
    _require(stored == expected, "current args, input, CLI, rubric or source differ from frozen plan")


@contextlib.contextmanager
def _exclusive_lock(root: Path):
    """A kernel-owned lock releases after a crashed supervisor process."""
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


def _validate_window(root: Path, plan: Mapping[str, Any], start: int) -> dict[str, Any]:
    run, archive, _, completed = _window_paths(root, start)
    proof = verifier.verify(run, archive)
    _require(proof.get("status") == "executed_rows_structurally_verified_not_gold"
             and proof.get("gold_qualification") is False
             and proof.get("model_calls_by_verifier") == 0
             and proof.get("selected_records") == 20
             and proof.get("records_ok") == 20
             and proof.get("records_content_error") == 0
             and proof.get("safety_errors") == 0
             and proof.get("start_index") == start,
             f"window {start} did not pass full executed raw replay")
    manifest = _read_json(run / "run_manifest.json")
    expected_ids = plan["windows"][start // 20]["selected_ids"]
    expected_fields = {
        "input_sha256": plan["input_sha256"], "selected_ids": expected_ids,
        "start_index": start, "batch_size": plan["batch_size"], "mode": plan["mode"],
        "max_budget_usd": plan["max_budget_usd"], "timeout_seconds": plan["timeout_seconds"],
        "model": plan["model"], "observed_model": plan["observed_model"],
        "rubric_sha256": plan["rubric_sha256"],
        "rubric_source_sha256": plan["rubric_source_sha256"],
        "system_sha256": plan["system_sha256"], "source_bundle": plan["source_bundle"],
        "pilot_source_sha256": plan["source_hashes"]["pilot"], "cli": plan["cli"],
        "execute": True, "qualification": "none_not_gold",
    }
    _require(all(manifest.get(key) == value for key, value in expected_fields.items()),
             f"window {start} differs from frozen supervisor tuple")
    _require(proof.get("run_manifest_sha256") == manifest.get("manifest_sha256"),
             f"window {start} verifier manifest binding differs")
    return {
        "schema_version": SCHEMA,
        "status": "executed_rows_structurally_verified_not_gold",
        "plan_sha256": plan["plan_sha256"],
        "start_index": start,
        "run_dir": str(run),
        "source_archive": str(archive),
        "run_tree_sha256": _tree_hash(run),
        "archive_tree_sha256": _tree_hash(archive),
        "run_manifest_sha256": manifest["manifest_sha256"],
        "verifier_proof": proof,
        "gold_qualification": False,
    }


def _completed_prefix(root: Path, plan: Mapping[str, Any]) -> int:
    prefix = 0
    gap = False
    for start in WINDOW_STARTS:
        run, archive, attempt, completed = _window_paths(root, start)
        if completed.exists():
            _require(not gap, "completed windows are not a contiguous prefix")
            _require(attempt.is_file(), f"window {start} completion has no attempt marker")
            receipt = _read_json(completed)
            current = _validate_window(root, plan, start)
            _require(receipt == current, f"window {start} completed receipt or artifacts changed")
            prefix += 1
        else:
            gap = True
            _require(not any(path.exists() for path in (run, archive, attempt)),
                     f"window {start} was attempted or partially written; manual reconciliation required")
    return prefix


def _pilot_argv(plan: Mapping[str, Any], run: Path, start: int) -> list[str]:
    return [sys.executable, "-B", "-m", "tools.independent_gold.claude_batch_pilot",
            "--input", plan["input_path"], "--output-dir", str(run),
            "--mode", plan["mode"], "--start-index", str(start), "--limit", "20",
            "--batch-size", str(plan["batch_size"]),
            "--claude-bin", plan["claude_bin_argument"],
            "--timeout", str(plan["timeout_seconds"]),
            "--max-budget-usd", str(plan["max_budget_usd"]), "--execute"]


def _invoke_pilot(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, check=False)


def _run_window(root: Path, plan: Mapping[str, Any], start: int) -> dict[str, Any]:
    _require(_source_hashes() == plan["source_hashes"] and
             _sha_file(pilot.DEV_INPUT.resolve()) == plan["input_sha256"],
             "frozen source or organizer input changed before new window")
    run, archive, attempt, completed = _window_paths(root, start)
    _require(not any(path.exists() for path in (run, archive, attempt, completed)),
             f"window {start} output is not fresh")
    argv = _pilot_argv(plan, run, start)
    _write_new(attempt, {"schema_version": SCHEMA, "status": "attempt_started",
                         "plan_sha256": plan["plan_sha256"], "start_index": start,
                         "created_utc": _now(), "argv": argv,
                         "possible_unreceipted_model_call": True})
    result = _invoke_pilot(argv)
    if result.returncode != 0:
        provenance: dict[str, Any] = {"status": "no_run_manifest_to_archive"}
        if (run / "run_manifest.json").is_file():
            try:
                archiver.archive(archive, run / "run_manifest.json")
                provenance = {"status": "source_archived_after_failed_pilot",
                              "archive_tree_sha256": _tree_hash(archive)}
                try:
                    proof = verifier.verify(run, archive)
                    provenance["raw_verifier_status"] = proof.get("status")
                    provenance["raw_verifier_proof"] = proof
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    provenance["raw_verifier_error"] = f"{type(exc).__name__}: {exc}"
            except (ValueError, KeyError, TypeError, OSError) as exc:
                provenance = {"status": "source_archive_failed_after_pilot",
                              "reason": f"{type(exc).__name__}: {exc}"}
        _write_new(root / "failures" / f"window-{start:03d}.json", {
            "schema_version": SCHEMA, "status": "pilot_failed_manual_reconciliation_required",
            "plan_sha256": plan["plan_sha256"], "start_index": start,
            "returncode": result.returncode,
            "stdout_sha256": hashlib.sha256(result.stdout.encode("utf-8")).hexdigest(),
            "stderr_sha256": hashlib.sha256(result.stderr.encode("utf-8")).hexdigest(),
            "stderr_tail": result.stderr[-2000:], "finished_utc": _now(),
            "provenance": provenance,
        })
        raise SupervisorError(f"window {start} pilot failed with exit {result.returncode}; no automatic retry")
    _require(run.joinpath("run_manifest.json").is_file(), "pilot exited zero without a run manifest")
    _require(_source_hashes() == plan["source_hashes"],
             "source changed during pilot; preserve attempt and stop")
    archiver.archive(archive, run / "run_manifest.json")
    receipt = _validate_window(root, plan, start)
    _write_new(completed, receipt)
    return receipt


def _score(root: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    _require(_completed_prefix(root, plan) == 10, "all ten verified windows required before scoring")
    pairs = [_window_paths(root, start)[:2] for start in WINDOW_STARTS]
    path = root / "score_report.json"
    receipt_path = root / "score_receipt.json"
    if path.exists() or receipt_path.exists():
        _require(path.is_file() and receipt_path.is_file(), "partial score publication requires review")
        report = _read_json(path)
        receipt = _read_json(receipt_path)
        _require(receipt == {"schema_version": SCHEMA, "plan_sha256": plan["plan_sha256"],
                             "report_sha256": _sha_file(path), "status": report.get("status"),
                             "gold_qualification": False}, "stored score receipt differs")
        _check_score_contract(report)
        _require(report.get("official_dev_labels_sha256") == _sha_file(scorer.DEV_LABELS),
                 "official dev labels changed after the stored score report")
        return report
    _require(_source_hashes() == plan["source_hashes"], "source changed before full dev200 scoring")
    report = scorer.score_windows(pairs)
    _check_score_contract(report)
    _write_new(path, report)
    _write_new(receipt_path, {"schema_version": SCHEMA, "plan_sha256": plan["plan_sha256"],
                              "report_sha256": _sha_file(path), "status": report["status"],
                              "gold_qualification": False})
    return report


def _check_score_contract(report: Mapping[str, Any]) -> None:
    unresolved = report.get("unresolved_cells")
    _require(isinstance(unresolved, list) and
             report.get("status") == ("failed_unresolved_cells" if unresolved else
                                      "metrics_only_pending_quality_policy_not_gold") and
             report.get("qualified_for_gold_generation") is False and
             report.get("quality_threshold_applied") is False and
             report.get("model_calls_by_scorer") == 0 and
             report.get("records") == 200 and report.get("cells_verified") == 4800,
             "dev200 score report does not satisfy the not-gold metrics contract")


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = _root(args.output_root, resume=args.resume)
    plan = build_plan(args, root)
    if not args.execute:
        if args.resume:
            _check_frozen(root, plan)
            prefix = _completed_prefix(root, plan)
        else:
            prefix = 0
        return {"status": "read_only_plan_no_model_calls", "output_root": str(root),
                "plan_sha256": plan["plan_sha256"], "completed_windows": prefix,
                "next_start_index": None if prefix == 10 else WINDOW_STARTS[prefix],
                "windows": plan["windows"], "qualification": "none_not_gold"}
    if not args.resume:
        root.mkdir(parents=True, exist_ok=False)
        _write_new(root / "plan.json", plan)
    with _exclusive_lock(root):
        _check_frozen(root, plan)
        prefix = _completed_prefix(root, plan)
        remaining = min(args.max_new_windows, 10 - prefix)
        for start in WINDOW_STARTS[prefix:prefix + remaining]:
            _run_window(root, plan, start)
        total = prefix + remaining
        if total == 10:
            score = _score(root, plan)
            return {"status": "dev200_scored_not_gold" if not score["unresolved_cells"]
                    else "dev200_unresolved_not_gold", "completed_windows": 10,
                    "score_report": str(root / "score_report.json"),
                    "macro_positive_f1": score["macro_positive_f1"],
                    "unresolved_cells": len(score["unresolved_cells"]),
                    "qualification": "none_not_gold"}
        return {"status": "verified_prefix_incomplete_not_gold", "completed_windows": total,
                "next_start_index": WINDOW_STARTS[total], "qualification": "none_not_gold"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("source_lean", "full_shared"), required=True)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--max-budget-usd", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--max-new-windows", type=int, default=1)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except (SupervisorError, verifier.BatchVerificationError, scorer.BatchDevScoreError,
            OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"schema_version": SCHEMA, "status": "supervisor_stopped_not_gold",
                          "reason": f"{type(exc).__name__}: {exc}", "model_calls_by_supervisor_verifier": 0},
                         ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] != "dev200_unresolved_not_gold" else 1


if __name__ == "__main__":
    raise SystemExit(main())
