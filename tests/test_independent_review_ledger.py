import copy
import importlib.util
import json
import pathlib

import pytest

from tools.independent_gold import codex_cli_annotator as CODEX_BASE
from tools.independent_gold import codex_full_record_annotator as FULL_RUNNER
from tools.independent_gold import full_record_output as FULL_OUTPUT


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "review_ledger.py"
SPEC = importlib.util.spec_from_file_location("independent_review_ledger", MODULE_PATH)
LEDGER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(LEDGER)


def digest(value: str) -> str:
    return LEDGER.sha256_text(value)


def record(record_id: str = "R-1"):
    return {
        "id": record_id,
        "meta": {"title": "fixture"},
        "input_completeness": {"notice": True, "attachments": True},
        "dropped_doc_counts": {},
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": "기관 제한 문구\n실적 제한 문구\n같은 근거\n같은 근거\n마지막 문장",
            },
            {"doc_id": "D1", "type": "규격서", "text": "특정 모델 ABC-100을 사용한다."},
        ],
    }


def model_reviewer(reviewer_id: str, family: str):
    return {
        "reviewer_id": reviewer_id,
        "kind": "model",
        "independence_key": f"independent:{reviewer_id}",
        "method": "source-grounded rubric review",
        "method_version": "rubric-v1",
        "model_family": family,
        "model_name": f"{family}-instruct",
        "model_revision": "revision-1",
        "prompt_sha256": digest(f"prompt:{reviewer_id}"),
        "request_sha256": digest(f"request:{reviewer_id}"),
        "response_sha256": digest(f"response:{reviewer_id}"),
    }


def human_reviewer(reviewer_id: str = "human-judge"):
    return {
        "reviewer_id": reviewer_id,
        "kind": "human",
        "independence_key": f"independent:{reviewer_id}",
        "method": "source-and-law adjudication",
        "method_version": "protocol-v1",
        "review_protocol_version": "protocol-v1",
    }


def full_record_receipt(source, role: str):
    model = "gpt-5.6-sol" if role == "candidate" else "gpt-6-astra"
    profile = (
        "candidate-full-record-source-direct-v2"
        if role == "candidate"
        else "verifier-full-record-falsification-v2"
    )
    identity = CODEX_BASE.build_model_identity(model=model)
    span_id = "SRC-D0000-S000000"
    wire = {
        "source_span_ids": [span_id],
        "decisions": {
            item: {
                "label": 0,
                "confidence": "H",
                "rationale": f"{item} source-grounded fixture decision",
                "premise_span_ids": [span_id],
                "exception_analysis": None,
                "completeness": "sufficient",
                "material_missing_information": None,
                "positive_evidence_span_id": None,
            }
            for item in LEDGER.ITEMS
        },
    }
    raw = CODEX_BASE.canonical_json(wire)
    ledger = FULL_OUTPUT.canonical_ledger_projection(
        FULL_OUTPUT.normalize_output(FULL_OUTPUT.parse_output(raw)), source
    )
    tuple_sha = digest(f"qualified tuple:{role}")
    events_sha = digest(f"event stream:{role}")
    content_sha = digest(raw)
    return {
        "schema_version": FULL_RUNNER.RESULT_SCHEMA_VERSION,
        "runner_receipt_schema_version": FULL_RUNNER.RECEIPT_SCHEMA_VERSION,
        "status": "ok",
        "error": None,
        "validation_errors": [],
        "id": source["id"],
        "group": FULL_RUNNER.GROUP_NAME,
        "target_items": list(LEDGER.ITEMS),
        "run_key": digest(f"run:{role}"),
        "run_manifest_sha256": digest(f"manifest:{role}"),
        "task_manifest_sha256": digest(f"task:{role}"),
        "source_sha256": LEDGER.sha256_object(source),
        "full_record_context_sha256": digest(f"context:{role}"),
        "full_record_context_schema_version": FULL_RUNNER.full_record_context.SCHEMA_VERSION,
        "full_record_context_bounds": {
            "source_chars": sum(len(doc["text"]) for doc in source["docs"]),
            "max_source_chars": FULL_RUNNER.full_record_context.MAX_SOURCE_CHARS,
            "source_span_count": 1,
            "max_source_span_chars": FULL_RUNNER.full_record_context.MAX_SOURCE_SPAN_CHARS,
            "max_rendered_chars": FULL_RUNNER.full_record_context.MAX_RENDERED_CHARS,
            "rendered_chars": 1,
            "truncated": False,
        },
        "rubric_sha256": digest("rubric"),
        "system_prompt_sha256": digest("rubric"),
        "prompt_sha256": digest(f"prompt:{role}"),
        "output_schema_sha256": CODEX_BASE.sha256_object(FULL_OUTPUT.output_schema()),
        "request_sha256": digest(f"request:{role}"),
        "raw_response_sha256": events_sha,
        "event_stream_sha256": events_sha,
        "stderr_sha256": digest(""),
        "content_sha256": content_sha,
        "raw_final_sha256": content_sha,
        "raw_content": raw,
        "ledger": ledger,
        "model_provenance": {
            "endpoint_kind": "codex_cli_exec",
            "requested_model": model,
            "declared_reasoning_effort": "max",
            "resolved_model": None,
            "resolved_model_note": "CLI event protocol does not attest a resolved alias",
            "codex_cli": {"version_output": "fixture"},
            "command_template": ["codex", "exec"],
            "session_ephemeral": True,
            "sandbox": "read-only",
            "ignore_user_config": True,
            "ignore_rules": True,
            "thread_ids": [f"thread-{role}"],
        },
        "event_audit": {
            "event_count": 3,
            "parse_errors": [],
            "unsafe_items": [],
            "service_errors": [],
            "turn_completed": True,
        },
        "usage": {},
        "attempt": 1,
        "completed_utc": "2026-09-18T12:00:00+00:00",
        "elapsed_seconds": 1.0,
        "annotator_role": role,
        "pass_kind": "blind_first_pass",
        "phase": "unlabeled_20000",
        "tuple_sha256": tuple_sha,
        "run_instance_sha256": digest(f"run:{role}"),
        "cohort_plan_sha256": digest(f"cohort:{role}"),
        "prompt_profile": profile,
        "prompt_lineage_sha256": digest(f"prompt lineage:{role}"),
        "model_identity": identity,
        "peer_visibility": CODEX_BASE.blind_peer_visibility(),
        "imported_source_bundle_sha256": digest(f"bundle:{role}"),
    }


