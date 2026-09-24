"""Catalog expansion must not replace or drown out prerequisite questions."""
import copy

import pytest

from submission.pps.notice_search import NoticeSearch
from submission.pps.purchase_reading import read_purchase
from tests.test_notice_search import CharacterTokenizer, record


def test_more_candidate_questions_cannot_take_the_eligibility_groups_rank_budget():
    class RankedSearch(NoticeSearch):
        def _lexical_lists(self, items):
            return [[]]
        def _query_lexical_lists(self, queries):
            return [[0] if q == 'eligibility' else [1, 2] for q in queries]
    rec = record('참가 조건을 확인한다.', '물품 후보를 확인한다.', '다른 물품 후보이다.')
    tool = RankedSearch(rec, CharacterTokenizer())
    a = tool.search([18], method='lexical', token_budget=20, expand_context=False,
                    query_groups={'eligibility': ['eligibility'], 'catalog': ['candidate0']})
    b = tool.search([18], method='lexical', token_budget=20, expand_context=False,
                    query_groups={'eligibility': ['eligibility'], 'catalog': ['candidate'+str(i) for i in range(24)]})
    assert a['spans'] == b['spans']
    assert any(s['doc_index'] == 0 for s in b['spans'])
    assert b['source_tokens'] <= 20 and not b['coverage']['absence_verified']


def test_grouped_dense_questions_are_encoded_once_and_cached_only_in_current_notice():
    import numpy as np
    class Encoder:
        def __init__(self): self.calls = []
        def encode(self, texts):
            self.calls.append(tuple(texts))
            return np.array([[1., 0.] for _ in texts])
    encoder = Encoder()
    tool = NoticeSearch(record('원문 조건'), CharacterTokenizer(), encoder)
    groups = {'conditions': ['공통 질문', '공통 질문'], 'catalog': ['공통 질문', '다른 질문']}
    before = copy.deepcopy(groups)
    a = tool.search([18], method='dense', token_budget=100, query_groups=groups)
    count = len(encoder.calls)
    b = tool.search([18], method='dense', token_budget=100, query_groups=groups)
    assert a == b and len(encoder.calls) == count
    assert ('공통 질문', '다른 질문') in encoder.calls and groups == before


@pytest.mark.parametrize('groups', [{}, {'x': 'question'}, {'x': []}, {'': ['q']}, {'x': [None]}, ['q']])
def test_invalid_groups_fail_before_encoder_allocation(groups):
    class Encoder:
        def encode(self, texts): raise AssertionError('Must validate first')
    tool = NoticeSearch(record('원문 조건'), CharacterTokenizer(), Encoder())
    with pytest.raises(ValueError):
        tool.search([18], token_budget=100, query_groups=groups)


def test_ambiguous_queries_and_altered_current_control_are_rejected():
    tool = NoticeSearch(record('원문 조건'), CharacterTokenizer())
    with pytest.raises(ValueError):
        tool.search([18], token_budget=100, queries=['q'], query_groups={'x': ['q']})
    with pytest.raises(ValueError):
        tool.search([18], token_budget=100, queries='a question')
    with pytest.raises(ValueError):
        tool.search([18], method='current', token_budget=100, query_groups={'x': ['q']})


def test_purchase_followup_keeps_core_questions_and_seed_inside_the_same_cap():
    rec = record('1. 구매내역\n품명: 온도측정장치\n2. 참가자격\n소기업만 참가할 수 있다.',
                 '3. 납품조건\n동등품을 허용한다.\n단, 사용 조건을 충족해야 한다.')
    before = copy.deepcopy(rec)
    tool = NoticeSearch(rec, CharacterTokenizer())
    result = read_purchase(tool, token_budget=200, source_method='lexical', followup_policy='fact_groups')
    groups = result['diagnostics']['query_groups']
    assert set(groups) == {'specification', 'eligibility', 'purchase_candidates'}
    assert any('예외' in q for q in groups['eligibility'])
    audit = result['diagnostics']['purchase_feedback']
    assert audit['cumulative_unique_source_tokens'] == result['source_tokens'] <= 200
    assert not audit['previous_text_discarded'] and rec == before
    assert all(any(s['doc_index'] == di and s['start'] <= lo and hi <= s['end'] for s in result['spans'])
               for di, lo, hi in audit['seed_ranges'])
