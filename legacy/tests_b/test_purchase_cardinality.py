"""Item counts, literal list ownership, and catalog conditions stay separate."""
import copy
from types import SimpleNamespace

import pytest

from submission.pps.purchase_cardinality import purchase_cardinality
from submission.pps.qualification import purchase_scope
from tests.test_comparison import record
from tests.test_purchase_qualification_boundaries import CATALOG


def notice(text, meta='정밀측정장치[9912340001]'):
    return record(text, 업무구분='물품(내자)', 세부품명번호목록=meta)


def scope(text, meta='정밀측정장치[9912340001]', catalog=CATALOG):
    return purchase_scope(notice(text, meta), catalog, [], [])


def table(title='구입내역: 정밀측정장치 등 2종', rows=None, header='품명 | 세부품명번호 | 규격'):
    rows = rows if rows is not None else [
        '정밀측정장치(실내형) | 9912340001 | 실내형',
        '정밀측정장치(실외형) | 9912340001 | 실외형']
    return '\n'.join([title, header, *rows])


@pytest.mark.parametrize('count,total', (
    ('2종', 2), ('총 2품목', 2), ('등(2종)', 2), ('등（2종）', 2),
    ('외 (2종)', 3), ('등 (총 2 종)', 2), ('1,438종', 1438), ('４０６종', 406),
))
@pytest.mark.parametrize('layout', ('구매품목: 정밀측정장치 {}', '구 입 내 역\n정밀측정장치 {}'))
def test_explicit_purchase_counts_reveal_unidentified_members(count, total, layout):
    rec = notice(layout.format(count))
    before = copy.deepcopy(rec)
    p = purchase_scope(rec, CATALOG, [], [])
    assert p['status'] == 'unknown'
    assert p['purchase_item_counts'][0]['minimum_total'] == total
    assert 'explicit_multiple_items_not_all_identified' in p['uncertainty']
    for key in ('evidence', 'scope_evidence'):
        ev = p['purchase_item_counts'][0][key]
        assert rec['docs'][ev['doc_index']]['text'][ev['start']:ev['end']] == ev['text']
    assert rec == before


@pytest.mark.parametrize('text', (
    '과업내용: 국내 담수식물 표본 확보 1,438종(10,000점)',
    '사업내용: 브랜드 홍보용 영상(2종) 및 이미지(10장) 제작',
    '구매품목: 정밀측정장치(부속품 2종 포함)',
    '구매품목: 제2종 면허 판독기',
    '구매품목: 정밀측정장치 2.1 종류 및 기호',
    '구매품목: 정밀측정장치 1,43종',
))
def test_species_components_license_classes_and_decimal_sections_are_not_purchase_totals(text):
    assert not purchase_cardinality(notice(text))['counts']


@pytest.mark.parametrize('count', ('1종', '총 1품목', '등(1종)'))
def test_single_item_does_not_require_a_multi_item_table(count):
    assert scope('구매품목: 정밀측정장치 '+count)['status'] == 'competition'


def test_purchase_title_parenthesis_preserves_total():
    p = scope('가. 건 명: 정밀측정장치 등(3품목)입찰 구매')
    assert p['purchase_item_counts'][0]['minimum_total'] == 3
    assert p['status'] == 'unknown'


def test_classification_code_count_does_not_measure_item_count():
    p = scope(table())
    assert p['status'] == 'competition'
    witness = p['purchase_item_lists'][0]
    assert witness['observed_item_count'] == 2
    assert witness['distinct_codes'] == ['9912340001']
    assert witness['catalog_identity_complete'] and witness['whole_purchase_certified']
    assert not witness['catalog_conditions_certified'] and not witness['missing_cells_inferred']


def test_distinct_observed_specifications_can_distinguish_rows_with_same_product_name():
    p = scope(table(rows=['정밀측정장치 | 9912340001 | 실내형', '정밀측정장치 | 9912340001 | 실외형']))
    assert p['status'] == 'competition'


