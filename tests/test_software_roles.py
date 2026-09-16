"""The source's actor and direct object constrain a claimed software action."""
import json

import pytest

from submission.pps.other_checks import sw_check
from submission.pps.software_facts import decide, followup_plan
from tests.test_software_facts import source, response


def evaluate(text, action, quote=None):
    rec, spans = source(text)
    return decide(rec, json.dumps(response(quote or text, object='software', action=action)), spans)


@pytest.mark.parametrize('text,action', [
    ('계약업체는 소프트웨어 설명서를 제공해야 한다.', 'provide'),
    ('계약업체는 소프트웨어 설명서를 먼저 제공해야 한다.', 'provide'),
    ('계약업체는 소프트웨어 개발 계획서를 작성해야 한다.', 'create'),
    ('계약업체는 소프트웨어 개발 계획서 및 설명서를 제공해야 한다.', 'provide'),
    ('계약업체는 소프트웨어 운영 매뉴얼을 수정해야 한다.', 'modify'),
    ('계약업체는 라이선스 계약서를 갱신해야 한다.', 'renew'),
    ('계약업체는 소프트웨어 개발 실적이 있어야 한다.', 'create'),
    ('계약업체는 소프트웨어 수정 역량을 입증해야 한다.', 'modify'),
    ('소프트웨어 설치 경험이 있는 자여야 한다.', 'install'),
    ('계약업체는 소프트웨어 개발 교육을 실시해야 한다.', 'create'),
    ('소프트웨어 개발 요구사항', 'create'),
    ('소프트웨어 운영 요구사항 목록', 'operate'),
    ('계약업체는 소프트웨어 개발 요구사항을 충족해야 한다.', 'create'),
    ('계약업체는 소프트웨어 요구사항 목록을 작성해야 한다.', 'create'),
    ('수강생은 소프트웨어를 개발해야 한다.', 'create'),
    ('수강생은 기존에 설치된 소프트웨어를 수정해야 한다.', 'modify'),
    ('계약업체와 협의한 수강생이 소프트웨어를 개발해야 한다.', 'create'),
    ('교육생이 소프트웨어를 수정해야 한다.', 'modify'),
    ('발주기관은 라이선스를 구매해야 한다.', 'provide'),
    ('수요기관이 소프트웨어를 설치해야 한다.', 'install'),
    ('계약업체는 수강생이 소프트웨어를 개발하도록 지도해야 한다.', 'create'),
    ('발주기관이 제공한 소프트웨어를 계약업체가 사용해야 한다.', 'provide'),
    ('이미 개발된 소프트웨어를 설명해야 한다.', 'create'),
    ('계약업체가 보유하고 있던 소프트웨어를 제공한 실적을 제출한다.', 'provide'),
    ('부품 PCB의 F/W 소스를 제출한다. 라이선스는 제공하지 않는다.', 'provide'),
    ('부품 PCB의 F/W 소스를 제출한다. 소프트웨어 개발은 면제한다.', 'provide'),
    ('라이선스는 제공하지 않으며 부품 PCB의 F/W 소스를 제출한다.', 'provide'),
])
def test_claim_about_a_different_role_or_object_cannot_force_a_sw_task(text, action):
    result = evaluate(text, action)
    assert result['value'] is None
    assert not result['actual_software_relations']
    assert result['semantic_audit']['relations'][0]['issues']
    # Preserve the erroneous claim for inspection and further source reading.
    assert result['facts']['relations'][0]['action'] == action
    assert result['facts']['relations'][0]['actor'] == 'contractor'
    assert 'witness_meaning' in followup_plan(result)['needs']


@pytest.mark.parametrize('text,action', [
    ('계약업체는 소프트웨어와 설명서를 제공해야 한다.', 'provide'),
    ('계약업체는 DEWESOFT 및 운영 매뉴얼을 납품해야 한다.', 'provide'),
    ('계약업체는 문서관리 소프트웨어를 개발해야 한다.', 'create'),
    ('계약업체는 설명서와 함께 소프트웨어를 제공해야 한다.', 'provide'),
    ('계약업체는 소프트웨어를 설명서와 함께 제공해야 한다.', 'provide'),
    ('계약업체는 교육용 소프트웨어를 개발해야 한다.', 'create'),
    ('계약업체는 수강생이 사용할 소프트웨어를 개발해야 한다.', 'create'),
    ('발주기관이 지정한 소프트웨어를 계약업체는 개발해야 한다.', 'create'),
    ('계약업체와 수강생이 공동으로 소프트웨어를 개발해야 한다.', 'create'),
    ('수강생은 계약업체와 함께 소프트웨어를 개발해야 한다.', 'create'),
    ('수강생에게 계약업체는 소프트웨어를 제공해야 한다.', 'provide'),
    ('계약업체는 발주기관에 소프트웨어를 제공해야 한다.', 'provide'),
    ('계약업체는 수강생이 개발한 소프트웨어를 수정해야 한다.', 'modify'),
    ('소프트웨어를 개발한 후 발주기관에 납품해야 한다.', 'create'),
    ('소프트웨어를 개발한 후에 발주기관에 납품해야 한다.', 'create'),
    ('소프트웨어를 개발한 뒤에 발주기관에 납품해야 한다.', 'create'),
    ('소프트웨어를 개발한 다음에 발주기관에 납품해야 한다.', 'create'),
    ('소프트웨어를 개발하여 발주기관에 납품해야 한다.', 'create'),
    ('소프트웨어 개발 실적을 제출하며 신규 소프트웨어를 개발해야 한다.', 'create'),
    ('설명서는 제공하지 않으며 소프트웨어를 납품해야 한다.', 'provide'),
    ('부품 PCB의 F/W 소스와 별도의 소프트웨어 사용권을 제공해야 한다.', 'provide'),
    ('부품 PCB의 F/W 소스를 제출한다. 별도의 사용권을 제공해야 한다.', 'provide'),
    ('계약업체는 소프트웨어를 납품해야 한다. 수강생은 소프트웨어를 개발한다.', 'provide'),
])
def test_independent_actual_work_and_shared_actor_are_preserved(text, action):
    result = evaluate(text, action)
    assert result['value'] == 1
    assert not result['semantic_audit']['relations'][0]['issues']


