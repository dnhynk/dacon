"""v10 construction/scoring contracts, using invented hosts only."""
import argparse
import copy
import json

import pytest

from tools.independent_gold import cell_plant as p, cell_score as s


def host(extra="", **meta):
    return {"id": "test", "docs": [{"doc_id": "d0", "type": "공고문", "text":
        "1. 입찰에 부치는 사항\n가. 건명: 일반 시험용역\n추정가격: 120,000,000원\n배정예산: 132,000,000원\n"
        "3. 입찰참가자격\n가. 시행령 제13조의 자격을 갖춘 자\n나. 부정당업자 제재를 받지 않은 자\n"
        + extra + "\n4. 입찰서 제출\n가. 전자입찰"}],
        "meta": {"입찰추정가격": 120_000_000, "배정예산금액": 132_000_000,
                 "적용계약법": "국가계약법", "업무구분": "일반용역", "계약방법": "제한경쟁",
                 "낙찰방법": "협상에의한계약", **meta}}


def test_near_miss_target_is_never_a_positive_or_an_unvetted_zero():
    h = host()
    record, prov = p.legal_near_miss(h, "v22", "general")
    row = p._v10_row(h, record, prov, "v22", "family", near=True)
    assert s.targets_of(row) == []
    assert row["near_miss_items"] == ["v22"]
    assert "v22" not in row["expected_zero_items"]
    key = {"test": {"targets": [], "near_miss_items": ["v22"], "expected_zero_items": []}}
    predictions = {"test": {**dict.fromkeys(s.ITEMS, "0"), "v22": "1"}}
    result = s.score_submission(key, predictions)
    assert result["near_miss_fired"] == result["near_miss_cells"] == 1
    assert result["items"]["v22"]["near_miss_firing_rate"] == 1
    assert result["items"]["v22"]["near_miss_ci95"][0] > 0


@pytest.mark.parametrize("law,floor", [("국가계약법", "10"), ("지방계약법", "5")])
def test_legal_share_uses_contract_law(law, floor):
    h = host("다. 공동이행 최소지분율 15% 이상", 적용계약법=law, 공동도급구성방식="공동이행")
    record, _ = p.legal_near_miss(h, "v21", "general")
    assert f"최소지분율 {floor}%" in record["docs"][0]["text"]


def test_national_regional_boundary_is_one_won_below_and_meta_matches():
    h = host("다. 본점이 서울특별시에 소재한 업체")
    h["meta"]["낙찰방법"] = "적격심사제"
    record, _ = p.legal_near_miss(h, "v5", "general")
    assert record["meta"]["입찰추정가격"] == 229_999_999
    assert "229,999,999" in record["docs"][0]["text"]
    h["meta"]["적용계약법"] = "지방계약법"
    with pytest.raises(p.PlantError, match="national"):
        p.legal_near_miss(h, "v5", "general")


def test_v24_near_miss_rejects_an_existing_budget_price_role_mismatch():
    h = host()
    p.verify_comparison_amount_roles(h)
    h["docs"][0]["text"] += "\n사 업 예 산 | 금120,000,000원(부가가치세 포함)"
    with pytest.raises(p.PlantError, match="amount-role mismatch"):
        p.verify_comparison_amount_roles(h)


def writer_pair():
    h = host()
    task = {"item": "v22", "mode": "near_miss", "host_id": "test", "split": "dev",
            "prompt_sha256": p.sha256_text(p.WRITER_PROMPT), "host_sha256": p.sha256_text(p.canonical_json(h)),
            "insert_after_line_candidates": [6]}
    entry = {"item": "v22", "mode": "near_miss", "host_id": "test", "label": 0,
             "clause": "설명회 불참 업체도 자유롭게 응찰할 수 있습니다.", "why_label": "참석은 의무가 아니다.",
             "insert_after_line": 6, "writer_model": "external-test-fixture", "prompt_sha256": task["prompt_sha256"]}
    return h, task, entry


def test_written_intake_is_review_pending_and_places_exactly_after_selected_line():
    h, task, entry = writer_pair()
    row = p.plant_written_clause(entry, h, task)
    assert row["record"]["docs"][0]["text"].splitlines()[7] == entry["clause"]
    assert row["eligible_for_scoring"] is False


@pytest.mark.parametrize("marker", ["가.", "1.", "| ○ |", "|", "  "])
def test_written_copy_check_skips_marker_only_host_lines(marker):
    h, task, entry = writer_pair()
    h["docs"][0]["text"] += "\n" + marker
    task["host_sha256"] = p.sha256_text(p.canonical_json(h))
    p.validate_written_clause(entry, h, task)
    entry["clause"] = "가. 시행령 제13조의 자격을 갖춘 자"
    with pytest.raises(p.PlantError, match="copied"):
        p.validate_written_clause(entry, h, task)


