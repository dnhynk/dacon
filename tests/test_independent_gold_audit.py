from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from tools.independent_gold import build_gold as GOLD
from tools.independent_gold import gold_audit as AUDIT


HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64


def _source() -> dict:
    return {
        "id": "R1",
        "docs": [{"doc_id": "D0", "type": "공고문", "text": "원문 근거"}],
        "meta": {},
        "input_completeness": {"notice": True},
        "dropped_doc_counts": {},
    }


def _rows(source: dict) -> dict[str, dict]:
    decisions = {}
    for item in GOLD.ITEMS:
        decisions[item] = {
            "label": 0,
            "confidence": "high",
            "evidence": "",
            "route": "independent_consensus",
            "vote_provenance": {
                "candidate": {
                    "label": 0,
                    "confidence": "high",
                    "reviewer": {"reviewer_id": "candidate-model"},
                },
                "verifier": {
                    "label": 0,
                    "confidence": "high",
                    "reviewer": {"reviewer_id": "verifier-model"},
                },
            },
        }
    return {
        source["id"]: {
            "id": source["id"],
            "source_sha256": GOLD.review_ledger.source_descriptor(source)["source_sha256"],
            "snapshot_event_sha256": HEX_C,
            "decisions": decisions,
        }
    }


def _roster(tmp_path: pathlib.Path) -> pathlib.Path:
    attestation = tmp_path / "auditor-attestation.txt"
    attestation.write_text("independent human auditor", encoding="utf-8")
    roster = [
        {
            "reviewer_id": "auditor-1",
            "identity_provider": "local-attested-test",
            "identity_subject": "human-1",
            "independence_key": "human-independent-1",
            "protocol_version": "source-first-blind-v1",
            "attestation_path": str(attestation),
            "attestation_sha256": hashlib.sha256(attestation.read_bytes()).hexdigest(),
        }
    ]
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(roster, ensure_ascii=False), encoding="utf-8")
    return path


def _plan(
    tmp_path: pathlib.Path,
    source_path: pathlib.Path,
    export_path: pathlib.Path,
    rows: dict[str, dict],
    records: dict[str, dict],
) -> tuple[pathlib.Path, pathlib.Path, list[dict]]:
    selection, populations = GOLD._expected_audit_selection(
        rows, records, seed="seed", per_stratum=30
    )
    selection_path = tmp_path / "selection.jsonl"
    AUDIT._atomic_jsonl(selection_path, selection)
    policy_path = tmp_path / "policy.json"
    AUDIT.freeze_policy(
        organizer_input=source_path,
        output=policy_path,
        selection_seed="seed",
        frozen_at_utc="2026-09-18T00:00:00+00:00",
    )
    plan = {
        "schema_version": GOLD.AUDIT_PLAN_SCHEMA,
        "plan_id": HEX_A,
        "frozen_at_utc": "2026-09-18T01:00:00+00:00",
        "organizer_input_sha256": GOLD.file_sha256(source_path),
        "review_export_manifest_sha256": GOLD.file_sha256(export_path),
        "review_ledger_head_sha256": HEX_B,
        "policy_path": str(policy_path),
        "policy_sha256": GOLD.file_sha256(policy_path),
        "population_records": 1,
        "population_cells": 24,
        "population_strata": dict(sorted(populations.items())),
        "selection_method": "mandatory_risk_plus_sha256_rank_per_item_category_completeness_v2",
        "selection_seed": "seed",
        "per_stratum": 30,
        "selection_path": str(selection_path),
        "selection_sha256": GOLD.file_sha256(selection_path),
        "selected_cells": len(selection),
        "zero_defect_required": True,
    }
    plan["plan_sha256"] = GOLD.sha256_object(plan)
    plan_path = tmp_path / "plan.json"
    AUDIT._atomic_json(plan_path, plan)
    return plan_path, selection_path, selection


