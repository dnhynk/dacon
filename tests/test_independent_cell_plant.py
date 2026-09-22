"""CPU-only invariants for planted-violation notices; no model call."""

import copy

import pytest

from tools.independent_gold import cell_plant as plant


HOST_TEXT = "\n".join([
    "1. 입찰에 부치는 사항",
    "가. 건명: 시험 용역",
    "3. 입찰참가자격",
    "가. 시행령 제13조의 자격을 갖춘 자 [지역:r1|단위=기초|광역=경기도]",
    "  - 세부 등록 요건",
    "나. 부정당업자 제재를 받지 않은 자",
    "",
    "4. 입찰서 제출",
    "가. 전자입찰",
])


def _host(text=HOST_TEXT):
    return {
        "id": "HOST-1",
        "docs": [
            {"doc_id": "d0", "type": "공고문", "text": text},
            {"doc_id": "d1", "type": "과업지시서", "text": "과업 내용"},
        ],
        "meta": {"입찰추정가격": 50_000_000, "배정예산금액": 55_000_000, "낙찰방법": "적격심사제"},
    }


@pytest.mark.parametrize("clause, body", [
    ("라. 본 용역은 대학만 참여 가능함", "본 용역은 대학만 참여 가능함"),
    ("2) 최근 5년 이내 실적이 있어야 합니다.", "최근 5년 이내 실적이 있어야 합니다."),
    ("③ 최근 5년 이내 납품한 실적", "최근 5년 이내 납품한 실적"),
    ("○ | 동종업종 실적 5천만원 이상 보유한 업체 |", "동종업종 실적 5천만원 이상 보유한 업체"),
    ("o 사업설명회 : 별도 개최", "사업설명회 : 별도 개최"),
    ("50,000,000원(VAT별도) 이상 실적", "50,000,000원(VAT별도) 이상 실적"),
    ("2026. 2. 6.(금) 설명회", "2026. 2. 6.(금) 설명회"),
])
def test_only_the_source_list_marker_is_removed(clause, body):
    assert plant.strip_leading_marker(clause) == body


def test_section_is_found_and_next_marker_continues_the_host_list():
    section = plant.find_qualification_section(HOST_TEXT)
    assert (section["heading_line"], section["end_line"]) == (2, 7)
    assert (section["style"], section["last_marker"]) == ("ga", "나")
    assert plant.next_marker("ga", "나") == "다."
    assert plant.next_marker("circled", "②") == "③"
    assert plant.next_marker("paren", "6") == "7)"
    assert plant.next_marker("bullet", "○") == "○"


@pytest.mark.parametrize("text", [
    "3. 국가종합전자조달시스템 입찰참가자격등록규정\n가. 항목\n4. 다음",
    "3. 입찰참가자격\n가. 항목만 있고 다음 절이 없음",
    "3. 입찰참가자격\n목록 표식이 없는 문장\n4. 입찰서 제출",
])
def test_unparsable_hosts_fail_closed(text):
    with pytest.raises(plant.PlantError):
        plant.find_qualification_section(text)


def test_plant_changes_one_line_of_the_notice_and_nothing_else():
    host = _host()
    original = copy.deepcopy(host)
    clause = "라. 주된 영업소가 서울특별시 [지역:r1|단위=기초|광역=서울특별시] 내에 소재하는 업체"
    planted, provenance = plant.plant_clause(host, clause)
    assert host == original
    assert planted["id"] == host["id"] and planted["meta"] == host["meta"]
    assert planted["docs"][1] == host["docs"][1]
    new_lines = planted["docs"][0]["text"].split("\n")
    old_lines = HOST_TEXT.split("\n")
    assert [line for line in new_lines if line not in old_lines] == [provenance["planted_line"]]
    assert new_lines.index(provenance["planted_line"]) == 6
    assert provenance["planted_line"].startswith("다. 주된 영업소가")
    # The host already uses r1, so the transplanted region gets a fresh symbol.
    assert "[지역:r2|단위=기초|광역=서울특별시]" in provenance["planted_line"]
    text = planted["docs"][0]["text"]
    assert text[provenance["start"]:provenance["end"]] == provenance["planted_line"]
    assert "label" not in plant.canonical_json(planted)


def test_host_eligibility_encodes_each_item_necessary_condition():
    host = _host()
    assert plant.host_is_eligible("v2", "S", host)
    assert plant.host_is_eligible("v6", "S", host)
    assert not plant.host_is_eligible("v5", "S", host)
    assert not plant.host_is_eligible("v14", "S", host)
    assert not plant.host_is_eligible("v22", "S", host)
    small_quote = _host()
    small_quote["meta"]["낙찰방법"] = plant.SMALL_QUOTE_AWARD
    assert not plant.host_is_eligible("v2", "S", small_quote)
    assert not plant.host_is_eligible("v7", "S", small_quote)
    # v3 needs the stated prior-performance amount to exceed the host budget with margin.
    assert plant.host_is_eligible("v3", "PPS-DEV-042", host)
    rich = _host()
    rich["meta"].update({"입찰추정가격": 90_000_000, "배정예산금액": 99_000_000})
    assert not plant.host_is_eligible("v3", "PPS-DEV-042", rich)


SIZE_TEXT = "\n".join([
    "3. 입찰참가자격",
    "가. 시행령 제13조의 자격을 갖춘 자",
    "나. 「중소기업기본법」 제2조 제2항에 따른 소기업 또는 「소상공인기본법」 제2조에 따른",
    "   소상공인으로서 소기업·소상공인 확인서를 소지한 자",
    "※ <소기업·소상공인확인서>가 중소기업제품 공공구매 종합정보망에서 확인되지 않을 경우 자격이 없습니다.",
    "다. 부정당업자 제재를 받지 않은 자",
    "4. 제출서류",
    "가. 입찰참가신청서 1부",
    "나. 소기업 또는 소상공인 확인서 1부.",
    "다. 청렴서약서 1부",
    "",
    "5. 본 입찰은 「중소기업제품 구매촉진 및 판로지원에 관한 법률」을 따릅니다.",
])


def _size_host(text=SIZE_TEXT, price=50_000_000):
    host = _host(text)
    host["meta"].update({"입찰추정가격": price, "배정예산금액": round(price * 1.1)})
    return host


@pytest.mark.parametrize("line, scope", [
    ("「중소기업기본법」 제2조 제2항에 따른 소기업 또는 소상공인", "small"),
    ("발급된 중기업·소기업·소상공인확인서를 소지한 업체", "sme"),
    ("중·소기업·소상공인 확인서를 소지한 업체", "sme"),
    ("「중소기업기본법」제2조에 따른 중소기업자 또는 소상공인", "sme"),
    ("중소기업 또는 소상공인으로서 발급된 소기업·소상공인확인서를 소지한 자", "mixed"),
    ("「소상공인 보호 및 지원에 관한 법률」 제2조에 따른 소상공인", "neutral"),
    ("중소기업제품 공공구매 종합정보망에서 확인", None),
    ("본 입찰은 중소기업자간 경쟁제품 입찰입니다", None),
])
def test_size_scope_reads_restrictions_and_ignores_law_and_scheme_names(line, scope):
    assert plant.size_scope(line) == scope


