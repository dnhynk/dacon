"""CPU-only, lossless source admission preflight for independent gold.

This tool reads organizer input plus the organizer-supplied law and catalog
snapshots required by full_record_context.  It never reads labels, predictions,
production code, or model responses, and it never creates annotation staging.
The output is an immutable, fresh-only admission report, not a label source.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import pathlib
import time
from collections.abc import Mapping, Sequence
from typing import Any

from tools.independent_gold import full_record_context


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data_open" / "train_unlabeled.jsonl.gz"
SCHEMA_VERSION = "dacon.independent.source_context_preflight.v1"
EXPECTED_RECORDS = 20_000


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _json_line(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_shape(record: Mapping[str, Any]) -> tuple[int | None, int | None]:
    documents = record.get("docs")
    if not isinstance(documents, list):
        return None, None
    if any(
        not isinstance(document, Mapping)
        or not isinstance(document.get("text"), str)
        for document in documents
    ):
        return len(documents), None
    return len(documents), sum(len(document["text"]) for document in documents)


def _safe_error(exc: BaseException) -> dict[str, str]:
    # Exception messages from child builders may contain source fragments.
    # Preserve a fingerprint for diagnosis without copying those fragments.
    message = str(exc)
    return {
        "type": type(exc).__name__,
        "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
    }


def _admit_record(
    record: Any,
    line_number: int,
    *,
    catalog_index: Any,
    qualification_catalog: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "line_number": line_number,
        "status": "fail",
        "id": None,
        "record_sha256": None,
        "doc_count": None,
        "source_chars": None,
        "source_span_count": None,
        "rendered_chars": None,
        "context_sha256": None,
        "organizer_declared_incomplete": None,
        "has_dropped_docs": None,
    }
    if not isinstance(record, Mapping):
        row["error"] = {"type": "InputShapeError", "message": "record is not an object"}
        return row
    record_id = record.get("id")
    if isinstance(record_id, (str, int)) and not isinstance(record_id, bool):
        row["id"] = str(record_id)
    row["record_sha256"] = full_record_context.sha256_object(record)
    row["doc_count"], row["source_chars"] = _source_shape(record)
    completeness = record.get("input_completeness")
    if isinstance(completeness, Mapping):
        row["organizer_declared_incomplete"] = any(
            value is False for value in completeness.values()
        )
    dropped_counts = record.get("dropped_doc_counts")
    if isinstance(dropped_counts, Mapping):
        row["has_dropped_docs"] = any(
            type(value) is int and value > 0 for value in dropped_counts.values()
        )
    try:
        context = full_record_context.build_full_record_context(
            record,
            catalog_index=catalog_index,
            qualification_catalog=qualification_catalog,
        )
        validation = full_record_context.validate_full_record_context(
            record,
            context,
            catalog_index=catalog_index,
        )
        if validation:
            raise full_record_context.FullRecordContextError(
                f"explicit post-build validation failed: {validation[:10]}"
            )
        rendered_chars = len(full_record_context.canonical_json(context))
        bounds = context["bounds"]
        if rendered_chars != bounds["rendered_chars"]:
            raise full_record_context.FullRecordContextError(
                "rendered length differs from validated context bound"
            )
        if rendered_chars > full_record_context.MAX_RENDERED_CHARS:
            raise full_record_context.FullRecordContextError(
                f"rendered_chars={rendered_chars} exceeds hard limit"
            )
        row.update(
            status="pass",
            source_span_count=bounds["source_span_count"],
            rendered_chars=rendered_chars,
            context_sha256=context["context_sha256"],
        )
    except Exception as exc:
        row["error"] = _safe_error(exc)
    return row


def run_preflight(
    input_path: pathlib.Path,
    output_dir: pathlib.Path,
    *,
    expected_records: int = EXPECTED_RECORDS,
    progress_every: int = 100,
) -> dict[str, Any]:
    """Build and explicitly revalidate every record, then seal a fresh report.

    A failed or incomplete run is never admitted as a successful source.  No
    record is truncated, skipped after a per-record failure, or labeled.
    """

    input_path = pathlib.Path(input_path).resolve(strict=True)
    output_dir = pathlib.Path(output_dir).resolve(strict=False)
    if expected_records < 1:
        raise ValueError("expected_records must be positive")
    if progress_every < 1:
        raise ValueError("progress_every must be positive")
    if output_dir.exists():
        raise FileExistsError(f"fresh-only output already exists: {output_dir}")
    if not input_path.is_file():
        raise ValueError(f"input is not a file: {input_path}")
    if input_path.suffix.lower() != ".gz":
        raise ValueError("source input must be a .jsonl.gz file")

    started = time.monotonic()
    started_utc = _utc_now()
    input_sha256_before = _sha256_file(input_path)
    software_paths = {
        "context_preflight": pathlib.Path(__file__).resolve(),
        "full_record_context": pathlib.Path(full_record_context.__file__).resolve(),
        "fact_context": pathlib.Path(full_record_context.fact_context.__file__).resolve(),
        "qualification_context": pathlib.Path(
            full_record_context.qualification_context.__file__
        ).resolve(),
        "law_context": pathlib.Path(full_record_context.law_context.__file__).resolve(),
    }
    software_sha256_before = {
        name: _sha256_file(path) for name, path in software_paths.items()
    }
    catalog_index = full_record_context.fact_context.catalog_facts.CatalogIndex.load()
    qualification_catalog = (
        full_record_context.qualification_context.qualification_facts
        .CatalogReference.load()
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(exist_ok=False)
    rows_path = output_dir / "records.jsonl"
    rows_digest = hashlib.sha256()
    ids_digest = hashlib.sha256()
    seen_ids: set[str] = set()
    errors: list[dict[str, Any]] = []
    count = passed = failed = total_docs = total_source_chars = 0
    declared_incomplete = records_with_dropped_docs = 0
    max_source: dict[str, Any] = {"chars": -1, "id": None}
    max_rendered: dict[str, Any] = {"chars": -1, "id": None}
    near_rendered_limit = 0
    fatal_error: dict[str, str] | None = None

    try:
        with gzip.open(input_path, "rt", encoding="utf-8") as source, rows_path.open(
            "wb"
        ) as rows:
            for line_number, line in enumerate(source, 1):
                count += 1
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, UnicodeError) as exc:
                    row = {
                        "line_number": line_number,
                        "status": "fail",
                        "id": None,
                        "error": _safe_error(exc),
                    }
                else:
                    row = _admit_record(
                        record,
                        line_number,
                        catalog_index=catalog_index,
                        qualification_catalog=qualification_catalog,
                    )
                record_id = row.get("id")
                if isinstance(record_id, str) and record_id:
                    if record_id in seen_ids:
                        row["status"] = "fail"
                        row["error"] = {
                            "type": "DuplicateIdError",
                            "message": "organizer input ID appears more than once",
                        }
                    else:
                        seen_ids.add(record_id)
                    ids_digest.update((record_id + "\n").encode("utf-8"))
                else:
                    row["status"] = "fail"
                    row.setdefault(
                        "error",
                        {
                            "type": "InvalidIdError",
                            "message": "organizer input ID is empty or invalid",
                        },
                    )
                if isinstance(row.get("doc_count"), int):
                    total_docs += row["doc_count"]
                declared_incomplete += row.get("organizer_declared_incomplete") is True
                records_with_dropped_docs += row.get("has_dropped_docs") is True
                if isinstance(row.get("source_chars"), int):
                    source_chars = row["source_chars"]
                    total_source_chars += source_chars
                    if source_chars > max_source["chars"]:
                        max_source = {"chars": source_chars, "id": record_id}
                if isinstance(row.get("rendered_chars"), int):
                    rendered_chars = row["rendered_chars"]
                    near_rendered_limit += (
                        rendered_chars >= int(full_record_context.MAX_RENDERED_CHARS * 0.95)
                    )
                    if rendered_chars > max_rendered["chars"]:
                        max_rendered = {"chars": rendered_chars, "id": record_id}
                if row["status"] == "pass":
                    passed += 1
                else:
                    failed += 1
                    errors.append(
                        {
                            "line_number": line_number,
                            "id": record_id,
                            "error": row.get("error"),
                        }
                    )
                encoded = _json_line(row)
                rows.write(encoded)
                rows_digest.update(encoded)
                if count % progress_every == 0:
                    rows.flush()
                    print(
                        json.dumps(
                            {"processed": count, "passed": passed, "failed": failed},
                            sort_keys=True,
                        ),
                        flush=True,
                    )
    except (OSError, EOFError, UnicodeError, ValueError) as exc:
        fatal_error = _safe_error(exc)

    input_sha256_after = _sha256_file(input_path)
    software_sha256_after = {
        name: _sha256_file(path) for name, path in software_paths.items()
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "semantic_role": "organizer_source_admission_only_not_labels_or_model_staging",
        "status": "pass",
        "started_utc": started_utc,
        "finished_utc": _utc_now(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "input": {
            "path": str(input_path),
            "bytes": input_path.stat().st_size,
            "sha256_before": input_sha256_before,
            "sha256_after": input_sha256_after,
        },
        "context_schema_version": full_record_context.SCHEMA_VERSION,
        "software": {
            name: {
                "path": str(path),
                "sha256_before": software_sha256_before[name],
                "sha256_after": software_sha256_after[name],
            }
            for name, path in software_paths.items()
        },
        "bounds": {
            "max_source_chars": full_record_context.MAX_SOURCE_CHARS,
            "max_rendered_chars": full_record_context.MAX_RENDERED_CHARS,
        },
        "counts": {
            "expected_records": expected_records,
            "processed": count,
            "unique_ids": len(seen_ids),
            "passed": passed,
            "failed": failed,
            "total_docs": total_docs,
            "total_source_chars": total_source_chars,
            "organizer_declared_incomplete": declared_incomplete,
            "records_with_dropped_docs": records_with_dropped_docs,
            "rendered_at_least_95_percent_of_limit": near_rendered_limit,
        },
        "maxima": {"source_chars": max_source, "rendered_chars": max_rendered},
        "id_order_sha256": ids_digest.hexdigest(),
        "record_rows": {
            "path": str(rows_path),
            "sha256": rows_digest.hexdigest(),
        },
        "errors": errors,
        "fatal_error": fatal_error,
    }
    if (
        count != expected_records
        or passed != expected_records
        or failed
        or len(seen_ids) != expected_records
        or fatal_error
        or input_sha256_before != input_sha256_after
        or software_sha256_before != software_sha256_after
    ):
        report["status"] = "fail_closed"
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(report_path),
                "status": report["status"],
                "processed": count,
                "passed": passed,
                "failed": failed,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=pathlib.Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--expected-records", type=int, default=EXPECTED_RECORDS)
    parser.add_argument("--progress-every", type=int, default=100)
    args = parser.parse_args(argv)
    report = run_preflight(
        args.input,
        args.output_dir,
        expected_records=args.expected_records,
        progress_every=args.progress_every,
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
