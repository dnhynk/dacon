from submission.pps.performance import performance_facts, won
import pytest


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


def test_specific_school_student_experience_is_a_specific_prior_class():
    r = notice('가. 최근 3년 이내 중고등학교 학생 대상 숙박형 국내여행 '
               '1억원 이상의 실적이 있는 업체이어야 한다.')
    facts = performance_facts(r)
    assert facts['overlays']['v4']['value'] == 1
    assert facts['candidates'][0]['purchaser'] == 'specific_purchaser_required'


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


def test_notice_amount_priority_does_not_resolve_unrelated_quote_exception():
    r=notice("가. 최근 3년 단일 용역 수행 실적 2억원 이상이 있는 업체\n나. 본점 소재지를 제주도에 둔 업체")
    r["docs"][0]["text"]="소액수의 견적제출 안내공고\n추정가격: 300,000,000원\n"+r["docs"][0]["text"]
    decisions=performance_facts(r)["overlays"]
    assert decisions['v2']['value'] == 0
    assert all(decisions[k]["value"] is None for k in ("v3","v8"))


@pytest.mark.parametrize('text,value', [
    ('2천3백만원', 23_000_000), ('1억2천3백만원', 123_000_000),
    ('12억3천4백만5천원', 1_234_005_000), ('2천3백4십5만원', 23_450_000),
    ('0.5억원', 50_000_000), ('120,000천원', 120_000_000),
])
def test_experience_amounts_share_exact_korean_place_unit_arithmetic(text, value):
    from submission.pps.comparison import won_value
    assert won(text) == won_value(text) == value


def test_composed_units_reach_actual_positive_consumer_without_decimal_loss():
    from submission.pps.rules import apply_rules
    rec = notice('가. 단일 용역 수행 실적 2천3백만원 이상인 업체이어야 한다.')
    rec['meta'].update(입찰추정가격=10_000_000, 배정예산금액=11_000_000)
    facts = performance_facts(rec)
    assert facts['candidates'][0]['required_money']['won'] == 23_000_000
    result, trace = apply_rules(rec, {'v3': 0, 'e3': ''}, items=(3,))
    assert result['v3'] == 1
    assert result['e3'] in rec['docs'][0]['text']


def test_invalid_unit_order_is_recorded_without_a_spurious_amount_or_crash():
    rec = notice('가. 단일 용역 수행 실적 2억1조원 이상인 업체이어야 한다.')
    facts = performance_facts(rec)
    candidate = facts['candidates'][0]
    assert candidate['required_money'] is None
    assert candidate['money'][0]['parse_status'] == 'unresolved_unit_expression'
    assert facts['overlays']['v3']['value'] is None


def test_qualitative_experience_gate_still_combines_with_bidder_region_for_v8():
    r = notice('다. 경북에 소재한 실적이 우수한 업체이어야 한다.')
    facts = performance_facts(r)
    assert facts['candidates'][0]['status'] == 'qualitative_mandatory'
    assert facts['overlays']['v2']['value'] is None
    assert facts['overlays']['v3']['value'] is None
    assert facts['overlays']['v8']['value'] == 1
    assert len(facts['overlays']['v8']['evidence']) == 2


def test_verified_local_small_quote_allows_region_and_experience_combination():
    from submission.pps.rules import apply_rules
    r = notice('가. 최근 3년 용역 수행 실적 2억원 이상인 업체이어야 한다.\n'
               '나. 본점 소재지를 강원특별자치도에 둔 업체이어야 한다.')
    r['meta'].update(계약방법='수의계약', 입찰추정가격=83_000_000,
                     배정예산금액=91_000_000)
    r['docs'][0]['text'] = '소액수의 견적서 제출 안내 공고\n' + r['docs'][0]['text']
    row, trace = apply_rules(r, {'v8': 1, 'e8': 'model'}, items=(8,))
    assert row == {'v8': 0, 'e8': ''}
    decision = next(x for x in trace if x['item'] == 8 and x['value'] == 0)
    assert decision['reason'] == 'local_actual_small_quote_allows_region_and_experience_combination'


def test_competitive_route_keeps_region_and_experience_violation():
    from submission.pps.rules import apply_rules
    r = notice('가. 최근 3년 용역 수행 실적 2억원 이상인 업체이어야 한다.\n'
               '나. 본점 소재지를 강원특별자치도에 둔 업체이어야 한다.')
    r['meta'].update(계약방법='제한경쟁')
    row, _ = apply_rules(r, {'v8': 0, 'e8': ''}, items=(8,))
    assert row['v8'] == 1
