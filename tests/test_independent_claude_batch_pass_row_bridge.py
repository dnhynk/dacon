"""No-model synthetic raw/stage/archive bridge checks for one 100-record shard."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from tools.independent_gold import claude_batch_pass_row_bridge as bridge
from tools.independent_gold import full_record_output, provisional_release


def _record(number: int) -> dict:
    return {
        "id": f"SYN-BATCH-{number:03d}",
        "meta": {"title": "synthetic notice"},
        "input_completeness": {"notice": True, "attachments": number != 0},
        "dropped_doc_counts": {"attachment": 1} if number == 0 else {},
        "docs": [{"doc_id": "D0", "type": "공고문", "text": "기관 제한 문구와 예외 확인을 위한 원문입니다.\n"}],
    }


def _ledger(record: dict, *, unresolved: bool = False) -> dict:
    span = "SRC-D0000-S000000"
    decisions = {}
    for item in provisional_release.ITEMS:
        label = "U" if unresolved and item == "v1" else 0
        decisions[item] = {
            "label": label, "confidence": "H",
            "rationale": "제공 원문에서 항목 구성요건과 예외를 검토함",
            "premise_span_ids": [span], "exception_analysis": None,
            "completeness": "unknown" if label == "U" else "sufficient",
            "material_missing_information": "누락된 첨부 조항의 본문" if label == "U" else None,
            "positive_evidence_span_id": None,
        }
    return full_record_output.canonical_ledger_projection(
        {"source_span_ids": [span], "decisions": decisions}, record,
    )


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((bridge.pilot.base.canonical_json(value) + "\n").encode("utf-8"))


def _seal(value: dict, hash_key: str) -> dict:
    value[hash_key] = bridge.pilot.base.sha256_object(value)
    return value


@pytest.fixture
def case(tmp_path: Path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(bridge, "RUNS", runs)
    stage, execution, archive = runs / "stage", runs / "execution", runs / "dry_archive"
    stage.mkdir()
    execution.mkdir()
    archive.mkdir()
    _write(archive / "manifest.json", {"synthetic": True})
    records = [_record(index) for index in range(100)]
    source = tmp_path / "unlabeled.jsonl.gz"
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    monkeypatch.setattr(bridge.verifier, "INPUT", source)
    input_sha = bridge._sha_file(source)
    stage_manifest = _seal({
        "schema_version": "synthetic.stage", "first_shard_index": 0,
        "last_shard_index": 0, "input_sha256": input_sha, "mode": "source_lean",
    }, "manifest_sha256")
    _write(stage / "run_manifest.json", stage_manifest)
    execution_manifest = _seal({
        "schema_version": "synthetic.execution",
        "staged_run_manifest_sha256": stage_manifest["manifest_sha256"],
        "staged_root": str(stage), "shard_index": 0, "organizer_input_sha256": input_sha,
        "mode": "source_lean", "model": bridge.pilot.base.REQUESTED_MODEL,
        "observed_model": bridge.pilot.base.OBSERVED_MODEL,
    }, "manifest_sha256")
    _write(execution / "run_manifest.json", execution_manifest)
    source_archive_manifest = _seal({"run_manifest_sha256": execution_manifest["manifest_sha256"]},
                                    "archive_manifest_sha256")
    _write(execution / "source_archive" / "manifest.json", source_archive_manifest)
    preflight = [{"id": record["id"], "organizer_declared_incomplete": index == 0,
                  "has_dropped_docs": index == 0} for index, record in enumerate(records)]
    for batch_index in range(20):
        group = records[batch_index * 5:(batch_index + 1) * 5]
        ids = [record["id"] for record in group]
        staged = _seal({
            "selected_ids": ids, "run_manifest_sha256": stage_manifest["manifest_sha256"],
            "prompt_sha256": hashlib.sha256(f"prompt {batch_index}".encode()).hexdigest(),
            "completeness_by_id": {record["id"]: {
                "input_completeness": record["input_completeness"],
                "dropped_doc_counts": record["dropped_doc_counts"],
                "organizer_declared_incomplete": record["id"] == records[0]["id"],
                "has_dropped_docs": record["id"] == records[0]["id"],
            } for record in group},
        }, "manifest_sha256")
        stage_batch = stage / "shard-000-of-200" / f"batch-{batch_index:02d}"
        _write(stage_batch / "batch_manifest.json", staged)
        raw_batch = execution / "batches" / f"batch-{batch_index:02d}"
        rows = {}
        for record in group:
            row = {"id": record["id"], "status": "provisional_unqualified",
                   "batch_index": batch_index, "source_sha256": bridge.pilot.base.sha256_object(record),
                   "ledger": _ledger(record, unresolved=record["id"] == records[0]["id"])}
            _write(raw_batch / f"{record['id']}.row.json", row)
            rows[record["id"]] = bridge.pilot.base.sha256_object(row)
        receipt = _seal({
            "status": "ok", "errors": {}, "batch_ids": ids,
            "run_manifest_sha256": execution_manifest["manifest_sha256"],
            "staged_batch_manifest_sha256": staged["manifest_sha256"],
            "prompt_sha256": staged["prompt_sha256"],
            "source_archive_manifest_sha256": source_archive_manifest["archive_manifest_sha256"],
            "row_hashes": rows,
            "model_usage": {bridge.pilot.base.OBSERVED_MODEL: {
                "canonicalModel": bridge.pilot.base.OBSERVED_MODEL, "provider": "firstParty"}},
        }, "receipt_sha256")
        _write(raw_batch / "receipt.json", receipt)
    report = {
        "schema_version": bridge.verifier.SCHEMA,
        "status": "executed_100_provisional_rows_with_U_not_gold",
        "structural_full_shard_verified": True,
        "provisional_pass_rows_eligible": True,
        "gold_qualification": False, "model_calls_by_verifier": 0,
        "planned_records": 100, "planned_batches": 20,
        "attempted_batches": 20, "completed_receipts": 20,
        "verified_prefix_batches": 20, "verified_prefix_records": 100,
        "structurally_replayed_rows_including_nonprefix": 100,
        "nonprefix_replayed_rows": 0, "unverified_planned_records": 0,
        "unverified_planned_cells": 0, "u_cells_in_nonprefix_rows": 0,
        "u_cells_in_verified_prefix": 1,
        "u_cells_in_structurally_replayed_rows": 1,
        "unreceipted_attempt_batch": None, "shard_index": 0, "mode": "source_lean",
        "staged_run_manifest_sha256": stage_manifest["manifest_sha256"],
        "execution_manifest_sha256": execution_manifest["manifest_sha256"],
        "source_archive_manifest_sha256": source_archive_manifest["archive_manifest_sha256"],
        "staged_source_archive": {
            "path": str(archive), "tree_sha256": bridge.verifier._tree_hash(archive),
            "manifest_sha256": bridge._sha_file(archive / "manifest.json"),
        },
    }
    monkeypatch.setattr(bridge.verifier, "verify_execution", lambda *args: report)
    monkeypatch.setattr(bridge.verifier, "_load_source_and_preflight",
                        lambda shard: ([], records, preflight, {}))
    return {"stage": stage, "execution": execution, "archive": archive,
            "source": source, "records": records, "report": report, "runs": runs}


def _bridge(case, output: Path | None = None):
    return bridge.bridge(staged_root=case["stage"], execution_root=case["execution"],
                         staged_source_archive=case["archive"],
                         output_dir=output or case["runs"] / "pass_export")


def test_verified_100_rows_preserve_U_and_incomplete_route(case) -> None:
    result = _bridge(case)
    assert result["records"] == 100 and result["u_cells"] == 1
    assert result["gold_qualification"] is False and result["model_calls_by_bridge"] == 0
    output = Path(result["output_dir"])
    rows = [json.loads(line) for line in (output / "pass_rows.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 100
    assert rows[0]["ledger"]["cells"][0]["label"] == "U"
    assert rows[0]["schema_version"] == provisional_release.PASS_ROW_SCHEMA
    provenance = json.loads((output / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["record_proofs"][0]["organizer_declared_incomplete"] is True
    assert provenance["record_proofs"][0]["release_route"] == "all_cells_abstain_source_incomplete"
    cells = provisional_release._cell_release(case["records"][0], rows[0], None, item_index=0)
    assert cells["tier"] == "abstain" and cells["provisional_label"] is None
    assert cells["gold_label"] is None and cells["eligible_as_gold"] is False


def test_complete_U_zero_status_is_also_provisional_only(case) -> None:
    record = case["records"][0]
    raw = case["execution"] / "batches" / "batch-00" / f"{record['id']}.row.json"
    row = json.loads(raw.read_text(encoding="utf-8"))
    row["ledger"] = _ledger(record, unresolved=False)
    _write(raw, row)
    receipt_path = case["execution"] / "batches" / "batch-00" / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["row_hashes"][record["id"]] = bridge.pilot.base.sha256_object(row)
    receipt["receipt_sha256"] = bridge.pilot.base.sha256_object({
        key: value for key, value in receipt.items() if key != "receipt_sha256"
    })
    _write(receipt_path, receipt)
    case["report"]["status"] = "executed_100_provisional_rows_structurally_verified_not_gold"
    case["report"]["u_cells_in_verified_prefix"] = 0
    case["report"]["u_cells_in_structurally_replayed_rows"] = 0
    result = _bridge(case)
    assert result["u_cells"] == 0 and result["gold_qualification"] is False
    first = json.loads((Path(result["output_dir"]) / "pass_rows.jsonl").read_text(
        encoding="utf-8").splitlines()[0])
    assert first["ledger"]["cells"][0]["label"] == 0
    cell = provisional_release._cell_release(record, first, None, item_index=0)
    assert cell["tier"] == "abstain" and cell["provisional_label"] is None


@pytest.mark.parametrize("fault", ("partial", "safety", "missing_archive", "U_mismatch"))
def test_bridge_refuses_nonpass_or_inconsistent_evidence(case, fault) -> None:
    if fault == "partial":
        case["report"]["verified_prefix_records"] = 95
    elif fault == "safety":
        case["report"]["status"] = "stopped_safety_with_verified_prefix_not_gold"
    elif fault == "missing_archive":
        case["report"]["staged_source_archive"] = None
    else:
        case["report"]["status"] = "executed_100_provisional_rows_structurally_verified_not_gold"
        case["report"]["u_cells_in_verified_prefix"] = 0
        case["report"]["u_cells_in_structurally_replayed_rows"] = 0
    output = case["runs"] / f"rejected-{fault}"
    with pytest.raises(bridge.BatchPassRowBridgeError):
        _bridge(case, output)
    assert not output.exists()


def test_bridge_refuses_raw_row_hash_tamper(case) -> None:
    raw = case["execution"] / "batches" / "batch-00" / "SYN-BATCH-000.row.json"
    row = json.loads(raw.read_text(encoding="utf-8"))
    row["ledger"]["cells"][0]["label"] = 0
    _write(raw, row)
    with pytest.raises(bridge.BatchPassRowBridgeError, match="row source, receipt"):
        _bridge(case)
    assert not (case["runs"] / "pass_export").exists()


def test_bridge_refuses_overwrite_or_source_overlap(case) -> None:
    with pytest.raises(bridge.BatchPassRowBridgeError, match="overlap"):
        _bridge(case, case["execution"] / "new_export")
    _bridge(case)
    with pytest.raises(bridge.BatchPassRowBridgeError, match="fresh"):
        _bridge(case)
