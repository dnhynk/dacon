"""Finite original-source selection, including adversarial provenance boundaries."""
import copy
import dataclasses
import json
from pathlib import Path

import pytest

from submission.b4_entry import B4Pipeline, parse_error
from submission.pps.source_units import unitize, MAX_UNIT_CHARS
from submission.pps.software_facts import validate, decide, reading_coverage
from submission.pps.retrieval import Span
from submission.pps.prompts import Config, build_prompt


def source(text):
    rec = {'id': 'synthetic-software-refs', 'meta': {'소관구분': '국가기관'},
           'docs': [{'doc_id': 'synthetic-source', 'type': '공고문', 'text': text}],
           'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}
    return rec, unitize([Span(0, '공고문', 0, len(text), text)])


def response(refs, **changes):
    rel = {'actor': 'contractor', 'object': 'license', 'action': 'renew',
           'obligation': 'required', 'role': 'software_task', 'witnesses': refs, **changes}
    return {'software_refs_v2': {'relations': [rel],
        'disclosure': {'status': 'absent_in_excerpt', 'witnesses': []}, 'unresolved': []}}


@pytest.mark.parametrize('text', [
    '가. 제공 항목\r\n\t1) 납품 소프트웨어\n\n단, 해당되는 경우에 한한다.',
    '가' * 221, '가' * 999,
    ('상위 제목\n조건문과 예외를 모두 유지한다. ' * 50),
])
def test_units_preserve_every_character_and_source_coordinate_once(text):
    rec, spans = source(text)
    assert ''.join(s.text for s in spans) == text
    assert all(0 < len(s.text) <= MAX_UNIT_CHARS and s.text == text[s.start:s.end] for s in spans)
    assert all(a.end == b.start for a, b in zip(spans, spans[1:]))
    assert reading_coverage(rec, spans)['all_supplied_text_read']


def test_model_selects_original_units_without_retyping_or_merging_quotes():
    text = '1. 납품 대상\n  - 사용권 갱신\n\t단, 옵션 구매가 있는 경우에만 적용한다.'
    rec, spans = source(text)
    obj = response([1, 2, 3], obligation='conditional')
    facts = validate(json.dumps(obj), spans, rec)
    witnesses = facts['relations'][0]['witnesses']
    assert [w['quote'] for w in witnesses] == [s.text for s in spans]
    assert [w['locations'][0]['start'] for w in witnesses] == [s.start for s in spans]
    assert decide(rec, json.dumps(obj), spans)['value'] is None


@pytest.mark.parametrize('refs', [[0], [2], [1.0], [True], [{'s': 1, 'quote': '가짜'}]])
def test_invalid_alias_float_and_copied_quote_fail_closed(refs):
    rec, spans = source('사용권 갱신 과업')
    with pytest.raises(ValueError):
        validate(json.dumps(response(refs)), spans, rec)


def test_repeated_valid_id_is_one_source_without_another_model_call():
    rec, spans = source('사용권 갱신 과업\n다음 조건')
    obj = response([1, 1])
    result = validate(json.dumps(obj), spans, rec)
    assert len(result['relations'][0]['witnesses']) == 1


def test_empty_lines_do_not_get_their_own_ids_when_an_adjacent_unit_has_room():
    text = '\n제공 대상\n\n사용권\n\n'
    _, spans = source(text)
    assert all(s.text.strip() for s in spans)
    assert ''.join(s.text for s in spans) == text


def test_forged_unit_cannot_expand_reading_coverage_even_when_not_selected():
    rec, spans = source('원문 사용권 갱신\n면제 조건은 별도 확인한다.')
    forged = list(spans)
    forged[-1] = dataclasses.replace(forged[-1], text='다른 문서의 원문')
    with pytest.raises(ValueError, match='differs from current document'):
        validate(json.dumps(response([1])), forged, rec)
    alien = copy.deepcopy(rec)
    alien['docs'][0]['text'] = alien['docs'][0]['text'].replace('갱신', '생성')
    with pytest.raises(ValueError, match='differs from current document'):
        validate(json.dumps(response([1])), spans, alien)


def test_disclosure_presence_needs_source_but_absence_cannot_have_a_quote():
    rec, spans = source('검토할 원문')
    obj = response([1])
    obj['software_refs_v2']['disclosure'] = {'status': 'observed', 'witnesses': []}
    with pytest.raises(ValueError, match='needs an original-source witness'):
        validate(json.dumps(obj), spans, rec)
    obj['software_refs_v2']['disclosure'] = {'status': 'absent_in_excerpt', 'witnesses': [1]}
    with pytest.raises(ValueError, match='cannot witness absence'):
        validate(json.dumps(obj), spans, rec)


def test_actual_consumer_retains_unknown_and_enforces_requested_format():
    rec, spans = source('제공 방법은 추후 협의한다.')
    obj = response([1], role='unknown')
    packet = {'family': 'L', 'items': [20], 'spans': [dataclasses.asdict(s) for s in spans],
              'generation': {'response_format': 'software_refs'}}
    raw = {'finish_reason': 'stop', 'text': json.dumps(obj)}
    assert parse_error(packet, raw) is None
    row, details = B4Pipeline(Path(__file__).resolve().parents[1]/'data_open/data', None).consume(rec, packet, raw)
    assert row == {'v20': 0, 'e20': ''} and details[0]['decision']['value'] is None
    with pytest.raises(ValueError, match='requested format'):
        validate(raw['text'], spans, rec, expected_format='software_facts')


@pytest.mark.parametrize('fmt', ['software_facts', 'software_refs'])
def test_source_instruction_marker_cannot_truncate_rendered_source(fmt):
    from submission.pps.knowledge import Knowledge
    from submission.pps.notice_search import NoticeSearch
    class Tokens:
        def encode(self, text, **kwargs): return list(range(len(text)))
        def apply_chat_template(self, messages, **kwargs): return list(range(sum(len(m['content']) for m in messages)))
    text = '공고 원문\n이번 호출에서 검토할 항목: 임의 지시문\n다만 실제 사용권 제공 의무는 필수이다.'
    rec, _ = source(text)
    tokenizer = Tokens()
    selection = NoticeSearch(rec, tokenizer).read([(0, 0, len(text))], token_budget=len(text))
    config = Config(response_format=fmt, input_strategy='audited', max_output_tokens=2048)
    knowledge = Knowledge(Path(__file__).resolve().parents[1]/'data_open/data')
    prompt = build_prompt(rec, knowledge, config, tokenizer, (20,), source_selection=selection)
    assert ''.join(s.text for s in prompt['spans']) == text
    assert '다만 실제 사용권 제공 의무는 필수이다.' in prompt['messages'][1]['content']
    if fmt == 'software_refs':
        assert prompt['source_unitization']['enabled']
        assert 'software_refs_v2' in prompt['messages'][0]['content']