def test_delete_removes_clause_note_and_submission_line_and_closes_list_gaps():
    host = _size_host()
    edited, provenance = plant.delete_size_restriction(host)
    assert host == _size_host()
    lines = edited["docs"][0]["text"].split("\n")
    assert lines[:3] == ["3. 입찰참가자격", "가. 시행령 제13조의 자격을 갖춘 자", "나. 부정당업자 제재를 받지 않은 자"]
    assert lines[3:6] == ["4. 제출서류", "가. 입찰참가신청서 1부", "나. 청렴서약서 1부"]
    # A law title is not a size restriction and must survive untouched.
    assert lines[-1] == SIZE_TEXT.split("\n")[-1]
    assert len(provenance["removed_lines"]) == 4
    assert plant.host_size_scope(edited) is None


def test_delete_fails_closed_when_the_edit_would_not_be_surgical():
    shared = SIZE_TEXT.replace("소지한 자", "소지하고 본점 소재지가 경기도인 자")
    with pytest.raises(plant.PlantError, match="shares a list item"):
        plant.delete_size_restriction(_size_host(shared))
    packed = SIZE_TEXT.replace("나. 소기업 또는 소상공인 확인서 1부.", "나. ① 신청서 ② 등기부등본 ③ 소기업 확인서 ④ 서약서")
    with pytest.raises(plant.PlantError, match="shares a list item"):
        plant.delete_size_restriction(_size_host(packed))
    other_doc = _size_host()
    other_doc["docs"][1]["text"] = "과업: 소기업 제한 입찰"
    with pytest.raises(plant.PlantError, match="survives"):
        plant.delete_size_restriction(other_doc)
    with pytest.raises(plant.PlantError, match="no enterprise-size"):
        plant.delete_size_restriction(_host())


@pytest.mark.parametrize("text, reason", [
    # PPS-D-007494: the host's own list already skips markers (나 -> 마).
    ("3. 입찰참가자격\n가. 자격을 갖춘 자\n나. 소기업·소상공인 확인서를 소지한 자\n마. 조세포탈 제외\n4. 기타",
     "not consecutive"),
    # PPS-D-008292: a wrapped note continues after a blank line and would be left as an orphan.
    ("3. 입찰참가자격\n가. 소기업·소상공인 확인서를 소지한 업체\n\n※ <소기업·소상공인확인서>는 정보망으로\n\n확인하며 확인이 안 될 경우 자격이 없습니다.\n\n4. 기타",
     "continues past a blank line"),
    # PPS-D-008995: one physical line carries an unrelated note and the start of the size item.
    ("3. 입찰참가자격\n가. 자격을 갖춘 자\n※ 내역서를 확인하시기 바랍니다. 라. 관련 법령에 따라 소기업 확인서를 소지한 업체\n4. 기타",
     "shares a list item"),
    # PPS-D-002988: the size clause also restricts the bidder's address.
    ("3. 입찰참가자격\n가. 소기업·소상공인의 자격을 구비해야 하고 사업장의 주소가 전라북도에 소재하는 업체\n4. 기타",
     "shares a list item"),
    # PPS-D-019543 / PPS-D-020681: a form field or a payment-scheme sentence is not a restriction.
    ("3. 입찰참가자격\n가. 자격을 갖춘 자\n4. 업체현황\n가. 대기업( ), 소기업( )", "no enterprise-size restriction"),
])
def test_delete_regressions_found_by_the_teacher_check(text, reason):
    with pytest.raises(plant.PlantError, match=reason):
        plant.delete_size_restriction(_size_host(text))


def test_portal_check_lines_are_part_of_the_size_restriction():
    # PPS-D-003563: the surviving portal note still read as a size-linked requirement.
    text = "\n".join([
        "3. 입찰참가자격",
        "가. 중소기업 확인서를 소지한 자",
        "※ 확인서는 공공구매 종합정보망(www.smpp.go.kr)에서 확인되지 않을 경우 자격이 없습니다.",
        "나. 부정당업자가 아닌 자",
        "4. 기타",
    ])
    edited, provenance = plant.delete_size_restriction(_size_host(text))
    assert edited["docs"][0]["text"].split("\n") == ["3. 입찰참가자격", "가. 부정당업자가 아닌 자", "4. 기타"]
    assert len(provenance["removed_lines"]) == 2


def test_a_certificate_validity_note_goes_with_the_size_clause():
    # PPS-D-017888: "※ 확인서는 … 발급된 것으로 유효기간 내" named no size, survived, and read as an orphan.
    text = "\n".join([
        "3. 입찰참가자격",
        "가. 소기업ㆍ소상공인 확인서를 소지한 업체이어야 합니다.",
        "※ 확인서는 전자입찰서 제출 마감일 전일까지 발급된 것으로 유효기간 내에 있어야 합니다.",
        "나. 부정당업자가 아닌 자",
        "4. 기타",
    ])
    edited, _ = plant.delete_size_restriction(_size_host(text))
    assert edited["docs"][0]["text"].split("\n") == ["3. 입찰참가자격", "가. 부정당업자가 아닌 자", "4. 기타"]
    # PPS-D-017888 (second pass): a note that names nothing at all hangs on the deleted clause.
    dangling = text.replace("※ 확인서는 전자입찰서 제출 마감일", "※ 입찰서 제출 마감일")
    with pytest.raises(plant.PlantError, match="would be orphaned"):
        plant.delete_size_restriction(_size_host(dangling))
    # The same kind of note about a direct-production certificate is not a size statement.
    assert not plant._is_size_line("※ 직접생산확인증명서는 제출마감일 전일까지 발급된 것으로 유효기간 내에 있어야 합니다.")


def test_a_size_word_that_is_not_a_requirement_gives_no_scope_to_rewrite():
    form = "3. 입찰참가자격\n가. 자격을 갖춘 자\n4. 업체현황\n가. 대기업( ), 중소기업( )"
    assert plant.host_size_scope(_size_host(form)) is None
    payment = "3. 입찰참가자격\n가. 자격을 갖춘 자\n5. 기타\n· 기관은 중소기업과의 상생협력을 강화하고자 상생결제를 운영합니다."
    assert plant.host_size_scope(_size_host(payment)) is None
    with pytest.raises(plant.PlantError, match="uniform scope"):
        plant.rewrite_size_scope(_size_host(form), "narrow")
    # A requirement wrapped over several physical lines still counts.
    wrapped = "3. 입찰참가자격\n가. 「중소기업기본법」 제2조에 따른 소기업자로서\n   발급된 확인서를 소지한 자이어야 합니다.\n4. 기타"
    assert plant.host_size_scope(_size_host(wrapped)) == "small"


