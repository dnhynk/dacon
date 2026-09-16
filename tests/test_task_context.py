"""Source-group and bounded reading contracts, independent of model judgments."""
import copy

import numpy as np
import pytest

from submission.pps.retrieval import Span
from submission.pps.source_units import unitize
from submission.pps.task_context import TaskContextSearch, field_groups, render_groups, source_fields
from tests.test_notice_search import CharacterTokenizer, record


def all_units(rec):
    return unitize([Span(i, d['type'], 0, len(d['text']), d['text'])
                    for i, d in enumerate(rec['docs'])])


def test_groups_keep_complete_label_value_and_continuation_separate_from_identity():
    text = '용역량: 폐기물 수집(콘크리트 650톤, 혼합\n폐기물 190톤)\n입찰보증금은 면제한다.'
    rec = record(text)
    before = copy.deepcopy(rec)
    units = all_units(rec)
    groups = field_groups(rec, units)
    assert len(groups) == 1 and groups[0]['selected_units'] == [1, 2]
    assert not groups[0]['whole_contract_identity_certified']
    assert groups[0]['text'] == text[:text.index('\n입찰')]
    assert field_groups(rec, units[:1]) == []
    assert field_groups(rec, units[1:]) == []
    assert 'S1,S2' in render_groups(groups)
    assert groups[0]['text'] not in render_groups(groups)  # Addresses do not repeat raw-source words.
    assert rec == before


def test_conflicting_documents_and_repeated_occurrences_are_not_collapsed():
    rec = record('용역개요: 공원 수목 현황 조사\n용역개요: 공원 수목 현황 조사',
                 '용역개요: 조사 자료를 사용한 소프트웨어 개발')
    groups = field_groups(rec, all_units(rec))
    assert len(groups) == 3
    second = rec['docs'][0]['text'].index('\n') + 1
    assert [(g['doc_index'], g['start']) for g in groups] == [(0, 0), (0, second), (1, 0)]
    assert all(not g['whole_contract_identity_certified'] for g in groups)


def test_metadata_document_title_and_unoffered_scope_do_not_become_a_group():
    rec = record('과 업 내 용 서\n용역량: 900톤', '용역개요: 폐기물 수집 운반')
    assert field_groups(rec, all_units(record(rec['docs'][0]['text']))) == []
    fields = source_fields(rec)
    assert len(fields) == 1 and fields[0]['doc_index'] == 1
    shown = render_groups([])
    assert '부재 확인이 아니다' in shown


def test_invalid_source_is_rejected_before_groups_are_offered():
    rec = record('용역명: 연구 자료 분석')
    with pytest.raises(ValueError, match='differs'):
        field_groups(rec, [Span(0, '공고문', 0, 11, '용역명: 잘못된 내용')])


def test_group_hints_preserve_source_units_and_do_not_shrink_the_output_contract():
    import json
    from submission.pps.catalog_scope import prompt, schema
    class Tokenizer(CharacterTokenizer):
        def apply_chat_template(self, messages, **kwargs):
            return list(json.dumps(messages, ensure_ascii=False))
    rec = record('용역량: 폐기물 처리(콘크리트\n900톤)')
    tokenizer = Tokenizer()
    tool = TaskContextSearch(rec, tokenizer)
    selected = tool.read([(0, 0, len(rec['docs'][0]['text']))], token_budget=100)
    before = copy.deepcopy((rec, selected))
    plain = prompt(rec, selected, tokenizer, {}, explain_contract=True)
    grouped = prompt(rec, selected, tokenizer, {}, explain_contract=True, task_groups=True)
    assert plain['spans'] == grouped['spans']
    assert plain['messages'][0] == grouped['messages'][0]
    assert grouped['messages'][1]['content'].startswith(plain['messages'][1]['content'])
    assert plain['source_unitization'] == grouped['source_unitization']
    assert schema(len(plain['spans'])) == schema(len(grouped['spans']))
    assert (rec, selected) == before


def test_document_vector_cache_is_bound_to_exact_notice_and_verifies_saved_bytes(tmp_path):
    from tools.compare_task_context import document_vectors
    class Encoder:
        receipt = dict(model='BAAI/bge-m3', required_revision='fixed', dtype='float32',
                       pooling='CLS_L2', tokenizer_sha256='tokenizer', max_length=512)
        calls = 0
        def encode(self, texts):
            self.calls += 1
            values = np.zeros((len(texts), 1024), dtype=np.float32)
            values[:, 0] = 1.
            return values
    encoder = Encoder()
    rec = record('용역명: 연구자료 분석')
    first = TaskContextSearch(rec, CharacterTokenizer())
    receipt = document_vectors(first, encoder, tmp_path / 'first')
    second = TaskContextSearch(rec, CharacterTokenizer())
    assert document_vectors(second, encoder, tmp_path / 'second', tmp_path / 'first')['reused']
    assert encoder.calls == 1 and np.array_equal(first.vectors, second.vectors)
    changed = TaskContextSearch(record('용역명: 연구자료 분석 및 소프트웨어 개발'), CharacterTokenizer())
    with pytest.raises(ValueError, match='current notice'):
        document_vectors(changed, encoder, tmp_path / 'changed', tmp_path / 'first')
    path = next((tmp_path / 'first').glob('*.npy'))
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='current notice'):
        document_vectors(first, encoder, tmp_path / 'corrupt', tmp_path / 'first')


@pytest.mark.parametrize('method', ('lexical', 'dense', 'hybrid'))
@pytest.mark.parametrize('budget', (1, 100, 500))
def test_task_search_keeps_all_selected_words_in_the_same_source_budget(method, budget):
    class Encoder:
        def encode(self, texts):
            return np.asarray([[1., 0.] for _ in texts])
    rec = record('입찰 안내\n용역개요: 폐기물 수집 및 처리(폐콘크리트\n900톤)\n'
                 '다만 지정폐기물은 대상에서 제외한다.\n' + '대금 지급 조건을 확인한다.\n' * 35)
    before = copy.deepcopy(rec)
    tool = TaskContextSearch(rec, CharacterTokenizer(), Encoder())
    selected = tool.select(budget, method=method)
    assert selected['source_tokens'] <= budget
    for s in selected['spans']:
        assert s['text'] == rec['docs'][s['doc_index']]['text'][s['start']:s['end']]
    assert not selected['coverage']['absence_verified']
    assert not selected['diagnostics']['task_context']['field_identity_certified']
    assert rec == before