def test_policy_is_predeclared_hashed_and_never_overwritten(tmp_path):
    source = tmp_path / "organizer.gz"
    source.write_bytes(b"organizer")
    output = tmp_path / "policy.json"

    policy = AUDIT.freeze_policy(
        organizer_input=source,
        output=output,
        selection_seed="fixed-before-annotation",
        frozen_at_utc="2026-09-18T00:00:00+00:00",
    )

    assert policy["per_stratum"] == 30
    assert policy["zero_defect_required"] is True
    assert policy["policy_sha256"] == GOLD.sha256_object(
        {key: value for key, value in policy.items() if key != "policy_sha256"}
    )
    assert AUDIT._load_policy(output) == policy
    with pytest.raises(AUDIT.AuditWorkflowError, match="refusing to overwrite"):
        AUDIT.freeze_policy(organizer_input=source, output=output)


def test_blind_packets_do_not_expose_labels_routes_or_votes():
    source = _source()
    records = {source["id"]: source}
    rows = _rows(source)
    selection, _ = GOLD._expected_audit_selection(
        rows, records, seed="seed", per_stratum=30
    )

    packets = AUDIT._make_blind_packets(selection, records)

    assert len(packets) == 24
    forbidden = {"label", "route", "decision", "votes", "rationale", "stratum"}
    assert all(not (forbidden & set(packet)) for packet in packets)
    assert all(packet["annotation_answer_visible"] is False for packet in packets)
    assert all(
        packet["packet_sha256"]
        == GOLD.sha256_object(
            {key: value for key, value in packet.items() if key != "packet_sha256"}
        )
        for packet in packets
    )


def _prepared_pass_case(tmp_path, monkeypatch):
    source = _source()
    records = {source["id"]: source}
    rows = _rows(source)
    source_path = tmp_path / "organizer.gz"
    source_path.write_bytes(b"organizer")
    export_path = tmp_path / "review-export.json"
    export_path.write_text("{}", encoding="utf-8")
    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text("sealed", encoding="utf-8")
    plan_path, _, selection = _plan(
        tmp_path, source_path, export_path, rows, records
    )
    packets_path = tmp_path / "blind-packets.jsonl"
    AUDIT._atomic_jsonl(packets_path, AUDIT._make_blind_packets(selection, records))
    session_path = tmp_path / "session.json"
    AUDIT.start_session(
        plan_path=plan_path,
        blind_packets_path=packets_path,
        roster_path=_roster(tmp_path),
        output=session_path,
        started_utc="2026-09-18T02:00:00+00:00",
    )
    judgments = []
    for selected in selection:
        judgments.append(
            {
                "schema_version": AUDIT.BLIND_JUDGMENT_SCHEMA,
                "id": selected["id"],
                "item": selected["item"],
                "source_sha256": selected["source_sha256"],
                "snapshot_event_sha256": selected["snapshot_event_sha256"],
                "auditor_id": "auditor-1",
                "reviewed_utc": "2026-09-18T03:00:00+00:00",
                "blind_to_annotation_votes": True,
                "proposed_label": 0,
                "confidence": "high",
                "proposed_evidence": "",
                "premise_quotes": ["원문 근거"],
                "rationale": "독립적으로 0으로 판정",
                "rule_application_confirmed": True,
                "source_completeness_confirmed": True,
            }
        )
    judgments_path = tmp_path / "judgments.jsonl"
    AUDIT._atomic_jsonl(judgments_path, judgments)

    monkeypatch.setattr(
        GOLD,
        "_load_organizer_records",
        lambda *_args, **_kwargs: (["R1"], records),
    )
    monkeypatch.setattr(
        GOLD,
        "_verify_review_export",
        lambda **_kwargs: (
            {"ledger_head_sha256": HEX_B},
            rows,
            {"first_event_utc": "2026-09-18T01:30:00+00:00"},
        ),
    )
    return {
        "source_path": source_path,
        "export_path": export_path,
        "ledger_path": ledger_path,
        "plan_path": plan_path,
        "packets_path": packets_path,
        "session_path": session_path,
        "judgments_path": judgments_path,
        "records": records,
        "rows": rows,
        "selection": selection,
    }


