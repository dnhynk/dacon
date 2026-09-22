import copy
import hashlib
import importlib.util
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "qualification_facts.py"
SPEC = importlib.util.spec_from_file_location(
    "independent_qualification_facts", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def fixture_record():
    qualification = """물품 구매 입찰공고

1. 입찰에 부치는 사항
가. 사업명: 기타행사기획및대행서비스 구매
나. 세부품명번호: 8014199001

2. 입찰참가자격
아래의 요건을 모두 갖춘 자이어야 합니다.
가. 소기업 또는 소상공인으로서 소기업·소상공인확인서를 소지한 업체
나. 세부품명번호 8014199001에 대한 직접생산확인증명서를 전자입찰서 제출 마감일 전일까지 발급받아 유효기간 내에 있는 업체
다. 다만, 판로지원법 제33조에 따라 중소기업자로 간주되는 특별법인은 입찰에 참가할 수 있습니다.
"""
    document_list = """제안서 제출서류
1. 사업자등록증 1부
2. 직접생산확인증명서 1부
3. 중소기업확인서 1부
"""
    postaward = """계약 이행 안내
낙찰자는 계약 체결 후 직접생산확인증명서를 제출하여야 합니다.
"""
    reference = """관련 법령 및 참고 규정
중소기업제품 구매촉진 및 판로지원에 관한 법률의 직접생산 기준
"""
    software = """과업지시서

1. 사업명: 통합 정보시스템 구축 및 유지관리 용역
2. 과업내용: 계약상대자는 정보시스템을 개발·구축하고 운영, 유지보수, 패치 및 기술지원을 수행한다.
3. 입찰참가자격: 본 사업은 대기업인 소프트웨어사업자가 참여할 수 없다.
"""
    boilerplate = """교육 및 보안 참고사항
교육생은 코딩 실습 프로그램을 도구로 사용한다.
요구사항 목록에는 정보시스템 접근권한 현황과 프로그램 소스코드 유출금지를 기재한다.
"""
    texts = [qualification, document_list, postaward, reference, software, boilerplate]
    return {
        "id": "qualification-fixture",
        "docs": [
            {"doc_id": f"D{index}", "type": "공고문" if index < 4 else "과업지시서", "text": text}
            for index, text in enumerate(texts)
        ],
        "meta": {
            "적용계약법": "국가계약법",
            "업무구분": "일반용역",
            "계약방법": "제한경쟁",
            "낙찰방법": "협상에의한계약",
            "배정예산금액": 330_000_000,
            "입찰추정가격": 300_000_000,
            "정보화사업여부": "미입력",
            "세부품명번호목록": "기타행사기획및대행서비스[8014199001]",
            "면허업종제한목록": "소프트웨어사업자(컴퓨터관련서비스사업)(1468)",
            "업종제한여부": "Y",
            "조항호내용": "소기업, 소상공인 제한",
            "공고게시일자": "20260101",
            "개찰예정일자": "20260120",
        },
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }


def walk_spans(value):
    if isinstance(value, dict):
        if {"source_kind", "start", "end", "text", "text_sha256"} <= set(value):
            yield value
        for child in value.values():
            yield from walk_spans(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_spans(child)


def test_supplied_catalog_reference_is_validated_and_hashed():
    catalog = MODULE.CatalogReference.load()
    assert len(catalog) == 615
    assert catalog.total_rows == 616
    assert catalog.sha256 == hashlib.sha256(MODULE.DEFAULT_CATALOG_PATH.read_bytes()).hexdigest()
    row = catalog.get("8014199001")
    assert row["detail_name"] == "기타행사기획및대행서비스"
    assert row["source_coordinate"]["row_number"] > 1
    assert catalog.codes_for_exact_name("기타행사기획및대행서비스") == ["8014199001"]


def test_every_document_and_meta_fact_has_verbatim_coordinates_and_hashes():
    record = fixture_record()
    result = MODULE.extract_qualification_facts(record)
    assert MODULE.validate_fact_coordinates(record, result) == []
    assert result["label_decisions_present"] is False
    assert result["semantic_role"] == "source_grounded_facts_only"
    assert json.loads(json.dumps(result, ensure_ascii=False))["record_id"] == record["id"]

    seen_document = seen_meta = False
    for span in walk_spans(result["facts"]):
        if span["source_kind"] == "document":
            source = record["docs"][span["doc_index"]]["text"]
            assert source[span["start"] : span["end"]] == span["text"]
            assert hashlib.sha256(source.encode()).hexdigest() == span["source_doc_sha256"]
            seen_document = True
        else:
            field = span["path"].split(".", 1)[1]
            source = str(record["meta"][field])
            assert source[span["start"] : span["end"]] == span["text"]
            assert hashlib.sha256(source.encode()).hexdigest() == span["source_value_sha256"]
            seen_meta = True
        assert hashlib.sha256(span["text"].encode()).hexdigest() == span["text_sha256"]
    assert seen_document and seen_meta


def test_direct_production_roles_timing_and_current_bid_object_binding_stay_distinct():
    result = MODULE.extract_qualification_facts(fixture_record())
    facts = result["facts"]["direct_production_requirements"]
    roles = {fact["clause_role"] for fact in facts}
    assert "operative_bid_qualification_candidate" in roles
    assert "proof_document_submission_list" in roles
    assert "post_award_or_contract_candidate" in roles
    assert "generic_legal_reference_candidate" in roles

    operative = next(
        fact
        for fact in facts
        if fact["clause_role"] == "operative_bid_qualification_candidate"
    )
    assert operative["bound_product_codes"] == ["8014199001"]
    assert any(
        row["timing"] == "before_bid_or_quote_deadline"
        for row in operative["timing_mentions"]
    )
    binding = next(
        row
        for row in result["relations"]["direct_production_target_bindings"]
        if row["direct_production_fact_id"] == operative["fact_id"]
    )
    assert binding["relation"] == "exact_code_overlap_with_bid_metadata"
    assert binding["is_label_decision"] is False


def test_catalog_name_family_is_a_routed_ambiguity_not_an_exact_code_finding():
    record = {
        "id": "name-family",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": "사업명: 지역 문화공연 운영 대행 용역",
            }
        ],
        "meta": {"입찰추정가격": 70_000_000},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }
    result = MODULE.extract_facts(record)
    candidates = result["facts"]["catalog_product_name_candidates"]
    family = next(
        fact
        for fact in candidates
        if fact["name_family"] == "event_planning_or_operation_service"
    )
    assert family["exact_catalog_identity"] is False
    assert len(family["candidate_catalog_codes"]) == 4
    assert family["ambiguities"] == ["name_family_does_not_select_one_exact_catalog_row"]


