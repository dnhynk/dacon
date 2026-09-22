"""Read-only raw-replay verifier for a staged 100-record Claude batch shard.

The verifier is separate from the executor.  It uses only organizer source,
the sealed source preflight, the frozen staged inputs, and (when supplied) raw
execution artifacts.  It never calls a model, opens dev labels, writes a row,
or describes provisional decisions as gold.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import itertools
import json
import math
import pathlib
import sys
from collections.abc import Mapping, Sequence
from typing import Any

try:
    from tools.independent_gold import claude_batch_pilot as pilot
    from tools.independent_gold import claude_batch_dev_gate as dev_gate
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from tools.independent_gold import claude_batch_pilot as pilot  # type: ignore[no-redef]
    from tools.independent_gold import claude_batch_dev_gate as dev_gate  # type: ignore[no-redef]


base = pilot.base
ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
INPUT = ROOT / "data_open" / "train_unlabeled.jsonl.gz"
PREFLIGHT = RUNS / "self_label_20000_20260918" / "source_preflight_v2" / "report.json"
PREPARER = ROOT / "tools" / "independent_gold" / "claude_unlabeled_batch_prepare.py"
EXECUTOR = ROOT / "tools" / "independent_gold" / "claude_unlabeled_batch_executor.py"
SCHEMA = "dacon.independent.claude_unlabeled_batch_verification.v1"
TOTAL_SHARDS = 200
RECORDS_PER_SHARD = 100
BATCH_SIZE = 5
EXPECTED_INCOMPLETE = 853


class BatchVerificationError(ValueError):
    """Source, staged input, or raw execution was not independently verified."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BatchVerificationError(message)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_text(value: str) -> str:
    return _sha_bytes(value.encode("utf-8"))


def _sha_object(value: Any) -> str:
    return _sha_text(_canonical(value))


def _sha_file(path: pathlib.Path) -> str:
    _require(path.is_file() and not path.is_symlink(), f"missing or linked file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        _require(key not in value, f"duplicate JSON key: {key}")
        value[key] = child
    return value


def _bad_constant(value: str) -> Any:
    raise BatchVerificationError(f"nonfinite JSON constant: {value}")


def _json_file(path: pathlib.Path, *, canonical: bool = True) -> dict[str, Any]:
    _sha_file(path)
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_bad_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BatchVerificationError(f"invalid JSON artifact: {path}") from exc
    _require(isinstance(value, dict), f"JSON artifact is not an object: {path}")
    if canonical:
        _require(raw == (_canonical(value) + "\n").encode("utf-8"),
                 f"noncanonical staged JSON artifact: {path}")
    return value


def _safe_dir(raw: pathlib.Path) -> pathlib.Path:
    absolute = raw.absolute()
    _require(not any(path.is_symlink() for path in (absolute, *absolute.parents)),
             "linked run path component")
    path = absolute.resolve()
    runs = RUNS.resolve()
    _require(path.is_dir() and path != runs and path.is_relative_to(runs),
             "run must be a directory below repository runs")
    return path


def _tree_hash(root: pathlib.Path) -> str:
    _require(root.is_dir() and not root.is_symlink(), "missing or linked staged tree")
    entries: list[tuple[str, str]] = []
    for path in root.rglob("*"):
        _require(not path.is_symlink(), f"linked staged artifact: {path}")
        if path.is_file():
            entries.append((path.relative_to(root).as_posix(), _sha_file(path)))
    _require(bool(entries), "empty staged tree")
    return _sha_object(sorted(entries))


def _load_source_and_preflight(shard_index: int) -> tuple[list[str], list[dict[str, Any]],
                                                          list[dict[str, Any]], dict[str, Any]]:
    """Reconcile all 20k source/preflight rows, retaining only one strided shard."""
    _require(0 <= shard_index < TOTAL_SHARDS, "shard index must be 0..199")
    report = _json_file(PREFLIGHT, canonical=False)
    rows_path = PREFLIGHT.parent / "records.jsonl"
    _require(report.get("status") == "pass", "source preflight did not pass")
    counts = report.get("counts")
    _require(isinstance(counts, Mapping) and counts.get("processed") == 20_000
             and counts.get("passed") == 20_000 and counts.get("failed") == 0
             and counts.get("unique_ids") == 20_000
             and counts.get("organizer_declared_incomplete") == EXPECTED_INCOMPLETE
             and counts.get("records_with_dropped_docs") == EXPECTED_INCOMPLETE,
             "preflight coverage or completeness counts differ")
    input_sha = _sha_file(INPUT)
    _require(report.get("input", {}).get("sha256_before") == input_sha
             and report.get("input", {}).get("sha256_after") == input_sha,
             "organizer input differs from preflight")
    rows_sha = _sha_file(rows_path)
    _require(report.get("record_rows", {}).get("sha256") == rows_sha,
             "preflight row ledger SHA differs")
    all_ids: list[str] = []
    selected: list[dict[str, Any]] = []
    selected_preflight: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    declared_count = dropped_count = 0
    with gzip.open(INPUT, "rb") as source, rows_path.open("rb") as ledger:
        for ordinal, (raw, row_raw) in enumerate(itertools.zip_longest(source, ledger)):
            _require(raw is not None and row_raw is not None,
                     "organizer/preflight row counts differ")
            try:
                record = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                                    parse_constant=_bad_constant)
                row = json.loads(row_raw.decode("utf-8"), object_pairs_hook=_pairs,
                                 parse_constant=_bad_constant)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise BatchVerificationError(f"invalid source/preflight JSON line {ordinal + 1}") from exc
            _require(isinstance(record, Mapping) and isinstance(row, Mapping),
                     f"source/preflight row {ordinal + 1} is not an object")
            record_id = record.get("id")
            _require(isinstance(record_id, str) and record_id,
                     f"source row {ordinal + 1} ID invalid")
            docs = record.get("docs")
            completeness = record.get("input_completeness")
            dropped = record.get("dropped_doc_counts")
            _require(isinstance(docs, list) and docs and isinstance(completeness, Mapping)
                     and isinstance(dropped, Mapping),
                     f"source row {ordinal + 1} shape invalid")
            incomplete = any(value is False for value in completeness.values())
            has_drops = any(type(value) is int and value > 0 for value in dropped.values())
            _require(row.get("line_number") == ordinal + 1 and row.get("id") == record_id
                     and row.get("status") == "pass"
                     and row.get("record_sha256") == _sha_object(record)
                     and row.get("doc_count") == len(docs)
                     and row.get("source_chars") == sum(len(doc["text"]) for doc in docs)
                     and row.get("organizer_declared_incomplete") is incomplete
                     and row.get("has_dropped_docs") is has_drops
                     and isinstance(row.get("context_sha256"), str),
                     f"source/preflight drift at line {ordinal + 1}")
            all_ids.append(record_id)
            digest.update((record_id + "\n").encode("utf-8"))
            declared_count += incomplete
            dropped_count += has_drops
            if ordinal % TOTAL_SHARDS == shard_index:
                selected.append(dict(record))
                selected_preflight.append(dict(row))
    _require(len(all_ids) == 20_000 and len(set(all_ids)) == 20_000
             and len(selected) == RECORDS_PER_SHARD
             and declared_count == EXPECTED_INCOMPLETE and dropped_count == EXPECTED_INCOMPLETE
             and digest.hexdigest() == report.get("id_order_sha256")
             and _sha_file(INPUT) == input_sha and _sha_file(rows_path) == rows_sha,
             "source/preflight full coverage or stable-hash check failed")
    return all_ids, selected, selected_preflight, {
        "path": str(PREFLIGHT.resolve()),
        "report_file_sha256": _sha_file(PREFLIGHT),
        "record_rows_sha256": rows_sha,
        "id_order_sha256": digest.hexdigest(),
        "status": "pass",
    }


