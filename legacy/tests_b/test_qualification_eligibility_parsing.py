"""Qualification syntax keeps operative duties separate from citations and notes."""
import pytest
from submission.pps import sme
from submission.pps.qualification import inventory, qualification_facts
from tests.test_qualification_heading_roles import notice, CLAUSE


def facts(text, header='3. 입찰참가자격'):
    record = notice(header, text)
    return qualification_facts(record, inventory(record))


@pytest.mark.parametrize('header', [
    '3. 입찰참가자격(아래 조건을 동시에 모두 충족할 것)',
    '4. 견적제출 참가자격(모두 갖추어야 함)',
    '4. 입찰 참가자격(다음 각 호를 모두 갖추어야 합니다.)',
    '3. 견적서 제출 참가자격(각 아래 호를 모두 충족하여야 합니다)',
    '3. 입찰 참가자격 < 다음 각 호의 입찰참가자격을 모두 갖춘 자 이어야 합니다. >',
    '3. 견적서 참가자격',
    '3. 입찰제출 참가자격',
    '3. 입찰(견적)참가자격 및 계약방법',
    '3. 입찰참가자격(아래 각 항의 자격을 모두 갖춘 자)',
])
def test_governor_grammar(header):
    f = facts(CLAUSE, header)
    assert f['closed_eligibility']
    assert f['allowed'] == ['micro', 'small']


@pytest.mark.parametrize('note', [
    '※ 구비서류 제출 시 번호 순서대로 제출하시기 바랍니다.',
    '※ 확인서 제출목록은 발급기관에서 확인하여야 합니다.',
    '※ 평가기준에 관한 문의는 담당자에게 연락하시기 바랍니다.',
])
def test_instruction_note_does_not_change_role(note):
    f = facts(note+'\n'+CLAUSE)
    assert f['allowed'] == ['micro', 'small']
    assert f['active_size'][0]['section_role'] == 'eligibility'


def test_new_heading_at_input_end_is_not_closed():
    record = notice('3. 입찰참가자격(모두 갖추어야 함)', '일반 등록 업체')
    record['docs'][0]['text'] = record['docs'][0]['text'].split('5. 계약조건')[0]
    f = qualification_facts(record, inventory(record))
    assert f['unclosed_eligibility']
    assert not f['closed_eligibility'] and not f['no_size'] and not f['no_direct']


@pytest.mark.parametrize('title', [
    '중소기업제품 구매촉진 및 판로지원에 관한 법률',
    '「중소기업제품 구매촉진 및 판로지원에 관한 법률」',
    '중소기업 범위 및 확인에 관한 규정',
])
def test_statute_name_alone_does_not_require_bidder_size(title):
    f = facts('가. '+title+' 제9조에 따라 직접생산확인증명서를 소지한 자')
    assert f['allowed'] is None and f['no_size']
    assert not f['no_direct']


def test_statutory_article_caption_is_not_an_ordinary_size_restriction():
    f = facts('「중소기업제품 구매촉진 및 판로지원에 관한 법률」 '
              '제8조의2(중소기업자간 경쟁입찰 참여 제한 등)에 해당하는 자는 입찰에 참여할 수 없습니다.')
    assert f['no_size']


def test_cooperative_name_alone_does_not_restrict_ordinary_companies():
    branch = ('「중소기업협동조합법」 제3조에 따른 중소기업협동조합으로서 '
              '적격조합 확인서를 소지한 자는 입찰참가가 가능합니다.')
    assert facts(branch)['no_size']
    restricted = facts(CLAUSE+'\n※ '+branch)
    assert restricted['allowed'] == ['micro','small']
    assert not restricted['no_size']


def test_a_real_size_certificate_in_a_special_branch_still_blocks_absence():
    f = facts('※ 협동조합은 중소기업확인서를 소지한 조합원으로 구성하여야 합니다.')
    assert not f['no_size']


@pytest.mark.parametrize('denial', ['확인이 안 될 경우 입찰 참가자격이 없습니다.',
                                  '확인이 되지 않을 경우 입찰 참가자격이 없습니다.'])
def test_certificate_exclusion_still_blocks_absence_beside_a_special_branch(denial):
    f = facts('※ 소기업·소상공인확인서가 공공구매종합정보망에서 '+denial+
              '\n※ 중소기업으로 간주되는 특별법인은 입찰 참가 가능합니다.')
    assert f['allowed'] is None  # An exact set still needs exception-aware parsing.
    assert not f['no_size']
    ordinary = next(e for e in f['inventory'] if denial in e['evidence']['text'])
    assert ordinary['status'] == 'verification_or_exception_note'
    assert '특별법인' not in ordinary['evidence']['text']


def test_validity_note_alone_is_not_an_operative_allowed_set():
    f = facts('※ 소기업·소상공인확인서는 발급된 것으로 유효기간 내에 있어야 합니다.')
    assert f['allowed'] is None


@pytest.mark.parametrize('header', ['③ 제출서류', '5. 제출서류', '구비서류'])
def test_submission_scope_cannot_erase_an_explicit_eligibility_exclusion(header):
    f = facts('가. 일반 등록 업체\n'+header+'\n'
              '※ 소기업·소상공인확인서가 정보망에서 확인이 안 될 경우 입찰참가자격이 없습니다.\n'
              '※ 중소기업협동조합은 적격조합확인서를 소지한 경우 참가 가능합니다.')
    assert f['allowed'] is None and not f['active_size']
    assert not f['no_size']
    assert f['unresolved_size'][0]['reason'] == 'certificate_verification_exclusion_in_submission_scope'


def test_example_verification_text_is_not_a_required_submission():
    f = facts('가. 일반 등록 업체\n5. 제출서류 작성 예시\n'
              '소기업확인서가 정보망에서 확인이 안 될 경우 입찰참가자격이 없습니다.')
    assert f['no_size'] and not f['unresolved_size']


@pytest.mark.parametrize('header', [
    '4. 제출서류(아래 조건을 모두 충족할 것)',
    '4. 입찰참가자격(아래 조건을 모두 충족할 것이라는 작성 예시)',
    '4. 평가기준',
])
def test_forms_and_examples_do_not_open_operative_eligibility(header):
    assert facts(CLAUSE,header)['allowed'] is None
