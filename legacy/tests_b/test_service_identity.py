"""A fallible model phrase must not disable independent source identity proofs."""
from pathlib import Path

import pytest

from submission.pps.knowledge import Knowledge
from submission.pps.qualification import infer
from submission.pps.sme import extract_sme_facts
from submission.pps.service_identity import provide


DATA = Path(__file__).resolve().parents[1]/'data_open/data'


def notice(title='통학버스 임차 및 운행 용역', performance=True, certificate=True):
    text = ('1. 사업개요\n용역명: '+title+'\n차량규격: 45인승 차량 2대\n'
            '2. 입찰참가자격\n가. 해당 업종을 등록한 업체\n')
    if certificate:
        text += '나. 통학운송서비스(7811189902)의 직접생산확인증명서를 소지한 업체\n'
    text += '3. 과업내용\n'
    if performance:
        text += '운전원을 제공하여야 한다.\n통학차량은 지정 노선을 운행하여야 한다.\n'
    return {'id': 'synthetic-service-candidate',
            'meta': {'업무구분': '일반용역', '적용계약법': '국가계약법',
                     '계약방법': '제한경쟁', '입찰추정가격': 150_000_000},
            'docs': [{'type': '공고문', 'doc_id': 'test-notice', 'text': text}],
            'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


def resolve(rec, claim):
    knowledge = Knowledge(DATA)
    knowledge.detailed_product_facts(rec)
    pf = knowledge._product_facts
    _, qualification = infer(rec, {}, pf)
    sme = extract_sme_facts(rec, pf)
    return provide(rec, {'실제구매대상_경쟁제품_고시조건': claim},
                   sme['product'], qualification, knowledge.products)


@pytest.mark.parametrize('claim', [
    '', '불명확', '통학운송서비스(7811189902), 중소기업자간 경쟁제품 해당',
    '중소기업자간 경쟁제품(통학운송서비스, 7811189902) 해당',
    '경쟁제품이 아닌 일반용역으로 판단함',
])
def test_independent_scope_proofs_do_not_depend_on_model_wording(claim):
    rec = notice()
    product, log = resolve(rec, claim)
    assert product['status'] == 'competition'
    assert not log['model_fact_used_for_identity']
    assert all(rec['docs'][e['doc_index']]['text'][e['start']:e['end']] == e['text'] for e in log['proofs'])


@pytest.mark.parametrize('changes', [
    {'performance': False}, {'certificate': False},
    {'title': '학교 체험활동 및 행사 운영 용역'},
    {'title': '통학버스 운행 실태 조사 용역'},
])
def test_candidate_certificate_and_model_claim_do_not_prove_actual_scope(changes):
    product, log = resolve(notice(**changes), '통학운송서비스(7811189902), 경쟁제품에 해당함')
    assert product is None and not log['accepted']


def test_negated_transport_duty_does_not_prove_service_identity():
    rec = notice()
    rec['docs'][0]['text'] = rec['docs'][0]['text'].replace('운행하여야 한다.', '운행하지 않는다.')
    assert resolve(rec, '경쟁제품에 해당함')[0] is None


def test_direct_production_certificate_does_not_supply_a_size_requirement():
    rec = notice()
    product, _ = resolve(rec, '')
    knowledge = Knowledge(DATA)
    knowledge.detailed_product_facts(rec)
    row, facts = infer(rec, {}, knowledge._product_facts, product_override=product)
    assert row['v10'] == '0' and row['v11'] == '1'
    assert not facts['qualification']['active_size']


def test_whole_contract_body_can_establish_scope_under_generic_title():
    rec = notice(title='학생 이동 지원 용역')
    rec['docs'][0]['text'] += '본 용역의 전체 과업은 학생 통학버스 임차 및 운행을 대상으로 한다.\n'
    product, log = resolve(rec, '알 수 없음')
    assert product['status'] == 'competition'
    assert log['scope_path'] == 'explicit_whole_contract_body'
    assert any('전체 과업' in e['text'] for e in log['proofs'])


@pytest.mark.parametrize('scope', [
    '본 용역의 전체 과업은 학생 통학버스 운행 실태 조사이다.',
    '본 용역의 전체 과업은 학생 통학버스 임차 및 행사 운영이다.',
    '본 용역의 일부 과업은 학생 통학버스 임차 및 운행을 대상으로 한다.',
    '본 용역의 전체 과업은 학생 통학버스 임차 및 운행을 대상으로 하지 않는다.',
    '본 용역의 전체 과업은 학생 통학버스 임차 및 운행을 대상으로 한다는 예시이다.',
])
def test_body_candidate_does_not_loosen_independent_scope_proof(scope):
    rec = notice(title='학생 이동 지원 용역')
    rec['docs'][0]['text'] += scope+'\n'
    assert resolve(rec, '경쟁제품에 해당함')[0] is None


def test_whole_body_scope_keeps_actual_performance_and_title_conflict_checks():
    for rec in [notice(title='학생 이동 지원 용역', performance=False),
                notice(title='통학버스 운행 실태 조사 용역')]:
        rec['docs'][0]['text'] += '본 용역의 전체 과업은 학생 통학버스 임차 및 운행을 대상으로 한다.\n'
        assert resolve(rec, '')[0] is None
