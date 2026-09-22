from __future__ import annotations

import copy
import importlib.util
import pathlib

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "law_context.py"
SPEC = importlib.util.spec_from_file_location("independent_law_context", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_every_group_context_is_bounded_hash_checked_and_source_exact():
    for group in MODULE.GROUP_ITEMS:
        context = MODULE.build_law_context(group)
        assert MODULE.validate_law_context(context) == []
        assert len(MODULE.render_law_context(context)) <= MODULE.DEFAULT_MAX_CHARS
        assert "labels" not in MODULE.render_law_context(context).casefold()


def test_critical_numeric_rules_are_present_in_relevant_groups():
    performance = MODULE.render_law_context(MODULE.build_law_context("v1-4"))
    assert "2억 3천만 원" in performance
    assert "고시금액 미만" in performance
    assert "해당 용역계약의 추정가격" in performance
    assert "고시한 금액 이상이어야 한다" in performance
    assert "기획재정부장관이 정하여 고시한 금액 이상 특수한 기술이 요구되는 용역" in performance
    assert "1배" in performance

    region = MODULE.render_law_context(MODULE.build_law_context("v5-8"))
    assert "3억 3천만원" in region
    assert "1억 5천만원" in region
    assert "5억원" in region
    assert "추정가격5.0억원미만" in region
    assert "추정가격 1억원 이하" in region
    assert "인접 시·군" in region
    assert "중소기업 제품을 제조ㆍ구매하는 경우" in region
    assert "제공하는 용역" in region
    assert "경쟁제품에 대하여는" in region
    assert "중소기업자만을 대상으로 하는 제한경쟁" in region
    assert "제품조달계약을 체결하려면" in region
    assert "직접생산 여부를 확인" in region

    region_reference_ids = {
        row["reference_id"]
        for row in MODULE.build_law_context("v5-8")["references"]
    }
    assert {
        "local_international_tender_sme_product_exclusion",
        "sme_product_definition_includes_services",
        "competitive_product_contract_method_for_local_scope",
        "competitive_product_direct_production_for_local_scope",
    } <= region_reference_ids

    enterprise = MODULE.render_law_context(MODULE.build_law_context("v14-19"))
    assert "1억원 미만" in enterprise
    assert "소기업" in enterprise
    assert "중소기업자" in enterprise
    assert "국가를 당사자로 하는 계약에 관한 법률」 제4조제1항" in enterprise
    assert "고시한 금액 미만의 물품 및 용역" in enterprise

    software = MODULE.render_law_context(MODULE.build_law_context("v20"))
    assert "20억원 이상" in software
    assert "40억원 이상" in software
    assert "80억원 이상" in software
    assert "8천억원" in software

    joint = MODULE.render_law_context(MODULE.build_law_context("v21-23"))
    assert "10% 이상" in joint
    assert "5% 이상" in joint
    assert "1억원 미만인 경우 10일" in joint


def test_tampering_quote_coordinate_or_hash_is_rejected():
    context = MODULE.build_law_context("v20")
    forged = copy.deepcopy(context)
    forged["references"][0]["source"]["start"] += 1
    assert any("quote_mismatch" in error or "anchor_mismatch" in error for error in MODULE.validate_law_context(forged))

    forged = copy.deepcopy(context)
    forged["references"][0]["source"]["source_sha256"] = "0" * 64
    assert any("source_hash_mismatch" in error for error in MODULE.validate_law_context(forged))


def test_too_small_budget_refuses_to_drop_mandatory_law():
    with pytest.raises(ValueError, match="mandatory law reference"):
        MODULE.build_law_context("v20", max_chars=1000)


def test_manifest_hash_is_stable_and_complete():
    first = MODULE.reference_manifest_sha256()
    second = MODULE.reference_manifest_sha256()
    assert first == second
    assert len(first) == 64


def test_module_has_no_runtime_prediction_or_label_dependency():
    source = MODULE_PATH.read_text(encoding="utf-8").casefold()
    forbidden = (
        "import submission",
        "from submission",
        "saved" + "_response",
        "production_prediction",
        "dev_labels.csv",
    )
    assert not any(token in source for token in forbidden)
