import hashlib
import importlib.util
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "legal_facts.py"
SPEC = importlib.util.spec_from_file_location("independent_gold_legal_facts", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def fixture_record():
    text = """용 역 입 찰 공 고

1. 입찰에 부치는 사항
용역금액: 금330,000,000원(추정가격 300,000,000원, 부가가치세 포함)
계약방법: 제한경쟁, 협상에 의한 계약

2. 입찰참가자격
가. 대학 또는 산학협력단만 참여 가능함.
나. 최근 3년 이내 국가기관이 발주한 행사 용역의 단일 건 3억원 이상 수행실적을 보유한 업체
다. 법인등기부상 본점 소재지가 경기도 또는 제주도에 있는 업체

3. 규격
제조사·모델명: ACME ZX-900. 동등 이상 제품은 별도 승인을 받아야 함.

4. 확약서
입찰 참가자는 제조사(공급사)로부터 물품공급·기술지원 확약서를 전자입찰서 제출 마감일 전까지 보유하고 계약 시 제출해야 합니다.

5. 공동계약
공동수급(공동이행방식)을 허용하며 구성원의 최소 지분율 3% 이상으로 합니다.

6. 일정
공고기간: 2026. 2. 1. ~ 2026. 2. 20.
사업설명회 일시: 2026. 2. 5. 14:00. 설명회 참석업체에 한하여 제안서 제출 자격을 부여합니다.
접수마감: 2026. 2. 20. 17:00
"""
    return {
        "id": "fixture-legal-facts",
        "docs": [{"doc_id": "D0", "type": "공고문", "text": text}],
        "meta": {
            "적용계약법": "지방계약법",
            "업무구분": "일반용역",
            "계약방법": "제한경쟁",
            "낙찰방법": "협상에의한계약",
            "배정예산금액": 330_000_000,
            "입찰추정가격": 300_000_000,
            "공동도급구성방식": "공동이행",
            "제한지역코드목록": "경기도, 제주특별자치도",
            "지역제한여부": "Y",
            "공고게시일자": "20260201",
            "개찰예정일자": "20260220",
        },
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }


def test_extracts_required_intermediate_fact_families_without_labels():
    record = fixture_record()
    result = MODULE.extract_legal_facts(record)
    facts = result["facts"]

    assert result["label_decisions_present"] is False
    assert "labels" not in result
    assert facts["institution_qualification_restrictions"]
    assert facts["performance_requirements"]
    assert facts["headquarters_region_requirements"]
    assert facts["specific_model_candidates"]
    assert facts["third_party_pledges"]
    assert facts["joint_contract_terms"]
    assert facts["briefings"]
    assert facts["document_amount_claims"]

    performance = next(
        fact
        for fact in facts["performance_requirements"]
        if "3억원" in fact["span"]["text"]
    )
    assert performance["scope"] == "bid_qualification"
    assert performance["semantic_role"] == "eligibility_requirement"
    assert performance["creates_bid_eligibility_requirement"] is True
    assert performance["creates_monetary_threshold"] is True
    assert performance["aggregation"] == "single"
    assert performance["boundary_materiality"]["v3"] == (
        "aggregation_does_not_change_amount_ratio"
    )
    assert 3 in performance["recency_years"]
    assert any(value["value_won"] == 300_000_000 for value in performance["amount_mentions"])
    assert "국가기관" in performance["client_or_issuer_terms"]

    region = next(
        fact
        for fact in facts["headquarters_region_requirements"]
        if "제주도" in fact["span"]["text"]
    )
    assert region["connective"] == "or"
    assert [value["raw"] for value in region["regions"]] == ["경기도", "제주도"]

    pledge = facts["third_party_pledges"][0]
    assert "공급사" in pledge["issuer_terms"]
    assert {"at_contract", "before_bid_deadline"} <= set(pledge["required_times"])
    assert "possession_and_submission_times_differ" in pledge["ambiguity"]

    joint = facts["joint_contract_terms"][0]
    assert joint["method"] == "joint_performance"
    assert joint["minimum_member_share_percent"] == [3.0]

    briefing = next(
        fact for fact in facts["briefings"] if "사업설명회 일시" in fact["span"]["text"]
    )
    assert briefing["attendance_effect"] == "mandatory_for_bid_or_proposal"
    assert any(value["normalized"] == "2026-02-05T14:00" for value in briefing["date_mentions"])
    assert any(
        value["normalized"] == "2026-02-05T14:00"
        for value in briefing["briefing_date_candidates"]
    )
    assert any(
        value["normalized"] == "2026-02-20T17:00"
        for value in briefing["submission_deadline_candidates"]
    )
    assert all(
        value["source_span"]["text"] == value["raw"]
        for value in briefing["submission_deadline_candidates"]
    )


def test_briefing_uses_a_coordinate_preserving_document_wide_submission_deadline():
    deadline = "제안서 제출 마감일시: 2026. 3. 23. 17:00\n"
    filler = "별도 과업 안내 사항입니다.\n" * 180
    briefing = "현장설명회 일시: 2026. 3. 18. 14:00, 장소: 본관 회의실\n"
    record = {
        "id": "distant-briefing-deadline",
        "docs": [{"doc_id": "D0", "type": "공고문", "text": deadline + filler + briefing}],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }

    result = MODULE.extract_legal_facts(record)
    fact = result["facts"]["briefings"][0]
    candidate = next(
        value
        for value in fact["submission_deadline_candidates"]
        if value["normalized"] == "2026-03-23T17:00"
    )

    assert candidate["start"] < fact["span"]["start"] - 1600
    assert candidate["source_span"]["text"] == candidate["raw"]
    assert MODULE.validate_fact_coordinates(record, result) == []


def test_direct_payment_pledge_is_not_a_third_party_supply_or_support_pledge():
    record = {
        "id": "direct-payment-pledge",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "하도급대금을 수요기관이 하수급인에게 직접 지급하는 것에 "
                    "합의한다는 내용의 확약서를 제출하여야 합니다.\n"
                    "입찰서 제출 시 하도급대금 직불조건부 입찰참가 확약서를 "
                    "전자입찰서로 갈음합니다."
                ),
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }

    facts = MODULE.extract_legal_facts(record)["facts"]["third_party_pledges"]

    assert facts == []