@pytest.mark.parametrize("item,price,product,accepted", [
    ("v2", 229_999_999, "general", True), ("v2", 230_000_000, "general", False),
    ("v5", 500_000_000, "general", True), ("v5", 100_000_000, "general", False),
    ("v6", 149_999_999, "general", True), ("v7", 150_000_000, "general", False),
    ("v14", 230_000_000, "general", True), ("v14", 230_000_000, "competitive", False),
    ("v15", 100_000_000, "general", True), ("v16", 230_000_000, "general", False),
    ("v17", 99_999_999, "general", True), ("v18", 100_000_000, "general", False),
    ("v12", 120_000_000, "general", True), ("v12", 120_000_000, "competitive", False),
    ("v13", 120_000_000, "competitive", True), ("v13", 120_000_000, None, False),
])
def test_writer_host_price_and_product_premises(item, price, product, accepted):
    h = host(입찰추정가격=price)
    assert p.writer_host_is_eligible(h, item, "violation", product) is accepted


def test_writer_host_contract_object_and_insertion_premises():
    h = host()
    assert p.writer_host_is_eligible(h, "v22", "violation", "general")
    h["meta"]["낙찰방법"] = "적격심사제"
    assert not p.writer_host_is_eligible(h, "v22", "violation", "general")
    assert not p.writer_host_is_eligible(h, "v19", "violation", "general")
    h["meta"]["업무구분"] = "물품(내자)"
    assert p.writer_host_is_eligible(h, "v19", "violation", "general")
    assert not p.writer_host_is_eligible(h, "v21", "violation", "general")
    h["meta"]["공동도급구성방식"] = "공동이행"
    assert p.writer_host_is_eligible(h, "v21", "violation", "general")
    assert not p.writer_host_is_eligible(h, "v20", "violation", "general")
    h["meta"]["업무구분"] = "일반용역"
    h["docs"][0]["text"] = h["docs"][0]["text"].replace("일반 시험용역", "소프트웨어 유지보수 용역")
    assert p.writer_host_is_eligible(h, "v20", "violation", None)
    h = host("다. 중소기업 확인서를 소지한 업체")
    assert not p.writer_host_is_eligible(h, "v16", "violation", "general")
    assert p.writer_host_is_eligible(h, "v16", "near_miss", "general")


def test_writer_catalog_premise_does_not_require_the_clause_whose_absence_is_targeted(monkeypatch):
    from tools.independent_gold import catalog_facts
    monkeypatch.setattr(catalog_facts, "resolve_record", lambda *_: {"summary": {"decision": "applicable"}})
    h = host()
    product = p.writer_product_class(h, None)
    assert product == "competitive"
    assert p.writer_host_is_eligible(h, "v10", "violation", product)
    h["docs"][0]["text"] += "\n직접생산확인증명서를 보유하여야 합니다."
    assert not p.writer_host_is_eligible(h, "v10", "violation", product)
    assert p.writer_host_is_eligible(h, "v10", "near_miss", product)


def test_writer_does_not_treat_low_price_alone_as_permission_for_small_quotes():
    h = host(계약방법="수의계약", 낙찰방법="기타", 입찰추정가격=30_000_000)
    assert not p.writer_host_is_eligible(h, "v2", "violation", "general")
    assert not p.writer_host_is_eligible(h, "v7", "violation", "general")


def test_writer_software_host_needs_a_software_object_not_generic_maintenance_or_portal():
    h = host("다. 전자입찰시스템 유지보수 안내를 참고하십시오.")
    h["docs"][0]["text"] = h["docs"][0]["text"].replace("일반 시험용역", "교실 시설물 유지보수")
    assert not p.writer_host_is_eligible(h, "v20", "violation", None)


def test_v3_selection_caps_deterministic_positives_but_keeps_all_near_and_written():
    rows = [{"split": "dev", "kind": "edited", "target_item": "v22", "plant_id": str(i),
             "operation": "written" if i >= 8 else "insert", "near_miss_items": ["v22"] if 4 <= i < 8 else []}
            for i in range(10)]
    chosen = s.select(rows, "dev", 2, include_all_near_written=True)
    assert len(chosen) == 8
    assert len([r for r in chosen if r["near_miss_items"]]) == 4
    assert len([r for r in chosen if r["operation"] == "written"]) == 2


