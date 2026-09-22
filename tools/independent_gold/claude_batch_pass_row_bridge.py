"""Export one fully raw-verified Claude batch shard as provisional pass rows.

This adapter makes no model calls, does not coerce U, and never assigns gold.
It requires the exact organizer stage, its mechanical source archive, the
execution's source archive and all 20 raw batch receipts to pass the separate
read-only verifier. Only fresh output directories under runs/ are accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import tempfile
from collections.abc import Mapping, Sequence
from typing import Any

try:
    from tools.independent_gold import claude_batch_pilot as pilot
    from tools.independent_gold import provisional_release
    from tools.independent_gold import verify_claude_unlabeled_batch as verifier
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from tools.independent_gold import claude_batch_pilot as pilot  # type: ignore[no-redef]
    from tools.independent_gold import provisional_release  # type: ignore[no-redef]
    from tools.independent_gold import verify_claude_unlabeled_batch as verifier  # type: ignore[no-redef]


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
SCHEMA = "dacon.independent.claude_batch_pass_row_bridge.v1"
PASS_STATUSES = frozenset({
    "executed_100_provisional_rows_structurally_verified_not_gold",
    "executed_100_provisional_rows_with_U_not_gold",
})


class BatchPassRowBridgeError(ValueError):
    """A batch shard cannot safely enter a provisional pass."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BatchPassRowBridgeError(message)