def qualification_report(receipt, role: str):
    return {
        "schema_version": "dacon.independent.codex_full_dev_gate.v1",
        "purpose": "complete_official_dev_annotator_role_qualification",
        "is_full_official_dev_evaluation": True,
        "is_selected_panel": False,
        "qualified_for_declared_role": True,
        "official_dev_role_gate_pass": True,
        "qualified_annotator_role": role,
        "topology": "full_record_24",
        "qualified_tuple_sha256": receipt["tuple_sha256"],
        "qualified_for_gold_generation": False,
        "qualified_for_final_gold_generation": False,
        "identity": {
            "tuple_sha256": receipt["tuple_sha256"],
            "annotator_role": role,
            "prompt_profile": receipt["prompt_profile"],
            "prompt_lineage_sha256": receipt["prompt_lineage_sha256"],
            "model_identity": copy.deepcopy(receipt["model_identity"]),
        },
        "coverage": {
            "unique_ids": 200,
            "unique_cells": 4800,
            "missing_rows": 0,
            "duplicate_rows": 0,
            "extra_rows": 0,
            "abstentions": 0,
            "invalid_evidence": 0,
            "unsafe_tool_events": 0,
        },
        "macro_positive_f1": 0.95,
        "minimum_item_positive_f1": 0.80,
        "by_item": {item: {"positive_f1": 0.80} for item in LEDGER.ITEMS},
        "information_boundary": {
            "model_invocations": 0,
            "labels_read_after_all_candidate_artifact_validation": True,
            "competition_runtime_outputs_used": False,
        },
        "integrity": {"all_cells_binary": True, "unsafe_events_absent": True},
    }


def blind(*, peers_hidden: bool = True, visible=()):
    return {
        "production_outputs_hidden": True,
        "production_code_hidden": True,
        "prohibited_inputs_confirmed_absent": True,
        "peer_votes_hidden": peers_hidden,
        "visible_review_ids": list(visible),
        "attestation": "Only organizer records, supplied law, and the independent rubric were used.",
    }


def decisions(source, *, positives=(), labels=None, confidence=None, uncertainty=None):
    positives = set(positives)
    labels = dict(labels or {})
    confidence = dict(confidence or {})
    uncertainty = dict(uncertainty or {})
    rows = {}
    for item in LEDGER.ITEMS:
        label = labels.get(item, 1 if item in positives else 0)
        evidence = "기관 제한 문구" if label == 1 and item not in LEDGER.ABSENCE_ITEMS else ""
        rows[item] = LEDGER.make_decision(
            source,
            item,
            label=label,
            confidence=confidence.get(item, "high"),
            reason=f"{item} fixture judgment",
            evidence=evidence,
            uncertainty_codes=uncertainty.get(item, ()),
        )
    return rows


