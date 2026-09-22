import copy
import gzip
import json
import pathlib
from datetime import datetime, timezone

import pytest

from tests import test_independent_review_ledger as FIX
from tests import test_independent_full_run_receipt_verify as RUN_FIX
from tools.independent_gold import full_record_output
from tools.independent_gold import gold_audit
from tools.independent_gold import review_ledger as LEDGER
from tools.independent_gold import review_ledger_workflow as WORKFLOW


class MemoryIndex:
    def __init__(self, role: str, receipts: dict[str, dict]):
        self._receipts = copy.deepcopy(receipts)
        self.locations = {record_id: record_id for record_id in receipts}
        self.descriptors = [
            {
                "role": role,
                "records": len(receipts),
                "checkpoint_sha256": LEDGER.sha256_object(receipts),
            }
        ]

    def load(self, record_id: str) -> dict:
        return copy.deepcopy(self._receipts[record_id])


def _write_records(path: pathlib.Path, rows: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: pathlib.Path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _receipt_with_label(source: dict, role: str, item: str, label: int) -> dict:
    receipt = FIX.full_record_receipt(source, role)
    wire = json.loads(receipt["raw_content"])
    cell = wire["decisions"][item]
    cell["label"] = label
    cell["positive_evidence_span_id"] = (
        wire["source_span_ids"][0] if label == 1 else None
    )
    raw = FIX.CODEX_BASE.canonical_json(wire)
    receipt["raw_content"] = raw
    receipt["content_sha256"] = FIX.CODEX_BASE.sha256_text(raw)
    receipt["raw_final_sha256"] = FIX.CODEX_BASE.sha256_text(raw)
    receipt["ledger"] = full_record_output.canonical_ledger_projection(
        full_record_output.normalize_output(full_record_output.parse_output(raw)), source
    )
    return receipt


def _prepare_fixture(tmp_path: pathlib.Path):
    source = FIX.record("R-1")
    organizer = tmp_path / "organizer.jsonl.gz"
    _write_records(organizer, [source])
    policy = tmp_path / "audit-policy.json"
    gold_audit.freeze_policy(
        organizer_input=organizer,
        output=policy,
        selection_seed="fixture-seed",
        frozen_at_utc="2026-09-18T11:00:00+00:00",
    )
    candidate = FIX.full_record_receipt(source, "candidate")
    verifier = _receipt_with_label(source, "verifier", "v2", 1)
    qualifications = {
        "candidate": FIX.qualification_report(candidate, "candidate"),
        "verifier": FIX.qualification_report(verifier, "verifier"),
    }
    candidate_qualification = tmp_path / "candidate-qualification.json"
    verifier_qualification = tmp_path / "verifier-qualification.json"
    _write_json(candidate_qualification, qualifications["candidate"])
    _write_json(verifier_qualification, qualifications["verifier"])
    paths = {
        "preflight": tmp_path / "preflight.json",
        "ledger": tmp_path / "review-ledger.jsonl",
        "queue": tmp_path / "blind-queue.jsonl",
        "prepare": tmp_path / "prepare.json",
    }
    prepared = WORKFLOW.prepare_model_passes(
        organizer_input=organizer,
        audit_policy=policy,
        candidate_run_dirs=(),
        verifier_run_dirs=(),
        candidate_qualification=candidate_qualification,
        verifier_qualification=verifier_qualification,
        preflight_plan=paths["preflight"],
        ledger_path=paths["ledger"],
        blind_queue=paths["queue"],
        prepare_manifest=paths["prepare"],
        expected_records=1,
        _indexes={
            "candidate": MemoryIndex("candidate", {source["id"]: candidate}),
            "verifier": MemoryIndex("verifier", {source["id"]: verifier}),
        },
        _qualification_reports=qualifications,
    )
    return source, organizer, policy, qualifications, paths, prepared


def test_prepare_freezes_label_blind_queue_and_resumes_exactly(tmp_path):
    source, organizer, policy, qualifications, paths, prepared = _prepare_fixture(tmp_path)
    assert prepared["records"] == 1
    assert prepared["unresolved_cells"] == 1
    assert prepared["independent_consensus_cells"] == 23
    packet = json.loads(paths["queue"].read_text(encoding="utf-8"))
    assert packet["items"] == ["v2"]
    assert packet["source_record"] == source
    forbidden = {"label", "confidence", "evidence", "reason", "route", "trigger_codes", "votes"}
    assert not (forbidden & set(packet))
    assert packet["annotation_answer_visible"] is False
    assert packet["peer_votes_visible"] is False

    event_count = sum(1 for _ in LEDGER.iter_events(paths["ledger"]))
    second = WORKFLOW.prepare_model_passes(
        organizer_input=organizer,
        audit_policy=policy,
        candidate_run_dirs=(),
        verifier_run_dirs=(),
        candidate_qualification=tmp_path / "candidate-qualification.json",
        verifier_qualification=tmp_path / "verifier-qualification.json",
        preflight_plan=paths["preflight"],
        ledger_path=paths["ledger"],
        blind_queue=paths["queue"],
        prepare_manifest=paths["prepare"],
        expected_records=1,
        _indexes={
            "candidate": MemoryIndex(
                "candidate", {source["id"]: FIX.full_record_receipt(source, "candidate")}
            ),
            "verifier": MemoryIndex(
                "verifier", {source["id"]: _receipt_with_label(source, "verifier", "v2", 1)}
            ),
        },
        _qualification_reports=qualifications,
    )
    assert second == prepared
    assert sum(1 for _ in LEDGER.iter_events(paths["ledger"])) == event_count


def test_finalize_embeds_human_judgment_seals_exports_and_is_idempotent(tmp_path):
    source, organizer, _, qualifications, paths, prepared = _prepare_fixture(tmp_path)
    packet = json.loads(paths["queue"].read_text(encoding="utf-8"))
    judgment = LEDGER.make_human_judgment(
        source,
        "v2",
        packet_sha256=packet["packet_sha256"],
        initial_snapshot_event_id=packet["initial_snapshot_event_id"],
        initial_snapshot_event_sha256=packet["initial_snapshot_event_sha256"],
        reviewer_id="human-a",
        reviewed_utc=datetime.now(timezone.utc).isoformat(),
        label=1,
        confidence="high",
        reason="Blind review finds an exact prohibited institution limitation.",
        premise_quotes=["기관 제한 문구"],
        evidence="기관 제한 문구",
    )
    judgments = tmp_path / "judgments.jsonl"
    judgments.write_text(LEDGER.canonical_json(judgment) + "\n", encoding="utf-8")
    attestation = tmp_path / "human-a-attestation.txt"
    attestation.write_text("I reviewed only the blind packet and applicable law.\n", encoding="utf-8")
    roster = tmp_path / "human-roster.json"
    roster_row = {
        "reviewer_id": "human-a",
        "identity_provider": "test-provider",
        "identity_subject": "test-subject-a",
        "independence_key": "independent:human-a",
        "protocol_version": "blind-human-v1",
        "attestation_path": str(attestation.resolve()),
        "attestation_sha256": WORKFLOW.gold.file_sha256(attestation),
    }
    _write_json(roster, [roster_row])
    export_dir = tmp_path / "export"
    human_receipt = tmp_path / "human-receipt.json"
    first = WORKFLOW.finalize_human_adjudication(
        organizer_input=organizer,
        ledger_path=paths["ledger"],
        preflight_plan=paths["preflight"],
        prepare_manifest=paths["prepare"],
        blind_queue=paths["queue"],
        judgments_path=judgments,
        roster_path=roster,
        export_output_dir=export_dir,
        human_receipt_output=human_receipt,
        expected_records=1,
        _qualification_reports=qualifications,
    )
    assert first["status"] == "SEALED_AND_EXPORTED"
    assert first["human_adjudicated_cells"] == 1
    assert LEDGER.materialize_ledger(
        LEDGER.iter_events(paths["ledger"]), {source["id"]: source}, require_sealed=True
    ).resolved_cells == 24
    receipt = json.loads(human_receipt.read_text(encoding="utf-8"))
    assert receipt["adjudicated_cells"] == 1
    resolved = json.loads((export_dir / "resolved_rows.jsonl").read_text(encoding="utf-8"))
    assert resolved["snapshot_event_id"].startswith("evt-")
    assert resolved["decisions"]["v2"]["route"] == "adjudication"
    assert (
        resolved["decisions"]["v2"]["vote_provenance"]["adjudicator"]["reviewer"]["kind"]
        == "human"
    )

    second = WORKFLOW.finalize_human_adjudication(
        organizer_input=organizer,
        ledger_path=paths["ledger"],
        preflight_plan=paths["preflight"],
        prepare_manifest=paths["prepare"],
        blind_queue=paths["queue"],
        judgments_path=judgments,
        roster_path=roster,
        export_output_dir=export_dir,
        human_receipt_output=human_receipt,
        expected_records=1,
        _qualification_reports=qualifications,
    )
    assert second == first


def test_finalize_rejects_missing_or_tampered_blind_judgment_before_mutation(tmp_path):
    source, organizer, _, qualifications, paths, _ = _prepare_fixture(tmp_path)
    roster = tmp_path / "roster.json"
    attestation = tmp_path / "attestation.txt"
    attestation.write_text("attested", encoding="utf-8")
    _write_json(
        roster,
        [
            {
                "reviewer_id": "human-a",
                "identity_provider": "test",
                "identity_subject": "subject",
                "independence_key": "independent:human-a",
                "protocol_version": "v1",
                "attestation_path": str(attestation.resolve()),
                "attestation_sha256": WORKFLOW.gold.file_sha256(attestation),
            }
        ],
    )
    empty = tmp_path / "empty-judgments.jsonl"
    empty.write_text("", encoding="utf-8")
    before = paths["ledger"].read_bytes()
    with pytest.raises(WORKFLOW.ReviewWorkflowError, match="omit 1 queued cells"):
        WORKFLOW.finalize_human_adjudication(
            organizer_input=organizer,
            ledger_path=paths["ledger"],
            preflight_plan=paths["preflight"],
            prepare_manifest=paths["prepare"],
            blind_queue=paths["queue"],
            judgments_path=empty,
            roster_path=roster,
            export_output_dir=tmp_path / "export",
            human_receipt_output=tmp_path / "receipt.json",
            expected_records=1,
            _qualification_reports=qualifications,
        )
    assert paths["ledger"].read_bytes() == before


def test_actual_run_artifacts_to_ledger_export_without_human_votes(tmp_path):
    catalogs = (
        RUN_FIX.BASE.catalog_facts.CatalogIndex.load(),
        RUN_FIX.BASE.qualification_context.qualification_facts.CatalogReference.load(),
    )
    candidate_case = RUN_FIX._build_case(tmp_path / "candidate", catalogs)
    verifier_case = RUN_FIX._build_case(
        tmp_path / "verifier",
        catalogs,
        role="verifier",
        organizer_input=candidate_case["records_path"],
    )
    organizer = candidate_case["records_path"]
    record_id = next(iter(candidate_case["records"]))
    candidate_receipt = candidate_case["receipts"][record_id]
    verifier_receipt = verifier_case["receipts"][record_id]
    qualifications = {
        "candidate": FIX.qualification_report(candidate_receipt, "candidate"),
        "verifier": FIX.qualification_report(verifier_receipt, "verifier"),
    }
    candidate_qualification = tmp_path / "candidate-qualification.json"
    verifier_qualification = tmp_path / "verifier-qualification.json"
    _write_json(candidate_qualification, qualifications["candidate"])
    _write_json(verifier_qualification, qualifications["verifier"])
    policy = tmp_path / "audit-policy.json"
    gold_audit.freeze_policy(
        organizer_input=organizer,
        output=policy,
        selection_seed="real-run-integration",
        frozen_at_utc="2000-01-01T00:00:00+00:00",
    )
    preflight = tmp_path / "preflight.json"
    review_ledger = tmp_path / "ledger.jsonl"
    queue = tmp_path / "queue.jsonl"
    prepare_manifest = tmp_path / "prepare.json"
    prepared = WORKFLOW.prepare_model_passes(
        organizer_input=organizer,
        audit_policy=policy,
        candidate_run_dirs=[candidate_case["run_dir"]],
        verifier_run_dirs=[verifier_case["run_dir"]],
        candidate_qualification=candidate_qualification,
        verifier_qualification=verifier_qualification,
        preflight_plan=preflight,
        ledger_path=review_ledger,
        blind_queue=queue,
        prepare_manifest=prepare_manifest,
        expected_records=1,
        _qualification_reports=qualifications,
    )
    assert prepared["unresolved_cells"] == 0
    assert queue.read_bytes() == b""
    result = WORKFLOW.finalize_human_adjudication(
        organizer_input=organizer,
        ledger_path=review_ledger,
        preflight_plan=preflight,
        prepare_manifest=prepare_manifest,
        blind_queue=queue,
        judgments_path=None,
        roster_path=None,
        export_output_dir=tmp_path / "export",
        human_receipt_output=None,
        expected_records=1,
        _qualification_reports=qualifications,
    )
    assert result["cells"] == 24
    assert result["human_receipt"] is None
    resolved = json.loads((tmp_path / "export" / "resolved_rows.jsonl").read_text(encoding="utf-8"))
    assert resolved["snapshot_event_id"].startswith("evt-")
    assert all(cell["route"] == "independent_consensus" for cell in resolved["decisions"].values())
