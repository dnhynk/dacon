"""Narrow, original-source fact contract for designation predicates.

A property mention in a nearby table/model/attachment does not bind it to every
item. Initial automatic consumption accepts explicit universal named-purchase
fields only. Other property mentions remain discoverable diagnostics; broader
table or model-proposed scope bridges need a separate validated consumer.
"""
from __future__ import annotations

from decimal import Decimal
import re
import unicodedata

from .catalog_condition_specs import BOOLEAN_LABELS, NUMERIC_FIELDS, ENUM_FIELDS, INTEGER_FIELDS


def _norm(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).casefold()


_LABELS = {
    'cpu_architecture': ('CPU 아키텍처', 'CPU 구조', '프로세서 아키텍처', 'Processor', 'CPU'),
    'cpu_count': ('CPU 개수', 'CPU 수량', 'CPU 수'),
    'cpu_base_ghz': ('CPU 기본주파수', 'CPU 기본 주파수', 'CPU 기본클럭'),
    'fixed_wing': ('고정익',), 'military_use': ('군사용',), 'hydrogen_drone': ('수소드론',),
    'self_weight_kg': ('자체중량',), 'operating_altitude_m': ('운용상승고도',),
    'material': ('재질', '제품 소재'), 'body_material': ('본체 재질',),
    'product_subtype': ('제품 세부유형', '세부유형'),
    'gross_tonnage': ('총톤수', '총톤수(Gross-tonnage)'), 'lifting_tonnes': ('인양능력',),
    'generation_kw': ('발전용량',), 'output_kw': ('정격출력', '출력용량'),
    'apparent_power_kva': ('피상전력', '정격용량'), 'pcs_output_kw': ('PCS 출력용량',),
    'gate_area_m2': ('수문 1련당 면적',), 'speed_m_per_min': ('분속',),
    'daily_tonnes': ('일일처리용량',), 'capacity_l': ('제품 용량',),
    'usable_tb': ('실용량(Usable)', '실용량'), 'physical_tb': ('물리적용량(Physical)', '물리적용량'),
    'cache_gb': ('캐시메모리',),
    'statutory_heritage_repair': ('국가유산수리법 제2조 제1호 국가유산수리용',),
    'public_agency_promotion': ('공공기관 홍보용',),
    'commissioning_public_agency_identified': ('제작 의뢰 공공기관 식별정보 포함',),
    'delivery_province': ('납품 광역지역',), 'annual_exception_percent': ('연간 예측량 대비 예외 비율',),
    'quota_exception_applied': ('연간 예측량 예외 적용',),
    'public_sale_housing': ('공공분양주택용',), 'urban_public_housing_complex': ('도심공공주택복합사업용',),
    'rubber_product': ('고무 소재 제품',), 'marine_diesel_generator': ('해상용 디젤발전기',),
    'floating_solar': ('수상용 태양광발전장치',), 'building_integrated_solar': ('건물일체형 태양광발전장치',),
    'power_generation_use': ('발전용',), 'domestic_heating_use': ('가정 난방용',), 'lng_use': ('LNG용',),
    'gas_pipe': ('가스관',), 'oil_pipe': ('송유관',), 'powder_lined_steel_pipe': ('분체라이닝식 강관',),
    'naval_vessel_use': ('해군 선박용',), 'portable_flowmeter': ('휴대용 유량계',),
    'fire_agency_supply': ('소방관련 기관 공급용',), 'vts_system': ('선박교통관제(VTS) 시스템',),
    'radiation_protective_clothing': ('방사능 보호복',), 'air_force_maintenance_clothing': ('공군정비복',),
    'latex_mattress': ('라텍스 매트리스',), 'fiberglass_composite_manhole': ('유리섬유복합관맨홀',),
    'jacking_concrete_pipe': ('원심력철근콘크리트추진관',), 'multi_video_wall': ('멀티형비디오월',),
    'laminate_flooring': ('강화마루제품',),
    # Party/contract fields cannot be inferred from procurement method metadata
    # or an anonymous institution token. They require an explicit scoped field.
    'public_agency': ('계약 발주자 공공기관 해당',), 'subsidiary_counterparty': ('계약 상대자 발주자 자회사 해당',),
    'private_contract': ('해당 물품 수의계약 적용',),
}
_UNITS = {
    'cpu_count': {'개': '1'}, 'cpu_base_ghz': {'ghz': '1', 'mhz': '.001'},
    'self_weight_kg': {'kg': '1', 'g': '.001'}, 'operating_altitude_m': {'m': '1', 'cm': '.01'},
    'gross_tonnage': {'톤': '1', 'ton': '1', 'gt': '1'}, 'lifting_tonnes': {'톤': '1', 'ton': '1', 'kg': '.001'},
    'generation_kw': {'kw': '1', 'w': '.001'}, 'output_kw': {'kw': '1', 'w': '.001'},
    'apparent_power_kva': {'kva': '1', 'va': '.001'}, 'pcs_output_kw': {'kw': '1', 'w': '.001'},
    'gate_area_m2': {'m2': '1'}, 'speed_m_per_min': {'m/분': '1', 'm/min': '1'},
    'daily_tonnes': {'ton/일': '1', '톤/일': '1'}, 'capacity_l': {'l': '1', 'ml': '.001'},
    # Do not assume TB/TiB or GB/GiB equivalence, or usable/physical equivalence.
    'usable_tb': {'tb': '1'}, 'physical_tb': {'tb': '1'}, 'cache_gb': {'gb': '1'},
    'annual_exception_percent': {'%': '1'},
}
_ENUMS = {
    'cpu_architecture': {'x86': 'x86', 'x86-64': 'x86', 'arm': 'arm', 'arm64': 'arm', 'aarch64': 'arm'},
    'material': {'금속': 'metal', '강철': 'steel', '철강': 'steel', '스테인레스': 'stainless',
        '스테인리스': 'stainless', '알루미늄': 'aluminum', '목재': 'wood', '플라스틱': 'plastic',
        '폴리에틸렌': 'pe', '폴리에틸렌(pe)': 'pe', 'pe': 'pe', '콘크리트': 'concrete', '고무': 'rubber'},
    'product_subtype': { _norm(x): _norm(x) for x in ('혼합간장', '양조간장', '한식간장', '자장소스',
        '소둔 결속선', '농산물 세척기', '생선묵 튀김제품')},
    'delivery_province': {alias: key for key, aliases in (
        ('서울', ('서울', '서울특별시')), ('경기', ('경기', '경기도')), ('인천', ('인천', '인천광역시')),
        ('대전', ('대전', '대전광역시')), ('세종', ('세종', '세종특별자치시')), ('충남', ('충남', '충청남도')),
        ('부산', ('부산', '부산광역시')), ('대구', ('대구', '대구광역시')), ('광주', ('광주', '광주광역시')),
        ('울산', ('울산', '울산광역시')), ('강원', ('강원', '강원특별자치도', '강원도')),
        ('충북', ('충북', '충청북도')), ('전북', ('전북', '전북특별자치도', '전라북도')),
        ('전남', ('전남', '전라남도')), ('경북', ('경북', '경상북도')), ('경남', ('경남', '경상남도')),
        ('제주', ('제주', '제주특별자치도'))) for alias in aliases},
}
_ENUMS['body_material'] = _ENUMS['material']
_ENUMS['material'].update({'강제': 'steel', '목제': 'wood', '합성수지': 'synthetic_resin',
    '합성수지제': 'synthetic_resin', '경금속': 'light_metal', '경금속제': 'light_metal'})
