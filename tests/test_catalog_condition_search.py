"""Search obligations remain separate from designation truth and source scope."""
import copy
import csv

import pytest

from submission.pps.catalog_condition_search import query_plan
from submission.pps.catalog_predicates import compile_note
from submission.pps.catalog_candidates import CatalogCandidates
from submission.pps.notice_search import NoticeSearch
from submission.pps.purchase_reading import read_purchase
from tests.test_catalog_candidates import CharacterTokenizer, PRODUCTS
from tests.test_service_identity import DATA


SERVER = 'x86 서버 CPU 1개 전체, CPU 2개 중 Clock(기본주파수) 3.2GHz 이하 제품에 한함'
DRONE = '1. 고정익, 군사용, 수소드론 제외 2. 자체중량 25㎏ 이하 또는 운용상승고도 150m 이하의 무인비행장치에 한함'


def row(code='4321150102', name='컴퓨터서버', note=SERVER):
    return {'code': code, 'name': name, 'condition': note}


def test_compound_condition_keeps_each_atomic_question_and_the_original_logic():
    result = query_plan([row(), row('2513189901', '드론', DRONE)])
    assert len(result['queries']) == 8
    for product in result['products']:
        assert product['program'] == compile_note(product['note'])
        assert set(product['queried_fields']) == set(product['program']['required_fields'])
    text = '\n'.join(result['queries'])
    assert all(word in text for word in ('ARM', '기본', '코어', '이륙중량', '자체중량', '군사용', '혼합 동력'))
    assert result['query_plan_complete']
    assert not result['condition_truth_certified']
    assert not result['purchase_identity_certified']
    assert not result['absence_verified']


def test_permuted_duplicate_candidates_do_not_change_query_weight_or_cap_order():
    rows = [row(), row('2513189901', '드론', DRONE)]
    before = copy.deepcopy(rows)
    assert query_plan(rows, max_queries=4) == query_plan(list(reversed(rows)) + rows, max_queries=4)
    assert rows == before
    result = query_plan(rows, max_queries=2)
    assert [len(p['queried_fields']) for p in result['products']] == [1, 1]
    assert result['omitted_questions'] == 6
    assert not result['query_plan_complete']
    assert all(p['unsearched_fields'] and not p['query_plan_complete'] for p in result['products'])


def test_unsupported_tail_is_retained_without_partial_predicate_certification():
    note = SERVER + ' 추가 미지원 조건을 확인해야 함'
    product = query_plan([row(note=note)])['products'][0]
    assert product['program'] is None
    assert note in product['questions'][0]['query']
    assert product['queried_fields'] == []


def test_long_unsupported_note_does_not_silently_disappear_at_query_cap():
    note = '미지원 추가 조건 ' * 70
    plan = query_plan([row(note=note)], max_queries=1)
    product = plan['products'][0]
    assert product['note'] == note
    assert len(product['questions']) > 1
    assert not product['query_plan_complete']
    assert plan['omitted_questions'] > 0


def test_conflicting_same_code_notes_are_not_collapsed():
    plan = query_plan([row(), row(note='목재 소재의 제품에 한함')])
    assert len(plan['products']) == 2
    assert set(p['note'] for p in plan['products']) == {SERVER, '목재 소재의 제품에 한함'}


@pytest.mark.parametrize('cap', [0, -1, True, 1.0])
def test_query_cap_rejects_nonpositive_or_inexact_values(cap):
    with pytest.raises(ValueError):
        query_plan([row()], max_queries=cap)


@pytest.mark.parametrize('changes', [{'code': '123'}, {'code': 4321150102}, {'name': ''}, {'condition': None}])
def test_untrusted_candidate_shape_cannot_become_a_search_plan(changes):
    with pytest.raises(ValueError):
        query_plan([{**row(), **changes}])


def test_supplied_catalog_has_no_silently_omitted_compiled_fields():
    path = DATA / '법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv'
    with path.open(encoding='utf-8-sig', newline='') as f:
        for source in csv.DictReader(f):
            plan = query_plan([row(source['세부품명번호'], source['세부품명'], source['특이사항'])])
            assert plan['query_plan_complete']
            for product in plan['products']:
                if product['program']:
                    assert product['queried_fields'] == product['program']['required_fields']


def test_condition_feedback_preserves_seed_budget_and_current_notice_boundary():
    text = ('1. 구매내역\n품명: 컴퓨터서버\n2. 제품규격\nProcessor: ARM\n'
            '3. 허용범위\n동등 이상인 다른 규격도 허용한다.\n4. 참가자격\n중소기업 확인서를 제출한다.')
    record = {'id': 'synthetic', 'meta': {'세부품명번호목록': '컴퓨터서버[4321150102]'},
              'docs': [{'doc_id': 'D0', 'type': '규격서', 'text': text}]}
    before = copy.deepcopy(record)
    rows = copy.deepcopy(PRODUCTS)
    rows['4321150102'] = {**rows['9912340001'], '세부품명': '컴퓨터서버', '특이사항': SERVER}
    catalog = CatalogCandidates(rows)
    tool = NoticeSearch(record, CharacterTokenizer())
    result = read_purchase(tool, catalog, token_budget=300, catalog_method='lexical',
                           source_method='lexical', followup_policy='condition_groups')
    audit = result['diagnostics']['purchase_feedback']
    assert audit['condition_plan']['queries']
    assert audit['cumulative_unique_source_tokens'] == result['source_tokens'] <= 300
    assert not audit['previous_text_discarded']
    assert record == before and not result['coverage']['absence_verified']
