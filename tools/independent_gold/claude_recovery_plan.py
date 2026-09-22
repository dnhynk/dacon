"""Prepare or verify a sealed, manual Claude recovery plan; never call a model.

Only failed/unattempted organizer IDs from a finished partial shard are eligible.
Only successful rows independently reconstructed against this exact archived
runner source are reused. Verification detects any changed run or archive.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
from typing import Any

try:
    from tools.independent_gold import unlabeled_progress as progress
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import unlabeled_progress as progress

try:
    from tools.independent_gold import pass_row_bridge as bridge
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import pass_row_bridge as bridge


SCHEMA = "dacon.independent.claude_manual_recovery_plan.v1"


class RecoveryPlanError(ValueError):
    pass


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryPlanError(f"unreadable JSON metadata: {path}") from exc
    if not isinstance(value, dict):
        raise RecoveryPlanError(f"invalid JSON object: {path}")
    return value


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _run_snapshot(run: Path, root: Path, *, require_summary: bool = False) -> dict[str, Any]:
    resolved = run.resolve()
    if not _within(resolved, root.resolve()):
        raise RecoveryPlanError("source run is outside unlabeled run root")
    details = progress._manifest_details(resolved)
    if details is None or details[0] != "claude":
        raise RecoveryPlanError(f"not an admitted Claude unlabeled run: {resolved}")
    _, selected, bundle, input_hash = details
    manifest = _read(resolved / "run_manifest.json")
    if manifest.get("execute") is not True:
        raise RecoveryPlanError("source run must have been executed")
    selected_set = set(selected)
    files = [resolved / "run_manifest.json"]
    receipts: dict[str, list[str]] = {}
    ok_receipt_hashes: dict[str, set[str]] = {}
    receipt_attempts = 0
    for receipt_path in sorted((resolved / "tasks").glob("*/v1-24/attempts/attempt-*/receipt.json")):
        value = _read(receipt_path)
        record_id = value.get("record_id")
        if record_id not in selected_set:
            raise RecoveryPlanError("receipt ID outside source manifest")
        status = value.get("status")
        if status not in {"ok", "error"}:
            raise RecoveryPlanError("unrecognized receipt status")
        receipts.setdefault(record_id, []).append(status)
        if status == "ok":
            receipt_hash = value.get("receipt_sha256")
            if not isinstance(receipt_hash, str) or not receipt_hash:
                raise RecoveryPlanError("ok receipt is missing its immutable receipt hash")
            ok_receipt_hashes.setdefault(record_id, set()).add(receipt_hash)
        receipt_attempts += 1
        files.append(receipt_path)
        for filename, digest_field in (("stdout.bin", "stdout_sha256"), ("stderr.bin", "stderr_sha256")):
            raw_path = receipt_path.parent / filename
            if not raw_path.is_file() or _digest(raw_path) != value.get(digest_field):
                raise RecoveryPlanError("raw CLI output is missing or differs from receipt")
            files.append(raw_path)
    checkpoint = resolved / "full_records.jsonl"
    success: set[str] = set()
    if checkpoint.is_file():
        files.append(checkpoint)
        try:
            with checkpoint.open(encoding="utf-8") as handle:
                for line in handle:
                    row = json.loads(line)
                    record_id = row["id"]
                    cells = row["ledger"]["cells"]
                    if (record_id not in selected_set or record_id in success
                            or row.get("status") != "provisional_unqualified"
                            or row.get("receipt_sha256") not in ok_receipt_hashes.get(record_id, set())
                            or len(cells) != 24
                            or len({cell["item"] for cell in cells}) != 24):
                        raise RecoveryPlanError("invalid or duplicate checkpoint row")
                    success.add(record_id)
        except (OSError, UnicodeError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RecoveryPlanError("unreadable checkpoint") from exc
    if any("ok" not in receipts.get(record_id, []) for record_id in success):
        raise RecoveryPlanError("checkpoint has no matching ok receipt")
    if any("ok" in statuses and record_id not in success for record_id, statuses in receipts.items()):
        raise RecoveryPlanError("ok receipt has no durable checkpoint; inspect manually")
    summary_path = resolved / "run_summary.json"
    summary = _read(summary_path) if summary_path.is_file() else None
    if summary:
        files.append(summary_path)
        error_attempts = sum(status == "error" for statuses in receipts.values() for status in statuses)
        if (summary.get("records_selected") != len(selected)
                or summary.get("tasks_ok") != len(success)
                or summary.get("calls_attempted") != receipt_attempts
                or summary.get("tasks_error") != error_attempts):
            raise RecoveryPlanError("terminal summary disagrees with receipts/checkpoint")
        if ("content_errors" in summary and "safety_errors" in summary
                and summary["content_errors"] + summary["safety_errors"] != error_attempts):
            raise RecoveryPlanError("terminal error classes disagree with receipts")
    elif require_summary:
        raise RecoveryPlanError("source run has no terminal summary; do not plan recovery while active")
    before = {path.relative_to(resolved).as_posix(): _digest(path) for path in files}
    after = {path.relative_to(resolved).as_posix(): _digest(path) for path in files}
    if before != after:
        raise RecoveryPlanError("source run changed during snapshot")
    return {
        "path": str(resolved), "files_sha256": before,
        "selected_ids": selected, "success_ids": sorted(success),
        "attempted_ids": sorted(receipts), "input_sha256": input_hash,
        "source_bundle_sha256": bundle, "has_terminal_summary": summary is not None,
    }


def _source_snapshot(root: Path, source: Path) -> dict[str, Any]:
    run_paths, _ = progress._discover(root)
    if source not in {path.resolve() for path in run_paths}:
        raise RecoveryPlanError("source run is not an admitted named shard")
    source_details = progress._manifest_details(source)
    if source_details is None or source_details[0] != "claude":
        raise RecoveryPlanError("source run is not an admitted named shard")
    return _run_snapshot(source, root, require_summary=True)


def _verify_archived_successes(
    run: Path, archive: Path, snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Reuse the release bridge's exact archive and per-row prompt reconstruction."""
    source_manifest = bridge._canonical_file(run / "run_manifest.json", "recovery source manifest")
    source = Path(str(source_manifest["input_path"])).resolve()
    expected = (bridge.claude.ROOT / "data_open" / progress.SOURCE_BASENAME).resolve()
    if source != expected or not source.is_file():
        raise RecoveryPlanError("recovery source is not the organizer unlabeled input")
    input_sha = bridge._file_sha(source)
    if input_sha != snapshot["input_sha256"]:
        raise RecoveryPlanError("organizer input hash differs from original run")
    wanted = set(snapshot["success_ids"])
    order: list[str] = []
    records: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for record in bridge.claude._records(source):
        record_id = record["id"]
        if record_id in seen:
            raise RecoveryPlanError("duplicate organizer record ID")
        seen.add(record_id)
        order.append(record_id)
        if record_id in wanted:
            records[record_id] = record
    if set(records) != wanted:
        raise RecoveryPlanError("successful ID absent from organizer input")
    catalogs = (
        bridge.full_record_context.fact_context.catalog_facts.CatalogIndex.load(),
        bridge.full_record_context.qualification_context.qualification_facts.CatalogReference.load(),
    )
    manifest, selected = bridge._claude_manifest(
        run, source, input_sha, order, catalogs, archived=True, allow_partial=True,
    )
    if selected != snapshot["selected_ids"]:
        raise RecoveryPlanError("archived selected IDs differ from receipt snapshot")
    proof = bridge._verify_archived_source(archive, run, manifest, "claude")
    if proof["archive_binding"] != "exact_run":
        raise RecoveryPlanError("source archive is not anchored to this exact run manifest")
    checkpoint = run / "full_records.jsonl"
    offsets, ignored_tail = bridge._checkpoint_offsets(checkpoint, selected, allow_partial=True)
    if set(offsets) != wanted or ignored_tail:
        raise RecoveryPlanError("archived checkpoint differs or has an unfinished tail")
    if offsets:
        with checkpoint.open("rb") as handle:
            for record_id in selected:
                if record_id not in offsets:
                    continue
                record = records[record_id]
                row = bridge._checkpoint_row(handle, offsets[record_id], record_id)
                pass_row = bridge._claude_pass(
                    record, row, manifest, run, catalogs, "verifier", archived=True,
                )
                bridge.provisional_release._validate_pass_row(
                    pass_row, role="verifier",
                    source_hashes={record_id: bridge._sha_object(record)},
                    context=f"recovery verified {record_id}",
                )
                bridge.provisional_release.validate_ledger(record, pass_row["ledger"])
    if bridge._file_sha(source) != input_sha:
        raise RecoveryPlanError("organizer input changed during archived verification")
    if bridge._verify_archived_source(archive, run, manifest, "claude") != proof:
        raise RecoveryPlanError("source archive or live source changed during verification")
    if _run_snapshot(run, run.parent)["files_sha256"] != snapshot["files_sha256"]:
        raise RecoveryPlanError("source run changed during archived verification")
    return {
        "archive_path": str(archive.resolve()),
        "archive_manifest_sha256": proof["archive_manifest_sha256"],
        "archive_binding": proof["archive_binding"],
        "source_bundle_sha256": proof["source_bundle_sha256"],
        "verified_success_rows": len(offsets),
        "context_prompt_reconstructed": True,
        "qualification_context_only_live_drift": proof["live_source_drift_allowed"],
    }