def _finalize_case(tmp_path, case):
    report_path = tmp_path / "report.json"
    report = AUDIT.finalize_report(
        organizer_input=case["source_path"],
        ledger_path=case["ledger_path"],
        review_export_manifest=case["export_path"],
        plan_path=case["plan_path"],
        session_path=case["session_path"],
        judgments_path=case["judgments_path"],
        results_output=tmp_path / "results.jsonl",
        report_output=report_path,
        contract=GOLD.BuildContract(1, 24, False),
        completed_utc="2026-09-18T04:00:00+00:00",
    )
    return report, report_path


def _builder_replay(case, report_path):
    return GOLD._validate_audit(
        plan_path=case["plan_path"],
        report_path=report_path,
        rows=case["rows"],
        organizer_input=case["source_path"],
        review_export_manifest_path=case["export_path"],
        review_export_manifest={"ledger_head_sha256": HEX_B},
        reviewer_ids={
            "candidate": {"candidate-model"},
            "verifier": {"verifier-model"},
            "adjudicator": set(),
        },
        records=case["records"],
        review_started_utc="2026-09-18T01:30:00+00:00",
        contract=GOLD.BuildContract(1, 24, False),
    )


def test_finalize_emits_pass_only_for_complete_matching_blind_commitments(
    tmp_path, monkeypatch
):
    case = _prepared_pass_case(tmp_path, monkeypatch)
    report, report_path = _finalize_case(tmp_path, case)

    assert report["status"] == "PASS"
    assert report["audited_cells"] == 24
    assert report["defect_cells"] == 0
    assert report["unresolved_cells"] == 0
    assert report["audit_session_path"] == str(case["session_path"].resolve())
    assert report["audit_session_file_sha256"] == GOLD.file_sha256(case["session_path"])
    assert report["audit_session_sha256"] == GOLD._load_json(case["session_path"], "test")["session_sha256"]
    assert report["blind_packets_path"] == str(case["packets_path"].resolve())
    assert report["blind_packets_sha256"] == GOLD.file_sha256(case["packets_path"])
    assert report["judgments_path"] == str(case["judgments_path"].resolve())
    assert report["judgments_sha256"] == GOLD.file_sha256(case["judgments_path"])
    receipt = _builder_replay(case, report_path)
    assert receipt["defects"] == 0
    assert receipt["audited_cells"] == 24


@pytest.mark.parametrize("artifact", ["session_path", "packets_path", "judgments_path"])
@pytest.mark.parametrize("damage", ["mutate", "delete"])
def test_builder_replay_refuses_post_report_chain_damage(
    tmp_path, monkeypatch, artifact, damage
):
    case = _prepared_pass_case(tmp_path, monkeypatch)
    _, report_path = _finalize_case(tmp_path, case)
    assert _builder_replay(case, report_path)["defects"] == 0
    target = case[artifact]
    if damage == "mutate":
        target.write_bytes(target.read_bytes() + b"\n")
    else:
        target.unlink()
    with pytest.raises(GOLD.GoldBuildError):
        _builder_replay(case, report_path)


@pytest.mark.parametrize("damage", ["mutate", "delete"])
def test_finalize_refuses_changed_or_missing_session_bound_packets(
    tmp_path, monkeypatch, damage
):
    case = _prepared_pass_case(tmp_path, monkeypatch)
    packets_path = case["packets_path"]
    if damage == "mutate":
        packets_path.write_bytes(packets_path.read_bytes() + b"\n")
        expected_message = "blind packet file hash mismatch"
    else:
        packets_path.unlink()
        expected_message = "blind packet file is missing"
    with pytest.raises(AUDIT.AuditWorkflowError, match=expected_message):
        _finalize_case(tmp_path, case)
    assert not (tmp_path / "results.jsonl").exists()
    assert not (tmp_path / "report.json").exists()


