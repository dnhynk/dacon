"""Closed designation predicates compiled from the supplied catalog's notes.

These predicates never identify a purchase. Each full note must be understood;
an unrecognized extra clause prevents execution of the whole note. Missing facts
are unknown, including the non-occurrence of an exclusion. Three-valued logic
can still decide a conjunction/alternative from its decisive branch.
"""
from __future__ import annotations

from decimal import Decimal
import re
import unicodedata


def compact(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).casefold()


def atom(field, operator, value):
    return {'field': field, 'operator': operator, 'value': value}


def all_of(*args):
    return {'all': list(args)}


def any_of(*args):
    return {'any': list(args)}


def negate(arg):
    return {'not': arg}


def flag(field):
    return atom(field, 'eq', True)


def fields(expression):
    if 'field' in expression:
        return {expression['field']}
    if 'not' in expression:
        return fields(expression['not'])
    return set().union(*(fields(x) for x in children(expression)))


def children(expression):
    for operator in ('all', 'any', 'interpretations'):
        if operator in expression:
            return expression[operator]
    return []


def ambiguities(expression):
    if 'not' in expression:
        return ambiguities(expression['not'])
    return ([expression['ambiguity']] if 'ambiguity' in expression else []) + [
        issue for child in children(expression) for issue in ambiguities(child)]


# Pure additions to the category's scope. No blanket "ends in 포함" heuristic:
# a conditional inclusion (e.g. a resin concentration) is not a free extension.
_EXTENSIONS = {compact(s) for s in (
    'PDF물탱크 포함', 'VR(Voltage Regulator) 포함', '중계기 포함', '산책로 설치 포함',
    '해외전시회 포함', '통합감시 제어설비 포함', '가스냉각장치, 유해 가스 저감 장치 포함',
    '에어필터(프리필터, 미듐, 헤파필터), 자동공기여과장치 포함', '상·하수 측정용 계측기 포함',
    "'전시산업발전법' 제2조 2호, 3호에 따른 전시회 및 전시회 부대행사용 전시부스 설치 및 디자인서비스 등을 포함",
    "'전시산업발전법' 제2조 2호, 3호에 따른 전시회 및 전시회 부대행사용 전시홍보관설치 및 디자인서비스 등을 포함",
)}


_EXCLUDED_FLAGS = {
    '고무 소재의 제품은 제외': 'rubber_product',
    '해상용 디젤발전기는 제외': 'marine_diesel_generator',
    '수상용 또는 건물일체형 태양광 발전장치는 제외': ('floating_solar', 'building_integrated_solar'),
    '공공분양주택(뉴홈, 신혼희망타운 포함), 도심공공주택복합사업은 적용 제외': ('public_sale_housing', 'urban_public_housing_complex'),
    '도심공공주택복합사업은 적용 제외': 'urban_public_housing_complex',
    '발전용(전력생산용 보일러)과 가정용(가정에서 난방용으로 사용하는 보일러)은 제외': ('power_generation_use', 'domestic_heating_use'),
    '발전용 및 액화 천연가스 (LNG)용 제품은 제외': ('power_generation_use', 'lng_use'),
    '가스관 또는 송유관은 제외': ('gas_pipe', 'oil_pipe'),
    '분체라이닝식 강관은 제외': 'powder_lined_steel_pipe',
    '해군 선박용은 제외': 'naval_vessel_use',
    '휴대용 유량계 제외': 'portable_flowmeter',
    '소방관련 기관 공급용 제품은 제외': 'fire_agency_supply',
    '선박교통관제(VTS) 시스템 제외': 'vts_system',
    '방사능 보호복은 제외': 'radiation_protective_clothing',
    '공군정비복은 제외': 'air_force_maintenance_clothing',
    '라텍스 매트리스 제외': 'latex_mattress',
    '유리섬유복합관맨홀은 제외': 'fiberglass_composite_manhole',
    '원심력철근콘크리트추진관 제외': 'jacking_concrete_pipe',
    '멀티형비디오월 제외': 'multi_video_wall',
    '강화마루제품 제외.': 'laminate_flooring',
}
_EXCLUDED_FLAGS = {compact(k): v for k, v in _EXCLUDED_FLAGS.items()}

_MATERIALS = {'금속': ['metal', 'steel', 'stainless', 'aluminum'], '목재': ['wood'],
    '플라스틱': ['plastic', 'pe'], '폴리에틸렌(pe)': ['pe'],
    '스테인레스': ['stainless'], '콘크리트': ['concrete']}

_SUBTYPES = {compact(x): x for x in (
    '혼합간장', '자장소스', '소둔 결속선', '농산물 세척기', '생선묵 튀김제품',
)}

