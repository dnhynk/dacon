"""Construction regressions for the G7 exclusions; invented source notices only."""
import copy
import gzip
import json

import pytest

from tools.independent_gold import cell_plant as p
from test_independent_cell_v10 import host, writer_pair
from test_independent_cell_plant import _size_host


@pytest.mark.parametrize("amount", ["132,000천원", "132000천원", "132,000,000원"])
def test_price_shift_preserves_units_and_exact_boundary_vat(amount):
    h = host(낙찰방법="적격심사제")
    h["docs"].append({"type": "규격서", "text": "사업예산: " + amount})
    result, _ = p.shift_price(h, "v5", exact_price=229_999_999)
    assert result["meta"]["배정예산금액"] == 252_999_999
    assert result["docs"][1]["text"] == "사업예산: " + ("252,999.999천원" if "천원" in amount else "252,999,999원")
    assert "229,999,999원" in result["docs"][0]["text"]
    assert h["meta"]["입찰추정가격"] == 120_000_000


def test_price_shift_table_unit_and_unformatted_repeated_amount():
    h = host(낙찰방법="적격심사제")
    h["docs"].append({"type": "규격서", "text": "단위: 천원\n사업예산 | 132000"})
    h["meta"]["비고"] = "추정가격 120000000원"
    result, _ = p.shift_price(h, "v5", exact_price=229_999_999)
    assert "252,999.999" in result["docs"][1]["text"]
    assert result["meta"]["비고"] == "추정가격 229,999,999원"


def test_price_shift_does_not_leave_unlabeled_thousand_won_table_cells():
    h = host(낙찰방법="적격심사제")
    h["docs"].append({"type": "규격서", "text": "단위: 천원\n추정가격 | 사업예산\n120000 | 132000"})
    result, _ = p.shift_price(h, "v5", exact_price=229_999_999)
    assert result["docs"][1]["text"].endswith("229,999.999 | 252,999.999")


@pytest.mark.parametrize("extra", ["사업예산: 132,000,000천원", "추정가격: 132,000,000원", "기초금액: 120,000,000원"])
def test_price_shift_rejects_unit_and_role_conflicts(extra):
    h = host(낙찰방법="적격심사제")
    h["docs"][0]["text"] += "\n" + extra
    with pytest.raises(p.PlantError):
        p.shift_price(h, "v5", exact_price=229_999_999)


@pytest.mark.parametrize("extra", ["혼합 품목 구매", "구매목록: 두 종류", "붙임 규격서 참조", "중소기업기본법 시행령 제8조", "단가/제한 입찰"])
def test_broaden_rejects_open_product_or_narrow_provision(extra):
    h = _size_host()
    h["docs"][0]["text"] += "\n" + extra
    with pytest.raises(p.PlantError):
        p.rewrite_size_scope(h, "broaden")


@pytest.mark.parametrize("name", ["소기업‧소상공인확인서", "소기업․ 소상공인확인서", "소기업·소\n상공인확인서", "소기업·소상공인\n확인서"])
def test_broaden_updates_certificate_separators_and_extraction_wraps(name):
    h = _size_host()
    h["docs"][0]["text"] += "\n제출서류: " + name
    result, _ = p.rewrite_size_scope(h, "broaden")
    assert result["docs"][0]["text"].endswith("중소기업확인서")


def test_broaden_synchronizes_the_metadata_eligibility_statement():
    h = _size_host()
    h["meta"]["조항호내용"] = "소기업, 소상공인 제한"
    result, provenance = p.rewrite_size_scope(h, "broaden")
    assert result["meta"]["조항호내용"] == "중소기업, 소상공인 제한"
    assert provenance["meta_changes"]["조항호내용"]["from"] == h["meta"]["조항호내용"]


@pytest.mark.parametrize("item", ["v10", "v11", "v20"])
@pytest.mark.parametrize("extra", ["구매규격: 식자재 50종", "등록업종: 식품판매업(1234)", "쌀 20kg 100포", "과업내용: 식품 운반"])
def test_o7_rejects_surviving_purchase_registration_and_task(item, extra):
    from tools.independent_gold.catalog_facts import CatalogIndex
    h = host(extra)
    with pytest.raises(p.PlantError):
        p.retarget_procurement(h, item, CatalogIndex.load())


