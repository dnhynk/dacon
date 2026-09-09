from pps.performance import performance_facts, won


def notice(clause, heading="2. 입찰 참가자격"):
    return {"meta":{"적용계약법":"지방계약법","업무구분":"일반용역",
                    "입찰추정가격":100_000_000,"배정예산금액":110_000_000},
            "docs":[{"doc_id":"a","type":"공고문","text":heading+"\n"+clause}]}


def test_prior_purchaser_location_is_not_current_bidder_region():
    r = notice("가. 경기도 소재 공공기관에서 발주한 단일 용역 수행 실적 2억원 이상이 있는 업체")
    facts=performance_facts(r)
    assert facts["overlays"]["v2"]["value"]==1
    assert facts["overlays"]["v3"]["value"]==1
    assert facts["overlays"]["v8"]["value"] is None
    r["docs"][0]["text"] += "\n나. 법인등기부상 본점 소재지를 경기도에 둔 업체"
    assert performance_facts(r)["overlays"]["v8"]["value"]==1


def test_experience_scores_forms_or_permissions_are_not_qualification():
    for heading, text in [("3. 정량평가 기준", "최근 3년 실적 2억원 이상인 업체 배점 10점"),
                          ("4. 제출서류", "실적증명서 서식 1부: 단일 실적 2억원 이상 업체"),
                          ("2. 입찰 참가자격", "실적이 없어도 참가할 수 있는 업체")]:
        assert all(x["value"] is None for x in performance_facts(notice(text,heading))["overlays"].values())


def test_exact_won_units_and_equality_abstention():
    assert won("1억5천만원")==150_000_000
    assert won("0.5억원")==50_000_000
    assert won("120,000천원")==120_000_000
    r=notice("가. 단일 용역 수행 실적 1억1천만원 이상이 있는 업체")
    assert performance_facts(r)["overlays"]["v3"]["value"] is None


def test_actual_quote_scope_and_conflicting_prices_abstain():
    r=notice("가. 최근 3년 단일 용역 수행 실적 2억원 이상이 있는 업체\n나. 본점 소재지를 제주도에 둔 업체")
    r["docs"][0]["text"]="소액수의 견적제출 안내공고\n추정가격: 300,000,000원\n"+r["docs"][0]["text"]
    decisions=performance_facts(r)["overlays"]
    assert all(decisions[k]["value"] is None for k in ("v2","v3","v8"))
