"""Gate-closed, one-shard Claude batch executor for provisional source-only votes.

No invocation occurs without --execute and a freshly recomputed dev200 Gate 1
PASS. One 100-record staged shard is executed at most once; resume and
automatic retry are deliberately unsupported. This is not a gold-label tool.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import pathlib
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

try:
    from tools.independent_gold import claude_batch_dev_gate as dev_gate
    from tools.independent_gold import claude_batch_pilot as pilot
    from tools.independent_gold import claude_unlabeled_batch_prepare as preparer
except ModuleNotFoundError:  # Direct script invocation.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from tools.independent_gold import claude_batch_dev_gate as dev_gate  # type: ignore[no-redef]
    from tools.independent_gold import claude_batch_pilot as pilot  # type: ignore[no-redef]
    from tools.independent_gold import claude_unlabeled_batch_prepare as preparer  # type: ignore[no-redef]


base = pilot.base
ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS_ROOT = ROOT / "runs"
SCHEMA = "dacon.independent.claude_unlabeled_batch_execution.v1"
BATCH_RECEIPT_SCHEMA = "dacon.independent.claude_unlabeled_batch_receipt.v1"
START_SCHEMA = "dacon.independent.claude_unlabeled_batch_start.v1"
SUMMARY_SCHEMA = "dacon.independent.claude_unlabeled_batch_summary.v1"
SOURCE_ARCHIVE_SCHEMA = "dacon.independent.claude_unlabeled_batch_source_archive.v1"


class BatchExecutionError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BatchExecutionError(message)


def _new_bytes(path: pathlib.Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


def _new_json(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    _new_bytes(path, (base.canonical_json(value) + "\n").encode("utf-8"))


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    _require(path.is_file() and not path.is_symlink(), f"missing or linked JSON: {path}")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BatchExecutionError(f"invalid JSON: {path}") from exc
    _require(isinstance(value, dict) and raw == (base.canonical_json(value) + "\n").encode("utf-8"),
             f"noncanonical JSON object: {path}")
    return value


def _sealed(path: pathlib.Path, hash_key: str) -> dict[str, Any]:
    value = _read_json(path)
    _require(value.get(hash_key) == base.sha256_object({key: child for key, child in value.items()
                                                       if key != hash_key}),
             f"self-hash differs: {path}")
    return value


def _under_runs(path: pathlib.Path, *, fresh: bool = False) -> pathlib.Path:
    absolute = path.absolute()
    _require(not any(part.is_symlink() for part in (absolute, *absolute.parents)),
             f"linked path component: {path}")
    resolved = absolute.resolve()
    _require(RUNS_ROOT.resolve() in resolved.parents and (not fresh or not resolved.exists()),
             "path must be a fresh child of repository runs" if fresh else "path must be under repository runs")
    return resolved


def _exact_bytes(path: pathlib.Path, expected: bytes) -> None:
    _require(path.is_file() and not path.is_symlink() and path.read_bytes() == expected,
             f"staged input bytes differ: {path}")


def _source_snapshot() -> dict[str, str]:
    return {
        "executor": base.file_sha256(pathlib.Path(__file__)),
        "preparer": base.file_sha256(pathlib.Path(preparer.__file__)),
        "pilot": base.file_sha256(pathlib.Path(pilot.__file__)),
        "gate": base.file_sha256(pathlib.Path(dev_gate.__file__)),
    }


def _source_archive_files(source_bundle: Mapping[str, Any]) -> list[dict[str, str]]:
    """List every byte needed to replay staging, execution and dev gate code."""
    indexed: dict[str, str] = {}
    extras = (
        pathlib.Path(__file__), pathlib.Path(preparer.__file__),
        pathlib.Path(pilot.__file__), pathlib.Path(dev_gate.__file__),
        pathlib.Path(preparer.supervisor.__file__),
        pathlib.Path(preparer.supervisor.context_preflight.__file__),
        base.RUBRIC_PATH, *dev_gate.SOURCE_FILES.values(),
    )
    for entry in source_bundle["files"]:
        relative = entry["path"]
        _require(isinstance(relative, str) and relative and ".." not in pathlib.PurePosixPath(relative).parts,
                 "unsafe source bundle path")
        path = ROOT.joinpath(*pathlib.PurePosixPath(relative).parts)
        _require(path.resolve().is_relative_to(ROOT.resolve()) and not path.is_symlink()
                 and base.file_sha256(path) == entry["sha256"],
                 f"source bundle file changed: {relative}")
        indexed[relative] = entry["sha256"]
    for path in extras:
        resolved = path.resolve(strict=True)
        _require(not path.is_symlink() and resolved.is_relative_to(ROOT.resolve()),
                 "source archive extra path leaves repository")
        relative = resolved.relative_to(ROOT.resolve()).as_posix()
        observed = base.file_sha256(resolved)
        _require(relative not in indexed or indexed[relative] == observed,
                 f"source archive extra conflicts with bundle: {relative}")
        indexed[relative] = observed
    return [{"path": relative, "sha256": indexed[relative]} for relative in sorted(indexed)]


def _archive_sources(output: pathlib.Path, plan: Mapping[str, Any]) -> str:
    """Create a verified immutable source copy before the first model call."""
    archive = output / "source_archive"
    _require(not archive.exists(), "source archive already exists")
    entries = plan["source_archive_files"]
    _require(entries == _source_archive_files(plan["source_bundle"]),
             "source archive files changed after gate plan")
    for entry in entries:
        relative = pathlib.PurePosixPath(entry["path"])
        source = ROOT.joinpath(*relative.parts)
        target = archive.joinpath(*relative.parts)
        _require(source.resolve().is_relative_to(ROOT.resolve())
                 and target.resolve().is_relative_to(archive.resolve()),
                 "source archive path escapes allowed root")
        body = source.read_bytes()
        _require(base.sha256_bytes(body) == entry["sha256"],
                 f"source changed before archival: {entry['path']}")
        _new_bytes(target, body)
        _require(base.file_sha256(target) == entry["sha256"]
                 and base.file_sha256(source) == entry["sha256"],
                 f"source changed during archival: {entry['path']}")
    archive_manifest = _seal({
        "schema_version": SOURCE_ARCHIVE_SCHEMA,
        "archive_kind": "exact_source_bytes_before_first_model_call",
        "run_manifest_sha256": plan["manifest_sha256"],
        "source_bundle_sha256": plan["source_bundle"]["bundle_sha256"],
        "files": entries,
    }, "archive_manifest_sha256")
    _new_json(archive / "manifest.json", archive_manifest)
    _require(_read_json(archive / "manifest.json") == archive_manifest
             and _source_archive_files(plan["source_bundle"]) == entries,
             "source archive verification failed")
    return archive_manifest["archive_manifest_sha256"]


def _verify_source_archive(output: pathlib.Path, plan: Mapping[str, Any], expected_sha: str) -> None:
    archive = output / "source_archive"
    manifest = _sealed(archive / "manifest.json", "archive_manifest_sha256")
    _require(manifest == {
        "schema_version": SOURCE_ARCHIVE_SCHEMA,
        "archive_kind": "exact_source_bytes_before_first_model_call",
        "run_manifest_sha256": plan["manifest_sha256"],
        "source_bundle_sha256": plan["source_bundle"]["bundle_sha256"],
        "files": plan["source_archive_files"],
        "archive_manifest_sha256": expected_sha,
    }, "frozen source archive manifest differs")
    expected_files = {"manifest.json"}
    for entry in plan["source_archive_files"]:
        relative = pathlib.PurePosixPath(entry["path"])
        path = archive.joinpath(*relative.parts)
        _require(path.is_file() and not path.is_symlink()
                 and base.file_sha256(path) == entry["sha256"],
                 f"archived source bytes differ: {entry['path']}")
        expected_files.add(relative.as_posix())
    _require(not any(path.is_symlink() for path in archive.rglob("*")) and
             {path.relative_to(archive).as_posix() for path in archive.rglob("*") if path.is_file()}
             == expected_files, "source archive file set differs")


def _verified_stage(stage_root: pathlib.Path, shard_index: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Rebuild all 100 model inputs; never trust staged prompt bytes alone."""
    stage_root = _under_runs(stage_root)
    _require(stage_root.is_dir(), "staged root missing")
    manifest = _sealed(stage_root / "run_manifest.json", "manifest_sha256")
    _require(manifest.get("schema_version") == preparer.SCHEMA_VERSION
             and manifest.get("phase") == "unlabeled_batch_input_dry_only"
             and manifest.get("qualification") == "input_prepared_only_model_quality_unverified"
             and manifest.get("model_calls") == 0 and manifest.get("batch_size") == 5
             and manifest.get("first_shard_index") == shard_index
             and manifest.get("last_shard_index") == shard_index
             and manifest.get("total_shards") == 200
             and manifest.get("records_per_shard") == 100
             and manifest.get("organizer_records") == 20000
             and manifest.get("organizer_declared_incomplete") == 853
             and manifest.get("records_with_dropped_docs") == 853
             and manifest.get("mode") in {"source_lean", "full_shared"},
             "staged run is not exactly one dry 100-record shard")
    _require(manifest.get("input_path") == str(preparer.INPUT.resolve())
             and manifest.get("input_sha256") == base.file_sha256(preparer.INPUT)
             and manifest.get("source_bundle") == base.source_bundle()
             and manifest.get("batch_pilot_source_sha256") == base.file_sha256(pathlib.Path(pilot.__file__))
             and manifest.get("preparer_source_sha256") == base.file_sha256(pathlib.Path(preparer.__file__)),
             "staged organizer input or source software changed")
    ids, count = base.select_ids(preparer.INPUT, [], shard_index=0, shard_count=1, limit=None)
    _require(count == 20000, "organizer source no longer has 20,000 records")
    admission, preflight_rows = preparer.verify_preflight(preparer.DEFAULT_PREFLIGHT, ids)
    _require(manifest.get("source_admission") == admission, "staged source preflight admission differs")
    shard_ids = preparer.shard_ids(ids)[shard_index]
    _require(manifest.get("shards") == [{"index": shard_index, "count": 100,
                                        "selected_ids_sha256": base.sha256_object(shard_ids)}]
             and manifest.get("selected_ids_sha256") == base.sha256_object(shard_ids),
             "staged shard selection differs from strided organizer IDs")
    original_rubric = base.RUBRIC_PATH.read_text(encoding="utf-8")
    rubric = base.annotation_rubric(original_rubric)
    system = pilot._system(rubric, manifest["mode"])
    schema = base.canonical_json(pilot.batch_schema(5))
    _require(manifest.get("rubric_source_sha256") == base.sha256_text(original_rubric)
             and manifest.get("rubric_projection_sha256") == base.sha256_text(rubric)
             and manifest.get("system_sha256") == base.sha256_text(system)
             and manifest.get("output_schema_sha256") == base.sha256_text(schema),
             "staged rubric, system, or schema changed")
    shard_id_set = set(shard_ids)
    _require(all(re.fullmatch(r"[A-Za-z0-9_-]+", record_id) for record_id in shard_ids),
             "unsafe organizer ID for batch row path")
    source = {record["id"]: record for record in base._records(preparer.INPUT)
              if record["id"] in shard_id_set}
    _require(set(source) == set(shard_ids) and len(source) == 100, "selected source records missing")
    row_by_id = {row["id"]: row for row in preflight_rows if row["id"] in source}
    catalog = base.full_record_context.fact_context.catalog_facts.CatalogIndex.load()
    qualification_catalog = base.full_record_context.qualification_context.qualification_facts.CatalogReference.load()
    stage_shard = stage_root / f"shard-{shard_index:03d}-of-200"
    _require(stage_shard.is_dir() and not stage_shard.is_symlink(), "staged shard directory missing")
    batches: list[dict[str, Any]] = []
    prompt_bytes = incomplete_count = dropped_count = 0
    for batch_index in range(20):
        batch_ids = shard_ids[batch_index * 5:(batch_index + 1) * 5]
        batch_dir = stage_shard / f"batch-{batch_index:02d}"
        _require(batch_dir.is_dir() and not batch_dir.is_symlink(), "staged batch directory missing")
        staged = _sealed(batch_dir / "batch_manifest.json", "manifest_sha256")
        records = [source[record_id] for record_id in batch_ids]
        rows = [row_by_id[record_id] for record_id in batch_ids]
        contexts = [base.full_record_context.build_full_record_context(
            record, catalog_index=catalog, qualification_catalog=qualification_catalog,
        ) for record in records]
        for record_id, context, row in zip(batch_ids, contexts, rows):
            _require(context["context_sha256"] == row["context_sha256"],
                     f"preflight context drift: {record_id}")
            _exact_bytes(batch_dir / f"{record_id}.full_context.json",
                         (base.canonical_json(context) + "\n").encode("utf-8"))
            incomplete_count += row["organizer_declared_incomplete"] is True
            dropped_count += row["has_dropped_docs"] is True
        projection = pilot.batch_projection(contexts, manifest["mode"])
        prompt = pilot._prompt(projection, batch_ids)
        _exact_bytes(batch_dir / "system_prompt.txt", system.encode("utf-8"))
        _exact_bytes(batch_dir / "prompt.txt", prompt.encode("utf-8"))
        _exact_bytes(batch_dir / "output_schema.json", schema.encode("utf-8"))
        expected_batch = {
            "schema_version": "dacon.independent.claude_unlabeled_batch_input.v1",
            "phase": "dry_input_only_no_model_call",
            "run_manifest_sha256": manifest["manifest_sha256"],
            "shard_index": shard_index, "batch_index": batch_index,
            "selected_ids": batch_ids, "selected_ids_sha256": base.sha256_object(batch_ids),
            "record_source_sha256_by_id": {record["id"]: row["record_sha256"]
                                           for record, row in zip(records, rows)},
            "full_context_sha256_by_id": {record["id"]: context["context_sha256"]
                                          for record, context in zip(records, contexts)},
            "completeness_by_id": {record["id"]: {
                "input_completeness": record["input_completeness"],
                "dropped_doc_counts": record["dropped_doc_counts"],
                "organizer_declared_incomplete": row["organizer_declared_incomplete"],
                "has_dropped_docs": row["has_dropped_docs"],
            } for record, row in zip(records, rows)},
            "projection_sha256": base.sha256_object(projection),
            "system_sha256": base.sha256_text(system),
            "prompt_sha256": base.sha256_text(prompt),
            "output_schema_sha256": base.sha256_text(schema),
            "prompt_bytes": len(prompt.encode("utf-8")), "model_calls": 0,
        }
        expected_batch["manifest_sha256"] = base.sha256_object(expected_batch)
        _require(staged == expected_batch, f"staged batch {batch_index} lineage differs")
        _require({path.name for path in batch_dir.iterdir()} == {
            "batch_manifest.json", "system_prompt.txt", "prompt.txt", "output_schema.json",
            *(f"{record_id}.full_context.json" for record_id in batch_ids),
        }, "staged batch file set differs")
        prompt_bytes += expected_batch["prompt_bytes"]
        batches.append({"index": batch_index, "ids": batch_ids, "records": records,
                        "contexts": contexts, "prompt": prompt, "system": system,
                        "schema": schema, "stage_manifest": staged})
    summary = _read_json(stage_root / "prepare_summary.json")
    _require(summary == {
        "schema_version": "dacon.independent.claude_unlabeled_batch_prepare_summary.v1",
        "status": "input_prepared_only_model_quality_unverified",
        "run_manifest_sha256": manifest["manifest_sha256"],
        "selected_shards": 1, "selected_records": 100, "prepared_batches": 20,
        "selected_declared_incomplete": incomplete_count,
        "selected_with_dropped_docs": dropped_count,
        "prompt_bytes": prompt_bytes, "model_calls": 0,
    }, "staged preparation summary differs")
    _require({path.name for path in stage_shard.iterdir()} ==
             {f"batch-{index:02d}" for index in range(20)}, "staged batch set differs")
    _require({path.name for path in stage_root.iterdir()} ==
             {"run_manifest.json", "prepare_summary.json", stage_shard.name},
             "staged run file set differs")
    _require(base.file_sha256(preparer.INPUT) == manifest["input_sha256"]
             and base.source_bundle() == manifest["source_bundle"],
             "organizer or source software changed during staging verification")
    return manifest, batches


