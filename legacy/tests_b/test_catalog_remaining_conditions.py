"""Whole-note boundaries, scope and exceptions in the supplied catalog.

These are contract/counterexample tests, not human gold or a score benchmark.
The source parser and designation consumer both run; no predicate fact doubles.
"""
import copy
import csv

import pytest

from submission.pps.catalog_condition_facts import parse_value, _LABELS
from submission.pps.catalog_condition_review import condition_plan
from submission.pps.catalog_predicates import compile_note
from submission.pps.qualification import catalog_condition
from tests.test_catalog_predicate_consumption import evaluate_note, purchase, product
from tests.test_service_identity import DATA


PUMP = ('1. 상수도용은 토출구경 500mm 미만에 한함 '
        '2. 발전용 및 액화 천연가스 (LNG)용 제품은 제외')
MICRO = '픽셀간격 1mm 이하 마이크로 LED 제외'
WINDOW = '1. 알루미늄제에 한함 2. 20층 이상 건물에 시공하는 커튼월은 제외'
QUANTITY = '연간 구매예정수량 20% 이내에서 예외'
GEOLOGY = '1. 1천만원 이상의 지질 관련 용역에 한함 2. 노선사업은 1천만원 이상의 실시 설계에 한함'
WATER = ('1. 수처리설비에 한함 '
         '2. 신설 설비는 일일처리용량 하폐수는 10만톤 이하, 상수는 30만톤 이하에 한함 '
         '3. 통합감시 제어설비 포함')


def status(note, fields):
    return evaluate_note(note, fields)[0]['status']


@pytest.mark.parametrize('water,diameter,expected', [
    ('아니오', None, 'met'), ('아니오', '2m', 'met'),
    ('예', '49.999cm', 'met'), ('예', '500mm', 'not_met'),
    ('예', '0.5m', 'not_met'), ('예', '500mm 미만', 'met'),
    ('예', '500mm 이하', 'unknown'), (None, '100mm', 'met'),
    (None, '500mm', 'unknown'), ('예', None, 'unknown'),
    ('예', '500kg', 'unknown'), ('예', '5 00mm', 'unknown'),
])
def test_pump_diameter_is_conditional_on_water_supply(water, diameter, expected):
    facts = [('발전용', '아니오'), ('LNG용', '아니오')]
    facts += [('상수도용', water)] if water is not None else []
    facts += [('토출구경', diameter)] if diameter is not None else []
    assert status(PUMP, facts) == expected


def test_a_pump_exclusion_is_not_overruled_by_a_successful_diameter():
    assert status(PUMP, [('토출구경', '1mm'), ('발전용', '예')]) == 'not_met'
    assert status(PUMP, [('토출구경', '1mm')]) == 'unknown'


@pytest.mark.parametrize('led,pitch,expected', [
    ('예', '1mm', 'not_met'), ('예', '1.001mm', 'met'),
    ('예', '0.1cm', 'not_met'), ('아니오', None, 'met'),
    (None, '2mm', 'met'), (None, '1mm', 'unknown'),
])
def test_micro_led_exclusion_requires_both_properties(led, pitch, expected):
    facts = [('마이크로 LED', led)] if led is not None else []
    facts += [('픽셀간격', pitch)] if pitch is not None else []
    assert status(MICRO, facts) == expected


@pytest.mark.parametrize('fields,expected', [
    ([('재질', '알루미늄'), ('커튼월', '아니오')], 'met'),
    ([('재질', '알루미늄'), ('커튼월', '예'), ('건물 층수', '19층')], 'met'),
    ([('재질', '알루미늄'), ('커튼월', '예'), ('건물 층수', '20층')], 'not_met'),
    ([('재질', '알루미늄'), ('건물 층수', '20층')], 'unknown'),
    ([('재질', '알루미늄'), ('커튼월', '예'), ('건물 층수', '19.5층')], 'unknown'),
    ([('재질', '강철')], 'not_met'),
    ([('재질', '금속'), ('커튼월', '아니오')], 'unknown'),
    ([('재질', '경금속'), ('커튼월', '아니오')], 'unknown'),
])
def test_window_material_and_height_do_not_replace_each_other(fields, expected):
    assert status(WINDOW, fields) == expected