def _reconstruct_docs(record: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    registry = context.get("allowed_span_registry")
    organizer = context.get("organizer_record")
    _require(isinstance(registry, Mapping) and isinstance(organizer, Mapping),
             "context source registry or organizer descriptor missing")
    documents = organizer.get("documents")
    _require(isinstance(documents, list) and len(documents) == len(record["docs"]),
             "context document inventory differs from organizer")
    for index, (doc, descriptor) in enumerate(zip(record["docs"], documents)):
        _require(isinstance(descriptor, Mapping), "context document descriptor invalid")
        span_ids = descriptor.get("span_ids")
        _require(isinstance(span_ids, list) and all(span_id in registry for span_id in span_ids),
                 "context document has missing source spans")
        text = "".join(registry[span_id]["quote"] for span_id in span_ids)
        _require(text == doc["text"] and descriptor.get("doc_index") == index
                 and descriptor.get("doc_id") == doc["doc_id"]
                 and descriptor.get("doc_type") == doc["type"]
                 and descriptor.get("chars") == len(text)
                 and descriptor.get("sha256") == _sha_text(text),
                 f"supplied document {index} was not reconstructed losslessly")
    _require(organizer.get("record_sha256") == _sha_object(record)
             and context.get("source_completeness", {}).get("input_completeness")
             == record["input_completeness"]
             and context.get("source_completeness", {}).get("dropped_doc_counts")
             == record["dropped_doc_counts"],
             "context source SHA or incomplete flags differ")


def _verify_staged_with_material(staged_root: pathlib.Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Independently re-create every staged context, projection, and prompt."""
    root = _safe_dir(staged_root)
    tree_before = _tree_hash(root)
    manifest = _json_file(root / "run_manifest.json")
    shard_index = manifest.get("first_shard_index")
    _require(type(shard_index) is int and 0 <= shard_index < TOTAL_SHARDS
             and manifest.get("last_shard_index") == shard_index,
             "staged run must contain exactly one 100-record shard")
    all_ids, records, preflight_rows, admission = _load_source_and_preflight(shard_index)
    selected_ids = all_ids[shard_index::TOTAL_SHARDS]
    _require([record["id"] for record in records] == selected_ids,
             "organizer strided shard IDs differ")
    mode = manifest.get("mode")
    _require(mode in {"source_lean", "full_shared"}, "staged projection mode invalid")
    original_rubric = base.RUBRIC_PATH.read_text(encoding="utf-8")
    rubric = base.annotation_rubric(original_rubric)
    system = pilot._system(rubric, mode)
    expected_manifest = {
        "schema_version": "dacon.independent.claude_unlabeled_batch_prepare.v1",
        "phase": "unlabeled_batch_input_dry_only",
        "qualification": "input_prepared_only_model_quality_unverified",
        "input_path": str(INPUT.resolve()),
        "input_sha256": _sha_file(INPUT),
        "source_admission": admission,
        "organizer_records": 20_000,
        "organizer_declared_incomplete": EXPECTED_INCOMPLETE,
        "records_with_dropped_docs": EXPECTED_INCOMPLETE,
        "total_shards": TOTAL_SHARDS,
        "records_per_shard": RECORDS_PER_SHARD,
        "batch_size": BATCH_SIZE,
        "mode": mode,
        "first_shard_index": shard_index,
        "last_shard_index": shard_index,
        "selected_ids_sha256": _sha_object(selected_ids),
        "shards": [{"index": shard_index, "count": RECORDS_PER_SHARD,
                    "selected_ids_sha256": _sha_object(selected_ids)}],
        "source_bundle": base.source_bundle(),
        "batch_pilot_source_sha256": _sha_file(pathlib.Path(pilot.__file__)),
        "preparer_source_sha256": _sha_file(PREPARER),
        "rubric_source_sha256": _sha_text(original_rubric),
        "rubric_projection_sha256": _sha_text(rubric),
        "system_sha256": _sha_text(system),
        "output_schema_sha256": _sha_object(pilot.batch_schema(BATCH_SIZE)),
        "model_calls": 0,
    }
    expected_manifest["manifest_sha256"] = _sha_object(expected_manifest)
    _require(manifest == expected_manifest, "staged run manifest differs from exact source tuple")
    shard_dir = root / f"shard-{shard_index:03d}-of-200"
    _require({path.name for path in root.iterdir()}
             == {"run_manifest.json", "prepare_summary.json", shard_dir.name},
             "unexpected staged root artifact")
    _require(shard_dir.is_dir() and {path.name for path in shard_dir.iterdir()}
             == {f"batch-{index:02d}" for index in range(20)},
             "staged shard lacks exact 20 batches")
    catalog = base.full_record_context.fact_context.catalog_facts.CatalogIndex.load()
    qualification_catalog = (base.full_record_context.qualification_context
                             .qualification_facts.CatalogReference.load())
    total_prompt_bytes = declared = dropped = supplied_docs = 0
    materials: list[dict[str, Any]] = []
    for batch_index in range(20):
        batch = shard_dir / f"batch-{batch_index:02d}"
        group = records[batch_index * BATCH_SIZE:(batch_index + 1) * BATCH_SIZE]
        rows = preflight_rows[batch_index * BATCH_SIZE:(batch_index + 1) * BATCH_SIZE]
        ids = [record["id"] for record in group]
        _require({path.name for path in batch.iterdir()} == {
            "system_prompt.txt", "prompt.txt", "output_schema.json", "batch_manifest.json",
            *(f"{record_id}.full_context.json" for record_id in ids),
        }, f"batch {batch_index}: unexpected or missing staged artifact")
        contexts = []
        flags: dict[str, Any] = {}
        for record, row in zip(group, rows):
            record_id = record["id"]
            stored_context = _json_file(batch / f"{record_id}.full_context.json")
            rebuilt = base.full_record_context.build_full_record_context(
                record, catalog_index=catalog, qualification_catalog=qualification_catalog,
            )
            _require(stored_context == rebuilt
                     and stored_context.get("context_sha256") == row["context_sha256"],
                     f"{record_id}: staged context differs from source/preflight rebuild")
            validation = base.full_record_context.validate_full_record_context(
                record, stored_context, catalog_index=catalog,
            )
            _require(not validation, f"{record_id}: full context validation failed")
            _reconstruct_docs(record, stored_context)
            contexts.append(stored_context)
            incomplete = row["organizer_declared_incomplete"]
            has_drops = row["has_dropped_docs"]
            flags[record_id] = {
                "input_completeness": record["input_completeness"],
                "dropped_doc_counts": record["dropped_doc_counts"],
                "organizer_declared_incomplete": incomplete,
                "has_dropped_docs": has_drops,
            }
            declared += incomplete
            dropped += has_drops
            supplied_docs += len(record["docs"])
        projection = pilot.batch_projection(contexts, mode)
        prompt = pilot._prompt(projection, ids)
        schema = _canonical(pilot.batch_schema(BATCH_SIZE))
        _require((batch / "system_prompt.txt").read_bytes() == system.encode("utf-8")
                 and (batch / "prompt.txt").read_bytes() == prompt.encode("utf-8")
                 and (batch / "output_schema.json").read_bytes() == schema.encode("utf-8"),
                 f"batch {batch_index}: prompt/system/schema bytes differ")
        expected_batch = {
            "schema_version": "dacon.independent.claude_unlabeled_batch_input.v1",
            "phase": "dry_input_only_no_model_call",
            "run_manifest_sha256": manifest["manifest_sha256"],
            "shard_index": shard_index,
            "batch_index": batch_index,
            "selected_ids": ids,
            "selected_ids_sha256": _sha_object(ids),
            "record_source_sha256_by_id": {record["id"]: row["record_sha256"]
                                           for record, row in zip(group, rows)},
            "full_context_sha256_by_id": {record["id"]: context["context_sha256"]
                                          for record, context in zip(group, contexts)},
            "completeness_by_id": flags,
            "projection_sha256": _sha_object(projection),
            "system_sha256": _sha_text(system),
            "prompt_sha256": _sha_text(prompt),
            "output_schema_sha256": _sha_text(schema),
            "prompt_bytes": len(prompt.encode("utf-8")),
            "model_calls": 0,
        }
        expected_batch["manifest_sha256"] = _sha_object(expected_batch)
        _require(_json_file(batch / "batch_manifest.json") == expected_batch,
                 f"batch {batch_index}: staged manifest or completeness flags differ")
        total_prompt_bytes += expected_batch["prompt_bytes"]
        materials.append({"index": batch_index, "ids": ids, "records": group,
                          "contexts": contexts, "stage_manifest": expected_batch})
    summary = _json_file(root / "prepare_summary.json")
    _require(summary == {
        "schema_version": "dacon.independent.claude_unlabeled_batch_prepare_summary.v1",
        "status": "input_prepared_only_model_quality_unverified",
        "run_manifest_sha256": manifest["manifest_sha256"],
        "selected_shards": 1,
        "selected_records": RECORDS_PER_SHARD,
        "prepared_batches": 20,
        "selected_declared_incomplete": declared,
        "selected_with_dropped_docs": dropped,
        "prompt_bytes": total_prompt_bytes,
        "model_calls": 0,
    }, "staged summary differs from independently reconstructed inputs")
    _require(_tree_hash(root) == tree_before and _sha_file(INPUT) == manifest["input_sha256"]
             and base.source_bundle() == manifest["source_bundle"]
             and _sha_file(PREPARER) == manifest["preparer_source_sha256"],
             "staged inputs or source tuple changed during verification")
    proof = {
        "schema_version": SCHEMA,
        "status": "staged_inputs_verified_model_unrun_not_gold",
        "staged_root": str(root),
        "staged_tree_sha256": tree_before,
        "staged_run_manifest_sha256": manifest["manifest_sha256"],
        "input_sha256": manifest["input_sha256"],
        "source_admission": admission,
        "mode": mode,
        "shard_index": shard_index,
        "selected_ids_sha256": _sha_object(selected_ids),
        "selected_records": RECORDS_PER_SHARD,
        "prepared_batches": 20,
        "supplied_documents_reconstructed": supplied_docs,
        "organizer_declared_incomplete": declared,
        "records_with_dropped_docs": dropped,
        "provisional_rows_verified": 0,
        "unresolved_cells": None,
        "structural_full_shard_verified": False,
        "provisional_pass_rows_eligible": False,
        "gold_qualification": False,
        "model_calls_by_verifier": 0,
    }
    return proof, materials


def verify_staged(staged_root: pathlib.Path) -> dict[str, Any]:
    """Independently re-create every staged context, projection, and prompt."""
    proof, _ = _verify_staged_with_material(staged_root)
    return proof


def _sealed(path: pathlib.Path, hash_key: str) -> dict[str, Any]:
    value = _json_file(path)
    _require(value.get(hash_key) == _sha_object({key: child for key, child in value.items()
                                                if key != hash_key}),
             f"artifact self-hash differs: {path}")
    return value


def _source_snapshot() -> dict[str, str]:
    return {
        "executor": _sha_file(EXECUTOR),
        "preparer": _sha_file(PREPARER),
        "pilot": _sha_file(pathlib.Path(pilot.__file__)),
        "gate": _sha_file(pathlib.Path(dev_gate.__file__)),
    }


def _source_archive_files(source_bundle: Mapping[str, Any]) -> list[dict[str, str]]:
    """Independently inventory every frozen source byte, including extra modules."""
    files = source_bundle.get("files")
    _require(isinstance(files, list) and source_bundle.get("bundle_sha256") == _sha_object(files),
             "source bundle manifest invalid")
    indexed: dict[str, str] = {}
    for entry in files:
        _require(isinstance(entry, Mapping) and isinstance(entry.get("path"), str)
                 and isinstance(entry.get("sha256"), str), "source bundle entry invalid")
        relative = pathlib.PurePosixPath(entry["path"])
        _require(not relative.is_absolute() and ".." not in relative.parts
                 and entry["path"] == relative.as_posix(), "unsafe source bundle path")
        path = ROOT.joinpath(*relative.parts)
        _require(path.resolve().is_relative_to(ROOT.resolve())
                 and _sha_file(path) == entry["sha256"],
                 f"current source bundle byte mismatch: {relative}")
        _require(entry["path"] not in indexed, "duplicate source bundle file")
        indexed[entry["path"]] = entry["sha256"]
    extras = [
        EXECUTOR, PREPARER, pathlib.Path(pilot.__file__), pathlib.Path(dev_gate.__file__),
        ROOT / "tools" / "independent_gold" / "claude_shard_supervisor.py",
        ROOT / "tools" / "independent_gold" / "context_preflight.py",
        base.RUBRIC_PATH, *dev_gate.SOURCE_FILES.values(),
    ]
    for path in extras:
        resolved = path.resolve(strict=True)
        _require(resolved.is_relative_to(ROOT.resolve()) and not path.is_symlink(),
                 "source archive extra path leaves repository")
        relative = resolved.relative_to(ROOT.resolve()).as_posix()
        observed = _sha_file(resolved)
        _require(relative not in indexed or indexed[relative] == observed,
                 f"source archive entry conflicts with bundle: {relative}")
        indexed[relative] = observed
    return [{"path": relative, "sha256": indexed[relative]} for relative in sorted(indexed)]


def _verify_staged_archive(staged_root: pathlib.Path,
                           archive_root: pathlib.Path) -> dict[str, str]:
    """Verify the separate mechanical archive of dry-stage source bytes."""
    archive = _safe_dir(archive_root)
    _require(archive != staged_root, "stage source archive overlaps stage")
    stage_manifest = _json_file(staged_root / "run_manifest.json")
    stored = _json_file(archive / "manifest.json")
    extras = [
        {"path": "tools/independent_gold/claude_batch_pilot.py",
         "sha256": stage_manifest["batch_pilot_source_sha256"]},
        {"path": "tools/independent_gold/claude_unlabeled_batch_prepare.py",
         "sha256": stage_manifest["preparer_source_sha256"]},
    ]
    expected = {
        "schema_version": "dacon.independent.source_archive.v1",
        "bundle_sha256": stage_manifest["source_bundle"]["bundle_sha256"],
        "files": stage_manifest["source_bundle"]["files"],
        "rubric_sha256": stage_manifest["rubric_source_sha256"],
        "run_manifest_sha256": stage_manifest["manifest_sha256"],
        "archive_kind": "mechanical_source_bytes_only_no_model_call",
        "extra_sources": extras,
    }
    _require(stored == expected, "dry-stage source archive manifest differs")
    expected_files = {entry["path"] for entry in [*expected["files"], *extras]}
    expected_files.add("tools/independent_gold/rubric_v1.md")
    expected_files.add("manifest.json")
    observed_files = {path.relative_to(archive).as_posix() for path in archive.rglob("*")
                      if path.is_file()}
    _require(observed_files == expected_files, "dry-stage source archive file set differs")
    for entry in [*expected["files"], *extras]:
        relative = pathlib.PurePosixPath(entry["path"])
        _require(not relative.is_absolute() and ".." not in relative.parts,
                 "dry-stage archive path unsafe")
        _require(_sha_file(archive.joinpath(*relative.parts)) == entry["sha256"],
                 f"dry-stage archived byte differs: {entry['path']}")
    _require(_sha_file(archive / "tools" / "independent_gold" / "rubric_v1.md")
             == expected["rubric_sha256"], "dry-stage archived rubric differs")
    return {"path": str(archive), "manifest_sha256": _sha_file(archive / "manifest.json"),
            "tree_sha256": _tree_hash(archive)}


def _verify_source_archive(root: pathlib.Path, plan: Mapping[str, Any]) -> str:
    archive = root / "source_archive"
    _require(archive.is_dir() and not archive.is_symlink(), "exact source archive missing")
    entries = _source_archive_files(plan["source_bundle"])
    _require(plan.get("source_archive_files") == entries,
             "execution source archive inventory differs from live exact source")
    manifest = _sealed(archive / "manifest.json", "archive_manifest_sha256")
    expected = {
        "schema_version": "dacon.independent.claude_unlabeled_batch_source_archive.v1",
        "archive_kind": "exact_source_bytes_before_first_model_call",
        "run_manifest_sha256": plan["manifest_sha256"],
        "source_bundle_sha256": plan["source_bundle"]["bundle_sha256"],
        "files": entries,
    }
    expected["archive_manifest_sha256"] = _sha_object(expected)
    _require(manifest == expected, "source archive manifest differs from frozen plan")
    observed_files = {path.relative_to(archive).as_posix() for path in archive.rglob("*")
                      if path.is_file()}
    _require(observed_files == {"manifest.json", *(entry["path"] for entry in entries)},
             "source archive has missing or extra files")
    for entry in entries:
        relative = pathlib.PurePosixPath(entry["path"])
        frozen = archive.joinpath(*relative.parts)
        _require(frozen.resolve().is_relative_to(archive.resolve())
                 and _sha_file(frozen) == entry["sha256"],
                 f"archived source byte mismatch: {entry['path']}")
    return manifest["archive_manifest_sha256"]


def _execution_plan(root: pathlib.Path, stage: Mapping[str, Any],
                    materials: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Bind the execution manifest to the independently verified stage/gate."""
    stored = _sealed(root / "run_manifest.json", "manifest_sha256")
    score_path = pathlib.Path(stored.get("score_report_path", ""))
    _require(score_path.name == "score_report.json", "execution score path invalid")
    gate_proof = dev_gate.evaluate(score_path)
    _require(gate_proof.get("candidate_gate1_minimum_pass") is True
             and gate_proof.get("status") == "gate1_minimum_dev_and_structural_source_pass_not_gold"
             and gate_proof.get("qualified_for_gold_generation") is False,
             "execution teacher does not currently pass exact dev200 Gate 1")
    gate_plan = _json_file(score_path.parent / "plan.json")
    frozen = dev_gate._frozen_tuple_from_plan(gate_plan)
    expected_frozen = {
        "mode": stage["mode"], "batch_size": BATCH_SIZE,
        "model": base.REQUESTED_MODEL, "observed_model": base.OBSERVED_MODEL,
        "rubric_sha256": stored["rubric_projection_sha256"],
        "rubric_source_sha256": stored["rubric_source_sha256"],
        "system_sha256": stored["system_sha256"],
        "input_sha256": _sha_file(pilot.DEV_INPUT),
        "source_bundle": base.source_bundle(),
        "pilot_source_sha256": _sha_file(pathlib.Path(pilot.__file__)),
        "max_budget_usd": frozen.get("max_budget_usd"),
        "timeout_seconds": frozen.get("timeout_seconds"),
        "cli_executable_sha256": frozen.get("cli_executable_sha256"),
        "cli_version_output": frozen.get("cli_version_output"),
    }
    _require(frozen == expected_frozen, "dev200 tuple differs from staged batch tuple")
    _require(type(frozen["max_budget_usd"]) in (int, float)
             and math.isfinite(frozen["max_budget_usd"]) and frozen["max_budget_usd"] > 0
             and type(frozen["timeout_seconds"]) in (int, float)
             and math.isfinite(frozen["timeout_seconds"]) and frozen["timeout_seconds"] > 0,
             "qualified budget or timeout invalid")
    cli = gate_plan.get("cli")
    _require(isinstance(cli, Mapping) and cli.get("executable_sha256") == frozen["cli_executable_sha256"]
             and cli.get("version_output") == frozen["cli_version_output"],
             "execution CLI identity differs from qualified gate")
    _require(base.resolve_cli(cli["requested_executable"]) == cli,
             "current Claude CLI bytes/version differ from qualified gate")
    expected = {
        "schema_version": "dacon.independent.claude_unlabeled_batch_execution.v1",
        "phase": "provisional_unlabeled_batch_vote_not_gold",
        "output_root": str(root),
        "staged_root": stage["staged_root"],
        "staged_run_manifest_sha256": stage["staged_run_manifest_sha256"],
        "staged_tree_sha256": stage["staged_tree_sha256"],
        "shard_index": stage["shard_index"],
        "selected_ids_sha256": stage["selected_ids_sha256"],
        "batch_manifest_sha256s": [material["stage_manifest"]["manifest_sha256"]
                                   for material in materials],
        "source_admission": stage["source_admission"],
        "organizer_input_sha256": stage["input_sha256"],
        "source_bundle": base.source_bundle(),
        "source_archive_files": _source_archive_files(base.source_bundle()),
        "mode": stage["mode"],
        "batch_size": BATCH_SIZE,
        "rubric_source_sha256": _sha_text(base.RUBRIC_PATH.read_text(encoding="utf-8")),
        "rubric_projection_sha256": _sha_text(base.annotation_rubric(
            base.RUBRIC_PATH.read_text(encoding="utf-8"))),
        "system_sha256": _sha_text(pilot._system(base.annotation_rubric(
            base.RUBRIC_PATH.read_text(encoding="utf-8")), stage["mode"])),
        "output_schema_sha256": _sha_text(_canonical(pilot.batch_schema(BATCH_SIZE))),
        "pilot_source_sha256": _sha_file(pathlib.Path(pilot.__file__)),
        "preparer_source_sha256": _sha_file(PREPARER),
        "score_report_path": str(score_path.resolve()),
        "score_report_sha256": _sha_file(score_path),
        "gate_tree_sha256": _tree_hash(score_path.parent),
        "gate_proof_sha256": _sha_object(gate_proof),
        "gate_status": gate_proof["status"],
        "frozen_teacher_tuple_sha256": _sha_object(frozen),
        "model": base.REQUESTED_MODEL,
        "observed_model": base.OBSERVED_MODEL,
        "cli": dict(cli),
        "max_budget_usd": frozen["max_budget_usd"],
        "timeout_seconds": frozen["timeout_seconds"],
        "source_code_sha256": _source_snapshot(),
        "resume_policy": "disabled_manual_reconciliation_required",
        "model_calls_when_planned": 0,
    }
    expected["manifest_sha256"] = _sha_object(expected)
    _require(stored == expected, "execution manifest differs from stage, gate, or source tuple")
    return stored


def _started_marker(directory: pathlib.Path, plan: Mapping[str, Any],
                    material: Mapping[str, Any], archive_sha: str) -> dict[str, Any]:
    marker = _sealed(directory / "start_marker.json", "marker_sha256")
    index, staged = material["index"], material["stage_manifest"]
    expected = {
        "schema_version": "dacon.independent.claude_unlabeled_batch_start.v1",
        "status": "started_unresolved_until_receipt",
        "run_manifest_sha256": plan["manifest_sha256"],
        "source_archive_manifest_sha256": archive_sha,
        "batch_index": index,
        "batch_ids": material["ids"],
        "staged_batch_manifest_sha256": staged["manifest_sha256"],
        "system_sha256": staged["system_sha256"],
        "prompt_sha256": staged["prompt_sha256"],
        "output_schema_sha256": staged["output_schema_sha256"],
        "context_sha256_by_id": staged["full_context_sha256_by_id"],
        "started_utc": marker.get("started_utc"),
    }
    _require(marker == {**expected, "marker_sha256": _sha_object(expected)},
             f"batch {index}: start marker differs from staged inputs")
    try:
        started = dt.datetime.fromisoformat(marker["started_utc"].replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise BatchVerificationError(f"batch {index}: invalid start timestamp") from exc
    _require(started.tzinfo is not None and started.utcoffset() == dt.timedelta(0),
             f"batch {index}: start timestamp is not UTC")
    return marker


def _replay_attempt(directory: pathlib.Path, plan: Mapping[str, Any],
                    material: Mapping[str, Any], marker: Mapping[str, Any],
                    archive_sha: str) -> dict[str, Any]:
    """Recompute receipt/rows from raw bytes; no executor code is invoked."""
    index, ids = material["index"], material["ids"]
    receipt = _sealed(directory / "receipt.json", "receipt_sha256")
    _sha_file(directory / "stdout.bin")
    _sha_file(directory / "stderr.bin")
    stdout = (directory / "stdout.bin").read_bytes()
    stderr = (directory / "stderr.bin").read_bytes()
    _require(_sha_bytes(stdout) == receipt.get("stdout_sha256")
             and _sha_bytes(stderr) == receipt.get("stderr_sha256"),
             f"batch {index}: raw Claude stdout/stderr hash differs")
    returncode = receipt.get("returncode")
    transport_error = receipt.get("transport_error")
    _require((returncode is None or type(returncode) is int)
             and (transport_error is None or isinstance(transport_error, str)),
             f"batch {index}: CLI return/transport metadata invalid")
    status = "ok"
    envelope: Mapping[str, Any] | None = None
    errors: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
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
        assert envelope is not None
        try:
            entries = pilot._batch_entries(envelope["structured_output"], ids)
        except ValueError as exc:
            status = "batch_content_error"
            errors["batch"] = f"{type(exc).__name__}: {exc}"
        else:
            for record, context, entry in zip(material["records"], material["contexts"], entries):
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
                    "source_sha256": _sha_object(record),
                    "full_context_sha256": context["context_sha256"],
                    "batch_index": index,
                }
                _require(_json_file(directory / f"{record_id}.row.json") == row,
                         f"batch {index}: replayed provisional row differs: {record_id}")
                rows.append(row)
            if errors:
                status = "partial_content_error"
    staged = material["stage_manifest"]
    u_cells = sum(cell.get("label") == "U" for row in rows
                  for cell in row["ledger"]["cells"])
    expected = {
        "schema_version": "dacon.independent.claude_unlabeled_batch_receipt.v1",
        "status": status,
        "run_manifest_sha256": plan["manifest_sha256"],
        "source_archive_manifest_sha256": archive_sha,
        "start_marker_sha256": marker["marker_sha256"],
        "batch_index": index,
        "batch_ids": ids,
        "staged_batch_manifest_sha256": staged["manifest_sha256"],
        "system_sha256": staged["system_sha256"],
        "prompt_sha256": staged["prompt_sha256"],
        "output_schema_sha256": staged["output_schema_sha256"],
        "context_sha256_by_id": staged["full_context_sha256_by_id"],
        "returncode": returncode,
        "transport_error": transport_error,
        "stdout_sha256": _sha_bytes(stdout),
        "stderr_sha256": _sha_bytes(stderr),
        "reported_cost_usd": None if envelope is None else envelope["total_cost_usd"],
        "usage": None if envelope is None else envelope["usage"],
        "model_usage": None if envelope is None else envelope["modelUsage"],
        "errors": errors,
        "row_hashes": {row["id"]: _sha_object(row) for row in rows},
        "unresolved_cells": u_cells,
    }
    expected["receipt_sha256"] = _sha_object(expected)
    _require(receipt == expected, f"batch {index}: receipt differs from raw replay")
    _require({path.name for path in directory.iterdir()} == {
        "start_marker.json", "stdout.bin", "stderr.bin", "receipt.json",
        *(f"{row['id']}.row.json" for row in rows),
    }, f"batch {index}: artifact file set differs from replayed rows")
    return {"status": status, "rows": len(rows), "content_error":
            0 if status in {"ok", "safety_error"} else BATCH_SIZE - len(rows),
            "safety_error": int(status == "safety_error"),
            "reported_cost_usd": expected["reported_cost_usd"] or 0.0,
            "u_cells": u_cells}


def verify_execution(staged_root: pathlib.Path, execution_root: pathlib.Path,
                     staged_source_archive: pathlib.Path | None = None) -> dict[str, Any]:
    """Return a verified complete prefix or an explicitly unresolved stop."""
    stage, materials = _verify_staged_with_material(staged_root)
    dry_archive = (_verify_staged_archive(pathlib.Path(stage["staged_root"]),
                                          staged_source_archive)
                   if staged_source_archive is not None else None)
    root = _safe_dir(execution_root)
    _require(root != pathlib.Path(stage["staged_root"]), "execution overlaps staged inputs")
    stage_hash_before = stage["staged_tree_sha256"]
    output_hash_before = _tree_hash(root)
    plan = _execution_plan(root, stage, materials)
    archive_sha = _verify_source_archive(root, plan)
    batches_dir = root / "batches"
    attempted_dirs = sorted(path for path in batches_dir.iterdir() if path.is_dir()) if batches_dir.exists() else []
    _require(all(not path.is_symlink() for path in attempted_dirs)
             and [path.name for path in attempted_dirs]
             == [f"batch-{index:02d}" for index in range(len(attempted_dirs))]
             and len(attempted_dirs) <= 20,
             "execution batch directories are not an exact contiguous prefix")
    completed = prefix = replayed_rows = u_prefix = u_nonprefix = content_errors = safety_errors = 0
    cost = 0.0
    unreceipted_attempt: int | None = None
    stop_status: str | None = None
    for index, directory in enumerate(attempted_dirs):
        material = materials[index]
        marker = _started_marker(directory, plan, material, archive_sha)
        if not (directory / "receipt.json").exists():
            _require(index == len(attempted_dirs) - 1,
                     "unreceipted attempted batch has a successor")
            unreceipted_attempt = index
            break
        replay = _replay_attempt(directory, plan, material, marker, archive_sha)
        completed += 1
        replayed_rows += replay["rows"]
        content_errors += replay["content_error"]
        safety_errors += replay["safety_error"]
        cost += replay["reported_cost_usd"]
        if replay["status"] == "ok":
            prefix += 1
            u_prefix += replay["u_cells"]
        else:
            _require(index == len(attempted_dirs) - 1,
                     "non-ok batch has a successor; automatic continuation forbidden")
            u_nonprefix += replay["u_cells"]
            stop_status = replay["status"]
            break
    _require(not batches_dir.exists() or {path.name for path in batches_dir.iterdir()}
             == {path.name for path in attempted_dirs},
             "unexpected execution batch artifact")
    summary_path = root / "run_summary.json"
    summary_present = summary_path.exists()
    if summary_present:
        _require(unreceipted_attempt is None,
                 "run summary exists despite an unreceipted CLI attempt")
        expected_status = (
            "stopped_safety_not_gold" if stop_status == "safety_error" else
            "stopped_content_not_gold" if stop_status is not None else
            "complete_provisional_unqualified_not_gold"
        )
        _require(stop_status is not None or completed == 20,
                 "incomplete attempt count cannot have a complete summary")
        expected_summary = {
            "schema_version": "dacon.independent.claude_unlabeled_batch_summary.v1",
            "status": expected_status,
            "run_manifest_sha256": plan["manifest_sha256"],
            "source_archive_manifest_sha256": archive_sha,
            "shard_index": stage["shard_index"],
            "batches_planned": 20,
            "batches_attempted": completed,
            "records_planned": 100,
            "records_ok": replayed_rows,
            "records_content_error": content_errors,
            "safety_errors": safety_errors,
            "unresolved_cells": u_prefix + u_nonprefix,
            "reported_cost_usd": cost,
            "resume_policy": "disabled_manual_reconciliation_required",
        }
        expected_summary["summary_sha256"] = _sha_object(expected_summary)
        _require(_sealed(summary_path, "summary_sha256") == expected_summary,
                 "execution summary differs from raw receipt replay")
    _require({path.name for path in root.iterdir()}
             == {"run_manifest.json", "source_archive", *( ["batches"] if batches_dir.exists() else []),
                 *( ["run_summary.json"] if summary_present else [])},
             "unexpected execution root artifact")
    _require(_tree_hash(root) == output_hash_before
             and _tree_hash(pathlib.Path(stage["staged_root"])) == stage_hash_before
             and (dry_archive is None or _tree_hash(pathlib.Path(dry_archive["path"]))
                  == dry_archive["tree_sha256"])
             and _tree_hash(pathlib.Path(plan["score_report_path"]).parent)
             == plan["gate_tree_sha256"]
             and _sha_file(pathlib.Path(plan["score_report_path"]))
             == plan["score_report_sha256"]
             and _source_snapshot() == plan["source_code_sha256"]
             and _source_archive_files(plan["source_bundle"]) == plan["source_archive_files"],
             "execution, staged, gate, or source bytes changed during verification")
    complete = summary_present and completed == prefix == 20 and stop_status is None
    status = (
        "executed_100_provisional_rows_structurally_verified_not_gold" if complete and u_prefix == 0 else
        "executed_100_provisional_rows_with_U_not_gold" if complete else
        "stopped_safety_with_verified_prefix_not_gold" if stop_status == "safety_error" else
        "stopped_content_with_verified_prefix_not_gold" if stop_status is not None else
        "interrupted_unsealed_attempt_not_gold"
    )
    return {
        "schema_version": SCHEMA, "status": status,
        "staged_run_manifest_sha256": stage["staged_run_manifest_sha256"],
        "execution_manifest_sha256": plan["manifest_sha256"],
        "source_archive_manifest_sha256": archive_sha,
        "staged_source_archive": dry_archive,
        "shard_index": stage["shard_index"], "mode": stage["mode"],
        "planned_records": 100, "planned_batches": 20,
        "attempted_batches": len(attempted_dirs),
        "completed_receipts": completed,
        "verified_prefix_batches": prefix,
        "verified_prefix_records": prefix * BATCH_SIZE,
        "structurally_replayed_rows_including_nonprefix": replayed_rows,
        "nonprefix_replayed_rows": replayed_rows - prefix * BATCH_SIZE,
        "unverified_planned_records": 100 - prefix * BATCH_SIZE,
        "unverified_planned_cells": (100 - prefix * BATCH_SIZE) * 24,
        "u_cells_in_verified_prefix": u_prefix,
        "u_cells_in_nonprefix_rows": u_nonprefix,
        "u_cells_in_structurally_replayed_rows": u_prefix + u_nonprefix,
        "structural_full_shard_verified": complete,
        "provisional_pass_rows_eligible": complete,
        "unreceipted_attempt_batch": unreceipted_attempt,
        "organizer_declared_incomplete": stage["organizer_declared_incomplete"],
        "records_with_dropped_docs": stage["records_with_dropped_docs"],
        "gold_qualification": False,
        "model_calls_by_verifier": 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged-root", type=pathlib.Path, required=True)
    parser.add_argument("--execution-root", type=pathlib.Path,
                        help="When present, replay raw attempted batches and receipts")
    parser.add_argument("--staged-source-archive", type=pathlib.Path,
                        help="Optional exact mechanical archive bound to the dry stage")
    args = parser.parse_args(argv)
    try:
        if args.execution_root is not None:
            report = verify_execution(args.staged_root, args.execution_root,
                                      args.staged_source_archive)
        else:
            report = verify_staged(args.staged_root)
            if args.staged_source_archive is not None:
                report["staged_source_archive"] = _verify_staged_archive(
                    pathlib.Path(report["staged_root"]), args.staged_source_archive)
    except (BatchVerificationError, dev_gate.DevGateError, ValueError, KeyError,
            TypeError, AttributeError, IndexError, OSError) as exc:
        print(json.dumps({"schema_version": SCHEMA,
                          "status": "verification_failed_not_gold",
                          "gold_qualification": False,
                          "reason": f"{type(exc).__name__}: {exc}",
                          "model_calls_by_verifier": 0},
                         ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] in {
        "staged_inputs_verified_model_unrun_not_gold",
        "executed_100_provisional_rows_structurally_verified_not_gold",
        "executed_100_provisional_rows_with_U_not_gold",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