def test_scope_rewrite_synchronizes_definition_certificate_and_notes():
    broadened, provenance = plant.rewrite_size_scope(_size_host(), "broaden")
    text = broadened["docs"][0]["text"]
    assert "제2조에 따른 중소기업 또는" in text and "중소기업 확인서를 소지한 자" in text
    assert "제2항" not in text and "소기업·소상공인확인서" not in text
    assert "「중소기업기본법」" in text and "중중소기업" not in text
    assert plant.host_size_scope(broadened) == "sme"
    assert len(provenance["changed_lines"]) == 4
    narrowed, _ = plant.rewrite_size_scope(broadened, "narrow")
    assert plant.host_size_scope(narrowed) == "small"
    sme = "3. 입찰참가자격\n가. 중소기업자 또는 소상공인으로서 중·소기업·소상공인 확인서를 소지한 업체\n4. 기타"
    narrowed, _ = plant.rewrite_size_scope(_size_host(sme), "narrow")
    assert "가. 소기업자 또는 소상공인으로서 소기업·소상공인 확인서를 소지한 업체" in narrowed["docs"][0]["text"]
    with pytest.raises(plant.PlantError, match="uniform scope"):
        plant.rewrite_size_scope(_size_host(), "narrow")
    mixed = SIZE_TEXT.replace("소기업·소상공인 확인서를 소지한 자", "중소기업 확인서를 소지한 자")
    with pytest.raises(plant.PlantError, match="uniform scope"):
        plant.rewrite_size_scope(_size_host(mixed), "broaden")


@pytest.mark.parametrize("operation, product, price, target", [
    ("delete", "general", 50_000_000, "v18"),
    ("delete", "general", 150_000_000, "v16"),
    ("delete", "general", 300_000_000, None),
    ("delete", "competitive", 300_000_000, "v11"),
    ("narrow", "competitive", 50_000_000, "v13"),
    ("narrow", "general", 150_000_000, "v15"),
    ("narrow", "general", 50_000_000, None),
    ("broaden", "general", 50_000_000, "v17"),
    ("broaden", "general", 150_000_000, None),
    ("broaden", "competitive", 50_000_000, None),
    ("delete", None, 50_000_000, None),
])
def test_size_edit_target_follows_product_class_and_price_band(operation, product, price, target):
    assert plant.size_edit_target(operation, product, price) == target


def test_meta_price_divergence_keeps_the_document_and_the_price_band():
    host = _size_host(SIZE_TEXT + "\n6. 추정가격: 금50,000,000원", price=50_000_000)
    edited, provenance = plant.diverge_meta_price(host)
    assert edited["docs"] == host["docs"]
    new_price = edited["meta"]["입찰추정가격"]
    assert new_price != 50_000_000 and plant._band(new_price) == plant._band(50_000_000)
    assert f"{new_price:,}" not in host["docs"][0]["text"]
    assert provenance["evidence_line"] == "6. 추정가격: 금50,000,000원"
    with pytest.raises(plant.PlantError, match="verbatim"):
        plant.diverge_meta_price(_size_host())
    # A matching amount in another accounting role is not the estimated price.
    with pytest.raises(plant.PlantError, match="verbatim"):
        plant.diverge_meta_price(_size_host(SIZE_TEXT + "\n6. 기초금액: 금50,000,000원", price=50_000_000))
    spaced = _size_host(SIZE_TEXT + "\n6. 추 정 가 격\n금50,000,000원", price=50_000_000)
    assert plant.diverge_meta_price(spaced)[1]["evidence_line"] == "금50,000,000원"
    # 97,000,000 x any factor crosses the 1억 band edge, which would flip other items.
    edge = _size_host(SIZE_TEXT + "\n6. 추정가격: 금97,500,000원", price=97_500_000)
    with pytest.raises(plant.PlantError, match="price band"):
        plant.diverge_meta_price(edge)


QUALIFICATION = "\n".join([
    "1. 입찰에 부치는 사항",
    "가. 추정가격: 금60,000,000원 (부가가치세 6,000,000원 별도, 기초금액 66,000,000원)",
    "3. 입찰참가자격",
    "가. 입찰공고일 전일부터 주된 영업소의 소재지가 경기도에 있는 업체",
    "나. 공동수급(공동이행방식)을 허용하며 구성원별 최소지분율은 10% 이상이어야 합니다.",
    "다. 낙찰자는 제조사(공급사)의 기술지원 확약서를 계약체결 시 제출하여야 합니다.",
    "4. 입찰서 제출",
    "가. 전자입찰",
])


def _qualified_host(text=QUALIFICATION, price=60_000_000, **meta):
    host = _host(text)
    host["meta"].update({"입찰추정가격": price, "배정예산금액": round(price * 1.1),
                         "공동도급구성방식": "공동이행", "제한지역코드목록": "경기도", **meta})
    return host


def test_min_share_is_lowered_below_every_lawful_floor():
    edited, provenance = plant.rewrite_min_share(_qualified_host())
    assert provenance["from_percent"] == "10" and provenance["to_percent"] in plant.VIOLATING_SHARES
    assert f"최소지분율은 {provenance['to_percent']}% 이상" in edited["docs"][0]["text"]
    with pytest.raises(plant.PlantError, match="joint performance"):
        plant.rewrite_min_share(_qualified_host(QUALIFICATION.replace("공동이행", "분담이행"), 공동도급구성방식="분담이행"))
    with pytest.raises(plant.PlantError, match="one lawful minimum share"):
        plant.rewrite_min_share(_qualified_host(QUALIFICATION + "\n나. 최소지분율 5% 이상"))


def test_region_is_narrowed_to_a_basic_unit_only_where_that_is_a_violation():
    edited, provenance = plant.narrow_region(_qualified_host())
    assert "소재지가 경기도 [지역:r1|단위=기초|광역=경기도]에 있는 업체" in edited["docs"][0]["text"]
    assert edited["meta"]["제한지역코드목록"] == "[등록지역:r1|단위=기초|광역=경기도]"
    assert provenance["province"] == "경기도"
    with pytest.raises(plant.PlantError, match="regional ceiling"):
        plant.narrow_region(_qualified_host(price=200_000_000))
    with pytest.raises(plant.PlantError, match="small-quote"):
        plant.narrow_region(_qualified_host(낙찰방법=plant.SMALL_QUOTE_AWARD))
    two = QUALIFICATION.replace("경기도에 있는", "경기도 또는 인천광역시에 있는")
    with pytest.raises(plant.PlantError, match="exactly one"):
        plant.narrow_region(_qualified_host(two))


def test_pledge_timing_moves_before_the_bid_deadline():
    edited, provenance = plant.advance_pledge_timing(_qualified_host())
    assert provenance["changed_lines"][0]["to"] == (
        "다. 입찰참가자는 제조사(공급사)의 기술지원 확약서를 입찰서 제출 마감일 전일까지 제출하여야 합니다."
    )
    with pytest.raises(plant.PlantError, match="exactly one"):
        plant.advance_pledge_timing(_host())


