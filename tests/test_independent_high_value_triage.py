"""Regression checks for source-only high-value routing, not label quality."""

from __future__ import annotations

import copy

import pytest

from tools.independent_gold import high_value_triage as triage


def _record(text: str, *, complete: bool = True, meta: dict | None = None) -> dict:
    return {
        "id": "PPS-D-TEST",
        "docs": [{"doc_id": "D0", "type": "공고문", "text": text}],
        "dropped_doc_counts": {} if complete else {"과업지시서": 1},
        "input_completeness": {"공고문_수집": True, "과업지시서": complete},
        "meta": meta or {},
        "anon_applied": True,
    }


def test_source_cues_are_not_answers_and_missing_source_is_retained() -> None:
    record = _record("직접생산확인증명서와 중소기업 확인서. 다만 예외를 적용한다.", complete=False)
    features = triage.source_features(record)
    assert "direct_production" in features["signals"]
    assert "small_business" in features["signals"]
    assert features["cross_rule_cue"]
    assert features["complexity_cue"]
    assert not features["source_complete"]
    assert "label" not in features


def test_price_boundary_and_metadata_difference_are_only_review_cues() -> None:
    features = triage.source_features(
        _record("참가자격에 중소기업 확인서를 요구한다.", meta={"입찰추정가격": 230_000_000, "지역제한여부": "Y"})
    )
    assert features["price_boundaries_within_5pct_won"] == [230_000_000]
    assert features["region_meta_without_clause_cue"]
    assert features["high_signal"]
    assert "decision" not in features


def test_model_or_gold_fields_are_rejected() -> None:
    record = _record("중소기업 확인서를 요구한다.")
    record["production_prediction"] = {"v1": 0}
    with pytest.raises(ValueError, match="non-organizer|forbidden"):
        triage.source_features(record)


def test_pilot_is_deterministic_and_pairs_are_not_propagation() -> None:
    def row(record_id: str, family: str, tier: str, representative: bool) -> dict:
        return {
            "id": record_id,
            "source_sha256": record_id,
            "family_id": family,
            "family_representative": representative,
            "tier": tier,
            "features": {"source_complete": True},
        }

    plan = {
        "schema_version": triage.SCHEMA,
        "rows": [
            row("A", "F1", "priority_blind_annotation", True),
            row("B", "F1", "family_sibling_deferred", False),
            row("C", "F2", "single_or_weak_cue_sample", True),
        ],
    }
    plan["content_sha256"] = triage.sha_object(plan)
    first = triage.build_pilot(plan, quotas={"single_or_weak_cue_sample": 1}, family_pairs=1)
    assert first == triage.build_pilot(plan, quotas={"single_or_weak_cue_sample": 1}, family_pairs=1)
    assert first["unique_records"] == 3
    assert {entry["id"] for entry in first["rows"]} == {"A", "B", "C"}
    assert first["binary_answers_generated"] is False
    assert first["labels_or_model_responses_consumed"] is False
    tampered = copy.deepcopy(plan)
    tampered["rows"][0]["tier"] = "source_gap_review"
    with pytest.raises(ValueError, match="hash differs"):
        triage.build_pilot(tampered, quotas={}, family_pairs=0)
