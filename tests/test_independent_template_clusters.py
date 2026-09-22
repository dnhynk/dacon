from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import pathlib

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "template_clusters.py"
SPEC = importlib.util.spec_from_file_location("independent_template_clusters", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


@pytest.fixture(scope="module")
def catalog():
    return MODULE.catalog_facts.CatalogIndex.load()


def record(
    record_id: str,
    text: str,
    *,
    contract_law: str = "국가계약법",
    complete: bool = True,
):
    meta_fields = MODULE.legal_facts.META_FIELDS
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
            meta_fields["contract_law"]: contract_law,
            meta_fields["work_type"]: "용역",
            meta_fields["contract_method"]: "제한경쟁",
            meta_fields["budget_won"]: 300_000_000,
            meta_fields["estimated_price_won"]: 272_727_273,
        },
        "anon_applied": True,
    }


def build_census(records, catalog, monkeypatch):
    monkeypatch.setattr(
        MODULE.catalog_facts.CatalogIndex,
        "load",
        classmethod(lambda cls, path=MODULE.catalog_facts.DEFAULT_CATALOG_PATH: catalog),
    )
    return MODULE.build_census(records)


def repeated_cluster(census, group="v1-4"):
    return next(
        row for row in census["groups"][group]["exact_clusters"] if row["member_count"] > 1
    )


def test_admissibility_rejects_top_level_and_nested_decision_or_model_artifacts():
    source = record("R-1", "수행실적은 최근 3년 기준이다.")
    attacks = []
    top_level = copy.deepcopy(source)
    top_level["production_prediction"] = {"v2": 1}
    attacks.append(top_level)
    nested_meta = copy.deepcopy(source)
    nested_meta["meta"]["Final Label"] = {"v2": 1}
    attacks.append(nested_meta)
    nested_doc = copy.deepcopy(source)
    nested_doc["docs"][0]["model-output"] = "0010"
    attacks.append(nested_doc)
    unicode_alias = copy.deepcopy(source)
    unicode_alias["meta"]["Ｖ２"] = 1
    attacks.append(unicode_alias)

    for attack in attacks:
        with pytest.raises(ValueError, match="forbidden"):
            MODULE.assert_admissible_record(attack)


def test_admissibility_enforces_supplied_organizer_schema_and_document_integrity():
    source = record("R-1", "수행실적은 최근 3년 기준이다.")
    unknown = copy.deepcopy(source)
    unknown["opaque_channel"] = "not an organizer field"
    with pytest.raises(ValueError, match="non-organizer"):
        MODULE.assert_admissible_record(unknown)

    duplicate_doc = copy.deepcopy(source)
    duplicate_doc["docs"].append(copy.deepcopy(duplicate_doc["docs"][0]))
    with pytest.raises(ValueError, match="duplicate/invalid doc_id"):
        MODULE.assert_admissible_record(duplicate_doc)

    wrong_length = copy.deepcopy(source)
    wrong_length["docs"][0]["n_chars"] = 1
    with pytest.raises(ValueError, match="n_chars mismatch"):
        MODULE.assert_admissible_record(wrong_length)

    wrong_completeness = copy.deepcopy(source)
    wrong_completeness["input_completeness"]["제안요청서"] = "true"
    with pytest.raises(ValueError, match="boolean map"):
        MODULE.assert_admissible_record(wrong_completeness)


def test_exact_clusters_are_order_deterministic_and_retain_every_member_coordinate(
    catalog, monkeypatch
):
    first = record("R-B", "입찰 참가 안내\n\n수행실적은 계약일로부터 3년 동안 인정한다.")
    second = record("R-A", "입찰 참가 안내\n\n수행실적은 계약일로부터 5년 동안 인정한다.")
    forward = build_census([first, second], catalog, monkeypatch)
    reverse = build_census([second, first], catalog, monkeypatch)

    assert forward["content_sha256"] == reverse["content_sha256"]
    left = copy.deepcopy(forward)
    right = copy.deepcopy(reverse)
    left.pop("generated_at_utc")
    right.pop("generated_at_utc")
    assert left == right

    cluster = repeated_cluster(forward)
    assert cluster["representative"]["record_id"] == "R-A"
    assert cluster["automatic_decision_propagation_allowed"] is False
    assert cluster["semantic_equivalence_claimed"] is False
    sources = {row["id"]: row for row in (first, second)}
    assert [row["record_id"] for row in cluster["members"]] == ["R-A", "R-B"]
    for member in cluster["members"]:
        coordinates = member["coordinates"]
        assert coordinates
        assert member["coordinates_sha256"] == MODULE.sha256_object(coordinates)
        source = sources[member["record_id"]]
        for coordinate in coordinates:
            text = source["docs"][coordinate["doc_index"]]["text"]
            quote = text[coordinate["start"] : coordinate["end"]]
            assert hashlib.sha256(quote.encode("utf-8")).hexdigest() == coordinate[
                "text_sha256"
            ]
            assert hashlib.sha256(text.encode("utf-8")).hexdigest() == coordinate[
                "source_doc_sha256"
            ]


def test_metadata_boundary_prevents_exact_cluster_merge(catalog, monkeypatch):
    text = "수행실적은 최근 3년 기준이다."
    national = record("R-A", text, contract_law="국가계약법")
    local = record("R-B", text, contract_law="지방계약법")
    census = build_census([national, local], catalog, monkeypatch)
    group = census["groups"]["v1-4"]
    assert group["statistics"]["records"] == 2
    assert group["statistics"]["unique_exact_clusters"] == 2
    assert group["statistics"]["repeated_exact_clusters"] == 0