@pytest.mark.parametrize('text,quote,action', [
    ('수강생은 소프트웨어를 개발해야 한다.', '소프트웨어를 개발', 'create'),
    ('계약업체는 소프트웨어 설명서를 제공해야 한다.', '제공해야 한다.', 'provide'),
    ('소프트웨어 개발 실적을 제출해야 한다.', '소프트웨어 개발', 'create'),
    ('계약업체는 소프트웨어를 개발한다. 수강생은 소프트웨어를 개발한다.', '소프트웨어를 개발', 'create'),
])
def test_short_witness_cannot_hide_original_actor_object_or_qualification(text, quote, action):
    result = evaluate(text, action, quote=quote)
    assert result['value'] is None
    assert result['semantic_audit']['relations'][0]['issues']


@pytest.mark.parametrize('text', [
    '정보시스템 운영 실적을 제출해야 한다.',
    '정보시스템 구축 계획서를 작성해야 한다.',
    '수강생이 정보시스템을 운영해야 한다.',
    '발주기관은 라이선스를 구매해야 한다.',
    '소프트웨어 설치 경험이 있는 자여야 한다.',
    '소프트웨어 설치 교육을 실시해야 한다.',
])
def test_default_source_rule_uses_the_same_role_guard(text):
    rec, _ = source('소프트웨어사업자(컴퓨터관련서비스사업) 등록\n'+text)
    rec['meta']['업무구분'] = '일반용역'
    result = sw_check(rec)
    assert result['value'] is None
    assert not result['facts']['actual_work']


def test_separate_full_source_declaration_survives_an_invalid_model_relation():
    quote = '수강생은 소프트웨어를 개발해야 한다.'
    result = evaluate('본 사업은 소프트웨어 사업이다.\n'+quote, 'create', quote=quote)
    assert result['value'] == result['source_rule']['value'] == 1
    assert result['semantic_audit']['relations'][0]['issues']
    assert not result['actual_software_relations']


def test_ambiguous_quote_trace_keeps_each_actor_and_exact_source_coordinate():
    text = '계약업체는 소프트웨어를 개발한다. 수강생은 소프트웨어를 개발한다.'
    result = evaluate(text, 'create', quote='소프트웨어를 개발')
    actions = result['semantic_audit']['source_actions'][0]['actions']
    assert len(actions) == 1
    assert not actions[0]['passes_necessary_checks']
    contexts = actions[0]['contexts']
    assert len(contexts) == 2
    assert not contexts[0]['issues']
    assert 'source_action_has_a_different_explicit_actor' in contexts[1]['issues']
    for context in contexts:
        assert context['coordinate_space'] == 'original_document'
        assert context['doc_index'] == 0
        assert text[context['start']:context['end']] == context['quote']
        assert text[context['action_start']:context['action_end']] == context['action_quote'] == '개발'


def test_independent_action_trace_does_not_hide_a_rejected_handover():
    result = evaluate('부품 PCB F/W 소스를 제출한다. 사용권을 제공해야 한다.', 'provide')
    actions = result['semantic_audit']['source_actions'][0]['actions']
    assert result['value'] == 1
    assert [a['passes_necessary_checks'] for a in actions] == [False, True]
    assert actions[0]['contexts'][0]['action_quote'] == '제출'
    assert actions[1]['contexts'][0]['action_quote'] == '제공'


@pytest.mark.parametrize('separator', ['  ', '\n', '\t'])
@pytest.mark.parametrize('sequence', ['후', '후에', '다음에'])
def test_wrapping_cannot_turn_an_ordered_work_step_into_an_existing_result(separator, sequence):
    text = '소프트웨어를 개발한' + separator + sequence + ' 발주기관에 납품해야 한다.'
    result = evaluate(text, 'create')
    assert result['value'] == 1
    assert not result['semantic_audit']['relations'][0]['issues']
