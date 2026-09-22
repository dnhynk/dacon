import csv
import gzip
import json
import pathlib

import pytest

from tools.independent_gold import build_gold as GOLD


HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
EVENT_C = "evt-" + "c" * 32


def _source(record_id: str = "R1", text: str = "원문에 있는 정확한 근거") -> dict:
    return {
        "id": record_id,
        "docs": [{"doc_id": "D0", "type": "공고문", "text": text}],
        "meta": {},
        "input_completeness": {"notice": True},
        "dropped_doc_counts": {},
    }


def _identity(role: str, family: str, token: str) -> dict:
    return {
        "tuple_sha256": token,
        "annotator_role": role,
        "prompt_profile": f"{role}-profile",
        "prompt_lineage_sha256": token,
        "model_identity": {
            "mode": "pinned_snapshot",
            "family": family,
            "snapshot": f"{family}-snapshot",
        },
    }


def _qualifications() -> dict:
    return {
        "candidate": {"identity": _identity("candidate", "family-a", HEX_A)},
        "verifier": {"identity": _identity("verifier", "family-b", HEX_B)},
    }


def _reviewer(role: str, family: str, token: str) -> dict:
    model_identity = {
        "mode": "pinned_snapshot",
        "family": family,
        "snapshot": f"{family}-snapshot",
    }
    return {
        "reviewer_id": f"{role}-reviewer",
        "kind": "model",
        "independence_key": f"{role}-independence",
        "method": "independent-full-record-review",
        "method_version": "v1",
        "model_family": family,
        "model_name": f"{family}-snapshot",
        "prompt_sha256": token,
        "request_sha256": token,
        "response_sha256": token,
        "annotator_role": role,
        "qualified_tuple_sha256": token,
        "prompt_lineage_sha256": token,
        "model_identity": model_identity,
    }


def _cell(record: dict, item: str, *, label: int = 0, evidence: str = "") -> dict:
    candidate_vote = {
        "vote_id": f"candidate-{item}",
        "label": label,
        "confidence": "high",
        "reviewer": _reviewer("candidate", "family-a", HEX_A),
    }
    verifier_vote = {
        "vote_id": f"verifier-{item}",
        "label": label,
        "confidence": "high",
        "reviewer": _reviewer("verifier", "family-b", HEX_B),
    }
    return {
        "label": label,
        "confidence": "high",
        "evidence": evidence,
        "evidence_locations": GOLD._source_locations(record, evidence),
        "reason": "fixture decision",
        "route": "independent_consensus",
        "trigger_codes": [],
        "selected_vote_ids": [candidate_vote["vote_id"], verifier_vote["vote_id"]],
        "reviewer_provenance": candidate_vote["reviewer"],
        "vote_provenance": {
            "candidate": candidate_vote,
            "verifier": verifier_vote,
        },
    }


def _resolved_row(
    record: dict,
    *,
    positive_item: str | None = None,
    evidence: str = "",
) -> dict:
    decisions = {
        item: _cell(
            record,
            item,
            label=1 if item == positive_item else 0,
            evidence=evidence if item == positive_item else "",
        )
        for item in GOLD.ITEMS
    }
    return {
        "schema_version": GOLD.review_ledger.RESOLVED_SCHEMA,
        "status": "ok",
        "id": record["id"],
        "source_sha256": GOLD.review_ledger.source_descriptor(record)["source_sha256"],
        "snapshot_event_id": EVENT_C,
        "snapshot_event_sha256": HEX_C,
        "decisions": decisions,
    }


def _write_gzip_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _complete_cli(output_dir: pathlib.Path) -> list[str]:
    return [
        "--input", "input.jsonl.gz",
        "--review-ledger", "ledger.jsonl",
        "--review-export-manifest", "review-export.json",
        "--preflight-plan", "preflight.json",
        "--prepare-manifest", "prepare.json",
        "--candidate-qualification", "candidate.json",
        "--verifier-qualification", "verifier.json",
        "--audit-plan", "audit-plan.json",
        "--audit-report", "audit-report.json",
        "--output-dir", str(output_dir),
    ]