def _build(
    root: Path, source_run: Path, recovery_output: Path, source_archive: Path | None,
    *, require_fresh_output: bool,
) -> dict[str, Any]:
    root = root.resolve()
    source = source_run.resolve()
    output = recovery_output.resolve()
    if source_archive is None:
        raise RecoveryPlanError("--source-archive is mandatory; unarchived runs require a fresh full-shard rerun")
    archive = source_archive.resolve()
    if output.parent != root or not output.name.startswith("recovery_claude_"):
        raise RecoveryPlanError("recovery output must be a recovery_claude_* child of the unlabeled run root")
    if require_fresh_output and output.exists():
        raise RecoveryPlanError("recovery output must be fresh")
    if not require_fresh_output and not output.is_dir():
        raise RecoveryPlanError("completed recovery output directory is missing")
    source_snapshot = _source_snapshot(root, source)
    archive_proof = _verify_archived_successes(source, archive, source_snapshot)
    if (archive_proof.get("archive_binding") != "exact_run"
            or archive_proof.get("verified_success_rows") != len(source_snapshot["success_ids"])
            or archive_proof.get("context_prompt_reconstructed") is not True):
        raise RecoveryPlanError("source archive proof is not exact and complete")
    # Only success rows proven against this exact archived runner are reusable.
    # Unarchived historical rows (notably shards 001-003) never suppress reruns.
    verified_success = set(source_snapshot["success_ids"])
    source_ids = source_snapshot["selected_ids"]
    recovery_ids = [record_id for record_id in source_ids if record_id not in verified_success]
    if not recovery_ids:
        raise RecoveryPlanError("all source IDs are already checkpointed successfully")
    source_attempted = set(source_snapshot["attempted_ids"])
    source_manifest = _read(source / "run_manifest.json")
    context_mode = source_manifest.get("context_mode", "compact")
    budget = source_manifest.get("max_budget_usd_per_call")
    if (context_mode not in {"full", "compact"} or type(budget) not in {int, float}
            or not math.isfinite(budget) or budget <= 0):
        raise RecoveryPlanError("source context mode or per-call budget is not reusable")
    manual_argv = [
        sys.executable, "-B", str(Path(__file__).with_name("claude_full_record_annotator.py")),
        "--input", str(source_manifest["input_path"]),
        "--output-dir", str(output),
        "--phase", "unlabeled_20000",
        "--context-mode", context_mode,
        "--shard-index", "0", "--shard-count", "1",
        "--max-budget-usd", str(budget),
        "--continue-on-content-error",
        *[part for record_id in recovery_ids for part in ("--record-id", record_id)],
        "--execute",
    ]
    return {
        "schema_version": SCHEMA,
        "purpose": "manual_recovery_only_not_gold_not_automatic_execution",
        "root": str(root),
        "source_run": str(source),
        "source_archive": str(archive),
        "source_archive_proof": archive_proof,
        "recovery_output": str(output),
        "organizer_input_path": str(source_manifest["input_path"]),
        "organizer_input_sha256_from_manifest": source_snapshot["input_sha256"],
        "source_bundle_sha256_from_manifest": source_snapshot["source_bundle_sha256"],
        "source_selected_count": len(source_ids),
        "success_excluded_count": len(source_ids) - len(recovery_ids),
        "success_exclusion_scope": "exact_archive_verified_source_run_only",
        "failed_attempt_ids": [record_id for record_id in recovery_ids if record_id in source_attempted],
        "unattempted_ids": [record_id for record_id in recovery_ids if record_id not in source_attempted],
        "recovery_ids": recovery_ids,
        "snapshots": [{"path": source_snapshot["path"], "files_sha256": source_snapshot["files_sha256"]}],
        "manual_execution_argv": manual_argv,
        "execution": "No model call is made by this planner. Re-verify immediately before manually running the explicit argv. The new runner source bundle/tuple must be reviewed separately; no old-tuple identity is asserted.",
    }