@pytest.mark.parametrize('fields,expected', [
    ([('수처리용', '아니오')], 'not_met'),
    ([('수처리용', '예'), ('신설 설비', '아니오')], 'met'),
    ([('수처리용', '예'), ('신설 설비', '예'), ('수처리 종류', '하폐수'), ('일일처리용량', '100000톤/일')], 'met'),
    ([('수처리용', '예'), ('신설 설비', '예'), ('수처리 종류', '하폐수'), ('일일처리용량', '100001톤/일')], 'not_met'),
    ([('수처리용', '예'), ('신설 설비', '예'), ('수처리 종류', '상수'), ('일일처리용량', '300000톤/일')], 'met'),
    ([('수처리용', '예'), ('신설 설비', '예'), ('일일처리용량', '300000톤/일')], 'unknown'),
    ([('수처리용', '예'), ('일일처리용량', '100000톤/일')], 'met'),
    ([('수처리용', '예'), ('신설 설비', '예'), ('수처리 종류', '하폐수'), ('일일처리용량', '100000톤')], 'unknown'),
])
def test_water_facility_threshold_keeps_stage_category_and_daily_unit(fields, expected):
    assert status(WATER, fields) == expected


@pytest.mark.parametrize('fields,expected', [
    ([('일반가정용', '예')], 'not_met'),
    ([('일반가정용', '아니오'), ('배전용', '아니오'), ('PCS 출력용량', '250kW')], 'met'),
    ([('일반가정용', '아니오'), ('배전용', '아니오'), ('PCS 출력용량', '250.001kW')], 'not_met'),
])
def test_household_use_is_a_property_not_a_hypothetical_marker(fields, expected):
    note = ('1. 전력변환장치(PCS) 출력용량 250kW 이하에 한함 '
            '2. 가정용(일반가정에서 사용) 및 배전용(한국전력 등 배전선로에 사용)은 제외')
    assert status(note, fields) == expected
    rec = purchase('시험품목', '1234567890', fields, extra='달리 가정하면 다른 규격을 적용한다.')
    assert catalog_condition(note, None, None, record=rec, product_name='시험품목')['status'] == 'unknown'


@pytest.mark.parametrize('apartment,wired,expected', [
    ('예', '예', 'not_met'), ('예', '아니오', 'met'),
    ('아니오', '예', 'met'), ('아니오', None, 'met'), ('예', None, 'unknown'),
])
def test_apartment_wired_exclusion_is_a_conjunction(apartment, wired, expected):
    facts = [('공동주택용', apartment)] + ([('유선방식', wired)] if wired is not None else [])
    assert status('공동주택 유선방식 제외', facts) == expected


@pytest.mark.parametrize('fields,expected', [
    ([('연간 구매예정수량 대비 예외 비율', '20%')], 'unknown'),
    ([('연간 구매예정수량 대비 예외 비율', '20%'), ('연간 구매예정수량 예외 적용', '예')], 'not_met'),
    ([('연간 구매예정수량 대비 예외 비율', '20%'), ('연간 구매예정수량 예외 적용', '아니오')], 'met'),
    ([('연간 구매예정수량 대비 예외 비율', '20.001%')], 'met'),
    ([('연간 예측량 대비 예외 비율', '20%'), ('연간 예측량 예외 적용', '예')], 'unknown'),
    ([('계약 구매액 대비 예외 비율', '10%'), ('연간 구매예정수량 예외 적용', '예')], 'unknown'),
])
def test_optional_annual_quantity_exception_needs_actual_application_and_denominator(fields, expected):
    assert status(QUANTITY, fields) == expected


def test_pipe_physical_exclusion_and_annual_exception_both_survive():
    note = '1. 공동주택용 200Φ 이하 제외 2. 연간 구매 예정 수량 20% 이내에서 예외'
    assert status(note, [('공동주택용', '예'), ('관 호칭구경', '200Φ')]) == 'not_met'
    assert status(note, [('공동주택용', '아니오')]) == 'unknown'
    assert status(note, [('공동주택용', '아니오'), ('연간 구매예정수량 예외 적용', '아니오')]) == 'met'
    assert status(note, [('공동주택용', '예'), ('관 호칭구경', '200mm'),
        ('연간 구매예정수량 예외 적용', '아니오')]) == 'unknown'


