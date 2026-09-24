"""Token-vector matching is directional relevance with bounded source context."""
import numpy as np
import pytest

from submission.pps.embeddings import colbert_similarity
from submission.pps.notice_search import NoticeSearch
from tests.test_notice_search import CharacterTokenizer, record


def test_maxsim_averages_query_matches_and_is_directional():
    query=np.array([[1.,0.],[0.,1.]],dtype=np.float32)
    doc=np.array([[1.,0.]],dtype=np.float32)
    assert colbert_similarity(query,doc)==.5
    assert colbert_similarity(doc,query)==1.
    # Duplicate query tokens still contribute to the query mean.
    assert colbert_similarity(np.array([[1.,0.],[1.,0.],[0.,1.]]),doc)==pytest.approx(2/3)


def test_negative_matches_are_not_clamped_to_zero_or_called_probability():
    assert colbert_similarity(np.array([[1.,0.]]),np.array([[-1.,0.]]))==-1.


@pytest.mark.parametrize('query,document',[
    (np.empty((0,2)),np.ones((1,2))),
    (np.ones((1,2)),np.empty((0,2))),
    (np.ones((1,2)),np.ones((1,3))),
    (np.ones(2),np.ones((1,2))),
    (np.array([[np.nan,0.]]),np.ones((1,2))),
    (np.ones((1,2)),np.array([[np.inf,0.]])),
    (np.array([[1,0]]),np.ones((1,2))),
])
def test_invalid_token_matrices_fail_closed(query,document):
    with pytest.raises(ValueError,match='ColBERT'):
        colbert_similarity(query,document)


class TokenEncoder:
    sparse_enabled=True
    colbert_enabled=True

    def __init__(self):self.calls=[]

    def encode_features(self,texts):
        self.calls.append(tuple(texts))
        vectors=[np.array([[1.,0.]]) if '제출' in text else np.array([[0.,1.]]) for text in texts]
        return {'dense':np.array([[1.,0.] for text in texts]),
                'sparse':[{25:1.} if '제출' in text else {50:1.} for text in texts],
                'colbert':vectors}


def test_shared_forward_and_scores_cache_only_inside_the_current_notice():
    encoder=TokenEncoder()
    search=NoticeSearch(record('자료를 제출한다.','식대 정산 기준'),CharacterTokenizer(),encoder)
    for method in ('hybrid_colbert','colbert','hybrid_sparse','dense','colbert'):
        out=search.search([20],queries=['소스 제출'],token_budget=120,method=method)
        assert out['source_tokens']<=120 and not out['coverage']['absence_verified']
    assert len(encoder.calls)==2
    assert search.colbert_receipt['scored_query_chunk_pairs']==len(search.chunks)
    NoticeSearch(record('다른 공고의 제출 의무'),CharacterTokenizer(),encoder).search(
        [20],queries=['소스 제출'],token_budget=120,method='colbert')
    assert len(encoder.calls)==4


@pytest.mark.parametrize('budget',[1,20,90])
def test_original_context_and_exception_words_are_charged_to_the_same_budget(budget):
    rec=record('1. 제출 조건\n소스를 제출한다.\n다만 상용제품의 소스는 제출하지 않는다.',
               '식대 정산 기준')
    search=NoticeSearch(rec,CharacterTokenizer(),TokenEncoder())
    for method in ('colbert','hybrid_colbert'):
        out=search.search([20],queries=['소스 제출'],token_budget=budget,method=method)
        assert sum(len(s['text']) for s in out['spans'])==out['source_tokens']<=budget
        for span in out['spans']:
            assert span['text']==rec['docs'][span['doc_index']]['text'][span['start']:span['end']]
            if span['doc_index']==0:assert '다만 상용제품' in span['text']


def test_colbert_requires_an_enabled_head_and_valid_normalized_features():
    with pytest.raises(ValueError,match='explicitly enabled'):
        NoticeSearch(record('소스 제출'),CharacterTokenizer()).search([20],token_budget=50,method='colbert')
    encoder=TokenEncoder()
    encoder.encode_features=lambda texts:{'dense':np.ones((len(texts),2)),
        'sparse':[{} for _ in texts],'colbert':[np.zeros((1,2)) for _ in texts]}
    with pytest.raises(ValueError,match='normalized ColBERT'):
        NoticeSearch(record('소스 제출'),CharacterTokenizer(),encoder).search([20],token_budget=50,method='colbert')