def test_price_shift_rewrites_every_role_of_the_amount_consistently():
    edited, provenance = plant.shift_price(_qualified_host(), "v5")
    new_price = edited["meta"]["입찰추정가격"]
    assert 550_000_000 <= new_price < 900_000_000 and new_price % 1000 == 0
    tax = round(new_price * 0.1)
    assert edited["meta"]["배정예산금액"] == new_price + tax
    assert f"추정가격: 금{new_price:,}원 (부가가치세 {tax:,}원 별도, 기초금액 {new_price + tax:,}원)" in edited["docs"][0]["text"]
    assert "60,000,000" not in edited["docs"][0]["text"]
    with pytest.raises(plant.PlantError, match="other than this contract"):
        plant.shift_price(_qualified_host(QUALIFICATION + "\n나. 전년도 계약금액 12,345,000원"), "v5")
    with pytest.raises(plant.PlantError, match="in words"):
        plant.shift_price(_qualified_host(QUALIFICATION + "\n(금육천만원)"), "v5")
    with pytest.raises(plant.PlantError, match="below the target band"):
        plant.shift_price(_qualified_host(price=600_000_000), "v5")
    # PPS-D-000207: a shifted total no longer equals its own unit-price arithmetic, and a
    # small-quote procedure cannot carry a price above its ceiling.
    arithmetic = QUALIFICATION.replace("가. 추정가격: 금60,000,000원", "가. 추정가격: 24,000원 × 2,500개 = 금60,000,000원")
    with pytest.raises(plant.PlantError, match="unit-price calculation"):
        plant.shift_price(_qualified_host(arithmetic), "v5")
    with pytest.raises(plant.PlantError, match="small-quote"):
        plant.shift_price(_qualified_host(낙찰방법=plant.SMALL_QUOTE_AWARD), "v5")
    with pytest.raises(plant.PlantError, match="title band tag"):
        plant.shift_price(_qualified_host("시험 용역(수의계약·1억 미만)\n" + QUALIFICATION), "v5")


@pytest.mark.parametrize("text, won", [
    ("단일 건 3억 원(VAT포함) 이상의 실적", 300_000_000),
    ("1억 5천만원 이상 실적", 150_000_000),
    ("실적 5천만원 이상 보유한 업체", 50_000_000),
    ("455,000,000원 이상(기초금액의 130% 이상)", 455_000_000),
    ("1억원 이상 또는 2억원 이상", None),
    ("유사 실적이 있는 업체", None),
])
def test_stated_won_reads_one_amount_or_none(text, won):
    assert plant.stated_won(text) == won


@pytest.mark.parametrize("text, won", [("1.5천만원", 15_000_000), ("12천만원", 120_000_000),
                                     ("0.5억원", 50_000_000)])
def test_decimal_amount_is_never_read_from_its_tail(text, won):
    assert plant.stated_won(text) == won


@pytest.mark.parametrize("clause", [
    "공공기관 또는 사회복지시설에서 발주한 용역 실적이 있는 업체",
    "사회복지사업법에 의해 설립된 시설이나 공공기관에 납품실적이 있는 업체",
    "사회복지기관이나 공공기관에 납품 실적이 있는 업체",
    "국가기관, 대학, 병원에서 발주한 용역 실적이 있는 업체",
    "지방자치단체 입찰 및 계약 집행기준에 따라 실적이 있는 업체",
    "아래 기준에 따라 공공기관에서 발주한 실적이 있는 업체",
])
def test_doubtful_public_client_clauses_are_not_v4_sources(clause):
    host = _size_host("3. 입찰참가자격\n가. " + clause + "\n4. 기타")
    assert "v4" not in {c["target_item"] for c in plant.bank_clauses(host, "general")}


@pytest.mark.parametrize("clause", [
    "<삭제> 공공기관에서 발주한 실적이 있는 업체 <삭제>",
    "소프트웨어사업자 실적관리지침에 따라 등록을 완료한 업체",
    "아래 기준의 수행실적이 있는 업체",
])
def test_deleted_law_only_and_dangling_performance_clauses_are_not_sources(clause):
    host = _size_host("3. 입찰참가자격\n가. " + clause + "\n4. 기타")
    assert not plant.bank_clauses(host, "general")


def test_size_law_name_alone_cannot_be_narrowed():
    host = _size_host("3. 입찰참가자격\n가. 중소기업 구매촉진 및 판로지원에 관한 법률에 따른 확인서를 소지한 업체\n4. 기타")
    with pytest.raises(plant.PlantError):
        plant.rewrite_size_scope(host, "narrow")


def test_size_rewrite_preserves_unquoted_law_names_beside_real_requirements():
    law = "중소기업 구매촉진 및 판로지원에 관한 법률"
    host = _size_host("3. 입찰참가자격\n가. " + law + "에 따른 중소기업 확인서를 소지한 업체\n4. 기타")
    edited, _ = plant.rewrite_size_scope(host, "narrow")
    assert law + "에 따른 소기업 확인서를 소지한 업체" in edited["docs"][0]["text"]


def test_retention_replays_current_filters_and_requires_exact_input(monkeypatch):
    host = _host()
    row = {
        "plant_id": "synthetic", "host_id": host["id"], "family_id": "dev-family", "split": "dev",
        "kind": "edited", "operation": "edit", "target_item": "v1", "source_id": None,
        "record": copy.deepcopy(host), "provenance": {}, "extra_targets": [],
        "expected_zero_items": [item for item in plant.ITEMS if item not in set(plant.EDIT_AFFECTED["v1"]) | {"v24"}],
    }
    monkeypatch.setattr(plant, "_split_of", lambda family: "dev")
    monkeypatch.setattr(plant, "_apply_pool_edit", lambda *args: (copy.deepcopy(host), {}))
    monkeypatch.setattr(plant, "add_second_edit", lambda row: None)
    monkeypatch.setattr(plant, "drop_attachments", lambda row: None)
    args = ({host["id"]: host}, {"v1": {host["id"]: "edit"}}, [], {}, {}, None, [])
    kept, rejected = plant.retain_dev_edits([row], *args)
    assert len(kept) == 1 and not rejected
    row["record"]["docs"][0]["text"] += "\nchanged old input"
    kept, rejected = plant.retain_dev_edits([row], *args)
    assert not kept and rejected["replayed input or constructed cells changed"] == 1
    row["split"] = "holdout"
    with pytest.raises(plant.PlantError, match="only dev"):
        plant.retain_dev_edits([row], *args)


def test_review_never_renders_holdout_rows_but_keeps_official_dev_mode():
    record, provenance = plant.plant_clause(_host(), "중소기업 확인서를 소지한 업체")
    row = {"kind": "planted", "plant_id": "visible", "target_item": "v14", "source_id": "source",
           "host_id": record["id"], "provenance": provenance}
    hidden = dict(row, split="holdout", plant_id="hidden_holdout")
    seen = dict(row, split="holdout_seen_wording", plant_id="hidden_seen")
    review = plant.review_markdown([row, hidden, seen])
    assert "visible" in review and "hidden_holdout" not in review and "hidden_seen" not in review


