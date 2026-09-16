"""A model's required-action enum cannot reverse the original predicate."""
import json

import pytest

from submission.pps.other_checks import sw_check
from submission.pps.software_facts import decide, followup_plan
from tests.test_software_facts import source, response


def decision(text, action, *, quote=None, obj='software'):
    rec, spans = source(text)
    return decide(rec, json.dumps(response(quote or text, object=obj, action=action)), spans)


@pytest.mark.parametrize('text,action,obj', [
    ('계약업체는 소프트웨어를 개발하지 않는다.', 'create', 'software'),
    ('계약업체는 소프트웨어를 수정할 수 있다.', 'modify', 'software'),
    ('계약업체는 사용권을 연장할 의무가 없다.', 'renew', 'license'),
    ('정보시스템 운영은 수행하지 않는다.', 'operate', 'software'),
    ('사업자등록증을 제출해야 한다.', 'renew', 'license'),
    ('소프트웨어를 설치하지 않아도 된다.', 'install', 'software'),
    ('사용권 제공은 선택사항이다.', 'provide', 'license'),
    ('소프트웨어 유지보수 의무는 면제한다.', 'maintain', 'software'),
    ('소프트웨어를 안 개발한다.', 'create', 'software'),
    ('필요한 경우에만 소프트웨어를 수정한다.', 'modify', 'software'),
    ('소프트웨어 수정 여부는 추후 협의한다.', 'modify', 'software'),
    ('소프트웨어를 수정하는 부분이 있으면 방법을 설명한다.', 'modify', 'software'),
    ('소프트웨어 개발 의무는 철회한다.', 'create', 'software'),
    ('라이선스 갱신은 필수가 아니다.', 'renew', 'license'),
    ('소프트웨어 설치는 요구하지 아니한다.', 'install', 'software'),
    ('소프트웨어 개발과 설치는 수행하지 않는다.', 'create', 'software'),
    ('소프트웨어를 개발하지 않으며 장비를 납품해야 한다.', 'create', 'software'),
    ('계약업체는 소프트웨어를 보유한 회사다.', 'provide', 'software'),
    ('계약업체는 사업자등록증을 갱신해야 한다.', 'renew', 'license'),
    ('이 문서는 소프트웨어 사용 안내서이다.', 'operate', 'software'),
    ('소프트웨어 설치 방법을 제공해야 한다.', 'install', 'software'),
    ('소프트웨어를 개발할 수 있도록 장비를 제공해야 한다.', 'create', 'software'),
])
def test_unsupported_required_action_is_retained_as_unresolved(text, action, obj):
    result = decision(text, action, obj=obj)
    assert result['value'] is None
    assert not result['actual_software_relations']
    assert result['semantic_audit']['relations'][0]['issues']
    assert result['facts']['relations'][0]['obligation'] == 'required'
    assert not result['semantic_audit']['passing_is_semantic_certification']
    assert 'witness_meaning' in followup_plan(result)['needs']


@pytest.mark.parametrize('text,action', [
    ('소프트웨어를 개발하며 장비 구매는 요구하지 않는다.', 'create'),
    ('장비를 구매하지 않으며 소프트웨어를 개발해야 한다.', 'create'),
    ('수정이 가능하도록 소프트웨어를 개발해야 한다.', 'create'),
    ('계약업체는 발주처의 사용권을 1년 연장해야 한다.', 'renew'),
    ('소프트웨어 설치 및 운영', 'install'),
    ('정보시스템을 운영해야 한다.', 'operate'),
    ('라이선스 장애 발생 시 복구와 기술지원을 제공해야 한다.', 'maintain'),
    ('장애가 발생하는 경우 소프트웨어를 복구하고 기술지원을 제공해야 한다.', 'maintain'),
    ('소프트웨어를 개발하여 납품해야 한다. 수정은 선택사항이다.', 'create'),
    ('소프트웨어를 수정하지 않으며 라이선스는 갱신해야 한다.', 'renew'),
    ('소프트웨어 개발은 필수이며 하드웨어 교체는 선택사항이다.', 'create'),
    ('계약업체는 별도로 발주된 DEWESOFT를 납품해야 한다.', 'provide'),
])
def test_positive_work_keeps_its_own_action_and_modality(text, action):
    result = decision(text, action)
    assert result['value'] == 1
    assert not result['semantic_audit']['relations'][0]['issues']


