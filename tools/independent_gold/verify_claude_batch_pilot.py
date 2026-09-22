"""Read-only, fail-closed verifier for the diagnostic Claude batch pilot.

The exact source archive (including the pilot source as an extra source) is
mandatory. No model is called, no result is designated gold, and no file is
written. Archived Python is not executed: live source must match every archived
byte before the original inputs and model artifacts are reconstructed.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path, PurePosixPath
import sys
from typing import Any, Mapping

try:
    from tools.independent_gold import claude_batch_pilot as pilot
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import claude_batch_pilot as pilot


SCHEMA = "dacon.independent.claude_batch_pilot_verification.v1"
ARCHIVE_SCHEMA = "dacon.independent.source_archive.v1"
RUNS_ROOT = pilot.ROOT / "runs"
PILOT_PATH = "tools/independent_gold/claude_batch_pilot.py"
RUBRIC_PATH = "tools/independent_gold/rubric_v1.md"
MANIFEST_KEYS = {
    "schema_version", "phase", "input_sha256", "selected_ids", "start_index", "batch_size",
    "mode", "model", "observed_model", "rubric_sha256", "rubric_source_sha256",
    "system_sha256", "source_bundle", "pilot_source_sha256", "cli", "execute",
    "qualification", "manifest_sha256",
    "max_budget_usd", "timeout_seconds",
}


class BatchVerificationError(ValueError):
    """An artifact failed an exact read-only verification gate."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BatchVerificationError(message)


