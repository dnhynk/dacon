import copy
import dataclasses
import json
from pathlib import Path

import pytest

from submission.b4_entry import B4Pipeline, parse_error
from submission.pps.prompts import Config, build_prompt, output_schema
from submission.pps.retrieval import Span
from submission.pps.software_facts import decide, validate, followup_plan


def source(text, *, complete=True):
    rec = {'id': 'synthetic-software-relations', 'meta': {'소관구분': '국가기관'},
        'docs': [{'doc_id': 'notice', 'type': '공고문', 'text': text}],
        'input_completeness': {'완전관측': complete}, 'dropped_doc_counts': {}}
    return rec, [Span(0, '공고문', 0, len(text), text)]


def response(quote, **overrides):
    relation = {'actor': 'contractor', 'object': 'license', 'action': 'renew',
        'obligation': 'required', 'role': 'software_task', 'witnesses': [{'s': 1, 'quote': quote}]}
    relation.update(overrides)
    return {'software_facts_v1': {'relations': [relation],
        'disclosure': {'status': 'absent_in_excerpt', 'witnesses': []}, 'unresolved': []}}


def evaluate(text, **overrides):
    rec, spans = source(text)
    return decide(rec, json.dumps(response(text, **overrides), ensure_ascii=False), spans)


def test_license_renewal_is_not_conditioned_on_new_code_or_hardware():
    result = evaluate('계약업체는 발주처의 사용권을 1년 연장해야 한다.')
    assert result['value'] == 1
    assert result['facts']['relations'][0]['action'] == 'renew'


def test_existing_full_source_rule_is_not_replaced_by_partial_model_read():
    quote = '본 사업은 소프트웨어 사업이다.'
    rec, _ = source(quote + '\n다음은 관련 없는 추가 자료이다.')
    spans = [Span(0, '공고문', 0, len(quote), quote)]
    result = decide(rec, json.dumps(response(quote)), spans)
    assert not result['coverage']['all_supplied_text_read']
    assert result['source_rule']['value'] == result['value'] == 1
    assert result['reason'] == 'independent_full_source_SW_rule'


@pytest.mark.parametrize('fields', [
    {'object': 'source_material', 'action': 'provide', 'role': 'hardware_ancillary'},
    {'object': 'firmware', 'action': 'integrate', 'role': 'hardware_ancillary'},
    {'object': 'documentation', 'action': 'provide'},
    {'object': 'software', 'action': 'use', 'role': 'internal_tool'},
    {'actor': 'trainee', 'action': 'create', 'role': 'trainee_practice'},
    {'obligation': 'conditional'}, {'obligation': 'negated'}, {'role': 'unknown'},
])
def test_partial_duties_are_preserved_without_proving_software_applicability(fields):
    result = evaluate('이 문장에서 주체와 대상의 구별을 검사한다.', **fields)
    assert result['value'] is None
    for key, value in fields.items():
        assert result['facts']['relations'][0][key] == value


def test_search_absence_and_ingestion_completeness_do_not_certify_reading():
    quote = '계약업체는 사용권을 1년 연장해야 한다.'
    rec, _ = source(quote + '\n다만 다음 절의 대체 조건을 적용한다.')
    spans = [Span(0, '공고문', 0, len(quote), quote)]
    result = decide(rec, json.dumps(response(quote)), spans)
    assert result['value'] is None
    assert result['reason'] == 'software_absence_requires_remaining_source_read'
    assert result['coverage']['documents'][0]['unread']
    plan = followup_plan(result)
    assert plan['notice_read']['remaining_ranges']
    assert not plan['absence_verified'] and not plan['requests_executed']


def test_unknown_scope_requests_parent_scope_and_preserves_original_duty():
    result = evaluate('계약업체는 소스 자료를 인도한다.', object='source_material', action='provide', role='unknown')
    plan = followup_plan(result)
    assert 'procurement_scope' in plan['needs']
    assert any('상위 납품 대상' in q for q in plan['notice_search']['queries'])
    assert plan['notice_search']['required_ranges']


