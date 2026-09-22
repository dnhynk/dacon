import importlib.util
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "catalog_facts.py"
SPEC = importlib.util.spec_from_file_location("independent_catalog_facts", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def fixture_record(text, *, meta=None, record_id="fixture"):
    return {
        "id": record_id,
        "docs": [{"doc_id": "D0", "type": "공고문", "text": text}],
        "meta": meta or {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }


def test_supplied_catalog_is_validated_and_preserves_non_code_umbrella_row():
    catalog = MODULE.CatalogIndex.load()
    assert len(catalog) == 615
    assert catalog.total_rows == 616
    assert len(catalog.non_code_rows) == 1
    assert catalog.non_code_rows[0]["세부품명번호"] == ""
    assert catalog.non_code_rows[0]["대분류"] == "국방규격"
    row = catalog.get("1017161101")
    assert row["세부품명"] == "석회질비료"
    assert row["catalog_coordinate"]["row_number"] == 2
    assert len(catalog.sha256) == 64


def test_exact_and_pdf_fragmented_codes_keep_verbatim_coordinates():
    catalog = MODULE.CatalogIndex.load()
    text = (
        "세부품명번호 8014199001로 등록하고, "
        "세부품명번호 10자리 40 10180602로 등록한다."
    )
    record = fixture_record(text)
    extracted = MODULE.extract_code_mentions(record, catalog)
    assert extracted["unique_codes"] == ["4010180602", "8014199001"]
    by_code = {mention["code"]: mention for mention in extracted["mentions"]}
    assert by_code["4010180602"]["mention_form"] == "whitespace_normalized_10_digit"
    assert by_code["4010180602"]["raw"] == "40 10180602"
    for mention in extracted["mentions"]:
        assert text[mention["start"] : mention["end"]] == mention["raw"]
        assert mention["context"] == text[
            mention["context_start"] : mention["context_end"]
        ]


def test_ten_digit_price_is_retained_as_ambiguous_not_promoted_to_code():
    catalog = MODULE.CatalogIndex.load()
    record = fixture_record("사업예산: 1000000000원이며 일반 물품을 구매한다.")
    extracted = MODULE.extract_code_mentions(record, catalog)
    assert extracted["mentions"] == []
    assert [row["code"] for row in extracted["ambiguous_ten_digit_tokens"]] == [
        "1000000000"
    ]
    result = MODULE.resolve_record(record, catalog)
    assert result["summary"]["decision"] == "unknown"
    assert result["summary"]["reason_code"] == "no_identified_product_code"


def test_meta_amount_fields_are_never_scanned_as_product_code_fields():
    catalog = MODULE.CatalogIndex.load()
    record = fixture_record(
        "코드가 기재되지 않았다.",
        meta={"배정예산금액": 1000000000, "입찰추정가격": 909090909},
    )
    extracted = MODULE.extract_code_mentions(record, catalog)
    assert extracted["mentions"] == []
    assert extracted["ambiguous_ten_digit_tokens"] == []


def test_meta_product_code_has_exact_field_coordinate():
    catalog = MODULE.CatalogIndex.load()
    value = "기타행사기획및대행서비스[8014199001]"
    record = fixture_record("본문", meta={"세부품명번호목록": value})
    mention = MODULE.extract_code_mentions(record, catalog)["mentions"][0]
    assert mention["source_kind"] == "meta"
    assert mention["path"] == "meta.세부품명번호목록"
    assert value[mention["start"] : mention["end"]] == "8014199001"


def test_catalog_note_is_structured_with_exact_note_coordinates_and_price():
    catalog = MODULE.CatalogIndex.load()
    row = catalog.get("8014199001")
    condition = row["condition"]
    assert condition["kind"] == "conditional"
    assert condition["categories"] == ["price"]
    clause = condition["clauses"][0]
    assert condition["raw"][clause["note_start"] : clause["note_end"]] == clause["text"]
    price = clause["price_conditions"][0]
    assert price["metric"] == "estimated_price"
    assert price["comparator"] == "lt"
    assert price["threshold_won"] == 1_000_000_000
    assert condition["raw"][price["note_start"] : price["note_end"]] == price["verbatim"]
    assert condition["source_coordinate"]["quote"] == condition["raw"]


def test_simple_price_scope_resolves_both_sides_and_missing_fact_stays_unknown():
    catalog = MODULE.CatalogIndex.load()
    text = "세부품명번호 8014199001"
    below = MODULE.resolve_record(
        fixture_record(text, meta={"입찰추정가격": 999_999_999}), catalog
    )["codes"][0]
    boundary = MODULE.resolve_record(
        fixture_record(text, meta={"입찰추정가격": 1_000_000_000}), catalog
    )["codes"][0]
    missing = MODULE.resolve_record(fixture_record(text), catalog)["codes"][0]
    assert (below["decision"], below["reason_code"]) == (
        "applicable",
        "catalog_conditions_met",
    )
    assert (boundary["decision"], boundary["reason_code"]) == (
        "not_applicable",
        "catalog_condition_not_met",
    )
    assert (missing["decision"], missing["reason_code"]) == (
        "unknown",
        "catalog_condition_unresolved",
    )
    observed = below["condition_evaluation"]["clause_evaluations"][0][
        "price_evaluations"
    ][0]
    assert observed["observed_won"] == 999_999_999
    assert observed["observed_coordinate"]["path"] == "meta.입찰추정가격"


def test_material_and_purpose_conditions_are_not_guessed():
    catalog = MODULE.CatalogIndex.load()
    # 약품탱크 is limited to plastic products by the supplied special note.
    fact = MODULE.resolve_record(
        fixture_record("세부품명번호 2411180501 약품탱크"), catalog
    )["codes"][0]
    assert fact["catalog_registered"] is True
    assert fact["catalog_row"]["condition"]["categories"] == ["material"]
    assert fact["decision"] == "unknown"
    assert fact["condition_evaluation"]["clause_evaluations"][0]["reason_code"] == (
        "required_nonprice_fact_unresolved"
    )


def test_public_body_video_identifier_condition_uses_exact_source_evidence():
    catalog = MODULE.CatalogIndex.load()
    text = (
        "품명: 동영상제작서비스(8213160301)\n"
        "교육영상 제작 및 편집\n"
        "제작 영상에는 [수요기관(기타공공기관)] 캐릭터를 활용하여야 한다.\n"
    )
    fact = MODULE.resolve_record(fixture_record(text), catalog)["codes"][0]
    clause = fact["condition_evaluation"]["clause_evaluations"][0]

    assert fact["decision"] == "applicable"
    assert clause["reason_code"] == "required_nonprice_scope_met"
    assert clause["nonprice_evidence"][0]["raw"] == "[수요기관(기타공공기관)] 캐릭터"
    evidence = clause["nonprice_evidence"][0]
    assert text[evidence["start"] : evidence["end"]] == evidence["raw"]


def test_public_body_name_in_header_does_not_satisfy_video_identifier_condition():
    catalog = MODULE.CatalogIndex.load()
    text = "[수요기관(기타공공기관)] 공고\n품명: 동영상제작서비스(8213160301)"
    fact = MODULE.resolve_record(fixture_record(text), catalog)["codes"][0]
    assert fact["decision"] == "unknown"


def test_compound_price_and_use_scope_can_fail_price_but_never_guesses_use():
    catalog = MODULE.CatalogIndex.load()
    text = "세부품명번호 8115160401 측량용역"
    below = MODULE.resolve_record(
        fixture_record(text, meta={"입찰추정가격": 29_999_999}), catalog
    )["codes"][0]
    above = MODULE.resolve_record(
        fixture_record(text, meta={"입찰추정가격": 30_000_000}), catalog
    )["codes"][0]
    assert below["decision"] == "not_applicable"
    assert above["decision"] == "unknown"
    assert above["condition_evaluation"]["clause_evaluations"][0]["reason_code"] == (
        "required_nonprice_fact_unresolved"
    )


def test_unregistered_explicit_code_stays_unknown_not_not_applicable():
    catalog = MODULE.CatalogIndex.load()
    result = MODULE.resolve_record(
        fixture_record("세부품명번호 9999999999로 입찰참가 등록"), catalog
    )
    assert result["summary"]["decision"] == "unknown"
    assert result["codes"][0]["catalog_registered"] is False
    assert result["codes"][0]["reason_code"] == "code_not_in_supplied_catalog"


def test_multiple_distinct_codes_preserve_a_unanimous_applicability_summary():
    catalog = MODULE.CatalogIndex.load()
    result = MODULE.resolve_record(
        fixture_record("세부품명번호 1017161101 및 세부품명번호 1017169401"),
        catalog,
    )
    assert [row["decision"] for row in result["codes"]] == [
        "applicable",
        "applicable",
    ]
    assert result["summary"] == {
        "decision": "applicable",
        "reason_code": "all_identified_product_codes_applicable",
        "reason": "Every source-identified product code is applicable under its supplied catalog row.",
        "mixed_product_codes": True,
        "unique_code_count": 2,
        "code_decisions": ["applicable"],
    }


def test_result_is_json_serializable_and_has_no_runtime_prediction_input():
    catalog = MODULE.CatalogIndex.load()
    result = MODULE.resolve_record(
        fixture_record(
            "세부품명번호 9015189001",
            meta={"입찰추정가격": 250_000_000},
        ),
        catalog,
    )
    assert json.loads(json.dumps(result, ensure_ascii=False))["id"] == "fixture"
    source = MODULE_PATH.read_text(encoding="utf-8")
    forbidden = (
        "import " + "submission",
        "from " + "submission",
        "import " + "pps",
        "from " + "pps",
        "saved" + "_response",
        "existing" + "_prediction",
    )
    assert not any(token in source for token in forbidden)


def test_official_dev_and_unlabeled_sample_produce_auditable_statistics():
    catalog = MODULE.CatalogIndex.load()
    dev = MODULE.audit_records(
        MODULE.read_jsonl_gz(ROOT / "data_open" / "dev.jsonl.gz"), catalog=catalog
    )
    sample = MODULE.audit_records(
        MODULE.read_jsonl_gz(ROOT / "data_open" / "train_unlabeled.jsonl.gz"),
        catalog=catalog,
        limit=250,
    )
    assert dev["records"] == 200
    assert dev["records_with_identified_codes"] > 0
    assert dev["mentions"] > dev["records_with_identified_codes"]
    assert sum(dev["summary_decisions"].values()) == 200
    assert sum(dev["code_decisions"].values()) >= dev["records_with_identified_codes"]
    assert sample["records"] == 250
    assert sample["records_with_identified_codes"] > 0
    assert sample["catalog_code_rows"] == 615
    assert sample["catalog_non_code_rows"] == 1
    assert sample["catalog_total_rows"] == 616
