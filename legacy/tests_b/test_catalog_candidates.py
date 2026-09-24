"""Candidate discovery preserves static coverage and never changes applicability."""
import copy

import pytest

from submission.pps.catalog_candidates import CatalogCandidates


PRODUCTS = {
    '9912340001': {'대분류': '장치', '제품명': '측정기', '세부품명': '온도측정장치', '특이사항': '실외용은 제외'},
    '9912340002': {'대분류': '장치', '제품명': '측정기', '세부품명': '압력측정장치', '특이사항': '2MPa 이하에 한함'},
    '9912340003': {'대분류': '자재', '제품명': '보호구', '세부품명': '안전장갑', '특이사항': ''},
}


class CharacterTokenizer:
    def encode(self, text, add_special_tokens=False):
        assert not add_special_tokens
        return list(text)


class OrderedDenseEncoder:
    """Give catalog rows stable descending semantic ranks for frontier tests."""
    def encode(self, texts):
        import numpy as np
        values = []
        for text in texts:
            match = __import__('re').search(r'후보(\d+)', text)
            if match:
                score = 1 - int(match.group(1)) / 100
                values.append([score, (1-score**2)**0.5])
            else:
                values.append([1., 0.])
        return np.asarray(values, dtype=np.float32)


def search(index, queries, **kwargs):
    return index.search(queries, CharacterTokenizer(), token_budget=kwargs.pop('token_budget', 120),
                        method=kwargs.pop('method', 'lexical'), **kwargs)


def test_complete_parent_membership_is_separate_from_the_displayed_candidates():
    index = CatalogCandidates(PRODUCTS)
    result = search(index, ['온도측정장치'], max_candidates=1)
    assert result['candidates'][0]['code'] == '9912340001'
    assert result['unshown_rows'] == 2 and not result['all_catalog_rows_shown']
    assert sum(len(g['codes']) for g in result['complete_parent_groups']) == 3
    assert not result['identity_certified'] and not result['absence_certified']
    assert result['parent_names_are_not_product_definitions']


def test_code_lookup_miss_is_explicit_and_is_not_a_general_product_verdict():
    result = search(CatalogCandidates(PRODUCTS), ['등록한 업체'], required_codes=['9900000000'])
    assert result['required_lookups'] == [{'code': '9900000000', 'listed': False, 'row_ids': []}]
    assert not result['candidates'] and not result['absence_certified']


def test_required_rows_never_lose_their_conditions_to_fit_the_budget():
    index = CatalogCandidates(PRODUCTS)
    result = search(index, ['측정장치'], required_codes=['9912340002'], token_budget=15)
    assert not result['candidates'] and result['catalog_tokens'] == 0
    assert result['omitted_required_codes'] == ['9912340002']
    result = search(index, ['측정장치'], required_codes=['9912340002'], token_budget=120)
    row = result['candidates'][0]
    assert row['code'] == '9912340002' and row['condition'] == '2MPa 이하에 한함'
    assert result['catalog_tokens'] == len(index.render_rows(result['candidates'])) <= 120


def test_duplicate_queries_do_not_vote_and_catalog_order_does_not_change_ties():
    a = CatalogCandidates(PRODUCTS)
    b = CatalogCandidates(dict(reversed(list(PRODUCTS.items()))))
    assert search(a, ['온도측정장치', '안전장갑']) == search(b, ['온도측정장치', '안전장갑', ' 온도측정장치 '])
    assert a.catalog_sha256 == b.catalog_sha256


def test_different_source_items_receive_distinct_candidate_coverage():
    result = search(CatalogCandidates(PRODUCTS), ['온도측정장치', '안전장갑'], max_candidates=2)
    assert {r['code'] for r in result['candidates']} == {'9912340001', '9912340003'}


def test_expanding_a_group_returns_every_child_and_original_condition():
    index = CatalogCandidates(PRODUCTS)
    group = next(g['group'] for g in index.complete_groups() if g['parent'] == '측정기')
    members = index.members([group, group])
    assert {m['code'] for m in members} == {'9912340001', '9912340002'}
    assert {m['condition'] for m in members} == {'실외용은 제외', '2MPa 이하에 한함'}
    members[0]['name'] = 'tampered'
    assert all(m['name'] != 'tampered' for m in index.members([group]))


@pytest.mark.parametrize('groups', ([0], [True], [1.0], [999], ['1']))
def test_invalid_group_addresses_are_not_coerced(groups):
    with pytest.raises(ValueError):
        CatalogCandidates(PRODUCTS).members(groups)


@pytest.mark.parametrize('options', ({'token_budget': True}, {'token_budget': 0},
    {'max_candidates': 1.5}, {'required_codes': ['99123400']}, {'method': 'probability'}))
def test_invalid_contract_parameters_fail_before_search(options):
    with pytest.raises(ValueError):
        search(CatalogCandidates(PRODUCTS), ['측정기'], **options)


def test_a_full_catalog_display_is_only_coverage_not_semantic_certification():
    result = search(CatalogCandidates(PRODUCTS), ['측정장치', '안전장갑'], token_budget=1000)
    assert result['all_catalog_rows_shown']
    assert not result['identity_certified'] and not result['absence_certified']


def test_input_catalog_is_not_mutated_and_a_condition_change_invalidates_its_identity():
    products = copy.deepcopy(PRODUCTS)
    index = CatalogCandidates(products)
    search(index, ['장치'])
    assert products == PRODUCTS
    products['9912340001']['특이사항'] = '실내용은 제외'
    assert index.catalog_sha256 != CatalogCandidates(products).catalog_sha256


