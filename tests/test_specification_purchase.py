import pytest

from submission.pps.rules import apply_rules
from submission.pps.specification_purchase import named_purchase_check


def record(text, notice='물품 구매 입찰', doc_type='규격서'):
    return {'id': 'arbitrary', 'meta': {}, 'docs': [
        {'doc_id': 'D0', 'type': '공고문', 'text': notice},
        {'doc_id': 'D1', 'type': doc_type, 'text': text}]}


@pytest.mark.parametrize('clause', [
    '모델명: XY-740', '서버모델: Acme Stream 425',
    '제조사: 한빛전자', '모델: Northern Instrument',
    '차 종 | 화물차량(모델: MX-52)',
])
def test_named_purchase_field(clause):
    rec = record('구매 규격서\n품명: 시험장비\n' + clause)
    check = named_purchase_check(rec)
    assert check and check['value'] == 1
    assert check['evidence'] in rec['docs'][1]['text']
    assert len(check['evidence']) <= 500


@pytest.mark.parametrize('clause', [
    '납품 물품은 지정 모델(모델번호 QX-740)로 한정한다.',
    '입찰참가자는 행사 음향 장비로 TX4 모델을 사용하여야 한다.',
    '납품 물품은 모델번호 QX-740으로 한정하며 동등품으로는 참가할 수 없음.',
])
def test_explicit_obligation_in_notice(clause):
    assert named_purchase_check(record('', notice=clause))


@pytest.mark.parametrize('body', [
    '모델명: XY-740 또는 동등 이상',
    '모델명: XY-740 또는 동급 이상',
    '모델명: XY-740 또는 동등품',
    '모델명: XY-740\n해당 모델과 동급 이상 제품도 납품 가능',
    '모델명: XY-740\n본 규격서의 기준과 동등 이상의 장비를 납품할 수 있다.',
    '모델명: XY-740\n전압: 220V\n무게: 10kg\n상기 장비는 동급 이상 제품으로 납품 가능',
    '예시 모델명: XY-740', '참고 모델명: XY-740', '추천모델: XY-740',
    '기존 장비 현황\n제조사: 한빛전자\n모델명: XY-740',
    '소모품 적용 기종\n모델명: XY-740',
    '제품명칭 일회용 센서\n주요제원 XY-740 모델에 사용할 수 있어야 한다.',
    'OS: Ubuntu 24.04', '모델명: ISO-9001', '모델명: 제조사 무관',
    '제조사: 확인서제출', '제조사: 없습니다',
    '브랜드 개발 및 모델 수립 과업이다.',
])
def test_near_miss_withholds_cpu_positive(body):
    assert named_purchase_check(record('구매 규격서\n' + body)) is None


def test_cross_document_whole_spec_permission():
    rec = record('구매 규격서\n모델명: XY-740',
                 notice='첨부 규격서의 모든 조건은 동등 이상의 제품을 납품할 수 있다.')
    assert named_purchase_check(rec) is None


def test_whole_procurement_equivalence_in_proposal_acceptance_criteria():
    rec = record('구매 규격서\n모델명: XY-740')
    rec['docs'].append({'doc_id': 'D2', 'type': '제안요청서',
                       'text': '전체 물품의 규격을 모두 충족 (동급 이상 규격)할 경우 적합'})
    assert named_purchase_check(rec) is None


def test_other_component_permission_does_not_clear_model():
    rec = record('구매 규격서\n품명: 서버\n모델명: XY-740\n메모리: 64GB 또는 동급 이상')
    assert named_purchase_check(rec)


def test_other_product_form_permission_does_not_clear_model():
    rec = record('구매규격 및 용도설명서\n품명: 서버\n모델명: XY-740\n'
                 '구매규격 및 용도설명서\n품명: 케이블\n본 규격서 기준과 동등 이상의 제품 납품 가능')
    assert named_purchase_check(rec)


def test_maintenance_equipment_is_not_new_product():
    rec = record('정기수리 내역서\n냉장고 도어 패킹 교체\n규격: 고무\n모델명: XY-740')
    assert named_purchase_check(rec) is None


def test_collective_compatibility_note_binds_manufacturer_rows():
    rec = record('구매 규격서\n제조사가 명시된 물품은 동일 제조사 물품이어야 한다.\n'
        '* 기 사용 분석기와 호환되어야 하는 물품\n품명: 센서\n제조사: 한빛전자')
    assert named_purchase_check(rec) is None


def test_cpu_or_preserves_model_positive_and_item_scope():
    rec = record('구매 규격서\n모델명: XY-740')
    row, checks = apply_rules(rec, {'v9': 0, 'e9': ''}, items=(9,))
    assert row['v9'] == 1 and checks
    old = {'v9': 1, 'e9': 'independent model evidence'}
    assert apply_rules(rec, old, items=(9,)) == (old, [])
    assert apply_rules(rec, old, items=()) == (old, [])
    legal = record('구매 규격서\n모델명: XY-740 또는 동등품')
    assert apply_rules(legal, old, items=(9,)) == (old, [])


def test_specialist_negative_cannot_erase_cpu_witness():
    from submission.pps import specification_candidate_review as review
    from tests.test_specification_candidate_review import fixture, response
    rec, _, _, payload, packet = fixture('구매 규격서\n모델명: XY-740')
    # Simulate the fallible model misclassifying the explicit field as generic.
    answer = payload[review.NAME]['C1']
    answer.update(specificity='generic', role='new_whole_product',
                  requirement='mandatory', scope_sources=[2], permission_scope='not_observed')
    row, details = review.overlay_review(rec, response(payload), packet)
    assert row['v9'] == 1 and row['e9'] == '모델명: XY-740'
    assert details[-1]['source'] == 'named_purchase_requirement'