@pytest.mark.parametrize('value,expected', [('9.999억원', 'met'), ('10억원', 'not_met'),
    ('1,000,000,000원', 'not_met'), ('10억원 미만', 'met'), ('10억원 이하', 'unknown'),
    ('10억 5천만원', 'unknown')])
def test_public_tender_amount_uses_the_same_role_and_exact_boundary(value, expected):
    assert status('공공입찰 금액 10억원 미만에 한함', [('공공입찰 금액', value)]) == expected


def test_generic_estimate_and_meta_do_not_fill_special_note_amounts():
    rec = purchase('시험품목', '1234567890', [('입찰 추정가격', '1억원')])
    rec['meta']['입찰추정가격'] = 100_000_000
    before = copy.deepcopy(rec)
    assert catalog_condition('공공입찰 금액 10억원 미만에 한함', 100_000_000, 110_000_000,
        record=rec, product_name='시험품목')['status'] == 'unknown'
    assert catalog_condition('총액 100억원 이상 대규모 국방사업의 경우 제외', 20_000_000_000,
        22_000_000_000, record=rec, product_name='시험품목')['status'] == 'unknown'
    assert rec == before


@pytest.mark.parametrize('fields,expected', [
    ([('국방사업용', '아니오')], 'met'),
    ([('전체 국방사업 총액', '99.999억원')], 'met'),
    ([('국방사업용', '예'), ('전체 국방사업 총액', '100억원')], 'not_met'),
    ([('국방사업용', '예'), ('카메라 계약금액', '100억원')], 'unknown'),
])
def test_defense_total_is_the_whole_project_amount(fields, expected):
    assert status('총액 100억원 이상 대규모 국방사업의 경우 제외', fields) == expected


@pytest.mark.parametrize('fields,expected', [
    ([('지질 관련 용역 금액', '999만원')], 'not_met'),
    ([('지질 관련 용역 금액', '1000만원'), ('노선사업', '아니오')], 'met'),
    ([('지질 관련 용역 금액', '1000만원'), ('노선사업', '예'), ('실시설계', '예')], 'met'),
    ([('지질 관련 용역 금액', '1000만원'), ('노선사업', '예'), ('실시설계', '아니오')], 'not_met'),
    ([('지질 관련 용역 금액', '1000만원')], 'unknown'),
])
def test_geological_service_keeps_conditional_design_stage(fields, expected):
    assert status(GEOLOGY, fields) == expected


@pytest.mark.parametrize('note,fields,expected', [
    ('교육 및 실험용에 한함', [('교육용', '예')], 'met'),
    ('교육 및 실험용에 한함', [('교육용', '아니오'), ('실험용', '예')], 'met'),
    ('교육 및 실험용에 한함', [('교육용', '아니오')], 'unknown'),
    ('국방규격 및 경찰규격에 한함', [('경찰규격 적용', '예')], 'met'),
    ('국방규격에 한함', [('국방기관 발주', '예'), ('군사용', '예')], 'unknown'),
    ('수처리용 및 음식물 처리장용 제품에 한함', [('음식물 처리장용', '예')], 'met'),
    ('단독 발주되는 재난방지 시설에 한함', [('재난방지 시설용', '예')], 'unknown'),
    ('단독 발주되는 재난방지 시설에 한함', [('재난방지 시설용', '예'), ('단독 발주', '아니오')], 'not_met'),
    ('슬러지 저장용 탱크 및 사일로에 한함', [('저장 용기 유형', '사일로')], 'unknown'),
    ('슬러지 저장용 탱크 및 사일로에 한함', [('저장 용기 유형', '사일로'), ('슬러지 저장용', '예')], 'met'),
])
def test_enumerated_purposes_are_not_conflated_with_shared_qualifiers(note, fields, expected):
    assert status(note, fields) == expected