def test_event_family_does_not_match_the_verb_exercise_or_cross_sentences():
    record = {
        "id": "not-an-event-service",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": "권리를 행사하는 부당행위를 금지한다. 운영 기준은 별도로 정한다.",
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }
    result = MODULE.extract_facts(record)
    assert not any(
        fact.get("name_family") == "event_planning_or_operation_service"
        for fact in result["facts"]["catalog_product_name_candidates"]
    )


def test_information_system_family_does_not_bind_to_workspace_or_cross_lines():
    record = {
        "id": "not-an-information-system-service",
        "docs": [
            {
                "doc_id": "D0",
                "type": "과업지시서",
                "text": (
                    "전산시스템 등의 콜센터 업무공간 유지 및 환경구축\n"
                    "모든 상담사 PC에는 정보보호 S/W를 설치한다.\n"
                ),
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }
    result = MODULE.extract_facts(record)
    assert not any(
        fact.get("name_family")
        == "information_system_development_or_maintenance_service"
        for fact in result["facts"]["catalog_product_name_candidates"]
    )


def test_information_system_family_keeps_same_clause_object_action_binding():
    record = {
        "id": "actual-information-system-service",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": "사업명: 통합 정보시스템 구축 및 유지관리 용역",
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }
    result = MODULE.extract_facts(record)
    assert any(
        fact.get("name_family")
        == "information_system_development_or_maintenance_service"
        for fact in result["facts"]["catalog_product_name_candidates"]
    )


def test_declared_code_free_procurement_name_records_full_catalog_exact_miss():
    record = {
        "id": "declared-name-no-catalog-row",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": "입찰에 부치는 사항\n세부품명: 연구조사서비스\n과업: ESG 경영전략 연구",
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }
    result = MODULE.extract_facts(record)
    fact = next(
        row
        for row in result["facts"]["catalog_product_name_candidates"]
        if row.get("matched_name") == "연구조사서비스"
    )
    assert fact["candidate_catalog_codes"] == []
    assert fact["catalog_comparison_scope"] == "all_exact_names_in_supplied_catalog"
    assert fact["exact_catalog_match_count"] == 0


def test_direct_production_proof_list_is_not_promoted_to_qualification():
    record = {
        "id": "direct-production-proof-list",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "입찰참가등록 신청서류\n"
                    "산업디자인 신고확인증, 중소기업확인서, "
                    "직접생산확인증명서 각 1부\n"
                ),
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }
    result = MODULE.extract_facts(record)
    fact = result["facts"]["direct_production_requirements"][0]
    assert fact["clause_role"] == "proof_document_submission_list"
    assert fact["binding_scope"] == "unbound_proof_document"
    assert fact["ambiguities"] == []
    assert fact["eligibility_effect"] == "document_submission_only"
    assert fact["qualification_constitutive"] is False
    binding = result["relations"]["direct_production_target_bindings"][0]
    assert binding["relation"] == "binding_not_applicable"
    assert binding["binding_applicability"] == "not_applicable"
    assert binding["qualification_constitutive"] is False


def test_direct_production_mandatory_possession_remains_operative_without_code():
    record = {
        "id": "direct-production-operative",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "3. 입찰참가 자격\n"
                    "해당업종 및 세부품명의 직접생산확인증명서를 보유한 업체여야 합니다.\n"
                ),
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }
    fact = MODULE.extract_facts(record)["facts"]["direct_production_requirements"][0]
    assert fact["clause_role"] == "operative_bid_qualification_candidate"
    assert fact["eligibility_effect"] == "mandatory_possession_or_verification"
    assert fact["qualification_constitutive"] is True


