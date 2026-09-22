"""Read-only, zero-model-call audit of stopped Codex full-record shards.

This diagnostic never exports pass rows and never authorizes a retry.  Only a
contiguous checkpoint prefix whose raw attempts replay through the archived
bridge verifier is called ``verified_completed``.  Every other observed task
or receipt remains an unresolved operational state, not a gold label.
"""

from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import sys
from collections.abc import Mapping, Sequence
from typing import Any

try:
    from tools.independent_gold import pass_row_bridge as bridge
except ModuleNotFoundError:  # Direct script invocation.
    import pass_row_bridge as bridge  # type: ignore[no-redef]


SCHEMA = "dacon.independent.sol_partial_stop_audit.v1"


def _organizer(source: pathlib.Path, wanted: set[str]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    order: list[str] = []
    selected: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    opener = gzip.open if source.suffix.lower() == ".gz" else pathlib.Path.open
    with opener(source, "rb") as handle:
        for line_number, raw in enumerate(handle, 1):
            bridge._require(bool(raw.strip()), f"organizer line {line_number}: blank row")
            record = bridge._json_bytes(raw, f"organizer line {line_number}")
            try:
                bridge.review_ledger._validate_organizer_record(record)
            except ValueError as exc:
                raise bridge.PassRowBridgeError(f"organizer line {line_number}: {exc}") from exc
            record_id = record.get("id")
            bridge._require(isinstance(record_id, str) and bool(record_id) and record_id not in seen,
                            f"organizer line {line_number}: missing or duplicate ID")
            seen.add(record_id)
            order.append(record_id)
            if record_id in wanted:
                selected[record_id] = record
    bridge._require(bool(order) and wanted <= seen, "organizer does not contain all selected IDs")
    return order, selected


def _checkpoint_rows(path: pathlib.Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Keep only complete canonical rows; never repair or ignore a bad tail."""
    if not path.exists():
        return [], [], None
    bridge._require(path.is_file() and not path.is_symlink(), "checkpoint is not a regular file")
    sha = bridge._file_sha(path)
    rows: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        for line_number, raw in enumerate(handle, 1):
            if not raw.endswith(b"\n"):
                problems.append({"kind": "unterminated_checkpoint_tail", "line": line_number})
                break
            try:
                row = bridge._json_bytes(raw, f"checkpoint line {line_number}")
                bridge._require(raw == (bridge._canonical(row) + "\n").encode("utf-8"),
                                "noncanonical checkpoint row")
            except (bridge.PassRowBridgeError, ValueError) as exc:
                problems.append({"kind": "corrupt_checkpoint_row", "line": line_number,
                                 "reason": str(exc)[:240]})
                break
            rows.append(row)
    return rows, problems, sha


def _latest_receipt(task_dir: pathlib.Path) -> tuple[dict[str, Any] | None, list[str]]:
    attempts = task_dir / "attempts"
    if not attempts.is_dir():
        return None, []
    names = sorted(path.name for path in attempts.iterdir() if path.is_dir())
    if not names:
        return None, []
    path = attempts / names[-1] / "receipt.json"
    if not path.exists():
        return None, names
    return bridge._canonical_file(path, "latest Codex attempt receipt"), names


def _prepared_task_verified(
    record: Mapping[str, Any], manifest: Mapping[str, Any],
    run_dir: pathlib.Path, catalogs: tuple[Any, Any],
) -> bool:
    """A directory alone is not proof that task preparation finished."""
    bridge._codex_archived_task(record, manifest, run_dir)
    bridge.codex_verify._verify_task(
        run_dir=run_dir, manifest=manifest, record=record,
        fact_catalog=catalogs[0], qualification_catalog=catalogs[1],
    )
    return True


def audit_run(
    *, run_dir: pathlib.Path, source_archive: pathlib.Path, organizer_input: pathlib.Path,
    order: Sequence[str], records: Mapping[str, Mapping[str, Any]],
    catalogs: tuple[Any, Any], expected_shard_index: int | None = None,
    expected_shard_count: int | None = None,
) -> dict[str, Any]:
    """Audit one stopped shard; caller must not mutate any input during the read."""
    run_dir = run_dir.resolve()
    source_archive = source_archive.resolve()
    source = organizer_input.resolve()
    source_sha = bridge._file_sha(source)
    raw_manifest = bridge._canonical_file(run_dir / "run_manifest.json", "Codex run manifest")
    bridge._require(raw_manifest.get("annotator_role") == "candidate", "Sol run is not a raw candidate")
    manifest, selected = bridge._codex_manifest(
        run_dir, source, source_sha, "candidate", order, catalogs,
        archived=True, allow_partial=True,
    )
    bridge._require(manifest.get("phase") == "unlabeled_20000", "Sol run is not unlabeled phase")
    partition = manifest["cohort_plan"]["partitioning"]
    if expected_shard_index is not None:
        bridge._require(partition["shard_index"] == expected_shard_index, "Sol shard index differs")
    if expected_shard_count is not None:
        bridge._require(partition["shard_count"] == expected_shard_count, "Sol shard count differs")
    bridge._require(set(selected) <= set(records), "selected organizer records unavailable")
    archive_proof = bridge._verify_archived_source(source_archive, run_dir, manifest, "codex")
    bridge._require(archive_proof["archive_binding"] == "exact_run",
                    "Sol archive is not anchored to this exact run")
    checkpoint = run_dir / "full_records.jsonl"
    rows, problems, checkpoint_sha = _checkpoint_rows(checkpoint)
    selected_set = set(selected)
    verified: list[str] = []
    seen_checkpoint: set[str] = set()
    checkpoint_mentioned: set[str] = set()
    prefix_open = True
    for line_number, row in enumerate(rows, 1):
        record_id = row.get("id")
        if not isinstance(record_id, str) or record_id not in selected_set:
            problems.append({"kind": "out_of_cohort_checkpoint_row", "line": line_number})
            prefix_open = False
            continue
        checkpoint_mentioned.add(record_id)
        if record_id in seen_checkpoint:
            problems.append({"kind": "duplicate_checkpoint_row", "id": record_id,
                             "line": line_number})
            prefix_open = False
            continue
        seen_checkpoint.add(record_id)
        if not prefix_open or len(verified) >= len(selected) or record_id != selected[len(verified)]:
            problems.append({"kind": "nonprefix_checkpoint_row", "id": record_id,
                             "line": line_number})
            prefix_open = False
            continue
        try:
            bridge._codex_pass(records[record_id], row, manifest, run_dir, "candidate",
                               catalogs, archived=True)
        except Exception as exc:  # Malformed artifacts may also raise shape/type errors.
            problems.append({"kind": "unverified_checkpoint_row", "id": record_id,
                             "line": line_number, "reason": str(exc)[:240]})
            prefix_open = False
            continue
        verified.append(record_id)

    receipt_without_checkpoint: list[dict[str, Any]] = []
    prepared_without_receipt: list[dict[str, Any]] = []
    unprepared_remaining: list[str] = []
    for record_id in selected:
        if record_id in verified:
            continue
        task_dir = run_dir / "tasks" / bridge.codex.base._task_slug(record_id) / bridge.codex.GROUP_NAME
        if not task_dir.exists():
            if record_id in checkpoint_mentioned:
                problems.append({"kind": "checkpoint_without_task", "id": record_id})
            else:
                unprepared_remaining.append(record_id)
            continue
        try:
            receipt, attempts = _latest_receipt(task_dir)
        except Exception as exc:  # A torn receipt is always unresolved.
            problems.append({"kind": "corrupt_or_unreadable_receipt", "id": record_id,
                             "reason": str(exc)[:240]})
            prepared_without_receipt.append({"id": record_id, "attempt_dirs": [],
                                             "task_artifacts_verified": False})
            continue
        if receipt is None:
            task_verified = False
            try:
                task_verified = _prepared_task_verified(records[record_id], manifest,
                                                         run_dir, catalogs)
            except Exception as exc:  # A partially prepared task is unresolved.
                problems.append({"kind": "partial_or_corrupt_prepared_task", "id": record_id,
                                 "reason": str(exc)[:240]})
            prepared_without_receipt.append({"id": record_id, "attempt_dirs": attempts,
                                             "task_artifacts_verified": task_verified})
            continue
        verified_raw = False
        if receipt.get("status") == "ok":
            try:
                bridge._codex_pass(records[record_id], receipt, manifest, run_dir,
                                   "candidate", catalogs, archived=True)
                verified_raw = True
            except Exception as exc:  # Never turn a malformed receipt into a success.
                problems.append({"kind": "unverified_receipt_raw_attempt", "id": record_id,
                                 "reason": str(exc)[:240]})
        else:
            problems.append({"kind": "non_success_receipt", "id": record_id})
        if record_id in checkpoint_mentioned:
            # A row that exists in the checkpoint but is nonprefix/corrupt is
            # not a "receipt without checkpoint", even if its raw attempt is
            # independently valid.  Keep the disagreement visible instead.
            problems.append({"kind": "unverified_checkpoint_receipt_state", "id": record_id,
                             "raw_attempt_replay_verified": verified_raw})
            continue
        receipt_without_checkpoint.append({
            "id": record_id, "status": receipt.get("status"),
            "raw_attempt_replay_verified": verified_raw,
        })
    bridge._require(bridge._file_sha(source) == source_sha and
                    bridge._file_sha(run_dir / "run_manifest.json") == archive_proof["run_manifest_file_sha256"] and
                    (bridge._file_sha(checkpoint) if checkpoint.is_file() else None) == checkpoint_sha,
                    "source, manifest, or checkpoint changed during audit")
    bridge._require(bridge._verify_archived_source(source_archive, run_dir, manifest, "codex") == archive_proof,
                    "source archive changed during audit")
    return {
        "schema_version": SCHEMA, "status": "read_only_partial_stop_diagnostic_not_gold",
        "run_dir": str(run_dir), "run_manifest_sha256": manifest["manifest_sha256"],
        "source_archive_proof": archive_proof,
        "checkpoint_sha256": checkpoint_sha, "selected_count": len(selected),
        "selected_ids_sha256": bridge._sha_object(selected),
        "verified_completed": verified,
        "receipt_without_checkpoint": receipt_without_checkpoint,
        "prepared_without_receipt": prepared_without_receipt,
        "partial_or_corrupt_tail": problems,
        "unprepared_remaining": unprepared_remaining,
        "uncertain_ids": [record_id for record_id in selected if record_id not in verified
                          and record_id not in unprepared_remaining],
        "automatic_recall_authorized": False,
        "model_calls": 0, "output_files_created": 0,
    }


def audit_runs(
    *, organizer_input: pathlib.Path, runs_and_archives: Sequence[tuple[pathlib.Path, pathlib.Path]],
    expected_shard_indices: Sequence[int] | None = None,
    expected_shard_count: int | None = None,
) -> dict[str, Any]:
    """Audit disjoint stopped Sol shards without writing or invoking a model."""
    bridge._require(bool(runs_and_archives), "no stopped Sol runs supplied")
    if expected_shard_indices is not None:
        bridge._require(len(expected_shard_indices) == len(runs_and_archives), "expected shard count differs")
    source = organizer_input.resolve()
    bridge._require(source.is_file(), "organizer input missing")
    candidate_ids: set[str] = set()
    for run_dir, _ in runs_and_archives:
        manifest = bridge._canonical_file(run_dir / "run_manifest.json", "Codex run manifest")
        plan = manifest.get("cohort_plan")
        bridge._require(isinstance(plan, Mapping) and isinstance(plan.get("selected_ids"), list),
                        "Sol run selected IDs unavailable")
        candidate_ids.update(plan["selected_ids"])
    order, records = _organizer(source, candidate_ids)
    catalogs = (
        bridge.full_record_context.fact_context.catalog_facts.CatalogIndex.load(),
        bridge.full_record_context.qualification_context.qualification_facts.CatalogReference.load(),
    )
    reports: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, (run_dir, archive) in enumerate(runs_and_archives):
        report = audit_run(
            run_dir=run_dir, source_archive=archive, organizer_input=source,
            order=order, records=records, catalogs=catalogs,
            expected_shard_index=expected_shard_indices[index] if expected_shard_indices is not None else None,
            expected_shard_count=expected_shard_count,
        )
        selected = bridge._canonical_file(run_dir / "run_manifest.json", "Codex run manifest")["cohort_plan"]["selected_ids"]
        bridge._require(not (set(selected) & seen), "Sol shard selections overlap")
        seen.update(selected)
        reports.append(report)
    return {
        "schema_version": SCHEMA, "status": "read_only_partial_stop_diagnostic_not_gold",
        "runs": reports,
        "verified_completed_count": sum(len(report["verified_completed"]) for report in reports),
        "uncertain_count": sum(len(report["uncertain_ids"]) for report in reports),
        "automatic_recall_authorized": False,
        "model_calls": 0, "output_files_created": 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organizer-input", type=pathlib.Path, required=True)
    parser.add_argument("--run-dir", type=pathlib.Path, action="append", required=True)
    parser.add_argument("--source-archive", type=pathlib.Path, action="append", required=True)
    parser.add_argument("--acknowledge-stopped", action="store_true",
                        help="Confirm all supplied runner processes have stopped; this audit is read-only.")
    args = parser.parse_args(argv)
    if not args.acknowledge_stopped:
        parser.error("--acknowledge-stopped is required; do not audit live runs")
    if len(args.run_dir) != 4 or len(args.source_archive) != 4:
        parser.error("exactly four run/archive pairs are required for the Sol stop audit")
    try:
        report = audit_runs(
            organizer_input=args.organizer_input,
            runs_and_archives=list(zip(args.run_dir, args.source_archive)),
            expected_shard_indices=(0, 1, 2, 3), expected_shard_count=200,
        )
    except (bridge.PassRowBridgeError, OSError, ValueError, KeyError) as exc:
        print(f"sol partial stop audit failed closed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