@pytest.mark.parametrize('value,expected', [('3형식', 'not_met'), ('4형식', 'met'),
    ('6형식', 'met'), ('7형식', 'not_met'), ('4.5형식', 'unknown'), ('4등급', 'unknown')])
def test_protective_clothing_type_is_not_a_continuous_quantity(value, expected):
    assert status('4~6형식에 한함', [('화학물질보호복 형식', value)]) == expected


def test_standard_identifier_and_type_are_two_numbers_not_a_joined_number():
    note = 'KS M 6080 5종(상온경화형 플라스틱 도료) 제외'
    assert status(note, [('도료 규격 형식', 'KS M 6080 5종')]) == 'not_met'
    assert status(note, [('도료 규격 형식', 'KS M 6080 4종')]) == 'met'
    assert status(note, [('도료 규격 형식', 'KS M 6 080 5종')]) == 'unknown'
    assert catalog_condition(note.replace('6080', '6 080'), None, None)['status'] == 'not_evaluated'


def test_generic_sausage_does_not_prove_the_excluded_subtype_absent():
    note = '소시지, 부대찌개용 소시지(비엔나 소시지는 제외)에 한함'
    assert status(note, [('식품 세부유형', '소시지')]) == 'unknown'
    assert status(note, [('식품 세부유형', '소시지'), ('비엔나 소시지', '아니오')]) == 'met'
    assert status(note, [('식품 세부유형', '부대찌개용 소시지'), ('비엔나 소시지', '예')]) == 'not_met'
    assert status(note, [('식품 세부유형', '비엔나 소시지')]) == 'not_met'


def test_precise_material_requirement_does_not_reject_a_broader_unknown_material():
    assert status('알루미늄제에 한함', [('재질', '금속')]) == 'unknown'
    assert status('알루미늄제에 한함', [('재질', '경금속')]) == 'unknown'
    assert status('금속 소재의 제품에 한함', [('재질', '알루미늄')]) == 'met'
    assert status('폴리에틸렌(PE) 소재의 제품에 한함', [('재질', '플라스틱')]) == 'unknown'
    assert status('금속 소재의 제품에 한함', [('재질', '목재')]) == 'not_met'


@pytest.mark.parametrize('subtype', ['소둔 결속선', '농산물 세척기', '생선묵 튀김제품'])
def test_existing_spaced_subtype_note_and_source_use_the_same_value_contract(subtype):
    assert status(subtype+'에 한함', [('제품 세부유형', subtype)]) == 'met'
    assert status(subtype+'에 한함', [('제품 세부유형', subtype.replace(' ', ''))]) == 'met'
    assert status(subtype+'에 한함', [('제품 세부유형', '양조간장')]) == 'not_met'


def test_new_conditions_use_the_real_catalog_and_whole_purchase_consumer():
    rec = purchase('금속제창', '3017169801', [('재질', '알루미늄'), ('커튼월', '예'), ('건물 층수', '20층')])
    assert product(rec)['status'] == 'general'
    rec = purchase('금속제창', '3017169801', [('재질', '알루미늄'), ('커튼월', '예'), ('건물 층수', '19층')])
    assert product(rec)['status'] == 'competition'
    for prefix in ['일부 납품', '기존', '예시: 전체 납품']:
        rec = purchase('금속제창', '3017169801', [('재질', '알루미늄'), ('커튼월', '예'), ('건물 층수', '20층')], prefix=prefix)
        assert product(rec)['status'] == 'unknown'


def test_new_property_offsets_and_other_document_permissions_remain_visible():
    rec = purchase('시험품목', '1234567890', [('상수도용', '예'), ('토출구경', '600mm'),
        ('발전용', '아니오'), ('LNG용', '아니오')])
    before = copy.deepcopy(rec)
    result = catalog_condition(PUMP, None, None, record=rec, product_name='시험품목')
    assert result['status'] == 'not_met'
    for observation in result['source_facts']['observations']:
        e = observation['evidence']
        assert rec['docs'][e['doc_index']]['text'][e['start']:e['end']] == e['text']
    assert rec == before
    rec['docs'].append({'doc_id': 'exception', 'type': '규격서', 'text': '동등 이상 타 규격도 납품 가능하다.'})
    result = catalog_condition(PUMP, None, None, record=rec, product_name='시험품목')
    assert result['status'] == 'unknown' and result['source_facts']['scope_issues']