def review_pair(source, *, candidate_decisions=None, verifier_decisions=None, candidate_full=True):
    candidate = LEDGER.make_review_session(
        source,
        role="candidate",
        reviewer=model_reviewer("candidate-q", "Qwen"),
        blinding=blind(),
        decisions=candidate_decisions or decisions(source),
        full_record_visible=candidate_full,
    )
    verifier = LEDGER.make_review_session(
        source,
        role="verifier",
        reviewer=model_reviewer("verifier-m", "Mistral"),
        blinding=blind(),
        decisions=verifier_decisions or decisions(source),
    )
    return candidate, verifier


def complete_consensus_snapshot(source, *, positive_items=("v1",)):
    rows = decisions(source, positives=positive_items)
    candidate, verifier = review_pair(
        source, candidate_decisions=rows, verifier_decisions=copy.deepcopy(rows)
    )
    return LEDGER.build_snapshot(source, [candidate, verifier])


def write_and_seal(tmp_path, source, snapshot):
    path = tmp_path / "review-ledger.jsonl"
    writer = LEDGER.LedgerWriter(path, "ledger-fixture")
    event = writer.append_snapshot(snapshot, source)
    writer.seal(expected_record_ids=[source["id"]])
    return path, event


def test_exact_evidence_coordinates_commit_every_occurrence_and_hash():
    source = record()
    decision = LEDGER.make_decision(
        source,
        "v1",
        label=1,
        confidence="high",
        reason="verbatim witness",
        evidence="같은 근거",
    )
    locations = decision["evidence"]["locations"]
    assert len(locations) == 2
    for location in locations:
        text = source["docs"][location["doc_index"]]["text"]
        assert text[location["start"] : location["end"]] == "같은 근거"
        assert location["source_doc_sha256"] == digest(text)
        assert location["evidence_sha256"] == digest("같은 근거")


def test_positive_quote_with_forged_coordinate_is_rejected():
    source = record()
    row = LEDGER.make_decision(
        source,
        "v1",
        label=1,
        confidence="high",
        reason="fixture",
        evidence="기관 제한 문구",
    )
    row["evidence"]["locations"][0]["start"] += 1
    with pytest.raises(LEDGER.LedgerValidationError, match="coordinates"):
        LEDGER.make_review_session(
            source,
            role="candidate",
            reviewer=model_reviewer("candidate-q", "Qwen"),
            blinding=blind(),
            decisions={"v1": row},
        )


def test_consensus_ledger_seals_validates_and_exports_build_inputs(tmp_path):
    source = record()
    snapshot = complete_consensus_snapshot(source, positive_items=("v1", "v10"))
    assert snapshot["resolutions"]["v1"]["route"] == "independent_consensus"
    assert snapshot["resolutions"]["v10"]["evidence"]["quote"] == ""
    path, _ = write_and_seal(tmp_path, source, snapshot)

    events = LEDGER.read_events(path)
    state = LEDGER.materialize_ledger(events, {source["id"]: source}, require_sealed=True)
    assert state.sealed
    assert state.resolved_cells == 24

    output = tmp_path / "export"
    manifest = LEDGER.export_build_gold_inputs(path, {source["id"]: source}, output)
    assert manifest["records"] == 1
    assert manifest["cells"] == 24
    assert manifest["expected_records"] == 1
    assert manifest["expected_cells"] == 24
    assert manifest["unresolved_cells"] == 0
    primary = json.loads((output / "primary.jsonl").read_text(encoding="utf-8"))
    verifier = json.loads((output / "verifier.jsonl").read_text(encoding="utf-8"))
    resolved = json.loads((output / "resolved_rows.jsonl").read_text(encoding="utf-8"))
    assert primary["status"] == verifier["status"] == "ok"
    assert primary["decisions"]["v1"]["evidence"] == "기관 제한 문구"
    assert set(resolved["decisions"]["v1"]["vote_provenance"]) == {"candidate", "verifier"}
    for role, provenance in resolved["decisions"]["v1"]["vote_provenance"].items():
        assert provenance["vote_id"] in resolved["decisions"]["v1"]["selected_vote_ids"]
        assert provenance["reviewer"]["reviewer_id"]
        assert provenance["review_sha256"] == (
            primary if role == "candidate" else verifier
        )["decisions"]["v1"]["review_sha256"]
    assert (output / "adjudication.jsonl").read_text(encoding="utf-8") == ""