def test_build_contract_requires_exactly_twenty_four_cells_per_record():
    with pytest.raises(ValueError, match="records times 24"):
        GOLD.BuildContract(
            expected_records=2,
            expected_cells=47,
            require_official_input=False,
        )


def test_production_review_lineage_rejects_generic_model_votes():
    snapshot = {
        "source": {"record_id": "R1"},
        "reviews": [
            {"role": "candidate", "schema_version": GOLD.review_ledger.REVIEW_SCHEMA},
            {"role": "verifier", "schema_version": GOLD.review_ledger.REVIEW_SCHEMA},
        ],
    }
    with pytest.raises(GOLD.GoldBuildError, match="not a raw full-record artifact"):
        GOLD._require_full_record_model_reviews(snapshot)
    snapshot["reviews"][0]["schema_version"] = GOLD.review_ledger.FULL_RECORD_REVIEW_SCHEMA
    snapshot["reviews"][1]["schema_version"] = GOLD.review_ledger.FULL_RECORD_REVIEW_SCHEMA
    with pytest.raises(GOLD.GoldBuildError, match="annotation artifact missing"):
        GOLD._require_full_record_model_reviews(snapshot)


def test_organizer_input_count_is_exact_not_at_least(tmp_path):
    source_path = tmp_path / "organizer.jsonl.gz"
    _write_gzip_jsonl(source_path, [_source("R1")])
    contract = GOLD.BuildContract(
        expected_records=2,
        expected_cells=48,
        require_official_input=False,
    )

    with pytest.raises(GOLD.GoldBuildError, match="exactly 2 IDs, found 1"):
        GOLD._load_organizer_records(source_path, contract)


@pytest.mark.parametrize(
    "attribute",
    [
        "input",
        "review_ledger",
        "review_export_manifest",
        "candidate_qualification",
        "verifier_qualification",
        "audit_plan",
        "audit_report",
    ],
)
def test_every_final_gold_receipt_is_mandatory_at_the_cli_boundary(tmp_path, attribute):
    args = GOLD.build_parser().parse_args(_complete_cli(tmp_path))
    setattr(args, attribute, None)

    with pytest.raises(GOLD.GoldBuildError, match=attribute.replace("_", "-")):
        GOLD._require_cli_paths(args)


@pytest.mark.parametrize("legacy_flag", ["--primary", "--verifier", "--adjudication"])
def test_legacy_unsealed_jsonl_inputs_are_permanently_rejected(tmp_path, legacy_flag):
    args = GOLD.build_parser().parse_args(
        [*_complete_cli(tmp_path), legacy_flag, "legacy.jsonl"]
    )

    with pytest.raises(GOLD.GoldBuildError, match="permanently disabled"):
        GOLD._require_cli_paths(args)


def test_resolved_rows_require_exactly_all_twenty_four_items():
    record = _source()
    row = _resolved_row(record)
    del row["decisions"]["v24"]
    contract = GOLD.BuildContract(
        expected_records=1,
        expected_cells=24,
        require_official_input=False,
    )

    with pytest.raises(GOLD.GoldBuildError, match="exact full-24"):
        GOLD._validate_resolved_rows(
            {record["id"]: row},
            {record["id"]: record},
            _qualifications(),
            contract,
        )


def test_unresolved_label_is_forbidden_in_final_cells():
    record = _source()
    row = _resolved_row(record)
    row["decisions"]["v1"]["label"] = "U"
    contract = GOLD.BuildContract(1, 24, False)

    with pytest.raises(GOLD.GoldBuildError, match="binary integer; U is forbidden"):
        GOLD._validate_resolved_rows(
            {record["id"]: row},
            {record["id"]: record},
            _qualifications(),
            contract,
        )


