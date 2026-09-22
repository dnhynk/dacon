"""Prepare source-complete, five-notice Claude inputs without calling a model.

This is input staging, not annotation or gold. Only organizer-supplied records,
law and catalog snapshots enter the prompts. A complete source preflight is a
mandatory admission gate; a partial or changed preflight is never accepted.
"""

from __future__ import annotations

import argparse
import itertools
import json
import pathlib
import sys
from collections.abc import Mapping, Sequence
from typing import Any

try:
    from tools.independent_gold import claude_batch_pilot as pilot
    from tools.independent_gold import claude_shard_supervisor as supervisor
except ModuleNotFoundError:  # Direct script invocation.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from tools.independent_gold import claude_batch_pilot as pilot  # type: ignore[no-redef]
    from tools.independent_gold import claude_shard_supervisor as supervisor  # type: ignore[no-redef]


base = pilot.base
ROOT = pathlib.Path(__file__).resolve().parents[2]
INPUT = ROOT / "data_open" / "train_unlabeled.jsonl.gz"
DEFAULT_PREFLIGHT = ROOT / "runs" / "self_label_20000_20260918" / "source_preflight_v2" / "report.json"
TOTAL_SHARDS = 200
RECORDS_PER_SHARD = 100
BATCH_SIZE = 5
EXPECTED_INCOMPLETE = 853
SCHEMA_VERSION = "dacon.independent.claude_unlabeled_batch_prepare.v1"


class PrepareError(ValueError):
    pass


def _json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PrepareError(f"expected JSON object: {path}")
    return value