def test_full_record_adapter_preserves_receipt_and_opaque_identity_without_revision(tmp_path):
    source = record()
    candidate_receipt = full_record_receipt(source, "candidate")
    verifier_receipt = full_record_receipt(source, "verifier")
    candidate = LEDGER.make_full_record_review_session(
        source,
        role="candidate",
        receipt=candidate_receipt,
        qualification_report=qualification_report(candidate_receipt, "candidate"),
    )
    verifier = LEDGER.make_full_record_review_session(
        source,
        role="verifier",
        receipt=verifier_receipt,
        qualification_report=qualification_report(verifier_receipt, "verifier"),
    )

    assert candidate["schema_version"] == LEDGER.FULL_RECORD_REVIEW_SCHEMA
    assert candidate["annotation_artifact"]["receipt"] == candidate_receipt
    assert candidate["annotation_artifact"]["receipt_sha256"] == LEDGER.sha256_object(
        candidate_receipt
    )
    reviewer = candidate["reviewer"]
    assert "model_revision" not in reviewer
    assert reviewer["model_identity"]["mode"] == "opaque_hosted_alias"
    assert reviewer["model_identity"]["requested_revision"] is None
    assert reviewer["model_identity"]["resolved_revision"] is None
    assert reviewer["qualified_tuple_sha256"] == candidate_receipt["tuple_sha256"]
    assert reviewer["response_sha256"] == candidate_receipt["content_sha256"]
    assert len(candidate["decisions"]) == 24

    snapshot = LEDGER.build_snapshot(source, [candidate, verifier])
    assert len(snapshot["resolutions"]) == 24
    assert {route["route"] for route in snapshot["resolutions"].values()} == {
        "independent_consensus"
    }
    path, _ = write_and_seal(tmp_path, source, snapshot)
    resolved = list(
        LEDGER.iter_resolved_rows(
            LEDGER.read_events(path), {source["id"]: source}
        )
    )[0]
    vote_reviewer = resolved["decisions"]["v1"]["vote_provenance"]["candidate"][
        "reviewer"
    ]
    assert vote_reviewer["qualified_tuple_sha256"] == candidate_receipt["tuple_sha256"]
    assert "model_revision" not in vote_reviewer


def test_full_record_adapter_rejects_stale_qualification_and_receipt_tampering():
    source = record()
    receipt = full_record_receipt(source, "candidate")
    stale = qualification_report(receipt, "candidate")
    stale["qualified_tuple_sha256"] = digest("another tuple")
    with pytest.raises(LEDGER.LedgerValidationError, match="stale/different tuple"):
        LEDGER.make_full_record_review_session(
            source,
            role="candidate",
            receipt=receipt,
            qualification_report=stale,
        )

    tampered = copy.deepcopy(receipt)
    tampered["ledger"]["cells"][0]["label"] = 1
    with pytest.raises(LEDGER.LedgerValidationError, match="ledger shadow mismatch"):
        LEDGER.make_full_record_review_session(
            source,
            role="candidate",
            receipt=tampered,
            qualification_report=qualification_report(tampered, "candidate"),
        )


def test_opaque_qualified_reviewer_cannot_claim_a_model_revision():
    source = record()
    receipt = full_record_receipt(source, "candidate")
    review = LEDGER.make_full_record_review_session(
        source,
        role="candidate",
        receipt=receipt,
        qualification_report=qualification_report(receipt, "candidate"),
    )
    forged = copy.deepcopy(review)
    forged["reviewer"]["model_revision"] = "invented-service-revision"
    forged["review_sha256"] = LEDGER.sha256_object(
        {key: value for key, value in forged.items() if key != "review_sha256"}
    )
    with pytest.raises(LEDGER.LedgerValidationError, match="must not claim model_revision"):
        LEDGER.build_snapshot(source, [forged])


def test_disagreement_is_unresolved_until_independent_adjudicator_votes(tmp_path):
    source = record()
    candidate_rows = decisions(source, labels={"v2": 1})
    verifier_rows = decisions(source, labels={"v2": 0})
    candidate, verifier = review_pair(
        source, candidate_decisions=candidate_rows, verifier_decisions=verifier_rows
    )
    unresolved = LEDGER.build_snapshot(source, [candidate, verifier])
    assert "v2" not in unresolved["resolutions"]
    assert LEDGER.derive_routing(unresolved)["v2"] == {
        "status": "unresolved",
        "route": "adjudication_required",
        "trigger_codes": ["disagreement"],
    }
    writer = LEDGER.LedgerWriter(tmp_path / "ledger.jsonl", "review")
    first = writer.append_snapshot(unresolved, source)
    with pytest.raises(LEDGER.LedgerValidationError, match="unresolved scope"):
        writer.seal(expected_record_ids=[source["id"]])

    adjudicator = LEDGER.make_review_session(
        source,
        role="adjudicator",
        reviewer=human_reviewer(),
        blinding=blind(peers_hidden=False, visible=(candidate["review_id"], verifier["review_id"])),
        decisions={"v2": candidate_rows["v2"]},
    )
    resolved = LEDGER.build_snapshot(
        source, [candidate, verifier, adjudicator], supersedes_event_id=first["event_id"]
    )
    assert resolved["resolutions"]["v2"]["route"] == "adjudication"
    assert resolved["resolutions"]["v2"]["trigger_codes"] == ["disagreement"]
    writer.append_snapshot(resolved, source)
    writer.seal(expected_record_ids=[source["id"]])
    state = LEDGER.materialize_ledger(
        LEDGER.read_events(tmp_path / "ledger.jsonl"), {source["id"]: source}, require_sealed=True
    )
    assert state.resolved_cells == 24
    rows = list(
        LEDGER.iter_resolved_rows(
            LEDGER.read_events(tmp_path / "ledger.jsonl"), {source["id"]: source}
        )
    )
    assert set(rows[0]["decisions"]["v2"]["vote_provenance"]) == {
        "candidate",
        "verifier",
        "adjudicator",
    }