def test_same_property_conflict_and_other_item_do_not_disappear():
    assert status(MICRO, [('마이크로 LED', '예'), ('마이크로 LED', '아니오'), ('픽셀간격', '1mm')]) == 'unknown'
    rec = purchase('시험품목', '1234567890', [('마이크로 LED', '예')])
    rec['docs'][0]['text'] += '\n전체 납품 다른품목의 픽셀간격: 1mm'
    assert catalog_condition(MICRO, None, None, record=rec, product_name='시험품목')['status'] == 'unknown'


def test_unfamiliar_extra_clause_is_not_dropped_after_expansion():
    for note in (PUMP, WINDOW, MICRO, GEOLOGY, QUANTITY):
        assert catalog_condition(note + ' 추가 조건을 충족해야 함', None, None)['status'] == 'not_evaluated'


def test_planned_fields_have_source_types_and_role_specific_definitions():
    note = '총액 100억원 이상 대규모 국방사업의 경우 제외'
    plan = condition_plan({'products': [{'code': '4617161002', 'name': '보안용카메라',
        'listed': True, 'note': note, 'condition': {'status': 'unknown'}}]})[0]
    amount = next(f for f in plan['fields'] if f['field'] == 'defense_project_total_won')
    assert amount['definition']['type'] == 'interval'
    assert '부분 계약' in amount['definition']['meaning']
    for field in plan['fields']:
        assert field['source_labels'] and field['field'] in _LABELS


def test_all_static_compiled_notes_have_a_complete_source_field_contract():
    path = DATA/'법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv'
    with path.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        program = compile_note(row['특이사항'])
        if program:
            assert set(program['required_fields']) <= set(_LABELS)
            assert program['whole_note_consumed']
            if program['required_fields']:
                assert catalog_condition(row['특이사항'], None, None)['status'] == 'unknown'


@pytest.mark.parametrize('fields,expected', [
    ([('필름 유형', 'PE')], 'met'),
    ([('필름 유형', 'PE'), ('농업용 필름', '아니오')], 'met'),
    ([('필름 유형', 'PO'), ('농업용 필름', '예')], 'met'),
    ([('필름 유형', 'PO'), ('농업용 필름', '아니오')], 'not_met'),
    ([('필름 유형', 'EVA'), ('농업용 필름', '예'), ('초산비닐 함량', '13%')], 'met'),
    ([('필름 유형', 'EVA'), ('농업용 필름', '예'), ('초산비닐 함량', '13.001%')], 'not_met'),
    ([('필름 유형', 'EVA'), ('농업용 필름', '예')], 'unknown'),
    ([('필름 유형', 'EVA'), ('농업용 필름', '예'), ('초산비닐 함량', '101%')], 'unknown'),
    ([('농업용 필름', '예')], 'unknown'),
])
def test_conditional_film_extension_does_not_restrict_the_base_category(fields, expected):
    note = '농업용 필름(PO필름, 초산비닐 함량 13% 이내의 EVA필름)포함'
    assert status(note, fields) == expected


def test_unknown_numeric_category_does_not_enter_an_unbounded_other_branch():
    facts = [('수처리용', '예'), ('신설 설비', '예'), ('수처리 종류', '기타'), ('일일처리용량', '1000000톤/일')]
    assert status(WATER, facts) == 'unknown'


SURVEY = ('1. 건설공사 설계 관련 공간정보관리법 시행령 제34조 제2항에 따른 금액(3천만원) 이상의 '
          '측량용역으로 ‘국토교통부 고시’(설계공모, 기본설계 등의 시행 및 설계의 경제성 등 검토에 관한 지침) '
          '별표2의 각 공종별 측량항목에 한정 '
          '2. 위의 측량 항목 중 도로, 철도(지하철 포함), 지중 송·배전 전력구, 하천, 광역 ·공업용 수도분야 '
          '노선사업은 실시설계에 한함')