def _new_bytes(path: pathlib.Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(value)


def _new_json(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    _new_bytes(path, (base.canonical_json(value) + "\n").encode("utf-8"))


def shard_ids(ids: Sequence[str]) -> list[list[str]]:
    if len(ids) != TOTAL_SHARDS * RECORDS_PER_SHARD or len(set(ids)) != len(ids):
        raise PrepareError("organizer ID count/uniqueness differs from 200 x 100")
    shards = [list(ids[index::TOTAL_SHARDS]) for index in range(TOTAL_SHARDS)]
    if any(len(shard) != RECORDS_PER_SHARD for shard in shards):
        raise PrepareError("200 x 100 strided shard mapping failed")
    return shards


def _incomplete(record: Mapping[str, Any]) -> tuple[bool, bool]:
    completeness = record.get("input_completeness")
    dropped = record.get("dropped_doc_counts")
    if not isinstance(completeness, Mapping) or not isinstance(dropped, Mapping):
        raise PrepareError("organizer completeness fields are missing")
    return (
        any(value is False for value in completeness.values()),
        any(type(value) is int and value > 0 for value in dropped.values()),
    )


def verify_preflight(report_path: pathlib.Path, ids: Sequence[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reconcile every admission row with the exact organizer record bytes."""
    admission = supervisor._validated_admission_report(report_path, ids)
    report = _json(report_path.resolve(strict=True))
    counts = report["counts"]
    if (counts.get("organizer_declared_incomplete") != EXPECTED_INCOMPLETE
            or counts.get("records_with_dropped_docs") != EXPECTED_INCOMPLETE):
        raise PrepareError("preflight incomplete counts differ from 853/853")
    rows_path = report_path.resolve(strict=True).parent / "records.jsonl"
    rows: list[dict[str, Any]] = []
    declared_count = dropped_count = 0
    with rows_path.open("rt", encoding="utf-8") as row_handle:
        for number, (record, row_line) in enumerate(
            itertools.zip_longest(base._records(INPUT), row_handle), 1
        ):
            if record is None or row_line is None:
                raise PrepareError("preflight row count differs from organizer input")
            row = json.loads(row_line)
            if not isinstance(row, dict):
                raise PrepareError(f"invalid preflight row {number}")
            declared, dropped = _incomplete(record)
            if (row.get("line_number") != number or row.get("id") != record["id"]
                    or row.get("status") != "pass"
                    or row.get("record_sha256") != base.sha256_object(record)
                    or row.get("organizer_declared_incomplete") is not declared
                    or row.get("has_dropped_docs") is not dropped
                    or not isinstance(row.get("context_sha256"), str)):
                raise PrepareError(f"preflight row/source mismatch at line {number}")
            declared_count += declared
            dropped_count += dropped
            rows.append(row)
    if (len(rows) != TOTAL_SHARDS * RECORDS_PER_SHARD
            or declared_count != EXPECTED_INCOMPLETE
            or dropped_count != EXPECTED_INCOMPLETE):
        raise PrepareError("organizer completeness totals differ from preflight")
    return admission, rows


def _fresh_root(path: pathlib.Path) -> pathlib.Path:
    resolved = path.resolve()
    if (ROOT / "runs").resolve() not in resolved.parents or resolved.exists():
        raise PrepareError("output must be a fresh directory below repository runs/")
    return resolved


def _prepare_batch(
    output: pathlib.Path,
    manifest: Mapping[str, Any],
    system: str,
    shard_index: int,
    batch_index: int,
    records: Sequence[Mapping[str, Any]],
    preflight_rows: Sequence[Mapping[str, Any]],
    catalog: Any,
    qualification_catalog: Any,
) -> dict[str, Any]:
    ids = [record["id"] for record in records]
    if len(ids) != BATCH_SIZE or len(set(ids)) != BATCH_SIZE:
        raise PrepareError("batch must contain five disjoint organizer records")
    contexts = []
    flags = {}
    for record, row in zip(records, preflight_rows):
        if row["id"] != record["id"]:
            raise PrepareError("batch/preflight order differs")
        context = base.full_record_context.build_full_record_context(
            record, catalog_index=catalog, qualification_catalog=qualification_catalog,
        )
        if context["context_sha256"] != row["context_sha256"]:
            raise PrepareError(f"preflight context hash drift: {record['id']}")
        if (context["source_completeness"]["input_completeness"] != record["input_completeness"]
                or context["source_completeness"]["dropped_doc_counts"] != record["dropped_doc_counts"]):
            raise PrepareError(f"organizer completeness flags changed: {record['id']}")
        contexts.append(context)
        flags[record["id"]] = {
            "input_completeness": record["input_completeness"],
            "dropped_doc_counts": record["dropped_doc_counts"],
            "organizer_declared_incomplete": row["organizer_declared_incomplete"],
            "has_dropped_docs": row["has_dropped_docs"],
        }
    projection = pilot.batch_projection(contexts, manifest["mode"])
    prompt = pilot._prompt(projection, ids)
    schema = base.canonical_json(pilot.batch_schema(BATCH_SIZE))
    batch_dir = output / f"shard-{shard_index:03d}-of-200" / f"batch-{batch_index:02d}"
    _new_bytes(batch_dir / "system_prompt.txt", system.encode("utf-8"))
    _new_bytes(batch_dir / "prompt.txt", prompt.encode("utf-8"))
    _new_bytes(batch_dir / "output_schema.json", schema.encode("utf-8"))
    for record_id, context in zip(ids, contexts):
        _new_json(batch_dir / f"{record_id}.full_context.json", context)
    batch_manifest: dict[str, Any] = {
        "schema_version": "dacon.independent.claude_unlabeled_batch_input.v1",
        "phase": "dry_input_only_no_model_call",
        "run_manifest_sha256": manifest["manifest_sha256"],
        "shard_index": shard_index,
        "batch_index": batch_index,
        "selected_ids": ids,
        "selected_ids_sha256": base.sha256_object(ids),
        "record_source_sha256_by_id": {r["id"]: row["record_sha256"] for r, row in zip(records, preflight_rows)},
        "full_context_sha256_by_id": {r["id"]: c["context_sha256"] for r, c in zip(records, contexts)},
        "completeness_by_id": flags,
        "projection_sha256": base.sha256_object(projection),
        "system_sha256": base.sha256_text(system),
        "prompt_sha256": base.sha256_text(prompt),
        "output_schema_sha256": base.sha256_text(schema),
        "prompt_bytes": len(prompt.encode("utf-8")),
        "model_calls": 0,
    }
    batch_manifest["manifest_sha256"] = base.sha256_object(batch_manifest)
    _new_json(batch_dir / "batch_manifest.json", batch_manifest)
    return batch_manifest


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    if not 0 <= args.first_shard_index <= args.last_shard_index < TOTAL_SHARDS:
        raise PrepareError("invalid inclusive shard range")
    if args.mode not in {"source_lean", "full_shared"}:
        raise PrepareError("invalid batch mode")
    output = _fresh_root(args.output_dir)
    ids, count = base.select_ids(INPUT, [], shard_index=0, shard_count=1, limit=None)
    if count != TOTAL_SHARDS * RECORDS_PER_SHARD:
        raise PrepareError("organizer input does not contain 20,000 records")
    shards = shard_ids(ids)
    admission, preflight_rows = verify_preflight(args.preflight_report, ids)
    input_sha = base.file_sha256(INPUT)
    source_bundle = base.source_bundle()
    pilot_sha = base.file_sha256(pathlib.Path(pilot.__file__))
    own_sha = base.file_sha256(pathlib.Path(__file__))
    original_rubric = base.RUBRIC_PATH.read_text(encoding="utf-8")
    rubric = base.annotation_rubric(original_rubric)
    system = pilot._system(rubric, args.mode)
    indices = list(range(args.first_shard_index, args.last_shard_index + 1))
    selected_ids = [record_id for index in indices for record_id in shards[index]]
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "phase": "unlabeled_batch_input_dry_only",
        "qualification": "input_prepared_only_model_quality_unverified",
        "input_path": str(INPUT.resolve()), "input_sha256": input_sha,
        "source_admission": admission,
        "organizer_records": TOTAL_SHARDS * RECORDS_PER_SHARD,
        "organizer_declared_incomplete": EXPECTED_INCOMPLETE,
        "records_with_dropped_docs": EXPECTED_INCOMPLETE,
        "total_shards": TOTAL_SHARDS, "records_per_shard": RECORDS_PER_SHARD,
        "batch_size": BATCH_SIZE, "mode": args.mode,
        "first_shard_index": args.first_shard_index,
        "last_shard_index": args.last_shard_index,
        "selected_ids_sha256": base.sha256_object(selected_ids),
        "shards": [
            {"index": index, "count": RECORDS_PER_SHARD,
             "selected_ids_sha256": base.sha256_object(shards[index])}
            for index in indices
        ],
        "source_bundle": source_bundle,
        "batch_pilot_source_sha256": pilot_sha,
        "preparer_source_sha256": own_sha,
        "rubric_source_sha256": base.sha256_text(original_rubric),
        "rubric_projection_sha256": base.sha256_text(rubric),
        "system_sha256": base.sha256_text(system),
        "output_schema_sha256": base.sha256_object(pilot.batch_schema(BATCH_SIZE)),
        "model_calls": 0,
    }
    manifest["manifest_sha256"] = base.sha256_object(manifest)
    catalog = base.full_record_context.fact_context.catalog_facts.CatalogIndex.load()
    qualification_catalog = base.full_record_context.qualification_context.qualification_facts.CatalogReference.load()
    output.mkdir(parents=True, exist_ok=False)
    _new_json(output / "run_manifest.json", manifest)
    pending: dict[int, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = {index: [] for index in indices}
    observed: dict[int, list[str]] = {index: [] for index in indices}
    batch_counts = {index: 0 for index in indices}
    incomplete_selected = 0
    dropped_selected = 0
    prompt_bytes = 0
    for ordinal, record in enumerate(base._records(INPUT)):
        index = ordinal % TOTAL_SHARDS
        if index not in pending:
            continue
        row = preflight_rows[ordinal]
        observed[index].append(record["id"])
        pending[index].append((record, row))
        incomplete_selected += row["organizer_declared_incomplete"] is True
        dropped_selected += row["has_dropped_docs"] is True
        if len(pending[index]) == BATCH_SIZE:
            entries = pending[index]
            batch_manifest = _prepare_batch(
                output, manifest, system, index, batch_counts[index],
                [entry[0] for entry in entries], [entry[1] for entry in entries],
                catalog, qualification_catalog,
            )
            prompt_bytes += batch_manifest["prompt_bytes"]
            batch_counts[index] += 1
            pending[index] = []
    if (any(pending.values()) or any(observed[index] != shards[index] for index in indices)
            or any(count != RECORDS_PER_SHARD // BATCH_SIZE for count in batch_counts.values())):
        raise PrepareError("prepared batch mapping/count differs from frozen 200 x 100 source")
    if (base.file_sha256(INPUT) != input_sha or base.source_bundle() != source_bundle
            or base.file_sha256(pathlib.Path(pilot.__file__)) != pilot_sha
            or base.file_sha256(pathlib.Path(__file__)) != own_sha
            or base.sha256_text(base.RUBRIC_PATH.read_text(encoding="utf-8"))
            != manifest["rubric_source_sha256"]):
        raise PrepareError("source/input/rubric changed during preparation")
    summary: dict[str, Any] = {
        "schema_version": "dacon.independent.claude_unlabeled_batch_prepare_summary.v1",
        "status": "input_prepared_only_model_quality_unverified",
        "run_manifest_sha256": manifest["manifest_sha256"],
        "selected_shards": len(indices),
        "selected_records": len(selected_ids),
        "prepared_batches": sum(batch_counts.values()),
        "selected_declared_incomplete": incomplete_selected,
        "selected_with_dropped_docs": dropped_selected,
        "prompt_bytes": prompt_bytes,
        "model_calls": 0,
    }
    _new_json(output / "prepare_summary.json", summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-report", type=pathlib.Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--first-shard-index", type=int, required=True)
    parser.add_argument("--last-shard-index", type=int, required=True)
    parser.add_argument("--mode", choices=("source_lean", "full_shared"), required=True)
    args = parser.parse_args(argv)
    try:
        print(base.canonical_json(prepare(args)))
    except (PrepareError, ValueError, OSError) as exc:
        print(f"batch input preparation stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