def test_human_adjudication_artifact_binds_blind_judgment_and_queue(tmp_path):
    source = record()
    candidate_rows = decisions(source, labels={"v2": 1})
    verifier_rows = decisions(source, labels={"v2": 0})
    candidate, verifier = review_pair(
        source, candidate_decisions=candidate_rows, verifier_decisions=verifier_rows
    )
    initial = LEDGER.build_snapshot(source, [candidate, verifier])
    writer = LEDGER.LedgerWriter(tmp_path / "ledger.jsonl", "human-artifact")
    initial_event = writer.append_snapshot(initial, source)
    judgment = LEDGER.make_human_judgment(
        source,
        "v2",
        packet_sha256="a" * 64,
        initial_snapshot_event_id=initial_event["event_id"],
        initial_snapshot_event_sha256=initial_event["event_sha256"],
        reviewer_id="human-judge",
        reviewed_utc="2026-09-18T13:30:00+00:00",
        label=1,
        confidence="high",
        reason="The exact clause satisfies the rubric after blind source review.",
        premise_quotes=["기관 제한 문구"],
        evidence="기관 제한 문구",
    )
    adjudicator = LEDGER.make_human_adjudication_review_session(
        source,
        reviewer=human_reviewer(),
        judgments=[judgment],
        prepare_manifest_file_sha256="b" * 64,
        judgments_file_sha256="c" * 64,
    )
    assert adjudicator["schema_version"] == LEDGER.HUMAN_ADJUDICATION_REVIEW_SCHEMA
    assert adjudicator["blinding"]["peer_votes_hidden"] is True
    assert adjudicator["blinding"]["visible_review_ids"] == []
    assert (
        adjudicator["annotation_artifact"]["judgments"][0]["packet_sha256"]
        == "a" * 64
    )
    revised = LEDGER.build_snapshot(
        source,
        [candidate, verifier, adjudicator],
        supersedes_event_id=initial_event["event_id"],
    )
    writer.append_snapshot(revised, source)
    writer.seal(expected_record_ids=[source["id"]])
    state = LEDGER.materialize_ledger(
        LEDGER.iter_events(tmp_path / "ledger.jsonl"),
        {source["id"]: source},
        require_sealed=True,
    )
    assert state.resolved_cells == 24


def test_human_judgment_rejects_tamper_and_incomplete_absence_without_impact_analysis():
    source = record()
    judgment = LEDGER.make_human_judgment(
        source,
        "v2",
        packet_sha256="a" * 64,
        initial_snapshot_event_id="evt-" + "b" * 32,
        initial_snapshot_event_sha256="c" * 64,
        reviewer_id="human-judge",
        reviewed_utc="2026-09-18T13:30:00+00:00",
        label=0,
        confidence="medium",
        reason="The cited premise does not meet the rubric threshold.",
        premise_quotes=["기관 제한 문구"],
    )
    forged = copy.deepcopy(judgment)
    forged["reason"] = "Tampered after commitment."
    with pytest.raises(LEDGER.LedgerValidationError, match="self-hash mismatch"):
        LEDGER._validate_human_judgment(source, forged, context="forged")

    incomplete = record("R-incomplete")
    incomplete["input_completeness"]["attachments"] = False
    incomplete["dropped_doc_counts"] = {"제안요청서": 1}
    with pytest.raises(LEDGER.LedgerValidationError, match="missing-document impact analysis"):
        LEDGER.make_human_judgment(
            incomplete,
            "v10",
            packet_sha256="a" * 64,
            initial_snapshot_event_id="evt-" + "b" * 32,
            initial_snapshot_event_sha256="c" * 64,
            reviewer_id="human-judge",
            reviewed_utc="2026-09-18T13:30:00+00:00",
            label=0,
            confidence="high",
            reason="Missing attachment prevents an unsupported absence claim.",
            premise_quotes=["기관 제한 문구"],
            source_completeness="organizer_scope_sufficient",
        )