_LABELS.update(BOOLEAN_LABELS)
for _field, (_labels, _units) in NUMERIC_FIELDS.items():
    _LABELS[_field], _UNITS[_field] = _labels, _units
for _field, (_labels, _values) in ENUM_FIELDS.items():
    _LABELS[_field], _ENUMS[_field] = _labels, _values
_BOOL = {'예': True, '해당': True, '해당함': True, 'y': True,
         '아니오': False, '아님': False, '해당없음': False, 'n': False}
_NUMBER = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
_SCOPE_GUARD = re.compile(r'예시|예제|가정(?!\s*(?:용|난방|에서))|조건부|동등|선택할\s*수|대체할\s*수|철회|삭제|'
    r'참고\s*규격|권장\s*규격|선택\s*규격|경우에\s*한|일부에만|적용하지\s*않|적용\s*제외')


def parse_value(field, text):
    original = unicodedata.normalize('NFKC', text).casefold().strip()
    if original.endswith(('.', '。')):
        original = original[:-1].rstrip()
    n = _norm(original)
    if field in _UNITS:
        units = _UNITS[field]
        alternatives = '|'.join(re.escape(u) for u in sorted(units, key=len, reverse=True))
        match = re.fullmatch('('+_NUMBER+')[ \t]*('+alternatives+')[ \t]*(이하|미만|이상|초과)?', original)
        if not match:
            return None
        value = Decimal(match[1].replace(',', '')) * Decimal(units[match[2]])
        if field == 'vinyl_acetate_percent' and value > 100:
            return None  # A material composition is bounded; quota ratios need not be.
        if field in {'cpu_count', *INTEGER_FIELDS} and value != value.to_integral_value():
            return None
        op = match[3]
        lo, hi = (Decimal(0), value) if op in ('이하', '미만') else (value, None) if op else (value, value)
        if lo == hi and op == '미만':
            return None  # The admitted physical-value interval is empty.
        # Numbered physical properties have a nonnegative domain. Count 0 is
        # retained as observed; never repaired to one or extracted from cores.
        return 'interval', {'lower': str(lo) if lo is not None else None, 'upper': str(hi) if hi is not None else None,
            'lower_closed': op != '초과', 'upper_closed': op != '미만'}
    if field in _ENUMS:
        if field == 'paint_standard_type' and not re.fullmatch(r'ks[ \t]*m[ \t]*6080[ \t]+[1-5]종', original):
            return None
        if field == 'cpu_architecture':
            processor = re.fullmatch(r'(?:\d+-core[ \t]+)?(x86(?:-64)?|arm(?:64)?|aarch64)'
                r'(?:[ \t]+\(([^()]+)\))?', original)
            if not processor or (processor[2] and re.search(r'지원|또는|선택|옵션|support|emulat|\bor\b', processor[2])):
                return None
            n = processor[1]
        if n not in _ENUMS[field]:
            return None
        return 'enum', _ENUMS[field][n]
    if field in _LABELS and n in _BOOL:
        return 'boolean', _BOOL[n]
    return None


