"""No-model tests for the separate, read-only dev200 Gate 1 diagnostic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.independent_gold import claude_batch_dev_gate as gate


def _metric(tp: int = 200, fn: int = 0, u: int = 0) -> dict:
    return {
        "gold_positive": 200, "gold_negative": 0, "tp": tp, "tn": 0,
        "fp": 0, "fn": fn, "u": u, "u_gold_positive": u,
        "u_gold_negative": 0,
        "positive_f1": None if u else 2 * tp / (2 * tp + fn) if tp or fn else 0.0,
    }


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture
def case(tmp_path: Path, monkeypatch):
    runs_root = tmp_path / "runs"
    root = runs_root / "test_epoch"
    root.mkdir(parents=True)
    monkeypatch.setattr(gate, "RUNS_ROOT", runs_root)
    source_hashes = {name: gate._sha_file(path.resolve()) for name, path in gate.SOURCE_FILES.items()}
    plan = {
        "schema_version": gate.SUPERVISOR_SCHEMA,
        "phase": "blind_dev200_teacher_quality_diagnostic_only_not_gold",
        "qualification": "none_not_gold",
        "output_root": str(root),
        "window_starts": list(gate.WINDOW_STARTS),
        "source_hashes": source_hashes,
        "input_sha256": "1" * 64,
        "mode": "source_lean", "batch_size": 5,
        "model": "synthetic-model", "observed_model": "synthetic-observed",
        "rubric_sha256": "2" * 64, "rubric_source_sha256": "3" * 64,
        "system_sha256": "4" * 64,
        "source_bundle": {"bundle_sha256": "5" * 64, "files": []},
        "pilot_source_sha256": source_hashes["pilot"],
        "max_budget_usd": 1.0, "timeout_seconds": 30.0,
        "cli": {"executable_sha256": "6" * 64, "version_output": "synthetic"},
        "windows": [
            {"start_index": start,
             "selected_ids": [f"SYN-{i:03d}" for i in range(start, start + 20)]}
            for start in gate.WINDOW_STARTS
        ],
    }
    plan["plan_sha256"] = gate._sha_object(plan)
    _write_json(root / "plan.json", plan)
    report = {
        "schema_version": gate.scorer.SCHEMA,
        "status": "metrics_only_pending_quality_policy_not_gold",
        "qualified_for_gold_generation": False,
        "quality_threshold_applied": False,
        "records": 200, "cells_expected": 4800, "cells_verified": 4800,
        "macro_positive_f1": 1.0,
        "per_item": {item: _metric() for item in gate.ITEMS},
        "error_cells": [], "unresolved_cells": [],
        "frozen_teacher_tuple_sha256": gate._sha_object(gate._frozen_tuple_from_plan(plan)),
        "organizer_input_sha256": plan["input_sha256"],
        "official_dev_labels_sha256": "7" * 64,
        "verification_code_sha256": {
            "scorer_sha256": source_hashes["scorer"],
            "verifier_sha256": source_hashes["verifier"],
        },
        "model_calls_by_scorer": 0,
        "windows": [],
    }
    for start in gate.WINDOW_STARTS:
        tag = f"window-{start:03d}-{start + 19:03d}"
        run = root / "runs" / tag
        archive = root / "archives" / tag
        run.mkdir(parents=True)
        archive.mkdir(parents=True)
        (run / "artifact.txt").write_text("synthetic verified run", encoding="utf-8")
        (archive / "artifact.txt").write_text("synthetic source archive", encoding="utf-8")
        archive_proof = {"synthetic_archive": True, "start": start}
        report["windows"].append({
            "start_index": start, "run_dir": str(run), "source_archive": str(archive),
            "run_manifest_sha256": "8" * 64, "archive": archive_proof,
            "records_verified": 20,
        })
        completed = {
            "schema_version": gate.SUPERVISOR_SCHEMA,
            "status": "executed_rows_structurally_verified_not_gold",
            "plan_sha256": plan["plan_sha256"], "start_index": start,
            "run_dir": str(run), "source_archive": str(archive),
            "run_tree_sha256": gate._tree_hash(run),
            "archive_tree_sha256": gate._tree_hash(archive),
            "run_manifest_sha256": "8" * 64,
            "verifier_proof": {"archive": archive_proof},
            "gold_qualification": False,
        }
        _write_json(root / "completed" / f"{tag}.json", completed)

    def persist() -> None:
        path = root / "score_report.json"
        _write_json(path, report)
        _write_json(root / "score_receipt.json", {
            "schema_version": gate.SUPERVISOR_SCHEMA,
            "plan_sha256": plan["plan_sha256"],
            "report_sha256": gate._sha_file(path),
            "status": report["status"], "gold_qualification": False,
        })

    persist()
    calls: list[list[tuple[Path, Path]]] = []

    def replay(pairs):
        calls.append(list(pairs))
        return json.loads(json.dumps(report))

    monkeypatch.setattr(gate.scorer, "score_windows", replay)
    def evidence_summary(*_):
        return {
            "positive_cells": sum(metric["tp"] + metric["fp"]
                                  for metric in report["per_item"].values()),
            "nonabsence_positive_cells": sum(metric["tp"] + metric["fp"]
                                             for item, metric in report["per_item"].items()
                                             if item not in gate.ABSENCE_ITEMS),
            "nonabsence_positive_structural_exact_source_status":
            "verified_by_full_raw_replay_and_positive_span_audit",
            "legal_semantic_truth_certified": False,
        }

    monkeypatch.setattr(gate, "_evidence_summary", evidence_summary)
    return {"root": root, "plan": plan, "report": report, "persist": persist,
            "calls": calls}


def test_matching_raw_replay_and_metrics_pass_only_minimum_gate(case):
    before = {path.relative_to(case["root"]): path.read_bytes()
              for path in case["root"].rglob("*") if path.is_file()}
    result = gate.evaluate(case["root"] / "score_report.json")
    assert result["candidate_gate1_minimum_pass"] is True
    assert result["qualified_for_gold_generation"] is False
    assert result["gold_or_semantic_truth_certified"] is False
    assert result["provenance"]["raw_windows_replayed"] == 10
    assert len(case["calls"]) == 1 and len(case["calls"][0]) == 10
    assert result["threshold_source"]["section"] == "Promotion gates / 1. Candidate annotator"
    assert {path.relative_to(case["root"]): path.read_bytes()
            for path in case["root"].rglob("*") if path.is_file()} == before


def test_cli_success_is_machine_readable_and_does_not_promote_gold(case, capsys):
    code = gate.main(["--score-report", str(case["root"] / "score_report.json")])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["candidate_gate1_minimum_pass"] is True
    assert output["qualified_for_gold_generation"] is False
    assert output["gold_or_semantic_truth_certified"] is False


def test_single_item_below_070_fails_even_when_macro_exceeds_090(case):
    report = case["report"]
    report["per_item"]["v1"] = _metric(tp=100, fn=100)
    report["macro_positive_f1"] = (23 + report["per_item"]["v1"]["positive_f1"]) / 24
    report["error_cells"] = [
        {"id": f"SYN-{index:03d}", "item": "v1", "official": 1,
         "teacher": 0, "kind": "fn"}
        for index in range(100)
    ]
    case["persist"]()
    result = gate.evaluate(case["root"] / "score_report.json")
    assert result["metrics"]["macro_positive_f1"] > .90
    assert result["candidate_gate1_minimum_pass"] is False
    assert result["below_item_threshold"] == ["v1"]


def test_macro_below_090_fails_even_when_every_item_exceeds_070(case):
    report = case["report"]
    report["per_item"] = {item: _metric(tp=134, fn=66) for item in gate.ITEMS}
    report["macro_positive_f1"] = report["per_item"]["v1"]["positive_f1"]
    report["error_cells"] = [
        {"id": f"SYN-{index:03d}", "item": item, "official": 1,
         "teacher": 0, "kind": "fn"}
        for item in gate.ITEMS for index in range(66)
    ]
    case["persist"]()
    result = gate.evaluate(case["root"] / "score_report.json")
    assert result["candidate_gate1_minimum_pass"] is False
    assert result["below_item_threshold"] == []
    assert result["metrics"]["minimum_item_positive_f1"] > .70
    assert "macro_positive_F1_below_0.90_or_unavailable" in result["failure_reasons"]


def test_one_U_fails_without_mapping_to_zero(case):
    report = case["report"]
    report["per_item"]["v1"] = _metric(tp=199, u=1)
    report["macro_positive_f1"] = None
    report["status"] = "failed_unresolved_cells"
    report["unresolved_cells"] = [{"id": "SYN-000", "item": "v1", "official": 1}]
    case["persist"]()
    result = gate.evaluate(case["root"] / "score_report.json")
    assert result["candidate_gate1_minimum_pass"] is False
    assert result["metrics"]["unresolved_cells"] == 1
    assert result["metrics"]["false_negatives"] == 0


def test_scorer_source_hash_drift_blocks_before_replay(case):
    case["report"]["verification_code_sha256"]["scorer_sha256"] = "0" * 64
    case["persist"]()
    with pytest.raises(gate.DevGateError, match="scorer/verifier source hashes"):
        gate.evaluate(case["root"] / "score_report.json")
    assert case["calls"] == []


def test_report_replay_disagreement_is_not_a_gate_result(case, monkeypatch):
    original = json.loads(json.dumps(case["report"]))
    case["report"]["per_item"]["v1"] = _metric(tp=100, fn=100)
    case["persist"]()
    monkeypatch.setattr(gate.scorer, "score_windows", lambda _: original)
    with pytest.raises(gate.DevGateError, match="fresh raw replay"):
        gate.evaluate(case["root"] / "score_report.json")


def test_score_receipt_hash_drift_blocks_before_replay(case):
    receipt_path = case["root"] / "score_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["report_sha256"] = "0" * 64
    _write_json(receipt_path, receipt)
    with pytest.raises(gate.DevGateError, match="score receipt"):
        gate.evaluate(case["root"] / "score_report.json")
    assert case["calls"] == []


def test_raw_tree_drift_blocks_before_replay(case):
    run = case["root"] / "runs" / "window-000-019"
    (run / "artifact.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(gate.DevGateError, match="bytes differ"):
        gate.evaluate(case["root"] / "score_report.json")
    assert case["calls"] == []


def test_evidence_audit_rejects_missing_positive_span(tmp_path):
    root = tmp_path / "evidence"
    plan = {"windows": []}
    for start in gate.WINDOW_STARTS:
        ids = [f"SYN-{index:03d}" for index in range(start, start + 20)]
        plan["windows"].append({"selected_ids": ids})
        run = root / "runs" / f"window-{start:03d}-{start + 19:03d}"
        _write_json(run / "run_manifest.json", {"selected_ids": ids, "batch_size": 5})
        for local_index, record_id in enumerate(ids):
            cells = [
                {"item": item, "label": 1 if record_id == "SYN-000" and item == "v1" else 0,
                 "premise_span_ids": ["S1"],
                 "positive_evidence_span_id": "S1" if record_id == "SYN-000" and item == "v1" else None}
                for item in gate.ITEMS
            ]
            row = {"ledger": {"source_spans": [{"span_id": "S1", "quote": "source witness"}],
                              "cells": cells}}
            _write_json(run / f"batch-{local_index // 5:03d}" / f"{record_id}.row.json", row)
    checked = gate._evidence_summary(root, plan)
    assert checked["nonabsence_positive_cells"] == 1
    assert checked["legal_semantic_truth_certified"] is False
    first = root / "runs" / "window-000-019" / "batch-000" / "SYN-000.row.json"
    row = json.loads(first.read_text(encoding="utf-8"))
    row["ledger"]["cells"][0]["positive_evidence_span_id"] = None
    _write_json(first, row)
    with pytest.raises(gate.DevGateError, match="lacks source span"):
        gate._evidence_summary(root, plan)


def test_cli_integrity_failure_is_machine_readable_and_not_gold(tmp_path, capsys):
    code = gate.main(["--score-report", str(tmp_path / "score_report.json")])
    output = json.loads(capsys.readouterr().out)
    assert code == 2
    assert output["status"] == "gate1_integrity_unverified_not_gold"
    assert output["candidate_gate1_minimum_pass"] is False
    assert output["qualified_for_gold_generation"] is False
