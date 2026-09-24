"""Additional whole-clause contracts from the supplied designation notes.

Fields describe a property, purpose, standard or explicitly scoped amount of
the named purchase. They do not stand for a predicted designation/violation.
Only complete known clauses compile; examples and unrecognized tails cannot
be dropped. Source interpretation and purchase identity remain separate gates.
"""
from __future__ import annotations


BOOLEAN_LABELS = {
    'coal_based': ('석탄계',), 'granular_activated_carbon': ('입상활성탄',),
    'petrochemical_based': ('석유화학계',),
    'backpack_sprayer': ('배부식', '등에 매는 형식'),
    'shoulder_sprayer': ('견착식', '어깨에 매는 형식'),
    'filament_nonwoven': ('필라멘트 부직포', '장섬유 부직포'),
    'swimming_pool_tile': ('수영장타일',), 'functional_tile': ('기능성타일',),
    'polishing_tile': ('폴리싱타일',), 'stone_tile': ('석재타일',),
    'curtain_wall': ('커튼월',), 'roof_structure': ('지붕구조물 있음',),
    'pe_jetty': ('PE잔교',),
    'household_use': ('일반가정용', '가정용'), 'power_distribution_use': ('배전용',),
    'water_treatment_use': ('수처리용', '수처리설비용'), 'new_facility': ('신설 설비',),
    'water_supply_use': ('상수도용',), 'apartment_use': ('공동주택용',),
    'wired_system': ('유선방식',), 'standalone_display': ('단독형',),
    'micro_led': ('마이크로 LED',),
    'transmission_control_power_use': ('송변전 기기제어 전원용',),
    'telecom_stable_power_use': ('통신설비 안정 전원 공급용',),
    'cctv_mounting_use': ('CCTV 설치용',),
    'education_use': ('교육용',), 'experimental_use': ('실험용',),
    'separately_ordered': ('단독 발주',), 'disaster_prevention_facility': ('재난방지 시설용',),
    'food_waste_facility_use': ('음식물 처리장용',),
    'parking_enforcement_use': ('주차단속용',), 'security_use': ('보안용',),
    'sludge_storage_use': ('슬러지 저장용',), 'commercial_use': ('상업용',),
    'defense_standard': ('국방규격 적용',), 'police_standard': ('경찰규격 적용',),
    'outdoor_fitness_equipment': ('야외헬스기구',), 'printer_use': ('인쇄기용',),
    'vienna_sausage': ('비엔나 소시지',),
    'annual_quantity_exception_applied': ('연간 구매예정수량 예외 적용',),
    'defense_project': ('국방사업용',), 'route_project': ('노선사업',),
    'detailed_design': ('실시설계',),
    'agricultural_film_use': ('농업용 필름',),
    'concrete_aggregate_product_use': ('콘크리트용 순환골재 제품 제조용',),
    'asphalt_aggregate_product_use': ('아스팔트콘크리트용 순환골재 제품 제조용',),
    'waste_generator_installs_operates': ('배출자가 처리시설을 직접 설치 운영',),
    'facility_at_construction_site': ('처리시설이 건설공사 현장에 위치',),
    'construction_waste_recycled_aggregate': ('건설폐기물 재활용으로 생산한 순환골재',),
    'article27_production_basis': ('현장 순환골재 생산에 건설폐기물법 제27조 적용',),
    'construction_design_related': ('건설공사 설계 관련',),
    'survey_annex2_item': ('설계공모 기본설계 지침 별표2 공종별 측량항목 해당',),
    'mas_exception_applied': ('조달청 점유율 관리방안 예외 실제 적용',),
    'total_purchase_exception_applied': ('총액계약 구매액 예외 실제 적용',),
}