def test_size_deletion_rejects_surviving_procedure_field():
    host = _size_host("입 찰 방 법: 제한경쟁, 중소기업자간경쟁\n" + SIZE_TEXT)
    with pytest.raises(plant.PlantError, match="procedure"):
        plant.delete_size_restriction(host)


@pytest.mark.parametrize("clause", [
    "소기업자로서 소\\n기업 확인서를 소지한 업체",
    "소상공인으로서 소기업 확인서를 소지한 업체",
    "소기업 확인서를 소지한 업체\\n※ 소상공인에 한함",
])
def test_broaden_rejects_unedited_narrow_subject_certificate_or_note(clause):
    host = _size_host("3. 입찰참가자격\n가. " + clause.replace("\\n", "\n") + "\n4. 기타")
    with pytest.raises(plant.PlantError):
        plant.rewrite_size_scope(host, "broaden")


def test_middle_dot_variant_is_not_a_small_only_source():
    host = _size_host("3. 입찰참가자격\n가. 중‧소기업 확인서를 소지한 업체\n4. 기타")
    assert plant.size_scope("중‧소기업 확인서") == "sme"
    assert "v15" not in {c["target_item"] for c in plant.bank_clauses(host, "general")}


def test_transplant_refuses_attachment_list_inside_apparent_qualification_section():
    host = _host(HOST_TEXT.replace("4. 입찰서 제출", "붙임 1. 신청서\n가. 서식 목록\n4. 입찰서 제출"))
    with pytest.raises(plant.PlantError, match="attachment"):
        plant.plant_clause(host, "중소기업 확인서를 소지한 업체")


def test_spec_equivalence_in_another_document_rejects_host_and_donor():
    host = _host(HOST_TEXT + "\n규격서와 동등 이상의 물품 납품 가능")
    host["docs"][1] = {"type": "규격서", "text": "규격서\n물품 1식"}
    with pytest.raises(plant.PlantError, match="equivalent"):
        plant.plant_spec_line(host, "모델명: Device AB-100")
    host["docs"][1]["text"] += "\n모델명: Device AB-100"
    assert plant.spec_model_lines(host) == []


@pytest.mark.parametrize("field,value", [("낙찰방법", plant.SMALL_QUOTE_AWARD), ("계약방법", "수의계약")])
@pytest.mark.parametrize("product,price,targets", [("competitive", 50_000_000, {"v10", "v11", "v13"}),
                                                  ("general", 50_000_000, {"v18"})])
def test_small_quote_size_and_direct_production_targets_are_excluded(monkeypatch, field, value, product, price, targets):
    host = _size_host(SIZE_TEXT.replace("소기업", "중소기업"), price)
    host["meta"][field] = value
    monkeypatch.setattr(plant, "_SIZE_OPERATIONS", {"delete": lambda record: ({}, {}), "narrow": lambda record: ({}, {})})
    monkeypatch.setattr(plant, "delete_direct_production", lambda record, catalog: ({}, {}))
    assert not (targets & plant.feasible_pool_edits(host, object(), product).keys())


def test_bank_takes_lawful_natural_clauses_and_hosts_are_matched_by_context():
    source_text = "\n".join([
        "3. 입찰참가자격",
        "가. 최근 3년 이내 공공기관이 발주한 단일 건 2억원 이상의 행사 수행실적이 있는 업체",
        "나. 주된 영업소의 소재지가 전라남도 또는 경상남도에 있는 업체",
        "다. 제안서 평가 시 수행실적을 배점에 반영합니다.",
        "4. 기타",
    ])
    source = _qualified_host(source_text, price=400_000_000)
    clauses = {(clause["target_item"], clause["clause"][:2]) for clause in plant.bank_clauses(source, "general")}
    # The evaluation-scoring line is not a participation gate and never enters the bank.
    assert clauses == {("v2", "가."), ("v3", "가."), ("v4", "가."), ("v8", "가."), ("v7", "나.")}
    threshold = [c for c in plant.bank_clauses(source, "general") if c["target_item"] == "v3"][0]["threshold_won"]
    assert threshold == 200_000_000
    # Real pool lines that looked like gates: a proof-document instruction, and a client list that
    # also admits private clients (so it restricts nothing).
    decoys = _qualified_host("\n".join([
        "3. 입찰참가자격",
        "가. 편의점으로 판매실적을 증명하는 경우 유통개소 중 2개소 이상 포스 영수증 원본 각 1매 첨부",
        "나. 최근 3년 이내 국가기관, 공공기관이나 민간업체에서 발주한 용역 실적이 20,000,000원 이상인 실적이 있는 업체",
        "4. 기타",
    ]), price=400_000_000)
    assert {(c["target_item"], c["clause"][:2]) for c in plant.bank_clauses(decoys, "general")} == {("v2", "나."), ("v3", "나."), ("v8", "나.")}
    # Below the notice threshold the same clause would already violate v2, so it is not a v2 source.
    cheap = {c["target_item"] for c in plant.bank_clauses(_qualified_host(source_text, price=90_000_000), "general")}
    assert "v2" not in cheap and {"v3", "v4", "v7"} <= cheap
    host = _host(HOST_TEXT)
    host["meta"].update({"입찰추정가격": 50_000_000, "배정예산금액": 55_000_000})
    assert plant.transplant_host_targets(host, "general") == {
        "v4": True, "v2": True, "v3": 55_000_000, "v7": True, "v12": True, "v19": True}
    # A host that already gates on prior performance or location cannot take another such clause.
    assert plant.transplant_host_targets(_qualified_host(source_text, price=50_000_000), None) == {"v19": True}
    sources = [{"target_item": "v3", "source_id": "SRC", "clause": "가. 2억원 이상 실적", "threshold_won": 200_000_000}]
    split = plant.clause_split(sources[0]["clause"])
    # The same wording with another amount or marker must land on the same side of the split.
    assert plant.clause_split("나.  5억원 이상 실적") == split
    names = (f"f-{number}" for number in range(1000))
    same = [name for name in names if plant._split_of(name) == split][:2]
    other = next(name for name in (f"g-{number}" for number in range(1000)) if plant._split_of(name) != split)
    families = {"SRC": "f-src", "HOST": same[0], "RICH": same[1], "ELSEWHERE": other}
    hosts = {"HOST": {"v3": 55_000_000}, "RICH": {"v3": 190_000_000}, "ELSEWHERE": {"v3": 10_000_000}}
    paired = plant.pair_transplants(sources, hosts, set(), families, 10)
    # RICH's budget is not clearly below the required amount; ELSEWHERE sits in the other split.
    assert set(paired) == {"HOST"}