@pytest.mark.parametrize("field,value", [
    ("clause", "가. 시행령 제13조의 자격을 갖춘 자"),
    ("clause", "[지역:r99|단위=기초|광역=경기도] 업체만 참여 가능합니다."),
    ("clause", "지방계약법을 적용합니다."),
    ("clause", "추정가격: 999원입니다."),
    ("clause", "수의계약으로 진행합니다."),
    ("clause", "하나입니다. 둘입니다. 셋입니다. 넷입니다."),
    ("label", 1), ("prompt_sha256", "0" * 64), ("insert_after_line", 999), ("writer_model", "")])
def test_written_rejects_copy_tokens_meta_sentence_count_and_provenance(field, value):
    h, task, entry = writer_pair()
    entry[field] = value
    with pytest.raises(p.PlantError):
        p.validate_written_clause(entry, h, task)


def test_evidence_locations_recomputed_after_document_removal():
    record = host()
    record, provenance = p.plant_clause(record, "새 표적 근거입니다.")
    row = p._v10_row(host(), record, provenance, "v22", "family")
    spans = p.evidence_spans(row)["v22"]
    assert all(record["docs"][a["doc_index"]]["text"][a["start"]:a["end"]] == a["text"] for a in spans)
    span = spans[0]
    assert s.evidence_match("새 표적", [record["docs"][0]["text"]], spans)["gold_overlap"] == 4 / len(span["text"])
    assert s.evidence_match("가짜 인용", [record["docs"][0]["text"]], spans) == {"source_substring": 0.0, "gold_overlap": 0.0}
    row["target_item"] = "v10"
    assert p.evidence_spans(row) == {}


def test_attachment_drop_must_keep_the_only_positive_clause(monkeypatch):
    h = host()
    h["meta"]["공동도급구성방식"] = "공동이행"
    h["docs"].append({"doc_id": "d1", "type": "제안요청서", "text": "공동이행 최소지분율 10% 이상"})
    record, provenance = p.rewrite_min_share(h)
    row = p._v10_row(h, record, provenance, "v21", "family")
    monkeypatch.setattr(p, "ATTACHMENT_DROP_RATE", 1.0)
    p.drop_attachments(row)
    assert len(row["record"]["docs"]) == 2
    assert p.evidence_spans(row)["v21"]


def test_holdout_cannot_score_without_a_ledger_and_round_is_reserved_once(tmp_path):
    key = {"test": {"split": "holdout", "targets": [], "expected_zero_items": []}}
    with pytest.raises(ValueError, match="ledger"):
        s.score_submission(key, {})
    ledger = tmp_path / "HOLDOUT_LEDGER.md"
    ledger.write_text("# ledger\n", encoding="utf-8")
    args = argparse.Namespace(key=tmp_path / "key.json", holdout_ledger=ledger,
                              round_id="round-1", source_fingerprint="a" * 64, purpose="aggregate only")
    s.reserve_holdout_score(args)
    with pytest.raises(ValueError, match="already"):
        s.reserve_holdout_score(args)
    assert "aggregate only" in ledger.read_text(encoding="utf-8")


def test_o7_retargets_title_and_overview_and_rejects_delegated_scope():
    from tools.independent_gold.catalog_facts import CatalogIndex
    catalog = CatalogIndex.load()
    h = host()
    record, provenance = p.retarget_procurement(h, "v10", catalog)
    assert provenance["catalog_code"] in record["docs"][0]["text"]
    assert "일반 시험용역" not in record["docs"][0]["text"]
    goods = copy.deepcopy(h)
    goods["meta"]["업무구분"] = "물품(내자)"
    sw, sw_provenance = p.retarget_procurement(goods, "v20", catalog)
    assert sw["meta"]["업무구분"] == "일반용역"
    assert sw_provenance["meta_changes"]["업무구분"]["from"] == "물품(내자)"
    h["docs"][0]["text"] += "\n세부 사항은 과업지시서 참조"
    with pytest.raises(p.PlantError):
        p.retarget_procurement(h, "v20", catalog)


def test_export_filters_split_before_json_decoding(tmp_path):
    path = tmp_path / "plants.jsonl"
    path.write_text('{"split":"holdout", bad sealed row}\n{"split":"dev","id":1}\n', encoding="utf-8")
    assert s._rows(path, "dev") == [{"split": "dev", "id": 1}]


def test_briefing_near_miss_checks_both_intervals():
    h = host("제안서 제출마감: 2026. 03. 30.", 적용계약법="지방계약법", 공고게시일자="20260301")
    record, _ = p.legal_near_miss(h, "v23", "general")
    assert "2026. 03. 09." in record["docs"][0]["text"]
    h["docs"][0]["text"] = h["docs"][0]["text"].replace("03. 30.", "03. 20.")
    record, provenance = p.legal_near_miss(h, "v23", "general")
    assert provenance["operation"] == "near_miss:no_briefing"
    assert "설명회는 개최하지 않습니다" in record["docs"][0]["text"]