def test_near_boundary_is_invariant_to_exact_clause_multiset_order(catalog):
    source = record(
        "R-ORDER",
        "수행실적은 최근 3년 기준이며 모두 충족해야 한다.\n\n"
        "납품실적은 최근 5년 기준 또는 동등 실적으로 갈음한다.",
    )
    profile = MODULE.build_record_profiles(source, catalog_index=catalog)["v1-4"]
    clauses = profile["clauses"]
    assert len(clauses) >= 2
    forward = MODULE._near_boundary(
        "v1-4",
        profile["metadata_signature"],
        profile["source_completeness"],
        clauses,
    )
    reverse = MODULE._near_boundary(
        "v1-4",
        profile["metadata_signature"],
        profile["source_completeness"],
        list(reversed(clauses)),
    )
    assert forward == reverse


def test_empty_retrieval_is_record_specific_critical_review_not_an_absence_claim(
    catalog, monkeypatch
):
    census = build_census(
        [record("R-A", "일반 안내문"), record("R-B", "일반 안내문")],
        catalog,
        monkeypatch,
    )
    for group in census["groups"].values():
        assert group["statistics"]["unique_exact_clusters"] == 2
        assert group["statistics"]["repeated_exact_clusters"] == 0
        assert group["statistics"]["risk_tiers"] == {"critical": 2}
        for cluster in group["exact_clusters"]:
            assert cluster["members"][0]["risk_tier"] == "critical"
            assert "no_relevant_clause_retrieved" in cluster["members"][0]["risk_flags"]


def test_incomplete_source_is_preserved_as_risk_and_boundary(catalog, monkeypatch):
    text = "소프트웨어사업 대기업 참여 제한을 검토한다."
    complete = record("R-A", text, complete=True)
    incomplete = record("R-B", text, complete=False)
    census = build_census([complete, incomplete], catalog, monkeypatch)
    group = census["groups"]["v20"]
    assert group["statistics"]["unique_exact_clusters"] == 2
    member = next(
        member
        for cluster in group["exact_clusters"]
        for member in cluster["members"]
        if member["record_id"] == "R-B"
    )
    assert member["risk_tier"] == "critical"
    assert "absence_group_with_incomplete_source" in member["risk_flags"]


def test_validate_profile_detects_coordinate_identity_and_derived_hash_corruption(catalog):
    source = record("R-1", "수행실적은 최근 3년 기준이다.")
    profile = MODULE.build_record_profiles(source, catalog_index=catalog)["v1-4"]
    assert MODULE.validate_profile(source, profile) == []

    forged = copy.deepcopy(profile)
    forged["clauses"][0]["doc_id"] = "WRONG"
    forged["clauses"][0]["normalized_text"] = "forged"
    forged["clauses"][0]["clause_fingerprint"] = "0" * 64
    errors = MODULE.validate_profile(source, forged)
    assert any(value.startswith("document_id_mismatch") for value in errors)
    assert any(value.startswith("normalized_text_mismatch") for value in errors)
    assert any(value.startswith("clause_fingerprint_mismatch") for value in errors)
    assert "clause_multiset_mismatch" in errors


def test_duplicate_record_id_is_rejected_before_ambiguous_membership(catalog, monkeypatch):
    left = record("R-1", "수행실적은 최근 3년 기준이다.")
    right = record("R-1", "수행실적은 최근 5년 기준이다.")
    with pytest.raises(ValueError, match="duplicate record id"):
        build_census([left, right], catalog, monkeypatch)


def test_census_commits_catalog_provenance_and_never_authorizes_label_propagation(
    catalog, monkeypatch
):
    census = build_census(
        [
            record("R-A", "수행실적은 최근 3년 기준이다."),
            record("R-B", "수행실적은 최근 5년 기준이다."),
        ],
        catalog,
        monkeypatch,
    )
    assert census["input"]["catalog"] == {
        "path": catalog.path.resolve().relative_to(ROOT.resolve()).as_posix(),
        "sha256": catalog.sha256,
        "rows": catalog.total_rows,
    }
    contract = census["review_only_contract"]
    assert contract["automatic_decision_propagation_allowed"] is False
    assert contract["semantic_equivalence_claimed_for_exact_clusters"] is False
    assert contract["semantic_equivalence_claimed_for_near_candidates"] is False
    assert contract["labels_predictions_or_model_responses_consumed"] is False
    for group in census["groups"].values():
        for cluster in group["exact_clusters"]:
            assert cluster["automatic_decision_propagation_allowed"] is False
            assert cluster["safe_for"] == "review_batching_only"
        for candidate_set in group["near_duplicate_candidate_sets"]:
            assert candidate_set["automatic_decision_propagation_allowed"] is False
            assert candidate_set["safe_for"] == "review_routing_only"


def test_content_hash_summary_and_atomic_streaming_write(catalog, monkeypatch, tmp_path):
    census = build_census([record("R-1", "일반 안내문")], catalog, monkeypatch)
    committed = {
        key: value
        for key, value in census.items()
        if key not in {"generated_at_utc", "content_sha256"}
    }
    assert census["content_sha256"] == MODULE.sha256_object(committed)
    summary = MODULE.render_summary_markdown(census)
    assert "라벨 자동 전파는 금지" in summary
    assert "법적 결론이 같다는 뜻이 아니다" in summary
    assert census["content_sha256"] in summary
    output = tmp_path / "census.json"
    MODULE.write_census(census, output)
    assert json.loads(output.read_text(encoding="utf-8")) == census
    assert not (tmp_path / ".census.json.tmp").exists()


def test_module_has_no_competition_runtime_or_saved_prediction_dependency():
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    forbidden = (
        "import " + "submission",
        "from " + "submission",
        "import pps",
        "from pps",
        "saved" + "_response",
        "development" + "_predictions",
    )
    assert not any(token in source for token in forbidden)
