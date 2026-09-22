"""CPU recovery of facts the model read but answered 0 (v9/v19/v22)."""
import pytest

from submission.pps.model_exclusivity import exclusive_identity_check
from submission.pps.other_checks import _governing_section, briefing_check, pledge_check
from submission.pps.rules import apply_rules


def briefing(text):
    return {'meta': {'적용계약법': '국가계약법', '소관구분': '국가기관', '낙찰방법': '협상에의한계약'},
            'docs': [{'type': '공고문', 'text': text}],
            'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


def pledge(text):
    return {'id': 'synthetic-c2', 'meta': {'적용계약법': '국가계약법', '소관구분': '국가기관',
                                           '업무구분': '물품(내자)'},
            'docs': [{'type': '공고문', 'text': text}],
            'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


def spec(text, doc_type='규격서'):
    return {'id': 'synthetic-c2', 'meta': {'적용계약법': '국가계약법', '소관구분': '국가기관'},
            'docs': [{'type': '공고문', 'text': '물품 구매 입찰 공고'}, {'type': doc_type, 'text': text}],
            'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


# No heading word in the filler, and the title lies beyond the local heading
# window: only the governing section title can bind.
FILLER = '○ 국가종합전자조달시스템에 등록하고 관계 법령상 결격사유가 없는 업체이어야 합니다.\n' * 20


@pytest.mark.parametrize('text', [
    '2. 입찰참가 자격\n가. 아래의 자격을 모두 갖춘 자\n나. 입찰설명회에 참석한 경우에 한함.(참석업체 사업자등록증 지참)',
    '3. 입찰참가자격\n' + FILLER + '○ 현장 설명회에 참석한 자(업체)에 한함.\n4. 입찰보증금',
    '3. 입찰참가자격\n' + FILLER + '○ 「조달청 협상에 의한 계약 제안서평가 세부기준」 제6조에 따라 처리합니다.\n'
    '○ 현장 설명회에 참석한 자(업체)에 한함.\n4. 입찰보증금',
])
def test_attendance_restriction_bound_by_its_section(text):
    assert briefing_check(briefing(text))['value'] == 1


@pytest.mark.parametrize('text', [
    '3. 입찰참가자격\n' + FILLER + '○ 현장설명회에 참석한 업체에 한하여 자료를 배포합니다.\n4. 입찰보증금',
    '3. 입찰참가자격\n' + FILLER + '○ 사업설명회에 참석한 자는 주차권을 받는다.\n4. 입찰보증금',
    '3. 입찰참가자격\n' + FILLER + '○ 사업설명회는 자율 참석이며 참석 여부와 상관없이 입찰 가능합니다.\n4. 입찰보증금',
    '5. 제안서 평가\n' + FILLER + '○ 제안서 발표회에 참석한 업체에 한함.',
    '사업설명회 참석자는 확인서를 받을 수 있으며, 확인서 제출 여부와 관계없이 제안서를 제출할 수 있습니다.',
])
def test_section_or_certificate_alone_does_not_make_a_restriction(text):
    assert briefing_check(briefing(text))['value'] != 1


def test_a_numbered_sentence_is_not_a_section_title():
    text = ('3. 입찰참가자격\n○ 조건\n1. 금품을 요구하거나 약속하지 않을 것이며, 이를 위반하면 제재한다.\n'
            '2. 담합하지 않을 것\n여기')
    assert _governing_section(text, len(text)) == '입찰참가자격'


@pytest.mark.parametrize('text', [
    '3) 유지보수 대상의 주요품목에 대하여 제조사·공급사로부터 기술지원확약서를 입찰서 제출 마감일 전일까지 '
    '제출할 수 있는 업체이어야 합니다.',
    '공급업체가 계약을 체결하는 경우, 입찰서 제출 마감일 전일까지 제조사 또는 수입원의 제품공급 및 '
    '사후관리 확약서와 의료기기 품목허가인증서를 제출할 수 있는 업체',
])
def test_capability_bound_to_a_stated_pre_bid_deadline_requires_holding(text):
    result = pledge_check(pledge(text))
    assert result['value'] == 1
    assert result['facts']['pre_bid_relation']['binding'] == 'third_party_document_held_by_stated_pre_bid_deadline'


@pytest.mark.parametrize('text', [
    '제조사의 물품공급확약서를 계약 시 제출 가능한 업체이어야 한다.',
    '제조사의 기술지원확약서를 입찰 시 제출할 수 있는 업체',
    '◦ 물품제조사의 제품공급 및 기술지원(A/S) 확약서 제출 가능한 업체',
    '입찰서 제출 마감일 전일까지 입찰참가자격을 등록한 자\n◦ 물품제조사의 기술지원 확약서 제출 가능한 업체',
])
def test_capability_without_its_own_pre_bid_deadline_stays_withheld(text):
    assert pledge_check(pledge(text))['value'] != 1


@pytest.mark.parametrize('text', [
    '타) 입찰등록을 마치려면 본 물품 제작에 사용되는 부품 공급사가 발행한 공급확약서를 미리 제출하여야 하며, '
    '낙찰 후 해당 서류를 확보하는 조건부 등록은 받지 않습니다.',
    '액체산소 공급사가 서명한 공급확약서를 견적 마감 전에 갖추어야 하며, 계약 시 제출하더라도 발급일은 '
    '마감 전이어야 합니다.',
])
def test_refused_or_conceded_later_stage_does_not_veto(text):
    assert pledge_check(pledge(text))['value'] != 0


@pytest.mark.parametrize('text', [
    '제조사의 기술지원확약서는 계약 체결 시 제출하여야 한다.',
    '낙찰 후 제조사의 공급확약서를 제출하여야 한다.',
    '제조사의 공급확약서는 계약 시 제출하여도 된다.',
])
def test_a_genuine_later_stage_pledge_stays_a_negative(text):
    assert pledge_check(pledge(text))['value'] == 0


@pytest.mark.parametrize('text,identity', [
    ('재봉사 납품모델은 TH-W92로만 정하고, 길이와 재질이 같은 타 모델도 동등품으로 승인하지 않습니다.', 'TH-W92'),
    ('- 차 종 | 바디온 프레임 방식의 미들 사이즈 픽업트럭(모델: X-Pro 4WD)', 'X-Pro 4WD'),
])
def test_named_identity_with_closed_alternatives_is_a_witness(text, identity):
    rec = spec(text)
    witness = exclusive_identity_check(rec)
    assert witness['value'] == 1 and witness['identity'] == identity
    row, _ = apply_rules(rec, {'v9': 0, 'e9': ''}, items=(9,))
    assert row['v9'] == 1 and row['e9']


@pytest.mark.parametrize('text', [
    '납품모델은 TH-W92 또는 동등 이상 제품으로 하며, 타 모델도 동등품이면 승인할 수 있습니다.',
    '○ 납품 요구한 시약 및 소모품을 타 회사 제품으로 대체 납품할 수 없다.',
    '기존 장비 모델 XR-200과 호환되는 토너만 납품하며, 다른 제조사 토너는 납품할 수 없습니다.',
    '- 차 종 | 미들 사이즈 픽업트럭(모델: X-Pro 4WD)\n- 비 고 | 동등 이상 제품 납품 가능',
    '- 품 명 | 노트북(모델: Windows 11)',
    '기존 픽업트럭(모델: X-Pro 4WD)의 정기 점검을 수행한다.',
    '예시: 납품모델은 TH-W92로만 정하고 타 모델은 승인하지 않는 경우',
])
def test_open_alternatives_references_and_generic_names_are_not_witnesses(text):
    assert exclusive_identity_check(spec(text)) is None