def test_briefing_with_only_date_and_place_has_no_eligibility_link():
    record = {
        "id": "briefing-date-place-only",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "현장설명회를 다음과 같이 개최합니다. "
                    "일시: 2026. 3. 18. 14:00 / 장소: 본관 대회의실\n"
                    "제안서 제출 마감일시: 2026. 3. 23. 17:00"
                ),
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }

    fact = MODULE.extract_legal_facts(record)["facts"]["briefings"][0]

    assert fact["attendance_effect"] == "no_eligibility_link_observed"
    assert "attendance_effect_not_resolved" not in fact["ambiguity"]


def test_briefing_with_incomplete_mandatory_attendance_cue_stays_unresolved():
    record = {
        "id": "briefing-truncated-mandatory-cue",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "사업설명회 일시: 2026. 3. 18. 14:00, 장소: 본관 회의실\n"
                    "설명회 참석은 필수이며, 불참 시"
                ),
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }

    fact = MODULE.extract_legal_facts(record)["facts"]["briefings"][0]

    assert fact["attendance_effect"] == "not_resolved"
    assert "attendance_effect_not_resolved" in fact["ambiguity"]


def test_proof_document_listing_does_not_create_a_performance_threshold():
    record = {
        "id": "proof-document-only",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "3. 입찰참가자격\n가. 관련 사업 실적이 있는 자\n\n"
                    "6. 제출서류\n(11) 실적증명서 및 기타서류"
                ),
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }
    facts = MODULE.extract_legal_facts(record)["facts"]["performance_requirements"]
    proof = next(fact for fact in facts if "실적증명서" in fact["span"]["text"])
    assert proof["scope"] == "proof_document"
    assert proof["semantic_role"] == "proof_document"
    assert proof["creates_bid_eligibility_requirement"] is False
    assert proof["creates_monetary_threshold"] is False
    assert proof["amount_mentions"] == []
    assert "no_explicit_performance_amount" not in proof["ambiguity"]


