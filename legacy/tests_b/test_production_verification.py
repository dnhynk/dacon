"""The verified object, explicit exclusion and section role must agree."""
import pytest

from submission.pps.sme import direct_verification_requirement, extract_inventory, heading
from submission.pps.qualification import inventory


CLAUSE = ('직접생산여부 확인은 중소기업제품 공공구매 종합정보망(www.smpp.go.kr)에서 '
          '확인 가능하여야 하며, 확인이 되지 않을 경우 입찰 참가 자격이 없습니다.')


@pytest.mark.parametrize('subject', ['직접생산여부 확인은', '직접생산확인증명서는', '직접생산확인서는'])
@pytest.mark.parametrize('join', [' ', '\n\n'])
def test_production_verification_and_exclusion_are_an_operative_eligibility_fact(subject, join):
    text = '1. 입찰참가자격\n※ '+CLAUSE.replace('직접생산여부 확인은', subject).replace('하며, ', '하며,'+join)
    text += '\n2. 입찰일정\n내일 접수한다.'
    rec = {'docs': [{'doc_id': 'D0', 'type': '공고문', 'text': text}]}
    inventory = extract_inventory(rec)[0]
    own = [e for e in inventory if e['direct_requirement']]
    assert len(own) == 1 and own[0]['status'] == 'mandatory_eligibility'
    assert own[0]['direct_requirement_basis'] == 'mandatory_database_verification_with_exclusion'
    assert own[0]['codes'] == []  # No invented link to a neighboring code.
    ev = own[0]['evidence']
    assert text[ev['start']:ev['end']] == ev['text']


@pytest.mark.parametrize('text', [
    CLAUSE.replace('직접생산여부', '소기업'),
    CLAUSE.replace('확인 가능하여야 하며', '확인할 수 있으며'),
    CLAUSE.replace('자격이 없습니다', '자격에 영향이 없습니다'),
    CLAUSE.replace('입찰 참가 자격이 없습니다', '서류를 보완할 수 있습니다'),
    '직접생산확인서는 불필요합니다. '+CLAUSE.replace('직접생산여부 확인은', '소기업확인서는'),
    '직접생산여부는 조회할 수 있습니다. 확인이 되지 않을 경우 입찰 참가 자격이 없습니다.',
])
def test_a_different_object_or_optional_check_is_not_a_production_requirement(text):
    assert not direct_verification_requirement(text)


@pytest.mark.parametrize('heading', ['1. 제출서류', '1. 평가기준'])
def test_the_same_words_in_a_form_or_scoring_section_do_not_set_eligibility(heading):
    entries = extract_inventory({'docs': [{'type': '공고문', 'text': heading+'\n'+CLAUSE}]})[0]
    assert entries and all(e['status'] != 'mandatory_eligibility' for e in entries)


@pytest.mark.parametrize('label', ['참가자격', '입찰참가자격', '견적제출참가자격'])
def test_a_heading_with_an_explicit_governing_clause_is_not_a_warning_sentence(label):
    title = '3. '+label+': 아래의 자격을 모두 갖춘 자이어야 합니다.'
    text = title+'\n※ 자료를 제출하여야 하며, 미제출자는 참가자격이 없습니다.\n'
    text += '가. 소기업확인서를 소지한 자\n4. 제출일정\n내일 접수'
    rec = {'docs': [{'type': '공고문', 'text': text}]}
    entries = inventory(rec)[0]
    assert len(entries) == 1 and entries[0]['status'] == 'mandatory_eligibility'
    assert entries[0]['heading']['text'] == title


@pytest.mark.parametrize('bullet', ['-', '✓'])
def test_a_list_bullet_requiring_an_issued_certificate_is_not_a_verification_note(bullet):
    text = ('1. 입찰참가자격\n'+bullet+' 중소기업 확인기관에서 발급된 중소기업확인서를 소지한 자\n'
            '2. 입찰일정\n내일 접수')
    e = extract_inventory({'docs': [{'type': '공고문', 'text': text}]})[0][0]
    assert e['status'] == 'mandatory_eligibility' and 'medium' in e['size']['allowed']


def test_a_numbered_later_clause_cannot_finish_an_unrelated_production_note():
    text = ('1. 입찰참가자격\n직접생산 여부는 조회할 수 있으며\n'
            '2) 중소기업확인서를 소지한 자\n3. 입찰일정')
    entries = extract_inventory({'docs': [{'type': '공고문', 'text': text}]})[0]
    direct = next(e for e in entries if e['direct_production'])
    assert not direct['direct_requirement'] and '소지한' not in direct['evidence']['text']


def test_a_numbered_mandatory_check_does_not_close_the_eligibility_section():
    text = ('1. 입찰참가자격\n1) 직접생산확인증명서가 중소기업제품공공구매종합정보망에서 '
            '확인이 안 될 경우 입찰참가자격이 없습니다.\n나. 소기업확인서를 소지한 자\n2. 입찰일정')
    entries = inventory({'docs': [{'type': '공고문', 'text': text}]})[0]
    assert entries[0]['status'] == 'mandatory_eligibility' and entries[0]['direct_requirement']
    assert entries[1]['status'] == 'mandatory_eligibility'
