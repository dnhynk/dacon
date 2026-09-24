"""Source/cost and complementary-evidence contracts for joint context selection."""
import copy

import pytest

from submission.pps.evidence_selection import EvidenceObjective, pack_evidence, _portable_log1p
from submission.pps.notice_search import NoticeSearch
from submission.pps.retrieval import Span
from tests.test_notice_search import CharacterTokenizer, record


def test_source_selection_does_not_depend_on_platform_logarithm_or_decimal_context(monkeypatch):
    import math
    from decimal import localcontext,ROUND_UP
    rec=record('소스 및 사용권 인계\n단, 신규 개발은 없다.','수급인 작업 도구 사용 조건','실제 납품품 구성 목록')
    kwargs=dict(method='lexical',token_budget=35,selection_policy='evidence_cover')
    expected=NoticeSearch(rec,CharacterTokenizer()).search([20],**kwargs)
    def forbidden(_):raise AssertionError('Platform-specific logarithm reached the evidence objective')
    monkeypatch.setattr(math,'log1p',forbidden)
    with localcontext() as context:
        context.prec=3;context.rounding=ROUND_UP
        actual=NoticeSearch(rec,CharacterTokenizer()).search([20],**kwargs)
    assert actual==expected


@pytest.mark.parametrize('value',[0.,1e-100,1e-16,0.1,1.,17.,1000.])
def test_portable_diminishing_return_matches_high_precision_reference(value):
    from decimal import Decimal,localcontext
    with localcontext() as context:
        context.prec=180
        expected=float((Decimal(1)+Decimal.from_float(value)).ln())
    assert _portable_log1p(value)==expected


@pytest.mark.parametrize('value',[-1.,float('nan'),float('inf')])
def test_invalid_mass_cannot_enter_the_ranking(value):
    with pytest.raises(ValueError):_portable_log1p(value)


def test_second_distinct_passage_for_same_fact_has_diminishing_positive_value():
    search = NoticeSearch(record('소스를 인계한다.', '개발 의무는 없다.'), CharacterTokenizer())
    objective = EvidenceObjective(search, [[[0, 1]]], [0, 1])
    first, second = [objective.covered(context) for context in search._contexts]
    assert objective.value(first | second) > objective.value(first)
    assert objective.value(first | second) - objective.value(first) < objective.value(second)


def test_index_overlap_does_not_multiply_credit_or_hide_unread_conditions():
    search = NoticeSearch(record('소스를 인계한다.\n단, 신규 개발은 요구하지 않는다.'), CharacterTokenizer())
    before = EvidenceObjective(search, [[[0]]], [0])
    search.chunks.append(search.chunks[0])
    after = EvidenceObjective(search, [[[0, 1]]], [0, 1])
    complete = before.covered(search._contexts[0])
    assert before.value(complete) == after.value(complete)
    # An unreturned suffix in a line cannot earn that line's utility.
    assert after.value(after.covered(((0, 0, 4),))) == 0
    assert after.value(after.covered(((0, 0, 10),))) < after.value(complete)


def test_shared_context_receives_credit_for_every_fact_it_actually_returns():
    search = NoticeSearch(record('소스 인계\n개발 면제', '사용권 기간'), CharacterTokenizer())
    search.chunks = [Span(0, '공고문', 0, 5, '소스 인계'),
                     Span(0, '공고문', 6, 11, '개발 면제'), search.chunks[1]]
    objective = EvidenceObjective(search, [[[0], [1], [2]]], [0, 1, 2])
    mask = objective.covered(((0, 0, 11),))
    assert objective.masses(mask) == (1., 1., 0.)


