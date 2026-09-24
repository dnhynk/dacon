"""Registration ownership controls without development IDs or labels."""
import copy

import pytest

from submission.pps.comparison import compare, positive_decision
from submission.pps.temporal import industry_fields, region_clauses
from tests.test_comparison import record


@pytest.mark.parametrize('phrase,code', [
    ('학술연구용역(1169)', '1169'),
    ('기타자유업(행사대행업 : 9901)', '9901'),
    ('전기공사업[0037]', '0037'),
    ('정보통신공사업（0036）', '0036'),
])
def test_original_named_code_supports_its_own_registration(phrase, code):
    text = phrase + '으로 입찰 참가 등록을 필한 업체이어야 합니다.'
    rec = record(text, 면허업종제한목록='다른업종(9999)')
    facts = industry_fields(rec)
    assert len(facts) == 1 and facts[0]['value'] == code
    assert not facts[0]['assertion_scope_unresolved']
    assert facts[0]['extraction'] == 'original_name_code_pair'
    assert positive_decision(rec, compare(rec))['value'] == 1
    rec['meta']['면허업종제한목록'] = code
    assert positive_decision(rec, compare(rec)) is None


@pytest.mark.parametrize('text', [
    '학술연구용역(2026년 사업)에 참여하는 업체는 사업자 등록을 해야 한다.',
    '금속조립제품(세부품명번호 1234567890)으로 등록한 업체',
    '모델명 장치(1234)으로 입찰참가 등록한 업체',
    '연구용역(2026) 참여업체는 기타자유업으로 등록한 업체이어야 한다.',
    '연구용역(2026)\n• 전기공사업으로 등록한 업체이어야 한다.',
    '업종코드: 1169\n• 소상공인으로 등록한 업체만 참여할 수 있다.',
])
def test_year_product_model_or_adjacent_registration_does_not_prove_code_difference(text):
    rec = record(text, 면허업종제한목록='9999')
    assert positive_decision(rec, compare(rec)) is None


@pytest.mark.parametrize('suffix', [
    '으로 등록하지 않아도 참가할 수 있는 업체',
    ' 등록은 요구하지 않는 업체도 참가할 수 있다.',
    ' 또는 전기공사업(0037)으로 등록한 업체',
    '으로 등록한 업체이어야 하며 그 조건은 철회한다.',
])
def test_named_code_ownership_preserves_negation_alternative_and_withdrawal(suffix):
    rec = record('학술연구용역(1169)' + suffix, 면허업종제한목록='9999')
    assert positive_decision(rec, compare(rec)) is None


def test_unrelated_bullet_or_does_not_change_registration_predicate():
    text = ('• 전기공사업(0037)으로 등록한 업체이어야 한다\n'
            '• 소기업 또는 소상공인으로서 확인서를 소지한 업체이어야 한다\n'
            '• 공동수급은 불가합니다.')
    rec = record(text, 면허업종제한목록='0036')
    facts = industry_fields(rec)
    assert len(facts) == 1 and not facts[0]['alternative']
    assert '소기업' not in facts[0]['predicate_scope']
    assert positive_decision(rec, compare(rec))['value'] == 1


@pytest.mark.parametrize('bullet', ['•', '○', '◾', '※', '✓', '-', '3-2-1.'])
def test_long_region_predicate_ends_before_the_next_list_item(bullet):
    own = ('본점 소재지가 [수요기관(기초자치단체)]내에 소재하고 '
           '국가종합전자조달시스템 입찰참가자격등록 규정에 따라 '
           '입찰마감일 전일까지 다음의 자격을 모두 갖춘 자')
    text = own + '\n' + bullet + ' 납품장소는 경기도에 있는 업체입니다.'
    rec = record(text, 제한지역코드목록='경기도')
    facts = region_clauses(rec)
    assert len(facts) == 1 and facts[0]['value'] == []
    assert text[facts[0]['start']:facts[0]['end']] == own
    assert positive_decision(rec, compare(rec)) is None


def test_original_and_explicit_code_paths_deduplicate_and_ignore_record_identity():
    text = '학술연구용역(업종코드: 1169)으로 등록한 업체'
    rec = record(text, 면허업종제한목록='9999')
    before = copy.deepcopy(rec)
    first = compare(rec)
    assert len(industry_fields(rec)) == 1
    assert rec == before
    rec['id'] = 'any-renamed-record'
    assert compare(rec) == first


def test_short_region_eligibility_does_not_need_a_later_bidder_noun():
    own = '법인등기부상 본점소재지를 경상북도에 둔 자'
    rec = record(own + '\n\n• 본점소재지는 사업자등록증상 소재지이다.\n'
                 '나라장터에 등록한 업체', 제한지역코드목록='경상북도')
    facts = region_clauses(rec)
    assert len(facts) == 1
    assert facts[0]['evidence'] == own
    assert facts[0]['value'] == ['경상북도']