def test_prior_abstention_is_preserved_but_binary_adjudicated_final_is_allowed():
    record = _source()
    row = _resolved_row(record)
    cell = row["decisions"]["v1"]
    cell["route"] = "adjudication"
    cell["trigger_codes"] = ["abstention"]
    cell["vote_provenance"]["candidate"]["label"] = "U"
    human = {
        "reviewer_id": "human-adjudicator",
        "kind": "human",
        "independence_key": "human-independent",
        "method": "source-first-human-adjudication",
        "method_version": "v1",
        "review_protocol_version": "blind-source-v1",
    }
    adjudicator_vote = {
        "vote_id": "human-v1",
        "review_id": "review-human-v1",
        "review_sha256": HEX_C,
        "label": 0,
        "confidence": "high",
        "reviewer": human,
    }
    cell["vote_provenance"]["adjudicator"] = adjudicator_vote
    cell["selected_vote_ids"] = ["candidate-v1", "verifier-v1", "human-v1"]
    cell["reviewer_provenance"] = human

    strata, human_scope, _ = GOLD._validate_resolved_rows(
        {record["id"]: row},
        {record["id"]: record},
        _qualifications(),
        GOLD.BuildContract(1, 24, False),
    )

    assert sum(strata.values()) == 24
    assert human_scope == [
        {
            "id": "R1",
            "item": "v1",
            "vote_id": "human-v1",
            "review_id": "review-human-v1",
            "review_sha256": HEX_C,
            "reviewer_id": "human-adjudicator",
            "label": 0,
        }
    ]
    assert GOLD._audit_selection_category(cell) == ("mandatory_adjudication", True)


@pytest.mark.parametrize(
    ("evidence", "message"),
    [
        ("", "positive witness evidence missing"),
        ("원문에 없는 인용", "evidence is not exact source text"),
        ("x" * 501, "evidence exceeds 500 characters"),
    ],
)
def test_nonabsence_positive_requires_short_exact_source_evidence(evidence, message):
    record = _source()
    row = _resolved_row(record, positive_item="v1", evidence=evidence)
    contract = GOLD.BuildContract(1, 24, False)

    with pytest.raises(GOLD.GoldBuildError, match=message):
        GOLD._validate_resolved_rows(
            {record["id"]: row},
            {record["id"]: record},
            _qualifications(),
            contract,
        )


def test_exact_positive_evidence_and_coordinates_pass():
    record = _source(text="앞\n정확한 근거\n뒤")
    row = _resolved_row(record, positive_item="v1", evidence="정확한 근거")
    contract = GOLD.BuildContract(1, 24, False)

    strata, human_scope, reviewer_ids = GOLD._validate_resolved_rows(
        {record["id"]: row},
        {record["id"]: record},
        _qualifications(),
        contract,
    )

    assert sum(strata.values()) == 24
    assert human_scope == []
    assert reviewer_ids == {
        "candidate": {"candidate-reviewer"},
        "verifier": {"verifier-reviewer"},
        "adjudicator": set(),
    }


def test_audit_selection_reviews_every_boundary_cell_and_samples_thirty_per_item():
    records = {
        f"R-{index:02d}": _source(f"R-{index:02d}")
        for index in range(31)
    }
    rows = {record_id: _resolved_row(record) for record_id, record in records.items()}
    boundary = rows["R-30"]["decisions"]["v1"]
    boundary["vote_provenance"]["candidate"]["confidence"] = "medium"

    selected, populations = GOLD._expected_audit_selection(
        rows,
        records,
        seed="frozen-seed",
        per_stratum=30,
    )

    assert sum(populations.values()) == 31 * 24
    by_item = {item: [] for item in GOLD.ITEMS}
    for row in selected:
        by_item[row["item"]].append(row)
    assert len(by_item["v1"]) == 31
    assert len(by_item["v2"]) == 30
    boundary_rows = [
        row for row in by_item["v1"]
        if row["selection_category"] == "agreement_boundary_negative"
    ]
    assert len(boundary_rows) == 1
    assert boundary_rows[0]["id"] == "R-30"
    assert boundary_rows[0]["mandatory"] is True
    assert all(row["completeness_stratum"] == "complete" for row in selected)


def test_audit_risk_categories_and_completeness_are_fail_closed():
    record = _source()
    cell = _cell(record, "v1")
    cell["route"] = "adjudication"
    assert GOLD._audit_selection_category(cell) == ("mandatory_adjudication", True)

    cell = _cell(record, "v1", label=1, evidence="원문에 있는 정확한 근거")
    cell["vote_provenance"]["verifier"]["confidence"] = "medium"
    assert GOLD._audit_selection_category(cell) == ("mandatory_medium_positive", True)

    incomplete = _source()
    incomplete["dropped_doc_counts"] = {"제안요청서": 1}
    assert GOLD._audit_completeness_stratum(incomplete) == "incomplete"
    assert GOLD.BuildContract(1, 24, False).minimum_audit_per_stratum == 30