def test_competitive_product_regime_name_is_not_a_bidder_size_clause():
    record = {
        "id": "regime-name-not-enterprise-limit",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "중소기업자간 경쟁제품 직접생산확인기준에 따라 "
                    "동영상제작서비스 직접생산확인증명서를 소지한 업체"
                ),
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }
    result = MODULE.extract_facts(record)
    assert result["facts"]["enterprise_size_clauses"] == []


def test_product_code_after_blank_line_keeps_declared_object_role():
    record = {
        "id": "blank-line-product-code",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": "세부품명:\n\n디자인서비스(8214150201)",
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }
    result = MODULE.extract_facts(record)
    fact = next(
        row
        for row in result["facts"]["bid_product_code_mentions"]
        if row["code"] == "8214150201"
    )
    assert fact["source_role"] == "procurement_object_candidate"


def test_direct_production_vendor_scope_is_an_operative_binding():
    record = {
        "id": "direct-production-업체로서",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "세부품명: 디자인서비스(8214150201)\n\n"
                    "입찰참가자격: 디자인서비스(8214150201)의 "
                    "직접생산확인증명서를 발급받은 업체로서 참가하여야 한다."
                ),
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }
    result = MODULE.extract_facts(record)
    direct = next(
        row
        for row in result["facts"]["direct_production_requirements"]
        if "8214150201" in row["bound_product_codes"]
    )
    assert direct["clause_role"] == "operative_bid_qualification_candidate"
    relation = next(
        row
        for row in result["relations"]["direct_production_target_bindings"]
        if row["direct_production_fact_id"] == direct["fact_id"]
    )
    assert relation["relation"] == "exact_code_overlap_with_declared_bid_object"