def source_facts(record, product_name, required_fields):
    target = _norm(product_name)
    observations, scope_issues = [], []
    labels = {_norm(label): field for field in required_fields for label in _LABELS.get(field, ())}
    for di, doc in enumerate(record['docs']):
        if doc['type'] not in {'공고문', '규격서', '과업지시서', '제안요청서'}:
            continue
        text = doc['text']
        for match in re.finditer(r'[^\r\n]+', text):
            raw = match[0]
            parts = re.split(r'[:：]', raw, maxsplit=1)
            if len(parts) != 2:
                continue
            left = _norm(parts[0])
            whole = re.fullmatch(r'(?:본계약의|본공고의)?(?:전체|모든)(?:납품|구매|공급|제작)'
                + re.escape(target) + r'의(.+)', left)
            field = labels.get(whole[1] if whole else left)
            if field is None:
                continue
            parsed = parse_value(field, parts[1])
            observations.append({'field': field, 'type': parsed[0] if parsed else 'unresolved',
                'value': parsed[1] if parsed else None,
                'scope': 'entire_named_purchase' if whole else 'unbound_property_mention',
                'issue': None if parsed else 'unparsed_or_ambiguous_property_value',
                'evidence': {'doc_index': di, 'doc_id': doc.get('doc_id'), 'doc_type': doc['type'],
                             'start': match.start(), 'end': match.end(), 'text': raw}})
    if observations:
        # Cross-document exceptions cannot disappear just because the numeric
        # field appeared in another attachment. This conservative initial gate
        # does not resolve the scope of a permission in either direction.
        from .catalog_permissions import occurrences
        scope_issues = occurrences(record, required_fields)
    return {'observations': observations, 'scope_issues': scope_issues,
        'whole_purchase_certified': False, 'target_name': product_name,
        'automatic_scope_contract': 'explicit_universal_named_purchase_field_v1'}
