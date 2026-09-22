from __future__ import annotations

import copy
import gzip
import hashlib
import json
from collections import Counter

import pytest

from tools.independent_gold import build_gold as gold
from tools.independent_gold import gold_audit as audit
from tools.independent_gold import notice_families


SEED = "a" * 64


def _record(index: int, *, complete: bool = True) -> dict:
    return {
        "id": f"R{index:04d}",
        "docs": [{"doc_id": "D0", "type": "공고문", "text": f"organizer notice {index}"}],
        "meta": {},
        "input_completeness": {"notice": complete},
        "dropped_doc_counts": {} if complete else {"missing attachment": 1},
    }


def _row(record: dict, *, adjudicated: bool = False) -> dict:
    decisions = {}
    for item in gold.ITEMS:
        decisions[item] = {
            "label": 0,
            "evidence": "",
            "route": "adjudication" if adjudicated else "independent_consensus",
            "vote_provenance": {
                "candidate": {"confidence": "high", "reviewer": {"reviewer_id": "candidate-model"}},
                "verifier": {"confidence": "high", "reviewer": {"reviewer_id": "verifier-model"}},
            },
        }
    return {
        "id": record["id"],
        "source_sha256": gold.review_ledger.source_descriptor(record)["source_sha256"],
        "snapshot_event_sha256": "b" * 64,
        "decisions": decisions,
    }


def test_v3_ranking_is_independent_of_annotation_snapshot():
    records = {row["id"]: row for row in (_record(index) for index in range(301))}
    rows = {record_id: _row(record) for record_id, record in records.items()}
    families = {record_id: f"f-{index:064x}" for index, record_id in enumerate(records)}

    selected, populations, recurring = gold._expected_audit_selection_v3(
        rows, records, seed=SEED, per_stratum=300,
        family_by_id=families, recurring_family_per_stratum=30,
    )
    assert len(selected) == 24 * 300
    assert sum(populations.values()) == 24 * 301
    assert not recurring
    assert all(row["selection_reasons"] == ["primary_stratum"] for row in selected)
    power = gold._v3_audit_power_metadata(
        {"population_strata": populations, "per_stratum": 300, "recurring_family_per_stratum": 1},
        selected,
    )
    assert power["noncensus_strata"] == 24
    assert power["recurring_family_per_stratum"] == 1
    assert power["strata"]["v1|agreement_remaining_negative|complete"]["zero_defect_upper_95_one_sided"] < 0.01
    assert "not a per-family error bound" in power["interpretation"]
    before = {(row["id"], row["item"], row["rank_sha256"]) for row in selected}

    changed = copy.deepcopy(rows)
    for row in changed.values():
        row["snapshot_event_sha256"] = "c" * 64
    after, _, _ = gold._expected_audit_selection_v3(
        changed, records, seed=SEED, per_stratum=300,
        family_by_id=families, recurring_family_per_stratum=30,
    )
    assert {(row["id"], row["item"], row["rank_sha256"]) for row in after} == before
    assert all(row["snapshot_event_sha256"] == "c" * 64 for row in after)


def test_v3_incomplete_is_mandatory_and_cannot_escape_adjudication():
    record = _record(0, complete=False)
    records = {record["id"]: record}
    row = _row(record, adjudicated=True)
    selected, populations, _ = gold._expected_audit_selection_v3(
        {record["id"]: row}, records, seed=SEED, per_stratum=300,
        family_by_id={record["id"]: "f-1"}, recurring_family_per_stratum=30,
    )
    assert len(selected) == 24
    assert Counter(row["selection_category"] for row in selected) == {"mandatory_adjudication": 24}
    assert all(row["selection_reasons"] == ["mandatory"] for row in selected)
    assert sum(populations.values()) == 24

    row["decisions"]["v10"]["route"] = "independent_consensus"
    with pytest.raises(gold.GoldBuildError, match="incomplete source escaped"):
        gold._expected_audit_selection_v3(
            {record["id"]: row}, records, seed=SEED, per_stratum=300,
            family_by_id={record["id"]: "f-1"}, recurring_family_per_stratum=30,
        )


