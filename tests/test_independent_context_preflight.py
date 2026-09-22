from __future__ import annotations

import gzip
import hashlib
import json

import pytest

from tools.independent_gold import context_preflight


def _record(record_id: str, text: str = "입찰공고 원문입니다.") -> dict:
    return {
        "id": record_id,
        "docs": [{"doc_id": "D0", "type": "공고문", "text": text}],
        "meta": {},
        "dropped_doc_counts": {},
        "input_completeness": {"완전관측": True, "무탈락": True},
        "assembly_policy_version": "synthetic-test",
        "anon_applied": True,
    }


def _input(path, lines: list[str]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


def _encoded(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def test_preflight_builds_and_revalidates_every_record_with_sealed_ledger(tmp_path):
    input_path = tmp_path / "source.jsonl.gz"
    output_dir = tmp_path / "fresh"
    _input(input_path, [_encoded(_record("A")), _encoded(_record("B"))])

    report = context_preflight.run_preflight(
        input_path, output_dir, expected_records=2, progress_every=1
    )
    rows_bytes = (output_dir / "records.jsonl").read_bytes()
    rows = [json.loads(line) for line in rows_bytes.splitlines()]

    assert report["status"] == "pass"
    assert report["counts"]["processed"] == 2
    assert report["counts"]["passed"] == 2
    assert report["counts"]["unique_ids"] == 2
    assert report["counts"]["total_docs"] == 2
    assert report["context_schema_version"] == "dacon.independent.full_record_context.v1"
    assert report["record_rows"]["sha256"] == hashlib.sha256(rows_bytes).hexdigest()
    assert report["input"]["sha256_before"] == report["input"]["sha256_after"]
    assert [row["id"] for row in rows] == ["A", "B"]
    assert all(row["status"] == "pass" and row["context_sha256"] for row in rows)
    assert all(row["rendered_chars"] <= 300_000 for row in rows)
    assert "입찰공고 원문입니다." not in rows_bytes.decode("utf-8")
    assert json.loads((output_dir / "report.json").read_text(encoding="utf-8")) == report


def test_preflight_fails_closed_without_truncating_oversized_source(tmp_path):
    input_path = tmp_path / "oversized.jsonl.gz"
    output_dir = tmp_path / "fresh"
    _input(input_path, [_encoded(_record("A", "가" * 100_001))])

    report = context_preflight.run_preflight(
        input_path, output_dir, expected_records=1
    )
    row = json.loads((output_dir / "records.jsonl").read_text(encoding="utf-8"))

    assert report["status"] == "fail_closed"
    assert report["counts"]["failed"] == 1
    assert row["source_chars"] == 100_001
    assert row["rendered_chars"] is None
    assert row["status"] == "fail"
    assert row["error"]["type"] == "FullRecordContextError"
    assert "가" not in (output_dir / "report.json").read_text(encoding="utf-8")


def test_preflight_keeps_later_rows_after_malformed_json_and_fails_closed(tmp_path):
    input_path = tmp_path / "malformed.jsonl.gz"
    output_dir = tmp_path / "fresh"
    _input(input_path, ["{broken", _encoded(_record("B"))])

    report = context_preflight.run_preflight(
        input_path, output_dir, expected_records=2
    )
    rows = [
        json.loads(line)
        for line in (output_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert report["status"] == "fail_closed"
    assert report["counts"]["processed"] == 2
    assert report["counts"]["passed"] == 1
    assert report["counts"]["failed"] == 1
    assert rows[0]["error"]["type"] == "JSONDecodeError"
    assert rows[1]["status"] == "pass"


def test_preflight_rejects_duplicate_id_and_existing_output(tmp_path):
    input_path = tmp_path / "dupe.jsonl.gz"
    output_dir = tmp_path / "fresh"
    _input(input_path, [_encoded(_record("A")), _encoded(_record("A"))])

    report = context_preflight.run_preflight(
        input_path, output_dir, expected_records=2
    )
    assert report["status"] == "fail_closed"
    assert report["counts"]["unique_ids"] == 1
    assert report["counts"]["failed"] == 1
    assert report["errors"][0]["error"]["type"] == "DuplicateIdError"
    with pytest.raises(FileExistsError, match="fresh-only"):
        context_preflight.run_preflight(input_path, output_dir, expected_records=2)