def _sha_file(path: pathlib.Path) -> str:
    _require(path.is_file() and not path.is_symlink(), f"missing or linked file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: pathlib.Path) -> dict[str, Any]:
    try:
        return verifier._json_file(path)
    except (verifier.BatchVerificationError, OSError, ValueError, TypeError) as exc:
        raise BatchPassRowBridgeError(f"invalid exact JSON artifact {path}: {exc}") from exc


def _software_hashes() -> dict[str, str]:
    return {
        "bridge": _sha_file(pathlib.Path(__file__)),
        "verifier": _sha_file(pathlib.Path(verifier.__file__)),
        "pilot": _sha_file(pathlib.Path(pilot.__file__)),
        "provisional_release": _sha_file(pathlib.Path(provisional_release.__file__)),
        "full_record_output": _sha_file(pathlib.Path(provisional_release.full_record_output.__file__)),
    }


def _sealed(path: pathlib.Path, key: str) -> dict[str, Any]:
    try:
        return verifier._sealed(path, key)
    except (verifier.BatchVerificationError, OSError, ValueError, TypeError) as exc:
        raise BatchPassRowBridgeError(f"invalid sealed artifact {path}: {exc}") from exc


def _safe_input_dir(path: pathlib.Path) -> pathlib.Path:
    absolute = path.absolute()
    _require(not any(component.is_symlink() for component in (absolute, *absolute.parents)),
             "linked input path component")
    resolved = absolute.resolve()
    _require(resolved.is_dir() and RUNS.resolve() in resolved.parents,
             "input directory must be an existing child of runs/")
    return resolved


def _fresh_output_dir(path: pathlib.Path, protected: Sequence[pathlib.Path]) -> pathlib.Path:
    absolute = path.absolute()
    _require(not any(component.is_symlink() for component in (absolute, *absolute.parents)),
             "linked output path component")
    resolved = absolute.resolve()
    _require(RUNS.resolve() in resolved.parents and not resolved.exists(),
             "output must be a fresh child of runs/")
    _require(not any(resolved == other or resolved.is_relative_to(other)
                     or other.is_relative_to(resolved) for other in protected),
             "output may not overlap stage, execution, or source archive")
    return resolved


def _verified_report(stage: pathlib.Path, execution: pathlib.Path,
                     dry_archive: pathlib.Path) -> dict[str, Any]:
    report = verifier.verify_execution(stage, execution, dry_archive)
    _require(report.get("schema_version") == verifier.SCHEMA
             and report.get("status") in PASS_STATUSES
             and report.get("structural_full_shard_verified") is True
             and report.get("provisional_pass_rows_eligible") is True
             and report.get("gold_qualification") is False
             and report.get("model_calls_by_verifier") == 0
             and report.get("planned_records") == 100
             and report.get("planned_batches") == 20
             and report.get("attempted_batches") == 20
             and report.get("completed_receipts") == 20
             and report.get("verified_prefix_batches") == 20
             and report.get("verified_prefix_records") == 100
             and report.get("structurally_replayed_rows_including_nonprefix") == 100
             and report.get("nonprefix_replayed_rows") == 0
             and report.get("unverified_planned_records") == 0
             and report.get("unverified_planned_cells") == 0
             and report.get("u_cells_in_nonprefix_rows") == 0
             and report.get("unreceipted_attempt_batch") is None,
             "verifier did not certify a complete 100-row provisional shard")
    archive_proof = report.get("staged_source_archive")
    _require(isinstance(archive_proof, Mapping)
             and archive_proof.get("path") == str(dry_archive)
             and archive_proof.get("tree_sha256") == verifier._tree_hash(dry_archive)
             and archive_proof.get("manifest_sha256") == _sha_file(dry_archive / "manifest.json"),
             "exact dry-stage source archive proof missing or changed")
    return report


def _pass_rows(stage: pathlib.Path, execution: pathlib.Path,
               report: Mapping[str, Any], role: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    shard_index = report["shard_index"]
    _require(type(shard_index) is int and 0 <= shard_index < 200, "invalid verified shard index")
    stage_manifest = _sealed(stage / "run_manifest.json", "manifest_sha256")
    execution_manifest = _sealed(execution / "run_manifest.json", "manifest_sha256")
    _require(stage_manifest.get("manifest_sha256") == report["staged_run_manifest_sha256"]
             and execution_manifest.get("manifest_sha256") == report["execution_manifest_sha256"]
             and execution_manifest.get("staged_run_manifest_sha256") == stage_manifest["manifest_sha256"]
             and execution_manifest.get("staged_root") == str(stage)
             and stage_manifest.get("first_shard_index") == shard_index
             and stage_manifest.get("last_shard_index") == shard_index
             and execution_manifest.get("shard_index") == shard_index
             and stage_manifest.get("mode") == report["mode"] == execution_manifest.get("mode")
             and stage_manifest.get("input_sha256") == execution_manifest.get("organizer_input_sha256")
             == _sha_file(verifier.INPUT),
             "staged/executed organizer source or shard lineage differs")
    _require(execution_manifest.get("model") == pilot.base.REQUESTED_MODEL
             and execution_manifest.get("observed_model") == pilot.base.OBSERVED_MODEL,
             "execution model contract differs")
    archive_manifest = _sealed(execution / "source_archive" / "manifest.json",
                               "archive_manifest_sha256")
    _require(archive_manifest["archive_manifest_sha256"] == report["source_archive_manifest_sha256"]
             and archive_manifest.get("run_manifest_sha256") == execution_manifest["manifest_sha256"],
             "execution source archive is not bound to exact run")
    _, records, preflight_rows, _ = verifier._load_source_and_preflight(shard_index)
    _require(len(records) == len(preflight_rows) == 100, "verified organizer shard is not 100 records")
    record_by_id = {record["id"]: record for record in records}
    preflight_by_id = {row["id"]: row for row in preflight_rows}
    _require(len(record_by_id) == len(preflight_by_id) == 100
             and set(record_by_id) == set(preflight_by_id), "organizer/preflight shard IDs differ")
    pass_by_id: dict[str, dict[str, Any]] = {}
    record_proofs: dict[str, dict[str, Any]] = {}
    for batch_index in range(20):
        staged = _sealed(stage / f"shard-{shard_index:03d}-of-200" /
                         f"batch-{batch_index:02d}" / "batch_manifest.json", "manifest_sha256")
        receipt = _sealed(execution / "batches" / f"batch-{batch_index:02d}" /
                          "receipt.json", "receipt_sha256")
        ids = [record["id"] for record in records[batch_index * 5:(batch_index + 1) * 5]]
        _require(staged.get("selected_ids") == ids
                 and staged.get("run_manifest_sha256") == stage_manifest["manifest_sha256"]
                 and receipt.get("batch_ids") == ids
                 and receipt.get("run_manifest_sha256") == execution_manifest["manifest_sha256"]
                 and receipt.get("staged_batch_manifest_sha256") == staged["manifest_sha256"]
                 and receipt.get("status") == "ok"
                 and receipt.get("errors") == {}
                 and receipt.get("prompt_sha256") == staged.get("prompt_sha256")
                 and receipt.get("source_archive_manifest_sha256") ==
                 archive_manifest["archive_manifest_sha256"]
                 and isinstance(receipt.get("row_hashes"), Mapping)
                 and set(receipt["row_hashes"]) == set(ids),
                 f"batch {batch_index} receipt/stage lineage or success differs")
        model_usage = receipt.get("model_usage")
        body_model = model_usage.get(pilot.base.OBSERVED_MODEL) if isinstance(model_usage, Mapping) else None
        _require(isinstance(body_model, Mapping)
                 and body_model.get("canonicalModel") == pilot.base.OBSERVED_MODEL
                 and body_model.get("provider") == "firstParty",
                 f"batch {batch_index} model identity differs")
        for record_id in ids:
            record, preflight = record_by_id[record_id], preflight_by_id[record_id]
            row = _json(execution / "batches" / f"batch-{batch_index:02d}" /
                        f"{record_id}.row.json")
            flags = staged["completeness_by_id"][record_id]
            _require(row.get("id") == record_id
                     and row.get("status") == "provisional_unqualified"
                     and row.get("batch_index") == batch_index
                     and row.get("source_sha256") == pilot.base.sha256_object(record)
                     and pilot.base.sha256_object(row) == receipt["row_hashes"][record_id]
                     and flags.get("input_completeness") == record["input_completeness"]
                     and flags.get("dropped_doc_counts") == record["dropped_doc_counts"]
                     and flags.get("organizer_declared_incomplete") is
                     preflight["organizer_declared_incomplete"]
                     and flags.get("has_dropped_docs") is preflight["has_dropped_docs"],
                     f"{record_id}: row source, receipt, or incompleteness flags differ")
            ledger = row.get("ledger")
            _require(isinstance(ledger, Mapping), f"{record_id}: missing source-bound ledger")
            provisional_release.validate_ledger(record, ledger)
            u_cells = sum(cell["label"] == "U" for cell in ledger["cells"])
            pass_row = {
                "schema_version": provisional_release.PASS_ROW_SCHEMA,
                "record_id": record_id,
                "source_sha256": row["source_sha256"],
                "annotator_role": role,
                "lineage": {
                    "provider": "anthropic",
                    "model_family": f"anthropic:{body_model['canonicalModel']}",
                    "model_name": body_model["canonicalModel"],
                    "prompt_sha256": staged["prompt_sha256"],
                    "receipt_sha256": receipt["receipt_sha256"],
                    "input_boundary": "competition_source_only",
                    "blind_first_pass": True,
                    "peer_answer_visible": False,
                    "production_output_visible": False,
                },
                "ledger": ledger,
            }
            provisional_release._validate_pass_row(
                pass_row, role=role,
                source_hashes={record_id: row["source_sha256"]},
                context=f"batch bridge {record_id}",
            )
            _require(record_id not in pass_by_id, f"duplicate bridged ID: {record_id}")
            pass_by_id[record_id] = pass_row
            record_proofs[record_id] = {
                "record_id": record_id,
                "batch_index": batch_index,
                "source_sha256": row["source_sha256"],
                "raw_row_sha256": pilot.base.sha256_object(row),
                "prompt_sha256": staged["prompt_sha256"],
                "receipt_sha256": receipt["receipt_sha256"],
                "organizer_declared_incomplete": flags["organizer_declared_incomplete"],
                "has_dropped_docs": flags["has_dropped_docs"],
                "input_completeness": flags["input_completeness"],
                "dropped_doc_counts": flags["dropped_doc_counts"],
                "u_cells": u_cells,
                "release_route": "all_cells_abstain_source_incomplete" if
                                 provisional_release._source_incomplete(record)
                                 else "U_cells_abstain_other_cells_draft" if u_cells
                                 else "single_pass_draft_not_gold",
            }
    _require(len(pass_by_id) == len(record_proofs) == 100
             and set(pass_by_id) == set(record_by_id), "bridge did not preserve all 100 IDs")
    ordered_rows = [pass_by_id[record["id"]] for record in records]
    ordered_proofs = [record_proofs[record["id"]] for record in records]
    observed_u = sum(entry["u_cells"] for entry in ordered_proofs)
    _require(observed_u == report["u_cells_in_verified_prefix"]
             == report["u_cells_in_structurally_replayed_rows"]
             and (report["status"] == "executed_100_provisional_rows_with_U_not_gold")
             == (observed_u > 0), "U cells/status differ from exact raw ledger")
    return ordered_rows, ordered_proofs


def bridge(*, staged_root: pathlib.Path, execution_root: pathlib.Path,
           staged_source_archive: pathlib.Path, output_dir: pathlib.Path,
           role: str = "candidate") -> dict[str, Any]:
    """Publish fresh pass rows only after complete exact raw/stage/archive replay."""
    _require(role in {"candidate", "verifier"}, "release role must be candidate or verifier")
    stage = _safe_input_dir(staged_root)
    execution = _safe_input_dir(execution_root)
    dry_archive = _safe_input_dir(staged_source_archive)
    _require(len({stage, execution, dry_archive}) == 3
             and not any(first.is_relative_to(second) or second.is_relative_to(first)
                         for index, first in enumerate((stage, execution, dry_archive))
                         for second in (stage, execution, dry_archive)[index + 1:]),
             "stage, execution, and source archive must be separate")
    output = _fresh_output_dir(output_dir, (stage, execution, dry_archive))
    source_sha = _sha_file(verifier.INPUT)
    software = _software_hashes()
    snapshots = {"stage": verifier._tree_hash(stage),
                 "execution": verifier._tree_hash(execution),
                 "staged_source_archive": verifier._tree_hash(dry_archive)}
    report = _verified_report(stage, execution, dry_archive)
    rows, record_proofs = _pass_rows(stage, execution, report, role)
    _require(_sha_file(verifier.INPUT) == source_sha and _software_hashes() == software
             and snapshots == {"stage": verifier._tree_hash(stage),
                               "execution": verifier._tree_hash(execution),
                               "staged_source_archive": verifier._tree_hash(dry_archive)},
             "source, raw execution, stage, or archive changed while bridging")
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = pathlib.Path(tempfile.mkdtemp(prefix=f".{output.name}.bridge-", dir=output.parent))
    try:
        rows_path = scratch / "pass_rows.jsonl"
        with rows_path.open("xb") as handle:
            for row in rows:
                handle.write((pilot.base.canonical_json(row) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        provenance = {
            "schema_version": SCHEMA,
            "status": "complete_provisional_pass_rows_with_U_not_gold" if
                      report["u_cells_in_verified_prefix"] else
                      "complete_provisional_pass_rows_not_gold",
            "gold_qualification": False,
            "release_role": role,
            "pass_row_schema": provisional_release.PASS_ROW_SCHEMA,
            "raw_runner_role": None,
            "role_assignment_kind": "posthoc_independent_vote_order",
            "staged_root": str(stage), "execution_root": str(execution),
            "staged_source_archive": str(dry_archive),
            "staged_run_manifest_sha256": report["staged_run_manifest_sha256"],
            "execution_manifest_sha256": report["execution_manifest_sha256"],
            "execution_source_archive_manifest_sha256": report["source_archive_manifest_sha256"],
            "verifier_report": report,
            "verifier_report_sha256": pilot.base.sha256_object(report),
            "software_source_sha256": software,
            "organizer_input_sha256": source_sha,
            "source_tree_sha256": snapshots,
            "records": 100, "cells": 2400,
            "u_cells": report["u_cells_in_verified_prefix"],
            "organizer_declared_incomplete": sum(
                entry["organizer_declared_incomplete"] is True for entry in record_proofs),
            "records_with_dropped_docs": sum(
                entry["has_dropped_docs"] is True for entry in record_proofs),
            "pass_rows_sha256": _sha_file(rows_path),
            "record_proofs": record_proofs,
            "model_calls_by_bridge": 0,
        }
        provenance_path = scratch / "provenance.json"
        with provenance_path.open("xb") as handle:
            handle.write((pilot.base.canonical_json(provenance) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        _require(_sha_file(verifier.INPUT) == source_sha and _software_hashes() == software
                 and snapshots == {"stage": verifier._tree_hash(stage),
                                   "execution": verifier._tree_hash(execution),
                                   "staged_source_archive": verifier._tree_hash(dry_archive)},
                 "input evidence changed before provisional publication")
        _require(not output.exists(), "output appeared during bridge")
        os.replace(scratch, output)
        return {"status": provenance["status"], "output_dir": str(output),
                "pass_rows": str(output / "pass_rows.jsonl"),
                "pass_rows_sha256": provenance["pass_rows_sha256"],
                "provenance": str(output / "provenance.json"),
                "records": 100, "u_cells": provenance["u_cells"],
                "gold_qualification": False, "model_calls_by_bridge": 0}
    finally:
        if scratch.exists():
            for child in scratch.iterdir():
                if child.is_file():
                    child.unlink()
            scratch.rmdir()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged-root", type=pathlib.Path, required=True)
    parser.add_argument("--execution-root", type=pathlib.Path, required=True)
    parser.add_argument("--staged-source-archive", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--release-role", choices=("candidate", "verifier"), default="candidate")
    args = parser.parse_args(argv)
    try:
        result = bridge(staged_root=args.staged_root, execution_root=args.execution_root,
                        staged_source_archive=args.staged_source_archive,
                        output_dir=args.output_dir, role=args.release_role)
    except (BatchPassRowBridgeError, verifier.BatchVerificationError,
            provisional_release.ProvisionalReleaseError, OSError, ValueError,
            KeyError, TypeError, AttributeError, IndexError) as exc:
        print(json.dumps({"schema_version": SCHEMA, "status": "bridge_refused_not_gold",
                          "reason": f"{type(exc).__name__}: {exc}",
                          "model_calls_by_bridge": 0}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
