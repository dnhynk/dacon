"""Alternative factual queries must retain independent evidence candidates."""
import copy

import numpy as np
import pytest

from submission.pps.notice_search import NoticeSearch
from tests.test_notice_search import CharacterTokenizer, record


def test_best_query_keeps_one_fact_hit_competitive_with_one_lexical_hit():
    families = [[[0, 1]], [[2], [3], [4]]]
    before = copy.deepcopy(families)
    assert NoticeSearch._fuse(families) == [0, 1, 2, 3, 4]
    assert NoticeSearch._fuse(families, query_aggregation='best') == [0, 2, 3, 4, 1]
    assert families == before


def test_best_query_does_not_double_vote_paraphrases_but_retains_cross_family_votes():
    families = [[[0, 1]], [[1, 2], [2, 1]]]
    ranked = NoticeSearch._fuse(families, query_aggregation='best')
    assert ranked == [1, 0, 2]
    families[1] *= 4
    assert NoticeSearch._fuse(families, query_aggregation='best') == ranked


def test_best_query_is_order_invariant_and_respects_candidate_depth():
    families = [[[4, 9]], [[3, 7], [2, 6]]]
    assert NoticeSearch._fuse(families, depth=1, query_aggregation='best') == [2, 3, 4]
    families.reverse()
    families[0].reverse()
    assert NoticeSearch._fuse(families, depth=1, query_aggregation='best') == [2, 3, 4]


@pytest.mark.parametrize('budget', (30, 130))
def test_optional_rank_policy_preserves_original_context_cost_and_absence_contract(budget):
    class Encoder:
        def encode(self, texts):
            return np.array([[1., 0.] if '예외' in t else [0., 1.] for t in texts])
    rec = record('1. 제출 의무\n소스를 제출한다.\n단, 상용제품은 예외이다.',
        '2. 계약 단계\n계약 후 자료를 인계한다.')
    before = copy.deepcopy(rec)
    search = NoticeSearch(rec, CharacterTokenizer(), Encoder())
    result = search.search([20], method='hybrid', token_budget=budget, query_aggregation='best')
    assert sum(len(s['text']) for s in result['spans']) == result['source_tokens'] <= budget
    assert all(s['text'] == rec['docs'][s['doc_index']]['text'][s['start']:s['end']] for s in result['spans'])
    assert result['diagnostics']['query_aggregation'] == 'best'
    assert result['coverage']['absence_verified'] is False
    assert rec == before


def test_an_unrecognized_policy_or_changed_current_baseline_is_rejected():
    with pytest.raises(ValueError):
        NoticeSearch._fuse([[[0]]], query_aggregation='sum')
    search = NoticeSearch(record('기존 공고 원문'), CharacterTokenizer())
    with pytest.raises(ValueError):
        search.search([20], method='current', token_budget=100, query_aggregation='best')