def test_nonprofit_cost_table_note_is_not_a_statutory_eligibility_exception():
    record = {
        "id": "nonprofit-cost-note",
        "docs": [
            {
                "doc_id": "D0",
                "type": "과업지시서",
                "text": "용역원가 산출표\n일반관리비 5%\n이윤 10%(비영리단체 제외)\n부가가치세 10%",
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }
    result = MODULE.extract_facts(record)
    fact = next(
        row
        for row in result["facts"]["statutory_exception_clauses"]
        if row["exception_type"] == "nonprofit_eligibility_path"
    )
    assert fact["clause_role"] == "financial_calculation_note"
    assert fact["establishment_status"] == "excluded_noneligibility_context"


def test_closed_qualification_list_and_external_delegation_stay_distinct():
    closed_record = {
        "id": "closed-qualification",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "입찰참가자격: 다음 사항을 모두 충족한 업체\n"
                    "가. 중소기업자\n"
                    "사업안내 및 제출서류의 세부내용은 제안요청서 참조"
                ),
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": False},
        "dropped_doc_counts": {"제안요청서": 1},
    }
    result = MODULE.extract_facts(closed_record)
    closure = result["relations"]["qualification_source_closure"]
    assert closure["status"] == (
        "closed_qualification_list_without_detected_external_delegation"
    )
    assert closure["closed_list_marker_fact_ids"]
    assert closure["delegation_marker_fact_ids"] == []
    assert closure["scope_assessments"]["bid_qualification"] == {
        "source_status": "closed_qualification_list_without_detected_external_delegation",
        "missing_document_materiality": "not_material_to_closed_bid_qualification_scope",
        "basis": "explicit_closed_list_and_no_qualification_delegation",
        "basis_fact_ids": closure["closed_list_marker_fact_ids"],
    }
    assert closure["other_scope_reference_marker_fact_ids"]
    assert closure["scope_assessments"][
        "business_task_submission_or_evaluation_details"
    ]["missing_document_materiality"] == (
        "potentially_material_within_this_nonqualification_scope_only"
    )

    delegated_record = copy.deepcopy(closed_record)
    delegated_record["id"] = "delegated-qualification"
    delegated_record["docs"][0]["text"] += "\n입찰참가자격 세부사항은 제안요청서 참조"
    delegated = MODULE.extract_facts(delegated_record)
    delegated_closure = delegated["relations"]["qualification_source_closure"]
    assert delegated_closure["status"] == "external_qualification_delegation_observed"
    assert delegated_closure["delegation_marker_fact_ids"]
    assert delegated_closure["scope_assessments"]["bid_qualification"][
        "missing_document_materiality"
    ] == "potentially_material_to_bid_qualification_scope"


def test_closed_qualification_with_irrelevant_rfp_and_checklist_is_nonconstitutive():
    record = {
        "id": "closed-rfp-checklist",
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
    result = MODULE.extract_facts(record)
    direct = result["facts"]["direct_production_requirements"][0]
    assert direct["clause_role"] == "proof_document_submission_list"
    assert direct["binding_scope"] == "unbound_proof_document"
    assert direct["eligibility_effect"] == "document_submission_only"
    assert direct["qualification_constitutive"] is False
    assert direct["bound_product_codes"] == []
    assert direct["name_candidate_catalog_codes"] == []
    assert not any("binding" in ambiguity for ambiguity in direct["ambiguities"])

    binding = result["relations"]["direct_production_target_bindings"][0]
    assert binding["relation"] == "binding_not_applicable"
    assert binding["binding_applicability"] == "not_applicable"
    assert binding["clause_codes"] == []

    closure = result["relations"]["qualification_source_closure"]
    assert closure["status"] == (
        "closed_qualification_list_without_detected_external_delegation"
    )
    assert closure["delegation_marker_fact_ids"] == []
    assert closure["other_scope_reference_marker_fact_ids"]
    assert closure["scope_assessments"]["bid_qualification"][
        "missing_document_materiality"
    ] == "not_material_to_closed_bid_qualification_scope"
    assert {
        row["marker_type"]
        for row in result["facts"]["qualification_source_markers"]
    } == {
        "closed_cumulative_qualification_list",
        "external_nonqualification_reference",
    }
    assert {
        row["code"] for row in result["relations"]["exact_product_code_inventory"]
    } == {"6010989901", "7215409902", "8014198801"}


def test_enumerated_qualification_section_closes_v20_scope_and_title_binds_work():
    record = {
        "id": "enumerated-v20-closure",
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

    result = MODULE.extract_facts(record)
    marker_types = {
        row["marker_type"]
        for row in result["facts"]["qualification_source_markers"]
    }
    assert marker_types == {
        "closed_enumerated_qualification_section",
        "external_nonqualification_reference",
    }
    assert all(
        "v20" in row["support_items"]
        for row in result["facts"]["qualification_source_markers"]
    )

    closure = result["relations"]["qualification_source_closure"]
    assert closure["status"] == (
        "closed_qualification_list_without_detected_external_delegation"
    )
    assert closure["delegation_marker_fact_ids"] == []
    assert closure["scope_assessments"]["bid_qualification"][
        "missing_document_materiality"
    ] == "not_material_to_closed_bid_qualification_scope"
    assert closure["scope_assessments"][
        "business_task_submission_or_evaluation_details"
    ]["missing_document_materiality"] == (
        "potentially_material_within_this_nonqualification_scope_only"
    )

    software = result["facts"]["software_object_task_relations"]
    operative = [
        row
        for row in software
        if row["relation_role"] == "contract_software_object_task_candidate"
    ]
    assert len(operative) == 1
    assert operative[0]["span"]["text"] == (
        "차세대 경영정보시스템 구축(일반경쟁·50억원미만)"
    )
    assert {row["object_type"] for row in operative[0]["software_objects"]} >= {
        "information_system"
    }
    assert {row["task_type"] for row in operative[0]["task_actions"]} == {
        "build"
    }
    assert result["relations"]["software_restriction_linkage"] == {
        "status": "software_work_observed_restriction_not_extracted",
        "software_work_fact_ids": [operative[0]["fact_id"]],
        "restriction_fact_ids": [],
        "is_label_decision": False,
    }


def test_explicit_v20_qualification_delegation_overrides_enumerated_closure():
    record = {
        "id": "enumerated-v20-delegated",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "5. 입찰참가자격\n\n"
                    "① 국가계약법령에 따른 경쟁입찰 자격을 갖춘 자\n\n"
                    "② 소프트웨어사업자(컴퓨터관련서비스사업)로 등록한 자\n\n"
                    "입찰참가자격 세부사항은 제안요청서 참조\n"
                ),
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": False},
        "dropped_doc_counts": {"제안요청서": 1},
    }

    result = MODULE.extract_facts(record)
    closure = result["relations"]["qualification_source_closure"]
    assert closure["closed_list_marker_fact_ids"]
    assert closure["delegation_marker_fact_ids"]
    assert closure["status"] == "external_qualification_delegation_observed"
    assert closure["scope_assessments"]["bid_qualification"][
        "missing_document_materiality"
    ] == "potentially_material_to_bid_qualification_scope"


def test_operative_prebid_direct_production_remains_binding_applicable():
    record = {
        "id": "operative-prebid-direct-production",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "세부품명: 디자인서비스(8214150201)\n"
                    "입찰참가자격: 디자인서비스(8214150201)의 직접생산확인증명서를 "
                    "전자입찰서 제출 마감일 전일까지 발급받아 소지한 업체\n"
                ),
            }
        ],
        "meta": {"세부품명번호목록": "디자인서비스[8214150201]"},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }
    result = MODULE.extract_facts(record)
    direct = next(
        row
        for row in result["facts"]["direct_production_requirements"]
        if row["qualification_constitutive"] is True
    )
    assert direct["clause_role"] == "operative_bid_qualification_candidate"
    assert direct["bound_product_codes"] == ["8214150201"]
    assert "direct_production_product_binding_not_explicit" not in direct["ambiguities"]
    binding = next(
        row
        for row in result["relations"]["direct_production_target_bindings"]
        if row["direct_production_fact_id"] == direct["fact_id"]
    )
    assert binding["relation"] == "exact_code_overlap_with_bid_metadata"
    assert binding["binding_applicability"] == "applicable"
    assert binding["qualification_constitutive"] is True


