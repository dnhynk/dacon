"""CPU-only checks for organizer-incomplete source routing."""

from __future__ import annotations

import gzip
import json
import pathlib
import tempfile
import unittest

from tools.independent_gold import incomplete_review_queue as queue


class IncompleteReviewQueueTests(unittest.TestCase):
    def _fixture(self, root: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
        source_path = root / "organizer.jsonl.gz"
        item_path = root / "items.json"
        preflight_dir = root / "preflight"
        preflight_dir.mkdir()
        records = [
            {
                "id": "R1",
                "docs": [{"doc_id": "D0", "type": "notice", "text": "one"}],
                "meta": {},
                "input_completeness": {"notice_exists": True, "drop_free": False},
                "dropped_doc_counts": {"task": 2},
            },
            {
                "id": "R2",
                "docs": [{"doc_id": "D0", "type": "notice", "text": "two"}],
                "meta": {},
                "input_completeness": {"notice_exists": True, "drop_free": True},
                "dropped_doc_counts": {},
            },
        ]
        with gzip.open(source_path, "wt", encoding="utf-8") as handle:
            for record in records:
                handle.write(queue._jsonl(record))
        item_table = {
            "부재탐지항목": ["v10", "v11", "v16", "v18", "v20"],
            "항목": {
                f"v{i}": {
                    "항목명": f"item {i}",
                    "부재탐지": i in {10, 11, 16, 18, 20},
                }
                for i in range(1, 25)
            },
        }
        item_path.write_text(json.dumps(item_table), encoding="utf-8")
        ledger = []
        for index, record in enumerate(records, 1):
            ledger.append(
                {
                    "line_number": index,
                    "status": "pass",
                    "id": record["id"],
                    "record_sha256": queue._sha_text(queue._canonical(record)),
                    "doc_count": 1,
                    "source_chars": 3,
                    "context_sha256": "a" * 64,
                    "organizer_declared_incomplete": index == 1,
                    "has_dropped_docs": index == 1,
                }
            )
        ledger_path = preflight_dir / "records.jsonl"
        ledger_path.write_text("".join(queue._jsonl(row) for row in ledger), encoding="utf-8")
        source_sha = queue._sha_file(source_path)
        id_digest = queue._sha_text("R1\nR2\n")
        report = {
            "status": "pass",
            "counts": {
                "processed": 2,
                "passed": 2,
                "failed": 0,
                "organizer_declared_incomplete": 1,
                "records_with_dropped_docs": 1,
            },
            "input": {"sha256_before": source_sha, "sha256_after": source_sha},
            "record_rows": {"sha256": queue._sha_file(ledger_path)},
            "id_order_sha256": id_digest,
        }
        (preflight_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        return source_path, item_path, preflight_dir

    def test_exact_incomplete_coverage_and_all_item_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            source, items, preflight = self._fixture(root)
            output = root / "out"
            result = queue.build_queue(
                input_path=source,
                item_table_path=items,
                preflight_dir=preflight,
                output_dir=output,
                expected_input=2,
                expected_incomplete=1,
            )
            self.assertEqual(result["counts"]["item_route_cells"], 24)
            rows = [json.loads(line) for line in (output / "queue.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["id"] for row in rows], ["R1"])
            self.assertEqual(rows[0]["missing_document_total"], 2)
            self.assertEqual(len(rows[0]["item_routes"]), 24)
            self.assertFalse(rows[0]["automatic_zero_allowed"])
            self.assertFalse(rows[0]["binary_answer_generated"])
            self.assertNotIn("label", rows[0])
            self.assertEqual(rows[0]["supplied_documents"][0]["source_doc_sha256"], queue._sha_text("one"))

    def test_preflight_source_sha_mismatch_fails_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            source, items, preflight = self._fixture(root)
            report_path = preflight / "report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["input"]["sha256_before"] = "0" * 64
            report_path.write_text(json.dumps(report), encoding="utf-8")
            output = root / "out"
            with self.assertRaisesRegex(ValueError, "preflight source SHA mismatch"):
                queue.build_queue(
                    input_path=source,
                    item_table_path=items,
                    preflight_dir=preflight,
                    output_dir=output,
                    expected_input=2,
                    expected_incomplete=1,
                )
            self.assertFalse(output.exists())

    def test_ranking_means_only_source_loss(self) -> None:
        rows = [
            {"id": "B", "missing_document_total": 1, "missing_document_type_count": 1, "supplied_doc_count": 1, "supplied_source_chars": 10},
            {"id": "A", "missing_document_total": 9, "missing_document_type_count": 1, "supplied_doc_count": 1, "supplied_source_chars": 10},
        ]
        self.assertEqual([row["id"] for row in sorted(rows, key=queue._risk_key)], ["A", "B"])


if __name__ == "__main__":
    unittest.main()