_NUMERIC = (
    (r'총톤수\(gross-tonnage\)([\d,.]+)톤(미만|이하)에한함', 'gross_tonnage', 1),
    (r'인양능력([\d,.]+)ton(초과|이상)제품에한함', 'lifting_tonnes', 1),
    (r'발전용량([\d,.]+)kw(미만|이하)에한함', 'generation_kw', 1),
    (r'([\d,.]+)kw(미만|이하)에한함', 'output_kw', 1),
    (r'([\d,.]+)kva(미만|이하)에한함', 'apparent_power_kva', 1),
    (r'전력변환장치\(pcs\)출력용량([\d,.]+)kw(미만|이하)에한함', 'pcs_output_kw', 1),
    (r'수문1련당면적([\d,.]+)m2(미만|이하)에한함', 'gate_area_m2', 1),
    (r'속도분속([\d,.]+)m(미만|이하)에한함', 'speed_m_per_min', 1),
    (r'일일처리용량([\d,.]+)ton/일(미만|이하)에한함', 'daily_tonnes', 1),
)
_OPERATORS = {'미만': 'lt', '이하': 'le', '초과': 'gt', '이상': 'ge'}


def _clause(text):
    # Two separately identified numbers in a standard are not one damaged
    # numeral. Validate the full standard clause before the generic split-digit
    # guard; never remove whitespace from inside the actual standard number.
    if re.fullmatch(r'KS\s*M\s*6080\s+5종\(상온경화형\s*플라스틱\s*도료\)\s*제외', text, re.I):
        return negate(atom('paint_standard_type', 'eq', 'ks_m_6080_type_5')), []
    if re.search(r'\d[ \t]+\d|\d[ \t]+[.,](?=[ \t]*\d)|\d[.,][ \t]+\d', text):
        return None  # Removing whitespace must not invent a number.
    n = compact(text)
    if n in _EXTENSIONS:
        return all_of(), [text]
    if n in _EXCLUDED_FLAGS:
        names = _EXCLUDED_FLAGS[n]
        return negate(any_of(*(flag(f) for f in ((names,) if isinstance(names, str) else names)))), []
    if re.fullmatch(r"[‘']국가유산수리등에관한법률[’']제2조제1호에서정한국가유산수리용(?:은)?제외", n):
        return negate(flag('statutory_heritage_repair')), []
    for pattern, field, scale in _NUMERIC:
        match = re.fullmatch(pattern, n)
        if match:
            # Full numeric notation only: never turn 1,00 or 3..2 into a value.
            if not re.fullmatch(r'(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?', match[1]):
                return None
            return atom(field, _OPERATORS[match[2]], str(Decimal(match[1].replace(',', '')) * scale)), []
    material = re.fullmatch(r'(본체가)?(.+)소재의제품에한함', n)
    if material:
        options = material[2].split('또는')
        if all(p in _MATERIALS for p in options):
            choices = sorted({v for p in options for v in _MATERIALS[p]})
            return atom('body_material' if material[1] else 'material', 'in', choices), []
    subtype = re.fullmatch(r'(.+)에한함', n)
    if subtype and subtype[1] in _SUBTYPES:
        return atom('product_subtype', 'eq', subtype[1]), []
    if n == compact('x86 서버 CPU 1개 전체, CPU 2개 중 Clock(기본주파수) 3.2GHz 이하 제품에 한함'):
        return all_of(atom('cpu_architecture', 'eq', 'x86'), any_of(atom('cpu_count', 'eq', '1'),
            all_of(atom('cpu_count', 'eq', '2'), atom('cpu_base_ghz', 'le', '3.2')))), []
    if n == compact('고정익, 군사용, 수소드론 제외'):
        return negate(any_of(flag('fixed_wing'), flag('military_use'), flag('hydrogen_drone'))), []
    if n == compact('자체중량 25㎏ 이하 또는 운용상승고도 150m 이하의 무인비행장치에 한함'):
        # Identity of a drone/unmanned aircraft remains the caller's obligation.
        return any_of(atom('self_weight_kg', 'le', '25'), atom('operating_altitude_m', 'le', '150')), []
    if n == compact('실용량(Usable) 100TB 이하이면서 캐시메모리 64GB 이하 제품 또는 물리적용량(Physical) 200TB 이하이면서 캐시메모리 64GB 이하 제품에 한함'):
        return all_of(atom('cache_gb', 'le', '64'), any_of(atom('usable_tb', 'le', '100'),
            atom('physical_tb', 'le', '200'))), []
    if n == compact('공공기관 홍보용(제작 의뢰한 공공기관을 식별할 수 있는 정보가 포함된 영상은 모두 해당)에 한함'):
        return any_of(flag('public_agency_promotion'), flag('commissioning_public_agency_identified')), []
    if n == compact('용량 35L 이하 제품 제외'):
        return atom('capacity_l', 'gt', '35'), []
    if n == compact('공공기관이 자회사와 수의계약하는 경우 제외'):
        return negate(all_of(flag('public_agency'), flag('subsidiary_counterparty'), flag('private_contract'))), []
    region = re.fullmatch(r'(.+)지역(?:은)?연간예측량의20%이내에서예외가능', n)
    if region:
        provinces = region[1].split(',')
        if provinces in (['서울', '경기', '인천'], ['서울', '경기', '인천', '대전', '세종', '충남']):
            # Eligibility for an optional quota exception does not prove use of it.
            return negate(all_of(atom('delivery_province', 'in', provinces),
                atom('annual_exception_percent', 'le', '20'), flag('quota_exception_applied'))), []
    from .catalog_condition_specs import compile_clause
    return compile_clause(text)