def test_qualification_product_name_cannot_self_prove_the_procurement_object():
    record = {
        "id": "unrelated-direct-production-product",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "1. 입찰에 부치는 사항\n"
                    "가. 품 명: 농림수산연구조사서비스\n\n"
                    "2. 입찰참가자격\n"
                    "아래 자격을 모두 갖춘 업체\n"
                    "가. 중소기업 또는 소상공인\n"
                    "나. 직접생산확인증명서[세부품명: "
                    "기타행사기획및대행서비스(8014199001)]를 소지한 업체\n"
                ),
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }

    result = MODULE.extract_facts(record)
    names = result["facts"]["catalog_product_name_candidates"]
    principal = next(row for row in names if row.get("matched_name") == "농림수산연구조사서비스")
    qualification_target = next(
        row for row in names if row.get("matched_name") == "기타행사기획및대행서비스"
    )
    assert principal["source_role"] == "procurement_object_candidate"
    assert qualification_target["source_role"] == "bid_qualification_object_candidate"

    direct = next(
        row
        for row in result["facts"]["direct_production_requirements"]
        if row["qualification_constitutive"] is True
    )
    binding = next(
        row
        for row in result["relations"]["direct_production_target_bindings"]
        if row["direct_production_fact_id"] == direct["fact_id"]
    )
    assert binding["bid_object_name_candidate_codes"] == []
    assert binding["declared_bid_object_names"] == ["농림수산연구조사서비스"]
    assert binding["relation"] == "clause_code_without_independent_bid_object_match"
    assert binding["binding_applicability"] == "requires_object_scope_adjudication"