def test_direct_production_bank_takes_the_requirement_and_checks_the_host_scope():
    text = "\n".join([
        "3. 입찰참가자격",
        "가. 직접생산확인증명서[세부품명: 기타인쇄물(5510159901)]를 소지한 업체여야 합니다.",
        "※ <직접생산확인증명서>는 제출마감일 전일까지 발급된 것으로 유효기간 내에 있어야 합니다.",
        "4. 기타",
    ])
    clauses = plant.bank_clauses(_qualified_host(text), "competitive")
    # PPS-D-016231: the validity note is not the requirement.
    assert [(c["target_item"], c["clause"][:2]) for c in clauses] == [("v12", "가.")]
    assert plant.bank_clauses(_qualified_host(text), "general") == []
    # PPS-D-006315: a host that orders leaflets makes the printed-matter requirement lawful.
    assert not plant.clause_product_is_unrelated(clauses[0]["clause"], "홍보 리플렛 인쇄물 제작 1식")
    assert plant.clause_product_is_unrelated(clauses[0]["clause"], "학술연구용역 수행")
    assert not plant.clause_product_is_unrelated("직접생산확인증명서를 소지한 업체", "학술연구용역 수행")


@pytest.mark.parametrize("text, codes, decision, expected", [
    ("사무용 의자 구매", [], "unknown", "general"),
    ("직접생산확인증명서를 소지한 업체", [{"catalog_registered": True}], "applicable", "competitive"),
    # PPS-D-020413: a software development task sits in a designated service field.
    ("업무 지원 소프트웨어 개발 용역", [], "unknown", None),
    # PPS-D-019310: the declared code is registered but its special condition is undecided.
    ("부식류(간장 등) 구매", [{"catalog_registered": True}], "unknown", None),
    ("제경비 및 일반관리비 포함 연구용역", [], "unknown", "general"),
    ("중소기업자간 경쟁제품이 아닌 물품", [], "unknown", None),
])
def test_product_class_is_decided_only_where_text_and_catalog_agree(monkeypatch, text, codes, decision, expected):
    from tools.independent_gold import catalog_facts

    class Catalog:
        rows = ({"세부품명": "제수밸브"},)

    monkeypatch.setattr(catalog_facts, "resolve_record",
                        lambda record, catalog: {"summary": {"decision": decision}, "codes": codes})
    assert plant.product_class(_host(text), Catalog()) == expected
    # A designated product named without its code also leaves the class open (PPS-D-008995).
    assert plant.product_class(_host("상수도 제수밸브 교체"), Catalog()) is None
    # 홈페이지 in the body of a furniture notice says nothing about what is being bought.
    body = "\n".join(["사무용 의자 구매"] + [f"{number}. 일반 안내 문장" for number in range(1, 15)] + ["세부사항은 홈페이지 참조"])
    if decision == "unknown" and not codes:
        assert plant.product_class(_host(body), Catalog()) == "general"


def test_bids_run_for_a_private_subsidy_recipient_take_no_size_edit(monkeypatch):
    monkeypatch.setattr(plant, "product_class", lambda record, catalog: "general")
    assert "v18" in plant.feasible_pool_edits(_size_host(), catalog=None)
    subsidy = _size_host(SIZE_TEXT + "\n6. 본 입찰은 보조사업자를 대신하여 집행합니다.")
    assert "v18" not in plant.feasible_pool_edits(subsidy, catalog=None)


def test_strict_holdout_keeps_only_wording_that_dev_never_shows():
    def row(split, provenance):
        return {"split": split, "provenance": provenance}

    boilerplate = ["다. 소기업 또는 소상공인 확인서를 소지한 업체"]
    rows = [
        row("dev", {"removed_lines": boilerplate}),
        # Same wording under another marker, spacing and number: seen by dev.
        row("holdout", {"removed_lines": ["라.  소기업 또는 소상공인  확인서를 소지한 업체"]}),
        row("holdout", {"changed_lines": [{"from": "가. 처음 보는 표현의 소기업 제한 조항", "to": "x"}]}),
        row("holdout", {"planted_body": "최근 3년 이내 실적이 있는 업체"}),
        # Price and meta edits carry no clause wording of their own.
        row("holdout", {"to_price": 1}),
    ]
    plant.separate_seen_wording(rows)
    assert [r["split"] for r in rows] == ["dev", "holdout_seen_wording", "holdout", "holdout", "holdout"]
    assert rows[0]["wording_sha256"] == rows[1]["wording_sha256"] and rows[4]["wording_sha256"] is None


COMPETITIVE = "\n".join([
    "1. 건명: 통학버스 운행 용역",
    "3. 입찰참가자격",
    "가. 시행령 제13조의 자격을 갖춘 자",
    "나. 직접생산확인증명서[세부품명: 통학운송서비스(7811189902)]를 소지한 업체이어야 합니다.",
    "※ 직접생산확인증명서는 제출마감일 전일까지 발급된 것으로 유효기간 내에 있어야 합니다.",
    "다. 부정당업자가 아닌 자",
    "4. 기타",
])


def test_direct_production_deletion_needs_the_product_to_stay_identified(monkeypatch):
    from tools.independent_gold import catalog_facts

    def resolve(record, catalog):
        named = "7811189902" in str(record["meta"].get("세부품명번호목록")) or "7811189902" in record["docs"][0]["text"]
        return {"summary": {"decision": "applicable" if named else "unknown"}, "codes": []}

    monkeypatch.setattr(catalog_facts, "resolve_record", resolve)
    # PPS-D-style notices often name the code only inside the clause: deleting it erases the product class.
    with pytest.raises(plant.PlantError, match="only inside the deleted clause"):
        plant.delete_direct_production(_host(COMPETITIVE), None)
    # PPS-D-010966: the extraction put the clause's head elsewhere; its tail starts mid-token.
    split = _host(COMPETITIVE.replace(
        "나. 직접생산확인증명서[세부품명: 통학운송서비스(7811189902)]를 소지한 업체이어야 합니다.",
        "나. 입찰보증금 귀속 안내\n7811189902)로 등록한 업체로서 직접생산확인증명서를 소지한 업체"))
    split["meta"]["세부품명번호목록"] = "통학운송서비스[7811189902]"
    with pytest.raises(plant.PlantError, match="split by the text extraction"):
        plant.delete_direct_production(split, None)
    registered = _host(COMPETITIVE)
    registered["meta"]["세부품명번호목록"] = "통학운송서비스[7811189902]"
    edited, provenance = plant.delete_direct_production(registered, None)
    assert edited["docs"][0]["text"].split("\n")[2:5] == ["가. 시행령 제13조의 자격을 갖춘 자", "나. 부정당업자가 아닌 자", "4. 기타"]
    assert len(provenance["removed_lines"]) == 2 and "직접생산" not in edited["docs"][0]["text"]


