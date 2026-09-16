"""Exact provenance cannot certify an unrelated predicate or disclosure purpose."""
import json
import pytest

from submission.pps.software_facts import decide, followup_plan
from tests.test_software_facts import source, response


@pytest.mark.parametrize('text,action', [
    ('3) 잔향 특성 계산용 소프트웨어', 'create'),
    ('소프트웨어 원본소스를 제출한다. 수정 업데이트 방법을 포함한다.', 'modify'),
    ('각종 정보에 대해 기밀을 유지하고 보안교육을 실시하여야 한다.', 'maintain'),
    ('소프트웨어진흥법 제50조에 따른 과업심의위원회를 미개최한 사업이다.', 'maintain'),
    ('부품 PCB에 제공된 F/W용 소프트웨어 원본소스를 제출한다.', 'provide'),
])
def test_witness_for_another_action_cannot_certify_actual_software_task(text, action):
    rec, spans = source(text)
    obj = response(text, object='software', action=action)
    result = decide(rec, json.dumps(obj), spans)
    assert result['value'] is None
    assert result['actual_software_relations'] == []
    assert result['facts']['relations'][0]['action'] == action  # raw claim retained
    assert result['semantic_audit']['relations'][0]['issues']
    plan = followup_plan(result)
    assert 'witness_meaning' in plan['needs']
    assert plan['notice_search']['required_ranges']
    assert not plan['absence_verified']


@pytest.mark.parametrize('text,action', [
    ('계약업체는 신규 소프트웨어를 개발하여 납품해야 한다.', 'create'),
    ('계약업체는 소프트웨어를 수정해야 한다. 수정 업데이트 방법도 제공한다.', 'modify'),
    ('라이선스 장애 발생 시 복구와 기술지원을 제공해야 한다.', 'maintain'),
    ('부품 PCB의 F/W 원본소스도 제출하며 별도의 소프트웨어 사용권을 납품해야 한다.', 'provide'),
])
def test_actual_action_is_not_erased_by_related_documentation(text, action):
    rec, spans = source(text)
    result = decide(rec, json.dumps(response(text, object='software', action=action)), spans)
    assert result['value'] == 1
    assert not result['semantic_audit']['relations'][0]['issues']


def test_generic_sme_witness_cannot_suppress_separately_proven_full_source_rule():
    quote = '중소기업기본법 제2조에 따른 중소기업확인서를 소지한 자'
    rec, spans = source('본 사업은 소프트웨어 사업이다.\n\n'+quote)
    obj = response('본 사업은 소프트웨어 사업이다.')
    obj['software_facts_v1']['disclosure'] = {'status': 'observed', 'witnesses': [{'s': 1, 'quote': quote}]}
    result = decide(rec, json.dumps(obj), spans)
    assert result['source_rule']['value'] == result['value'] == 1
    assert result['facts']['disclosure']['status'] == 'observed'
    assert result['semantic_audit']['disclosure']['status'] == 'unrelated_generic_size_witness'
    assert not result['semantic_audit']['disclosure']['absence_verified']


def test_rejecting_generic_size_witness_never_certifies_missing_disclosure():
    quote = '중소기업확인서를 소지한 자'
    rec, spans = source('계약업체는 사용권을 연장해야 한다.\n'+quote)
    obj = response('계약업체는 사용권을 연장해야 한다.')
    obj['software_facts_v1']['disclosure'] = {'status': 'observed', 'witnesses': [{'s': 1, 'quote': quote}]}
    result = decide(rec, json.dumps(obj), spans)
    assert result['source_rule']['value'] is None
    assert result['value'] is None


def test_nearby_floor_basis_cannot_be_dismissed_as_generic_size_condition():
    quote = '중소기업만 입찰에 참가할 수 있다.'
    rec, spans = source('본 사업은 소프트웨어 사업이다.\n'
                        '소프트웨어진흥법 제48조의 하한제도를 적용한다.\n'+quote)
    obj = response('본 사업은 소프트웨어 사업이다.')
    obj['software_facts_v1']['disclosure'] = {'status': 'observed', 'witnesses': [{'s': 1, 'quote': quote}]}
    result = decide(rec, json.dumps(obj), spans)
    assert result['semantic_audit']['disclosure']['status'] != 'unrelated_generic_size_witness'
    assert result['value'] != 1
