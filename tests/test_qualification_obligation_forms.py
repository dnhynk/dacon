"""Local obligation grammar must retain heading, item and quotation scope."""
import pytest
from submission.pps.qualification import inventory, qualification_facts
from tests.test_qualification_heading_roles import notice

SIZE = '소기업·소상공인확인서를 소지한 업체'
DIRECT = '직접생산확인증명서[세부품명: 시험물품(1111111111)]를 소지한 업체'


def facts(clause):
    rec = notice('3. 입찰참가자격', clause)
    return qualification_facts(rec, inventory(rec))


@pytest.mark.parametrize('child,sibling', [
    ('가. 제출서류', '나.'), ('① 제출서류', '②'), ('1) 제출서류', '2)'),
])
def test_sibling_item_ends_submission_subsection(child, sibling):
    f = facts(child+'\n확인서 사본 각 1부\n'+sibling+' '+SIZE)
    assert f['allowed'] == ['micro','small']


@pytest.mark.parametrize('prefix', ['4. 제출서류\n', '붙임 : 1. 제출서류\n2. 계약조건\n'])
def test_attachment_and_top_level_forms_cannot_borrow_old_eligibility(prefix):
    f = facts('가. 일반 업체\n'+prefix+'나. '+DIRECT)
    assert not f['active_direct']


@pytest.mark.parametrize('caption', ['제출서류', '평가기준'])
def test_unnumbered_new_role_does_not_restore_old_lettered_members(caption):
    f = facts('가. 일반 등록 업체\n'+caption+'\n가. 사업자등록증 사본\n나. '+DIRECT)
    assert not f['active_direct']


@pytest.mark.parametrize('ending', ['소지 업체', '소지 업체(자)', '보유 업체'])
def test_nominal_holding_is_operative_under_eligibility(ending):
    f = facts('가. 직접생산확인증명서[세부품명: 시험물품(1111111111) '+ending)
    assert f['active_direct']
    assert f['direct_certificate_coverage']['guaranteed_codes'] == ['1111111111']


@pytest.mark.parametrize('ending', ['를 소지한 자이어야 합니다.', ' 소지 업체(자)'])
def test_unclosed_item_bracket_does_not_quote_the_holding_predicate(ending):
    f = facts('가. 직접생산확인증명서[세부품명: 시험물품(1111111111)'+ending)
    assert f['direct_certificate_coverage']['guaranteed_codes'] == ['1111111111']


@pytest.mark.parametrize('prefix', ['예시: ', '“', '[참고 문구: '])
def test_unclosed_item_repair_preserves_actual_reference_scope(prefix):
    f = facts('가. '+prefix+'직접생산확인증명서[세부품명: 시험물품(1111111111)를 소지한 업체')
    assert not f['direct_certificate_coverage']['guaranteed_codes']


@pytest.mark.parametrize('bullet', ['ㅇ', '○', '❍'])
def test_independent_bullets_do_not_share_an_or(bullet):
    f = facts(bullet+' 중소기업기본법 또는 소상공인법의 규정에 따라 등록하여야 합니다.\n'+bullet+' '+DIRECT)
    assert f['direct_certificate_coverage']['guaranteed_codes'] == ['1111111111']


def test_numbered_choice_governor_stops_before_later_sibling():
    f = facts('라. 다음 각 항(㉠, ㉡) 중 어느 하나의 자격을 갖춘 업체\n'
              '㉠ 소기업확인서를 소지한 업체\n㉡ 소상공인확인서를 소지한 업체\n'
              '마. 일반 등록 업체\n사. '+DIRECT)
    assert f['direct_certificate_coverage']['guaranteed_codes'] == ['1111111111']


def test_real_unnumbered_choice_governor_keeps_its_children_alternative():
    f = facts('다음 중 어느 하나의 자격을 갖춘 업체\n가. '+DIRECT+'\n나. 일반등록확인서를 소지한 업체')
    assert not f['direct_certificate_coverage']['guaranteed_codes']


def test_connective_holding_with_explicit_obligation():
    f = facts('가. 소기업 또는 소상공인으로서 소기업·소상공인확인서를 소지하고 다음 조건을 충족하여야 합니다.')
    assert f['allowed'] == ['micro','small']


def test_postposed_size_parenthesis_binds_registered_bidder():
    f = facts('가. 사업자등록을 한 자, (중기업, 소기업, 소상공인으로 제한)')
    assert f['allowed'] == ['medium','micro','small']


@pytest.mark.parametrize('clause', [
    '가. 중/소기업자 또는 소상공인으로서 중/소기업·소상공인확인서를 소지한 자',
    '가. 중·소기업자 또는 소상공인으로서 중·소기업·소상공인확인서를 소지한 자',
])
def test_medium_small_abbreviation_is_not_small_only(clause):
    assert facts(clause)['allowed'] == ['medium','micro','small']


@pytest.mark.parametrize('caption', ['4. 제출서류', '4. 평가기준', '4. 제출서류 작성 예시'])
def test_new_grammar_cannot_promote_forms_or_scoring(caption):
    f = facts(caption+'\n가. '+SIZE.replace('소지한 업체','소지 업체'))
    assert not f['active_size']


@pytest.mark.parametrize('clause', ['예시: 소기업확인서 소지 업체', '소기업확인서 소지 업체에 가점 부여'])
def test_nominal_reference_or_scoring_is_not_a_size_duty(clause):
    assert not facts('가. '+clause)['active_size']


def test_nominal_holder_in_an_exclusion_is_not_an_obligation():
    assert not facts('가. 소기업확인서 소지 업체는 참가 대상에서 제외한다.')['active_size']


def test_unrelated_korean_word_is_not_a_numbered_item():
    from submission.pps.sme import list_marker
    assert list_marker('끝. 안내') is None


def test_broad_all_conditions_preamble_can_have_a_narrow_certificate_operand():
    f = facts('본 입찰은 중소기업자간 제한경쟁입찰로 이루어지며 다음의 자격을 모두 갖추어야 합니다.\n'
              '가. 소기업확인서를 소지한 자')
    assert f['allowed'] == ['micro','small'] and not f['size_conflict']


def test_unresolved_restricted_competition_field_blocks_size_absence():
    rec = notice('3. 입찰참가자격 및 유의사항', '가. 일반 등록 업체')
    rec['docs'][0]['text'] = ('입찰방법: 제한경쟁(총액, 지역제한)이며 협상에 의한 계약, 소기업 및 소상공인\n'
                             + rec['docs'][0]['text'])
    f = qualification_facts(rec, inventory(rec))
    assert f['allowed'] is None and not f['active_size']
    assert not f['no_size']
    assert any(e['reason']=='size_in_restricted_competition_field' for e in f['unresolved_size'])

