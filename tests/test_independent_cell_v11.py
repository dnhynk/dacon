"""Price premises and expanded legal controls, using invented notices only."""
import pytest
import json

from tools.independent_gold import cell_plant as p, cell_score as s
from test_independent_cell_v10 import host


@pytest.mark.parametrize("item", ["v2", "v3", "v5", "v16", "v17", "v18", "v24"])
def test_near_rejects_placeholder_price(item):
    h = host(입찰추정가격=1, 배정예산금액=1)
    with pytest.raises(p.PlantError, match="price premise"):
        p.legal_near_miss(h, item, "general")


def test_transplants_reject_price_targets_but_keep_unrelated_items():
    targets = p.transplant_host_targets(host(입찰추정가격=1, 배정예산금액=1), "general")
    assert not set(targets) & {"v2", "v3", "v7", "v8", "v14", "v15"}
    assert "v4" in targets


def test_v16_can_insert_a_lawful_sme_gate_without_an_existing_gate():
    record, prov = p.legal_near_miss(host(), "v16", "general")
    assert p.has_size_gate(record)
    assert p.host_size_scope(record) == "sme"
    assert prov["operation"] == "near_miss:insert_sme_gate"


def test_v23_no_briefing_is_a_closed_zero_without_inventing_dates():
    record, prov = p.legal_near_miss(host(적용계약법="지방계약법"), "v23", "general")
    assert "설명회는 개최하지 않습니다" in record["docs"][0]["text"]
    assert prov["operation"] == "near_miss:no_briefing"


def test_v23_preserves_lawful_explicit_schedule():
    h = host("제안서 제출마감: 2026. 11. 30.", 적용계약법="지방계약법", 공고게시일자="20260901")
    record, prov = p.legal_near_miss(h, "v23", "general")
    assert "2026. 09. 09." in record["docs"][0]["text"]
    assert "개최하지" not in record["docs"][0]["text"]


def test_unit_price_needs_a_matching_total_role():
    h = host()
    h["docs"][0]["text"] = h["docs"][0]["text"].replace("추정가격: 120,000,000원", "단가계약: 품목 단가 합계 120,000,000원").replace("배정예산: 132,000,000원", "연간 사용료 132,000,000원")
    assert not p.price_premise_is_valid(h)
    h["docs"][0]["text"] += "\n총사업금액: 132,000,000원"
    assert p.price_premise_is_valid(h)


def test_unit_price_empty_amount_cells_are_not_numbers():
    h = host()
    h["docs"][0]["text"] = "단가계약\n배정예산: ,원\n총사업비: 원"
    assert not p.price_premise_is_valid(h)


@pytest.mark.parametrize("amount,price,budget", [("75,000천원", 68_181_818, 75_000_000),
                                               ("약1억원", 100_000_000, 100_000_000)])
def test_unit_contract_with_explicit_scaled_project_budget_is_allowed(amount, price, budget):
    h = host(입찰추정가격=price, 배정예산금액=budget)
    h["docs"][0]["text"] = "단가계약\n사업예산: " + amount
    assert p.price_premise_is_valid(h)


def test_incidental_unit_cost_research_is_not_a_unit_contract():
    h = host()
    h["docs"][0]["text"] = "어업재해 복구품목 단가 연구용역"
    assert p.price_premise_is_valid(h)


@pytest.mark.parametrize("operation", [p.diverge_meta_price, p.retag_title_band,
                                      lambda h: p.shift_price(h, "v14")])
def test_price_mutations_cannot_turn_a_placeholder_into_a_valid_premise(operation):
    with pytest.raises(p.PlantError, match="price premise"):
        operation(host(입찰추정가격=1, 배정예산금액=1))


def test_top_level_envelope_never_decodes_record():
    raw = '{"extra_targets":[{"target_item":"v7"}],"record":{"secret":"x"},"target_item":"v2","split":"holdout","family_id":"f"}'
    assert p.plant_envelope(raw) == {"target_item": "v2", "split": "holdout", "family_id": "f"}


def test_selection_has_separate_near_quota_and_keeps_all_written():
    rows = [{"plant_id": str(i), "target_item": "v2", "split": "dev", "kind": "edited",
             "near_miss_items": ["v2"] if i > 3 else [], "operation": "written" if i in (3, 8) else "edit"}
            for i in range(10)]
    chosen = s.select(rows, "dev", 2, near_per_item=3, include_all_written=True)
    assert len(chosen) == 7
    assert {"3", "8"} <= {r["plant_id"] for r in chosen}


def test_opaque_exclusions_cover_extra_targets_and_preserve_license_axis(tmp_path):
    path = tmp_path / "plants.jsonl"
    common = {"split": "dev", "host_id": "bad", "target_item": "v24"}
    rows = [{**common, "operation": "diverge_meta_price"},
            {**common, "operation": "swap_meta_license"},
            {**common, "target_item": "v1", "operation": "insert_institution_limit",
             "extra_targets": [{"target_item": "v6", "operation": "narrow_region"}]}]
    path.write_text('\n'.join(json.dumps(r) for r in rows), encoding="utf-8")
    path.with_name("price_exclusions.json").write_text(json.dumps({
        "invalid_host_ids": ["bad"], "price_premised_items": sorted(p.PRICE_PREMISED_ITEMS)}), encoding="utf-8")
    assert s._rows(path, "dev") == [rows[1]]


def test_new_control_wording_is_separate_by_partition():
    for item in ("v16", "v23"):
        h = host(적용계약법="지방계약법")
        a, _ = p.legal_near_miss(h, item, "general", wording_partition="dev")
        b, _ = p.legal_near_miss(h, item, "general", wording_partition="holdout")
        assert a != b
        if item == "v16":
            assert p.host_size_scope(a) == p.host_size_scope(b) == "sme"
