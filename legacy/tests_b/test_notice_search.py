"""Synthetic source/coverage contracts; retrieval quality is measured separately."""
import copy
import numpy as np
import pytest

from submission.pps.notice_search import NoticeSearch, factual_queries, merge_ranges


class CharacterTokenizer:
    def encode(self, text, **kwargs):
        return list(text)


def record(*texts):
    return {'id': 'synthetic', 'meta': {'title': '연구 사업'},
            'docs': [{'doc_id': f'doc{i}', 'type': '공고문' if i == 0 else '과업지시서', 'text': t}
                     for i, t in enumerate(texts)], 'input_completeness': {'complete': True}}


def test_source_read_preserves_document_offsets_polarity_and_gaps():
    rec = record('동일 문구\n중간 조건을 생략했다\n동일 문구', '동일 문구')
    search = NoticeSearch(rec, CharacterTokenizer())
    result = search.read([(0, 0, 5), (0, 19, len(rec['docs'][0]['text'])), (1, 0, 5)], token_budget=100)
    assert len(result['spans']) == 3
    for s in result['spans']:
        assert s['text'] == rec['docs'][s['doc_index']]['text'][s['start']:s['end']]
    assert result['coverage']['absence_verified'] is False
    assert not result['coverage']['all_provided_text_returned']
    assert result['documents'][0]['unreturned_ranges']
    with pytest.raises(ValueError, match='exceeds'):
        search.read([(0, 0, len(rec['docs'][0]['text']))], token_budget=3)


def test_dense_discovers_actual_task_despite_unrelated_title_and_no_lexical_match():
    class Encoder:
        def __init__(self):
            self.calls = []
        def encode(self, texts):
            self.calls.append(tuple(texts))
            return np.array([[1., 0.] if '기기끼리' in t or t == '호환 조건' else [0., 1.] for t in texts])
    rec = record('회계 정산 절차 ' * 70, '기기끼리 연결되어 작동할 수 있어야 한다.')
    encoder = Encoder()
    search = NoticeSearch(rec, CharacterTokenizer(), encoder)
    result = search.search([20], queries=['호환 조건'], method='hybrid', token_budget=100)
    assert any(s['doc_index'] == 1 for s in result['spans'])
    assert result['source_tokens'] <= 100
    calls = len(encoder.calls)
    assert result == search.search([20], queries=['호환 조건'], method='hybrid', token_budget=100)
    assert len(encoder.calls) == calls  # current-notice cache only


def test_context_bundle_includes_heading_table_and_adjacent_exception():
    text = ('3. 제출 서류\n항목 | 제출 시점\n' + '기타 자료 | 나중에\n' * 30 +
            '기술지원확약서 | 입찰 전에 제출\n다만 제조사가 직접 입찰하면 제출하지 않는다.\n4. 다른 조건\n')
    search = NoticeSearch(record(text), CharacterTokenizer())
    target = next(i for i, s in enumerate(search.chunks) if '기술지원확약서' in s.text)
    expanded = search.read(search._contexts[target], token_budget=1000)
    shown = '\n'.join(s['text'] for s in expanded['spans'])
    assert '3. 제출 서류' in shown and '항목 | 제출 시점' in shown
    assert '다만 제조사가 직접 입찰하면 제출하지 않는다.' in shown
    with pytest.raises(ValueError, match='exceeds'):
        search.read(search._contexts[target], token_budget=20)


def test_no_results_or_complete_index_does_not_prove_absence_or_reading_order():
    search = NoticeSearch(record('알 수 없는 조건과 누락된 제안요청서 참조'), CharacterTokenizer())
    result = search.search([20], method='lexical', token_budget=1)
    assert result['spans'] == []
    assert result['coverage']['indexed_document_count'] == 1
    assert result['coverage']['returned_document_count'] == 0
    assert result['coverage']['absence_verified'] is False
    assert result['coverage']['reading_order_verified'] is False
    assert result['coverage']['referenced_document_completeness'] == 'not_verified_by_search'


