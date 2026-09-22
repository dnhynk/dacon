"""Source-only human-review queue for organizer-declared incomplete records.

No labels, predictions, model output, or production runtime are read.  This
artifact identifies source-closure risk; it is not an answer key or a model
annotation staging area.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import pathlib
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data_open" / "train_unlabeled.jsonl.gz"
DEFAULT_ITEMS = ROOT / "data_open" / "data" / "항목표.json"
DEFAULT_PREFLIGHT = ROOT / "runs" / "self_label_20000_20260918" / "source_preflight_v2"
SCHEMA = "dacon.independent.incomplete_source_review_queue.v1"
EXPECTED_INPUT = 20_000
EXPECTED_INCOMPLETE = 853


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _sha_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _jsonl(value: Mapping[str, Any]) -> str:
    return _canonical(value) + "\n"


def _load_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), f"{path.name} must be a JSON object")
    return value


def _item_routing(item_table: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = item_table.get("항목")
    _require(isinstance(raw, dict), "organizer item table lacks 항목")
    expected = {f"v{i}" for i in range(1, 25)}
    _require(set(raw) == expected, "organizer item table is not exactly v1..v24")
    declared_absence = item_table.get("부재탐지항목")
    _require(isinstance(declared_absence, list), "organizer item table lacks 부재탐지항목")
    _require(
        set(declared_absence)
        == {item for item, details in raw.items() if details.get("부재탐지") is True},
        "organizer absence-item flags disagree",
    )
    return {
        item: {
            "item_name": raw[item]["항목명"],
            "source_sensitivity": (
                "direct_absence_detection" if raw[item]["부재탐지"] else "potential_unseen_or_corrective_evidence"
            ),
            "source_closure_status": "human_source_review_or_U_candidate",
            "automatic_zero_allowed": False,
        }
        for item in sorted(expected, key=lambda value: int(value[1:]))
    }


def _queue_record(
    record: Mapping[str, Any],
    preflight: Mapping[str, Any],
    item_routes: Mapping[str, Any],
) -> dict[str, Any] | None:
    record_id = record.get("id")
    _require(isinstance(record_id, str) and record_id, "source ID invalid")
    _require(preflight.get("status") == "pass", f"{record_id}: preflight did not pass")
    _require(preflight.get("id") == record_id, f"{record_id}: preflight ID mismatch")
    _require(
        preflight.get("record_sha256") == _sha_text(_canonical(record)),
        f"{record_id}: source record differs from admitted preflight",
    )
    docs = record.get("docs")
    _require(isinstance(docs, list) and docs, f"{record_id}: docs invalid")
    _require(len(docs) == preflight.get("doc_count"), f"{record_id}: document count drift")
    source_chars = sum(len(doc["text"]) for doc in docs)
    _require(source_chars == preflight.get("source_chars"), f"{record_id}: source chars drift")
    dropped = record.get("dropped_doc_counts")
    completeness = record.get("input_completeness")
    _require(isinstance(dropped, dict), f"{record_id}: dropped counts invalid")
    _require(isinstance(completeness, dict) and completeness, f"{record_id}: completeness invalid")
    _require(
        all(isinstance(key, str) and type(value) is int and value > 0 for key, value in dropped.items()),
        f"{record_id}: dropped counts invalid",
    )
    _require(
        all(isinstance(key, str) and type(value) is bool for key, value in completeness.items()),
        f"{record_id}: completeness flags invalid",
    )
    incomplete = any(value is False for value in completeness.values())
    has_drops = bool(dropped)
    _require(
        incomplete == preflight.get("organizer_declared_incomplete")
        and has_drops == preflight.get("has_dropped_docs"),
        f"{record_id}: organizer/preflight incompleteness disagreement",
    )
    if not (incomplete or has_drops):
        return None
    _require(has_drops, f"{record_id}: incomplete without an explicit dropped count")
    documents = []
    for index, doc in enumerate(docs):
        _require(isinstance(doc, dict), f"{record_id}: document invalid")
        _require(
            isinstance(doc.get("doc_id"), str)
            and isinstance(doc.get("type"), str)
            and isinstance(doc.get("text"), str),
            f"{record_id}: document fields invalid",
        )
        documents.append(
            {
                "doc_index": index,
                "doc_id": doc["doc_id"],
                "doc_type": doc["type"],
                "chars": len(doc["text"]),
                "source_doc_sha256": _sha_text(doc["text"]),
            }
        )
    missing_total = sum(dropped.values())
    false_flags = sorted(key for key, value in completeness.items() if value is False)
    return {
        "schema_version": SCHEMA,
        "id": record_id,
        "line_number": preflight["line_number"],
        "record_sha256": preflight["record_sha256"],
        "context_sha256": preflight["context_sha256"],
        "supplied_documents": documents,
        "supplied_doc_count": len(docs),
        "supplied_source_chars": source_chars,
        "input_completeness": dict(completeness),
        "false_completeness_flags": false_flags,
        "dropped_doc_counts": dict(dropped),
        "missing_document_total": missing_total,
        "missing_document_type_count": len(dropped),
        "missing_reason": "organizer_input_length_exclusion_as_documented_in_data_open_README",
        "missing_contents_known": False,
        "review_route": "human_source_review_or_U_candidate",
        "automatic_zero_allowed": False,
        "binary_answer_generated": False,
        "item_routes": item_routes,
    }


def _risk_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    # Severity of source loss, never probability of a violation.
    return (
        -row["missing_document_total"],
        -row["missing_document_type_count"],
        row["supplied_doc_count"],
        row["supplied_source_chars"],
        row["id"],
    )


def build_queue(
    *,
    input_path: pathlib.Path,
    item_table_path: pathlib.Path,
    preflight_dir: pathlib.Path,
    output_dir: pathlib.Path,
    expected_input: int = EXPECTED_INPUT,
    expected_incomplete: int = EXPECTED_INCOMPLETE,
) -> dict[str, Any]:
    """Cross-check organizer input against a sealed preflight, then write fresh output."""
    paths = [input_path, item_table_path, preflight_dir / "report.json", preflight_dir / "records.jsonl"]
    paths = [pathlib.Path(path).resolve(strict=True) for path in paths]
    input_path, item_table_path, preflight_report_path, preflight_rows_path = paths
    output_dir = pathlib.Path(output_dir).resolve(strict=False)
    _require(not output_dir.exists(), f"fresh-only output exists: {output_dir}")
    _require(expected_input > 0 and expected_incomplete > 0, "expected counts must be positive")
    input_sha_before = _sha_file(input_path)
    report = _load_json(preflight_report_path)
    _require(report.get("status") == "pass", "preflight report is not pass")
    counts = report.get("counts")
    _require(isinstance(counts, dict), "preflight counts missing")
    _require(
        counts.get("processed") == expected_input
        and counts.get("passed") == expected_input
        and counts.get("failed") == 0
        and counts.get("organizer_declared_incomplete") == expected_incomplete
        and counts.get("records_with_dropped_docs") == expected_incomplete,
        "preflight counts disagree with required coverage",
    )
    _require(report["input"]["sha256_before"] == input_sha_before, "preflight source SHA mismatch")
    _require(report["input"]["sha256_after"] == input_sha_before, "preflight source drift")
    preflight_rows_sha = _sha_file(preflight_rows_path)
    _require(
        report["record_rows"]["sha256"] == preflight_rows_sha,
        "preflight ledger SHA mismatch",
    )
    item_table = _load_json(item_table_path)
    item_routes = _item_routing(item_table)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    input_ids_digest = hashlib.sha256()
    source_count = 0
    with gzip.open(input_path, "rt", encoding="utf-8") as source, preflight_rows_path.open(
        "rt", encoding="utf-8"
    ) as preflight_rows:
        for line_number, source_line in enumerate(source, 1):
            preflight_line = preflight_rows.readline()
            _require(bool(preflight_line), "preflight ledger shorter than input")
            record = json.loads(source_line)
            preflight = json.loads(preflight_line)
            _require(isinstance(record, dict) and isinstance(preflight, dict), "row object invalid")
            _require(preflight.get("line_number") == line_number, "preflight line drift")
            record_id = record.get("id")
            _require(record_id not in seen, f"duplicate source ID {record_id}")
            seen.add(record_id)
            input_ids_digest.update((record_id + "\n").encode("utf-8"))
            row = _queue_record(record, preflight, item_routes)
            if row is not None:
                rows.append(row)
            source_count += 1
        _require(not preflight_rows.readline(), "preflight ledger longer than input")
    _require(source_count == expected_input, "source record count mismatch")
    _require(len(seen) == expected_input, "unique source ID count mismatch")
    _require(len(rows) == expected_incomplete, "incomplete queue coverage mismatch")
    _require(input_ids_digest.hexdigest() == report["id_order_sha256"], "source order drift")
    _require(_sha_file(input_path) == input_sha_before, "source file mutated during queue build")
    _require(_sha_file(preflight_rows_path) == preflight_rows_sha, "preflight ledger mutated")

    top30 = sorted(rows, key=_risk_key)[:30]
    drop_totals: Counter[str] = Counter()
    drop_count_histogram: Counter[int] = Counter()
    for row in rows:
        drop_totals.update(row["dropped_doc_counts"])
        drop_count_histogram[row["missing_document_total"]] += 1
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(exist_ok=False)
    queue_path = output_dir / "queue.jsonl"
    queue_path.write_text("".join(_jsonl(row) for row in rows), encoding="utf-8")
    top_path = output_dir / "top30.json"
    top_path.write_text(
        json.dumps(
            [
                {
                    "rank": rank,
                    "id": row["id"],
                    "record_sha256": row["record_sha256"],
                    "missing_document_total": row["missing_document_total"],
                    "dropped_doc_counts": row["dropped_doc_counts"],
                    "supplied_doc_count": row["supplied_doc_count"],
                    "supplied_source_chars": row["supplied_source_chars"],
                    "review_route": row["review_route"],
                    "automatic_zero_allowed": False,
                }
                for rank, row in enumerate(top30, 1)
            ],
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    audit = {
        "schema_version": SCHEMA,
        "semantic_role": "organizer_source_loss_audit_queue_only_not_answers",
        "status": "pass",
        "counts": {
            "source_records": source_count,
            "queued_records": len(rows),
            "item_route_cells": len(rows) * 24,
            "top_summary_records": len(top30),
        },
        "source": {
            "organizer_input_sha256": input_sha_before,
            "organizer_item_table_sha256": _sha_file(item_table_path),
            "preflight_report_sha256": _sha_file(preflight_report_path),
            "preflight_rows_sha256": preflight_rows_sha,
            "id_order_sha256": input_ids_digest.hexdigest(),
        },
        "artifacts": {
            "queue_jsonl_sha256": _sha_file(queue_path),
            "top30_json_sha256": _sha_file(top_path),
        },
        "dropped_document_type_totals": dict(sorted(drop_totals.items())),
        "missing_document_total_histogram": {
            str(key): value for key, value in sorted(drop_count_histogram.items())
        },
        "ranking_rule": [
            "missing_document_total descending",
            "missing_document_type_count descending",
            "supplied_doc_count ascending",
            "supplied_source_chars ascending",
            "id ascending",
        ],
        "ranking_means": "source_loss_severity_only_not_violation_probability",
        "item_route_basis": {
            "direct_absence_detection": "Organizer item table marks this as absence-detection. A missing attachment may contain the allegedly absent condition; supplied-text absence cannot close either binary answer.",
            "potential_unseen_or_corrective_evidence": "An unavailable attachment may contain operative, conflicting, exception, or scope evidence. Applicability and binary answer cannot be closed from supplied text alone.",
        },
        "item_catalog": {
            item: {
                "item_name": route["item_name"],
                "source_sensitivity": route["source_sensitivity"],
            }
            for item, route in item_routes.items()
        },
        "automatic_zero_allowed": False,
        "binary_answers_generated": False,
        "uncertainty": "Dropped document contents are absent from the organizer input and cannot be reconstructed from this queue. Review must be source-first; unresolved cells stay U candidates, not 0/1. The rank is not a legal-risk prediction.",
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return audit


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=pathlib.Path, default=DEFAULT_INPUT)
    parser.add_argument("--item-table", type=pathlib.Path, default=DEFAULT_ITEMS)
    parser.add_argument("--preflight-dir", type=pathlib.Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--expected-input", type=int, default=EXPECTED_INPUT)
    parser.add_argument("--expected-incomplete", type=int, default=EXPECTED_INCOMPLETE)
    args = parser.parse_args(argv)
    result = build_queue(
        input_path=args.input,
        item_table_path=args.item_table,
        preflight_dir=args.preflight_dir,
        output_dir=args.output_dir,
        expected_input=args.expected_input,
        expected_incomplete=args.expected_incomplete,
    )
    print(json.dumps({"status": result["status"], "counts": result["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
