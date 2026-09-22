"""Parallel, resumable runner for blind Claude CLI tasks with custom prompts; diagnostic, never gold.

Each task is one isolated CLI call built with the reviewed flag set of
``claude_full_record_annotator.build_command``; only the trailing prompt
argument may differ.  Task inputs are written once and never rewritten, every
attempt keeps its raw stdout/stderr and a receipt, a finished task is never
called again, and a failed task is retried only on explicit request.  The first
failure stops new submissions so a rate-limited account is not hammered.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any

try:
    from tools.independent_gold import claude_full_record_annotator as base
except ModuleNotFoundError:  # Direct script invocation.
    import claude_full_record_annotator as base  # type: ignore[no-redef]


ROOT = pathlib.Path(__file__).resolve().parents[2]
TASK_SCHEMA = "dacon.independent.cell_task.v1"
RECEIPT_SCHEMA = "dacon.independent.cell_receipt.v1"
REVIEWED_CLI_VERSIONS = ("2.1.276 (Claude Code)",)
_TASK_FILES = ("system_prompt.txt", "prompt.txt", "output_schema.json")

Invoke = Callable[..., tuple[int | None, bytes, bytes, str | None]]


def resolve_cli(executable: str, also_reviewed: Sequence[str] = ()) -> dict[str, Any]:
    """Like the annotator's check, but a newer CLI can be admitted by an explicit caller flag."""

    candidate = shutil.which(executable)
    if candidate is None:
        raise FileNotFoundError(f"Claude CLI not found: {executable}")
    path = pathlib.Path(candidate).resolve()
    result = subprocess.run([str(path), "--version"], capture_output=True, timeout=30, shell=False)
    if result.returncode != 0:
        raise ValueError("Claude CLI version check failed")
    version = result.stdout.decode("utf-8", errors="strict").strip()
    pinned = any(version.startswith(reviewed) for reviewed in REVIEWED_CLI_VERSIONS)
    if not pinned and not any(version.startswith(reviewed) for reviewed in also_reviewed):
        raise ValueError(f"unreviewed Claude CLI version: {version!r}")
    return {
        "requested_executable": executable, "resolved_executable": str(path),
        "executable_sha256": base.file_sha256(path), "version_output": version,
        "version_admitted_by_caller_flag": not pinned,
    }


def invoke(
    *, executable: str, system: str, prompt: str, schema_json: str, timeout: float,
    max_budget_usd: float, prompt_argument: str | None,
) -> tuple[int | None, bytes, bytes, str | None]:
    with tempfile.TemporaryDirectory(prefix="dacon-cell-claude-") as temp_name:
        workdir = pathlib.Path(temp_name)
        system_path = workdir / "system_prompt.txt"
        system_path.write_bytes(system.encode("utf-8"))
        argv = base.build_command(
            executable, schema_json=schema_json, system_prompt_path=system_path,
            max_budget_usd=max_budget_usd,
        )
        if argv[-1] != base.PROMPT_ARGUMENT:
            raise ValueError("reviewed command layout changed")
        if prompt_argument is not None:
            argv[-1] = prompt_argument
        try:
            result = subprocess.run(
                argv, input=prompt.encode("utf-8"), capture_output=True, cwd=workdir,
                timeout=timeout, shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            return None, exc.stdout or b"", exc.stderr or b"", "timeout"
        except OSError as exc:
            return None, b"", str(exc).encode("utf-8"), "spawn_error"
        return result.returncode, result.stdout, result.stderr, None


def _task_manifest(task: Mapping[str, Any]) -> dict[str, Any]:
    manifest = {
        "schema_version": TASK_SCHEMA, "task_id": task["task_id"], "binding": task["binding"],
        "system_prompt_sha256": base.sha256_text(task["system"]),
        "prompt_sha256": base.sha256_text(task["prompt"]),
        "output_schema_sha256": base.sha256_text(task["schema_json"]),
        "prompt_argument": task["prompt_argument"],
    }
    manifest["manifest_sha256"] = base.sha256_object(manifest)
    return manifest


def stage_task(task: Mapping[str, Any], output_dir: pathlib.Path) -> pathlib.Path:
    """Write the immutable task inputs once; a differing re-stage is an error."""

    if not re.fullmatch(r"[0-9A-Za-z._-]{1,96}", task["task_id"]):
        raise ValueError(f"unsafe task id: {task['task_id']!r}")
    task_dir = output_dir / "tasks" / task["task_id"]
    manifest = _task_manifest(task)
    manifest_path = task_dir / "task.json"
    if manifest_path.exists():
        staged = base.full_record_output.parse_output(manifest_path.read_bytes())
        if staged != manifest:
            raise ValueError(f"task was already staged with different inputs: {task['task_id']}")
        return task_dir
    for name, body in zip(_TASK_FILES, (task["system"], task["prompt"], task["schema_json"])):
        base._write_new(task_dir / name, body.encode("utf-8"))
    base._write_json_new(manifest_path, manifest)
    return task_dir


def task_status(task_dir: pathlib.Path) -> tuple[str, int]:
    """('ok' | 'failed' | 'new', number of recorded attempts)."""

    attempts = sorted((task_dir / "attempts").glob("attempt-*/receipt.json"))
    statuses = [
        base.full_record_output.parse_output(path.read_bytes())["status"] for path in attempts
    ]
    return ("ok" if "ok" in statuses else "failed" if statuses else "new"), len(statuses)


def _attempt(
    task: Mapping[str, Any], task_dir: pathlib.Path, number: int, *, executable: str,
    timeout: float, max_budget_usd: float, invoke_fn: Invoke,
) -> dict[str, Any]:
    attempt_dir = task_dir / "attempts" / f"attempt-{number:03d}"
    returncode, stdout, stderr, transport_error = invoke_fn(
        executable=executable, system=task["system"], prompt=task["prompt"],
        schema_json=task["schema_json"], timeout=timeout, max_budget_usd=max_budget_usd,
        prompt_argument=task["prompt_argument"],
    )
    base._write_new(attempt_dir / "stdout.bin", stdout)
    base._write_new(attempt_dir / "stderr.bin", stderr)
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA, "task_id": task["task_id"], "attempt": number,
        "task_manifest_sha256": _task_manifest(task)["manifest_sha256"],
        "stdout_sha256": base.sha256_bytes(stdout), "stderr_sha256": base.sha256_bytes(stderr),
        "returncode": returncode, "transport_error": transport_error,
        "requested_max_budget_usd": max_budget_usd,
    }
    if transport_error is not None or returncode != 0:
        receipt["status"] = "transport_error"
    else:
        try:
            envelope = base.audit_envelope(stdout)
            if envelope["total_cost_usd"] > max_budget_usd:
                raise ValueError("reported cost exceeds cap")
        except ValueError as exc:
            receipt.update({"status": "safety_error", "error": f"{type(exc).__name__}: {exc}"})
        else:
            base._write_json_new(attempt_dir / "structured_output.json", envelope["structured_output"])
            receipt.update({
                "status": "ok", "reported_cost_usd": envelope["total_cost_usd"],
                "model_usage": envelope["modelUsage"],
                "structured_output_sha256": base.sha256_object(envelope["structured_output"]),
            })
    base._write_json_new(attempt_dir / "receipt.json", receipt)
    return receipt


