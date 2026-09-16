"""Source dependencies are atomic and never certify interpretation or absence."""
import copy

from submission.pps.catalog_condition_context import expand
from submission.pps.catalog_condition_review import _covers
from submission.pps.notice_search import NoticeSearch
from submission.pps.prompts import verified_search_spans
from tests.test_catalog_condition_review import setup
from tests.test_notice_search import CharacterTokenizer


def initial(rec, phrase, budget):
    text = rec['docs'][0]['text']
    start = text.index(phrase)
    return NoticeSearch(rec, CharacterTokenizer()).read([(0, start, start+len(phrase))], token_budget=budget)


def test_missing_other_document_permission_and_local_task_are_added_without_mutation():
    rec, _, knowledge = setup()
    rec['docs'].append({'doc_id': 'permit', 'type': '규격서',
        'text': '가. 납품 범위\n상기 규격과 동등한 제품을 허용한다.\n다만 명시된 연결 규격은 필수이다.'})
    prior = initial(rec, 'CPU 아키텍처: ARM', 500)
    before = copy.deepcopy((rec, prior))
    result, audit = expand(rec, prior, CharacterTokenizer(), knowledge)
    assert result['source_tokens'] <= prior['source_token_budget']
    shown = '\n'.join(s['text'] for s in result['spans'])
    assert '품명: 컴퓨터서버' in shown and rec['docs'][1]['text'] in shown
    spans = verified_search_spans(rec, result, CharacterTokenizer())
    for ev in audit['permission_sources']:
        assert _covers(rec, spans, list(range(1, len(spans)+1)), ev)
    assert audit['status'] == 'complete_dependencies_fit'
    assert not result['coverage']['absence_verified'] and not audit['semantics_certified']
    assert (rec, prior) == before
    assert expand(rec, prior, CharacterTokenizer(), knowledge) == (result, audit)


def test_excess_dependency_budget_abstains_instead_of_clipping_a_condition():
    rec, _, knowledge = setup(extra='동등한 제품을 허용하되 연결 규격은 반드시 충족하여야 한다.')
    prior = initial(rec, 'CPU 아키텍처: ARM', 14)
    result, audit = expand(rec, prior, CharacterTokenizer(), knowledge)
    assert result is None and audit['status'] == 'required_dependencies_exceed_budget'
    assert audit['required_source_tokens'] > prior['source_token_budget']
    assert audit['permission_sources'] and audit['observations']


def test_requirement_header_is_retained_but_contract_scope_is_not_inferred():
    text = '요구사항ID\nABC-001\n요구사항명\n신규 영상 제작\n모든 영상에 기관 로고를 넣는다.\n요구사항ID\nABC-002\n요구사항명\n기존 영상 편집\n편집만 한다.'
    rec, _, knowledge = setup(text, name='동영상제작서비스', code='8213160301')
    prior = initial(rec, '모든 영상에 기관 로고를 넣는다.', 400)
    result, audit = expand(rec, prior, CharacterTokenizer(), knowledge)
    shown = '\n'.join(s['text'] for s in result['spans'])
    assert '요구사항ID\nABC-001\n요구사항명\n신규 영상 제작' in shown
    frames = [f for o in audit['observations'] for f in o['requirement_frames']]
    assert frames and all(not f['scope_certified'] for f in frames)
    assert not audit['semantics_certified']


def test_household_word_does_not_hide_a_real_hypothesis_on_the_same_line():
    rec, _, knowledge = setup(extra='가정보호 교육으로 변경한다고 가정하면 다른 규격을 적용한다.')
    result, audit = expand(rec, initial(rec, 'CPU 아키텍처: ARM', 400), CharacterTokenizer(), knowledge)
    hypotheses = [ev for ev in audit['permission_sources'] if '가정하면' in ev['text']]
    assert hypotheses and result is not None