def test_dense_mode_cannot_silently_fall_back_to_keyword_search():
    with pytest.raises(ValueError, match='supplied encoder'):
        search(CatalogCandidates(PRODUCTS), ['장치'], method='dense')


def test_unnumbered_and_duplicate_code_rows_keep_distinct_original_conditions():
    rows = [{**r, '세부품명번호': c} for c, r in PRODUCTS.items()]
    rows += [{**rows[0], '특이사항': '특수용은 제외'},
             {'대분류': '기타', '제품명': '특수장치', '세부품명': '보호장치',
              '세부품명번호': '', '특이사항': '실외용에 한함'}]
    index = CatalogCandidates(rows)
    assert len(index.rows) == 5
    assert sum(len(g['row_ids']) for g in index.complete_groups()) == 5
    group = next(g['group'] for g in index.complete_groups() if g['parent'] == '특수장치')
    assert index.members([group])[0]['code'] == ''
    assert '고시 코드 공란' in index.render_rows(index.members([group]))
    result = search(index, ['측정장치'], required_codes=['9912340001'], token_budget=1000)
    assert len(result['required_lookups'][0]['row_ids']) == 2
    assert {r['condition'] for r in result['candidates'] if r['code'] == '9912340001'} == {
        '실외용은 제외', '특수용은 제외'}
    assert index.catalog_sha256 == CatalogCandidates(list(reversed(rows))).catalog_sha256


def test_two_unnumbered_rows_do_not_merge_across_groups():
    rows = [{**r, '세부품명번호': ''} for r in PRODUCTS.values()]
    index = CatalogCandidates(rows)
    group = next(g['group'] for g in index.complete_groups() if g['parent'] == '보호구')
    assert [r['name'] for r in index.members([group])] == ['안전장갑']


def test_rank_frontier_does_not_trade_strong_identity_words_for_a_short_condition():
    products=copy.deepcopy(PRODUCTS)
    products['9912340001']['특이사항']='정확한 지정조건을 생략할 수 없음. '*12
    result=search(CatalogCandidates(products),['온도측정장치'],token_budget=500,
                  max_candidates=1,selection_policy='rank_frontier')
    assert [r['code'] for r in result['candidates']]==['9912340001']
    assert result['candidates'][0]['condition']==products['9912340001']['특이사항']


def test_frontier_stops_before_arbitrarily_filling_remaining_capacity_with_rank_tails():
    products={f'991234{i:04d}':{'대분류':'장치','제품명':'측정기','세부품명':f'온도측정장치{i}',
                              '특이사항':''} for i in range(12)}
    result=search(CatalogCandidates(products),['온도측정장치'],token_budget=2000,
                  selection_policy='rank_frontier')
    assert len(result['candidates'])==3
    assert all(min(q['lexical'] for q in r['query_ranks'] if 'lexical' in q)<=3 for r in result['candidates'])
    assert not result['absence_certified'] and result['unshown_rows']==9


def test_frontier_keeps_distinct_items_when_the_query_order_changes():
    index=CatalogCandidates(PRODUCTS)
    a=search(index,['온도측정장치','안전장갑'],max_candidates=2,selection_policy='rank_frontier')
    b=search(index,['안전장갑','온도측정장치'],max_candidates=2,selection_policy='rank_frontier')
    assert [r['code'] for r in a['candidates']]==[r['code'] for r in b['candidates']]
    assert {r['code'] for r in a['candidates']}=={'9912340001','9912340003'}


def test_frontier_keeps_exact_required_lookup_even_outside_the_rank_frontier():
    result=search(CatalogCandidates(PRODUCTS),['안전장갑'],required_codes=['9912340002'],
                  token_budget=150,selection_policy='rank_frontier')
    assert result['candidates'][0]['code']=='9912340002'
    assert not result['identity_certified']


def test_semantic_frontier_expands_dense_but_not_lexical_rank_tails():
    products={f'991234{i:04d}':{'대분류':'장치','제품명':'후보군','세부품명':f'후보{i}',
                              '특이사항':''} for i in range(1,13)}
    index=CatalogCandidates(products, OrderedDenseEncoder())
    narrow=search(index,['의미 질의'],method='dense',token_budget=2000,
                  selection_policy='rank_frontier')
    wide=search(index,['의미 질의'],method='dense',token_budget=2000,
                selection_policy='semantic_frontier')
    assert len(narrow['candidates'])==3
    assert len(wide['candidates'])==10
    assert wide['candidate_frontier_depths']=={'lexical':3,'dense':10}
    lexical=search(CatalogCandidates(products),['후보'],token_budget=2000,
                   selection_policy='semantic_frontier')
    assert len(lexical['candidates'])==3


def test_lexical_candidate_records_exact_name_provenance_without_certifying_identity():
    result=search(CatalogCandidates(PRODUCTS),['실외용 온도측정장치'],
                  selection_policy='semantic_frontier')
    ranks=result['candidates'][0]['query_ranks'][0]
    assert ranks['lexical']==1 and ranks['lexical_exact'] is True
    assert not result['identity_certified']


def test_semantic_frontier_divides_dense_depth_across_many_queries():
    products={f'991234{i:04d}':{'대분류':'장치','제품명':'후보군','세부품명':f'후보{i}',
                              '특이사항':''} for i in range(1,13)}
    index=CatalogCandidates(products, OrderedDenseEncoder())
    result=search(index,['의미 질의 '+str(i) for i in range(13)],method='dense',
                  token_budget=4000,max_candidates=40,selection_policy='semantic_frontier')
    assert result['candidate_frontier_depths']=={'lexical':3,'dense':3}
    assert len(result['candidates'])==3
