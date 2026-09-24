"""Preserve operative bidder duties while distinguishing evaluation forms."""
import copy
import pytest

from submission.pps.performance import performance_facts, validate_model_witness
from submission.pps.qualification import inventory, qualification_facts
from submission.pps import sme
from tests.test_qualification_heading_roles import notice


@pytest.mark.parametrize('clause', [
    '가. 직접생산확인증명서는 제안서와 함께 제출하여야 하며, 그 증명서가 유효한 업체에 한하여 참가를 허용합니다.',
    '가. 직접생산확인증명서[세부품명: 시험제품, 물품분류번호: 1234567890]를 소지하고, 입찰서 제출 마감일 전일까지 나라장터에 제조물품으로 입찰참가 등록한 업체',
    '가. 직접생산확인증명서를 소지하고 국가종합전자조달시스템에 입찰참가 등록한 자',
])
def test_certificate_duty_and_bidder_subject_are_connected(clause):
    rec = notice('4. 입찰참가자격', clause)
    facts = qualification_facts(rec, inventory(rec))
    assert facts['active_direct']
    assert any(o['production_required_in_every_branch']
               for o in facts['direct_certificate_coverage']['observations'])


@pytest.mark.parametrize('clause', [
    '가. 직접생산확인증명서 또는 대리점확인서를 소지하고 나라장터에 입찰참가 등록한 업체',
    '가. 낙찰 후 직접생산확인증명서를 소지하고 나라장터에 입찰참가 등록한 업체',
    '가. 예시: 직접생산확인증명서를 소지하고 나라장터에 입찰참가 등록한 업체',
    '가. 직접생산확인증명서 안내를 참고하고 별도의 인증서를 소지하고 나라장터에 입찰참가 등록한 업체',
    '가. 직접생산확인증명서는 제안서와 함께 제출할 수 있으며, 유효한 증명서를 제출한 업체에는 가점을 부여합니다.',
    '가. 직접생산확인증명서는 제안서와 함께 제출하여야 하며, 별도의 등록증이 유효한 업체에 한하여 참가를 허용합니다.',
])
def test_certificate_object_alternatives_and_later_stage_are_not_invented(clause):
    rec = notice('4. 입찰참가자격', clause)
    facts = qualification_facts(rec, inventory(rec))
    assert not facts['active_direct']


def test_repeated_document_title_does_not_hide_an_explicit_qualification_governor():
    rec = notice('4. 입찰참가자격 및 제출서류',
        '제출서류\n* 아래의 자격을 모두 갖춘 자\n'
        '가. 중소기업확인서를 소지한 자')
    facts = qualification_facts(rec, inventory(rec))
    assert facts['allowed'] == ['medium', 'micro', 'small']
    assert not facts['no_size']


@pytest.mark.parametrize('governor', ['제출서류', '* 아래의 자격을 모두 갖춘 자 (작성 예시)', '* 아래의 자격을 모두 갖춘 자 조건은 철회'])
def test_document_list_and_nonoperative_governor_remain_nonrequirements(governor):
    rec = notice('4. 제출서류', governor + '\n중소기업확인서 1부')
    assert not qualification_facts(rec, inventory(rec))['active_size']


@pytest.mark.parametrize('heading,clause', [
    ('2. 책임연구원 연구경력(7점)', '최근 4년간 완료한 연구실적으로 건별 2천만원 이상인 용역에 한함'),
    ('유사사업 수행 실적표', '최근 4년간 8천만원 이상인 행사 용역 실적이며 허위 자료는 계약을 해지함'),
    ('3. 수행실적', '최근 4년 이내 완료된 실적만 인정하며 최근 연도순으로 기재'),
])
def test_evaluation_and_form_purpose_cannot_prove_bidder_exclusion(heading, clause):
    rec = notice('4. 입찰참가자격', '가. 사업자등록을 마친 업체')
    rec['docs'].append({'doc_id': 'D2', 'type': '제안요청서',
                        'text': heading + '\n' + clause})
    facts = performance_facts(rec, consumer=True)
    result = validate_model_witness(rec, {'v2': 1, 'e2': clause}, 2, facts)
    assert result and result['value'] == 0


def test_substantive_gate_repeated_in_a_form_is_preserved():
    clause = '최근 4년간 8천만원 이상 행사 용역 실적을 보유한 업체만 입찰에 참가할 수 있습니다.'
    rec = notice('4. 입찰참가자격', clause)
    rec['docs'].append({'doc_id': 'D2', 'type': '제안요청서',
                        'text': '유사사업 수행 실적표\n' + clause})
    facts = performance_facts(rec, consumer=True)
    assert validate_model_witness(rec, {'v2': 1, 'e2': clause}, 2, facts) is None
    assert any(c['status'] == 'mandatory' for c in facts['candidates'])


def test_bare_certificate_caption_does_not_disprove_an_independent_money_gate():
    # An incomplete model witness can point at the document list while the
    # notice independently contains an operative bidder threshold elsewhere.
    quote = '6. 실적증명서'
    rec = notice('4. 입찰참가자격',
        '가. 서비스 제공 실적 금액 4억원 이상인 사업자에 한함 (최근 4개년)')
    rec['docs'][0]['text'] = quote + '\n' + rec['docs'][0]['text']
    facts = performance_facts(rec, consumer=True)
    assert validate_model_witness(rec, {'v3': 1, 'e3': quote}, 3, facts) is None


def test_consumer_repairs_leave_frozen_input_facts_unchanged():
    rec = notice('4. 입찰참가자격 및 제출서류',
        '제출서류\n* 아래의 자격을 모두 갖춘 자\n가. 중소기업확인서를 소지한 자')
    before = copy.deepcopy(sme.extract_inventory(rec))
    inventory(rec)
    assert sme.extract_inventory(rec) == before