def test_v3_recurring_family_overlay_covers_rare_template_and_floor():
    records = {row["id"]: row for row in (_record(index) for index in range(301))}
    rows = {record_id: _row(record) for record_id, record in records.items()}
    families = {record_id: f"f-{index:064x}" for index, record_id in enumerate(records)}
    families["R0300"] = families["R0000"]
    selected, _, recurring = gold._expected_audit_selection_v3(
        rows, records, seed=SEED, per_stratum=300,
        family_by_id=families, recurring_family_per_stratum=30,
    )
    assert sum(recurring.values()) == 48
    assert len(selected) >= 24 * 300
    assert {
        (row["id"], row["item"]) for row in selected if row["id"] in {"R0000", "R0300"}
    } == {(record_id, item) for record_id in ("R0000", "R0300") for item in gold.ITEMS}
    assert audit.v3_seed_commitment(SEED) != audit.v3_seed_commitment("b" * 64)
    with pytest.raises(audit.AuditWorkflowError, match="64 lowercase hex"):
        audit.v3_seed_commitment("too-short")


def test_v3_policy_cannot_freeze_without_replayable_family_manifest(tmp_path):
    organizer_input = tmp_path / "organizer.gz"
    organizer_input.write_bytes(b"fixture organizer")
    output = tmp_path / "v3-policy.json"
    with pytest.raises(audit.AuditWorkflowError, match="family manifest is missing"):
        audit.freeze_policy_v3(
            organizer_input=organizer_input,
            family_manifest_path=tmp_path / "missing-family.json",
            output=output,
            seed_commitment_sha256=audit.v3_seed_commitment(SEED),
        )
    assert not output.exists()