def test_submission_section_role_propagates_to_following_performance_rows():
    record = {
        "id": "submission-section-performance",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "2. 입찰참가자격\n가. 관련 업종을 등록한 자\n\n"
                    "7. 제안서 제출\n라. 제출 서류\n"
                    "11) 사업수행실적(최근3년) [별지서식 7] 각 1부\n\n"
                    "- (공공) 나라장터 실적증명서 1부\n\n"
                    "※ 실적은 중·고등학교 현장체험학습 수행실적으로 최근 3년간의 "
                    "실적을 합산 적용함"
                ),
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }
    facts = MODULE.extract_legal_facts(record)["facts"]["performance_requirements"]
    relevant = [
        fact
        for fact in facts
        if "사업수행실적" in fact["span"]["text"]
        or "합산 적용" in fact["span"]["text"]
    ]
    assert len(relevant) == 2
    assert {fact["scope"] for fact in relevant} == {"proof_document"}
    assert {fact["semantic_role"] for fact in relevant} == {"proof_document"}
    assert all(fact["creates_bid_eligibility_requirement"] is False for fact in relevant)


def test_every_document_fact_has_verbatim_absolute_coordinates_and_hashes():
    record = fixture_record()
    result = MODULE.extract_legal_facts(record)
    assert MODULE.validate_fact_coordinates(record, result) == []
    source = record["docs"][0]["text"]
    for span in MODULE.iter_fact_spans(result):
        assert span["text"] == source[span["start"] : span["end"]]
        assert span["text_sha256"] == hashlib.sha256(span["text"].encode()).hexdigest()
        assert span["source_doc_sha256"] == hashlib.sha256(source.encode()).hexdigest()


def test_meta_values_and_document_values_are_kept_separate_until_adjudication():
    result = MODULE.extract_legal_facts(fixture_record())
    metadata = result["metadata_facts"]
    comparisons = result["document_meta_comparisons"]

    assert metadata["contract_law"]["value"] == "지방계약법"
    assert metadata["work_type"]["value"] == "일반용역"
    assert metadata["budget_won"]["value"] == 330_000_000
    assert metadata["estimated_price_won"]["value"] == 300_000_000
    assert comparisons["amounts"]["resolution"] == "not_adjudicated"
    assert comparisons["amounts"]["document_fact_ids"]
    assert comparisons["region"]["resolution"] == "not_adjudicated"
    assert comparisons["contract_law"]["resolution"] == "not_adjudicated"
    assert comparisons["work_type"]["document_fact_ids"]


def test_ambiguity_is_preserved_for_comma_region_lists_and_missing_documents():
    record = fixture_record()
    record["docs"][0]["text"] = record["docs"][0]["text"].replace(
        "경기도 또는 제주도", "경기도, 제주도"
    )
    record["input_completeness"] = {"완전관측": False}
    record["dropped_doc_counts"] = {"제안요청서": 1}
    result = MODULE.extract_legal_facts(record)

    region = next(
        fact
        for fact in result["facts"]["headquarters_region_requirements"]
        if "제주도" in fact["span"]["text"]
    )
    assert region["connective"] == "comma_list_ambiguous"
    assert "region_boolean_relation_ambiguous" in region["ambiguity"]
    assert {row["code"] for row in result["ambiguities"]} == {
        "source_documents_dropped",
        "input_not_fully_observed",
    }


def test_anonymized_region_token_keeps_declared_unit_and_metro():
    record = fixture_record()
    record["docs"][0]["text"] = record["docs"][0]["text"].replace(
        "경기도 또는 제주도", "서울특별시 [지역:r1|단위=기초|광역=서울특별시]"
    )
    result = MODULE.extract_legal_facts(record)
    region = next(
        fact
        for fact in result["facts"]["headquarters_region_requirements"]
        if "[지역:r1" in fact["span"]["text"]
    )
    token = next(value for value in region["regions"] if value["raw"].startswith("[지역:"))
    assert token["unit"] == "기초"
    assert token["metro"] == "서울특별시"
    assert region["regions"] == [token]
    assert region["connective"] == "single"
    assert "region_boolean_relation_ambiguous" not in region["ambiguity"]


