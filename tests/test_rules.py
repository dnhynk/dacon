import pytest

from pps.rules import joint_share_check, narrow_region_check, apply_rules


def notice(text, law="국가계약법", mode="공동이행", work="일반용역"):
    return {"id": "rule-probe", "meta": {"적용계약법": law, "공동도급구성방식": mode,
                                          "업무구분": work},
            "docs": [{"type": "공고문", "doc_id": "rule-probe", "text": text}]}


@pytest.mark.parametrize("law,share,expected", [
    ("국가계약법", "0.5", 1), ("국가계약법", "5", 1), ("국가계약법", "10", 0),
    ("지방계약법", "3", 1), ("지방계약법", "5", 0),
])
def test_joint_share_legal_floor_and_exact_evidence(law, share, expected):
    text = f"공동이행 구성원별 최소 지분율은 {share}% 이상이다."
    result = joint_share_check(notice(text, law))
    assert result["value"] == expected
    assert result["evidence"] == (text if expected else "")


def test_adjustment_requires_relevant_legal_clause_and_scope():
    unrelated = "공동이행 구성원별 최소 지분율은 4% 이상이다. 가격평가 배점은 20%이다."
    assert joint_share_check(notice(unrelated, "지방계약법"))["value"] == 1
    local = "일반용역 공동이행 구성원별 최소 지분율을 조정하여 4% 이상으로 한다."
    assert joint_share_check(notice(local, "지방계약법"))["value"] == 1
    state = ("계약담당공무원이 공동계약운용요령 제9조제5항에 따라 최소지분율을 20% 범위 내에서 조정한다. "
             "공동이행 구성원별 최소 지분율은 8% 이상이다.")
    result = joint_share_check(notice(state))
    assert result["value"] == 0 and [p["value"] for p in result["parsed"]] == [8.0]


@pytest.mark.parametrize("text", [
    "공동이행 구성원별 출자비율은 5% 이상으로 한다.",
    "공동이행 구성원별 최소 지분율은 10%에서 5%로 변경한다.",
    "공동이행 구성원별 최소 지분율은 5% 미만도 허용한다.",
    "서로 다른 법령에 따라 업종 간 공동수급체를 구성하므로 최소지분율을 적용하지 않는다. 구성원별 최소 지분율 3%를 허용한다.",
])
def test_ambiguous_or_exceptional_share_preserves_model_judgment(text):
    rec = notice(text)
    assert joint_share_check(rec) is None
    row = {"v21": 1, "e21": text}
    after, checks = apply_rules(rec, row)
    assert after == row and checks == []


def test_metadata_conflict_construction_and_missing_statement():
    text = "공동이행 구성원별 최소 지분율은 5% 이상으로 한다."
    assert joint_share_check(notice(text, mode="분담이행")) is None
    assert joint_share_check(notice(text, work="공사")) is None
    assert joint_share_check(notice("공동수급 불허. 지분율 기재 없음.")) is None


@pytest.mark.parametrize("unit", ["%", "％", "퍼센트"])
@pytest.mark.parametrize("law,share,expected", [("국가계약법", 7, 1), ("국가계약법", 10, 0),
                                               ("지방계약법", 3, 1), ("지방계약법", 5, 0)])
def test_joint_share_unit_variants_keep_source_and_legal_boundary(unit, law, share, expected):
    text = f"공동이행 구성원별 최소 지분율은 {share}{unit} 이상이다."
    check = joint_share_check(notice(text, law))
    assert check["value"] == expected
    assert check["evidence"] == (text if expected else "")


def test_unparsed_joint_conditions_cannot_clear_positive_model_answer():
    text = "공동이행 구성원별 출자비율은 오 퍼센트 이상으로 한다."
    rec = notice(text)
    row = {"v21": 1, "e21": text}
    after, checks = apply_rules(rec, row)
    assert after == row and checks == []


def test_basic_region_token_is_not_collapsed_to_parent_province():
    text = "주된 영업소가 서울특별시 [지역:r1|단위=기초|광역=서울특별시] 내에 소재하고 있는 업체이어야 합니다."
    rec = notice(text, law="지방계약법")
    rec["meta"].update({"입찰추정가격": 50_000_000,"계약방법":"제한경쟁"})
    result = narrow_region_check(rec)
    assert result["value"] == 1 and result["evidence"] == text
    rec["meta"]["계약방법"] = "수의계약"
    assert narrow_region_check(rec) is None
    rec["meta"]["계약방법"] = "제한경쟁"
    rec["docs"][0]["text"] = "물품구매 수의계약 안내공고\n\n" + text
    assert narrow_region_check(rec) is None
    rec["docs"][0]["text"] = "납품장소는 서울특별시 [지역:r1|단위=기초|광역=서울특별시] 내입니다."
    assert narrow_region_check(rec) is None