AGGREGATE = ('순환골재 제품 제조용(콘크리트용, 아스팔트콘크리트용) 및 「건설폐기물법」 제27조에 따라 '
             '배출자가 건설공사 현장에서 건설폐기물처리시설을 직접 설치 운영하여 건설폐기물을 재활용 하고자 '
             '생산한 순환골재는 제외')
SECURITY = ('1. 경비업법상의 기계경비업, 특수경비업 제외 '
            '2. 공공기관이 자회사와 수의계약을 체결하는 경우 적용 대상에서 제외')
PRINTER = ('1. 다수공급자계약은 조달청의 점유율 관리 방안에 따라 20% 이내에서 예외적용 가능 '
           '2. 총액 계약 체결시 FDM 방식 제품은 구매액의 20% 이내에서 예외를 적용하며, 다른 방식 제품은 적용 제외')


def test_external_survey_item_membership_requires_an_explicit_scoped_source():
    facts = [('건설공사 설계 관련', '예'), ('건설공사 설계 관련 측량용역 금액', '3000만원'),
             ('노선사업', '아니오')]
    assert status(SURVEY, facts) == 'unknown'
    assert status(SURVEY, facts + [('설계공모 기본설계 지침 별표2 공종별 측량항목 해당', '예')]) == 'met'
    rec = purchase('시험품목', '1234567890', facts)
    rec['docs'][0]['text'] += '\n관계법령 참고: 공간정보관리법 시행령 제34조 제2항, 국토교통부 고시 별표2'
    assert catalog_condition(SURVEY, None, None, record=rec, product_name='시험품목')['status'] == 'unknown'
    assert status(SURVEY, [('건설공사 설계 관련 측량용역 금액', '2999만원')]) == 'not_met'


def test_survey_route_stage_is_not_inferred_from_the_amount_or_annex():
    facts = [('건설공사 설계 관련', '예'), ('건설공사 설계 관련 측량용역 금액', '3000만원'),
        ('설계공모 기본설계 지침 별표2 공종별 측량항목 해당', '예'),
        ('노선사업', '예'), ('노선사업 분야', '철도')]
    assert status(SURVEY, facts) == 'unknown'
    assert status(SURVEY, facts + [('실시설계', '예')]) == 'met'
    assert status(SURVEY, facts + [('실시설계', '아니오')]) == 'not_met'


def test_aggregate_onsite_exclusion_keeps_the_full_conjunction():
    facts = [('콘크리트용 순환골재 제품 제조용', '아니오'),
        ('아스팔트콘크리트용 순환골재 제품 제조용', '아니오'),
        ('배출자가 처리시설을 직접 설치 운영', '예'),
        ('처리시설이 건설공사 현장에 위치', '예'), ('건설폐기물 재활용으로 생산한 순환골재', '예')]
    assert status(AGGREGATE, facts) == 'unknown'
    assert status(AGGREGATE, facts + [('현장 순환골재 생산에 건설폐기물법 제27조 적용', '예')]) == 'not_met'
    assert status(AGGREGATE, [('콘크리트용 순환골재 제품 제조용', '예')]) == 'not_met'


def test_security_legal_business_type_is_not_equipment_or_registration():
    assert status(SECURITY, [('경비업법상 업무 유형', '기계경비업')]) == 'not_met'
    assert status(SECURITY, [('경비 기계 사용', '예'), ('기계경비업 등록', '예')]) == 'unknown'
    assert status(SECURITY, [('경비업법상 업무 유형', '시설경비업')]) == 'unknown'
    assert status(SECURITY, [('경비업법상 업무 유형', '시설경비업'),
        ('계약 상대자 발주자 자회사 해당', '아니오')]) == 'met'
    assert status(SECURITY, [('계약 발주자 공공기관 해당', '예'),
        ('계약 상대자 발주자 자회사 해당', '예'), ('해당 물품 수의계약 적용', '예')]) == 'not_met'