@pytest.mark.parametrize('text,quote,action', [
    ('라이선스 갱신 의무가 없다.', '라이선스 갱신', 'renew'),
    ('필요한 경우에만\n소프트웨어를 수정해야 한다.', '소프트웨어를 수정해야 한다.', 'modify'),
    ('정보시스템 운영은\n수행하지 않는다.', '정보시스템 운영', 'operate'),
    ('소프트웨어를 개발하지 않으며 장비는 납품해야 한다.', '소프트웨어를 개발', 'create'),
    ('라이선스 갱신은 필수다.\n라이선스 갱신 의무가 없다.', '라이선스 갱신', 'renew'),
])
def test_clipped_or_ambiguous_witness_cannot_erase_governing_source(text, quote, action):
    result = decision(text, action, quote=quote)
    assert result['value'] is None
    assert result['semantic_audit']['relations'][0]['issues']
    assert result['facts']['relations'][0]['witnesses'][0]['quote'] == quote


@pytest.mark.parametrize('work', [
    '정보시스템 운영은 수행하지 않는다.',
    '정보시스템을 운영할 수 있다.',
    '정보시스템 구축은 선택사항이다.',
    '정보시스템 유지보수 의무가 없다.',
    '정보시스템 운영은\n수행하지 않는다.',
    '정보시스템유지관리서비스는 요구하지 않는다.',
    '라이선스 갱신 의무가 없다.',
    '라이선스 구매는 선택사항이다.',
    '소프트웨어를 설치해야 한다는 의무는 철회한다.',
    '정보시스템은 기존 장비다. 건물을 구축해야 한다.',
])
def test_default_source_rule_does_not_invent_sw_work(work):
    rec, _ = source('소프트웨어사업자(컴퓨터관련서비스사업) 등록\n'+work)
    rec['meta']['업무구분'] = '일반용역'
    result = sw_check(rec)
    assert result['value'] is None
    assert not result['facts']['actual_work']


@pytest.mark.parametrize('work', [
    '정보시스템 운영은 수행하지 않으며 라이선스 갱신은 필수이다.',
    '라이선스 구매는 선택사항이며 정보시스템을 운영해야 한다.',
    '정보시스템을 운영하며 장비 구매는 요구하지 않는다.',
    '장비는 구매하지 않으며 정보시스템을 운영해야 한다.',
    '정보시스템 운영',
    '정보시스템유지관리서비스',
    '라이선스 갱신 구매',
])
def test_default_rule_can_use_an_independent_positive_work_clause(work):
    rec, _ = source('소프트웨어사업자(컴퓨터관련서비스사업) 등록\n'+work)
    assert sw_check(rec)['value'] == 1


def test_invalid_model_claim_does_not_erase_independent_full_source_proof():
    text = '본 사업은 소프트웨어 사업이다.\n소프트웨어를 수정할 수 있다.'
    result = decision(text, 'modify', quote='소프트웨어를 수정할 수 있다.')
    assert result['value'] == result['source_rule']['value'] == 1
    assert result['semantic_audit']['relations'][0]['issues']
    assert not result['actual_software_relations']


def test_disconnected_quote_fragments_cannot_manufacture_action():
    result = decision('소프트웨어 개발 방법 안내.\n소프트웨어를 보유해야 한다.', 'create', quote='소프트웨어를 보유해야 한다.')
    assert result['value'] is None
    assert result['semantic_audit']['relations'][0]['issues']
