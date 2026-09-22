"""A sanction against future quotations is not today's procurement method."""
from copy import deepcopy

import pytest

from submission.pps.performance import performance_facts
from submission.pps.rules import apply_rules


def record(reference, *, method='제한경쟁', price=80_000_000):
    return {'id': 'unseen-notice', 'meta': {
        '적용계약법': '지방계약법', '업무구분': '일반용역',
        '계약방법': method, '입찰추정가격': price,
        '배정예산금액': price * 1.1}, 'docs': [{
            'doc_id': 'notice', 'type': '공고문', 'text':
            '용역 입찰공고\n1. 계약방법: 제한경쟁입찰\n'
            '2. 입찰참가자격\n'
            '가. 본점이 경상남도에 소재한 업체\n'
            '나. 최근 4년간 단일 계약 3천만원 이상 수행 실적이 있는 업체\n'
            '3. 유의사항\n' + reference}]}


def decide(rec):
    return apply_rules(rec, {'v2': 0, 'e2': '', 'v8': 0, 'e8': ''}, items=(2, 8))[0]


@pytest.mark.parametrize('reference', [
    '수의계약 배제업체로 등록된 이력을 확인합니다.',
    '공고일 기준 수의계약 결격업체 등록 이력이 있습니까?',
    '부정당업자 제재 후 수의계약 추가 제한을 받습니다.',
    '수의계약 배제업체 또는 수의계약 결격업체 여부를 조회합니다.',
])
def test_sanction_reference_does_not_suppress_competitive_experience(reference):
    rec = record(reference)
    facts = performance_facts(rec)
    frozen = deepcopy(facts)
    assert facts['overlays']['v2']['value'] is None
    row = decide(rec)
    assert row['v2'] == row['v8'] == 1
    assert row['e2'] in rec['docs'][0]['text']
    assert performance_facts(rec) == frozen


@pytest.mark.parametrize('reference', [
    '본 입찰은 수의계약으로 진행합니다.',
    '계약구분: 수의계약. 수의계약 배제업체는 참여할 수 없습니다.',
    '수의계약 각서 1부를 제출합니다.',
    '유찰시 수의계약 전환을 검토합니다.',
])
def test_unresolved_or_actual_route_is_not_dismissed(reference):
    row = decide(record(reference))
    assert row['v2'] == row['v8'] == 0


@pytest.mark.parametrize('method', [None, '수의계약'])
def test_registered_competitive_method_is_required(method):
    assert decide(record('수의계약 배제업체로 등록', method=method))['v2'] == 0


def test_quote_title_overrules_competitive_metadata():
    rec = record('수의계약 배제업체로 등록')
    rec['docs'][0]['text'] = '소액수의 견적제출 안내공고\n' + rec['docs'][0]['text']
    assert decide(rec)['v8'] == 0


def test_price_boundary_only_blocks_item2():
    row = decide(record('수의계약 배제업체로 등록', price=230_000_000))
    assert row['v2'] == 0 and row['v8'] == 1


def test_experience_permission_remains_unresolved():
    rec = record('수의계약 배제업체로 등록')
    rec['docs'][0]['text'] = rec['docs'][0]['text'].replace(
        '3. 유의사항', '다. 실적이 없어도 입찰에 참가할 수 있습니다.\n3. 유의사항')
    assert decide(rec)['v2'] == 0


def test_scoring_is_not_a_bidder_requirement():
    rec = record('수의계약 배제업체로 등록')
    rec['docs'][0]['text'] = rec['docs'][0]['text'].replace('2. 입찰참가자격', '2. 평가기준')
    assert decide(rec)['v2'] == 0


@pytest.mark.parametrize('clause', [
    '실적증명서는 평가점수 자료입니다. 실적 합계가 예산보다 적어도 '
    '점수만 달라지며 참가자격에는 영향이 없습니다.',
    '종전 실적은 실적표에 적을 수 있으나 무실적 업체의 참가를 제한하는 조건은 아닙니다.',
    '수행실적은 정량평가 항목으로만 반영하며 입찰참가자격 요건에는 해당하지 않습니다.',
])
def test_scoring_permission_with_eligibility_noun_is_not_mandatory(clause):
    rec = record('수의계약 배제업체로 등록')
    rec['docs'][0]['text'] = rec['docs'][0]['text'].replace(
        '나. 최근 4년간 단일 계약 3천만원 이상 수행 실적이 있는 업체',
        '나. ' + clause)
    assert decide(rec)['v2'] == decide(rec)['v8'] == 0


def test_goods_do_not_inherit_the_service_rule():
    rec = record('수의계약 배제업체로 등록')
    rec['meta']['업무구분'] = '물품(내자)'
    assert decide(rec)['v2'] == 0


@pytest.mark.parametrize('reference', [
    '수의계약 시 배제사유에 해당하는 자와 체결하는 계약',
    '수의계약 시 결격사유에 해당하는 자와 체결하는 계약',
    '지방의원의 배우자 및 계열회사가 당해 지방자치단체와 체결하는 수의계약(지방계약법 제33조)',
    '(입찰: 증명서 접수일까지, 수의계약: 계약체결 전)',
    '(경쟁입찰 – 심사서류 제출일 이전, 수의계약 – 계약체결 이전)',
    '입찰공고일 기준 3년 이내에 수의계약 배제\n업체로 등록된 이력이 있습니까?',
])
def test_generic_quote_disqualification_and_method_timing_are_not_current_route(reference):
    rec = record(reference)
    frozen = deepcopy(performance_facts(rec))
    assert decide(rec)['v2'] == decide(rec)['v8'] == 1
    assert performance_facts(rec) == frozen


@pytest.mark.parametrize('reference', [
    '수의계약 시 배제사유에 해당하는 자와 체결하는 계약. 본 입찰은 수의계약입니다.',
    '(입찰: 증명서 접수일까지, 수의계약: 계약체결 전) 본 계약은 수의계약으로 진행합니다.',
    '본 계약은 배우자와 체결하는 수의계약(지방계약법 제33조)',
    '참고: 종전 공고\n계약방법: 수의계약',
    '본 계약은 수의계약 배제하지 않고 진행합니다.',
])
def test_current_or_unresolved_quote_method_survives_reference_filter(reference):
    assert decide(record(reference))['v2'] == 0


def test_uncertain_submission_instruction_cannot_invent_quote_method():
    rec = record('수의계약 결격업체로 등록된 이력을 확인합니다.')
    rec['docs'][0]['text'] += '\n참고: 서식 안내\n본 입찰은 전자입찰서를 제출해야 합니다.'
    assert decide(rec)['v2'] == decide(rec)['v8'] == 1


def test_method_withdrawal_remains_a_blocker():
    rec = record('수의계약 결격업체로 등록된 이력을 확인합니다.')
    rec['docs'][0]['text'] += '\n본 입찰을 취소합니다.'
    assert decide(rec)['v2'] == 0