# Canonical dimensions are field-specific. In particular the nominal Φ size
# is not an observed outside diameter, and a tender/defense/service amount is
# not supplied by the generic estimated-price field.
NUMERIC_FIELDS = {
    'building_storeys': (('시공 대상 건물 층수', '건물 층수'), {'층': '1'}),
    'discharge_diameter_mm': (('토출구경',), {'mm': '1', 'cm': '10', 'm': '1000'}),
    'nominal_pipe_size_phi': (('관 호칭구경',), {'φ': '1'}),
    'display_luminance_cd_m2': (('표시 휘도', '휘도'), {'cd/m2': '1'}),
    'pixel_pitch_mm': (('픽셀간격', '픽셀 간격'), {'mm': '1', 'cm': '10'}),
    'chemical_suit_type': (('화학물질보호복 형식',), {'형식': '1'}),
    'annual_planned_quantity_exception_percent': (
        ('연간 구매예정수량 대비 예외 비율',), {'%': '1'}),
    'public_tender_amount_won': (('공공입찰 금액',),
        {'원': '1', '천원': '1000', '만원': '10000', '억원': '100000000'}),
    'defense_project_total_won': (('전체 국방사업 총액',),
        {'원': '1', '천원': '1000', '만원': '10000', '억원': '100000000'}),
    'geological_service_value_won': (('지질 관련 용역 금액',),
        {'원': '1', '천원': '1000', '만원': '10000', '억원': '100000000'}),
    'survey_service_value_won': (('건설공사 설계 관련 측량용역 금액',),
        {'원': '1', '천원': '1000', '만원': '10000', '억원': '100000000'}),
    'vinyl_acetate_percent': (('초산비닐 함량',), {'%': '1'}),
    'mas_exception_share_percent': (('조달청 점유율 관리방안 예외 점유율',), {'%': '1'}),
    'total_purchase_exception_percent': (('총액계약 구매액 대비 예외 금액 비율',), {'%': '1'}),
}
INTEGER_FIELDS = {'building_storeys', 'chemical_suit_type'}
# Legal-reference membership is not inferred from a nearby law name by the
# semantic boolean reader. Only an explicit original scoped declaration can
# supply it until a separate supplied-reference matching consumer is verified.
LITERAL_ONLY_FIELDS = {'article27_production_basis', 'survey_annex2_item'}

# Broad material words admit narrower alternatives. "Metal" does not prove
# aluminum and also does not disprove it. Leaves are disjoint semantic types;
# these are not inferred physical measurements or probabilities.
MATERIAL_DOMAINS = {
    'metal': {'carbon_steel', 'stainless', 'aluminum', 'other_light_metal', 'other_metal'},
    'steel': {'carbon_steel', 'stainless'}, 'stainless': {'stainless'},
    'light_metal': {'aluminum', 'other_light_metal'}, 'aluminum': {'aluminum'},
    'synthetic_resin': {'pe', 'other_plastic', 'other_resin'},
    'plastic': {'pe', 'other_plastic'}, 'pe': {'pe'},
}

ENUM_FIELDS = {
    'water_treatment_type': (('수처리 종류',), {
        '하폐수': 'wastewater', '하수': 'wastewater', '폐수': 'wastewater',
        '상수': 'water_supply'}),
    'paint_standard_type': (('도료 규격 형식',), {
        **{f'ksm6080{i}종': f'ks_m_6080_type_{i}' for i in range(1, 6)}}),
    'storage_vessel_type': (('저장 용기 유형',), {'탱크': 'tank', '사일로': 'silo', '호퍼': 'hopper'}),
    'kitchen_stand_type': (('주방 받침대 유형',), {
        '가정용가스레인지대': 'household_gas_range_stand', '복합취사대': 'combined_cooking_stand',
        '작업대': 'workbench'}),
    'food_subtype': (('식품 세부유형',), {x: x for x in (
        '돈까스', '미트볼', '탕수육', '팝콘형치킨', '불고기패티', '햄', '부대찌개용햄',
        '소시지', '부대찌개용소시지', '비엔나소시지', '맛김', '김자반',
        '자장면', '쫄면', '물냉면', '비빔냉면', '가락국수', '당면', '즉석쌀국수')}),
    'training_equipment_type': (('교육실습장비 유형',), {x: x for x in (
        '자동제어교육실습장비', '마이크로프로세서교육실습장비', '과학교구실험실습장비',
        '운전교육실습장비')}),
    'film_material_type': (('필름 유형',), {'pe': 'pe', 'pe필름': 'pe', '폴리에틸렌필름': 'pe',
        'po': 'po', 'po필름': 'po', 'eva': 'eva', 'eva필름': 'eva', 'pvc': 'pvc', 'pvc필름': 'pvc'}),
    'route_sector': (('노선사업 분야',), {'도로': 'road', '철도': 'railway', '지하철': 'railway',
        '지중송배전전력구': 'underground_power_duct', '지중송·배전전력구': 'underground_power_duct',
        '하천': 'river', '광역수도': 'regional_industrial_water', '공업용수도': 'regional_industrial_water'}),
    'statutory_security_type': (('경비업법상 업무 유형',), {'시설경비업': 'facility',
        '기계경비업': 'machine', '특수경비업': 'special'}),
    'catalog_contract_pricing_type': (('해당 품목 계약가격 방식',), {
        '다수공급자계약': 'mas', '총액계약': 'total', '단일공급자단가계약': 'single_supplier_unit'}),
    'printing_method': (('3차원 프린팅 방식',), {x: x for x in ('fdm', 'sla', 'sls', 'dlp')}),
}