def test_v3_policy_commitment_family_replay_and_sealed_selection(tmp_path, monkeypatch):
    records = {row["id"]: row for row in (_record(index) for index in range(2))}
    rows = {record_id: _row(record) for record_id, record in records.items()}
    organizer_input = tmp_path / "organizer.jsonl.gz"
    with gzip.open(organizer_input, "wt", encoding="utf-8") as handle:
        for record in records.values():
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    family_path = tmp_path / "family.json"
    audit._atomic_json(
        family_path,
        notice_families.build_family_manifest(organizer_input, expected_records=2),
    )
    policy_path = tmp_path / "policy.json"
    policy = audit.freeze_policy_v3(
        organizer_input=organizer_input,
        family_manifest_path=family_path,
        output=policy_path,
        seed_commitment_sha256=audit.v3_seed_commitment(SEED),
        frozen_at_utc="2026-09-18T00:00:00+00:00",
        expected_records=2,
    )
    assert policy["schema_version"] == gold.AUDIT_POLICY_SCHEMA_V3
    assert policy["per_stratum"] == 300
    assert policy["recurring_family_per_stratum"] == 1
    assert "selection_seed" not in policy
    assert audit._load_policy(policy_path) == policy

    export_path = tmp_path / "export.json"
    export_path.write_text("{}", encoding="utf-8")
    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text("sealed fixture", encoding="utf-8")
    monkeypatch.setattr(gold, "_load_organizer_records", lambda *_: (list(records), records))
    monkeypatch.setattr(
        gold, "_verify_review_export",
        lambda **_: (
            {"ledger_head_sha256": "c" * 64}, rows,
            {"first_event_utc": "2026-09-18T01:00:00+00:00"},
        ),
    )
    selection_path = tmp_path / "selection.jsonl"
    plan_path = tmp_path / "plan.json"
    packets_path = tmp_path / "packets.jsonl"
    options = dict(
        organizer_input=organizer_input,
        ledger_path=ledger_path,
        review_export_manifest=export_path,
        policy_path=policy_path,
        selection_output=selection_path,
        plan_output=plan_path,
        blind_packets_output=packets_path,
        contract=gold.BuildContract(2, 48, False),
        frozen_at_utc="2026-09-18T02:00:00+00:00",
    )
    with pytest.raises(audit.AuditWorkflowError, match="seed reveal differs"):
        audit.freeze_selection(**options, seed_reveal="b" * 64)
    assert not selection_path.exists()

    plan = audit.freeze_selection(**options, seed_reveal=SEED)
    assert plan["schema_version"] == gold.AUDIT_PLAN_SCHEMA_V3
    assert plan["seed_commitment_sha256"] == policy["seed_commitment_sha256"]
    assert plan["population_cells"] == 48
    assert plan["selected_cells"] == 48
    assert all(
        row["schema_version"] == gold.AUDIT_SELECTION_SCHEMA_V3
        for row in gold._read_jsonl(selection_path, "test selection")
    )

    report_path = tmp_path / "not-yet-audited.json"

    def validate_plan():
        return gold._validate_audit(
            plan_path=plan_path,
            report_path=report_path,
            rows=rows,
            organizer_input=organizer_input,
            review_export_manifest_path=export_path,
            review_export_manifest={"ledger_head_sha256": "c" * 64},
            reviewer_ids={"candidate": set(), "verifier": set(), "adjudicator": set()},
            records=records,
            review_started_utc="2026-09-18T01:00:00+00:00",
            contract=gold.BuildContract(2, 48, False),
        )

    # A valid plan gets all the way through independent selection replay;
    # publication still requires the separate blind audit report.
    with pytest.raises(gold.GoldBuildError, match="gold audit report: missing file"):
        validate_plan()

    corrupted_plan = copy.deepcopy(plan)
    corrupted_plan["selection_seed"] = "b" * 64
    corrupted_plan["plan_sha256"] = gold.sha256_object(
        {key: value for key, value in corrupted_plan.items() if key != "plan_sha256"}
    )
    audit._atomic_json(plan_path, corrupted_plan)
    with pytest.raises(gold.GoldBuildError, match="pre-annotation commitment"):
        validate_plan()

    audit._atomic_json(plan_path, plan)
    attestation = tmp_path / "independent-auditor.txt"
    attestation.write_text("independent human auditor", encoding="utf-8")
    roster_path = tmp_path / "roster.json"
    roster_path.write_text(json.dumps([{
        "reviewer_id": "auditor-1",
        "identity_provider": "local-test",
        "identity_subject": "independent-person",
        "independence_key": "human-independent-1",
        "protocol_version": "source-first-blind-v1",
        "attestation_path": str(attestation),
        "attestation_sha256": hashlib.sha256(attestation.read_bytes()).hexdigest(),
    }]), encoding="utf-8")
    session_path = tmp_path / "session.json"
    audit.start_session(
        plan_path=plan_path, blind_packets_path=packets_path,
        roster_path=roster_path, output=session_path,
        started_utc="2026-09-18T03:00:00+00:00",
    )
    judgments_path = tmp_path / "judgments.jsonl"
    selection = gold._read_jsonl(selection_path, "v3 selection")
    judgments = [{
        "schema_version": audit.BLIND_JUDGMENT_SCHEMA,
        "id": selected["id"], "item": selected["item"],
        "source_sha256": selected["source_sha256"],
        "snapshot_event_sha256": selected["snapshot_event_sha256"],
        "auditor_id": "auditor-1",
        "reviewed_utc": "2026-09-18T04:00:00+00:00",
        "blind_to_annotation_votes": True,
        "proposed_label": 0,
        "confidence": "high",
        "proposed_evidence": "",
        "premise_quotes": [records[selected["id"]]["docs"][0]["text"]],
        "rationale": "source-first independent negative decision",
        "rule_application_confirmed": True,
        "source_completeness_confirmed": True,
    } for selected in selection]
    audit._atomic_jsonl(judgments_path, judgments)
    report_path = tmp_path / "report.json"
    report = audit.finalize_report(
        organizer_input=organizer_input, ledger_path=ledger_path,
        review_export_manifest=export_path, plan_path=plan_path,
        session_path=session_path, judgments_path=judgments_path,
        results_output=tmp_path / "results.jsonl", report_output=report_path,
        contract=gold.BuildContract(2, 48, False),
        completed_utc="2026-09-18T05:00:00+00:00",
    )
    assert report["status"] == "PASS"
    assert report["schema_version"] == gold.AUDIT_REPORT_SCHEMA_V4
    assert report["audit_power"]["selected_cells"] == 48
    assert validate_plan()["audited_cells"] == 48