def test_o7_synchronizes_the_entire_supported_task_field():
    from tools.independent_gold.catalog_facts import CatalogIndex
    h = host("과업내용: 일반 시험용역 납품", 업무구분="물품(내자)")
    result, _ = p.retarget_procurement(h, "v20", CatalogIndex.load())
    assert "과업내용: 소프트웨어 유지 및 기술지원 용역 수행" in result["docs"][0]["text"]
    assert "납품" not in result["docs"][0]["text"]


def written(item, mode, clause, extra=""):
    h, task, entry = writer_pair()
    h["meta"]["공동도급구성방식"] = "공동이행"
    h["docs"][0]["text"] += "\n" + extra
    task.update(item=item, mode=mode, host_sha256=p.sha256_text(p.canonical_json(h)))
    entry.update(item=item, mode=mode, clause=clause, label=int(mode == "violation"))
    return h, task, entry


@pytest.mark.parametrize("extra", ["구성원 최소지분율 10% 이상", "출자비율은 10% 이상", "분담비율은 협정서에 따릅니다."])
def test_written_share_cannot_repeal_cumulative_floor(extra):
    h, task, entry = written("v21", "violation", "구성원 지분율은 3% 이상으로 참여할 수 있습니다.", extra)
    with pytest.raises(p.PlantError, match="cumulative"):
        p.validate_written_clause(entry, h, task)


def test_written_share_without_a_surviving_floor_remains_review_pending():
    h, task, entry = written("v21", "violation", "구성원 지분율은 3% 이상으로 참여할 수 있습니다.")
    assert not p.plant_written_clause(entry, h, task)["eligible_for_scoring"]


def test_written_equivalence_cannot_bind_a_different_component():
    h, task, entry = written("v9", "near_miss", "완제품은 동등품도 허용합니다.", "규격: 소재는 A사 제품이어야 한다.")
    with pytest.raises(p.PlantError, match="component-specific"):
        p.validate_written_clause(entry, h, task)


def test_corporate_clients_do_not_make_a_public_client_restriction():
    clause = "국가기관 또는 법인이 발주한 실적이 있는 업체"
    assert p._ANY_CLIENT_RE.search(clause)
    h, task, entry = written("v4", "violation", clause)
    with pytest.raises(p.PlantError, match="restricted-client"):
        p.validate_written_clause(entry, h, task)


def test_supplement_preserves_sealed_families_and_wording_without_decoding_records(tmp_path, monkeypatch):
    from tools.independent_gold import catalog_facts
    prior = tmp_path / "plants.jsonl"
    source = tmp_path / "natural.jsonl.gz"
    word = p.sha256_text(p._normalized_wording(["shared wording"]))
    prior.write_text('\n'.join([
        json.dumps({"split": "dev", "host_id": identity, "family_id": identity})
        for identity in ("safe", "collision")]) + '\n' +
        '{"split":"holdout","host_id":"sealed","family_id":"sealed","wording_hashes":["' + word +
        '"],"record":THIS_MUST_NOT_BE_DECODED}\n', encoding="utf-8")
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        for identity in ("safe", "collision"):
            h = host()
            h["id"] = identity
            handle.write(json.dumps(h) + "\n")
        handle.write('{"id":"sealed","record":THIS_MUST_NOT_BE_DECODED}\n')
    monkeypatch.setattr(p, "POOL_INPUT", source)
    monkeypatch.setattr(catalog_facts.CatalogIndex, "load", lambda: None)
    monkeypatch.setattr(p, "product_class", lambda *_: None)
    monkeypatch.setattr(p, "bank_clauses", lambda *_: [])
    monkeypatch.setattr(p, "transplant_host_targets", lambda *_: {})
    def rejected(*_):
        raise p.PlantError("closed-scope fixture")
    monkeypatch.setattr(p, "retarget_procurement", rejected)
    def near(h, *_):
        phrase = "shared wording" if h["id"] == "collision" else "dev-only wording"
        return copy.deepcopy(h), {"operation": "shift_price:v5", "planted_body": phrase}
    monkeypatch.setattr(p, "legal_near_miss", near)
    result = p.build_pool_supplement(prior)
    assert [r["host_id"] for r in result["rows"]] == ["safe"]
    assert result["manifest"]["rejected_attempts"]["shift_price:v5:v5:sealed_wording_collision"] == 1
    assert result["manifest"]["holdout_content_decoded"] is False