def test_incomplete_absence_requires_organizer_only_scope_justification():
    source = record("R-incomplete-justification")
    source["input_completeness"]["attachments"] = False
    source["dropped_doc_counts"] = {"제안요청서": 1}
    judgment = LEDGER.make_human_judgment(
        source,
        "v10",
        packet_sha256="a" * 64,
        initial_snapshot_event_id="evt-" + "b" * 32,
        initial_snapshot_event_sha256="c" * 64,
        reviewer_id="human-judge",
        reviewed_utc="2026-09-18T13:30:00+00:00",
        label=0,
        confidence="high",
        reason=(
            "The target is decided solely by the notice clause already printed in the "
            "organizer source; the omitted RFP cannot alter that specific predicate."
        ),
        premise_quotes=["기관 제한 문구"],
        source_completeness="organizer_scope_sufficient",
        missing_document_impact=(
            "The dropped RFP could add other conditions, but this item's tested "
            "condition is fixed by the quoted notice clause within the supplied record."
        ),
    )
    assert judgment["dropped_doc_types_reviewed"] == ["제안요청서"]
    assert judgment["material_missing_information"] is False
    assert "official_supplements" not in judgment
    forbidden_external = copy.deepcopy(judgment)
    forbidden_external["official_supplements"] = [
        {"url": "https://example.invalid/notice", "sha256": "d" * 64}
    ]
    forbidden_external["judgment_sha256"] = LEDGER.sha256_object(
        {key: value for key, value in forbidden_external.items() if key != "judgment_sha256"}
    )
    with pytest.raises(LEDGER.LedgerValidationError, match="keys"):
        LEDGER._validate_human_judgment(source, forbidden_external, context="replay")
    forged = copy.deepcopy(judgment)
    forged["material_missing_information"] = True
    forged["judgment_sha256"] = LEDGER.sha256_object(
        {key: value for key, value in forged.items() if key != "judgment_sha256"}
    )
    with pytest.raises(LEDGER.LedgerValidationError, match="materially missing"):
        LEDGER._validate_human_judgment(source, forged, context="replay")


def test_low_confidence_uncertainty_and_truncated_absence_are_routed():
    source = record()
    candidate_rows = decisions(
        source,
        confidence={"v3": "low"},
        uncertainty={"v4": ("law_scope_unclear",)},
    )
    verifier_rows = decisions(source)
    candidate, verifier = review_pair(
        source,
        candidate_decisions=candidate_rows,
        verifier_decisions=verifier_rows,
        candidate_full=False,
    )
    snapshot = LEDGER.build_snapshot(source, [candidate, verifier])
    routes = LEDGER.derive_routing(snapshot)
    assert routes["v3"]["trigger_codes"] == ["low_confidence"]
    assert routes["v4"]["trigger_codes"] == ["uncertainty_flag"]
    assert routes["v10"]["trigger_codes"] == ["incomplete_source_view"]
    assert routes["v9"]["status"] == "resolved"


def test_organizer_source_incompleteness_forces_every_item_to_adjudication():
    source = record()
    source["input_completeness"]["attachments"] = False
    candidate, verifier = review_pair(source)
    snapshot = LEDGER.build_snapshot(source, [candidate, verifier])
    routes = LEDGER.derive_routing(snapshot)
    for item in LEDGER.ITEMS:
        assert routes[item]["status"] == "unresolved"
        assert routes[item]["trigger_codes"] == ["organizer_source_incomplete"]


def test_same_model_family_cannot_masquerade_as_independent_verifier():
    source = record()
    rows = decisions(source)
    candidate = LEDGER.make_review_session(
        source,
        role="candidate",
        reviewer=model_reviewer("model-a", "Qwen"),
        blinding=blind(),
        decisions=rows,
    )
    verifier = LEDGER.make_review_session(
        source,
        role="verifier",
        reviewer=model_reviewer("model-b", "qWEN"),
        blinding=blind(),
        decisions=copy.deepcopy(rows),
    )
    snapshot = LEDGER.build_snapshot(source, [candidate, verifier])
    route = LEDGER.derive_routing(snapshot)["v1"]
    assert route["status"] == "unresolved"
    assert "non_independent_verifier" in route["trigger_codes"]


def test_model_verifier_must_have_independent_prompt_lineage():
    source = record()
    rows = decisions(source)
    candidate_reviewer = model_reviewer("model-a", "Qwen")
    candidate = LEDGER.make_review_session(
        source,
        role="candidate",
        reviewer=candidate_reviewer,
        blinding=blind(),
        decisions=rows,
    )
    verifier_reviewer = model_reviewer("model-b", "Mistral")
    verifier_reviewer["prompt_sha256"] = candidate_reviewer["prompt_sha256"]
    verifier = LEDGER.make_review_session(
        source,
        role="verifier",
        reviewer=verifier_reviewer,
        blinding=blind(),
        decisions=copy.deepcopy(rows),
    )
    route = LEDGER.derive_routing(LEDGER.build_snapshot(source, [candidate, verifier]))["v1"]
    assert route["status"] == "unresolved"
    assert route["trigger_codes"] == ["non_independent_verifier"]