def build(root: Path, source_run: Path, recovery_output: Path, source_archive: Path | None = None) -> dict[str, Any]:
    """Create a read-only plan for a fresh recovery output path."""
    return _build(root, source_run, recovery_output, source_archive, require_fresh_output=True)


def verify(plan_path: Path) -> dict[str, Any]:
    plan = _read(plan_path)
    if plan.get("schema_version") != SCHEMA:
        raise RecoveryPlanError("unknown recovery plan schema")
    current = build(
        Path(plan["root"]), Path(plan["source_run"]), Path(plan["recovery_output"]),
        Path(plan["source_archive"]),
    )
    if current != plan:
        raise RecoveryPlanError("recovery plan is stale; run metadata or successful coverage changed")
    return {"valid": True, "recovery_count": len(plan["recovery_ids"]),
            "recovery_output": plan["recovery_output"], "model_calls": 0}


def verify_for_completed_recovery(plan_path: Path, recovery_run: Path) -> dict[str, Any]:
    """Recheck an archived parent plan after its fresh recovery run has terminated.

    This does not validate recovery model rows; the pass-row bridge does that.
    It proves the explicit-ID cohort is exactly the archived parent's missing IDs.
    """
    plan = _read(plan_path)
    if plan.get("schema_version") != SCHEMA:
        raise RecoveryPlanError("unknown recovery plan schema")
    output = recovery_run.resolve()
    if output != Path(plan["recovery_output"]).resolve():
        raise RecoveryPlanError("completed recovery path differs from frozen plan")
    current = _build(
        Path(plan["root"]), Path(plan["source_run"]), output,
        Path(plan["source_archive"]), require_fresh_output=False,
    )
    if current != plan:
        raise RecoveryPlanError("recovery plan or archived source is stale")
    manifest = bridge._canonical_file(output / "run_manifest.json", "completed recovery manifest")
    expected_sha = bridge._sha_object({key: value for key, value in manifest.items() if key != "manifest_sha256"})
    if (manifest.get("manifest_sha256") != expected_sha
            or manifest.get("phase") != "unlabeled_20000"
            or manifest.get("execute") is not True
            or manifest.get("selected_ids") != plan["recovery_ids"]
            or manifest.get("selected_ids_sha256") != bridge._sha_object(plan["recovery_ids"])
            or manifest.get("input_sha256") != plan["organizer_input_sha256_from_manifest"]
            or Path(str(manifest.get("input_path") or "")).resolve() != Path(plan["organizer_input_path"]).resolve()
            or manifest.get("shard_index") != 0 or manifest.get("shard_count") != 1):
        raise RecoveryPlanError("completed recovery manifest differs from explicit-ID plan")
    summary = bridge._canonical_file(output / "run_summary.json", "completed recovery summary")
    if (summary.get("run_manifest_sha256") != expected_sha
            or summary.get("records_selected") != len(plan["recovery_ids"])
            or type(summary.get("calls_attempted")) is not int
            or summary["calls_attempted"] > len(plan["recovery_ids"])):
        raise RecoveryPlanError("completed recovery summary disagrees with plan")
    return {
        "valid": True, "recovery_ids": plan["recovery_ids"],
        "parent_source_archive_proof": plan["source_archive_proof"],
        "recovery_run_manifest_sha256": expected_sha,
        "model_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=progress.ROOT)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source-run", type=Path)
    group.add_argument("--verify-plan", type=Path)
    parser.add_argument("--recovery-output", type=Path)
    parser.add_argument("--plan-out", type=Path)
    parser.add_argument("--source-archive", type=Path, help="mandatory exact archived runner source for --source-run")
    args = parser.parse_args()
    if args.verify_plan:
        print(json.dumps(verify(args.verify_plan), ensure_ascii=False, separators=(",", ":")))
        return 0
    if not args.recovery_output or not args.plan_out or not args.source_archive:
        parser.error("--source-run requires --source-archive, --recovery-output and --plan-out")
    if (args.plan_out.resolve().parent != args.root.resolve()
            or not args.plan_out.name.startswith("recovery_plan_")
            or args.plan_out.suffix != ".json" or args.plan_out.exists()):
        raise RecoveryPlanError("plan output must be a fresh recovery_plan_*.json child of the unlabeled run root")
    plan = build(args.root, args.source_run, args.recovery_output, args.source_archive)
    with args.plan_out.open("x", encoding="utf-8") as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"plan_path": str(args.plan_out.resolve()),
                      "recovery_count": len(plan["recovery_ids"]),
                      "failed_attempt_count": len(plan["failed_attempt_ids"]),
                      "unattempted_count": len(plan["unattempted_ids"]),
                      "model_calls": 0}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
