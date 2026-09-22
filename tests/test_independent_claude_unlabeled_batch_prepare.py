"""Dry-only staging and admission invariants; no model or CLI calls."""

import json
import itertools
from argparse import Namespace
from unittest import mock

import pytest

from tools.independent_gold import claude_unlabeled_batch_prepare as prepare


def test_200_by_100_strided_mapping_and_disjoint_five_record_batches():
    ids = [f"R-{index:05d}" for index in range(20_000)]
    shards = prepare.shard_ids(ids)
    assert len(shards) == 200
    assert all(len(shard) == 100 for shard in shards)
    assert shards[0][:3] == [ids[0], ids[200], ids[400]]
    assert shards[199][-1] == ids[-1]
    assert {value for shard in shards for value in shard} == set(ids)
    assert all(len(set(shard[start:start + 5])) == 5
               for shard in shards for start in range(0, 100, 5))
    with pytest.raises(prepare.PrepareError, match="uniqueness"):
        prepare.shard_ids(ids[:-1] + [ids[0]])


def _fixture(tmp_path, monkeypatch):
    rows_path = tmp_path / "records.jsonl"
    report_path = tmp_path / "report.json"
    records = [
        {"id": "A", "input_completeness": {"notice": True}, "dropped_doc_counts": {"x": 0}},
        {"id": "B", "input_completeness": {"notice": False}, "dropped_doc_counts": {"x": 1}},
    ]
    rows = []
    for number, record in enumerate(records, 1):
        declared, dropped = prepare._incomplete(record)
        rows.append({"line_number": number, "id": record["id"], "status": "pass",
                     "record_sha256": prepare.base.sha256_object(record),
                     "context_sha256": f"context-{number}",
                     "organizer_declared_incomplete": declared,
                     "has_dropped_docs": dropped})
    rows_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    report_path.write_text(json.dumps({"counts": {"organizer_declared_incomplete": 1,
                                                  "records_with_dropped_docs": 1}}), encoding="utf-8")
    monkeypatch.setattr(prepare, "TOTAL_SHARDS", 1)
    monkeypatch.setattr(prepare, "RECORDS_PER_SHARD", 2)
    monkeypatch.setattr(prepare, "EXPECTED_INCOMPLETE", 1)
    monkeypatch.setattr(prepare.supervisor, "_validated_admission_report",
                        lambda *_: {"status": "pass"})
    monkeypatch.setattr(prepare.base, "_records", lambda *_: iter(records))
    return report_path, rows_path, records


def test_preflight_reconciles_every_source_hash_and_incomplete_flag(tmp_path, monkeypatch):
    report_path, rows_path, _ = _fixture(tmp_path, monkeypatch)
    admission, rows = prepare.verify_preflight(report_path, ["A", "B"])
    assert admission == {"status": "pass"}
    assert rows[1]["organizer_declared_incomplete"] is True
    tampered = json.loads(rows_path.read_text(encoding="utf-8").splitlines()[1])
    tampered["has_dropped_docs"] = False
    original = rows_path.read_text(encoding="utf-8").splitlines()
    rows_path.write_text(original[0] + "\n" + json.dumps(tampered) + "\n", encoding="utf-8")
    with pytest.raises(prepare.PrepareError, match="row/source mismatch"):
        prepare.verify_preflight(report_path, ["A", "B"])


def test_preflight_refuses_wrong_global_incomplete_count(tmp_path, monkeypatch):
    report_path, _, _ = _fixture(tmp_path, monkeypatch)
    report_path.write_text(json.dumps({"counts": {"organizer_declared_incomplete": 0,
                                                  "records_with_dropped_docs": 1}}), encoding="utf-8")
    with pytest.raises(prepare.PrepareError, match="incomplete counts"):
        prepare.verify_preflight(report_path, ["A", "B"])


def test_cli_is_dry_only_and_output_must_be_fresh(tmp_path):
    with pytest.raises(SystemExit) as exc:
        prepare.main(["--output-dir", str(tmp_path / "x"),
                      "--first-shard-index", "0", "--last-shard-index", "0",
                      "--mode", "source_lean", "--execute"])
    assert exc.value.code == 2
    with pytest.raises(prepare.PrepareError, match="fresh directory"):
        prepare._fresh_root(prepare.ROOT / "submission" / "batch")


def test_invalid_range_and_mode_fail_before_source_or_model_use(tmp_path):
    args = Namespace(first_shard_index=1, last_shard_index=0, mode="source_lean",
                     output_dir=tmp_path / "new", preflight_report=tmp_path / "report.json")
    with mock.patch.object(prepare.base, "select_ids", side_effect=AssertionError("should not read source")):
        with pytest.raises(prepare.PrepareError, match="range"):
            prepare.prepare(args)
    args.first_shard_index = args.last_shard_index = 0
    args.mode = "other"
    with pytest.raises(prepare.PrepareError, match="mode"):
        prepare.prepare(args)


@pytest.mark.parametrize("mode", ["source_lean", "full_shared"])
def test_five_real_organizer_inputs_are_saved_with_lineage_and_no_model_call(tmp_path, mode):
    records = list(itertools.islice(prepare.base._records(prepare.INPUT), 5))
    rows_path = prepare.DEFAULT_PREFLIGHT.parent / "records.jsonl"
    with rows_path.open("rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in itertools.islice(handle, 5)]
    catalog = (prepare.base.full_record_context.fact_context.catalog_facts
               .CatalogIndex.load())
    qualification_catalog = (prepare.base.full_record_context.qualification_context
                             .qualification_facts.CatalogReference.load())
    with mock.patch.object(prepare.base, "_invoke", side_effect=AssertionError("model call")):
        result = prepare._prepare_batch(
            tmp_path, {"mode": mode, "manifest_sha256": "frozen"}, "system", 0, 0,
            records, rows, catalog, qualification_catalog,
        )
    batch = tmp_path / "shard-000-of-200" / "batch-00"
    assert result["selected_ids"] == [record["id"] for record in records]
    assert result["model_calls"] == 0
    assert prepare.base.file_sha256(batch / "prompt.txt") == result["prompt_sha256"]
    saved = json.loads((batch / "batch_manifest.json").read_text(encoding="utf-8"))
    digest = saved.pop("manifest_sha256")
    assert digest == prepare.base.sha256_object(saved)
    for record in records:
        context = json.loads((batch / f"{record['id']}.full_context.json").read_text(encoding="utf-8"))
        assert context["source_completeness"]["input_completeness"] == record["input_completeness"]
        assert context["source_completeness"]["dropped_doc_counts"] == record["dropped_doc_counts"]
        assert context["context_sha256"] == result["full_context_sha256_by_id"][record["id"]]
