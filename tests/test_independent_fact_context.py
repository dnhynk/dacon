import copy
import gzip
import hashlib
import importlib.util
import json
import pathlib

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "fact_context.py"
SPEC = importlib.util.spec_from_file_location("independent_fact_context", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def fixture_record():
    text = """용역 입찰공고

2. 입찰참가자격
가. 대학 또는 산학협력단만 참여 가능함.
나. 최근 3년 이내 국가기관 발주 용역의 단일 건 3억원 이상 수행실적을 보유한 업체
다. 법인등기부상 본점 소재지가 경기도 또는 제주도에 있는 업체

3. 규격
제조사·모델명: ACME ZX-900. 동등 이상 제품도 별도 승인을 받아야 한다.

4. 확약서
입찰 참가자는 제조사로부터 물품공급 확약서를 입찰 마감 전까지 보유하고 계약 시 제출한다.

5. 공동계약과 설명회
공동이행 구성원의 최소 지분율은 3%이다.
사업설명회는 2026. 2. 5. 14:00이며 참석업체만 제안서를 제출할 수 있다.
제안서 접수마감은 2026. 2. 20. 17:00이다.

6. 제품
세부품명번호 8014199001로 입찰참가 등록하여야 한다.
"""
    return {
        "id": "fixture-fact-context",
        "docs": [{"doc_id": "D0", "type": "공고문", "text": text}],
        "meta": {
            "적용계약법": "지방계약법",
            "업무구분": "일반용역",
            "계약방법": "제한경쟁",
            "낙찰방법": "협상에의한계약",
            "배정예산금액": 330_000_000,
            "입찰추정가격": 300_000_000,
            "공동도급구성방식": "공동이행",
            "세부품명번호목록": "기타행사기획및대행서비스[8014199001]",
            "제한지역코드목록": "경기도, 제주특별자치도",
            "지역제한여부": "Y",
            "공고게시일자": "20260201",
            "개찰예정일자": "20260220",
        },
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }


def load_dev(*wanted_ids):
    wanted = set(wanted_ids)
    found = {}
    with gzip.open(ROOT / "data_open" / "dev.jsonl.gz", "rt", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record["id"] in wanted:
                found[record["id"]] = record
            if len(found) == len(wanted):
                return found
    raise AssertionError(f"missing dev fixtures: {sorted(wanted - set(found))}")


def all_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from all_keys(child)


def test_context_is_json_serializable_bounded_and_contains_no_decision_vector():
    record = fixture_record()
    catalog = MODULE.catalog_facts.CatalogIndex.load()
    prepared = MODULE.prepare_fact_inputs(record, catalog_index=catalog)
    context = MODULE.build_fact_context(
        record,
        "v1-4",
        prepared=prepared,
        catalog_index=catalog,
        max_chars=5_500,
    )
    rendered = MODULE.render_fact_context(context)

    assert json.loads(rendered) == context
    assert len(rendered) <= 5_500
    assert context["bounds"]["rendered_chars"] == len(rendered)
    assert context["semantic_role"] == "source_grounded_facts_only"
    assert context["target_items"] == ["v1", "v2", "v3", "v4"]
    assert "labels" not in set(all_keys(context))
    assert context["catalog_facts"]["status"] == "not_relevant_to_target_items"
    assert MODULE.validate_fact_context(record, context, catalog_index=catalog) == []


def test_pooled_segments_are_verbatim_absolute_source_coordinates():
    record = fixture_record()
    context = MODULE.build_fact_context(record, "v21-23", max_chars=9_000)
    source = record["docs"][0]["text"]
    segments = {row["segment_id"]: row for row in context["source_segments"]}

    assert segments
    for segment in segments.values():
        quote = source[segment["start"] : segment["end"]]
        assert quote == segment["quote"]
        assert segment["quote_sha256"] == hashlib.sha256(quote.encode()).hexdigest()
        assert segment["source_doc_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    for fact in context["legal_facts"]:
        coordinate = fact["source_coordinate"]
        segment = segments[coordinate["segment_id"]]
        exact = source[coordinate["start"] : coordinate["end"]]
        assert segment["start"] <= coordinate["start"] <= coordinate["end"] <= segment["end"]
        assert coordinate["quote_sha256"] == hashlib.sha256(exact.encode()).hexdigest()
    assert MODULE.validate_fact_context(record, context) == []


def test_catalog_group_preserves_document_meta_and_catalog_coordinates():
    record = fixture_record()
    catalog = MODULE.catalog_facts.CatalogIndex.load()
    prepared = MODULE.prepare_fact_inputs(record, catalog_index=catalog)
    context = MODULE.build_fact_context(
        record,
        "v10-13",
        prepared=prepared,
        catalog_index=catalog,
        max_chars=9_000,
    )

    assert context["catalog_facts"]["summary"]["applicability"] == "applicable"
    assert context["catalog_facts"]["code_index"][0]["code"] == "8014199001"
    detail = context["catalog_facts"]["code_details"][0]
    expected = catalog.get("8014199001")
    assert detail["catalog_identity"]["catalog_coordinate"] == expected["catalog_coordinate"]
    condition_coordinate = detail["special_condition"]["source_coordinate"]
    assert condition_coordinate["quote"] == expected["특이사항"]
    assert condition_coordinate["end"] == len(expected["특이사항"])

    mention_kinds = {mention["source_kind"] for mention in detail["mentions"]}
    assert mention_kinds == {"document", "meta"}
    assert MODULE.validate_catalog_coordinates(record, prepared["catalog"], catalog) == []
    assert MODULE.validate_fact_context(record, context, catalog_index=catalog) == []


@pytest.mark.parametrize("estimated_price", [499_999_999, 500_000_000])
def test_v5_context_preserves_exact_500m_boundary_and_catalog_link_evidence(
    estimated_price,
):
    record = fixture_record()
    record["id"] = f"fixture-v5-boundary-{estimated_price}"
    record["meta"]["입찰추정가격"] = estimated_price
    catalog = MODULE.catalog_facts.CatalogIndex.load()
    context = MODULE.build_fact_context(
        record,
        "v5-8",
        prepared=MODULE.prepare_fact_inputs(record, catalog_index=catalog),
        catalog_index=catalog,
    )

    assert context["metadata_facts"]["estimated_price_won"]["value"] == estimated_price
    assert context["metadata_facts"]["catalog_codes"]["value"] == record["meta"]["세부품명번호목록"]
    assert context["catalog_facts"]["status"] == "included"
    assert context["catalog_facts"]["summary"]["applicability"] == "applicable"
    assert context["catalog_facts"]["code_index"][0]["code"] == "8014199001"
    assert "does not by itself establish" in context["catalog_facts"]["interpretation_boundary"]
    assert any(
        "8014199001" in segment["quote"] and "입찰참가 등록" in segment["quote"]
        for segment in context["source_segments"]
    )
    assert MODULE.validate_fact_context(record, context, catalog_index=catalog) == []


def test_v5_dev_context_exposes_exact_applicable_competitive_code_and_current_bid_link():
    record = load_dev("PPS-DEV-11")["PPS-DEV-11"]
    catalog = MODULE.catalog_facts.CatalogIndex.load()
    context = MODULE.build_fact_context(
        record,
        "v5-8",
        prepared=MODULE.prepare_fact_inputs(record, catalog_index=catalog),
        catalog_index=catalog,
    )

    summary = context["catalog_facts"]["summary"]
    assert summary["applicability"] == "applicable"
    assert summary["unique_code_count"] == 2
    assert {
        (row["code"], row["applicability"])
        for row in context["catalog_facts"]["code_index"]
    } == {("8014198801", "applicable"), ("8014198901", "applicable")}
    retained = context["catalog_facts"]["code_details"]
    assert retained and retained[0]["catalog_registered"] is True
    segments = {row["segment_id"]: row for row in context["source_segments"]}
    document_mentions = [
        mention
        for detail in retained
        for mention in detail["mentions"]
        if mention["source_kind"] == "document"
    ]
    assert document_mentions
    assert all(
        "직접생산확인증명서" in segments[mention["source_coordinate"]["segment_id"]]["quote"]
        for mention in document_mentions
    )
    assert MODULE.validate_fact_context(record, context, catalog_index=catalog) == []


def test_v5_catalog_context_keeps_counterexample_scope_visible_without_inventing_link():
    record = fixture_record()
    record["id"] = "fixture-v5-historical-code"
    record["docs"][0]["text"] = """용역 입찰공고
입찰참가자는 중소기업확인서를 제출하여야 한다.
주된 영업소가 경기도에 있는 업체만 참가할 수 있다.
과거 수행실적 작성 예시: 전시회기획및대행서비스(8014198801)
현재 과업은 별도 일반 연구용역이다.
"""
    record["meta"]["세부품명번호목록"] = None
    record["meta"]["입찰추정가격"] = 500_000_000
    catalog = MODULE.catalog_facts.CatalogIndex.load()
    context = MODULE.build_fact_context(
        record,
        "v5-8",
        prepared=MODULE.prepare_fact_inputs(record, catalog_index=catalog),
        catalog_index=catalog,
    )

    # Catalog applicability and current-procurement linkage are deliberately
    # separate.  The exact source surroundings let the annotator reject this
    # past-example mention instead of turning it into an operative product.
    assert context["catalog_facts"]["summary"]["applicability"] == "applicable"
    assert "does not by itself establish" in context["catalog_facts"]["interpretation_boundary"]
    assert any("과거 수행실적 작성 예시" in row["quote"] for row in context["source_segments"])
    assert not any("operative_procurement" in key for key in all_keys(context))
    assert MODULE.validate_fact_context(record, context, catalog_index=catalog) == []


@pytest.mark.parametrize(
    ("contract_law", "work_type"),
    [("지방계약법", "건설기술용역"), ("국가계약법", "일반용역")],
)
def test_v5_context_retains_technical_service_and_national_law_counterexample_axes(
    contract_law, work_type
):
    record = fixture_record()
    record["id"] = f"fixture-v5-counterexample-{contract_law}-{work_type}"
    record["meta"]["적용계약법"] = contract_law
    record["meta"]["업무구분"] = work_type
    context = MODULE.build_fact_context(record, "v5-8")

    assert context["metadata_facts"]["contract_law"]["value"] == contract_law
    assert context["metadata_facts"]["work_type"]["value"] == work_type
    assert context["catalog_facts"]["status"] == "included"
    assert MODULE.validate_fact_context(record, context) == []


def test_hard_budget_omits_whole_facts_and_reports_counts():
    record = fixture_record()
    prepared = MODULE.prepare_fact_inputs(record)
    context = MODULE.build_fact_context(
        record, "v1-4", prepared=prepared, max_chars=4_500
    )

    assert len(MODULE.render_fact_context(context)) <= 4_500
    assert context["omissions"]["context_char_budget_truncated"] is True
    assert context["omissions"]["omitted_relevant_fact_count"] > 0
    assert context["bounds"]["selected_atomic_facts"] < context["bounds"]["available_atomic_facts"]
    for counts in context["omissions"]["legal"].values():
        assert counts["available"] == counts["included"] + counts["omitted"]
    # Every retained source fact still has a complete coordinate and segment;
    # the budget mechanism never slices an individual quote.
    assert MODULE.validate_fact_context(record, context) == []


def test_mismatched_or_tampered_inputs_are_rejected_or_detected():
    record = fixture_record()
    prepared = MODULE.prepare_fact_inputs(record)
    wrong = copy.deepcopy(prepared)
    wrong["legal"]["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source hash"):
        MODULE.build_fact_context(record, "v1-4", prepared=wrong)

    context = MODULE.build_fact_context(record, "v1-4", prepared=prepared)
    context["source_segments"][0]["quote"] += "변조"
    errors = MODULE.validate_fact_context(record, context)
    assert any("quote_mismatch" in error for error in errors)
    assert "context_hash_mismatch" in errors


def test_official_dev_context_keeps_known_source_facts_and_valid_coordinates():
    records = load_dev("PPS-DEV-01", "PPS-DEV-02", "PPS-DEV-03")
    catalog = MODULE.catalog_facts.CatalogIndex.load()
    for record in records.values():
        prepared = MODULE.prepare_fact_inputs(record, catalog_index=catalog)
        for group in MODULE.GROUPS:
            context = MODULE.build_fact_context(
                record, group, prepared=prepared, catalog_index=catalog
            )
            assert MODULE.validate_fact_context(record, context, catalog_index=catalog) == []
            assert len(MODULE.render_fact_context(context)) <= MODULE.DEFAULT_MAX_CHARS

    dev1 = MODULE.build_fact_context(
        records["PPS-DEV-01"],
        "v1-4",
        prepared=MODULE.prepare_fact_inputs(records["PPS-DEV-01"], catalog_index=catalog),
        catalog_index=catalog,
    )
    dev2 = MODULE.build_fact_context(
        records["PPS-DEV-02"],
        "v1-4",
        prepared=MODULE.prepare_fact_inputs(records["PPS-DEV-02"], catalog_index=catalog),
        catalog_index=catalog,
    )
    dev1_text = "\n".join(segment["quote"] for segment in dev1["source_segments"])
    dev2_text = "\n".join(segment["quote"] for segment in dev2["source_segments"])
    assert "대학 또는" in dev1_text and "산학협력단만 참여 가능" in dev1_text
    assert "3천만원 이상" in dev2_text and "실적이 있는 자" in dev2_text


def test_official_basic_region_is_not_rendered_as_an_ambiguous_region_pair():
    record = load_dev("PPS-DEV-08")["PPS-DEV-08"]
    context = MODULE.build_fact_context(record, "v5-8")
    region_facts = [
        row
        for row in context["legal_facts"]
        if row["category"] == "headquarters_region_requirements"
    ]
    assert region_facts
    for fact in region_facts:
        assert fact["attributes"]["scope"] == "bid_qualification"
        assert fact["attributes"]["scope_basis"] == "explicit_anchor_context"
        assert fact["attributes"]["connective"] == "single"
        assert fact["attributes"]["ambiguity"] == []
        assert [row["unit"] for row in fact["attributes"]["regions"]] == ["기초"]
        assert [row["metro"] for row in fact["attributes"]["regions"]] == [
            "서울특별시"
        ]
    assert MODULE.validate_fact_context(record, context) == []


def test_official_future_briefing_keeps_date_assessability_status():
    record = load_dev("PPS-DEV-138")["PPS-DEV-138"]
    context = MODULE.build_fact_context(record, "v21-23")
    briefing = next(
        row for row in context["legal_facts"] if row["category"] == "briefings"
    )
    attributes = briefing["attributes"]
    assert attributes["attendance_effect"] == "mandatory_for_bid_or_proposal"
    assert attributes["briefing_date_candidates"] == []
    assert attributes["briefing_date_source_status"] == (
        "prospective_individual_notice_only"
    )
    assert attributes["interval_assessability"] == (
        "not_measurable_without_actual_briefing_date"
    )
    assert MODULE.validate_fact_context(record, context) == []


def test_context_hash_and_rendered_size_are_reproducible():
    record = fixture_record()
    prepared = MODULE.prepare_fact_inputs(record)
    first = MODULE.build_fact_context(record, "v24", prepared=prepared)
    second = MODULE.build_fact_context(record, "v24", prepared=prepared)
    assert first == second
    assert first["context_sha256"] == MODULE.sha256_object(MODULE._hashable_context(first))
    assert first["bounds"]["rendered_chars"] == len(MODULE.canonical_json(first))


def test_source_has_no_competition_runtime_dependency():
    source = MODULE_PATH.read_text(encoding="utf-8").casefold()
    forbidden = (
        "import " + "submission",
        "from " + "submission",
        "import " + "pps",
        "from " + "pps",
        "gemma" + "_response",
        "saved" + "_response",
    )
    assert not any(token in source for token in forbidden)