def test_external_reference_membership_cannot_be_generated_as_semantic_boolean():
    from submission.pps.catalog_semantics import generation_schema, BOOLEAN_FIELDS, schema
    import jsonschema
    gen = generation_schema(8)
    fields = gen['properties']['semantic_readings']['items']['properties']['field']['enum']
    for key in ('article27_production_basis', 'survey_annex2_item'):
        assert key not in BOOLEAN_FIELDS and key not in fields
        assert parse_value(key, '예') == ('boolean', True)
    jsonschema.Draft202012Validator.check_schema(gen)
    # The readable schema keeps past raw responses auditable. New generation
    # has the narrower channel, and the consumer independently checks it.
    assert 'survey_annex2_item' in schema(8)['properties']['semantic_readings']['items']['properties']['field']['enum']


def test_consumer_also_rejects_a_model_invented_external_reference_membership():
    from submission.b4_entry import parse_error
    from submission.pps import catalog_semantics as sem
    from tests.test_catalog_condition_review import setup, units
    from tests.test_catalog_semantics import semantic_packet, response, obj
    rec, _, knowledge = setup('모든 측량은 설계공모 기본설계 지침 별표2 공종별 측량항목 해당을 검토한다.',
                             name='측량용역', code='8115160401')
    packet = semantic_packet(rec, knowledge)
    reading = {'code': '8115160401', 'field': 'survey_annex2_item',
        'value_units': units(packet, '모든 측량은'), 'scope_units': units(packet, '품명: 측량용역'),
        'condition_units': [], 'scope': 'whole_named_purchase', 'modality': 'required',
        'quantifier': 'all_named_targets', 'reason': '별표를 언급하므로 해당한다고 추론', 'polarity': 'affirmed'}
    raw = response(obj(semantic_readings=[reading]))
    assert parse_error(packet, raw) is None
    row, detail = sem.review(rec, raw, packet, knowledge)
    assert row is None and detail['gate'] == 'model_used_undeclared_condition_field'
    assert detail['conditions'][0]['status'] == 'unknown'


@pytest.mark.parametrize('fields,expected', [
    ([('해당 품목 계약가격 방식', '다수공급자계약'), ('조달청 점유율 관리방안 예외 실제 적용', '아니오')], 'met'),
    ([('해당 품목 계약가격 방식', '다수공급자계약'), ('조달청 점유율 관리방안 예외 점유율', '20%'),
      ('조달청 점유율 관리방안 예외 실제 적용', '예')], 'not_met'),
    ([('해당 품목 계약가격 방식', '다수공급자계약'), ('조달청 점유율 관리방안 예외 점유율', '20%')], 'unknown'),
    ([('해당 품목 계약가격 방식', '총액계약'), ('3차원 프린팅 방식', 'FDM'),
      ('총액계약 구매액 예외 실제 적용', '아니오')], 'met'),
    ([('해당 품목 계약가격 방식', '총액계약'), ('3차원 프린팅 방식', 'FDM'),
      ('총액계약 구매액 대비 예외 금액 비율', '20%'), ('총액계약 구매액 예외 실제 적용', '예')], 'not_met'),
    ([('해당 품목 계약가격 방식', '총액계약'), ('3차원 프린팅 방식', 'SLA')], 'unknown'),
    ([('해당 품목 계약가격 방식', '총액계약'), ('3차원 프린팅 방식', 'SLA'),
      ('총액계약 구매액 예외 실제 적용', '아니오')], 'unknown'),
    ([('계약 낙찰방법', '제한경쟁입찰'), ('3차원 프린팅 방식', 'FDM')], 'unknown'),
])
def test_printer_note_uses_consensus_not_an_optimistic_or_of_interpretations(fields, expected):
    result, _ = evaluate_note(PRINTER, fields)
    assert result['status'] == expected
    assert result['interpretation_ambiguities'][0]['selected_interpretation'] is None
    assert result['whole_note_consumed']


def test_one_interpretation_is_not_silently_selected_or_mutated():
    from submission.pps.catalog_predicates import evaluate, atom, fields
    expression = {'interpretations': [atom('x', 'eq', True), atom('x', 'eq', False)]}
    obs = [{'field': 'x', 'type': 'boolean', 'value': True, 'scope': 'entire_named_purchase'}]
    assert fields(expression) == {'x'}
    assert evaluate(expression, obs) is None
    before = compile_note(PRINTER)
    other = compile_note(PRINTER)
    other['expression'].clear()
    assert compile_note(PRINTER) == before
