"""Bidder identity/capacity gates, contrasted with nearby lawful relations."""
import pytest

from submission.pps.eligibility_restrictions import eligibility_restriction_check
from submission.pps.rules import apply_rules


def notice(body, heading='3. 입찰참가자격', **meta):
    return {'id': 'unseen-relation',
            'meta': {'업무구분': '물품(내자)', '계약방법': '제한경쟁', **meta},
            'docs': [{'doc_id': 'D0', 'type': '공고문',
                      'text': heading + '\n' + body + '\n4. 제출서류'}]}


@pytest.mark.parametrize('body', [
    '가. 산학협력단에 한하여 입찰에 참가할 수 있습니다.',
    '나. 본 입찰은 4년제 대학교만 참여 가능함.',
    '입찰참가자는 국공립 연구기관 또는 정부출연 연구기관이어야 합니다.',
    '라. 고등교육법에 따른 대학 또는 산업교육진흥법에 따른\n산학협력단만 참여 가능함.',
    '상시근로자 63명 이상을 직접 고용하고 있는 업체이어야 합니다.',
    '상시 고용한 정규직 인원이 37명 이상인 업체만 입찰에 참여할 수 있습니다.',
    '본사에 정규직 기술인력 22명 이상을 상시 보유한 업체에 한하여 참가할 수 있습니다.',
    '참가업체는 과업에 투입하는 인원과 별개로 본사 소속 상근직원 71명을 확보하여야 합니다.',
    '견적제출자는 본점에서 상시 근무하는 직원이 29명 이상임을 입증하여야 하며, 미달 업체의 견적은 접수하지 않습니다.',
    '전국 각 광역시·도에 직영 유지보수 센터를 갖추어야 합니다.',
    '전국 모든 광역단체에 기계 수리 센터가 있는 업체',
    '서울특별시, 대구광역시, 대전광역시에 직영 서비스센터를 모두 보유한 업체',
    '전국 17개 시·도에 지사 또는 영업소를 각각 설치·운영하고 있는 업체',
    '3개 이상의 자치구에 각각 자체 교육장을 설치하여 운영 중인 업체',
])
def test_recovers_closed_bidder_relation_with_literal_evidence(body):
    rec = notice(body)
    decision = eligibility_restriction_check(rec)
    assert decision and decision['value'] == 1
    assert decision['evidence'] in rec['docs'][0]['text']
    assert len(decision['evidence']) <= 500
    assert apply_rules(rec, {'v1': 0, 'e1': ''}, items=(1,))[0]['v1'] == 1


@pytest.mark.parametrize('body,heading', [
    ('법령에 따라 등록한 학술연구용역 업체는 참가 가능합니다.', '3. 입찰참가자격'),
    ('중소기업 또는 비영리법인인 대학, 연구기관도 참가할 수 있습니다.', '3. 입찰참가자격'),
    ('대학 또는 일반 기업에 한하여 참여 가능합니다.', '3. 입찰참가자격'),
    ('소기업, 소상공인 확인서를 소지한 업체 또는 비영리 법인이어야 합니다.', '3. 입찰참가자격'),
    ('대학교만 발주한 용역 실적을 보유한 업체이어야 합니다.', '3. 입찰참가자격'),
    ('대학교를 졸업한 기술자 5명 이상을 배치하여야 합니다.', '3. 입찰참가자격'),
    ('상시근로자 63명 이상을 보유한 업체는 가점 2점을 부여합니다.', '3. 평가기준'),
    ('본사 정규직 인원 63명 이상 여부를 기재합니다.', '붙임1. 인력현황 서식'),
    ('낙찰자는 계약체결 후 상시근로자 63명 이상을 고용하여야 합니다.', '3. 입찰참가자격'),
    ('과업 수행에 필요한 정규직 인원 3명을 투입하여야 합니다.', '3. 입찰참가자격'),
    ('법정 등록기준인 상시근로자 5명 이상을 고용한 업체', '3. 입찰참가자격'),
    ('상시근로자 63명 이상 보유를 요구하지 않습니다.', '3. 입찰참가자격'),
    ('상근직원 63명 이상인 업체라는 조건은 철회합니다.', '3. 입찰참가자격'),
    ('전국 서비스센터 보유와 무관하게 입찰에 참여할 수 있습니다.', '3. 입찰참가자격'),
    ('전국 각 지역에 물품을 납품할 수 있는 업체', '3. 입찰참가자격'),
    ('전국에 물품을 납품할 수 있고 본사에 서비스센터를 보유한 업체', '3. 입찰참가자격'),
    ('전국 각 광역시·도의 납품장소에 배송할 수 있고 정비용 서비스센터를 보유한 업체', '3. 입찰참가자격'),
    ('전국 각 시·도에 서비스센터를 보유 또는 임차하여 운영하는 업체', '3. 입찰참가자격'),
    ('낙찰자는 전국 각 시·도에 직영 서비스센터를 설치하여 운영하여야 합니다.', '3. 계약조건'),
    ('전국 각 시·도 서비스센터를 보유한 업체를 우대합니다.', '3. 입찰참가자격'),
    ('전국 각 시·도의 협력업체와 협약한 서비스센터를 운영하는 업체', '3. 입찰참가자격'),
    ('전국 각 시·도에 직영 서비스센터를 보유한 업체', '3. 제안서 평가기준'),
    ('[수요기관(대학)]에 물품을 공급할 수 있는 업체', '3. 입찰참가자격'),
])
def test_legal_near_relation_abstains_and_preserves_model_positive(body, heading):
    rec = notice(body, heading)
    assert eligibility_restriction_check(rec) is None
    original = {'v1': 1, 'e1': body}
    assert apply_rules(rec, original, items=(1,))[0] == original
    assert apply_rules(rec, {'v1': 0, 'e1': ''}, items=(1,))[0]['v1'] == 0


def test_does_not_join_separate_bullets_into_a_restriction():
    rec = notice('가. 상시근로자 63명 이상\n나. 보유 장비 목록을 기재합니다.')
    assert eligibility_restriction_check(rec) is None


def test_new_gates_do_not_require_a_competitive_procedure():
    rec = notice('상시근로자 63명 이상을 보유한 업체', 계약방법='수의계약')
    assert eligibility_restriction_check(rec)['value'] == 1


@pytest.mark.parametrize('heading,prefix', [
    ('3. 참가자격: 아래 요건을 모두 갖춘 업체', ''),
    ('3. 입찰참가자격', '가. 적격심사 서류를 제출하여야 합니다.\n'),
    ('3. 입찰참가자격', '가. 제출서류\n - 등록증 1부\n'),
])
def test_qualification_heading_and_sibling_scope(heading, prefix):
    rec = notice(prefix + '나. 전국 각 시·도에 직영 서비스센터를 보유한 업체', heading)
    assert eligibility_restriction_check(rec)['value'] == 1
