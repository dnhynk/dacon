"""Precision boundaries from private principals and operative source duties."""
from types import SimpleNamespace

import pytest

from submission.pps.contracting_principal import review
from submission.pps.qualification import infer
from tests.test_production_certificate_relations import record, CATALOG, A


@pytest.mark.parametrize('clause', [
    '본 입찰은 민간사업으로 낙찰자 결정 후 계약이 체결되지 않으면 무효 처리됩니다.',
    '본 입찰은 농장에서 시행하는 민간자본보조사업으로서 시에서 입찰을 대행하는 건이며, '
    '낙찰자 선정 이후의 물품계약은 농장 주관으로 직접 계약을 체결하여야 합니다.',
])
def test_explicit_private_purchase_is_outside_public_priority_duty(clause):
    rec = record(clause, purchases=A)
    assert review(rec)['outside_public_purchase_checks']
    row, _ = infer(rec, {'v18': '1', 'e18': ''}, CATALOG)
    assert row['v18'] == '0'


@pytest.mark.parametrize('clause', [
    '민간사업 지원을 위하여 시가 장비를 구매합니다.',
    '본 입찰은 민간사업이 아닙니다.',
    '예시: 본 입찰은 민간사업으로 추진한다.',
    '본 사업은 보조사업이며 입찰대행을 실시합니다.',
])
def test_private_topic_does_not_change_public_purchaser(clause):
    assert not review(record(clause))['outside_public_purchase_checks']


@pytest.mark.parametrize('size', ['소기업·소상공인', '중소기업'])
def test_scoring_only_size_document_does_not_restrict_eligibility(size):
    rec = record(f'가. {size} 확인서는 신인도 평가의 확인자료로만 쓰이며, '
                 f'{size}이 아닌 자도 입찰에 참가할 수 있음', purchases='일반장비[9999999999]')
    rec['meta']['입찰추정가격'] = 400_000_000
    row, facts = infer(rec, {'v14': '1', 'e14': 'model'}, CATALOG)
    assert row['v14'] == '0'
    assert not facts['qualification']['active_size']


def test_scoring_note_does_not_remove_independent_size_gate():
    rec = record('가. 소기업·소상공인확인서를 소지한 업체\n'
                 '나. 중소기업 확인서는 신인도 평가의 확인자료로만 쓰이며, '
                 '중소기업이 아닌 자도 해당 평가를 받을 수 있음', purchases='일반장비[9999999999]')
    rec['meta']['입찰추정가격'] = 400_000_000
    row, _ = infer(rec, {'v14': '0', 'e14': ''}, CATALOG)
    assert row['v14'] == '1'


def test_nonholder_exclusion_is_not_positive_permission():
    rec = record('가. 소기업 확인서는 신인도 평가의 확인자료로만 쓰이며, '
                 '소기업이 아닌 자는 입찰에 참가할 수 없음', purchases='일반장비[9999999999]')
    _, facts = infer(rec, {'v14': '0', 'e14': ''}, CATALOG)
    assert facts['qualification']['active_size']


@pytest.mark.parametrize('bullet', ['‣', '○', '-'])
def test_nominal_certificate_under_all_qualifications_is_required(bullet):
    rec = record('가. 아래 각 목을 모두 충족하는 자\n'
                 f'{bullet} 직접생산확인증명서[물품분류: 1111111111(세부품명: 시험품목가)]', purchases=A)
    row, facts = infer(rec, {'v10': '1', 'e10': ''}, CATALOG)
    assert row['v10'] == '0'
    assert facts['qualification']['incorporated_direct_requirements']


@pytest.mark.parametrize('prefix', [
    '가. 다음 중 어느 하나를 충족하는 자\n',
    '가. 참고용 제출서류\n',
    '가. 아래 각 목을 모두 충족하는 자\n나. 다음 중 어느 하나를 충족하는 자\n',
])
def test_nominal_certificate_without_binding_all_governor_is_not_proof(prefix):
    rec = record(prefix+'‣ 직접생산확인증명서[물품분류: 1111111111]', purchases=A)
    row, facts = infer(rec, {'v10': '1', 'e10': ''}, CATALOG)
    assert row['v10'] == '1'
    assert not facts['qualification']['incorporated_direct_requirements']


@pytest.mark.parametrize('break_at', ['직접\n생산', '직접생산'])
def test_specification_supplier_certificate_cannot_be_read_as_absent(break_at):
    rec = record('가. 일반 등록 업체', purchases=A)
    rec['docs'].append({'doc_id': 'S', 'type': '규격서', 'text':
        '1.4 공급자 자격요건\n1.4.1 본 장비 공급 및 설치업체는 시험품목가 '
        +break_at+' 확인 증명서를 받은 업체이어야 한다.\n1.5 설치조건'})
    row, facts = infer(rec, {'v10': '0', 'e10': ''}, CATALOG)
    assert row['v10'] == '0'
    assert not facts['qualification']['no_direct']


@pytest.mark.parametrize('predicate', ['받을 수 있다.', '받는 것을 권장한다.'])
def test_optional_specification_certificate_does_not_prevent_absence(predicate):
    rec = record('가. 일반 등록 업체', purchases=A)
    rec['docs'].append({'type': '규격서', 'text':
        '1.4 공급자 자격요건\n본 공급업체는 직접\n생산확인증명서를 '+predicate})
    _, facts = infer(rec, {'v10': '0', 'e10': ''}, CATALOG)
    assert facts['qualification']['no_direct']


def software_record(task):
    rec = record('가. 중기업·소기업·소상공인 확인서 중 하나를 소지한 자',
                 purchases='기타설치서비스[9999999999]')
    rec['meta']['입찰추정가격'] = 80_000_000
    rec['docs'].append({'type': '과업지시서', 'text': task})
    pf = SimpleNamespace(products={**CATALOG.products, '8111159801': {
        '세부품명': '패키지소프트웨어개발및도입서비스', '제품명': '소프트웨어엔지니어링업',
        '특이사항': '소프트웨어 진흥법 제48조 적용'}})
    return rec, pf


def test_actual_software_delivery_blocks_general_identity_from_metadata():
    rec, pf = software_record('위 패키지를 독립적으로 실행 가능한 SW로 제공하여야 함.\n'
        '정품 S/W만 납품하여야 함.\n본 사업은 소프트웨어 진흥법 제48조 적용 사업임.')
    row, facts = infer(rec, {'v17': '0', 'e17': ''}, pf)
    assert 'v17' not in facts['decisions']
    assert facts['product']['status'] == 'unknown'
    # The general size block (DESIGN_B 1-2) does not read the software-delivery uncertainty (no replica or dev record
    # carries it) and counts this unresolved goods identity without a direct-production requirement as general.
    assert row['v17'] == '1'


def test_software_law_or_bidding_system_reference_does_not_change_purchase():
    rec, pf = software_record('입찰용 소프트웨어를 사용한다. 소프트웨어 진흥법 제48조를 참고한다.')
    _, facts = infer(rec, {'v17': '0', 'e17': ''}, pf)
    assert facts['product']['status'] == 'general'
