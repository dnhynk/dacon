"""Deterministic item-1 relations and the nearby non-violation boundaries."""
from submission.pps.eligibility_restrictions import direct_facility_ownership_check
from submission.pps.rules import apply_rules


def notice(text, *, method='제한경쟁', work='일반용역'):
    return {
        'id': 'synthetic-eligibility-restriction',
        'meta': {'업무구분': work, '계약방법': method},
        'docs': [{'doc_id': 'D0', 'type': '공고문', 'text': text}],
        'input_completeness': {'완전관측': True},
        'dropped_doc_counts': {},
    }


def test_competitive_facility_rental_cannot_require_preexisting_direct_ownership():
    quote = '경기도 또는 제주도에 소재한 교육(연수) 시설을 보유하고 있는 자'
    rec = notice('건명: 교직원 연수시설 임차 용역\n'
                 '3. 입찰참가자격\n3) ' + quote + '\n4. 제출서류')
    decision = direct_facility_ownership_check(rec)
    assert decision['value'] == 1 and decision['evidence'] == '3) ' + quote
    result, trace = apply_rules(rec, {'v1': 0, 'e1': ''}, items=(1,))
    assert result == {'v1': 1, 'e1': '3) ' + quote}
    assert trace == [decision]


def test_lease_or_access_alternative_is_not_direct_ownership_restriction():
    rec = notice('용역명: 연수시설 임차 용역\n3. 입찰참가자격\n'
                 '가. 교육시설을 소유 또는 임차하여 사용할 수 있는 업체\n4. 계약조건')
    assert direct_facility_ownership_check(rec) is None
    assert apply_rules(rec, {'v1': 0, 'e1': ''}, items=(1,))[0]['v1'] == 0


def test_registered_youth_facility_route_is_not_recast_as_generic_ownership():
    rec = notice('용역명: 숙박형 현장체험학습 위탁 용역\n3. 입찰참가자격\n'
                 '가. 청소년활동진흥법에 따라 등록된 청소년수련시설로서 인증 프로그램을 운영하는 업체')
    assert direct_facility_ownership_check(rec) is None


def test_scoring_or_form_facility_inventory_is_not_an_eligibility_gate():
    for body in (
        '3. 제안서 평가기준\n교육(연수)시설을 보유한 업체 10점',
        '【붙임3】 시설현황서\n교육(연수)시설을 보유한 업체는 수량을 기재',
    ):
        rec = notice('용역명: 연수시설 임차 용역\n2. 입찰참가자격\n가. 일반 사업자\n' + body)
        assert direct_facility_ownership_check(rec) is None


def test_withdrawn_direct_ownership_clause_cannot_force_positive():
    rec = notice('용역명: 연수시설 임차 용역\n3. 입찰참가자격\n'
                 '가. 교육시설을 보유한 업체라는 조건은 철회한다.\n4. 계약조건')
    assert direct_facility_ownership_check(rec) is None


def test_rule_abstains_for_quote_procedure_and_goods_contracts():
    text = ('용역명: 연수시설 임차 용역\n3. 입찰참가자격\n'
            '가. 교육시설을 보유한 업체\n4. 계약조건')
    assert direct_facility_ownership_check(notice(text, method='수의계약')) is None
    assert direct_facility_ownership_check(notice(text, work='물품(내자)')) is None