def test_reviewer_identity_normalization_blocks_whitespace_aliases():
    source = record()
    rows = decisions(source)
    candidate = LEDGER.make_review_session(
        source,
        role="candidate",
        reviewer=model_reviewer("model-a", "Qwen"),
        blinding=blind(),
        decisions=rows,
    )
    verifier_reviewer = model_reviewer("model-b", " qWEN ")
    verifier = LEDGER.make_review_session(
        source,
        role="verifier",
        reviewer=verifier_reviewer,
        blinding=blind(),
        decisions=copy.deepcopy(rows),
    )
    route = LEDGER.derive_routing(LEDGER.build_snapshot(source, [candidate, verifier]))["v1"]
    assert route["status"] == "unresolved"
    assert "non_independent_verifier" in route["trigger_codes"]


def test_candidate_and_verifier_must_be_blind_to_peer_votes():
    source = record()
    with pytest.raises(LEDGER.LedgerValidationError, match="peer vote was visible"):
        LEDGER.make_review_session(
            source,
            role="candidate",
            reviewer=model_reviewer("candidate-q", "Qwen"),
            blinding=blind(peers_hidden=False, visible=("some-review",)),
            decisions={"v1": decisions(source)["v1"]},
        )


def test_forbidden_same_production_model_provenance_is_rejected():
    source = record()
    reviewer = model_reviewer("bad-model", "Gemma")
    with pytest.raises(LEDGER.LedgerValidationError, match="forbidden review provenance"):
        LEDGER.make_review_session(
            source,
            role="candidate",
            reviewer=reviewer,
            blinding=blind(),
            decisions={"v1": decisions(source)["v1"]},
        )


def test_organizer_source_rejects_injected_labels_or_prediction_fields():
    source = record()
    source["production_prediction"] = {"v1": 1}
    with pytest.raises(LEDGER.LedgerValidationError, match="non-organizer top-level"):
        LEDGER.source_descriptor(source)

    source = record()
    source["meta"]["정답지_예측"] = "hidden model answer"
    with pytest.raises(LEDGER.LedgerValidationError, match="prohibited label/prediction field"):
        LEDGER.source_descriptor(source)


def test_review_schema_rejects_shadow_dependency_fields_even_if_rehashed():
    source = record()
    candidate, _ = review_pair(source)
    forged = copy.deepcopy(candidate)
    forged["declared_inputs"] = {"prediction": "production answer"}
    forged["review_sha256"] = LEDGER.sha256_object(
        {key: value for key, value in forged.items() if key != "review_sha256"}
    )
    with pytest.raises(LEDGER.LedgerValidationError, match="schema keys differ"):
        LEDGER.build_snapshot(source, [forged])


def test_hash_chain_detects_payload_tamper_reorder_and_append_after_seal(tmp_path):
    source = record()
    path, _ = write_and_seal(tmp_path, source, complete_consensus_snapshot(source))
    events = LEDGER.read_events(path)

    tampered = copy.deepcopy(events)
    tampered[0]["payload"]["audit_tags"].append("forged")
    with pytest.raises(LEDGER.LedgerValidationError, match="event (id|hash) mismatch"):
        LEDGER.verify_event_chain(tampered)

    with pytest.raises(LEDGER.LedgerValidationError, match="sequence mismatch|previous hash"):
        LEDGER.verify_event_chain(list(reversed(events)))

    extra = LEDGER.make_event(
        ledger_id="ledger-fixture",
        seq=2,
        prev_event_sha256=events[-1]["event_sha256"],
        event_type="record_snapshot",
        payload=events[0]["payload"],
    )
    with pytest.raises(LEDGER.LedgerValidationError, match="appended after seal"):
        LEDGER.verify_event_chain(events + [extra])


def test_tail_truncation_is_refused_when_seal_is_required(tmp_path):
    source = record()
    path, _ = write_and_seal(tmp_path, source, complete_consensus_snapshot(source))
    events = LEDGER.read_events(path)
    assert LEDGER.verify_event_chain(events[:-1])["sealed"] is False
    with pytest.raises(LEDGER.LedgerValidationError, match="not sealed"):
        LEDGER.materialize_ledger(events[:-1], {source["id"]: source}, require_sealed=True)


