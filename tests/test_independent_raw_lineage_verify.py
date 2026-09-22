"""Sealed-ledger provenance must still depend on the preserved raw model events."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from tests import test_independent_full_run_receipt_verify as RUN_FIX
from tests import test_independent_review_ledger as LEDGER_FIX
from tools.independent_gold import gold_audit
from tools.independent_gold import raw_lineage_verify as RAW
from tools.independent_gold import review_ledger as LEDGER
from tools.independent_gold import review_ledger_workflow as WORKFLOW


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sealed_actual_run_fixture(tmp_path, monkeypatch, *, human_revision=False):
    catalogs = (
        RUN_FIX.BASE.catalog_facts.CatalogIndex.load(),
        RUN_FIX.BASE.qualification_context.qualification_facts.CatalogReference.load(),
    )
    candidate = RUN_FIX._build_case(tmp_path / "candidate", catalogs)
    if human_revision:
        original_raw_output = RUN_FIX._raw_output

        def disagreement_output(task):
            wire = json.loads(original_raw_output(task))
            cell = wire["decisions"]["v2"]
            cell["label"] = 1
            cell["positive_evidence_span_id"] = wire["source_span_ids"][0]
            return RUN_FIX.BASE.canonical_json(wire)

        monkeypatch.setattr(RUN_FIX, "_raw_output", disagreement_output)
    verifier = RUN_FIX._build_case(
        tmp_path / "verifier",
        catalogs,
        role="verifier",
        organizer_input=candidate["records_path"],
    )
    if human_revision:
        monkeypatch.setattr(RUN_FIX, "_raw_output", original_raw_output)
    record_id = next(iter(candidate["records"]))
    qualifications = {
        "candidate": LEDGER_FIX.qualification_report(candidate["receipts"][record_id], "candidate"),
        "verifier": LEDGER_FIX.qualification_report(verifier["receipts"][record_id], "verifier"),
    }
    qualification_paths = {
        role: tmp_path / f"{role}-qualification.json"
        for role in ("candidate", "verifier")
    }
    for role, path in qualification_paths.items():
        _write_json(path, qualifications[role])
    # The synthetic one-record reports intentionally are not official dev200
    # qualifications.  Only this fixture substitutes their report loader; the
    # production verifier always replays actual qualification files.
    monkeypatch.setattr(
        WORKFLOW,
        "_load_qualification",
        lambda _path, role: qualifications[role],
    )
    policy = tmp_path / "audit-policy.json"
    gold_audit.freeze_policy(
        organizer_input=candidate["records_path"],
        output=policy,
        selection_seed="raw-lineage-regression",
        frozen_at_utc="2000-01-01T00:00:00+00:00",
    )
    paths = {
        "organizer_input": candidate["records_path"],
        "preflight_plan": tmp_path / "preflight.json",
        "prepare_manifest": tmp_path / "prepare.json",
        "ledger_path": tmp_path / "review-ledger.jsonl",
    }
    queue = tmp_path / "blind-queue.jsonl"
    prepared = WORKFLOW.prepare_model_passes(
        organizer_input=paths["organizer_input"],
        audit_policy=policy,
        candidate_run_dirs=[candidate["run_dir"]],
        verifier_run_dirs=[verifier["run_dir"]],
        candidate_qualification=qualification_paths["candidate"],
        verifier_qualification=qualification_paths["verifier"],
        preflight_plan=paths["preflight_plan"],
        ledger_path=paths["ledger_path"],
        blind_queue=queue,
        prepare_manifest=paths["prepare_manifest"],
        expected_records=1,
    )
    assert prepared["unresolved_cells"] == (1 if human_revision else 0)
    judgment_path = None
    roster_path = None
    human_receipt = None
    if human_revision:
        packet = json.loads(queue.read_text(encoding="utf-8"))
        record = candidate["records"][record_id]
        quote = "공동수급 구성원의 최소 지분율은 5퍼센트입니다."
        judgment = LEDGER.make_human_judgment(
            record,
            "v2",
            packet_sha256=packet["packet_sha256"],
            initial_snapshot_event_id=packet["initial_snapshot_event_id"],
            initial_snapshot_event_sha256=packet["initial_snapshot_event_sha256"],
            reviewer_id="human-raw-lineage-fixture",
            reviewed_utc=datetime.now(timezone.utc).isoformat(),
            label=1,
            confidence="high",
            reason="The fixture reviewer independently checks the supplied notice wording and resolves this disputed cell.",
            premise_quotes=[quote],
            evidence=quote,
        )
        judgment_path = tmp_path / "human-judgments.jsonl"
        judgment_path.write_text(LEDGER.canonical_json(judgment) + "\n", encoding="utf-8")
        attestation = tmp_path / "human-attestation.txt"
        attestation.write_text("I reviewed only the blind organizer packet.\n", encoding="utf-8")
        roster_path = tmp_path / "human-roster.json"
        _write_json(
            roster_path,
            [
                {
                    "reviewer_id": "human-raw-lineage-fixture",
                    "identity_provider": "fixture-provider",
                    "identity_subject": "fixture-reviewer",
                    "independence_key": "human:raw-lineage-fixture",
                    "protocol_version": "blind-human-v1",
                    "attestation_path": str(attestation.resolve()),
                    "attestation_sha256": WORKFLOW.gold.file_sha256(attestation),
                }
            ],
        )
        human_receipt = tmp_path / "human-receipt.json"
    WORKFLOW.finalize_human_adjudication(
        organizer_input=paths["organizer_input"],
        ledger_path=paths["ledger_path"],
        preflight_plan=paths["preflight_plan"],
        prepare_manifest=paths["prepare_manifest"],
        blind_queue=queue,
        judgments_path=judgment_path,
        roster_path=roster_path,
        export_output_dir=tmp_path / "export",
        human_receipt_output=human_receipt,
        expected_records=1,
    )
    return paths, candidate, verifier, record_id


def test_sealed_raw_lineage_replays_actual_one_record_runs(tmp_path, monkeypatch):
    paths, candidate, verifier, _ = _sealed_actual_run_fixture(tmp_path, monkeypatch)
    report = RAW.verify_sealed_raw_lineage(**paths, expected_records=1)
    assert report["status"] == "PASS"
    assert report["records"] == 1
    assert report["cells"] == 24
    assert report["initial_event_count"] == 1
    assert report["sealed_event_count"] == 2
    assert report["roles"]["candidate"]["runs"] == 1
    assert report["roles"]["verifier"]["runs"] == 1
    assert RAW.verify_sealed_raw_lineage(**paths, expected_records=1) == report
    assert candidate["run_dir"].is_dir() and verifier["run_dir"].is_dir()


def test_sealed_raw_lineage_rejects_mutated_raw_event_stream(tmp_path, monkeypatch):
    paths, candidate, _, record_id = _sealed_actual_run_fixture(
        tmp_path, monkeypatch, human_revision=True
    )
    baseline = RAW.verify_sealed_raw_lineage(**paths, expected_records=1)
    sealed_ledger_bytes = paths["ledger_path"].read_bytes()
    task_slug = RUN_FIX.BASE._task_slug(record_id)
    raw_events = (
        candidate["run_dir"]
        / "tasks"
        / task_slug
        / RUN_FIX.RUNNER.GROUP_NAME
        / "attempts"
        / "attempt-001"
        / "events.jsonl"
    )
    original = raw_events.read_bytes()
    assert b"thread.started" in original
    raw_events.write_bytes(original.replace(b"thread.started", b"thread.changed", 1))

    with pytest.raises(WORKFLOW.ReviewWorkflowError, match="full-run verification failed"):
        RAW.verify_sealed_raw_lineage(**paths, expected_records=1)
    assert paths["ledger_path"].read_bytes() == sealed_ledger_bytes
    assert baseline["status"] == "PASS"

    raw_events.write_bytes(original)
    raw_events.rename(raw_events.with_suffix(".missing-fixture"))
    with pytest.raises(WORKFLOW.ReviewWorkflowError, match="full-run verification failed"):
        RAW.verify_sealed_raw_lineage(**paths, expected_records=1)


def test_sealed_raw_lineage_allows_preserved_human_revision(tmp_path, monkeypatch):
    paths, _, _, _ = _sealed_actual_run_fixture(tmp_path, monkeypatch, human_revision=True)
    report = RAW.verify_sealed_raw_lineage(**paths, expected_records=1)
    assert report["status"] == "PASS"
    assert report["initial_event_count"] == 1
    assert report["sealed_event_count"] == 3


def test_sealed_raw_lineage_requires_preflight_after_raw_completion(tmp_path, monkeypatch):
    paths, _, _, _ = _sealed_actual_run_fixture(tmp_path, monkeypatch)
    assert RAW.verify_sealed_raw_lineage(**paths, expected_records=1)["status"] == "PASS"
    plan = json.loads(paths["preflight_plan"].read_text(encoding="utf-8"))
    plan["frozen_at_utc"] = "2020-01-01T00:00:00+00:00"
    plan["plan_sha256"] = LEDGER.sha256_object(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    )
    _write_json(paths["preflight_plan"], plan)
    manifest = json.loads(paths["prepare_manifest"].read_text(encoding="utf-8"))
    manifest["preflight_plan_file_sha256"] = WORKFLOW.gold.file_sha256(paths["preflight_plan"])
    manifest["preflight_plan_sha256"] = plan["plan_sha256"]
    manifest["manifest_sha256"] = LEDGER.sha256_object(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )
    _write_json(paths["prepare_manifest"], manifest)

    with pytest.raises(WORKFLOW.ReviewWorkflowError, match="outside policy-to-preflight window"):
        RAW.verify_sealed_raw_lineage(**paths, expected_records=1)
