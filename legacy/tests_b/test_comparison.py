"""Semantic comparison controls; synthetic data, no notice IDs or gold labels."""
import copy
import dataclasses
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from submission.pps.comparison import amount_facts, compare, positive_decision, priority_ranges, prompt_packet, won_value
from submission.pps.knowledge import Knowledge
from submission.pps.prompts import Config, build_prompt, build_shared_prompts
from submission.pps.pipeline import _response_row
from submission.pps.retrieval import NoticeIndex, Span


def record(notice, attachment=None, **meta):
    defaults = {'배정예산금액': 55000000, '입찰추정가격': 50000000,
                '계약방법': '제한경쟁', '낙찰방법': '협상에의한계약', '적용계약법': '지방계약법'}
    defaults.update(meta)
    docs = [{'type': '공고문', 'text': notice}]
    if attachment is not None:
        docs.append({'type': '제안요청서', 'text': attachment})
    return {'id': 'synthetic', 'meta': defaults, 'docs': docs,
            'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


def comparison(rec, field='budget'):
    return next(c for c in compare(rec)['comparisons'] if c['field'] == field)


@pytest.mark.parametrize('text,expected', [
    ('55,000,000원', 55000000), ('5천5백만원', 55000000), ('2천3백만원', 23000000),
    ('1억 2천 3백만원', 123000000), ('2.3억원', 230000000), ('55000천원', 55000000),
    ('0.55억원', 55000000), ('2조 3억원', 2000300000000),
])
def test_exact_place_value(text, expected):
    assert won_value(text) == Decimal(expected)


@pytest.mark.parametrize('text', ['NaN원', '-500원', '2억3억원', '55,00원', '2천3천원', '2.3%', '3만원/년'])
def test_reject_malformed_money(text):
    assert won_value(text) is None


def test_same_amount_compound_unit_and_numeric_metadata_string():
    rec = record('사 업 예 산: 5천5백만원 (부가세 포함)', 배정예산금액='55,000,000')
    assert comparison(rec)['status'] == 'same'
    assert positive_decision(rec, compare(rec)) is None


def test_typed_price_bases_are_not_interchangeable():
    rec = record('추정가격: 50,000,000원\n기초금액: 49,000,000원 (부가세 포함)')
    assert comparison(rec)['status'] == 'no_comparable_document_value'
    assert comparison(rec, 'estimated_price')['status'] == 'same'
    assert positive_decision(rec, compare(rec)) is None


def test_unit_price_estimate_is_not_the_registered_whole_contract_estimate():
    text=('기초금액: 2,046,000원(추정가격 1,860,000원, 부가가치세 186,000원)\n'
          '용역예정금액: 94,035,000원(부가세 포함)\n'
          '※ 단가계약 입찰이므로 기초금액을 기준으로 투찰하여야 합니다.')
    rec=record(text,배정예산금액=94_035_000,입찰추정가격=85_486_364)
    packet=compare(rec)
    assert comparison(rec)['status']=='no_comparable_document_value'
    assert comparison(rec,'estimated_price')['status']=='no_comparable_document_value'
    price=comparison(rec,'estimated_price')
    assert price['noncomparable_unit_price_fact_indices']==price['fact_indices']
    estimate=next(f for f in packet['facts'] if f['field']=='estimated_price')
    assert estimate['scope']=='whole'  # Other item families retain the original price fact.
    assert positive_decision(rec,packet) is None


def test_non_unit_price_estimate_remains_comparable():
    text='기초금액: 2,046,000원(추정가격 1,860,000원, 부가가치세 186,000원)'
    rec=record(text,입찰추정가격=85_486_364)
    assert comparison(rec,'estimated_price')['status']=='different'


def test_do_not_borrow_next_fields_vat():
    rec = record('사업예산: 40,000,000원\n추정가격: 50,000,000원\n기초금액: 44,000,000원 (부가세 포함)')
    assert amount_facts(rec)[0]['basis'] == 'unknown'
    assert comparison(rec)['status'] == 'basis_unresolved'


def test_do_not_borrow_previous_inline_fields_vat():
    rec = record('기초금액: 40,000,000원(부가세 포함) 사업예산: 50,000,000원')
    assert next(f for f in amount_facts(rec) if f['field'] == 'budget')['basis'] == 'unknown'


def test_unknown_and_excluded_vat_remain_unresolved():
    for suffix in ['', '(부가세 별도)', '(부가세 포함 또는 부가세 별도)']:
        rec = record('사업예산: 50,000,000원' + suffix)
        assert comparison(rec)['status'] in {'no_comparable_document_value', 'basis_unresolved'}


def test_narrative_reference_cannot_borrow_an_unrelated_amount():
    rec = record('사업예산은 발주 부서에서 별도로 정한다. 납품 물품 가격은 66,000,000원(부가세 포함)')
    assert comparison(rec)['status'] == 'extraction_unresolved'
    assert positive_decision(rec, compare(rec)) is None


def test_threshold_text_is_not_the_current_project_budget():
    rec = record('사업예산 1억원 미만 사업은 별도로 검토한다(부가세 포함).')
    assert comparison(rec)['status'] == 'no_comparable_document_value'
    assert positive_decision(rec, compare(rec)) is None


def test_annual_is_not_total_but_explicit_total_remains():
    rec = record('1차년도 사업예산: 20,000,000원(부가세 포함)\n총 사업예산: 55,000,000원(부가세 포함)')
    assert comparison(rec)['status'] == 'same'
    assert [f['scope'] for f in amount_facts(rec)] == ['partial', 'whole']


def test_table_columns_keep_their_own_units_and_bases():
    rec = record('| 사업예산(천원, 부가세 포함) | 추정가격(원) |\n| --- | --- |\n| 55,000 | 50,000,000 |')
    assert comparison(rec)['status'] == 'same'
    assert comparison(rec, 'estimated_price')['status'] == 'same'


def test_attachment_only_difference_is_preserved_for_model_not_forced():
    rec = record('사업 내용과 예산은 제안요청서를 참고한다.', '사업예산: 66,000,000원(부가세 포함)')
    packet = compare(rec)
    assert comparison(rec)['status'] == 'different'
    assert positive_decision(rec, packet) is None
    fact = packet['facts'][0]
    assert rec['docs'][fact['doc_index']]['text'][fact['start']:fact['end']] == '사업예산: 66,000,000원(부가세 포함)'


def test_whole_program_budget_is_not_component_tender_amount():
    rec = record('사업예산: 1억원(부가세 포함)\n입찰대상금액: 3천만원(부가세 포함)', 배정예산금액=30000000)
    assert comparison(rec)['status'] == 'same'
    assert {f['scope'] for f in amount_facts(rec)} == {'project_total', 'whole'}
    assert positive_decision(rec, compare(rec)) is None


def test_component_tender_real_mismatch_is_still_detected():
    rec = record('사업예산: 1억원(부가세 포함)\n입찰대상금액: 3천만원(부가세 포함)', 배정예산금액=40000000)
    assert comparison(rec)['status'] == 'different'
    assert '입찰대상금액' in positive_decision(rec, compare(rec))['evidence']


def test_conflicting_documents_do_not_get_overwritten_by_metadata():
    rec = record('사업예산: 55,000,000원(부가세 포함)', '사업예산: 66,000,000원(부가세 포함)')
    assert comparison(rec)['status'] == 'documents_conflict'
    assert positive_decision(rec, compare(rec)) is None
    assert {f['value'] for f in amount_facts(rec)} == {'55000000', '66000000'}


def test_unknown_metadata_and_one_won_difference():
    text = '배정예산금액: 55,000,001원'
    assert comparison(record(text))['status'] == 'rounding_unresolved'
    for value in [None, True, '미입력', 'NaN', 0]:
        assert comparison(record(text, 배정예산금액=value))['status'] == 'metadata_missing_or_unparsed'


def test_contract_and_award_method_are_different_semantic_fields():
    assert comparison(record('계약방법: 협상에 의한 계약'), 'competition_method')['status'] == 'no_comparable_document_value'
    assert comparison(record('계약방법: 제한경쟁(협상에 의한 계약)'), 'competition_method')['status'] == 'same'
    assert comparison(record('계약방법: 일반경쟁'), 'competition_method')['status'] == 'different'


def test_small_quotation_keeps_its_procedure():
    rec = record('입찰방법: 소액수의(총액), 제한경쟁(지역), 전자견적', 계약방법='수의계약')
    assert comparison(rec, 'competition_method')['status'] == 'same'


def test_region_flag_is_not_an_explicit_value_mismatch():
    rec = record('주된 영업소 소재지가 경기도에 있는 업체', 지역제한여부='N', 제한지역코드목록=None)
    assert comparison(rec, 'region')['status'] == 'metadata_missing_or_unparsed'
    assert positive_decision(rec, compare(rec)) is None


def test_prompt_keeps_registration_flags_separate_and_checks_every_axis():
    text = ('주된 영업소 소재지가 경기도에 있는 업체\n'
            '학술연구용역(업종코드: 1169)으로 등록한 자')
    rec = record(text, 지역제한여부='N', 제한지역코드목록=None,
                 업종제한여부='N', 면허업종제한목록=None)
    span = Span(0, '공고문', 0, len(text), text)
    displayed = prompt_packet(compare(rec), [span], rec=rec)
    fields = {item['field']: item for item in displayed['fields']}
    assert fields['제한지역코드목록']['registered_value'] is None
    assert fields['제한지역코드목록']['registered_flag'] == 'N'
    assert fields['제한지역코드목록']['flag_relation'] == 'source_restriction_vs_registered_N_candidate'
    assert fields['면허업종제한목록']['registered_value'] is None
    assert fields['면허업종제한목록']['registered_flag'] == 'N'
    assert fields['면허업종제한목록']['flag_relation'] == 'source_restriction_vs_registered_N_candidate'
    assert '네 축을 모두 확인' in displayed['instruction']


def test_null_registration_flag_is_not_an_n_flag_candidate():
    text = '주된 영업소 소재지가 경기도에 있는 업체'
    rec = record(text, 지역제한여부=None, 제한지역코드목록=None)
    displayed = prompt_packet(compare(rec), [Span(0, '공고문', 0, len(text), text)], rec=rec)
    region = next(item for item in displayed['fields'] if item['field'] == '제한지역코드목록')
    assert region['registered_flag'] is None
    assert region['flag_relation'] == 'none'


def test_district_is_not_province_equality():
    rec = record('주된 영업소 소재지가 경기도 [지역:r1|단위=기초|광역=경기도]에 있는 업체', 제한지역코드목록='경기도')
    assert comparison(rec, 'region')['status'] == 'hierarchy_unresolved'


def test_industry_alternative_cannot_be_flattened_to_mismatch():
    rec = record('식품판매업(업종코드: 5210) 또는 다른 업종으로 등록한 업체', 면허업종제한목록='식품판매업(5246)')
    assert comparison(rec, 'industry')['status'] == 'and_or_scope_unresolved'
    assert positive_decision(rec, compare(rec)) is None


def test_matching_field_is_never_global_normal():
    rec = record('배정예산금액: 55,000,000원\n계약방법: 일반경쟁')
    assert comparison(rec)['status'] == 'same'
    assert positive_decision(rec, compare(rec))['value'] == 1


def test_source_gap_prevents_prompt_claim_even_when_endpoints_shown():
    rec = record('배정예산금액: 66,000,000원')
    text = rec['docs'][0]['text']
    spans = [Span(0, '공고문', 0, 5, text[:5]), Span(0, '공고문', 11, len(text), text[11:])]
    packet = prompt_packet(compare(rec), spans, rec=rec)
    assert packet['fields'][0]['comparison'] == 'source_omitted'
    assert packet['fields'][0]['observed'][0]['S'] == []


def test_required_sources_remain_exact_under_long_distractor_and_attachment():
    rec = record(('무관한 안내입니다.\n' * 300) + '배정예산금액: 55,000,000원',
                 '배정예산금액: 66,000,000원')
    packet = compare(rec)
    spans = NoticeIndex(rec, overlap=0).select(2500, mode='evidence_first', priority_ranges=priority_ranges(packet))
    displayed = prompt_packet(packet, spans, rec=rec)
    assert displayed['fields'][0]['comparison'] == 'documents_conflict'
    assert all(s.text == rec['docs'][s.doc_index]['text'][s.start:s.end] for s in spans)
    assert sum(len(s.text)+40 for s in spans) <= 2500


def test_no_file_io_no_input_mutation_and_id_invariance():
    rec = record('배정예산금액: 66,000,000원')
    saved = copy.deepcopy(rec)
    with patch('builtins.open', side_effect=AssertionError('filesystem accessed')):
        first = compare(rec)
        rec['id'] = 'renamed'
        assert compare(rec) == first
    rec['id'] = saved['id']
    assert rec == saved


def test_comparison_packet_is_only_in_requested_group_and_schema_is_unchanged():
    knowledge = Knowledge(Path('data_open/data'))
    config = Config(name='test_comparison', mode='evidence_first', rubric_version='v4',
                    response_format='factored', shared_prefix=True, cross_source_facts=True,
                    document_chars=4000, legal_chars=0)
    rec = record('배정예산금액: 66,000,000원\n계약방법: 제한경쟁')
    groups = [(1, 2), (23, 24)]
    prompts = build_shared_prompts(rec, knowledge, config, None, groups)
    assert prompts[0]['spans'] == prompts[1]['spans']
    assert '동일 필드 대조 보조사실' not in prompts[0]['messages'][1]['content']
    assert '동일 필드 대조 보조사실' in prompts[1]['messages'][1]['content']
    assert '예산=...;계약방법=...;지역=...;업종=...' not in prompts[0]['messages'][1]['content']
    assert '예산=...;계약방법=...;지역=...;업종=...' in prompts[1]['messages'][1]['content']
    assert prompts[0]['comparison_facts'] is None
    assert prompts[1]['comparison_facts']['comparisons'][0]['status'] == 'different'
    single = build_prompt(rec, knowledge, dataclasses.replace(config, shared_prefix=False), items=(24,))
    assert '동일 필드 대조 보조사실' in single['messages'][1]['content']
    assert '예산=...;계약방법=...;지역=...;업종=...' in single['messages'][0]['content']


def test_disabled_config_preserves_existing_prompt():
    knowledge = Knowledge(Path('data_open/data'))
    config = Config(mode='evidence_first')
    prompt = build_prompt(record('사업예산: 66,000,000원(부가세 포함)'), knowledge, config, items=(24,))
    assert prompt['comparison_facts'] is None
    assert '동일 필드 대조 보조사실' not in prompt['messages'][1]['content']


def test_pipeline_stops_cross_field_vat_borrow_and_keeps_real_difference():
    config = Config(mode='evidence_first', rule_checks=True, cross_source_facts=True)
    response = {'text': json.dumps({'v': [0], 'e': [0]})}
    rec = record('사업예산: 50,000,000원\n기초금액: 55,000,000원(부가세 포함)')
    text = rec['docs'][0]['text']
    prompt = {'spans': [Span(0, '공고문', 0, len(text), text)]}
    legacy_config = Config(mode='evidence_first', rule_checks=True, cross_source_facts=False)
    before, _ = _response_row(rec, response, prompt, (24,), legacy_config, None, (24,))
    automatic, _ = _response_row(rec, response, prompt, (24,), config, None, (24,))
    after, _ = _response_row(rec, response, prompt | {'comparison_facts': compare(rec)},
                             (24,), config, None, (24,))
    assert before['v24'] == 1  # Reproduced erroneous legacy override of a correct model value.
    assert automatic['v24'] == after['v24'] == 0
    rec = record('사업예산: 6천6백만원 (부가세 포함)')
    text = rec['docs'][0]['text']
    after, _ = _response_row(rec, response,
                             {'spans': [Span(0, '공고문', 0, len(text), text)], 'comparison_facts': compare(rec)},
                             (24,), config, None, (24,))
    assert after['v24'] == 1 and after['e24'] == text


@pytest.mark.parametrize('text,meta', [
    ('계약방법: 제한경쟁이 아닌 일반경쟁으로 진행한다.', {'계약방법': '일반경쟁'}),
    ('다음은 작성 예시이며 본 입찰에 적용하지 않는다.\n계약방법: 제한경쟁', {'계약방법': '일반경쟁'}),
    ('재공고도 유찰된 경우에만 적용할 계약방법: 수의계약\n본 공고는 최초 공고이다.', {'계약방법': '일반경쟁'}),
    ('입찰 참가 업체에 업종코드: 1234 등록을 요구하지 않는다. 업종 제한 없음.', {'면허업종제한목록': '5678'}),
    ('다음은 작성 예시이며 본 입찰에 적용하지 않는다.\n사업예산: 66,000,000원 (부가세 포함)', {}),
    ('사업예산: 월 5,000,000원 (부가세 포함), 계약기간 12개월', {'배정예산금액': 60000000}),
    ('사업예산: 30,000,000원/년 (부가세 포함), 사업기간 2년', {'배정예산금액': 60000000}),
    ('사업예산: 50,000,000원\n입찰금액은 부가세 포함하여 제출한다.', {}),
    ('사업예산: 50,000,000원 | 입찰금액: 부가세 포함', {}),
    ('사업예산: 50,000,000원; 입찰금액: 55,000,000원(부가세 포함)', {}),
    ('사업예산: 50,000,000원 입찰금액은 55,000,000원(부가세 포함)', {}),
])
def test_independent_adversarial_scope_controls(text, meta):
    rec = record(text, **meta)
    assert positive_decision(rec, compare(rec)) is None


@pytest.mark.parametrize('notice', [
    '| 사업예산(천원, 부가세 포함) | 55,000 |',
    '단위: 천원, 부가세 포함\n| 구분 | 사업예산 |\n| 본 사업 | 55,000 |',
])
def test_table_notice_conflict_remains_visible(notice):
    rec = record(notice, '사업예산: 66,000,000원(부가세 포함)')
    assert comparison(rec)['status'] == 'documents_conflict'
    assert {f['value'] for f in amount_facts(rec)} == {'55000000', '66000000'}
    assert positive_decision(rec, compare(rec)) is None


def test_multi_row_table_never_treats_first_row_as_complete():
    rec = record('| 구분 | 사업예산(천원, 부가세 포함) |\n| 공고문 | 55,000 |\n| 제안요청서 | 66,000 |')
    assert {f['value'] for f in amount_facts(rec)} == {'55000000', '66000000'}
    assert comparison(rec)['status'] == 'row_scope_unresolved'
    assert positive_decision(rec, compare(rec)) is None


def test_unparsed_occurrence_blocks_other_notice_from_silently_winning():
    rec = record('사업예산: 금 [판독불가]원(부가세 포함)', '사업예산: 66,000,000원(부가세 포함)')
    rec['docs'][1]['type'] = '공고문'
    packet = compare(rec)
    assert packet['comparisons'][0]['status'] == 'extraction_unresolved'
    assert any(f['value'] is None and f['doc_index'] == 0 for f in packet['facts'])
    assert positive_decision(rec, packet) is None