def test_region_scope_uses_the_local_eligibility_clause_not_later_evaluation_text():
    record = {
        "id": "long-normalized-region-clause",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "2. 입찰참가자격\n"
                    "라. 입찰공고일 전일부터 계약체결일까지 주된 영업소가 "
                    "서울특별시 [지역:r1|단위=기초|광역=서울특별시] 내에 소재한 "
                    "업체이어야 합니다.\n"
                    "3. 제안서 평가기준\n정량평가 점수 및 배점 기준을 적용합니다."
                ),
            }
        ],
        "meta": {"지역제한여부": "N", "제한지역코드목록": None},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }
    result = MODULE.extract_legal_facts(record)
    region = result["facts"]["headquarters_region_requirements"][0]
    assert region["scope"] == "bid_qualification"
    assert region["scope_basis"] == "explicit_anchor_context"
    assert [(row["unit"], row["metro"]) for row in region["regions"]] == [
        ("기초", "서울특별시")
    ]
    assert region["connective"] == "single"
    assert region["ambiguity"] == []


def test_individually_announced_future_briefing_has_no_measurable_date_interval():
    record = {
        "id": "prospective-briefing-date",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    "입찰참가자격\n"
                    "과업 이해를 돕기 위한 사업설명회를 개최할 예정이며"
                    "(일정·장소는 개별 안내), 설명회 불참 업체는 입찰 참가 "
                    "대상에서 제외함\n"
                    "제안서 제출 마감일: 2026. 3. 23. 14:00"
                ),
            }
        ],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }
    result = MODULE.extract_legal_facts(record)
    briefing = result["facts"]["briefings"][0]
    assert briefing["attendance_effect"] == "mandatory_for_bid_or_proposal"
    assert briefing["briefing_date_candidates"] == []
    assert briefing["briefing_date_source_status"] == "prospective_individual_notice_only"
    assert briefing["interval_assessability"] == (
        "not_measurable_without_actual_briefing_date"
    )
    assert "briefing_date_not_explicit" in briefing["ambiguity"]


def test_money_normalization_handles_korean_and_comma_forms():
    assert MODULE.parse_money_won("3억원") == 300_000_000
    assert MODULE.parse_money_won("5천만원") == 50_000_000
    assert MODULE.parse_money_won("455,000,000원") == 455_000_000
    assert MODULE.parse_money_won("금37,930,000원") == 37_930_000


def test_item_reference_is_limited_to_organizer_reference_entries():
    path = ROOT / "data_open" / "data" / "항목표.json"
    reference = MODULE.load_item_reference(path)
    assert reference["basis_date"] == "2026-07-27"
    assert tuple(reference["items"]) == MODULE.RELEVANT_ITEMS
    assert reference["items"]["v21"]["항목명"] == "공동 5% (10%)"
    assert reference["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_official_dev_relevant_nonempty_evidence_has_complete_coordinate_coverage():
    report = MODULE.audit_dev_evidence(
        ROOT / "data_open" / "dev.jsonl.gz", ROOT / "data_open" / "dev_labels.csv"
    )
    assert report["records"] == 200
    assert report["official_nonempty_positive_evidence_all_items"] == 54
    assert report["relevant_nonempty_positive_evidence"] == 45
    assert report["evidence_located_in_source"] == 45
    assert report["evidence_covered"] == 45
    assert report["coverage"] == 1.0
    assert report["misses"] == []


def test_source_has_no_runtime_or_saved_decision_dependency():
    source = MODULE_PATH.read_text(encoding="utf-8")
    forbidden = (
        "import " + "submission",
        "from " + "submission",
        "import " + "pps",
        "from " + "pps",
        "saved" + "_response",
        "development" + "_predictions",
        "gemma" + "_response",
    )
    assert not any(token in source.casefold() for token in forbidden)


def test_extract_cli_writes_valid_jsonl_for_one_official_record(tmp_path):
    output = tmp_path / "facts.jsonl"
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
