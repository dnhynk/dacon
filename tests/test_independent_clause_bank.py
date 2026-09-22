from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import pathlib
import sqlite3

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "clause_bank.py"
SPEC = importlib.util.spec_from_file_location("independent_clause_bank", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


@pytest.fixture(scope="module")
def catalog():
    return MODULE.catalog_facts.CatalogIndex.load()


def record(record_id: str, text: str, *, complete: bool = True):
    fields = MODULE.template_clusters.legal_facts.META_FIELDS
    return {
        "id": record_id,
        "docs": [{"doc_id": "D0", "type": "공고문", "text": text}],
        "dropped_doc_counts": {} if complete else {"제안요청서": 1},
        "input_completeness": {
            "공고문_본문": True,
            "제안요청서": complete,
        },
        "assembly_policy_version": "fixture-v1",
        "meta": {
            fields["contract_law"]: "국가계약법",
            fields["work_type"]: "용역",
            fields["contract_method"]: "제한경쟁",
            fields["budget_won"]: 300_000_000,
            fields["estimated_price_won"]: 272_727_273,
            fields["catalog_codes"]: "1234567890",
        },
        "anon_applied": True,
    }


def build(tmp_path, records, catalog, *, stem="bank", limit=None):
    jsonl = tmp_path / f"{stem}.jsonl"
    manifest = tmp_path / f"{stem}.manifest.json"
    summary = tmp_path / f"{stem}.md"
    payload = MODULE.build_clause_bank(
        records,
        output_jsonl=jsonl,
        manifest_output=manifest,
        summary_output=summary,
        catalog_index=catalog,
        limit=limit,
    )
    rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
    return payload, rows, jsonl, manifest, summary


def build_spool(tmp_path, records, catalog, *, stem="spool"):
    database = tmp_path / f"{stem}.sqlite3"
    connection = MODULE._connect(database)
    provenance_sha = MODULE.sha256_object(MODULE._catalog_provenance(catalog))
    try:
        for source in records:
            source_sha = MODULE.template_clusters.packetize.sha256_object(source)
            connection.execute(
                "INSERT INTO source_records VALUES (?, ?)",
                (str(source["id"]), source_sha),
            )
            profiles = MODULE.template_clusters.build_record_profiles(
                source, catalog_index=catalog
            )
            for group_name in MODULE.GROUPS:
                MODULE._insert_profile(
                    connection,
                    source,
                    profiles[group_name],
                    catalog_provenance_sha256=provenance_sha,
                )
        connection.commit()
    finally:
        connection.close()
    return database


def test_clause_fingerprint_bank_retains_every_occurrence_and_raw_quote_variant(
    tmp_path, catalog
):
    first = record("R-B", "입찰 참가 안내\n\n수행실적은 계약 후 3회 검토한다.")
    second = record("R-A", "입찰 참가 안내\n\n수행실적은 계약 후 5회 검토한다.")
    manifest, rows, jsonl, manifest_path, summary = build(
        tmp_path, [first, second], catalog
    )

    repeated = next(
        row
        for row in rows
        if row["group"] == "v1-4" and row["occurrence_count"] == 2
    )
    assert repeated["member_record_ids"] == ["R-A", "R-B"]
    assert repeated["member_record_count"] == 2
    assert repeated["raw_quote_variant_count"] == 2
    assert {variant["raw_quote"] for variant in repeated["raw_quote_variants"]} == {
        "수행실적은 계약 후 3회 검토한다.",
        "수행실적은 계약 후 5회 검토한다.",
    }
    sources = {row["id"]: row for row in (first, second)}
    for occurrence in repeated["occurrences"]:
        coordinate = occurrence["coordinate"]
        source = sources[occurrence["record_id"]]["docs"][coordinate["doc_index"]]
        quote = source["text"][coordinate["start"] : coordinate["end"]]
        assert hashlib.sha256(quote.encode("utf-8")).hexdigest() == coordinate[
            "text_sha256"
        ]
        assert hashlib.sha256(source["text"].encode("utf-8")).hexdigest() == coordinate[
            "source_doc_sha256"
        ]
        assert occurrence["coordinate_sha256"] == MODULE.sha256_object(coordinate)
    assert manifest["outputs"]["clauses_jsonl"]["rows"] == len(rows)
    assert manifest["outputs"]["clauses_jsonl"]["sha256"] == hashlib.sha256(
        jsonl.read_bytes()
    ).hexdigest()
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest
    assert "라벨 자동 전파" in summary.read_text(encoding="utf-8")
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.sqlite3"))


def test_clause_rows_are_order_deterministic_but_never_claim_semantic_equivalence(
    tmp_path, catalog
):
    left = record("R-B", "수행실적은 계약 후 3회 검토한다.")
    right = record("R-A", "수행실적은 계약 후 5회 검토한다.")
    forward, forward_rows, *_ = build(
        tmp_path, [left, right], catalog, stem="forward"
    )
    reverse, reverse_rows, *_ = build(
        tmp_path, [right, left], catalog, stem="reverse"
    )
    assert forward_rows == reverse_rows
    assert (
        forward["outputs"]["clauses_jsonl"]["sha256"]
        == reverse["outputs"]["clauses_jsonl"]["sha256"]
    )
    assert forward["review_only_contract"] == {
        "automatic_label_or_decision_propagation_allowed": False,
        "semantic_equivalence_claimed": False,
        "one_review_replaces_occurrence_context_audit": False,
        "every_occurrence_must_be_checked_against_context": True,
        "labels_predictions_or_model_outputs_consumed": False,
    }
    for row in forward_rows:
        assert row["review_contract"][
            "automatic_label_or_decision_propagation_allowed"
        ] is False
        assert row["review_contract"]["semantic_equivalence_claimed"] is False
        assert row["review_contract"]["each_occurrence_context_must_be_checked"] is True


def test_catalog_provenance_and_per_occurrence_scope_are_committed(tmp_path, catalog):
    source = record(
        "R-CATALOG",
        "입찰참가자는 직접생산확인증명서를 제출하여야 한다. "
        "세부품명번호 1234567890을 확인한다.",
    )
    manifest, rows, *_ = build(tmp_path, [source], catalog)
    expected = {
        "path": catalog.path.resolve().relative_to(ROOT.resolve()).as_posix(),
        "sha256": catalog.sha256,
        "rows": catalog.total_rows,
    }
    assert manifest["catalog_provenance"] == expected
    relevant = [row for row in rows if row["group"] in {"v10-13", "v14-19"}]
    assert relevant
    for row in relevant:
        assert row["catalog_provenance"] == expected
        assert row["catalog_provenance_sha256"] == MODULE.sha256_object(expected)
        assert row["catalog_scope_variants"]
        variant_ids = {entry["variant_id"] for entry in row["catalog_scope_variants"]}
        assert {
            occurrence["catalog_scope_variant_id"] for occurrence in row["occurrences"]
        } <= variant_ids


def test_incomplete_source_and_clause_risk_facets_remain_occurrence_visible(
    tmp_path, catalog
):
    source = record(
        "R-INCOMPLETE",
        "소프트웨어사업 대기업 참여 제한의 예외를 검토한다.",
        complete=False,
    )
    _, rows, *_ = build(tmp_path, [source], catalog)
    row = next(value for value in rows if value["group"] == "v20")
    occurrence = row["occurrences"][0]
    assert occurrence["source_completeness"]["fully_observed"] is False
    assert "absence_group_with_incomplete_source" in occurrence["risk_flags"]
    assert "explicit_exception_language" in occurrence["risk_flags"]
    assert row["risk_facets"]["risk_tiers"] == {"critical": 1}
    assert row["risk_facets"]["risk_flags"][
        "absence_group_with_incomplete_source"
    ] == 1


def test_nested_decision_artifacts_and_duplicate_record_ids_are_rejected_atomically(
    tmp_path, catalog
):
    poisoned = record("R-1", "수행실적은 최근 3년 기준이다.")
    poisoned["meta"]["nested"] = {"Final Prediction": {"v2": 1}}
    with pytest.raises(ValueError, match="forbidden"):
        build(tmp_path, [poisoned], catalog, stem="poisoned")
    assert not (tmp_path / "poisoned.jsonl").exists()
    assert not (tmp_path / "poisoned.manifest.json").exists()

    duplicate = record("R-DUP", "수행실적은 최근 3년 기준이다.")
    with pytest.raises(ValueError, match="duplicate record id"):
        build(tmp_path, [duplicate, copy.deepcopy(duplicate)], catalog, stem="duplicate")
    assert not (tmp_path / "duplicate.jsonl").exists()
    assert not (tmp_path / "duplicate.manifest.json").exists()
    assert not list(tmp_path.glob(".*.sqlite3"))


def test_coordinate_or_derived_hash_corruption_blocks_publication(
    tmp_path, catalog, monkeypatch
):
    source = record("R-FORGE", "수행실적은 최근 3년 기준이다.")
    original = MODULE.template_clusters.build_record_profiles

    def forged_profiles(*args, **kwargs):
        profiles = copy.deepcopy(original(*args, **kwargs))
        profiles["v1-4"]["clauses"][0]["text_sha256"] = "0" * 64
        return profiles

    monkeypatch.setattr(
        MODULE.template_clusters, "build_record_profiles", forged_profiles
    )
    with pytest.raises(ValueError, match="coordinate/hash validation failed"):
        build(tmp_path, [source], catalog, stem="forged")
    assert not (tmp_path / "forged.jsonl").exists()
    assert not (tmp_path / "forged.manifest.json").exists()


def test_limit_is_explicit_and_manifest_census_is_self_consistent(tmp_path, catalog):
    manifest, rows, *_ = build(
        tmp_path,
        [
            record("R-1", "수행실적은 최근 3년 기준이다."),
            record("R-2", "직접생산확인증명서를 제출한다."),
        ],
        catalog,
        limit=1,
    )
    assert manifest["statistics"]["records"] == 1
    assert manifest["input"]["limit"] == 1
    assert manifest["statistics"]["unique_clause_fingerprints"] == len(rows)
    assert manifest["statistics"]["occurrences"] == sum(
        row["occurrence_count"] for row in rows
    )
    committed = {
        key: value
        for key, value in manifest.items()
        if key not in {"generated_at_utc", "content_sha256"}
    }
    assert manifest["content_sha256"] == MODULE.sha256_object(committed)
    with pytest.raises(ValueError, match="limit must be positive"):
        MODULE.build_clause_bank(
            [],
            output_jsonl=tmp_path / "bad.jsonl",
            manifest_output=tmp_path / "bad.json",
            catalog_index=catalog,
            limit=0,
        )


def test_interrupted_spool_recovery_matches_clean_clause_rows_and_preserves_spool(
    tmp_path, catalog
):
    records = [
        record("R-RECOVER-2", "수행실적은 최근 3년 기준이다."),
        record(
            "R-RECOVER-1",
            "직접생산확인증명서를 제출한다. 세부품명번호 1234567890을 확인한다.",
            complete=False,
        ),
    ]
    clean, clean_rows, clean_jsonl, *_ = build(
        tmp_path, records, catalog, stem="clean"
    )
    database = build_spool(tmp_path, records, catalog)
    database_sha = MODULE.file_sha256(database)
    output = tmp_path / "recovered.jsonl"
    manifest_path = tmp_path / "recovered.manifest.json"
    summary_path = tmp_path / "recovered.md"

    recovered = MODULE.recover_clause_bank(
        iter(records),
        database_path=database,
        output_jsonl=output,
        manifest_output=manifest_path,
        summary_output=summary_path,
        expected_records=2,
        catalog_index=catalog,
    )

    assert output.read_bytes() == clean_jsonl.read_bytes()
    assert [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()] == clean_rows
    assert recovered["groups"] == clean["groups"]
    assert recovered["statistics"] == clean["statistics"]
    assert recovered["recovery_provenance"]["database_sha256"] == database_sha
    assert recovered["recovery_provenance"]["database_mutated_or_deleted"] is False
    assert database.exists()
    assert MODULE.file_sha256(database) == database_sha
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == recovered
    assert summary_path.exists()


def test_recovery_fails_atomically_on_source_or_spool_hash_tampering(tmp_path, catalog):
    source = record("R-RECOVER-BAD", "수행실적은 최근 3년 기준이다.")
    database = build_spool(tmp_path, [source], catalog, stem="bad-spool")
    changed = copy.deepcopy(source)
    changed["docs"][0]["text"] += " 변경"
    with pytest.raises(ValueError, match="database/source identity mismatch"):
        MODULE.recover_clause_bank(
            [changed],
            database_path=database,
            output_jsonl=tmp_path / "source-bad.jsonl",
            manifest_output=tmp_path / "source-bad.manifest.json",
            expected_records=1,
            catalog_index=catalog,
        )
    assert not (tmp_path / "source-bad.jsonl").exists()
    assert not (tmp_path / "source-bad.manifest.json").exists()

    connection = sqlite3.connect(database)
    try:
        group_name, fingerprint, quote_sha = connection.execute(
            "SELECT group_name, clause_fingerprint, quote_sha256 "
            "FROM quote_variants LIMIT 1"
        ).fetchone()
        connection.execute(
            "UPDATE quote_variants SET raw_quote=raw_quote || ' 위조' "
            "WHERE group_name=? AND clause_fingerprint=? AND quote_sha256=?",
            (group_name, fingerprint, quote_sha),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ValueError, match="quote variant hash mismatch"):
        MODULE.recover_clause_bank(
            [source],
            database_path=database,
            output_jsonl=tmp_path / "spool-bad.jsonl",
            manifest_output=tmp_path / "spool-bad.manifest.json",
            expected_records=1,
            catalog_index=catalog,
        )
    assert not (tmp_path / "spool-bad.jsonl").exists()
    assert not (tmp_path / "spool-bad.manifest.json").exists()


def test_module_has_no_submission_runtime_saved_predictions_or_label_file_dependency():
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    forbidden = (
        "import " + "submission",
        "from " + "submission",
        "import pps",
        "from pps",
        "saved" + "_response",
        "dev_" + "labels",
        "train_" + "labels",
    )
    assert not any(token in source for token in forbidden)