def test_article_summary_is_nonoperative_hint_and_exceptions_are_candidates_only():
    result = MODULE.extract_facts(fixture_record())
    summary = result["metadata_facts"]["article_summary"]
    assert summary["semantic_role"] == "search_or_classification_summary_only"
    assert summary["operative_bid_qualification"] is False
    assert summary["establishes_statutory_exception"] is False
    assert all(
        fact["establishment_status"] == "not_adjudicated_candidate_only"
        for fact in result["facts"]["statutory_exception_clauses"]
    )


def test_enterprise_size_paths_keep_and_or_timing_and_statutory_exception_facts():
    result = MODULE.extract_facts(fixture_record())
    clauses = result["facts"]["enterprise_size_clauses"]
    operative = [
        fact
        for fact in clauses
        if fact["clause_role"] == "operative_bid_qualification_candidate"
    ]
    assert operative
    assert any(
        fact["local_logic"]
        in {
            "explicit_alternative_candidate",
            "mixed_and_or_requires_parse",
            "cumulative_section_with_internal_or_candidate",
        }
        for fact in operative
    )
    scopes = {
        term["normalized_scope"]
        for fact in operative
        for term in fact["enterprise_terms"]
    }
    assert {"small_enterprise", "micro_enterprise", "special_act_entity"} <= scopes

    markers = result["facts"]["qualification_logic_markers"]
    assert any(row["relation"] == "explicit_cumulative_requirements" for row in markers)
    exceptions = result["facts"]["statutory_exception_clauses"]
    assert any(row["exception_type"] == "special_act_entity_path" for row in exceptions)
    path = result["relations"]["qualification_path_logic"]
    assert path["operative_clause_fact_ids"]
    assert path["is_label_decision"] is False


def test_software_object_task_and_large_enterprise_restriction_are_role_bound():
    result = MODULE.extract_facts(fixture_record())
    software = result["facts"]["software_object_task_relations"]
    operative = [
        fact
        for fact in software
        if fact["relation_role"] == "contract_software_object_task_candidate"
    ]
    assert operative
    assert {
        term["object_type"] for fact in operative for term in fact["software_objects"]
    } & {"information_system", "software"}
    assert {term["task_type"] for fact in operative for term in fact["task_actions"]} >= {
        "develop",
        "build",
        "maintain",
    }
    nonoperative_roles = {fact["relation_role"] for fact in software} - {
        "contract_software_object_task_candidate"
    }
    assert nonoperative_roles & {
        "training_or_content_context",
        "requirements_inventory_or_security_reference",
        "same_context_software_object_task_unresolved",
    }

    restrictions = result["facts"]["large_enterprise_software_restrictions"]
    assert any(
        fact["clause_role"] == "operative_bid_restriction_candidate"
        and fact["restriction_effect"] == "participation_prohibited_candidate"
        for fact in restrictions
    )
    linkage = result["relations"]["software_restriction_linkage"]
    assert linkage["status"] == "both_operative_candidate_types_observed"