def _verified_gate(score_report: pathlib.Path, stage: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    score_report = _under_runs(score_report)
    proof = dev_gate.evaluate(score_report)
    _require(proof.get("candidate_gate1_minimum_pass") is True
             and proof.get("status") == "gate1_minimum_dev_and_structural_source_pass_not_gold"
             and proof.get("qualified_for_gold_generation") is False,
             "dev200 batch teacher Gate 1 is not PASS")
    plan = _read_json(score_report.parent / "plan.json")
    frozen = dev_gate._frozen_tuple_from_plan(plan)
    expected = {
        "mode": stage["mode"], "batch_size": 5,
        "model": base.REQUESTED_MODEL, "observed_model": base.OBSERVED_MODEL,
        "rubric_sha256": stage["rubric_projection_sha256"],
        "rubric_source_sha256": stage["rubric_source_sha256"],
        "system_sha256": stage["system_sha256"],
        "input_sha256": base.file_sha256(pilot.DEV_INPUT),
        "source_bundle": stage["source_bundle"],
        "pilot_source_sha256": stage["batch_pilot_source_sha256"],
        "max_budget_usd": frozen.get("max_budget_usd"),
        "timeout_seconds": frozen.get("timeout_seconds"),
        "cli_executable_sha256": frozen.get("cli_executable_sha256"),
        "cli_version_output": frozen.get("cli_version_output"),
    }
    _require(frozen == expected, "qualified dev200 tuple differs from staged 20k tuple")
    budget, timeout = frozen["max_budget_usd"], frozen["timeout_seconds"]
    _require(type(budget) in (int, float) and math.isfinite(budget) and budget > 0
             and type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0,
             "qualified cost cap or timeout invalid")
    cli = base.resolve_cli(plan["cli"]["requested_executable"])
    _require(cli == plan["cli"] and cli["executable_sha256"] == frozen["cli_executable_sha256"]
             and cli["version_output"] == frozen["cli_version_output"],
             "current Claude CLI differs from qualified dev200 CLI")
    return proof, frozen, cli


def build_plan(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _require(type(args.shard_index) is int and 0 <= args.shard_index < 200,
             "one shard index 0..199 is required")
    output = _under_runs(args.output_dir, fresh=True)
    stage_root = _under_runs(args.staged_root)
    gate_root = _under_runs(args.score_report).parent
    _require(all(left != right and not left.is_relative_to(right)
                 and not right.is_relative_to(left)
                 for left, right in ((output, stage_root), (output, gate_root),
                                     (stage_root, gate_root))),
             "output, staged input, and dev gate trees must be disjoint")
    stage, batches = _verified_stage(args.staged_root, args.shard_index)
    gate_proof, frozen, cli = _verified_gate(args.score_report, stage)
    snapshot = _source_snapshot()
    plan: dict[str, Any] = {
        "schema_version": SCHEMA, "phase": "provisional_unlabeled_batch_vote_not_gold",
        "output_root": str(output), "staged_root": str(_under_runs(args.staged_root)),
        "staged_run_manifest_sha256": stage["manifest_sha256"],
        "staged_tree_sha256": dev_gate._tree_hash(_under_runs(args.staged_root)),
        "shard_index": args.shard_index,
        "selected_ids_sha256": stage["selected_ids_sha256"],
        "batch_manifest_sha256s": [batch["stage_manifest"]["manifest_sha256"] for batch in batches],
        "source_admission": stage["source_admission"],
        "organizer_input_sha256": stage["input_sha256"],
        "source_bundle": stage["source_bundle"],
        "source_archive_files": _source_archive_files(stage["source_bundle"]),
        "mode": stage["mode"], "batch_size": 5,
        "rubric_source_sha256": stage["rubric_source_sha256"],
        "rubric_projection_sha256": stage["rubric_projection_sha256"],
        "system_sha256": stage["system_sha256"],
        "output_schema_sha256": stage["output_schema_sha256"],
        "pilot_source_sha256": stage["batch_pilot_source_sha256"],
        "preparer_source_sha256": stage["preparer_source_sha256"],
        "score_report_path": str(_under_runs(args.score_report)),
        "score_report_sha256": base.file_sha256(args.score_report),
        "gate_tree_sha256": dev_gate._tree_hash(_under_runs(args.score_report).parent),
        "gate_proof_sha256": base.sha256_object(gate_proof),
        "gate_status": gate_proof["status"],
        "frozen_teacher_tuple_sha256": base.sha256_object(frozen),
        "model": base.REQUESTED_MODEL, "observed_model": base.OBSERVED_MODEL,
        "cli": cli, "max_budget_usd": frozen["max_budget_usd"],
        "timeout_seconds": frozen["timeout_seconds"],
        "source_code_sha256": snapshot,
        "resume_policy": "disabled_manual_reconciliation_required",
        "model_calls_when_planned": 0,
    }
    plan["manifest_sha256"] = base.sha256_object(plan)
    return plan, batches


def _seal(value: dict[str, Any], hash_key: str) -> dict[str, Any]:
    value[hash_key] = base.sha256_object(value)
    return value


def _attempt(output: pathlib.Path, plan: Mapping[str, Any], batch: Mapping[str, Any],
             archive_manifest_sha256: str) -> dict[str, Any]:
    index, ids = batch["index"], batch["ids"]
    directory = output / "batches" / f"batch-{index:02d}"
    _require(not directory.exists(), "attempted batch directory already exists; no automatic retry")
    marker = _seal({
        "schema_version": START_SCHEMA, "status": "started_unresolved_until_receipt",
        "run_manifest_sha256": plan["manifest_sha256"],
        "source_archive_manifest_sha256": archive_manifest_sha256,
        "batch_index": index, "batch_ids": ids,
        "staged_batch_manifest_sha256": batch["stage_manifest"]["manifest_sha256"],
        "system_sha256": batch["stage_manifest"]["system_sha256"],
        "prompt_sha256": batch["stage_manifest"]["prompt_sha256"],
        "output_schema_sha256": batch["stage_manifest"]["output_schema_sha256"],
        "context_sha256_by_id": batch["stage_manifest"]["full_context_sha256_by_id"],
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }, "marker_sha256")
    _new_json(directory / "start_marker.json", marker)  # Durable before CLI launch.
    returncode, stdout, stderr, transport_error = base._invoke(
        executable=plan["cli"]["resolved_executable"], system=batch["system"],
        prompt=batch["prompt"], schema_json=batch["schema"],
        timeout=plan["timeout_seconds"], max_budget_usd=plan["max_budget_usd"],
    )
    _new_bytes(directory / "stdout.bin", stdout)
    _new_bytes(directory / "stderr.bin", stderr)
    status = "ok"
    envelope: Mapping[str, Any] | None = None
    errors: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    unresolved_cells = 0
    if transport_error is not None or returncode != 0:
        status = "safety_error"
        errors["batch"] = "transport or nonzero Claude CLI return"
    else:
        try:
            envelope = base.audit_envelope(stdout)
            if envelope["total_cost_usd"] > plan["max_budget_usd"]:
                raise ValueError("reported cost exceeds qualified per-call cap")
        except ValueError as exc:
            status = "safety_error"
            errors["batch"] = f"{type(exc).__name__}: {exc}"
    if status != "safety_error":
        try:
            entries = pilot._batch_entries(envelope["structured_output"], ids)
        except ValueError as exc:
            status = "batch_content_error"
            errors["batch"] = f"{type(exc).__name__}: {exc}"
        else:
            for record, context, entry in zip(batch["records"], batch["contexts"], entries):
                record_id = record["id"]
                try:
                    ledger, normalized, normalization = base.validate_content(
                        {"structured_output": entry["annotation"]}, record, context,
                    )
                except (ValueError, KeyError, TypeError) as exc:
                    errors[record_id] = f"{type(exc).__name__}: {exc}"
                    continue
                row = {
                    "id": record_id, "status": "provisional_unqualified",
                    "ledger": ledger, "structured_output": normalized,
                    "normalization": normalization,
                    "source_sha256": base.sha256_object(record),
                    "full_context_sha256": context["context_sha256"],
                    "batch_index": index,
                }
                _new_json(directory / f"{record_id}.row.json", row)
                rows.append(row)
                unresolved_cells += sum(cell.get("label") == "U" for cell in ledger["cells"])
            if errors:
                status = "partial_content_error"
    receipt = _seal({
        "schema_version": BATCH_RECEIPT_SCHEMA, "status": status,
        "run_manifest_sha256": plan["manifest_sha256"],
        "source_archive_manifest_sha256": archive_manifest_sha256,
        "start_marker_sha256": marker["marker_sha256"],
        "batch_index": index, "batch_ids": ids,
        "staged_batch_manifest_sha256": batch["stage_manifest"]["manifest_sha256"],
        "system_sha256": batch["stage_manifest"]["system_sha256"],
        "prompt_sha256": batch["stage_manifest"]["prompt_sha256"],
        "output_schema_sha256": batch["stage_manifest"]["output_schema_sha256"],
        "context_sha256_by_id": batch["stage_manifest"]["full_context_sha256_by_id"],
        "returncode": returncode, "transport_error": transport_error,
        "stdout_sha256": base.sha256_bytes(stdout),
        "stderr_sha256": base.sha256_bytes(stderr),
        "reported_cost_usd": None if envelope is None else envelope["total_cost_usd"],
        "usage": None if envelope is None else envelope["usage"],
        "model_usage": None if envelope is None else envelope["modelUsage"],
        "errors": errors,
        "unresolved_cells": unresolved_cells,
        "row_hashes": {row["id"]: base.sha256_object(row) for row in rows},
    }, "receipt_sha256")
    _new_json(directory / "receipt.json", receipt)  # Last: absent means uncertain attempt.
    return {"status": status, "records_ok": len(rows),
            "records_content_error": 0 if status == "ok" else
            (5 - len(rows) if status != "safety_error" else 0),
            "safety_errors": int(status == "safety_error"),
            "unresolved_cells": unresolved_cells,
            "reported_cost_usd": receipt["reported_cost_usd"]}


def run(args: argparse.Namespace) -> dict[str, Any]:
    plan, batches = build_plan(args)
    if not args.execute:
        return {"status": "gate_checked_plan_only_no_model_call",
                "run_manifest_sha256": plan["manifest_sha256"],
                "shard_index": plan["shard_index"], "batches": 20,
                "model_calls": 0}
    output = _under_runs(args.output_dir, fresh=True)
    _require(base.file_sha256(args.score_report) == plan["score_report_sha256"]
             and dev_gate._tree_hash(_under_runs(args.score_report).parent) == plan["gate_tree_sha256"]
             and dev_gate._tree_hash(_under_runs(args.staged_root)) == plan["staged_tree_sha256"]
             and _source_snapshot() == plan["source_code_sha256"],
             "gate or executor source changed before first invocation")
    output.mkdir(parents=True, exist_ok=False)
    _new_json(output / "run_manifest.json", plan)
    archive_manifest_sha256 = _archive_sources(output, plan)
    _verify_source_archive(output, plan, archive_manifest_sha256)
    summary: dict[str, Any] = {
        "schema_version": SUMMARY_SCHEMA,
        "status": "complete_provisional_unqualified_not_gold",
        "run_manifest_sha256": plan["manifest_sha256"],
        "source_archive_manifest_sha256": archive_manifest_sha256,
        "shard_index": plan["shard_index"],
        "batches_planned": 20, "batches_attempted": 0,
        "records_planned": 100, "records_ok": 0,
        "records_content_error": 0, "safety_errors": 0,
        "unresolved_cells": 0,
        "reported_cost_usd": 0.0,
        "resume_policy": "disabled_manual_reconciliation_required",
    }
    for batch in batches:
        _require(base.file_sha256(args.score_report) == plan["score_report_sha256"]
                 and dev_gate._tree_hash(_under_runs(args.score_report).parent) == plan["gate_tree_sha256"]
                 and dev_gate._tree_hash(_under_runs(args.staged_root)) == plan["staged_tree_sha256"]
                 and _source_snapshot() == plan["source_code_sha256"]
                 and base.file_sha256(preparer.INPUT) == plan["organizer_input_sha256"]
                 and base.source_bundle() == plan["source_bundle"]
                 and _source_archive_files(plan["source_bundle"]) == plan["source_archive_files"]
                 and base.file_sha256(pathlib.Path(plan["cli"]["resolved_executable"])) ==
                 plan["cli"]["executable_sha256"]
                 and base.file_sha256(base.RUBRIC_PATH) == plan["rubric_source_sha256"]
                 and base.file_sha256(preparer.DEFAULT_PREFLIGHT) ==
                 plan["source_admission"]["report_file_sha256"]
                 and base.file_sha256(preparer.DEFAULT_PREFLIGHT.parent / "records.jsonl") ==
                 plan["source_admission"]["record_rows_sha256"],
                 "gate, source, or organizer input changed before next batch")
        _verify_source_archive(output, plan, archive_manifest_sha256)
        result = _attempt(output, plan, batch, archive_manifest_sha256)
        summary["batches_attempted"] += 1
        summary["records_ok"] += result["records_ok"]
        summary["records_content_error"] += result["records_content_error"]
        summary["safety_errors"] += result["safety_errors"]
        summary["unresolved_cells"] += result["unresolved_cells"]
        summary["reported_cost_usd"] += result["reported_cost_usd"] or 0.0
        if result["status"] != "ok":
            summary["status"] = ("stopped_safety_not_gold" if result["status"] == "safety_error"
                                 else "stopped_content_not_gold")
            break
    if summary["batches_attempted"] != 20 or summary["records_ok"] != 100:
        _require(summary["status"] != "complete_provisional_unqualified_not_gold",
                 "incomplete shard cannot be marked complete")
    _new_json(output / "run_summary.json", _seal(summary, "summary_sha256"))
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged-root", type=pathlib.Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--score-report", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Only after exact dev200 Gate 1 PASS")
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except (BatchExecutionError, ValueError, KeyError, TypeError, OSError,
            dev_gate.DevGateError, base.full_record_context.FullRecordContextError) as exc:
        print(f"Claude unlabeled batch execution refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(base.canonical_json(result))
    return 0 if result["status"] in {
        "gate_checked_plan_only_no_model_call", "complete_provisional_unqualified_not_gold"
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
