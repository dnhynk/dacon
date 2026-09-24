"""Optional model identity cannot supply prices, conditions or invented source."""
import copy
import dataclasses
import json
import re
from pathlib import Path

import pytest

from submission.b4_entry import B4Pipeline, parse_error
from submission.pps.catalog_scope import ITEMS, review, service_catalog, validate
from submission.pps.knowledge import Knowledge
from submission.pps.retrieval import Span
from tests.test_comparison import record


DATA = Path(__file__).resolve().parents[1]/'data_open/data'


def setup(title='자문 및 운영 분석', price=300_000_000):
    rec = record('용역명: '+title+'\n1. 입찰참가자격\n가. 중소기업 확인서를 소지한 업체이어야 한다.\n2. 계약조건',
                 업무구분='일반용역', 입찰추정가격=price, 배정예산금액=price*11//10)
    rec['docs'][0]['doc_id'] = 'D0'
    k = Knowledge(DATA)
    text = rec['docs'][0]['text']
    end = text.index('\n')
    spans = [Span(0, '공고문', 0, end, text[:end])]
    packet = {'spans': spans, 'catalog_scope': {'catalog': service_catalog(k.products),
        'all_supplied_service_rows_present': True}, 'items': list(ITEMS), 'family': 'Q',
        'generation': {'response_format': 'catalog_scope'}}
    obj = {'purchase_kind': 'service', 'whole_task_units': [1], 'task_summary': title,
        'catalog_relation': 'outside_all_listed_service_categories', 'relationships': [],
        'unresolved_scope': False}
    return rec, packet, obj, k


def response(obj):
    return {'finish_reason': 'stop', 'text': json.dumps(obj, ensure_ascii=False)}


def test_full_service_list_and_original_task_join_to_independent_qualification():
    rec, packet, obj, k = setup()
    before = copy.deepcopy(rec)
    result, log = review(rec, response(obj), packet, k)
    assert result['v14'] == 1 and result['e14'] in rec['docs'][0]['text']
    assert log['model_fact_is_fallible'] and log['source_scope_promoted']
    assert rec == before


@pytest.mark.parametrize('edit,gate', [
    ({'purchase_kind': 'mixed'}, 'model_scope_unresolved_or_mixed'),
    ({'unresolved_scope': True}, 'model_scope_unresolved_or_mixed'),
    ({'whole_task_units': []}, 'no_original_whole_task_anchor'),
    ({'catalog_relation': 'unknown'}, 'whole_category_not_resolved'),
    ({'relationships': [{'code': '8014190201', 'role': 'whole', 'source_units': [1]}]},
        'outside_claim_contradicts_catalog_relationship'),
    ({'relationships': [{'code': '9999999999', 'role': 'certificate_only', 'source_units': [1]}]},
        'unrecognized_or_conflicting_catalog_links'),
])
def test_semantic_unknowns_do_not_become_format_retries_or_guessed_labels(edit, gate):
    rec, packet, obj, k = setup()
    obj.update(edit)
    assert validate(response(obj)['text'], packet['spans']) == obj
    result, log = review(rec, response(obj), packet, k)
    assert result is None and log['gate'] == gate


def test_truncated_catalog_cannot_support_an_outside_all_claim():
    rec, packet, obj, k = setup()
    packet['catalog_scope']['catalog'].pop()
    result, log = review(rec, response(obj), packet, k)
    assert result is None and log['gate'] == 'complete_service_catalog_not_shown'


def test_model_cannot_promote_a_festival_over_its_supplied_price_ceiling():
    rec, packet, obj, k = setup('국제행사 운영', 350_000_000)
    obj.update(catalog_relation='listed_category', relationships=[
        {'code': '9015189001', 'role': 'whole', 'source_units': [1]}])
    result, log = review(rec, response(obj), packet, k)
    assert log['product']['status'] == 'general'
    assert log['product']['products'][0]['condition']['status'] == 'not_met'
    assert result['v10'] == 0 and result['v14'] == 1


def test_employee_commuter_bus_is_not_promoted_to_student_transport_service():
    rec, packet, obj, k = setup('직원 통근버스 운영 위탁 용역', 83_454_545)
    obj.update(catalog_relation='listed_category', relationships=[
        {'code': '7811189902', 'role': 'whole', 'source_units': [1]}])
    result, log = review(rec, response(obj), packet, k)
    assert result['v10'] == 0
    assert log['product']['status'] == 'general'
    assert log['source_rejected_whole_catalog_links'][0]['reason'] \
        == 'employee_commuting_is_not_student_transport'
    assert log['catalog_relation_source_correction']['basis'] \
        == 'explicit_disjoint_whole_task_semantics'


@pytest.mark.parametrize('title', [
    '학생 통학버스 임차 및 운행 용역',
    '직원 통근 및 학생 통학버스 통합 운행 용역',
])
def test_student_transport_cue_preserves_the_listed_transport_relation(title):
    rec, packet, obj, k = setup(title, 83_454_545)
    obj.update(catalog_relation='listed_category', relationships=[
        {'code': '7811189902', 'role': 'whole', 'source_units': [1]}])
    result, log = review(rec, response(obj), packet, k)
    assert log['product']['status'] == 'competition'
    assert 'source_rejected_whole_catalog_links' not in log
    assert result['v10'] == 1


def test_integrated_artwork_output_repairs_mixed_kind_and_component_only_unknown():
    rec, packet, obj, k = setup(
        '미술작품 제안·제작·설치 및 심의 대행 일체 용역', 259_211_818)
    obj.update(
        purchase_kind='mixed', catalog_relation='unknown', unresolved_scope=True,
        relationships=[
            {'code': '8214150201', 'role': 'component', 'source_units': [1]}])
    result, log = review(rec, response(obj), packet, k)
    assert result['v10'] == 0 and result['v11'] == 0
    assert log['product']['status'] == 'general'
    assert log['purchase_kind_source_correction']['basis'] \
        == 'complete_whole_service_field_without_independent_goods_structure'
    assert log['catalog_relation_source_correction']['basis'] \
        == 'complete_integrated_artwork_task_compared_with_full_service_catalog'
    assert log['source_rejected_component_catalog_links'][0]['code'] == '8214150201'
    assert log['product']['mechanism'] \
        == 'source_verified_integrated_service_full_catalog_exclusion'


@pytest.mark.parametrize('title', [
    '미술작품 제안·제작·설치 용역',
    '미술작품 제안·제작·설치 및 심의 대행 용역 / 물품 부문 별도 계약',
])
def test_artwork_scope_is_not_repaired_without_all_source_guards(title):
    rec, packet, obj, k = setup(title, 259_211_818)
    obj.update(
        purchase_kind='mixed', catalog_relation='unknown', unresolved_scope=True,
        relationships=[
            {'code': '8214150201', 'role': 'component', 'source_units': [1]}])
    result, log = review(rec, response(obj), packet, k)
    assert result is None
    assert log['gate'] == 'model_scope_unresolved_or_mixed'


def test_future_artwork_catalog_category_disables_closed_catalog_resolution():
    from submission.pps.catalog_scope import source_outside_service_catalog
    rec, packet, obj, k = setup(
        '미술작품 제안·제작·설치 및 심의 대행 일체 용역', 259_211_818)
    obj.update(
        purchase_kind='mixed', catalog_relation='unknown', unresolved_scope=True,
        relationships=[])
    from submission.pps.catalog_scope import whole_task_witnesses
    witnesses = whole_task_witnesses(rec, packet['spans'], [1])
    catalog = service_catalog(k.products) + [{
        'code': '9999999999', 'name': '미술작품제작설치서비스',
        'parent': '예술서비스', 'condition': ''}]
    correction = {'from': 'mixed', 'to': 'service'}
    assert source_outside_service_catalog(
        rec, obj, packet['spans'], [1], witnesses, catalog, correction) is None


def test_component_heading_cannot_be_declared_whole_when_model_read_a_wider_task():
    text = ('과 업 명 : 지역관광 브랜딩 전략 수립 용역\n'
            '1) 조사·분석 및 운영 전략 수립\n'
            '2) 브랜드 디자인 개발\n'
            '- 브랜드 기본·응용시스템 개발\n'
            '3) 홍보영상 및 시범상품 제작\n'
            '1. 입찰참가자격\n가. 중소기업 확인서를 소지한 업체\n2. 계약조건')
    rec = record(text, 업무구분='일반용역', 입찰추정가격=100_000_000,
                 배정예산금액=110_000_000)
    rec['docs'][0]['doc_id'] = 'D0'
    k = Knowledge(DATA)
    lines = list(re.finditer(r'[^\n]+(?:\n|$)', text))
    spans = [Span(0, '공고문', match.start(), match.end(), match[0]) for match in lines[:5]]
    packet = {'spans': spans, 'catalog_scope': {'catalog': service_catalog(k.products),
        'all_supplied_service_rows_present': True}, 'items': list(ITEMS), 'family': 'Q',
        'generation': {'response_format': 'catalog_scope'}}
    obj = {'purchase_kind': 'service', 'whole_task_units': [1, 2, 3, 4, 5],
        'task_summary': '조사·분석, 전략, 디자인, 영상 및 상품 제작을 수행한다.',
        'catalog_relation': 'listed_category', 'relationships': [
            {'code': '8214150201', 'role': 'whole', 'source_units': [3, 4]}],
        'unresolved_scope': False}
    result, log = review(rec, response(obj), packet, k)
    assert result is None
    assert log['gate'] == 'whole_catalog_link_covers_only_part_of_declared_task'


def test_bare_space_creation_title_cannot_establish_whole_design_service():
    rec, packet, obj, k = setup('굿즈숍 및 관객 라운지 공간 조성')
    obj.update(catalog_relation='listed_category', relationships=[
        {'code': '8214150201', 'role': 'whole', 'source_units': [1]}])
    result, log = review(rec, response(obj), packet, k)
    assert result is None
    assert log['gate'] == 'title_only_catalog_link_without_source_category_cue'
    assert log['uncued_title_only_catalog_links'][0]['support_units'] == [1]


def test_bidder_capability_cannot_supply_missing_category_cue_for_title():
    rec, packet, obj, k = setup('굿즈숍 및 관객 라운지 공간 조성')
    text = rec['docs'][0]['text']
    capability = ('가. 충분한 장비, 인력, 기술을 보유하고 있어 기획, 디자인, '
                  '제작, 시공, 관리 등 종합적인 업무가 가능한 업체')
    start = text.index('가. 중소기업 확인서를')
    end = start + len('가. 중소기업 확인서를 소지한 업체이어야 한다.')
    rec['docs'][0]['text'] = text[:start] + capability + text[end:]
    packet['spans'].append(Span(0, '공고문', start, start + len(capability), capability))
    obj.update(
        whole_task_units=[1, 2],
        task_summary='공간 조성과 기획, 디자인, 제작, 시공, 관리 업무를 수행한다.',
        catalog_relation='listed_category',
        relationships=[
            {'code': '8214150201', 'role': 'whole', 'source_units': [1, 2]}])
    result, log = review(rec, response(obj), packet, k)
    assert result is None
    assert log['gate'] == 'title_only_catalog_link_without_source_category_cue'
    rejected = log['uncued_title_only_catalog_links'][0]
    assert rejected['task_witness_units'] == [1]
    assert rejected['ignored_non_task_support_units'] == [2]


def test_outside_integrated_service_can_preserve_a_supported_design_component():
    rec, packet, obj, k = setup('운영 분석 및 브랜드 디자인 지원')
    obj['relationships'] = [
        {'code': '8214150201', 'role': 'component', 'source_units': [1]}]
    result, log = review(rec, response(obj), packet, k)
    assert result is not None and log['source_scope_promoted']
    assert log['product']['status'] == 'general'
    assert result['v14'] == 1


def test_outside_component_relation_needs_its_category_cue_in_source():
    rec, packet, obj, k = setup('자문 및 운영 분석')
    obj['relationships'] = [
        {'code': '8214150201', 'role': 'component', 'source_units': [1]}]
    result, log = review(rec, response(obj), packet, k)
    assert result is None
    assert log['gate'] == 'component_catalog_link_without_source_category_cue'


def test_listed_whole_relation_can_coexist_with_a_distinct_component_relation():
    rec, packet, obj, k = setup('브랜드 디자인 개발 및 홍보영상 제작')
    obj.update(catalog_relation='listed_category', relationships=[
        {'code': '8214150201', 'role': 'whole', 'source_units': [1]},
        {'code': '8213160301', 'role': 'component', 'source_units': [1]},
    ])
    result, log = review(rec, response(obj), packet, k)
    assert result is not None and log['source_scope_promoted']
    assert [row['code'] for row in log['product']['products']] == ['8214150201']


def test_certificate_only_catalog_item_does_not_turn_a_clear_outside_task_into_unknown():
    rec, packet, obj, k = setup('탄소 감축사업 운영 지원', 80_000_000)
    text = rec['docs'][0]['text']
    certificate = ('직접생산확인증명서(세부품명번호 8111219901, '
                   '세부품명 인터넷지원개발서비스)를 소지한 업체이어야 한다.')
    insert = text.index('2. 계약조건')
    rec['docs'][0]['text'] = text[:insert] + certificate + '\n' + text[insert:]
    packet['spans'].append(Span(0, '공고문', insert,
        insert + len(certificate), certificate))
    obj['relationships'] = [
        {'code': '8111219901', 'role': 'certificate_only', 'source_units': [2]}]
    result, log = review(rec, response(obj), packet, k)
    assert result is not None and log['source_scope_promoted']
    assert log['product']['status'] == 'general'
    assert result['v12'] == 1


def test_reduction_program_scope_rejects_uncertain_software_certificate_identity():
    rec, packet, obj, k = setup(
        '해운부문 외부사업 운영 지원 및 감축사업 활성화 용역', 80_000_000)
    text = rec['docs'][0]['text']
    certificate = ('직접생산확인증명서(세부품명번호 8111219901, '
                   '세부품명 인터넷지원개발서비스)를 소지한 업체이어야 한다.')
    insert = text.index('2. 계약조건')
    rec['docs'][0]['text'] = text[:insert] + certificate + '\n' + text[insert:]
    packet['spans'].append(Span(0, '공고문', insert,
        insert + len(certificate), certificate))
    obj.update(
        catalog_relation='unknown', unresolved_scope=True,
        relationships=[
            {'code': '8111219901', 'role': 'uncertain', 'source_units': [2]}])
    result, log = review(rec, response(obj), packet, k)
    assert result['v12'] == 1 and result['v13'] == 0
    assert log['product']['status'] == 'general'
    assert log['catalog_relation_source_correction']['basis'] \
        == 'complete_reduction_program_support_task_compared_with_full_service_catalog'
    assert log['source_rejected_catalog_links'][0]['code'] == '8111219901'
    assert log['product']['mechanism'] \
        == 'source_verified_integrated_service_full_catalog_exclusion'


@pytest.mark.parametrize('title', [
    '시스템 외부사업 운영 지원 및 감축사업 활성화 용역',
    '해운부문 외부사업 운영 지원 용역',
])
def test_reduction_program_resolution_requires_every_affirmative_source_guard(title):
    rec, packet, obj, k = setup(title, 80_000_000)
    text = rec['docs'][0]['text']
    certificate = ('직접생산확인증명서(세부품명번호 8111219901, '
                   '세부품명 인터넷지원개발서비스)를 소지한 업체이어야 한다.')
    insert = text.index('2. 계약조건')
    rec['docs'][0]['text'] = text[:insert] + certificate + '\n' + text[insert:]
    packet['spans'].append(Span(0, '공고문', insert,
        insert + len(certificate), certificate))
    obj.update(
        catalog_relation='unknown', unresolved_scope=True,
        relationships=[
            {'code': '8111219901', 'role': 'uncertain', 'source_units': [2]}])
    result, log = review(rec, response(obj), packet, k)
    assert result is None and log['gate'] == 'uncertain_identity_link'


def test_future_reduction_program_catalog_row_disables_closed_catalog_resolution():
    from submission.pps.catalog_scope import (
        source_outside_service_catalog, whole_task_witnesses)
    rec, packet, obj, k = setup(
        '해운부문 외부사업 운영 지원 및 감축사업 활성화 용역', 80_000_000)
    obj.update(catalog_relation='unknown', unresolved_scope=True, relationships=[])
    witnesses = whole_task_witnesses(rec, packet['spans'], [1])
    catalog = service_catalog(k.products) + [{
        'code': '9999999999', 'name': '탄소감축사업운영지원서비스',
        'parent': '기후대응서비스', 'condition': ''}]
    assert source_outside_service_catalog(
        rec, obj, packet['spans'], [1], witnesses, catalog, None) is None


def test_fallible_outside_role_cannot_erase_a_supported_candidate_family():
    from submission.pps.catalog_scope import unresolved_candidate_family_outside_claim
    original = {'mechanism': 'event_service_family_with_unresolved_detail',
                'products': [{'code': '8014199001'}]}
    obj = {'catalog_relation': 'outside_all_listed_service_categories'}
    assert unresolved_candidate_family_outside_claim(original, obj, None)
    assert not unresolved_candidate_family_outside_claim(
        original, obj, {'basis': 'source_disjoint'})
    obj['catalog_relation'] = 'listed_category'
    assert not unresolved_candidate_family_outside_claim(original, obj, None)


@pytest.mark.parametrize('title', [
    '브랜드 디자인 개발 용역',
    '시각 홍보물 편집 용역',
])
def test_title_only_design_relation_needs_an_original_category_cue(title):
    rec, packet, obj, k = setup(title)
    obj.update(catalog_relation='listed_category', relationships=[
        {'code': '8214150201', 'role': 'whole', 'source_units': [1]}])
    result, log = review(rec, response(obj), packet, k)
    assert result is not None and log['source_scope_promoted']


def test_named_whole_task_field_can_anchor_catalog_relation_with_supporting_detail():
    rec, packet, obj, k = setup('브랜드 정체성 개발')
    text = rec['docs'][0]['text']
    first_end = text.index('\n')
    detail = '브랜드 기본·응용 디자인을 개발한다.'
    rec['docs'][0]['text'] = text[:first_end] + '\n' + detail + text[first_end:]
    packet['spans'] = [
        Span(0, '공고문', 0, first_end, rec['docs'][0]['text'][:first_end]),
        Span(0, '공고문', first_end + 1, first_end + 1 + len(detail), detail),
    ]
    obj.update(whole_task_units=[1, 2], catalog_relation='listed_category', relationships=[
        {'code': '8214150201', 'role': 'whole', 'source_units': [1]}])
    result, log = review(rec, response(obj), packet, k)
    assert result is not None and log['source_scope_promoted']


def test_forged_or_out_of_range_source_selection_is_rejected():
    rec, packet, obj, k = setup()
    packet['spans'][0] = dataclasses.replace(packet['spans'][0], text='invented')
    with pytest.raises(ValueError, match='original source'):
        review(rec, response(obj), packet, k)
    obj['whole_task_units'] = [2]
    assert parse_error(packet, response(obj)) is not None


def test_real_consumer_route_is_separate_from_default_four_call_plan():
    rec, packet, obj, k = setup()
    pipeline = B4Pipeline(DATA, None)
    assert parse_error(packet, response(obj)) is None
    row, log = pipeline.consume(rec, packet, response(obj))
    assert row['v14'] == 1 and log['source_scope_promoted']
    packet['family'] = 'A'
    with pytest.raises(ValueError, match='Q family'):
        pipeline.consume(rec, packet, response(obj))


@pytest.mark.parametrize('field', ['whole_task_units', 'relationship'])
def test_decimal_source_ids_are_format_failures_before_consumer_indexing(field):
    rec, packet, obj, k = setup()
    if field == 'whole_task_units':
        obj[field] = [1.0]
    else:
        obj['relationships'] = [{'code': '8014190201', 'role': 'certificate_only',
                                 'source_units': [1.0]}]
    assert parse_error(packet, response(obj)) is not None


def test_wrapped_task_label_requires_its_actual_value_in_selected_units():
    from submission.pps.source_units import unitize
    rec, packet, obj, k = setup()
    text = rec['docs'][0]['text'].replace('용역명: 자문 및 운영 분석', '용역명:\n자문 및 운영 분석')
    rec['docs'][0]['text'] = text
    end = text.index('\n1.')
    packet['spans'] = unitize([Span(0, '공고문', 0, end, text[:end])])
    obj['whole_task_units'] = [1]
    result, log = review(rec, response(obj), packet, k)
    assert result is None and log['gate'] == 'no_original_whole_task_anchor'
    obj['whole_task_units'] = [2]
    result, log = review(rec, response(obj), packet, k)
    assert result['v14'] == 1
    assert log['task_reference_completion'][0]['added_units'] == [1]
    obj['whole_task_units'] = [1, 2]
    result, log = review(rec, response(obj), packet, k)
    assert result['v14'] == 1
    assert log['product']['identity_evidence'][0]['text'] == text[:end]


def test_long_task_cannot_be_resolved_from_a_truncated_first_unit():
    from submission.pps.source_units import unitize
    title = '문화 정책에 대한 자문과 운영 분석, ' * 13 + '예산집행 검토를 포함하는 용역'
    rec, packet, obj, k = setup(title)
    text = rec['docs'][0]['text']
    end = text.index('\n')
    packet['spans'] = unitize([Span(0, '공고문', 0, end, text[:end])])
    obj['task_summary'] = '문화 정책 자문과 운영 분석 및 예산집행 검토'
    assert len(packet['spans']) > 1
    result, log = review(rec, response(obj), packet, k)
    assert result is None and log['gate'] == 'no_original_whole_task_anchor'
    obj['whole_task_units'] = list(range(1, len(packet['spans']) + 1))
    result, log = review(rec, response(obj), packet, k)
    assert result['v14'] == 1
    assert log['product']['identity_evidence'][0]['text'] == text[:end]


def test_a_reference_word_inside_an_actual_task_is_not_a_missing_document():
    rec, packet, obj, k = setup('역사 참고자료 편집 및 운영 분석')
    result, log = review(rec, response(obj), packet, k)
    assert result['v14'] == 1 and log['source_scope_promoted']


def test_a_bare_task_document_pointer_is_not_an_observed_purchase_scope():
    rec, packet, obj, k = setup('제안요청서 참조')
    result, log = review(rec, response(obj), packet, k)
    assert result is None and log['gate'] == 'no_original_whole_task_anchor'


def test_whole_catalog_relation_needs_task_support_beyond_a_certificate():
    rec, packet, obj, k = setup()
    text = rec['docs'][0]['text']
    certificate = '직접생산확인증명서의 세부품명번호는 8014190201이다.'
    rec['docs'][0]['text'] = text + '\n' + certificate
    packet['spans'].append(Span(0, '공고문', len(text)+1,
        len(text)+1+len(certificate), certificate))
    obj.update(catalog_relation='listed_category', relationships=[
        {'code': '8014190201', 'role': 'whole', 'source_units': [2]}])
    assert parse_error(packet, response(obj)) is None
    row, log = review(rec, response(obj), packet, k)
    assert row is None and log['gate'] == 'whole_catalog_link_without_task_anchor'
    assert not log['source_scope_promoted']
    obj['relationships'][0]['source_units'] = [1, 2]
    row, log = review(rec, response(obj), packet, k)
    assert row is not None and log['source_scope_promoted']
