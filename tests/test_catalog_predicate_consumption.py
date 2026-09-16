"""Designation conditions require facts of the named purchase, not keywords."""
import copy

import pytest

from submission.pps.knowledge import Knowledge
from submission.pps.qualification import catalog_condition, infer
from tests.test_service_identity import DATA


SERVER = 'x86 서버 CPU 1개 전체, CPU 2개 중 Clock(기본주파수) 3.2GHz 이하 제품에 한함'
DRONE = '1. 고정익, 군사용, 수소드론 제외 2. 자체중량 25㎏ 이하 또는 운용상승고도 150m 이하의 무인비행장치에 한함'


def purchase(name, code, fields, *, prefix='전체 납품', extra=''):
    lines = '\n'.join(f'{prefix} {name}의 {k}: {v}' for k, v in fields)
    return {'id': 'synthetic-catalog-predicate',
        'meta': {'업무구분': '물품(내자)', '적용계약법': '국가계약법',
                 '입찰추정가격': 150_000_000, '세부품명번호목록': f'{name}[{code}]'},
        'docs': [{'doc_id': 'notice', 'type': '공고문',
                  'text': f'1. 구매내역\n품명: {name}\n{lines}\n{extra}\n2. 입찰참가자격\n일반 업체\n3. 계약조건'}],
        'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


def product(rec):
    original = copy.deepcopy(rec)
    knowledge = Knowledge(DATA)
    knowledge.detailed_product_facts(rec)
    _, facts = infer(rec, {}, knowledge._product_facts)
    assert rec == original
    return facts['product']


def server(fields, **kwargs):
    return product(purchase('컴퓨터서버', '4321150102', fields, **kwargs))


@pytest.mark.parametrize('note', ['PDF물탱크 포함', 'VR(Voltage Regulator) 포함',
    "1. 해외전시회 포함 2. '전시산업발전법' 제2조 2호, 3호에 따른 전시회 및 전시회 부대행사용 전시부스 설치 및 디자인서비스 등을 포함"])
def test_pure_scope_extensions_do_not_add_a_restrictive_condition(note):
    result = catalog_condition(note, None, None)
    assert result['status'] == 'no_stated_condition'
    assert result['scope_extensions']
    assert result['purchase_identity_certified'] is False


@pytest.mark.parametrize('fields,expected', [
    ([('CPU 아키텍처', 'x86'), ('CPU 개수', '1개')], 'competition'),
    ([('CPU 아키텍처', 'x86'), ('CPU 개수', '2개'), ('CPU 기본주파수', '3200MHz')], 'competition'),
    ([('CPU 아키텍처', 'x86'), ('CPU 개수', '2개'), ('CPU 기본주파수', '3.201GHz')], 'general'),
    ([('CPU 아키텍처', 'ARM')], 'general'),
    ([('CPU 아키텍처', 'x86'), ('CPU 개수', '3개')], 'general'),
])
def test_server_condition_is_computed_from_original_whole_purchase_fields(fields, expected):
    result = server(fields)
    assert result['status'] == expected


@pytest.mark.parametrize('fields', [
    [('CPU 모델', 'Intel Xeon'), ('CPU 개수', '1개')],
    [('CPU 아키텍처', 'x86'), ('CPU 코어 수', '1개')],
    [('CPU 아키텍처', 'x86'), ('CPU 개수', '2개'), ('CPU 최대주파수', '3.2GHz')],
    [('CPU 아키텍처', 'x86'), ('CPU 개수', '2개'), ('CPU 기본주파수', '3.2GHz 이상')],
    [('CPU 아키텍처', 'x86'), ('CPU 개수', '1개'), ('CPU 개수', '미정')],
    [('CPU 아키텍처', 'x86'), ('CPU 아키텍처', 'ARM'), ('CPU 개수', '1개')],
])
def test_missing_ambiguous_or_different_feature_is_not_a_condition_fact(fields):
    assert server(fields)['status'] == 'unknown'


@pytest.mark.parametrize('prefix', ['일부 납품', '기존', '예시: 전체 납품', '전체 납품 예정인'])
def test_component_or_hypothetical_property_does_not_prove_whole_purchase(prefix):
    assert server([('CPU 아키텍처', 'ARM')], prefix=prefix)['status'] == 'unknown'


@pytest.mark.parametrize('extra', ['참고 예시 규격이다.', '동등 이상의 다른 제품도 납품 가능하다.',
    '다만 다른 CPU 아키텍처를 선택할 수 있다.'])
def test_scope_exceptions_are_not_discarded(extra):
    assert server([('CPU 아키텍처', 'ARM')], extra=extra)['status'] == 'unknown'


def test_single_cpu_branch_does_not_need_a_frequency_fact():
    result = server([('CPU 아키텍처', 'x86'), ('CPU 개수', '1개')])
    condition = result['products'][0]['condition']
    assert condition['status'] == 'met'
    assert condition['missing_fields'] == ['cpu_base_ghz']
    assert condition['purchase_identity_certified'] is False
    assert condition['source_facts']['observations']


@pytest.mark.parametrize('weight,altitude,expected', [
    ('25000g', None, 'competition'), ('25.001kg', '150m', 'competition'),
    ('25.001kg', '150.001m', 'general'), (None, '150m', 'competition'),
    ('25.001kg', None, 'unknown'),
])
def test_drone_or_requires_only_one_true_branch_but_exclusions_stay_separate(weight, altitude, expected):
    fields = [('고정익', '아니오'), ('군사용', '아니오'), ('수소드론', '아니오')]
    if weight is not None:
        fields.append(('자체중량', weight))
    if altitude is not None:
        fields.append(('운용상승고도', altitude))
    assert product(purchase('드론', '2513189901', fields))['status'] == expected


def test_missing_exclusions_are_not_negative_facts():
    assert product(purchase('드론', '2513189901', [('자체중량', '1kg')]))['status'] == 'unknown'


def test_one_explicit_excluded_drone_type_is_decisive():
    assert product(purchase('드론', '2513189901', [('군사용', '예')]))['status'] == 'general'


def test_mixed_purchase_guard_survives_a_successful_condition():
    rec = purchase('컴퓨터서버', '4321150102', [('CPU 아키텍처', 'ARM')])
    rec['docs'][0]['text'] = rec['docs'][0]['text'].replace('품명: 컴퓨터서버', '품명: 컴퓨터서버 외 3종')
    result = product(rec)
    assert result['status'] == 'unknown'
    assert 'explicit_multiple_items_not_all_identified' in result['uncertainty']


def test_unknown_additional_catalog_clause_is_not_silently_dropped():
    assert catalog_condition(SERVER+' 추가 자격을 충족해야 함', None, None)['status'] == 'not_evaluated'


def evaluate_note(note, fields, name='시험품목'):
    rec = purchase(name, '1234567890', fields)
    return catalog_condition(note, None, None, record=rec, product_name=name), rec


@pytest.mark.parametrize('value,expected', [('999.999 GT', 'met'), ('1000GT', 'not_met'),
    ('1000GT 미만', 'met'), ('1000GT 이하', 'unknown'), ('1000GT 이상', 'not_met'),
    ('1 000GT', 'unknown'), ('1000kg', 'unknown'), ('0GT 미만', 'unknown')])
def test_gross_tonnage_bound_and_dimension(value, expected):
    result, _ = evaluate_note('총톤수(Gross-tonnage) 1,000톤 미만에 한함', [('총톤수', value)])
    assert result['status'] == expected


@pytest.mark.parametrize('value', ['1 0개', '1개..', '1. 0개', '1개 또는 2개', '1.5개', '코어 1개'])
def test_malformed_or_different_count_is_not_repaired(value):
    assert server([('CPU 아키텍처', 'x86'), ('CPU 개수', value)])['status'] == 'unknown'


@pytest.mark.parametrize('fields,expected', [
    ([('실용량', '100TB'), ('캐시메모리', '64GB')], 'met'),
    ([('물리적용량', '200TB'), ('캐시메모리', '64GB')], 'met'),
    ([('실용량', '100TB'), ('캐시메모리', '65GB')], 'not_met'),
    ([('실용량', '101TB'), ('캐시메모리', '64GB')], 'unknown'),
    ([('실용량', '101TB'), ('물리적용량', '201TB'), ('캐시메모리', '64GB')], 'not_met'),
    ([('실용량', '100TiB'), ('캐시메모리', '64GB')], 'unknown'),
])
def test_disk_array_does_not_conflate_usable_physical_or_binary_units(fields, expected):
    note = '실용량(Usable) 100TB 이하이면서 캐시메모리 64GB 이하 제품 또는 물리적용량(Physical) 200TB 이하이면서 캐시메모리 64GB 이하 제품에 한함'
    assert evaluate_note(note, fields)[0]['status'] == expected


@pytest.mark.parametrize('fields,expected', [
    ([('납품 광역지역', '제주특별자치도')], 'met'),
    ([('납품 광역지역', '경기도')], 'unknown'),
    ([('납품 광역지역', '경기도'), ('연간 예측량 대비 예외 비율', '20%')], 'unknown'),
    ([('납품 광역지역', '경기도'), ('연간 예측량 대비 예외 비율', '20%'), ('연간 예측량 예외 적용', '예')], 'not_met'),
    ([('납품 광역지역', '경기도'), ('연간 예측량 대비 예외 비율', '20.001%')], 'met'),
    ([('입찰참가 지역', '제주특별자치도')], 'unknown'),
    ([('납품 광역지역', '[지역:r1|단위=기초|광역=미상]')], 'unknown'),
])
def test_regional_optional_quota_needs_delivery_scope_and_actual_exception(fields, expected):
    note = '서울, 경기, 인천지역 연간 예측량의 20% 이내에서 예외 가능'
    assert evaluate_note(note, fields)[0]['status'] == expected


@pytest.mark.parametrize('value,expected', [('알루미늄', 'met'), ('목재', 'not_met'),
    ('금속 또는 목재', 'unknown'), ('금속을 포함한 복합재질', 'unknown')])
def test_whole_material_is_distinct_from_one_component(value, expected):
    assert evaluate_note('금속 소재의 제품에 한함', [('재질', value)])[0]['status'] == expected
    assert evaluate_note('금속 소재의 제품에 한함', [('본체 재질', value)])[0]['status'] == 'unknown'


def test_cross_document_exception_remains_in_the_condition_context():
    rec = purchase('컴퓨터서버', '4321150102', [('CPU 아키텍처', 'ARM')])
    rec['docs'].append({'doc_id': 'attachment', 'type': '규격서', 'text': '동등 이상의 타 제품 납품 가능'})
    assert product(rec)['status'] == 'unknown'


def test_other_product_cannot_supply_a_missing_attribute():
    rec = purchase('컴퓨터서버', '4321150102', [('CPU 개수', '1개')])
    rec['docs'][0]['text'] += '\n전체 납품 태블릿의 CPU 아키텍처: x86'
    assert product(rec)['status'] == 'unknown'


def test_unbound_literal_processor_architecture_is_diagnostic_only():
    rec = purchase('컴퓨터서버', '4321150102', [])
    raw = 'Processor: 20-core Arm (10 Cortex-X925 + 10 Cortex-A725)'
    rec['docs'].append({'doc_id': 'attachment', 'type': '규격서', 'text': raw})
    result = product(rec)
    assert result['status'] == 'unknown'
    fact = result['products'][0]['condition']['source_facts']['observations'][0]
    assert fact['value'] == 'arm'
    assert fact['scope'] == 'unbound_property_mention'
    assert fact['evidence']['text'] == raw


def test_source_coordinates_and_order_do_not_change_predicate_truth():
    fields = [('CPU 아키텍처', 'x86'), ('CPU 개수', '2개'), ('CPU 기본주파수', '3.2GHz')]
    first = purchase('컴퓨터서버', '4321150102', fields)
    second = purchase('컴퓨터서버', '4321150102', list(reversed(fields)))
    for rec in (first, second):
        rec['docs'][0]['text'] = '원문 접두\n' + rec['docs'][0]['text']
        result = product(rec)
        assert result['status'] == 'competition'
        for fact in result['products'][0]['condition']['source_facts']['observations']:
            e = fact['evidence']
            assert rec['docs'][e['doc_index']]['text'][e['start']:e['end']] == e['text']


def test_recognized_but_missing_conditions_still_require_review():
    rec = purchase('컴퓨터서버', '4321150102', [])
    facts = Knowledge(DATA).detailed_product_facts(rec)
    assert facts['catalog']['4321150102']['condition']['status'] == 'unknown'
    assert facts['uncertainty']['non_numeric_catalog_notes_require_review']


@pytest.mark.parametrize('note', ['총톤수(Gross-tonnage) 1 000톤 미만에 한함',
    SERVER+' 3. 다른 미확인 조건', '1. 금속 소재의 제품에 한함 3. 산책로 설치 포함',
    '농업용 필름(PO필름, 초산비닐 함량 13% 이내의 EVA필름)포함 추가 제한 적용'])
def test_unhandled_condition_syntax_is_not_partially_approved(note):
    assert catalog_condition(note, None, None)['status'] == 'not_evaluated'