MEANINGS = {
    'functional_tile': '기능성 타일 여부. 손잡이·골·수조벽트렌치·트린치앵글은 예시이며 목록이 전부는 아니다.',
    'transmission_control_power_use': '송전·배전설비의 기기제어 전원을 위한 충전장치 용도. 일반적인 충전·발전용과 다르다.',
    'telecom_stable_power_use': '통신설비에 안정된 전원을 공급하는 충전장치 용도. 통신기능이 있다는 사실과 다르다.',
    'defense_standard': '해당 납품제품에 적용하는 국방규격. 국방기관 발주 또는 군용이라는 이유로 추정하지 않는다.',
    'police_standard': '해당 납품제품에 적용하는 경찰규격. 경찰기관 발주라는 이유로 추정하지 않는다.',
    'annual_quantity_exception_applied': '해당 품목에 연간 구매예정수량을 분모로 한 예외를 실제 적용함. 적용 가능 또는 제출 생략과 다르다.',
    'annual_planned_quantity_exception_percent': '같은 연도·품목의 연간 구매예정수량 대비 예외 적용 수량의 비율. 금액·예측량·계약 점유율과 다르다.',
    'nominal_pipe_size_phi': '고시의 200Φ와 같은 호칭구경 표기. 실측 외경이나 단위 없는 숫자로 대체하지 않는다.',
    'public_tender_amount_won': '해당 품목의 공공입찰 금액. 추정가격·차수별 금액·전체 예산에서 임의 대입하지 않는다.',
    'defense_project_total_won': '해당 물품이 속한 국방사업 전체 총액. 카메라 단가나 부분 계약 금액과 다르다.',
    'geological_service_value_won': '해당 지질 관련 용역의 금액. 다른 용역을 포함한 전체 사업예산과 다르다.',
    'route_project': '해당 용역이 노선사업인지. 주소·이동경로·타 사업 언급으로 대체하지 않는다.',
    'detailed_design': '해당 용역의 설계 단계가 실시설계인지. 기본설계나 추후 실시설계 예정과 다르다.',
    'vienna_sausage': '비엔나 소시지 여부. 소시지·부대찌개용이라는 상위 품명만으로 부정할 수 없다.',
    'film_material_type': '납품 필름의 실제 PE/PO/EVA 등 유형. 폴리에틸렌필름이라는 고시 후보명·등록코드로 대입하지 않는다.',
    'article27_production_basis': '해당 현장 생산에 건설폐기물법 제27조가 적용된다는 명시 원문. 법률명 인용만으로 해당 여부를 판단하지 않는다.',
    'survey_annex2_item': '해당 측량 과업이 지정 지침 별표2의 공종별 측량항목에 속한다는 명시 원문. 측량 일반이나 법령명 언급과 다르다. 외부 별표 분류 추론은 지원하지 않는다.',
    'survey_service_value_won': '건설공사 설계 관련 측량용역 자체의 금액. 설계·토목공사 전체 금액과 다르다.',
    'statutory_security_type': '실제 경비 과업의 경비업법상 유형. 경비 기계 사용이나 입찰업종 등록만으로 해당 업무를 확정하지 않는다.',
    'catalog_contract_pricing_type': '해당 품목 계약의 다수공급자/총액 등 가격 방식. 제한경쟁·협상 낙찰방법·장기계속 차수와 다르다.',
    'mas_exception_applied': '해당 품목에 조달청 점유율 관리방안에 따른 예외가 실제 적용됨. 20% 이하로 적용 가능하다는 사실과 다르다.',
    'total_purchase_exception_applied': '해당 총액계약 품목에 구매액 기준 예외가 실제 적용됨. 예외 가능 비율과 다르다.',
    'total_purchase_exception_percent': '같은 총액계약 구매액 대비 예외 적용 금액의 비율. 연간 수량이나 MAS 점유율과 다르다.',
}