@pytest.mark.parametrize("field", ["source_record", "snapshot_event_sha256"])
def test_finalize_rejects_semantically_changed_packets_even_when_rehashed(
    tmp_path, monkeypatch, field
):
    case = _prepared_pass_case(tmp_path, monkeypatch)
    packets = GOLD._read_jsonl(case["packets_path"], "test packets")
    if field == "source_record":
        packets[0][field]["docs"][0]["text"] = "바뀐 원문"
    else:
        packets[0][field] = HEX_A
    packets[0]["packet_sha256"] = GOLD.sha256_object(
        {key: value for key, value in packets[0].items() if key != "packet_sha256"}
    )
    AUDIT._atomic_jsonl(case["packets_path"], packets)
    session = GOLD._load_json(case["session_path"], "test session")
    session["blind_packets_sha256"] = GOLD.file_sha256(case["packets_path"])
    session["session_sha256"] = GOLD.sha256_object(
        {key: value for key, value in session.items() if key != "session_sha256"}
    )
    AUDIT._atomic_json(case["session_path"], session)
    with pytest.raises(AUDIT.AuditWorkflowError, match="differ from frozen selection/source/snapshot"):
        _finalize_case(tmp_path, case)


def test_finalize_marks_mismatch_as_defect_not_pass(tmp_path, monkeypatch):
    source = _source()
    records = {source["id"]: source}
    rows = _rows(source)
    source_path = tmp_path / "organizer.gz"
    source_path.write_bytes(b"organizer")
    export_path = tmp_path / "review-export.json"
    export_path.write_text("{}", encoding="utf-8")
    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text("sealed", encoding="utf-8")
    plan_path, _, selection = _plan(tmp_path, source_path, export_path, rows, records)
    packets_path = tmp_path / "packets.jsonl"
    AUDIT._atomic_jsonl(packets_path, AUDIT._make_blind_packets(selection, records))
    session_path = tmp_path / "session.json"
    AUDIT.start_session(
        plan_path=plan_path,
        blind_packets_path=packets_path,
        roster_path=_roster(tmp_path),
        output=session_path,
        started_utc="2026-09-18T02:00:00+00:00",
    )
    judgments = []
    positive_key = next(
        (row["id"], row["item"])
        for row in selection
        if row["item"] not in GOLD.ABSENCE_ITEMS
    )
    for index, selected in enumerate(selection):
        positive = (selected["id"], selected["item"]) == positive_key
        judgments.append(
            {
                "schema_version": AUDIT.BLIND_JUDGMENT_SCHEMA,
                "id": selected["id"], "item": selected["item"],
                "source_sha256": selected["source_sha256"],
                "snapshot_event_sha256": selected["snapshot_event_sha256"],
                "auditor_id": "auditor-1",
                "reviewed_utc": "2026-09-18T03:00:00+00:00",
                "blind_to_annotation_votes": True,
                "proposed_label": 1 if positive else 0,
                "confidence": "high",
                "proposed_evidence": "원문 근거" if positive else "",
                "premise_quotes": ["원문 근거"],
                "rationale": "독립 판정",
                "rule_application_confirmed": True,
                "source_completeness_confirmed": True,
            }
        )
    judgments_path = tmp_path / "judgments.jsonl"
    AUDIT._atomic_jsonl(judgments_path, judgments)
    monkeypatch.setattr(GOLD, "_load_organizer_records", lambda *_a, **_k: (["R1"], records))
    monkeypatch.setattr(
        GOLD, "_verify_review_export",
        lambda **_k: ({"ledger_head_sha256": HEX_B}, rows, {"first_event_utc": "2026-09-18T01:30:00+00:00"}),
    )

    report = AUDIT.finalize_report(
        organizer_input=source_path, ledger_path=ledger_path,
        review_export_manifest=export_path, plan_path=plan_path,
        session_path=session_path, judgments_path=judgments_path,
        results_output=tmp_path / "results.jsonl",
        report_output=tmp_path / "report.json",
        contract=GOLD.BuildContract(1, 24, False),
        completed_utc="2026-09-18T04:00:00+00:00",
    )

    assert report["status"] == "FAIL"
    assert report["defect_cells"] == 1
    assert report["unresolved_cells"] == 0