def test_negative_or_absence_output_cannot_smuggle_evidence():
    record = _source()
    row = _resolved_row(record)
    row["decisions"]["v1"]["evidence"] = "원문에 있는 정확한 근거"
    row["decisions"]["v1"]["evidence_locations"] = GOLD._source_locations(
        record, "원문에 있는 정확한 근거"
    )
    contract = GOLD.BuildContract(1, 24, False)

    with pytest.raises(GOLD.GoldBuildError, match="requires empty evidence"):
        GOLD._validate_resolved_rows(
            {record["id"]: row},
            {record["id"]: record},
            _qualifications(),
            contract,
        )


def test_cli_failure_removes_stale_publishable_gold_and_writes_failure_receipt(
    tmp_path, capsys
):
    for name in GOLD.PUBLISHABLE_NAMES:
        (tmp_path / name).write_text("stale", encoding="utf-8")

    return_code = GOLD.main(["--output-dir", str(tmp_path)])

    assert return_code == 2
    assert all(not (tmp_path / name).exists() for name in GOLD.PUBLISHABLE_NAMES)
    failure = json.loads((tmp_path / "build_failure.json").read_text(encoding="utf-8"))
    assert failure["gold_written"] is False
    assert "missing required final-gold inputs" in failure["error"]
    assert "Final gold build refused" in capsys.readouterr().err


def test_mid_publication_failure_removes_every_partial_or_stale_output(
    tmp_path, monkeypatch
):
    record = _source()
    row = _resolved_row(record, positive_item="v1", evidence="원문에 있는 정확한 근거")
    for name in GOLD.PUBLISHABLE_NAMES:
        (tmp_path / name).write_text("stale", encoding="utf-8")
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")
    real_replace = GOLD.os.replace
    replace_count = 0

    def fail_on_second_replace(source, destination):
        nonlocal replace_count
        replace_count += 1
        if replace_count == 2:
            raise OSError("simulated publication failure")
        return real_replace(source, destination)

    monkeypatch.setattr(GOLD.os, "replace", fail_on_second_replace)

    with pytest.raises(OSError, match="simulated publication failure"):
        GOLD._write_outputs(
            output_dir=tmp_path,
            organizer_order=[record["id"]],
            rows={record["id"]: row},
            manifest_base={"schema_version": GOLD.BUILD_SCHEMA},
        )

    assert replace_count == 2
    assert all(not (tmp_path / name).exists() for name in GOLD.PUBLISHABLE_NAMES)
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert not list(tmp_path.glob(".gold-build-v2-*"))


def test_successful_publication_preserves_order_evidence_and_hashes(tmp_path):
    record = _source()
    evidence = "원문에 있는 정확한 근거"
    row = _resolved_row(record, positive_item="v1", evidence=evidence)

    manifest = GOLD._write_outputs(
        output_dir=tmp_path,
        organizer_order=[record["id"]],
        rows={record["id"]: row},
        manifest_base={"schema_version": GOLD.BUILD_SCHEMA},
    )

    with (tmp_path / "gold.csv").open("r", encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert len(csv_rows) == 1
    assert csv_rows[0]["id"] == record["id"]
    assert csv_rows[0]["v1"] == "1"
    assert csv_rows[0]["e1"] == evidence
    assert csv_rows[0]["v2"] == "0"
    assert csv_rows[0]["e2"] == ""
    assert GOLD.file_sha256(tmp_path / "gold.csv") == manifest["outputs"]["gold.csv"]["sha256"]
    assert (
        GOLD.file_sha256(tmp_path / "gold_ledger.jsonl")
        == manifest["outputs"]["gold_ledger.jsonl"]["sha256"]
    )
    persisted = json.loads((tmp_path / "build_manifest.json").read_text(encoding="utf-8"))
    assert persisted == manifest
    assert persisted["gold_written"] is True