def test_large_enterprise_limit_is_deleted_only_when_nothing_else_mentions_it():
    text = "\n".join([
        "1. 건명: 업무포털 정보시스템 유지관리 용역",
        "3. 입찰참가자격",
        "가. 소프트웨어사업자(1468)로 등록한 업체",
        "나. 본 사업은 「중소 소프트웨어사업자의 사업 참여 지원에 관한 지침」에 따라 대기업 및 중견기업은 입찰에 참여할 수 없습니다.",
        "다. 부정당업자가 아닌 자",
        "4. 기타",
    ])
    # PPS-D-019131 / PPS-D-012478: 상호출자제한 in a telecom-construction or affiliate clause is not the SW rule.
    telecom = text.replace("「중소 소프트웨어사업자의 사업 참여 지원에 관한 지침」에 따라", "「정보통신공사업법」에 따라 상호출자제한기업집단 등")
    telecom = telecom.replace("업무포털 정보시스템 유지관리 용역", "재해예방계측 저수위계 물품구매").replace("소프트웨어사업자(1468)", "정보통신공사업")
    with pytest.raises(plant.PlantError, match="does not show software work"):
        plant.delete_large_enterprise_limit(_host(telecom))
    affiliate = text.replace("나. 본 사업은 「중소 소프트웨어사업자의 사업 참여 지원에 관한 지침」에 따라 대기업 및 중견기업은 입찰에 참여할 수 없습니다.",
                             "나. 공동수급체 구성원은 상호출자제한기업집단 소속 계열회사가 아니어야 합니다.")
    with pytest.raises(plant.PlantError, match="no large-enterprise"):
        plant.delete_large_enterprise_limit(_host(affiliate))
    # PPS-D-015223: once the declaration is gone, a siren purchase shows no software work.
    sirens = text.replace("업무포털 정보시스템 유지관리 용역", "재난 예·경보시설 제조구매 설치").replace("소프트웨어사업자(1468)", "동보장치 제조업")
    with pytest.raises(plant.PlantError, match="does not show software work"):
        plant.delete_large_enterprise_limit(_host(sirens))
    edited, _ = plant.delete_large_enterprise_limit(_host(text))
    assert "대기업" not in edited["docs"][0]["text"] and "나. 부정당업자가 아닌 자" in edited["docs"][0]["text"]
    elsewhere = _host(text)
    elsewhere["docs"][1]["text"] = "과업: 대기업 참여제한 대상 사업"
    with pytest.raises(plant.PlantError, match="survives"):
        plant.delete_large_enterprise_limit(elsewhere)
    with pytest.raises(plant.PlantError, match="no large-enterprise"):
        plant.delete_large_enterprise_limit(_host())
    # PPS-D-013854: the only mention was a form field.
    with pytest.raises(plant.PlantError, match="no large-enterprise"):
        plant.delete_large_enterprise_limit(_host(HOST_TEXT + "\n5. 업체현황\n가. 대기업( ) 중소기업( ) | 년( )"))


def test_briefing_is_inserted_too_soon_after_posting_for_local_negotiated_bids_only():
    host = _host()
    host["meta"].update({"적용계약법": plant.LOCAL_LAW, "낙찰방법": plant.NEGOTIATED_AWARD,
                         "공고게시일자": "20260302", "개찰예정일자": "20260325"})
    edited, provenance = plant.insert_briefing(host)
    import datetime
    posted = datetime.date(2026, 3, 2)
    briefing = datetime.date.fromisoformat(provenance["briefing"])
    # Rule (A): the notice must be posted 7 days before the briefing; here it is 2-4 days, on a weekday.
    assert 2 <= (briefing - posted).days <= 4 and briefing.weekday() < 5
    assert f"{briefing.year}. {briefing.month}. {briefing.day}.(" in provenance["planted_line"]
    assert "설명회" in edited["docs"][0]["text"] or "설명" in edited["docs"][0]["text"]
    national = _host()
    national["meta"].update({"적용계약법": "국가계약법", "낙찰방법": plant.NEGOTIATED_AWARD,
                             "공고게시일자": "20260302", "개찰예정일자": "20260325"})
    with pytest.raises(plant.PlantError, match="local negotiated"):
        plant.insert_briefing(national)
    with pytest.raises(plant.PlantError, match="already states a briefing"):
        plant.insert_briefing({**host, "docs": [{"type": "공고문", "text": HOST_TEXT + "\n사업설명회 개최"}]})
    tight = _host()
    tight["meta"].update({"적용계약법": plant.LOCAL_LAW, "낙찰방법": plant.NEGOTIATED_AWARD,
                          "공고게시일자": "20260302", "개찰예정일자": "20260305"})
    with pytest.raises(plant.PlantError, match="fits a briefing"):
        plant.insert_briefing(tight)


def test_title_band_tag_is_lowered_below_the_price():
    text = "시험 용역(제한경쟁·1억원미만) 입찰공고\n" + HOST_TEXT + "\n건명: 시험 용역(제한경쟁·1억원미만)"
    host = _host(text)
    host["meta"]["입찰추정가격"] = 72_000_000
    edited, provenance = plant.retag_title_band(host)
    assert edited["docs"][0]["text"].count("(제한경쟁·5천만원미만)") == 2
    assert "1억원미만" not in edited["docs"][0]["text"] and edited["meta"] == host["meta"]
    cheap = _host(text)
    cheap["meta"]["입찰추정가격"] = 15_000_000
    with pytest.raises(plant.PlantError, match="below every lower band"):
        plant.retag_title_band(cheap)
    already = _host(text)
    already["meta"]["입찰추정가격"] = 170_000_000  # the DEV-055 state: the tag is wrong already
    with pytest.raises(plant.PlantError, match="already disagrees"):
        plant.retag_title_band(already)
    with pytest.raises(plant.PlantError, match="exactly one"):
        plant.retag_title_band(_host())


def test_meta_license_swap_needs_the_notice_to_state_the_registered_code():
    text = HOST_TEXT.replace("가. 시행령 제13조의 자격을 갖춘 자", "가. 학술연구용역(업종코드: 1169)으로 등록한 자")
    host = _host(text)
    host["meta"]["면허업종제한목록"] = "[학술.연구용역(1169)]"
    edited, provenance = plant.swap_meta_license(host, "[식품판매업(집단급식소식품판매업)(5246)]")
    assert edited["meta"]["면허업종제한목록"].endswith("(5246)]") and edited["meta"]["업종제한여부"] == "Y"
    assert edited["docs"] == host["docs"] and provenance["document_code"] == "1169"
    with pytest.raises(plant.PlantError, match="single registered industry"):
        plant.swap_meta_license(host, "[학술.연구용역(1169)]")
    unstated = _host()
    unstated["meta"]["면허업종제한목록"] = "[학술.연구용역(1169)]"
    with pytest.raises(plant.PlantError, match="single registered industry"):
        plant.swap_meta_license(unstated, "[식품판매업(5246)]")
    with pytest.raises(plant.PlantError, match="registers no licensed industry"):
        plant.swap_meta_license(_host(), "[식품판매업(5246)]")


def test_institution_templates_respect_the_host_kind_and_are_marked_as_templates():
    goods = _host()
    goods["meta"]["업무구분"] = "물품(내자)"
    _, provenance = plant.insert_institution_limit(goods)
    assert provenance["wording"] == "template" and not provenance["operation"].endswith(":institution")
    service = _host()
    service["meta"]["업무구분"] = "일반용역"
    kinds = set()
    for number in range(40):
        service["id"] = f"HOST-{number}"
        kinds.add(plant.insert_institution_limit(service)[1]["operation"].split(":")[1])
    assert kinds == {"institution", "facility", "headcount"}
    # Template wording is not natural wording: it never decides a holdout split.
    assert plant.wording_key({"provenance": provenance}) is None


