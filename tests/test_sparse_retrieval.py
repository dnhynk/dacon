"""Sparse relevance must preserve token identity, source limits and notice isolation."""
import numpy as np
import pytest

from submission.pps.embeddings import sparse_similarity, sparse_weights
from submission.pps.notice_search import NoticeSearch
from tests.test_notice_search import CharacterTokenizer, record


def test_sparse_pooling_uses_max_for_repeated_ids_and_excludes_special_tokens():
    weights = sparse_weights([0, 25, 8, 25, 2, 1, 3, 19], [8., 2., 3., 4., 9., 7., 6., 0.], {0, 1, 2, 3})
    assert weights == {8: 3., 25: 4.}
    assert list(weights) == [8, 25]
    # Exact token identity, dot product, no probability normalization.
    assert sparse_similarity(weights, {25: 2., 99: 100.}) == 8.
    assert sparse_similarity(weights, {99: 100.}) == 0.


@pytest.mark.parametrize('ids,weights', [([3], []), ([True], [1.]), ([-1], [1.]),
    (['25'], [1.]), ([25], [True]), ([25], [-1.]), ([25], [float('nan')]), ([25], [float('inf')])])
def test_malformed_sparse_features_are_rejected(ids, weights):
    with pytest.raises(ValueError):
        sparse_weights(ids, weights)


class JointEncoder:
    sparse_enabled = True

    def __init__(self):
        self.calls = []

    def encode_features(self, texts):
        self.calls.append(tuple(texts))
        return {'dense': np.array([[1., 0.] for t in texts]),
                'sparse': [{25: 2.} if '제출' in t else {99: 1.} for t in texts]}


def test_joint_forward_is_cached_once_per_current_notice_and_query():
    encoder = JointEncoder()
    rec = record('원본 소스를 제출한다.', '식대 정산 기준')
    search = NoticeSearch(rec, CharacterTokenizer(), encoder)
    for method in ['hybrid_sparse', 'dense', 'sparse', 'hybrid_sparse']:
        result = search.search([20], queries=['소스 제출'], token_budget=100, method=method)
        assert result['source_tokens'] <= 100
        assert not result['coverage']['absence_verified']
    assert len(encoder.calls) == 2
    # No notice-document caching in the encoder or across NoticeSearch instances.
    second = NoticeSearch(record('다른 공고의 제출 조건'), CharacterTokenizer(), encoder)
    second.search([20], queries=['소스 제출'], token_budget=100, method='hybrid_sparse')
    assert len(encoder.calls) == 4


def test_sparse_miss_returns_no_candidates_and_does_not_certify_absence():
    search = NoticeSearch(record('식대 정산 기준'), CharacterTokenizer(), JointEncoder())
    result = search.search([20], queries=['소스 제출'], token_budget=100, method='sparse')
    assert result['spans'] == []
    assert result['diagnostics']['candidate_chunks'] == 0
    assert not result['coverage']['absence_verified']
    assert result['documents'][0]['unreturned_ranges']


@pytest.mark.parametrize('budget', [1, 20, 90])
def test_sparse_selection_preserves_exceptions_and_exact_source_budget(budget):
    rec = record('1. 제출 조건\n소스를 제출한다.\n다만 상용제품의 소스는 제출하지 않는다.',
                 '일반 자료 목록 및 식대 정산 기준')
    search = NoticeSearch(rec, CharacterTokenizer(), JointEncoder())
    for method in ['sparse', 'hybrid_sparse']:
        result = search.search([20], queries=['소스 제출'], token_budget=budget, method=method)
        assert sum(len(s['text']) for s in result['spans']) == result['source_tokens'] <= budget
        for span in result['spans']:
            assert span['text'] == rec['docs'][span['doc_index']]['text'][span['start']:span['end']]
        duty = [s for s in result['spans'] if s['doc_index'] == 0]
        if duty:
            assert '다만 상용제품' in duty[0]['text']


def test_sparse_mode_requires_explicitly_enabled_head_and_valid_feature_count():
    search = NoticeSearch(record('소스 제출'), CharacterTokenizer())
    with pytest.raises(ValueError, match='explicitly enabled'):
        search.search([20], method='sparse', token_budget=100)
    encoder = JointEncoder()
    encoder.encode_features = lambda texts: {'dense': np.ones((len(texts), 2)), 'sparse': []}
    search = NoticeSearch(record('소스 제출'), CharacterTokenizer(), encoder)
    with pytest.raises(ValueError, match='dimensions'):
        search.search([20], method='hybrid_sparse', token_budget=100)