def _sha_bytes(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    _require(path.is_file() and not path.is_symlink(), f"missing or symlinked file: {path}")
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _bad_constant(value: str) -> None:
    raise BatchVerificationError(f"non-JSON numeric constant: {value}")


def _json_file(path: Path) -> dict[str, Any]:
    _require(path.is_file() and not path.is_symlink(), f"missing or symlinked JSON: {path}")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_bad_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BatchVerificationError(f"invalid JSON: {path}") from exc
    _require(isinstance(value, dict), f"JSON object required: {path}")
    _require(raw == (pilot.base.canonical_json(value) + "\n").encode("utf-8"), f"noncanonical JSON bytes: {path}")
    return value


def _safe_path(root: Path, relative: str) -> Path:
    _require(isinstance(relative, str), "archive source path is not text")
    parsed = PurePosixPath(relative)
    _require(
        relative == parsed.as_posix() and not parsed.is_absolute()
        and all(part not in {"", ".", ".."} for part in parsed.parts),
        f"unsafe archive source path: {relative}",
    )
    path = root.joinpath(*parsed.parts)
    _require(root.resolve() in path.resolve().parents and not path.is_symlink(), f"archive source escapes root: {relative}")
    return path


def _verify_archive(archive: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    _require(archive.is_dir() and not archive.is_symlink(), "source archive missing or symlinked")
    archive_manifest_path = archive / "manifest.json"
    frozen = _json_file(archive_manifest_path)
    _require(set(frozen) == {
        "schema_version", "bundle_sha256", "files", "rubric_sha256",
        "run_manifest_sha256", "archive_kind", "extra_sources",
    }, "source archive manifest shape differs")
    _require(frozen["schema_version"] == ARCHIVE_SCHEMA and
             frozen["archive_kind"] == "mechanical_source_bytes_only_no_model_call",
             "source archive kind differs")
    bundle = manifest["source_bundle"]
    _require(isinstance(bundle, Mapping) and isinstance(bundle.get("files"), list), "run source bundle missing")
    _require(pilot.base.sha256_object(bundle["files"]) == bundle.get("bundle_sha256") == frozen["bundle_sha256"],
             "source bundle self-hash or archive binding differs")
    _require(frozen["run_manifest_sha256"] == manifest["manifest_sha256"], "archive is not anchored to exact run")
    _require(frozen["files"] == bundle["files"], "archived source file list differs from run")
    _require(frozen["extra_sources"] == [{"path": PILOT_PATH, "sha256": manifest["pilot_source_sha256"]}],
             "pilot source is absent or differs from archive extra_sources")
    _require(frozen["rubric_sha256"] == manifest["rubric_source_sha256"], "archive rubric differs from run")
    entries = [*frozen["files"], *frozen["extra_sources"],
               {"path": RUBRIC_PATH, "sha256": frozen["rubric_sha256"]}]
    indexed: dict[str, str] = {}
    for entry in entries:
        _require(isinstance(entry, Mapping) and set(entry) == {"path", "sha256"}, "invalid archive file entry")
        relative, expected = entry["path"], entry["sha256"]
        _require(isinstance(expected, str) and len(expected) == 64 and
                 all(char in "0123456789abcdef" for char in expected), "invalid archive source hash")
        _require(relative not in indexed, "duplicate archive source path")
        indexed[relative] = expected
        frozen_file = _safe_path(archive, relative)
        live_file = _safe_path(pilot.ROOT, relative)
        _require(_sha_file(frozen_file) == expected, f"archived source bytes differ: {relative}")
        _require(_sha_file(live_file) == expected, f"live source differs from exact archive: {relative}")
    _require(not any(path.is_symlink() for path in archive.rglob("*")), "archive contains a symlink")
    actual = {path.relative_to(archive).as_posix() for path in archive.rglob("*") if path.is_file()}
    _require(actual == set(indexed) | {"manifest.json"}, "archive contains unexpected or missing files")
    return {
        "archive_manifest_sha256": _sha_file(archive_manifest_path),
        "source_bundle_sha256": bundle["bundle_sha256"],
        "pilot_source_sha256": manifest["pilot_source_sha256"],
        "files_verified": len(indexed),
    }


def _verify_manifest(run: Path) -> dict[str, Any]:
    manifest = _json_file(run / "run_manifest.json")
    _require(set(manifest) == MANIFEST_KEYS, "batch run manifest shape differs")
    _require(manifest["schema_version"] == pilot.SCHEMA_VERSION and
             manifest["phase"] == "blind_dev_batch_diagnostic_only" and
             manifest["qualification"] == "none_not_gold", "batch run identity differs")
    _require(manifest["manifest_sha256"] == pilot.base.sha256_object({
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    }), "batch run manifest self-hash differs")
    _require(manifest["mode"] in {"full_shared", "source_lean"} and
             type(manifest["batch_size"]) is int and 1 <= manifest["batch_size"] <= 5 and
             type(manifest["execute"]) is bool, "batch mode, size, or execute flag invalid")
    budget = manifest["max_budget_usd"]
    _require(type(budget) in {int, float} and math.isfinite(budget) and budget > 0,
             "batch per-call budget missing or invalid")
    timeout = manifest["timeout_seconds"]
    _require(type(timeout) in {int, float} and math.isfinite(timeout) and timeout > 0,
             "batch timeout missing or invalid")
    selected = manifest["selected_ids"]
    _require(isinstance(selected, list) and 1 <= len(selected) <= 20 and
             len(selected) == len(set(selected)) and all(isinstance(value, str) and value for value in selected),
             "selected organizer IDs invalid")
    start_index = manifest["start_index"]
    _require(type(start_index) is int and 0 <= start_index < 200 and
             start_index + len(selected) <= 200,
             "batch organizer dev window is invalid")
    _require(manifest["model"] == pilot.base.REQUESTED_MODEL and
             manifest["observed_model"] == pilot.base.OBSERVED_MODEL,
             "batch model contract differs")
    _require(isinstance(manifest["cli"], Mapping) and
             isinstance(manifest["cli"].get("executable_sha256"), str), "batch CLI provenance absent")
    return manifest


def _refs(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        if set(value) == {"_compact_ref"}:
            return [value["_compact_ref"]]
        return [reference for child in value.values() for reference in _refs(child)]
    if isinstance(value, list):
        return [reference for child in value for reference in _refs(child)]
    return []


def _verify_projection(projection: Mapping[str, Any], contexts: list[Mapping[str, Any]], mode: str) -> None:
    _require(projection.get("mode") == mode and len(projection.get("records", [])) == len(contexts),
             "batch projection mode or record count differs")
    shared = projection["shared"]
    for view, context in zip(projection["records"], contexts):
        _require(shared == {"law_contexts": context["law_contexts"],
                            "law_reference_registry": context["law_reference_registry"]},
                 "shared law context differs from original context")
        if mode == "full_shared":
            _require({**view, **shared} == pilot.base.compact_context(context),
                     "full_shared projection is not reversible")
        else:
            _require(view["organizer_record"] == context["organizer_record"] and
                     view["source_completeness"] == context["source_completeness"] and
                     view["source_span_text"] == {
                         span_id: span["quote"] for span_id, span in context["allowed_span_registry"].items()
                     } and view["qualification_contexts"] == pilot.base.compact_context(context)["qualification_contexts"] and
                     view["qualification_shared_values"] == pilot.base.compact_context(context)["qualification_shared_values"] and
                     view["catalog_facts_by_group"] == {
                         group: child["catalog_facts"] for group, child in context["fact_contexts"].items()
                     }, "source_lean projection omits or changes supplied material")
            for document in view["organizer_record"]["documents"]:
                text = "".join(view["source_span_text"][span_id] for span_id in document["span_ids"])
                _require(len(text) == document["chars"] and pilot.base.sha256_text(text) == document["sha256"],
                         "source_lean document text cannot be reconstructed")
        pool = {**view.get("fact_shared_values", {}), **view.get("qualification_shared_values", {})}
        _require(all(reference in pool for reference in _refs(view)), "unresolved record-local compact reference")


def _verify_bytes(path: Path, expected: bytes) -> None:
    _require(path.is_file() and not path.is_symlink() and path.read_bytes() == expected,
             f"stored batch input differs: {path}")


def verify(run_dir: Path, source_archive: Path) -> dict[str, Any]:
    """Verify one archived batch pilot without creating files or model turns."""
    run = run_dir.resolve()
    archive = source_archive.resolve()
    _require(run.is_dir() and not run_dir.is_symlink(), "batch run missing or symlinked")
    _require(RUNS_ROOT.resolve() in run.parents, "batch run is outside repository runs")
    manifest = _verify_manifest(run)
    archive_proof = _verify_archive(archive, manifest)
    _require(_sha_file(pilot.DEV_INPUT) == manifest["input_sha256"], "organizer dev input differs")
    all_records = list(pilot.base._records(pilot.DEV_INPUT))
    start_index = manifest["start_index"]
    _require(len(all_records) == 200 and manifest["selected_ids"] == [
        record["id"] for record in all_records[start_index:start_index + len(manifest["selected_ids"])]
    ], "selected IDs are not the exact organizer dev window")
    source = {record["id"]: record for record in all_records}
    original_rubric = pilot.base.RUBRIC_PATH.read_text(encoding="utf-8")
    rubric = pilot.base.annotation_rubric(original_rubric)
    system = pilot._system(rubric, manifest["mode"])
    _require(pilot.base.sha256_text(original_rubric) == manifest["rubric_source_sha256"] and
             pilot.base.sha256_text(rubric) == manifest["rubric_sha256"] and
             pilot.base.sha256_text(system) == manifest["system_sha256"],
             "rubric or system prompt differs from run manifest")
    catalog = pilot.base.full_record_context.fact_context.catalog_facts.CatalogIndex.load()
    qualification_catalog = pilot.base.full_record_context.qualification_context.qualification_facts.CatalogReference.load()
    selected = manifest["selected_ids"]
    expected_batches = (len(selected) + manifest["batch_size"] - 1) // manifest["batch_size"]
    actual_dirs = sorted(path.name for path in run.iterdir() if path.is_dir())
    _require(all(name.startswith("batch-") for name in actual_dirs), "unexpected directory in batch run")
    expected_summary = {
        "selected_records": len(selected), "batches": 0, "calls_attempted": 0,
        "records_ok": 0, "records_content_error": 0, "safety_errors": 0,
        "model_input_bytes": 0, "independent_single_compact_bytes": 0,
        "qualification": "diagnostic_only_not_gold",
    }
    stopped_safety = False
    for index in range(expected_batches):
        batch_name = f"batch-{index:03d}"
        batch_dir = run / batch_name
        if not batch_dir.is_dir():
            _require(stopped_safety, f"missing prepared batch: {batch_name}")
            break
        _require(not batch_dir.is_symlink(), f"batch directory is symlinked: {batch_name}")
        _require(actual_dirs[index] == batch_name, "batch directory order or numbering differs")
        batch_ids = selected[index * manifest["batch_size"]:(index + 1) * manifest["batch_size"]]
        contexts = [pilot.base.full_record_context.build_full_record_context(
            source[record_id], catalog_index=catalog, qualification_catalog=qualification_catalog,
        ) for record_id in batch_ids]
        for record_id, context in zip(batch_ids, contexts):
            _require(not pilot.base.full_record_context.validate_full_record_context(
                source[record_id], context, catalog_index=catalog,
            ), f"rebuilt context invalid: {record_id}")
            _verify_bytes(batch_dir / f"{record_id}.full_context.json",
                          (pilot.base.canonical_json(context) + "\n").encode("utf-8"))
            expected_summary["independent_single_compact_bytes"] += len(
                pilot.base.canonical_json(pilot.base.compact_context(context)).encode("utf-8")
            )
        projection = pilot.batch_projection(contexts, manifest["mode"])
        _verify_projection(projection, contexts, manifest["mode"])
        prompt = pilot._prompt(projection, batch_ids)
        schema = pilot.base.canonical_json(pilot.batch_schema(len(batch_ids)))
        _verify_bytes(batch_dir / "system_prompt.txt", system.encode("utf-8"))
        _verify_bytes(batch_dir / "prompt.txt", prompt.encode("utf-8"))
        _verify_bytes(batch_dir / "output_schema.json", schema.encode("utf-8"))
        receipt_common = {
            "run_manifest_sha256": manifest["manifest_sha256"],
            "batch_index": index, "batch_ids": batch_ids,
            "requested_max_budget_usd": manifest["max_budget_usd"],
            "system_sha256": pilot.base.sha256_text(system),
            "prompt_sha256": pilot.base.sha256_text(prompt),
            "output_schema_sha256": pilot.base.sha256_text(schema),
            "context_sha256_by_id": {
                record_id: context["context_sha256"]
                for record_id, context in zip(batch_ids, contexts)
            },
        }
        expected_summary["model_input_bytes"] += len(prompt.encode("utf-8"))
        expected_summary["batches"] += 1
        prepared_files = {"system_prompt.txt", "prompt.txt", "output_schema.json"} | {
            f"{record_id}.full_context.json" for record_id in batch_ids
        }
        if not manifest["execute"]:
            _require({path.name for path in batch_dir.iterdir()} == prepared_files,
                     "dry batch has unexpected model or result artifacts")
            continue
        stdout_path, stderr_path = batch_dir / "stdout.bin", batch_dir / "stderr.bin"
        stdout = stdout_path.read_bytes() if stdout_path.is_file() and not stdout_path.is_symlink() else None
        stderr = stderr_path.read_bytes() if stderr_path.is_file() and not stderr_path.is_symlink() else None
        _require(stdout is not None and stderr is not None, "executed batch lacks raw CLI bytes")
        receipt = _json_file(batch_dir / "receipt.json")
        expected_summary["calls_attempted"] += 1
        status = receipt.get("status")
        row_files: set[str] = set()
        if status == "safety_error":
            if "returncode" in receipt:
                _require(receipt == {
                    **receipt_common,
                    "status": "safety_error", "returncode": receipt["returncode"],
                    "transport_error": receipt["transport_error"],
                    "stdout_sha256": _sha_bytes(stdout), "stderr_sha256": _sha_bytes(stderr),
                } and (receipt["returncode"] != 0 or receipt["transport_error"] is not None),
                         "safety returncode receipt differs")
            else:
                _require(set(receipt) == set(receipt_common) | {"status", "error", "stdout_sha256", "stderr_sha256"},
                         "safety audit receipt shape differs")
                try:
                    audited = pilot.base.audit_envelope(stdout)
                    if audited["total_cost_usd"] > manifest["max_budget_usd"]:
                        actual_error = "ValueError: reported cost exceeds cap"
                    else:
                        raise BatchVerificationError("safety receipt masks a valid CLI envelope")
                except ValueError as exc:
                    if isinstance(exc, BatchVerificationError):
                        raise
                    actual_error = f"{type(exc).__name__}: {exc}"
                _require(receipt == {
                    **receipt_common,
                    "status": "safety_error", "error": actual_error,
                    "stdout_sha256": _sha_bytes(stdout), "stderr_sha256": _sha_bytes(stderr),
                }, "safety audit error differs from raw stdout")
            expected_summary["safety_errors"] += 1
            stopped_safety = True
        else:
            envelope = pilot.base.audit_envelope(stdout)
            _require(envelope["total_cost_usd"] <= manifest["max_budget_usd"],
                     "executed batch exceeds frozen per-call budget")
            try:
                entries = pilot._batch_entries(envelope["structured_output"], batch_ids)
            except ValueError as exc:
                _require(status == "batch_content_error" and receipt == {
                    **receipt_common,
                    "status": "batch_content_error", "error": str(exc),
                    "stdout_sha256": _sha_bytes(stdout),
                    "reported_cost_usd": envelope["total_cost_usd"],
                }, "batch-content receipt differs from raw stdout")
                expected_summary["records_content_error"] += len(batch_ids)
            else:
                errors: dict[str, str] = {}
                rows: list[dict[str, Any]] = []
                for record_id, context, entry in zip(batch_ids, contexts, entries):
                    try:
                        ledger, normalized, normalization = pilot.base.validate_content(
                            {"structured_output": entry["annotation"]}, source[record_id], context,
                        )
                    except (ValueError, KeyError) as exc:
                        errors[record_id] = f"{type(exc).__name__}: {exc}"
                        continue
                    rows.append({
                        "id": record_id, "status": "provisional_unqualified",
                        "ledger": ledger, "structured_output": normalized,
                        "normalization": normalization,
                        "source_sha256": pilot.base.sha256_object(source[record_id]),
                        "full_context_sha256": context["context_sha256"],
                        "batch_index": index,
                    })
                for row in rows:
                    filename = f"{row['id']}.row.json"
                    _verify_bytes(batch_dir / filename, (pilot.base.canonical_json(row) + "\n").encode("utf-8"))
                    row_files.add(filename)
                _require(status == ("ok" if not errors else "partial_content_error") and receipt == {
                    **receipt_common,
                    "status": "ok" if not errors else "partial_content_error",
                    "errors": errors, "stdout_sha256": _sha_bytes(stdout),
                    "stderr_sha256": _sha_bytes(stderr),
                    "reported_cost_usd": envelope["total_cost_usd"],
                    "model_usage": envelope["modelUsage"],
                    "row_hashes": {row["id"]: pilot.base.sha256_object(row) for row in rows},
                }, "success or partial receipt differs from raw output and rows")
                expected_summary["records_ok"] += len(rows)
                expected_summary["records_content_error"] += len(errors)
        _require({path.name for path in batch_dir.iterdir()} == prepared_files | {
            "stdout.bin", "stderr.bin", "receipt.json"
        } | row_files, "batch artifact set differs")
        if stopped_safety:
            break
    _require(actual_dirs == [f"batch-{index:03d}" for index in range(expected_summary["batches"])],
             "unexpected or skipped batch directory")
    summary = _json_file(run / "run_summary.json")
    _require(summary == expected_summary, "run summary differs from independently replayed batches")
    _require({path.name for path in run.iterdir()} == {"run_manifest.json", "run_summary.json"} | set(actual_dirs),
             "run artifact set differs")
    if not manifest["execute"]:
        status = "input_prepared_only_model_quality_unverified"
    elif expected_summary["safety_errors"]:
        status = "failed_safety"
    elif expected_summary["records_content_error"] or expected_summary["records_ok"] != len(selected):
        status = "failed_content_or_incomplete"
    else:
        status = "executed_rows_structurally_verified_not_gold"
    return {
        "schema_version": SCHEMA, "status": status, "gold_qualification": False,
        "run_manifest_sha256": manifest["manifest_sha256"],
        "start_index": start_index,
        "archive": archive_proof, "selected_records": len(selected),
        "calls_attempted": expected_summary["calls_attempted"],
        "records_ok": expected_summary["records_ok"],
        "records_content_error": expected_summary["records_content_error"],
        "safety_errors": expected_summary["safety_errors"], "model_calls_by_verifier": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--source-archive", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = verify(args.run_dir, args.source_archive)
    except (BatchVerificationError, ValueError, KeyError, TypeError, OSError, pilot.base.full_record_context.FullRecordContextError) as exc:
        print(json.dumps({"schema_version": SCHEMA, "status": "verification_failed",
                          "reason": f"{type(exc).__name__}: {exc}", "model_calls_by_verifier": 0},
                         ensure_ascii=False, separators=(",", ":")))
        return 2
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0 if report["status"] in {
        "input_prepared_only_model_quality_unverified", "executed_rows_structurally_verified_not_gold"
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