@pytest.mark.parametrize('budget', [30, 100, 600])
def test_all_search_arms_obey_actual_source_budget_and_preserve_input(budget):
    class Encoder:
        def encode(self, texts):
            return np.array([[1., 0.] for _ in texts])
    rec = record(('3. 제품 규격\n규격: 동등 이상 납품 가능\n다만 연결 규격은 필수이다.\n' * 30),
                 '소프트웨어 사용권을 제공해야 한다.')
    before = copy.deepcopy(rec)
    search = NoticeSearch(rec, CharacterTokenizer(), Encoder())
    for method in ('current', 'lexical', 'dense', 'hybrid'):
        result = search.search([9, 20], method=method, token_budget=budget)
        assert sum(len(s['text']) for s in result['spans']) == result['source_tokens'] <= budget
        assert result['coverage']['absence_verified'] is False
    assert rec == before


def test_range_contract_rejects_cross_document_and_invented_offsets():
    docs = record('제출하지 않는다')['docs']
    for ranges in ([(2, 0, 1)], [(0, -1, 2)], [(0, 0, 999)], [(False, 0, 1)]):
        with pytest.raises(ValueError):
            merge_ranges(ranges, docs)


def test_refinement_keeps_duty_witness_and_accounts_for_new_reading():
    rec = record('소스 자료를 인계한다.', '다만 신규 개발은 요구하지 않는다.')
    search = NoticeSearch(rec, CharacterTokenizer())
    prior = search.read([(0, 0, len(rec['docs'][0]['text']))], token_budget=50)
    result = search.refine(prior, [20], queries=['신규 개발 요구하지'],
        required_ranges=[(0, 0, len(rec['docs'][0]['text']))], method='lexical')
    assert any('신규 개발은 요구하지' in s['text'] for s in result['spans'])
    assert any('소스 자료를 인계' in s['text'] for s in result['spans'])
    assert result['source_tokens'] <= prior['source_token_budget']
    assert result['diagnostics']['refinement']['cumulative_unique_source_tokens'] >= result['source_tokens']
    assert not result['coverage']['absence_verified']
    prior['record_id'] = 'other-notice'
    with pytest.raises(ValueError, match='same notice'):
        search.refine(prior, [20], queries=['과업'], method='lexical')


def test_mandatory_witness_cannot_be_silently_dropped_to_fit():
    search = NoticeSearch(record('부정과 조건이 포함된 원문'), CharacterTokenizer())
    with pytest.raises(ValueError, match='Required source witnesses'):
        search.search([20], queries=['원문'], method='lexical', token_budget=1,
                      required_ranges=[(0, 0, 10)], selection_policy='facet_cover')


def test_facet_cover_diversifies_repeated_queries_under_exact_source_budget():
    search = NoticeSearch(record('소스 제출', '소스 사본', '조건 면제'), CharacterTokenizer())
    ranges, chosen = search._facet_cover([[[0, 1, 2], [2, 1, 0]]], [0, 1, 2], (), 10, False)
    assert set(chosen) == {0, 2}
    assert search.token_cost(ranges) == 10
    assert any('면제' in q or '예외' in q for q in factual_queries([19, 20]))
    with pytest.raises(ValueError):
        factual_queries([0])


@pytest.mark.parametrize('corruption', ['other_record', 'other_document', 'shift', 'text', 'budget', 'overlap'])
def test_prompt_boundary_rejects_tampered_search_evidence(corruption):
    from submission.pps.prompts import verified_search_spans
    rec = record('입찰 전에 제출할 필요가 없다.')
    tokenizer = CharacterTokenizer()
    selection = NoticeSearch(rec, tokenizer).read([(0, 0, len(rec['docs'][0]['text']))], token_budget=100)
    assert verified_search_spans(rec, selection, tokenizer)[0].text == rec['docs'][0]['text']
    if corruption == 'other_record':
        selection['record_id'] = 'another notice'
    elif corruption == 'other_document':
        selection['documents'][0]['doc_sha256'] = 'wrong'
    elif corruption == 'shift':
        selection['spans'][0]['start'] = 1
    elif corruption == 'text':
        selection['spans'][0]['text'] = '입찰 전에 반드시 제출한다.'
    elif corruption == 'budget':
        selection['source_token_budget'] = 1
    else:
        selection['spans'].append(selection['spans'][0].copy())
    with pytest.raises(ValueError):
        verified_search_spans(rec, selection, tokenizer)