def compile_note(note):
    """Return a closed expression or None; numbered clauses must all parse."""
    if not isinstance(note, str) or not note.strip():
        return None
    parts = [note]
    markers = list(re.finditer(r'(?<![\d.])([1-9])\.(?=\s*\D)', note))
    if markers and not note[:markers[0].start()].strip():
        if [int(m[1]) for m in markers] != list(range(1, len(markers)+1)):
            return None
        parts = [note[m.end():markers[i+1].start() if i+1 < len(markers) else len(note)].strip()
                 for i, m in enumerate(markers)]
    parsed = [_clause(p) for p in parts]
    if any(x is None for x in parsed):
        return None
    expressions = [x[0] for x in parsed if x[0] != all_of()]
    expression = expressions[0] if len(expressions) == 1 else all_of(*expressions)
    issues = ambiguities(expression)
    return {'expression': expression, 'scope_extensions': [p for x in parsed for p in x[1]],
            'required_fields': sorted(fields(expression)), 'whole_note_consumed': True,
            **({'interpretation_ambiguities': issues} if issues else {})}


def _numeric(interval, operator, threshold):
    """Truth over every admitted value, keeping open interval endpoints."""
    lo, hi = interval['lower'], interval['upper']
    lo = Decimal(lo) if lo is not None else None
    hi = Decimal(hi) if hi is not None else None
    t = Decimal(threshold)
    if operator in ('gt', 'ge'):
        opposite = _numeric(interval, 'le' if operator == 'gt' else 'lt', threshold)
        return None if opposite is None else not opposite
    if operator == 'lt':
        if hi is not None and (hi < t or (hi == t and not interval['upper_closed'])):
            return True
        if lo is not None and lo >= t:
            return False
    elif operator == 'le':
        if hi is not None and hi <= t:
            return True
        if lo is not None and (lo > t or (lo == t and not interval['lower_closed'])):
            return False
    elif operator == 'eq':
        if lo == hi == t:
            return True
        if ((lo is not None and (lo > t or (lo == t and not interval['lower_closed'])))
                or (hi is not None and (hi < t or (hi == t and not interval['upper_closed'])))):
            return False
    return None


def evaluate(expression, observations):
    if 'interpretations' in expression:
        alternatives = [evaluate(x, observations) for x in expression['interpretations']]
        # Alternative scopes of a supplied note are not OR opportunities. A
        # designation result is available only when every reading agrees.
        return alternatives[0] if alternatives and all(v is alternatives[0] for v in alternatives) else None
    if 'field' in expression:
        candidates = [x for x in observations if x['field'] == expression['field']]
        if not candidates or any(x.get('issue') or x['scope'] != 'entire_named_purchase' for x in candidates):
            return None
        values = []
        for fact in candidates:
            if fact['type'] == 'interval':
                value = _numeric(fact['value'], expression['operator'], expression['value'])
            elif expression['operator'] in ('eq', 'in'):
                value = _categorical(expression['field'], fact['value'], expression['operator'], expression['value'])
            else:
                value = None
            values.append(value)
        # Conflicting observations are possible source alternatives, not an
        # intersection of constraints that narrows away inconvenient readings.
        return values[0] if all(v is values[0] for v in values) else None
    if 'not' in expression:
        value = evaluate(expression['not'], observations)
        return None if value is None else not value
    conjunction = 'all' in expression
    values = [evaluate(x, observations) for x in expression['all' if conjunction else 'any']]
    decisive = False if conjunction else True
    if any(v is decisive for v in values):
        return decisive
    return None if any(v is None for v in values) else not decisive


def _categorical(field, value, operator, threshold):
    if field in {'material', 'body_material'}:
        from .catalog_condition_specs import MATERIAL_DOMAINS
        observed = MATERIAL_DOMAINS.get(value, {value})
        permitted = [threshold] if operator == 'eq' else threshold
        allowed = set().union(*(MATERIAL_DOMAINS.get(v, {v}) for v in permitted))
        if observed <= allowed:
            return True
        if observed.isdisjoint(allowed):
            return False
        return None
    return value == threshold if operator == 'eq' else value in threshold


def special_condition(note, *, record=None, product_name=None):
    program = compile_note(note)
    if program is None:
        return None
    from .catalog_condition_facts import source_facts
    facts = source_facts(record, product_name, program['required_fields']) if record and product_name else {
        'observations': [], 'scope_issues': [], 'whole_purchase_certified': False}
    value = evaluate(program['expression'], facts['observations'])
    if program['required_fields'] and facts['scope_issues']:
        value = None
    return {'kind': 'supplied_catalog_predicate_v1', **program,
        'status': ('no_stated_condition' if not program['required_fields'] else
                   'unknown' if value is None else 'met' if value else 'not_met'),
        'source_facts': facts,
        'missing_fields': sorted(set(program['required_fields']) - {x['field'] for x in facts['observations']
            if not x.get('issue') and x['scope'] == 'entire_named_purchase'}),
        'purchase_identity_certified': False,
        'basis': 'complete_catalog_note_and_explicit_product_scoped_source_fields'}
