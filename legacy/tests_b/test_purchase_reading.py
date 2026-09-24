"""Coupled retrieval has no hidden source reading or cross-notice feedback."""
import copy

import pytest

from submission.pps.catalog_candidates import CatalogCandidates
from submission.pps.notice_search import NoticeSearch
from submission.pps.purchase_reading import seed_reading, catalog_source_queries, read_purchase
from tests.test_catalog_candidates import PRODUCTS,CharacterTokenizer


def rec(text,tail=''):
    return {'id':'synthetic','meta':{},'docs':[{'doc_id':'D0','type':'규격서','text':text+tail}]}


def test_seed_preserves_a_multi_item_list_and_never_certifies_whole_identity():
    record=rec('구입품목\n- 온도측정장치 : 2대\n- 안전장갑 : 3개\n※ 규격서 참조\n1. 참가자격\n중소기업')
    tool=NoticeSearch(record,CharacterTokenizer())
    seed=seed_reading(tool,token_budget=600)
    assert any('안전장갑' in s['text'] for s in seed['spans'])
    assert seed['source_tokens']<=200
    assert not seed['diagnostics']['purchase_seed']['whole_purchase_certified']
    assert not seed['coverage']['absence_verified']


def test_every_catalog_query_is_a_returned_original_range_not_an_unseen_source_fact():
    record=rec('품명 | 규격 | 수량\n온도측정장치 | 실외용 | 2\n안전장갑 | 대형 | 3\n2. 납품조건\n매우 긴 정보\n'*3)
    tool=NoticeSearch(record,CharacterTokenizer())
    seed=seed_reading(tool,token_budget=450)
    for q in catalog_source_queries(record,seed):
        e=q['evidence']
        assert e['text']==record['docs'][e['doc_index']]['text'][e['start']:e['end']]
        assert any(s['start']<=e['start'] and e['end']<=s['end'] for s in seed['spans'])


def test_catalog_feedback_keeps_seed_source_and_all_followup_context_under_one_cap():
    record=rec('1. 구매내역\n품명 | 규격 | 수량\n온도측정장치 | 실외용 | 2\n안전장갑 | 대형 | 3\n',
        '2. 사용조건\n실외용 온도측정장치를 납품한다.\n3. 기타사항\n안전장갑은 동등품을 허용한다.')
    before=copy.deepcopy(record)
    tool=NoticeSearch(record,CharacterTokenizer())
    index=CatalogCandidates(PRODUCTS)
    result=read_purchase(tool,index,token_budget=220,catalog_method='lexical',source_method='lexical')
    audit=result['diagnostics']['purchase_feedback']
    assert result['source_tokens']==audit['cumulative_unique_source_tokens']<=220
    assert not audit['previous_text_discarded']
    assert all(any(s['doc_index']==di and s['start']<=lo and hi<=s['end'] for s in result['spans'])
               for di,lo,hi in audit['seed_ranges'])
    assert record==before and not result['coverage']['absence_verified']


def test_another_notices_seed_or_catalog_result_cannot_be_silently_reused():
    a=NoticeSearch(rec('품명: 온도측정장치'),CharacterTokenizer())
    b=NoticeSearch(rec('품명: 압력측정장치'),CharacterTokenizer())
    index=CatalogCandidates(PRODUCTS)
    seed=seed_reading(a,token_budget=450)
    with pytest.raises(ValueError):
        read_purchase(b,index,token_budget=450,seed=seed,catalog_method='lexical',source_method='lexical')
    result=read_purchase(a,index,token_budget=450,catalog_method='lexical',source_method='lexical')
    prior=result['diagnostics']['purchase_feedback']['catalog']
    with pytest.raises(ValueError):
        read_purchase(b,index,token_budget=450,catalog_result=prior,catalog_method='lexical',source_method='lexical')


@pytest.mark.parametrize('budget',[True,0,-1,123.5])
def test_invalid_source_budgets_fail_before_search(budget):
    with pytest.raises(ValueError):
        seed_reading(NoticeSearch(rec('품명: 장치'),CharacterTokenizer()),token_budget=budget)


def test_context_queries_retain_the_names_next_to_short_numeric_specification_lines():
    text='품명\n규격\n수량\n온도측정장치\n75~115cm/10매입\n2\n안전장갑\n대형\n3\n'
    record=rec(text)
    seed=NoticeSearch(record,CharacterTokenizer()).read([(0,0,len(text))],token_budget=300)
    queries=catalog_source_queries(record,seed,query_policy='context_blocks')
    assert len(queries)==1 and '온도측정장치\n75~115cm/10매입' in queries[0]['query']
    assert '안전장갑\n대형' in queries[0]['query']
    assert queries[0]['evidence']['text']==text


def test_context_queries_cannot_bridge_a_gap_that_was_never_read():
    record=rec('온도측정장치\n아직 읽지 않은 문장\n안전장갑\n')
    text=record['docs'][0]['text'];end=text.index('아직');start=text.index('안전장갑')
    seed=NoticeSearch(record,CharacterTokenizer()).read([(0,0,end),(0,start,len(text))],token_budget=300)
    queries=catalog_source_queries(record,seed,query_policy='context_blocks')
    assert len(queries)==2
    assert all('아직' not in q['query'] for q in queries)


def test_a_catalog_from_a_different_selection_policy_cannot_drive_feedback():
    tool=NoticeSearch(rec('품명: 온도측정장치'),CharacterTokenizer())
    index=CatalogCandidates(PRODUCTS)
    result=read_purchase(tool,index,token_budget=450,catalog_method='lexical',source_method='lexical')
    prior=result['diagnostics']['purchase_feedback']['catalog']
    with pytest.raises(ValueError,match='different inputs'):
        read_purchase(tool,index,token_budget=450,catalog_result=prior,catalog_method='lexical',
                      source_method='lexical',candidate_policy='rank_frontier')