def run_tasks(
    tasks: Sequence[Mapping[str, Any]], output_dir: pathlib.Path, *, executable: str | None,
    workers: int = 2, timeout: float = 1800, max_budget_usd: float = 5.0,
    retry_failed: bool = False, invoke_fn: Invoke = invoke,
) -> dict[str, Any]:
    """Stage every task; with an executable, call the unfinished ones. Returns counts."""

    output = output_dir.resolve()
    if ROOT / "runs" not in output.parents:
        raise ValueError("runner output must be under repository runs/")
    if len({task["task_id"] for task in tasks}) != len(tasks):
        raise ValueError("duplicate task id")
    if not 1 <= workers <= 8:
        raise ValueError("workers must be 1..8")
    summary = {
        "tasks": len(tasks), "already_ok": 0, "previously_failed_skipped": 0, "attempted": 0,
        "ok": 0, "failed": 0, "not_started_after_failure": 0, "reported_cost_usd": 0.0,
        "executed": executable is not None,
    }
    pending: list[tuple[Mapping[str, Any], pathlib.Path, int]] = []
    for task in tasks:
        task_dir = stage_task(task, output)
        status, attempts = task_status(task_dir)
        if status == "ok":
            summary["already_ok"] += 1
        elif status == "failed" and not retry_failed:
            summary["previously_failed_skipped"] += 1
        else:
            pending.append((task, task_dir, attempts + 1))
    if executable is None:
        summary["pending"] = len(pending)
        return summary
    stop = threading.Event()
    queue = iter(pending)
    running: dict[Any, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            while len(running) < workers and not stop.is_set():
                item = next(queue, None)
                if item is None:
                    break
                task, task_dir, number = item
                future = pool.submit(
                    _attempt, task, task_dir, number, executable=executable, timeout=timeout,
                    max_budget_usd=max_budget_usd, invoke_fn=invoke_fn,
                )
                running[future] = task["task_id"]
            if not running:
                break
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                running.pop(future)
                receipt = future.result()
                summary["attempted"] += 1
                if receipt["status"] == "ok":
                    summary["ok"] += 1
                    summary["reported_cost_usd"] += receipt["reported_cost_usd"]
                else:
                    summary["failed"] += 1
                    stop.set()
    summary["not_started_after_failure"] = len(pending) - summary["attempted"]
    return summary
