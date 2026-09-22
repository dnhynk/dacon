from __future__ import annotations

import csv
import copy
import gzip
import importlib.util
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "qualification_context.py"
SPEC = importlib.util.spec_from_file_location("independent_qualification_context", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def dev_records():
    with gzip.open(ROOT / "data_open" / "dev.jsonl.gz", "rt", encoding="utf-8") as handle:
        return {row["id"]: row for row in map(json.loads, handle)}


def test_irrelevant_groups_do_not_get_qualification_context():
    record = next(iter(dev_records().values()))
    assert MODULE.build_qualification_context(record, "v1-4") is None
    assert MODULE.build_qualification_context(record, "v9") is None


def test_dev_relevant_contexts_are_bounded_and_coordinate_valid():
    catalog = MODULE.qualification_facts.CatalogReference.load()
    for record in dev_records().values():
        prepared = MODULE.prepare_qualification_input(record, catalog=catalog)
        for group in MODULE.GROUP_FAMILIES:
            context = MODULE.build_qualification_context(
                record, group, prepared=prepared, catalog=catalog
            )
            assert context is not None
            assert MODULE.validate_qualification_context(record, context) == []
            assert len(MODULE.render_qualification_context(context)) <= MODULE.DEFAULT_MAX_CHARS
            assert context["semantic_role"].endswith("without_decisions")


def test_official_purchase_evidence_is_retained_in_source_segments():
    records = dev_records()
    with (ROOT / "data_open" / "dev_labels.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        labels = list(csv.DictReader(handle))
    catalog = MODULE.qualification_facts.CatalogReference.load()
    relevant = 0
    covered = 0
    misses = []
    for row in labels:
        record = records[row["id"]]
        prepared = MODULE.prepare_qualification_input(record, catalog=catalog)
        contexts = {
            group: MODULE.build_qualification_context(
                record, group, prepared=prepared, catalog=catalog
            )
            for group in MODULE.GROUP_FAMILIES
        }
        for number in range(10, 21):
            evidence = row[f"e{number}"].strip()
            if not evidence or number == 19:
                continue
            relevant += 1
            group = "v10-13" if number <= 13 else "v14-19" if number <= 19 else "v20"
            source = "\n".join(
                segment["quote"] for segment in contexts[group]["source_segments"]
            )
            if evidence in source:
                covered += 1
            else:
                misses.append((row["id"], f"v{number}"))
    assert relevant == 6
    assert covered == relevant, misses


def test_all_official_positive_qualification_premises_survive_compaction():
    records = dev_records()
    with (ROOT / "data_open" / "dev_labels.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        labels = list(csv.DictReader(handle))
    catalog = MODULE.qualification_facts.CatalogReference.load()
    relevant = 0
    covered = 0
    misses = []
    for row in labels:
        record = records[row["id"]]
        prepared = MODULE.prepare_qualification_input(record, catalog=catalog)
        contexts = {
            group: MODULE.build_qualification_context(
                record, group, prepared=prepared, catalog=catalog
            )
            for group in MODULE.GROUP_FAMILIES
        }
        for number in (*range(10, 19), 20):
            if int(row[f"v{number}"]) != 1:
                continue
            relevant += 1
            group = "v10-13" if number <= 13 else "v14-19" if number <= 18 else "v20"
            context = contexts[group]
            families = {fact["family"] for fact in context["facts"]}
            code = bool(
                families
                & {"bid_product_code_mentions", "catalog_product_name_candidates"}
            )
            direct = "direct_production_requirements" in families
            enterprise = "enterprise_size_clauses" in families
            price = (
                context["metadata_facts"].get("estimated_price_won", {}).get("status")
                == "present"
            )
            software = "software_object_task_relations" in families
            signal = {
                10: code,
                11: code,
                12: direct,
                13: code and enterprise,
                14: price and enterprise,
                15: price and enterprise,
                16: price,
                17: price and enterprise,
                18: price,
                20: software,
            }[number]
            covered += int(signal)
            if not signal:
                misses.append((row["id"], f"v{number}"))
    assert relevant == 63
    assert covered == relevant, misses


def test_source_has_no_submission_prediction_or_label_dependency():
    source = MODULE_PATH.read_text(encoding="utf-8").casefold()
    forbidden = (
        "import submission",
        "from submission",
        "saved" + "_response",
        "production_prediction",
        "dev_labels.csv",
    )
    assert not any(token in source for token in forbidden)


def _rehash(context):
    context["context_sha256"] = MODULE.sha256_object(MODULE._hashable_context(context))
    context["bounds"]["rendered_chars"] = 0
    for _ in range(6):
        size = len(MODULE.canonical_json(context))
        if context["bounds"]["rendered_chars"] == size:
            break
        context["bounds"]["rendered_chars"] = size


def test_validator_rejects_coordinate_and_manifest_tampering_even_if_rehashed():
    record = next(iter(dev_records().values()))
    context = MODULE.build_qualification_context(record, "v10-13")
    assert context is not None

    document_fact = next(
        fact
        for fact in context["facts"]
        if fact["source_coordinate"]["source_kind"] == "document"
    )
    tampered = copy.deepcopy(context)
    target = next(
        fact for fact in tampered["facts"] if fact["fact_id"] == document_fact["fact_id"]
    )
    target["source_coordinate"]["source_doc_sha256"] = "0" * 64
    _rehash(tampered)
    assert any(
        "document_hash_mismatch" in error
        for error in MODULE.validate_qualification_context(record, tampered)
    )

    tampered = copy.deepcopy(context)
    tampered["organizer_source"]["documents"][0]["doc_id"] = "forged"
    _rehash(tampered)
    assert "document_manifest_mismatch" in MODULE.validate_qualification_context(
        record, tampered
    )


def test_validator_rejects_meta_coordinate_tampering_even_if_rehashed():
    records = dev_records().values()
    context = None
    record = None
    for candidate in records:
        built = MODULE.build_qualification_context(candidate, "v10-13")
        if built and any(
            fact["source_coordinate"]["source_kind"] == "meta"
            for fact in built["facts"]
        ):
            record, context = candidate, built
            break
    assert record is not None and context is not None
    tampered = copy.deepcopy(context)
    fact = next(
        fact
        for fact in tampered["facts"]
        if fact["source_coordinate"]["source_kind"] == "meta"
    )
    fact["source_coordinate"]["source_value_sha256"] = "0" * 64
    _rehash(tampered)
    assert any(
        "meta_source_hash_mismatch" in error
        for error in MODULE.validate_qualification_context(record, tampered)
    )


def test_budget_truncation_counts_are_self_consistent():
    record = dev_records()["PPS-DEV-19"]
    context = MODULE.build_qualification_context(record, "v14-19")
    assert context is not None
    assert context["bounds"]["budget_truncated"] is True
    assert context["bounds"]["rendered_chars"] == len(MODULE.canonical_json(context))
    assert context["bounds"]["included_facts"] == len(context["facts"])
    assert sum(row["included"] for row in context["omissions"].values()) == len(
        context["facts"]
    )
    assert sum(row["available"] for row in context["omissions"].values()) == context[
        "bounds"
    ]["available_facts"]
    assert MODULE.validate_qualification_context(record, context) == []


def test_context_keeps_principal_object_separate_from_qualification_product():
    # Official source regression: the named research service is the principal
    # object, while unrelated event-service codes occur only inside a copied
    # direct-production qualification.  The latter must not corroborate itself
    # as the procurement object.
    record = dev_records()["PPS-DEV-053"]
    context = MODULE.build_qualification_context(record, "v14-19")
    binding = next(
        row
        for row in context["relation_summaries"]["direct_production_target_bindings"]
        if row["clause_codes"] == ["8014198801", "8014199001"]
    )
    assert binding["bid_object_name_candidate_codes"] == []
    assert binding["declared_bid_object_names"] == ["농림수산연구조사서비스"]
    assert binding["relation"] == "clause_code_without_independent_bid_object_match"
    assert binding["binding_applicability"] == "requires_object_scope_adjudication"
    assert MODULE.validate_qualification_context(record, context) == []


def test_context_preserves_catalog_provenance_and_rejects_unqualified_prepared_input():
    record = next(iter(dev_records().values()))
    catalog = MODULE.qualification_facts.CatalogReference.load()
    prepared = MODULE.prepare_qualification_input(record, catalog=catalog)
    context = MODULE.build_qualification_context(
        record, "v10-13", prepared=prepared, catalog=catalog
    )
    reference = context["extractor"]["reference_sources"][
        "competitive_product_catalog"
    ]
    assert reference["sha256"] == catalog.sha256
    assert reference["coded_rows"] == len(catalog)

    for key, bad_value in (
        ("schema_version", "foreign.schema"),
        ("semantic_role", "labeler"),
        ("label_decisions_present", True),
    ):
        tampered = copy.deepcopy(prepared)
        tampered[key] = bad_value
        try:
            MODULE.build_qualification_context(
                record, "v10-13", prepared=tampered, catalog=catalog
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"unqualified prepared input accepted: {key}")


def test_missing_metadata_source_field_remains_a_valid_auditable_absence():
    record = copy.deepcopy(next(iter(dev_records().values())))
    source_key = MODULE.qualification_facts.META_FIELDS["article_summary"]
    record["meta"].pop(source_key, None)
    context = MODULE.build_qualification_context(record, "v10-13")
    fact = context["metadata_facts"]["article_summary"]
    assert fact["status"] == "missing_or_unentered"
    assert fact["value"] is None
    assert fact["semantic_role"] == "search_or_classification_summary_only"
    assert fact["operative_bid_qualification"] is False
    assert fact["establishes_statutory_exception"] is False
    assert MODULE.validate_qualification_context(record, context) == []


def _closed_rfp_checklist_record():
    return {
        "id": "compact-closed-rfp-checklist",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "2. 입찰참가자격: 다음 사항을 모두 충족한 업체\n"
                    "가. 소기업 또는 소상공인\n"
                    "나. 세부품명번호 6010989901, 7215409902, 8014198801로 등록한 업체\n"
                    "사업안내 및 제출서류의 세부내용은 제안요청서 참조\n\n"
                    "입찰참가등록 신청서류\n"
                    "산업디자인전문회사 신고확인증, 중소기업확인서, "
                    "직접생산확인증명서 각 1부\n"
                ),
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": False},
        "dropped_doc_counts": {"제안요청서": 1},
    }


def test_compact_context_preserves_source_closure_scope_and_nonconstitutive_binding():
    record = _closed_rfp_checklist_record()
    context = MODULE.build_qualification_context(record, "v10-13")
    assert context is not None
    closure = context["relation_summaries"]["qualification_source_closure"]
    assert closure["status"] == (
        "closed_qualification_list_without_detected_external_delegation"
    )
    assert closure["delegation_marker_fact_ids"] == []
    assert closure["other_scope_reference_marker_fact_ids"]
    assert closure["scope_assessments"]["bid_qualification"][
        "missing_document_materiality"
    ] == "not_material_to_closed_bid_qualification_scope"
    assert closure["scope_assessments"][
        "business_task_submission_or_evaluation_details"
    ]["missing_document_materiality"] == (
        "potentially_material_within_this_nonqualification_scope_only"
    )

    source_markers = [
        fact
        for fact in context["facts"]
        if fact["family"] == "qualification_source_markers"
    ]
    assert {
        fact["marker_type"] for fact in source_markers
    } == {
        "closed_cumulative_qualification_list",
        "external_nonqualification_reference",
    }
    assert all(fact["source_coordinate"].get("segment_id") for fact in source_markers)

    direct = next(
        fact
        for fact in context["facts"]
        if fact["family"] == "direct_production_requirements"
    )
    assert direct["qualification_constitutive"] is False
    assert direct["ambiguities"] == []
    binding = context["relation_summaries"][
        "direct_production_target_bindings"
    ][0]
    assert binding["relation"] == "binding_not_applicable"
    assert binding["binding_applicability"] == "not_applicable"
    assert binding["qualification_constitutive"] is False
    assert MODULE.validate_qualification_context(record, context) == []


def test_exact_product_code_inventory_survives_fact_budget_truncation():
    record = _closed_rfp_checklist_record()
    prepared = MODULE.prepare_qualification_input(record)
    candidates = MODULE._fact_candidates("v10-13", prepared)

    # Set the budget immediately above the mandatory relation-only context.
    # This intentionally omits most detailed facts while the exact-code index
    # remains mandatory and auditable.
    probe = MODULE._assemble(
        record, "v10-13", prepared, (), candidates, 100_000
    )
    max_chars = len(MODULE.canonical_json(probe)) + 500
    context = MODULE.build_qualification_context(
        record, "v10-13", prepared=prepared, max_chars=max_chars
    )
    assert context is not None
    assert context["bounds"]["budget_truncated"] is True
    inventory = context["relation_summaries"]["exact_product_code_inventory"]
    assert {row["code"] for row in inventory} == {
        "6010989901",
        "7215409902",
        "8014198801",
    }
    assert all(row["mentions"] for row in inventory)
    included_codes = {
        fact["code"]
        for fact in context["facts"]
        if fact["family"] == "bid_product_code_mentions"
    }
    assert included_codes < {"6010989901", "7215409902", "8014198801"}
    assert MODULE.validate_qualification_context(record, context) == []


def test_explicit_qualification_delegation_stays_material_in_compact_context():
    record = _closed_rfp_checklist_record()
    record["id"] = "compact-explicit-delegation"
    record["docs"][0]["text"] += (
        "\n입찰참가자격의 세부사항은 제안요청서 참조\n"
    )
    context = MODULE.build_qualification_context(record, "v10-13")
    closure = context["relation_summaries"]["qualification_source_closure"]
    assert closure["status"] == "external_qualification_delegation_observed"
    assert closure["delegation_marker_fact_ids"]
    assert closure["scope_assessments"]["bid_qualification"][
        "missing_document_materiality"
    ] == "potentially_material_to_bid_qualification_scope"
    assert any(
        fact["family"] == "qualification_source_markers"
        and fact["marker_type"] == "external_qualification_delegation"
        for fact in context["facts"]
    )
    assert MODULE.validate_qualification_context(record, context) == []


def test_v20_context_keeps_enumerated_closure_and_procurement_title_relation():
    record = {
        "id": "compact-enumerated-v20",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "1. 입찰에 부치는 사항\n\n"
                    "계 약 건 명\n\n"
                    "차세대 경영정보시스템 구축(일반경쟁·50억원미만)\n\n"
                    "5. 입찰참가자격\n\n"
                    "① 국가계약법령에 따른 경쟁입찰 자격을 갖춘 자\n\n"
                    "② 소프트웨어사업자(컴퓨터관련서비스사업)로 등록한 자\n\n"
                    "6. 제출서류\n\n"
                    "제안서 1부\n\n"
                    "7. 용역 완료일 : 제안요청서 참조\n"
                ),
            }
        ],
        "meta": {
            "업무구분": "일반용역",
            "배정예산금액": 5_450_000_000,
            "입찰추정가격": 4_954_545_455,
        },
        "input_completeness": {"완전관측": False, "무탈락": False},
        "dropped_doc_counts": {"제안요청서": 1},
    }
    context = MODULE.build_qualification_context(record, "v20")

    assert context["schema_version"] == "dacon.independent.qualification_context.v4"
    assert context["extractor"]["schema_version"] == (
        "dacon.independent.qualification_facts.v4"
    )
    closure = context["relation_summaries"]["qualification_source_closure"]
    assert closure["status"] == (
        "closed_qualification_list_without_detected_external_delegation"
    )
    assert closure["scope_assessments"]["bid_qualification"][
        "missing_document_materiality"
    ] == "not_material_to_closed_bid_qualification_scope"
    assert context["relation_summaries"]["software_restriction_linkage"][
        "status"
    ] == "software_work_observed_restriction_not_extracted"
    assert {
        row.get("marker_type")
        for row in context["facts"]
        if row["family"] == "qualification_source_markers"
    } == {
        "closed_enumerated_qualification_section",
        "external_nonqualification_reference",
    }
    assert any(
        row["family"] == "software_object_task_relations"
        and row["relation_role"] == "contract_software_object_task_candidate"
        and row["task_types"] == ["build"]
        for row in context["facts"]
    )
    assert MODULE.validate_qualification_context(record, context) == []