def test_item_list_keeps_every_name_code_specification_and_header_source_address():
    rec = notice(table())
    witness = purchase_cardinality(rec)['item_lists'][0]
    refs = [witness['header_evidence'], witness['scope_evidence']]
    refs += [r[k] for r in witness['rows'] for k in ('name', 'code_evidence', 'specification')]
    assert all(rec['docs'][r['doc_index']]['text'][r['start']:r['end']] == r['text'] for r in refs)


@pytest.mark.parametrize('rows', (
    ['정밀측정장치 | 9912340001 | 실내형', '정밀측정장치 | 9912340001 | 실내형'],
    ['정밀측정장치 | 9912340001 | 실내형', ' | 9912340001 | 실외형'],
    ['정밀측정장치 | 9912340001 | 실내형', '정밀측정장치 | | 실외형'],
    ['정밀측정장치 | 9912340001 | 실내형', '정밀측정장치(실외형) | 99123400 | 실외형'],
    ['정밀측정장치 | 9912340001 | 실내형'],
    ['정밀측정장치 | 9912340001 | 실내형', '정밀측정장치(실외형) | 9912340001 | 실외형',
     '정밀측정장치(교육용) | 9912340001 | 교육용'],
    ['정밀측정장치 | 9912340001 | 실내형', '정밀측정장치(실외형) | 9912340001 | 실외형', '추가 품목 별도'],
))
def test_repeated_missing_wrapped_or_extra_rows_do_not_certify_complete_identity(rows):
    p = scope(table(rows=rows))
    assert p['status'] == 'unknown' and not p['purchase_item_lists']


@pytest.mark.parametrize('prefix', ('예시\n', '참고자료\n', '작성예\n'))
def test_reference_owned_table_is_not_an_operative_complete_purchase_list(prefix):
    assert not purchase_cardinality(notice(prefix+table()))['item_lists']


@pytest.mark.parametrize('title', (
    '구입내역: 정밀측정장치 등 2종(일부 발췌)',
    '구입내역: 정밀측정장치 등 2종 구매 취소',
    '구입내역: 정밀측정장치 등 2종 구매 예정',
    '구입내역: 정밀측정장치 등(2종',
    '구입내역: 정밀측정장치 외 2종',
    '구입내역: 정밀측정장치 등 2종 및 부속품 등 2종',
))
def test_partial_conditional_or_ambiguous_total_cannot_close_list(title):
    assert scope(table(title))['status'] == 'unknown'


def test_separate_documents_do_not_supply_missing_rows_by_adjacency():
    rec = notice('구입내역: 정밀측정장치 등 2종')
    rec['docs'].append({'type': '규격서', 'text': table().split('\n', 1)[1]})
    assert purchase_scope(rec, CATALOG, [], [])['status'] == 'unknown'


def test_unknown_code_or_additional_registration_codes_do_not_discharge_missing_items():
    p = scope(table(rows=['정밀측정장치 | 9912340001 | 실내형', '다른장비 | 9912340002 | 실외형']))
    assert p['status'] == 'unknown'
    p = scope('구매품목: 정밀측정장치 등 2종', meta='정밀측정장치[9912340001], 다른장비[9912340002]')
    assert 'explicit_multiple_items_not_all_identified' in p['uncertainty']


def test_conditional_catalog_status_is_not_certified_for_every_variant():
    catalog = SimpleNamespace(products=copy.deepcopy(CATALOG.products))
    catalog.products['9912340001']['특이사항'] = '추정가격 1억원 미만에 한함'
    p = scope(table(), catalog=catalog)
    assert p['products'][0]['condition']['status'] == 'met'
    assert p['status'] == 'unknown'
    assert 'multiple_item_catalog_condition_scope_unresolved' in p['uncertainty']


def test_anonymous_attributes_are_not_extra_columns():
    p = scope(table(rows=['정밀측정장치 | 9912340001 | 실내형 [지역:r1|단위=기초]',
                          '정밀측정장치 | 9912340001 | 실외형 [지역:r2|단위=기초]']))
    assert p['status'] == 'competition'