def test_model_lines_come_from_prescriptive_specs_and_land_under_a_spec_heading():
    source = _host()
    source["docs"][1] = {"type": "규격서", "text": "1. 규격\n모델명: Akron TurboJet 1720\n제조사: 참고용 ABC-100 또는 동급"}
    assert plant.spec_model_lines(source) == []  # the document offers an equivalent somewhere
    source["docs"][1]["text"] = "1. 규격\n모델명: Akron TurboJet 1720\n기존 보유 모델명: XYZ-200"
    assert plant.spec_model_lines(source) == ["모델명: Akron TurboJet 1720"]
    host = _host()
    host["docs"][1] = {"type": "규격서", "text": "물품 규격서\n1. 일반사항\n가. 수량 1식"}
    edited, provenance = plant.plant_spec_line(host, "모델명: Akron TurboJet 1720")
    assert edited["docs"][1]["text"].split("\n")[:2] == ["물품 규격서", "- 모델명: Akron TurboJet 1720"]
    assert edited["docs"][0] == host["docs"][0] and provenance["doc_index"] == 1
    host["docs"][1]["text"] += "\n동등 이상 제품 가능"
    with pytest.raises(plant.PlantError, match="already discusses"):
        plant.plant_spec_line(host, "모델명: Akron TurboJet 1720")


def test_bank_and_hosts_for_the_added_items():
    text = "\n".join([
        "3. 입찰참가자격",
        "가. 주된 영업소의 소재지가 전라남도에 있는 업체",
        "나. 중소기업자로서 중소기업확인서를 소지한 업체",
        "다. 소기업 또는 소상공인 확인서를 소지한 업체",
        "라. 현장설명회에 참석한 자에 한하여 입찰에 참가할 수 있습니다.",
        "마. 제안서 설명회에 참가하지 않은 경우 평가에서 제외합니다.",
        "4. 기타",
    ])
    found = {(c["target_item"], c["clause"][:2]) for c in plant.bank_clauses(_qualified_host(text), "general")}
    # The proposal presentation (마) is not a briefing; each other item feeds exactly one target.
    assert found == {("v5", "가."), ("v14", "나."), ("v15", "다."), ("v22", "라.")}
    plain = "3. 입찰참가자격\n가. 주된 영업소의 소재지가 경기도에 있는 업체\n나. 부정당업자가 아닌 자\n4. 기타"
    targets = plant.transplant_host_targets(_qualified_host(plain, price=60_000_000), "general")
    assert targets.get("v8") is True and "v7" not in targets and "v5" not in targets
    big = _host()
    big["meta"].update({"입찰추정가격": 700_000_000, "배정예산금액": 770_000_000, "낙찰방법": plant.NEGOTIATED_AWARD,
                        "업무구분": "물품(내자)", "세부품명번호목록": "소화기[4619160101]"})
    big["docs"][1] = {"type": "규격서", "text": "규격서\n1. 수량 1식"}
    targets = plant.transplant_host_targets(big, "general")
    assert {"v5", "v14", "v22", "v9", "v19"} <= set(targets) and "v15" not in targets and "v2" not in targets
    # A model line is planted only into a specification of the same product class (PPS-D-008016:
    # a tablet model had landed in a book-purchase task order).
    assert targets["v9"] == "461916"
    sources = [{"target_item": "v9", "source_id": "SRC", "clause": "모델명: AB-100", "threshold_won": None,
                "product_prefix": "432115"}]
    assert plant.pair_transplants(sources, {"BIG": targets}, set(), {}, 10) == {}
    band = _host()
    band["meta"].update({"입찰추정가격": 150_000_000, "배정예산금액": 165_000_000})
    assert "v15" in plant.transplant_host_targets(band, "general")
    assert "v15" not in plant.transplant_host_targets(band, None)


def test_attachments_are_dropped_silently_and_only_when_the_edit_is_in_the_notice():
    rows = []
    for number in range(60):
        rows.append({"plant_id": f"p{number}", "record": _host(), "provenance": {"doc_index": 0}})
        plant.drop_attachments(rows[-1])
    dropped = [row for row in rows if "attachments_removed" in row["provenance"]]
    assert 10 < len(dropped) < 50
    assert all([d["type"] for d in row["record"]["docs"]] == ["공고문"] for row in dropped)
    assert all("dropped_doc_counts" not in row["record"] for row in dropped)
    spec_edit = {"plant_id": "p0", "record": _host(), "provenance": {"doc_index": 1}}
    for number in range(60):
        spec_edit["plant_id"] = f"q{number}"
        plant.drop_attachments(spec_edit)
    assert len(spec_edit["record"]["docs"]) == 2


def test_second_violation_comes_from_another_family_and_widens_the_exclusions():
    added = []
    for number in range(80):
        host = _qualified_host()
        host["id"] = f"HOST-{number}"
        row = {"plant_id": f"p{number}", "target_item": "v18", "record": host, "extra_targets": [],
               "excluded_items": ["v18", "v24"], "expected_zero_items": []}
        plant.add_second_edit(row)
        if row["extra_targets"]:
            added.append(row)
    assert 5 < len(added) < 40
    for row in added:
        extra = row["extra_targets"][0]["target_item"]
        assert extra in {"v21", "v19", "v6", "v1"} and extra != "v24"
        assert extra in row["excluded_items"] and extra not in row["expected_zero_items"]


def test_holdout_fraction_is_honoured_by_hash():
    names = [f"family-{number}" for number in range(2000)]
    share = sum(plant._split_of(name) == "holdout" for name in names) / len(names)
    assert abs(share - plant.HOLDOUT_FRACTION) < 0.04


def test_real_dev_build_is_deterministic_and_targets_only_official_zero_hosts():
    kwargs = dict(hosts_per_source=1, hosts_per_single_source_item=1, controls=3, max_plants_per_host=4)
    first = plant.build_plants(**kwargs)
    second = plant.build_plants(**kwargs)
    assert first["manifest"]["rows_sha256"] == second["manifest"]["rows_sha256"]
    records, labels = plant.load_dev()
    for row in first["rows"]:
        assert all(labels[row["host_id"]][item] == "0" for item in plant.ITEMS)
        assert row["record"]["id"] == row["host_id"]
        if row["kind"] == "planted":
            assert row["host_id"] != row["source_id"]
            assert row["target_item"] in row["excluded_items"] and "v24" in row["excluded_items"]
            assert plant.host_is_eligible(row["target_item"], row["source_id"], records[row["host_id"]])
            text = row["record"]["docs"][row["provenance"]["doc_index"]]["text"]
            assert row["provenance"]["planted_body"] in text
        else:
            assert row["record"] == records[row["host_id"]]
