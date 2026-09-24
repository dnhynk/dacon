"""Query deduplication must not erase a condition that distinguishes products."""
import re

import pytest

from submission.pps.notice_search import NoticeSearch
from submission.pps.purchase_reading import catalog_source_queries
from tests.test_catalog_candidates import CharacterTokenizer


def reading(left, right):
    a = f'품명 | 단위 | 수량\n{left} | 개 | 2\n'
    b = f'품명 | 단위 | 수량\n{right} | 개 | 2\n'
    gap = '\n아직 읽지 않은 중간 문단\n'
    text = a + gap + b
    record = dict(id='synthetic', meta={}, docs=[dict(doc_id='D0', type='규격서', text=text)])
    seed = NoticeSearch(record, CharacterTokenizer()).read(
        [(0, 0, len(a)), (0, len(a)+len(gap), len(text))], token_budget=1000)
    return record, seed


@pytest.mark.parametrize('policy', ['units', 'context_blocks', 'table_candidates'])
@pytest.mark.parametrize('left,right', [
    ('보호복(남성용)', '보호복(여성용)'),
    ('측정장치(실내용)', '측정장치(실외용)'),
    ('온도계(세금 포함)', '온도계(세금 별도)'),
    ('압력계 2 3원', '압력계 23원'),
])
def test_qualifiers_and_internal_number_spacing_are_not_duplicate_keys(policy, left, right):
    record, seed = reading(left, right)
    queries = catalog_source_queries(record, seed, query_policy=policy)
    assert any(left in q['query'] for q in queries)
    assert any(right in q['query'] for q in queries)
    for q in queries:
        ref = q['evidence']
        assert ref['text'] == record['docs'][0]['text'][ref['start']:ref['end']]
        assert '아직 읽지 않은' not in q['query']


@pytest.mark.parametrize('policy', ['units', 'context_blocks', 'table_candidates'])
def test_exact_duplicate_queries_stay_deduplicated_and_count_cap_is_preserved(policy):
    record, seed = reading('측정장치(실내용)', '측정장치(실내용)')
    queries = catalog_source_queries(record, seed, query_policy=policy)
    keys = [re.sub(r'\s+', ' ', q['query']).strip() for q in queries]
    assert len(keys) == len(set(keys))
    assert len(catalog_source_queries(record, seed, query_policy=policy, max_queries=1)) == 1


@pytest.mark.parametrize('policy', ['units', 'context_blocks', 'table_candidates'])
def test_identity_fix_does_not_turn_an_isolated_department_placeholder_into_a_query(policy):
    text = '([부서])'
    record = dict(id='synthetic', meta={}, docs=[dict(doc_id='D0', type='규격서', text=text)])
    seed = NoticeSearch(record, CharacterTokenizer()).read([(0, 0, len(text))], token_budget=100)
    assert catalog_source_queries(record, seed, query_policy=policy) == []