def test_duplicate_examples_do_not_erase_a_later_operative_table():
    p = scope('예시\n'+table()+'\n본문\n'+table())
    assert len(p['purchase_item_lists']) == 1
    assert p['status'] == 'competition'


def test_more_than_one_unreconciled_total_remains_unknown():
    p = scope(table()+'\n구매품목: 정밀측정장치 등 3종')
    assert p['status'] == 'unknown'


def test_project_contents_count_uses_its_literal_purchase_object_in_same_document():
    rec=notice('1. 사업명: 측정 장비용 부품 구입\n2. 사업내용: 측정 장비용 부품 8종(120개)')
    facts=purchase_cardinality(rec)
    assert facts['counts'][0]['minimum_total']==8
    assert purchase_scope(rec,CATALOG,[],[])['status']=='unknown'


@pytest.mark.parametrize('title', ['사업명: 영상 제작 용역','사업명: 다른 장비 구매',
                                  '사업명: 측정 장비용 부품 구입 취소'])
def test_project_content_count_cannot_borrow_an_unrelated_or_withdrawn_purchase_title(title):
    assert not purchase_cardinality(notice(title+'\n사업내용: 측정 장비용 부품 8종'))['counts']


def test_project_content_count_does_not_borrow_another_documents_title():
    rec=notice('사업명: 측정 장비용 부품 구입')
    rec['docs'].append({'type':'규격서','text':'사업내용: 측정 장비용 부품 8종'})
    assert not purchase_cardinality(rec)['counts']


def repeated_specification(count=2, *, names=('정밀측정장치(실내형)', '정밀측정장치(실외형)'),
                           reference='붙임1', second_attachment=True):
    blocks = []
    for name in names:
        blocks.append('\n'.join(('번 호', '품 명', '단 위', '비 고', name, '개',
                                 '제시한 규격과 동등 이상의 물품', '1. 용도', '정밀 측정용 장치')))
    tail = '\n붙임 2\n제출서식' if second_attachment else ''
    return (f'구입품목: 정밀측정장치 등 {count}종({reference}. 물품 규격서 참고)\n'
            '붙임 1\n물품 규격서\n' + '\n'.join(blocks) + tail)


def test_referenced_repeated_specification_blocks_close_names_but_not_catalog_identity():
    rec = notice(repeated_specification())
    facts = purchase_cardinality(rec)
    assert len(facts['item_lists']) == 1
    witness = facts['item_lists'][0]
    assert witness['observed_item_count'] == 2
    assert [r['name']['text'] for r in witness['rows']] == [
        '정밀측정장치(실내형)', '정밀측정장치(실외형)']
    assert witness['whole_purchase_certified']
    assert not witness['catalog_identity_complete']
    assert not witness['summary_table_layout_reconstructed']
    p = purchase_scope(rec, CATALOG, [], [])
    assert p['status'] == 'unknown'
    assert 'explicit_multiple_items_catalog_identity_unresolved' in p['uncertainty']
    assert 'explicit_multiple_items_not_all_identified' not in p['uncertainty']


@pytest.mark.parametrize('text', (
    repeated_specification(count=3),
    repeated_specification(names=('정밀측정장치(실내형)', '정밀측정장치(실내형)')),
    repeated_specification(reference='붙임2'),
    repeated_specification(second_attachment=False) + '\n번 호\n품 명\n단 위\n비 고\n추가장비\n개',
))
def test_mismatched_duplicate_or_unbounded_repeated_blocks_do_not_close_list(text):
    facts = purchase_cardinality(notice(text))
    assert not facts['item_lists']


def test_same_exact_whole_total_in_notice_and_referenced_attachment_is_reconciled():
    rec = notice('구매품목: 정밀측정장치 2종')
    rec['docs'].append({'type': '규격서', 'text': repeated_specification()})
    facts = purchase_cardinality(rec)
    assert len(facts['item_lists']) == 1
    assert all(count['item_list_index'] == 0 for count in facts['counts'])
    assert facts['counts'][0]['item_list_reconciliation'].startswith('same_record_')
