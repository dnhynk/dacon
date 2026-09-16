"""A correct action enum cannot turn an unrelated contract target into SW."""
import json

import pytest

from submission.pps.software_facts import decide
from tests.test_software_facts import response, source


def evaluate(text, action, quote=None):
    rec, spans = source(text)
    return decide(rec, json.dumps(response(quote or text, object='software', action=action)),
                  spans, absence_scope='source_scan')


@pytest.mark.parametrize('text,action', [
    ('계약업체는 납품 물품의 사용설명서를 번역하여 제공해야 한다.', 'provide'),
    ('제작도면, 현장 반입 계획 및 시험 계획을 포함한 승인도면을 작성해야 한다.', 'create'),
    ('계약업체는 교육 홍보 영상을 제작해야 한다.', 'create'),
    ('지역 문화행사의 부가 프로그램을 개발해야 한다.', 'create'),
    ('소프트웨어 설치 방법을 설명하는 자료를 제작하여 제출해야 한다.', 'provide'),
    ('설비 공급자는 시험 결과 자료를 제공해야 한다.', 'provide'),
    ('정품 부품을 제조하여 납품해야 한다.', 'provide'),
    ('계약업체는 장비 유지보수 방안과 주요 부품의 재고를 제시해야 한다.', 'maintain'),
    ('물품공급 확약서를 보유하고 입찰 때 제출해야 한다.', 'provide'),
    ('제품의 사용설명서(국문이 아닌 매뉴얼은 번역하여 제공)를 제출한다.', 'provide'),
    ('설치 부실로 인한 기기의 불량은 무상 수리한다.', 'install'),
    ('1. 납품 수량\n기자재 13대', 'provide'),
    ('“공급자”는 수입품의 물품 납품 전 수입신고필증을 제출해야 한다.', 'provide'),
    ('Alpha Industries 원제조(공급)사는 Beta社이며, 순정품을 납품해야 한다.', 'provide'),
    ('규격: OS — Linux Ubuntu 설치 지원', 'install'),
])
def test_non_software_target_cannot_create_an_optional_positive(text, action):
    result = evaluate(text, action)
    assert result['value'] is None
    assert not result['actual_software_relations']
    assert result['facts']['relations'][0]['object'] == 'software'
    assert result['semantic_audit']['relations'][0]['issues']


@pytest.mark.parametrize('text,action', [
    ('계약업체는 신규 소프트웨어를 개발하여 납품해야 한다.', 'create'),
    ('계약업체는 문서관리 소프트웨어를 개발해야 한다.', 'create'),
    ('발주기관의 사용권을 1년 연장해야 한다.', 'renew'),
    ('계약업체는 NebulaEditor를 납품해야 한다.', 'provide'),
    ('계약업체는 NebulaEditor 및 운영 매뉴얼을 납품해야 한다.', 'provide'),
    ('계약업체는 소프트웨어와 설명서를 제공해야 한다.', 'provide'),
    ('계약업체는 설명서와 함께 소프트웨어를 제공해야 한다.', 'provide'),
    ('계약업체는 컴퓨터에서 실행할 프로그램을 개발해야 한다.', 'create'),
    ('계약업체는 운영체제 사용권을 제공해야 한다.', 'provide'),
])
def test_actual_and_named_software_candidates_are_preserved(text, action):
    result = evaluate(text, action)
    assert result['value'] == 1
    assert not result['semantic_audit']['relations'][0]['issues']
    for trace in result['semantic_audit']['source_actions'][0]['actions']:
        for context in trace['contexts']:
            support = context['object_support']
            for anchor in support['target_anchors'] + support['rejected_anchors']:
                assert text[anchor['start']:anchor['end']] == anchor['quote']


def test_other_witness_cannot_lend_software_type_to_document_delivery():
    text = '소프트웨어는 본 사업에서 제공하지 않는다. 시험 결과 자료를 납품해야 한다.'
    result = evaluate(text, 'provide', quote='시험 결과 자료를 납품해야 한다.')
    assert result['value'] is None
    assert not result['actual_software_relations']
