"""Budgeted multi-context replacement, tested independently of review labels."""
import itertools
import random

import pytest

from submission.pps.evidence_selection import EvidenceObjective, pack_evidence
from submission.pps.notice_search import NoticeSearch
from tests.test_notice_search import CharacterTokenizer, record


def make_search(costs):
    return NoticeSearch(record(*(chr(0xAC00 + i) * cost for i, cost in enumerate(costs))), CharacterTokenizer())


def test_one_large_context_can_be_replaced_by_two_complementary_contexts():
    costs = [8, 13, 10, 14, 11]
    families = [[[4, 1, 0, 3, 2]], [[4, 1, 3, 2, 0], [3, 1, 2, 4, 0]]]
    search = make_search(costs)
    old = pack_evidence(search, families, list(range(5)), (), 45, True)
    new = pack_evidence(search, families, list(range(5)), (), 45, True, refill=True)
    assert old[1] == [1, 3, 4] and new[1] == [0, 1, 2, 4]
    assert new[3]['utility'] > old[3]['utility']
    step = next(s for s in new[3]['selection_trace'] if s['action'] == 'refill')
    assert step['removed_candidate'] == 3 and step['added_candidates'] == [0, 2]
    assert search.token_cost(new[0]) == 42 <= 45


def test_bounded_refill_never_regresses_incumbent_on_small_exhaustive_problems():
    rng = random.Random(9132026)
    for _ in range(80):
        n = 5
        costs = [rng.randrange(3, 15) for _ in range(n)]
        rankings = [rng.sample(range(n), n) for _ in range(3)]
        families = [[rankings[0]], rankings[1:]]
        budget = rng.randrange(max(costs), sum(costs))
        search = make_search(costs)
        old = pack_evidence(search, families, list(range(n)), (), budget, True)
        new = pack_evidence(search, families, list(range(n)), (), budget, True, refill=True)
        objective = EvidenceObjective(search, families, list(range(n)))
        exact = max(objective.value(sum(1 << i for i in chosen))
            for size in range(n + 1) for chosen in itertools.combinations(range(n), size)
            if sum(costs[i] for i in chosen) <= budget)
        assert old[3]['utility'] <= new[3]['utility'] + 1e-12 <= exact + 2e-12
        assert search.token_cost(new[0]) <= budget
        assert not new[3]['optimality_certified']


def test_required_source_is_preserved_when_refill_removes_an_overlapping_candidate():
    search = make_search([8, 13, 10, 14, 11])
    families = [[[4, 1, 0, 3, 2]], [[4, 1, 3, 2, 0], [3, 1, 2, 4, 0]]]
    required = ((3, 0, 2),)
    result = pack_evidence(search, families, list(range(5)), required, 45, True, refill=True)
    assert any(di == 3 and lo == 0 and hi >= 2 for di, lo, hi in result[0])
    assert search.token_cost(result[0]) <= 45
    assert result == pack_evidence(search, families, list(range(5)), required, 45, True, refill=True)


def test_refill_has_a_deterministic_work_bound_on_a_larger_candidate_pool():
    rng = random.Random(1309)
    n = 65
    search = make_search([rng.randrange(3, 15) for _ in range(n)])
    families = [[rng.sample(range(n), n)], [rng.sample(range(n), n)]]
    result = pack_evidence(search, families, list(range(n)), (), 100, True, refill=True)
    diagnostics = result[3]
    assert diagnostics['refill_evaluations'] <= diagnostics['refill_evaluation_limit'] == 4096
    assert search.token_cost(result[0]) <= 100
    assert result == pack_evidence(search, families, list(range(n)), (), 100, True, refill=True)


@pytest.mark.parametrize('options', [
    {'required_ranges': [(0, -1, 3)]},
    {'required_ranges': [(0, 0, 1000)]},
    {'required_ranges': [(0, 0, 8)]},
    {'selection_policy': 'evidence_refill', 'expand_context': False},
    {'selection_policy': 'evidence_refill', 'query_aggregation': 'best'},
])
def test_invalid_source_or_policy_never_spends_encoder_compute(options):
    class NoCall:
        def encode(self, texts):
            pytest.fail('Impossible source request reached the embedding model')
    search = NoticeSearch(record('소프트웨어 소스 인계 조건'), CharacterTokenizer(), NoCall())
    with pytest.raises(ValueError):
        search.search([20], method='hybrid', token_budget=5, **options)


def test_empty_candidate_pool_keeps_paid_required_reading_without_absence_inference():
    search = NoticeSearch(record('관련 없는 문장'), CharacterTokenizer())
    result = search.search([20], method='lexical', token_budget=10,
        required_ranges=[(0, 0, 8)], selection_policy='evidence_refill')
    assert result['source_tokens'] == 8 and result['spans']
    assert not result['coverage']['absence_verified']
    assert result['diagnostics']['packing']['refill_evaluations'] == 0