def definition(field):
    """Describe newly supported fields without changing historical field plans."""
    if field in NUMERIC_FIELDS:
        result = {'type': 'interval', 'accepted_units': list(NUMERIC_FIELDS[field][1]),
                  'integer_domain': field in INTEGER_FIELDS}
    elif field in ENUM_FIELDS:
        result = {'type': 'enum', 'literal_values': list(ENUM_FIELDS[field][1])}
    elif field in BOOLEAN_LABELS:
        result = {'type': 'boolean', 'missing_is_false': False}
    else:
        return None
    if field in MEANINGS:
        result['meaning'] = MEANINGS[field]
    if field in LITERAL_ONLY_FIELDS:
        result['semantic_channel_allowed'] = False
        result['external_definition_matching_verified'] = False
    return result


def compile_clause(text):
    # Imported only while compiling, after the predicate operators are defined.
    from .catalog_predicates import atom, all_of, any_of, negate, flag, compact
    n = compact(text)
    eq = lambda field, value: atom(field, 'eq', value)
    one_of = lambda field, values: atom(field, 'in', values)
    implies = lambda condition, consequence: any_of(negate(condition), consequence)

    clauses = {
        '석탄계 입상활성탄 및 석유화학계 활성탄 제외':
            negate(any_of(all_of(flag('coal_based'), flag('granular_activated_carbon')),
                          flag('petrochemical_based'))),
        '배부식(등에 매는 형식) 또는 견착식(어깨에 매는 형식) 제품은 제외':
            negate(any_of(flag('backpack_sprayer'), flag('shoulder_sprayer'))),
        '필라멘트(생사의 섬유나 절단하지 않고 방사한 화학섬유에서 얻은 장섬유)로 만들어진 토목용 부직포는 제외':
            negate(flag('filament_nonwoven')),
        '수영장타일 중 기능성타일(손잡이타일, 골타일, 수조벽트렌치타일, 트린치앵글타일 등), 폴리싱타일 및 석재타일 제외':
            negate(any_of(all_of(flag('swimming_pool_tile'), flag('functional_tile')),
                          flag('polishing_tile'), flag('stone_tile'))),
        '알루미늄제에 한함': eq('material', 'aluminum'),
        '20층 이상 건물에 시공하는 커튼월은 제외':
            negate(all_of(flag('curtain_wall'), atom('building_storeys', 'ge', '20'))),
        '지붕구조물이 있는 제품에 한함': flag('roof_structure'),
        '강제, 목제, 합성수지제 (PE잔교 제외), 경금속제에 한함':
            all_of(negate(eq('material', 'pe')),
                any_of(one_of('material', ['steel', 'wood', 'light_metal', 'aluminum']),
                       all_of(eq('material', 'synthetic_resin'), negate(flag('pe_jetty'))))),
        'KS M 6080 5종(상온경화형 플라스틱 도료) 제외': negate(eq('paint_standard_type', 'ks_m_6080_type_5')),
        '가정용(일반가정에서 사용) 및 배전용(한국전력 등 배전선로에 사용)은 제외':
            negate(any_of(flag('household_use'), flag('power_distribution_use'))),
        '수처리설비에 한함': flag('water_treatment_use'),
        '신설 설비는 일일처리용량 하폐수는 10만톤 이하, 상수는 30만톤 이하에 한함':
            implies(flag('new_facility'), all_of(
                implies(eq('water_treatment_type', 'wastewater'), atom('daily_tonnes', 'le', '100000')),
                implies(eq('water_treatment_type', 'water_supply'), atom('daily_tonnes', 'le', '300000')))),
        '공동주택용 200Φ 이하 제외':
            negate(all_of(flag('apartment_use'), atom('nominal_pipe_size_phi', 'le', '200'))),
        '공동주택 유선방식 제외': negate(all_of(flag('apartment_use'), flag('wired_system'))),
        '단독형 600cd/㎡미만에 한함':
            all_of(flag('standalone_display'), atom('display_luminance_cd_m2', 'lt', '600')),
        '픽셀간격 1mm 이하 마이크로 LED 제외':
            negate(all_of(flag('micro_led'), atom('pixel_pitch_mm', 'le', '1'))),
        '송변전용(송전 설비 및 배전설비의 기기제어 전원용에 사용하는 충전장치) 또는 통신용(통신설비에 안정된 전원을 공급하기 위하여 사용되는 충전장치)에 한함':
            any_of(flag('transmission_control_power_use'), flag('telecom_stable_power_use')),
        'CCTV를 설치하기 위한 금속기둥에 한함': flag('cctv_mounting_use'),
        # These are enumerated permitted uses/standards, not a requirement to
        # serve both purposes or meet two different institutions' standards.
        '교육 및 실험용에 한함': any_of(flag('education_use'), flag('experimental_use')),
        '단독 발주되는 재난방지 시설에 한함':
            all_of(flag('separately_ordered'), flag('disaster_prevention_facility')),
        '수처리용 및 음식물 처리장용 제품에 한함':
            any_of(flag('water_treatment_use'), flag('food_waste_facility_use')),
        '주차단속 및 보안용 제품에 한함': any_of(flag('parking_enforcement_use'), flag('security_use')),
        '4~6형식에 한함': all_of(atom('chemical_suit_type', 'ge', '4'), atom('chemical_suit_type', 'le', '6')),
        '슬러지 저장용 탱크 및 사일로에 한함':
            all_of(flag('sludge_storage_use'), one_of('storage_vessel_type', ['tank', 'silo'])),
        '가정용 가스레인지대, 복합취사대에 한함(상업용 제외)':
            all_of(one_of('kitchen_stand_type', ['household_gas_range_stand', 'combined_cooking_stand']),
                   negate(flag('commercial_use'))),
        '국방규격에 한함': flag('defense_standard'),
        '국방규격 및 경찰규격에 한함': any_of(flag('defense_standard'), flag('police_standard')),
        '야외헬스기구에 한함': flag('outdoor_fitness_equipment'),
        '돈까스, 미트볼, 탕수육, 팝콘형 치킨, 불고기패티에 한함':
            one_of('food_subtype', ['돈까스', '미트볼', '탕수육', '팝콘형치킨', '불고기패티']),
        '햄(부대찌개용 햄 포함)에 한함': one_of('food_subtype', ['햄', '부대찌개용햄']),
        '소시지, 부대찌개용 소시지(비엔나 소시지는 제외)에 한함':
            all_of(one_of('food_subtype', ['소시지', '부대찌개용소시지']), negate(flag('vienna_sausage'))),
        '맛김 및 김자반에 한함': one_of('food_subtype', ['맛김', '김자반']),
        '자장면, 쫄면, 물냉면, 비빔냉면, 가락국수, 당면, 즉석 쌀국수에 한함':
            one_of('food_subtype', ['자장면', '쫄면', '물냉면', '비빔냉면', '가락국수', '당면', '즉석쌀국수']),
        '인쇄기용에 한함': flag('printer_use'),
        '자동제어교육실습장비, 마이크로프로세서교육실습장비, 과학교구실험실습장비에 한함':
            one_of('training_equipment_type', ['자동제어교육실습장비', '마이크로프로세서교육실습장비', '과학교구실험실습장비']),
        '연간 구매예정수량 20% 이내에서 예외':
            negate(all_of(atom('annual_planned_quantity_exception_percent', 'le', '20'),
                          flag('annual_quantity_exception_applied'))),
        '공공입찰 금액 10억원 미만에 한함': atom('public_tender_amount_won', 'lt', '1000000000'),
        '총액 100억원 이상 대규모 국방사업의 경우 제외':
            negate(all_of(flag('defense_project'), atom('defense_project_total_won', 'ge', '10000000000'))),
        '1천만원 이상의 지질 관련 용역에 한함': atom('geological_service_value_won', 'ge', '10000000'),
        '노선사업은 1천만원 이상의 실시 설계에 한함':
            implies(flag('route_project'), all_of(flag('detailed_design'),
                atom('geological_service_value_won', 'ge', '10000000'))),
        '농업용 필름(PO필름, 초산비닐 함량 13% 이내의 EVA필름)포함':
            any_of(eq('film_material_type', 'pe'), all_of(flag('agricultural_film_use'),
                any_of(eq('film_material_type', 'po'), all_of(eq('film_material_type', 'eva'),
                    atom('vinyl_acetate_percent', 'le', '13'))))),
        '순환골재 제품 제조용(콘크리트용, 아스팔트콘크리트용) 및 「건설폐기물법」 제27조에 따라 배출자가 건설공사 현장에서 건설폐기물처리시설을 직접 설치 운영하여 건설폐기물을 재활용 하고자 생산한 순환골재는 제외':
            negate(any_of(flag('concrete_aggregate_product_use'), flag('asphalt_aggregate_product_use'),
                all_of(flag('article27_production_basis'), flag('waste_generator_installs_operates'),
                       flag('facility_at_construction_site'), flag('construction_waste_recycled_aggregate')))),
        '건설공사 설계 관련 공간정보관리법 시행령 제34조 제2항에 따른 금액(3천만원) 이상의 측량용역으로 ‘국토교통부 고시’(설계공모, 기본설계 등의 시행 및 설계의 경제성 등 검토에 관한 지침) 별표2의 각 공종별 측량항목에 한정':
            all_of(flag('construction_design_related'), atom('survey_service_value_won', 'ge', '30000000'),
                   flag('survey_annex2_item')),
        '위의 측량 항목 중 도로, 철도(지하철 포함), 지중 송·배전 전력구, 하천, 광역 ·공업용 수도분야 노선사업은 실시설계에 한함':
            implies(all_of(flag('route_project'), one_of('route_sector', [
                'road', 'railway', 'underground_power_duct', 'river', 'regional_industrial_water'])),
                flag('detailed_design')),
        '경비업법상의 기계경비업, 특수경비업 제외':
            negate(one_of('statutory_security_type', ['machine', 'special'])),
        '공공기관이 자회사와 수의계약을 체결하는 경우 적용 대상에서 제외':
            negate(all_of(flag('public_agency'), flag('subsidiary_counterparty'), flag('private_contract'))),
        '다수공급자계약은 조달청의 점유율 관리 방안에 따라 20% 이내에서 예외적용 가능':
            implies(eq('catalog_contract_pricing_type', 'mas'), negate(all_of(
                atom('mas_exception_share_percent', 'le', '20'), flag('mas_exception_applied')))),
    }
    for clause, expression in clauses.items():
        if n == compact(clause):
            return expression, []
    for threshold in ('500', '1,200'):
        if n == compact(f'상수도용은 토출구경 {threshold}mm 미만에 한함'):
            return implies(flag('water_supply_use'), atom('discharge_diameter_mm', 'lt', threshold.replace(',', ''))), []
    ambiguous = '총액 계약 체결시 FDM 방식 제품은 구매액의 20% 이내에서 예외를 적용하며, 다른 방식 제품은 적용 제외'
    if n == compact(ambiguous):
        exception = negate(all_of(atom('total_purchase_exception_percent', 'le', '20'),
                                  flag('total_purchase_exception_applied')))
        total, fdm = eq('catalog_contract_pricing_type', 'total'), eq('printing_method', 'fdm')
        return {'interpretations': [implies(total, all_of(fdm, exception)),
                                    implies(all_of(total, fdm), exception)],
                'ambiguity': {'kind': 'designation_or_exception_application_scope',
                    'source_clause': text,
                    'unresolved_phrase': '다른 방식 제품은 적용 제외',
                    'alternatives': ['다른 방식은 지정 대상에서 제외', '다른 방식은 예외 적용에서 제외'],
                    'selected_interpretation': None}}, []
    return None