def test_source_completeness_prevents_absence_from_becoming_a_fact():
    record = fixture_record()
    record["dropped_doc_counts"] = {"규격서": 1}
    record["input_completeness"] = {"완전관측": False}
    result = MODULE.extract_facts(record)
    codes = {row["code"] for row in result["ambiguities"]}
    assert codes == {"source_documents_dropped", "input_not_fully_observed"}
    assert all(row["automatic_abstention"] is False for row in result["ambiguities"])
    assert all(
        row["requires_item_specific_materiality_review"] is True
        for row in result["ambiguities"]
    )
    assert result["label_decisions_present"] is False


def test_coordinate_validator_detects_tampering():
    record = fixture_record()
    result = MODULE.extract_facts(record)
    tampered = copy.deepcopy(result)
    tampered["facts"]["direct_production_requirements"][0]["span"]["text"] += "변조"
    errors = MODULE.validate_fact_coordinates(record, tampered)
    assert any(error.startswith("text_mismatch:") for error in errors)


def test_official_dev_positive_premise_coverage_and_false_positive_risk_are_reported():
    report = MODULE.audit_dev_coverage(
        ROOT / "data_open" / "dev.jsonl.gz",
        ROOT / "data_open" / "dev_labels.csv",
    )
    assert report["records"] == 200
    assert report["all_positive_records"] == 63
    assert report["all_positive_premises_covered"] == 63
    assert report["coordinate_error_records"] == []
    assert report["label_values_used_for_extraction"] is False
    for item in MODULE.TARGET_ITEMS:
        item_report = report["by_item"][item]
        assert item_report["positive_coverage"] == 1.0
        assert item_report["missed_positive_ids"] == []
        assert 0.0 <= item_report["negative_candidate_rate"] <= 1.0
        assert "no label was predicted" in item_report[
            "negative_candidate_rate_meaning"
        ]
    risk = report["review_risk_role_counts"]
    assert risk["direct_production_clause_roles"]
    assert risk["enterprise_clause_roles"]
    assert risk["software_relation_roles"]
    assert "false-positive risks" in risk["meaning"]


def test_first_1000_unlabeled_records_have_zero_coordinate_errors():
    report = MODULE.audit_records(
        MODULE.read_jsonl_gz(ROOT / "data_open" / "train_unlabeled.jsonl.gz"),
        limit=1_000,
    )
    assert report["records"] == 1_000
    assert report["coordinate_error_records"] == 0
    assert report["label_values_read"] is False
    assert report["records_with_fact_family"]["enterprise_size_clauses"] > 0
    assert report["records_with_fact_family"]["direct_production_requirements"] > 0
    assert report["records_with_fact_family"]["software_object_task_relations"] > 0
    assert report["review_risk_role_counts"]["software_relation_roles"]


def test_extract_cli_writes_valid_jsonl_without_labels(tmp_path):
    output = tmp_path / "qualification.jsonl"
    assert MODULE.main(
        [
            "extract",
            "--input",
            str(ROOT / "data_open" / "dev.jsonl.gz"),
            "--output",
            str(output),
            "--limit",
            "1",
        ]
    ) == 0
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["record_id"] == "PPS-DEV-01"
    assert rows[0]["schema_version"] == MODULE.SCHEMA_VERSION
    assert rows[0]["label_decisions_present"] is False


def test_source_has_no_submission_runtime_model_or_prediction_dependency():
    source = MODULE_PATH.read_text(encoding="utf-8").casefold()
    forbidden = (
        "import " + "submission",
        "from " + "submission",
        "import " + "pps",
        "from " + "pps",
        "gemma" + "_response",
        "saved" + "_response",
        "existing" + "_prediction",
        "development" + "_prediction",
    )
    assert not any(token in source for token in forbidden)