def test_final_export_rejects_a_sealed_subset_of_supplied_records(tmp_path):
    first = record("R-1")
    second = record("R-2")
    path, _ = write_and_seal(tmp_path, first, complete_consensus_snapshot(first))
    with pytest.raises(LEDGER.LedgerValidationError, match="every supplied source record"):
        LEDGER.export_build_gold_inputs(
            path,
            {first["id"]: first, second["id"]: second},
            tmp_path / "subset-export",
        )

    with pytest.raises(LEDGER.LedgerValidationError, match="every supplied source record"):
        list(
            LEDGER.iter_resolved_rows(
                LEDGER.read_events(path),
                {first["id"]: first, second["id"]: second},
            )
        )


def test_revision_must_supersede_current_snapshot_not_stale_one(tmp_path):
    source = record()
    writer = LEDGER.LedgerWriter(tmp_path / "ledger.jsonl", "revision-ledger")
    first_snapshot = complete_consensus_snapshot(source)
    first = writer.append_snapshot(first_snapshot, source)
    revision = LEDGER.build_snapshot(
        source,
        first_snapshot["reviews"],
        supersedes_event_id="evt-not-the-active-snapshot",
    )
    with pytest.raises(LEDGER.LedgerValidationError, match="wrong superseded"):
        writer.append_snapshot(revision, source)
    assert first["event_id"] == writer.state.active[source["id"]].event_id


def test_unsealed_ledger_can_resume_only_after_full_source_verification(tmp_path):
    source = record()
    path = tmp_path / "ledger.jsonl"
    original = LEDGER.LedgerWriter(path, "resume-ledger")
    original.append_snapshot(complete_consensus_snapshot(source), source)
    with pytest.raises(LEDGER.LedgerValidationError, match="requires organizer source"):
        LEDGER.LedgerWriter(path, "resume-ledger", create=False)
    resumed = LEDGER.LedgerWriter(
        path, "resume-ledger", create=False, records={source["id"]: source}
    )
    assert resumed.state.event_count == 1
    resumed.seal(expected_record_ids=[source["id"]])
    assert LEDGER.materialize_ledger(
        LEDGER.iter_events(path), {source["id"]: source}, require_sealed=True
    ).sealed


def test_source_mutation_after_review_is_detected(tmp_path):
    source = record()
    path, _ = write_and_seal(tmp_path, source, complete_consensus_snapshot(source))
    changed = copy.deepcopy(source)
    changed["docs"][0]["text"] += "\n사후 변경"
    with pytest.raises(LEDGER.LedgerValidationError, match="source descriptor/hash mismatch"):
        LEDGER.materialize_ledger(
            LEDGER.read_events(path), {source["id"]: changed}, require_sealed=True
        )


def test_stored_resolution_cannot_be_changed_even_if_snapshot_hash_is_recomputed():
    source = record()
    snapshot = complete_consensus_snapshot(source)
    forged = copy.deepcopy(snapshot)
    forged["resolutions"]["v1"]["final_label"] = 0
    forged["snapshot_sha256"] = LEDGER.sha256_object(
        {key: value for key, value in forged.items() if key != "snapshot_sha256"}
    )
    with pytest.raises(LEDGER.LedgerValidationError, match="does not match votes"):
        LEDGER.validate_snapshot(forged, source)


def test_stratified_audit_sampling_is_deterministic_and_covers_risk_classes(tmp_path):
    source = record()
    candidate_rows = decisions(source, positives=("v1", "v10"), labels={"v2": 1})
    verifier_rows = decisions(source, positives=("v1", "v10"), labels={"v2": 0})
    candidate, verifier = review_pair(
        source, candidate_decisions=candidate_rows, verifier_decisions=verifier_rows
    )
    adjudicator = LEDGER.make_review_session(
        source,
        role="adjudicator",
        reviewer=human_reviewer(),
        blinding=blind(peers_hidden=False, visible=(candidate["review_id"], verifier["review_id"])),
        decisions={"v2": candidate_rows["v2"]},
    )
    snapshot = LEDGER.build_snapshot(source, [candidate, verifier, adjudicator])
    path, _ = write_and_seal(tmp_path, source, snapshot)
    events = LEDGER.read_events(path)
    records = {source["id"]: source}
    first = LEDGER.stratified_audit_sample(events, records, per_stratum=1, seed="fixed")
    second = LEDGER.stratified_audit_sample(events, records, per_stratum=1, seed="fixed")
    assert first == second
    assert first["population_cells"] == 24
    assert "v1|consensus_positive" in first["strata"]
    assert "v2|adjudicated_disagreement" in first["strata"]
    assert "v10|absence_positive" in first["strata"]
    assert "v3|consensus_negative" in first["strata"]


def test_module_has_no_competition_runtime_import_or_model_call():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "import " + "submission" not in source
    assert "from " + "submission" not in source
    assert "import pps" not in source
    assert "from pps" not in source
    assert "urllib" not in source
    assert "requests" not in source