def test_disclosure_quote_cannot_be_missing_or_invented():
    quote = '계약업체는 사용권을 1년 연장해야 한다.'
    rec, spans = source(quote)
    obj = response(quote)
    obj['software_facts_v1']['disclosure']['status'] = 'observed'
    with pytest.raises(ValueError, match='needs an original-source witness'):
        validate(json.dumps(obj), spans, rec)
    obj['software_facts_v1']['disclosure']['witnesses'] = [{'s': 1, 'quote': '하한제도를 적용한다.'}]
    with pytest.raises(ValueError, match='not an exact quote'):
        validate(json.dumps(obj), spans, rec)


def test_witness_cannot_borrow_text_from_another_source_or_notice():
    rec, spans = source('원문 사용권 인도 의무.')
    obj = response('다른 문서의 사용권 의무.')
    with pytest.raises(ValueError, match='not an exact quote'):
        validate(json.dumps(obj), spans, rec)
    obj = response(spans[0].text)
    alien = copy.deepcopy(rec)
    alien['docs'][0]['text'] = '다른 내용'
    with pytest.raises(ValueError, match='differs from current document'):
        validate(json.dumps(obj), spans, alien)


def test_schema_cannot_smuggle_a_judgment_or_float_source_reference():
    rec, spans = source('정확한 인용')
    obj = response('정확한 인용')
    obj['v20'] = 1
    with pytest.raises(ValueError, match='schema'):
        validate(json.dumps(obj), spans, rec)
    obj.pop('v20')
    obj['software_facts_v1']['relations'][0]['witnesses'][0]['s'] = 1.0
    with pytest.raises(ValueError, match='source witness'):
        validate(json.dumps(obj), spans, rec)
    with pytest.raises(ValueError, match='only item20'):
        output_schema('software_facts', len(spans), (19, 20))


def test_repeated_exact_quote_retains_every_possible_location():
    quote = '자료 인계.'
    rec, spans = source(quote + '\n' + quote)
    result = validate(json.dumps(response(quote)), spans, rec)
    locations = result['relations'][0]['witnesses'][0]['locations']
    assert [(p['start'], p['end']) for p in locations] == [(0, len(quote)), (len(quote)+1, 2*len(quote)+1)]


def test_actual_consumer_records_unknown_separately_from_public_zero():
    rec, spans = source('소스 인계 범위는 추후 정한다.')
    obj = response(spans[0].text, object='source_material', action='provide', role='unknown')
    packet = {'family': 'L', 'items': [20], 'spans': [dataclasses.asdict(s) for s in spans],
              'generation': {'response_format': 'software_facts'}}
    raw = {'finish_reason': 'stop', 'text': json.dumps(obj, ensure_ascii=False)}
    assert parse_error(packet, raw) is None
    data = Path(__file__).resolve().parents[1] / 'data_open/data'
    row, details = B4Pipeline(data, None).consume(rec, packet, raw)
    assert row == {'v20': 0, 'e20': ''}
    assert details[0]['decision']['value'] is None
    assert details[0]['decision']['facts']['relations'][0]['object'] == 'source_material'


def test_fact_prompt_preserves_fixed_sources_and_excludes_model_verdict():
    from submission.pps.knowledge import Knowledge
    from submission.pps.notice_search import NoticeSearch
    class Tokens:
        def encode(self, text, **kwargs): return list(range(len(text)))
        def apply_chat_template(self, messages, **kwargs): return list(range(sum(len(m['content']) for m in messages)))
    text = '계약업체는 사용권을 연장하고 기술지원을 제공해야 한다.'
    rec, spans = source(text)
    tokenizer = Tokens()
    selection = NoticeSearch(rec, tokenizer).read([(0, 0, len(text))], token_budget=len(text))
    cfg = Config(response_format='software_facts', input_strategy='audited', v20_fact_contract=True,
                 rubric_version='v6', max_output_tokens=2048)
    knowledge = Knowledge(Path(__file__).resolve().parents[1] / 'data_open/data')
    prompt = build_prompt(rec, knowledge, cfg, tokenizer, (20,), source_selection=selection)
    assert prompt['spans'] == spans
    assert '"judgments"' not in prompt['messages'][0]['content']
    assert 'software_facts_v1' in prompt['messages'][0]['content']
    assert prompt['response_format'] == 'software_facts'