def test_one_exchange_revisits_density_choice_using_actual_union_cost():
    # Density first chooses A(4), then B(5). Swapping A for C(5) is better.
    search = NoticeSearch(record('가' * 4, '나' * 5, '다' * 5), CharacterTokenizer())
    families = [[[1, 0, 2]], [[2, 0, 1]]]
    ranges, chosen, _, diagnostics = pack_evidence(search, families, [0, 1, 2], (), 10, False)
    objective = EvidenceObjective(search, families, [0, 1, 2])
    feasible = [((i, 0, len(search.rec['docs'][i]['text'])),) for i in range(3)]
    feasible += [(feasible[a][0], feasible[b][0]) for a, b in ((0, 1), (0, 2), (1, 2))]
    assert diagnostics['utility'] == max(objective.value(objective.covered(r)) for r in feasible)
    assert search.token_cost(ranges) <= 10
    assert diagnostics['exchange_evaluations'] > 0
    assert chosen == [1, 2]
    exchange = next(row for row in diagnostics['selection_trace'] if row['action'] == 'exchange')
    assert exchange['removed_candidate'] == 0 and exchange['added_candidate'] == 2
    assert exchange['removed_complete_source_units'] == 1


def test_context_aliases_share_one_selection_and_all_source_costs_are_charged():
    text = '1. 인계 조건\n소스와 사용권을 인계한다.\n단, 신규 개발은 요구하지 않는다.'
    search = NoticeSearch(record(text), CharacterTokenizer())
    search.chunks.append(search.chunks[0])
    search._contexts.append(search._contexts[0])
    ranges, chosen, _, diagnostics = pack_evidence(search, [[[0, 1]]], [0, 1], (), len(text), True)
    assert len(chosen) == 1 and diagnostics['unique_candidate_contexts'] == 1
    assert diagnostics['candidate_status'][0]['aliases'] == [0, 1]
    assert search.token_cost(ranges) == len(text)
    assert '신규 개발은 요구하지 않는다.' in search.read(ranges, token_budget=len(text))['spans'][0]['text']


@pytest.mark.parametrize('budget', (1, 28, 100))
def test_policy_preserves_determinism_provenance_budget_and_unverified_absence(budget):
    rec = record('소프트웨어 소스를 인계한다.', '다만 신규 개발은 요구하지 않는다.')
    before = copy.deepcopy(rec)
    search = NoticeSearch(rec, CharacterTokenizer())
    kwargs = dict(method='lexical', token_budget=budget, selection_policy='evidence_cover')
    a = search.search([20], **kwargs)
    assert a == search.search([20], **kwargs)
    assert a['source_tokens'] == sum(len(s['text']) for s in a['spans']) <= budget
    assert all(s['text'] == rec['docs'][s['doc_index']]['text'][s['start']:s['end']] for s in a['spans'])
    assert not a['coverage']['absence_verified']
    assert not a['diagnostics']['packing']['utility_is_legal_confidence']
    assert rec == before


def test_required_witness_survives_exchange_and_is_never_free():
    search = NoticeSearch(record('소프트웨어 소스', '개발 면제', '독립된 필수 근거'), CharacterTokenizer())
    end = len(search.rec['docs'][2]['text'])
    required = [(2, 0, end)]
    result = search.search([20], queries=['소스 개발'], selection_policy='evidence_cover',
        method='lexical', required_ranges=required, token_budget=23)
    assert any(s['doc_index'] == 2 and s['end'] == end for s in result['spans'])
    assert result['source_tokens'] <= 23
    with pytest.raises(ValueError, match='Required source witnesses'):
        search.search([20], selection_policy='evidence_cover', method='lexical',
            required_ranges=required, token_budget=end - 1)


def test_identical_text_in_two_documents_keeps_both_provenance_locations():
    search = NoticeSearch(record('소스 인계 의무', '소스 인계 의무'), CharacterTokenizer())
    ranges, _, _, diagnostics = pack_evidence(search, [[[0, 1]]], [0, 1], (), 100, True)
    assert {r[0] for r in ranges} == {0, 1}
    assert diagnostics['unique_candidate_contexts'] == 2


@pytest.mark.parametrize('options', ({'selection_policy': 'unknown'},
    {'query_aggregation': 'unknown'}, {'selection_policy': 'evidence_cover', 'expand_context': False},
    {'selection_policy': 'evidence_cover', 'query_aggregation': 'best'}))
def test_invalid_policy_fails_before_loading_or_encoding_any_model(options):
    class NoCall:
        def encode(self, texts):
            pytest.fail('Invalid search arguments must not spend model compute')
    search = NoticeSearch(record('소프트웨어 소스 인계'), CharacterTokenizer(), NoCall())
    with pytest.raises(ValueError):
        search.search([20], method='hybrid', token_budget=100, **options)
