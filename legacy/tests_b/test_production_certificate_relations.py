"""A choice of certificates is not proof that every purchase target is covered."""
from types import SimpleNamespace

import pytest

from submission.pps.qualification import infer, inventory
from submission.pps.production_certificate import incorporated_registration_requirements


CATALOG = SimpleNamespace(products={
    '1111111111': {'세부품명': '시험품목가', '제품명': '시험품목가', '특이사항': ''},
    '2222222222': {'세부품명': '시험품목나', '제품명': '시험품목나', '특이사항': ''}})
A, B = '시험품목가[1111111111]', '시험품목나[2222222222]'


def record(clause, *, purchases=None, role='입찰참가자격'):
    return {'id': 'synthetic-certificate-relations', 'meta': {
        '적용계약법': '지방계약법', '업무구분': '물품(내자)', '계약방법': '제한경쟁',
        '입찰추정가격': 150_000_000, '세부품명번호목록': purchases or A+','+B},
        'docs': [{'doc_id': 'D0', 'type': '공고문', 'text': '1. '+role+'\n'+clause+'\n2. 제출서류\n사업자등록증 1부'}],
        'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


def inspect(clause, **kwargs):
    rec = record(clause, **kwargs)
    row, facts = infer(rec, {'v10': '1', 'e10': ''}, CATALOG)
    return row, facts


def incorporated(clause):
    rec = record(clause, purchases=A)
    parts = inventory(rec)
    return rec, incorporated_registration_requirements(rec, parts[0], parts[4])


def test_article9_registration_and_attached_validity_note_cover_the_exact_product():
    clause = ('가. 중소기업제품 구매촉진 및 판로지원에 관한 법률 제9조 및 같은 법 시행령 '
              '제10조에 따라 나라장터에 입찰서 제출 마감일 전일까지 '
              f'시험품목가(세부품명번호: 1111111111)로 등록한 자\n'
              '※ 직접생산확인증명서는 입찰서 제출마감일 전일까지 발급된 것으로서 '
              '유효기간 내에 있어야 함')
    rec, requirements = incorporated(clause)
    assert len(requirements) == 1
    assert requirements[0]['guaranteed_codes'] == ['1111111111']
    ev = requirements[0]['evidence']
    assert rec['docs'][ev['doc_index']]['text'][ev['start']:ev['end']] == ev['text']
    row, facts = infer(rec, {'v10': '1', 'e10': ''}, CATALOG)
    assert row['v10'] == '0'
    assert facts['decisions']['v10']['reason'] == 'all_identified_targets_have_possession_requirement'


@pytest.mark.parametrize('clause', [
    ('가. 중소기업제품 구매촉진 및 판로지원에 관한 법률에 따라 나라장터에 '
     '시험품목가(세부품명번호: 1111111111)로 등록한 자\n'
     '※ 직접생산확인증명서는 입찰서 제출마감일 전일까지 발급된 것으로서 유효기간 내에 있어야 함'),
    ('가. 중소기업제품 구매촉진 및 판로지원에 관한 법률 제9조에 따라 나라장터에 '
     '시험품목가(세부품명번호: 1111111111)로 등록한 자\n'
     '※ 직접생산확인증명서는 입찰서 제출마감일 전일까지 발급된 것으로서 유효기간 내에 있어야 함'),
    ('가. 중소기업제품 구매촉진 및 판로지원에 관한 법률 제9조 및 같은 법 시행령 '
     '제10조에 따라 나라장터에 시험품목가(세부품명번호: 1111111111)로 등록한 자\n'
     '※ 직접생산확인증명서를 제출한다.'),
    ('가. 중소기업제품 구매촉진 및 판로지원에 관한 법률 제9조 및 같은 법 시행령 '
     '제10조에 따라 나라장터에 시험품목가로 등록한 자\n'
     '※ 직접생산확인증명서는 입찰서 제출마감일 전일까지 발급된 것으로서 유효기간 내에 있어야 함'),
])
def test_incorporated_requirement_needs_both_statutes_validity_and_exact_code(clause):
    assert incorporated(clause)[1] == []


@pytest.mark.parametrize('clause', [
    f'가. 직접생산확인증명서({A} 또는 {B})를 소지한 업체',
    f'가. 직접생산확인증명서({A}) 또는 직접생산확인증명서({B})를 소지한 업체',
    f'가. 직접생산확인증명서({A}, {B}) 중 어느 하나를 소지한 업체',
    f'가. 직접생산확인증명서({A})를 소지한 업체 또는 직접생산확인증명서({B})를 소지한 업체',
])
def test_alternative_certificates_do_not_prove_both_products_required(clause):
    row, facts = inspect(clause)
    assert facts['product']['status'] == 'competition'
    assert row['v10'] == '1' and 'v10' not in facts['decisions']
    assert not facts['qualification']['no_direct']


@pytest.mark.parametrize('clause', [
    f'가. 직접생산확인증명서({A} 및 {B})를 모두 소지한 업체',
    f'가. 직접생산확인증명서({A})와 직접생산확인증명서({B})를 모두 소지한 업체',
    f'가. 직접생산확인증명서({A})를 소지한 업체\n나. 직접생산확인증명서({B})를 소지한 업체',
])
def test_independent_mandatory_certificates_can_cover_both_products(clause):
    row, facts = inspect(clause)
    assert row['v10'] == '0' and facts['decisions']['v10']['value'] == 0


@pytest.mark.parametrize('clause', [
    f'가. 업종코드 1234 또는 5678로 등록한 업체로서 직접생산확인증명서({A})를 소지한 업체',
    f'가. 직접생산확인증명서({A})를 소지한 업체이어야 하며 제출서류는 우편 또는 방문으로 제출한다.',
    f'가. 직접생산확인증명서({A} 또는 {A})를 소지한 업체',
])
def test_other_subject_options_or_identical_branches_do_not_erase_coverage(clause):
    row, facts = inspect(clause, purchases=A)
    assert row['v10'] == '0' and facts['decisions']['v10']['value'] == 0


def test_choice_of_certificate_and_unrelated_document_does_not_prove_certificate_requirement():
    clause = f'가. 직접생산확인증명서({A}) 또는 일반등록확인서를 소지한 업체'
    row, facts = inspect(clause, purchases=A)
    assert row['v10'] == '1' and 'v10' not in facts['decisions']


def test_a_shared_either_heading_does_not_turn_its_children_into_an_and():
    clause = ('다음 중 어느 하나의 자격을 갖춘 업체\n'
              f'가. 직접생산확인증명서({A})를 소지한 업체\n'
              f'나. 직접생산확인증명서({B})를 소지한 업체')
    row, facts = inspect(clause)
    assert row['v10'] == '1' and 'v10' not in facts['decisions']


@pytest.mark.parametrize('clause', [
    '※ 직접생산확인증명서는 입찰서 제출마감일 전일까지 발급된 것으로서 유효기간 내에 있어야 함',
    '직접생산확인증명서는 입찰마감일 전일까지 발급된 것이며 유효기간 내에 있어야 합니다.',
    '※ 직접생산확인증명서는 입찰서 제출마감일 전일까지 발급된 것으로서\n유효기간 내에 있어야 함',
])
def test_an_operative_certificate_validity_condition_blocks_certified_absence(clause):
    row, facts = inspect(clause)
    q = facts['qualification']
    assert not q['no_direct'] and not q['active_direct']
    assert 'v10' not in facts['decisions']


def test_a_certificate_date_example_does_not_become_a_possession_obligation():
    row, facts = inspect('예시: 직접생산확인증명서는 입찰마감일 전일까지 발급된 것으로서 유효기간 내에 있어야 함', role='서식 작성 예시')
    assert not facts['qualification']['active_direct']


@pytest.mark.parametrize('clause', [
    f'가. 세부품명 {B}를 등록한 업체로서 직접생산확인증명서({A})를 소지한 업체',
    f'가. 직접생산확인증명서({A})를 소지한 업체. 참고 품목은 {B}이다.',
    f'가. 직접생산확인증명서({A})와 일반등록확인서({B})를 소지한 업체',
    f'가. 직접생산확인증명서({A} 또는 일반등록확인서({A}))를 소지한 업체',
])
def test_unrelated_code_occurrences_do_not_supply_certificate_coverage(clause):
    _, facts = inspect(clause)
    coverage = facts['qualification']['direct_certificate_coverage']
    assert '2222222222' not in coverage['guaranteed_codes']
    assert 'v10' not in facts['decisions']
    if '또는' in clause:
        assert not coverage['guaranteed_codes']


@pytest.mark.parametrize('clause', [
    f'가. 해당 물품({A})의 직접생산확인증명서를 소지한 업체',
    f'가. {A}에 대한 직접생산확인증명서를 소지한 업체',
    f'가. 직접생산확인기준에 따라 세부품명 {A}를 소지한 업체',
])
def test_source_object_can_precede_the_certificate_name(clause):
    row, facts = inspect(clause, purchases=A)
    assert row['v10'] == '0'
    assert facts['qualification']['direct_certificate_coverage']['guaranteed_codes'] == ['1111111111']


def test_context_and_choice_guarantors_keep_exact_original_coordinates():
    rec = record('다음 중 어느 하나의 자격을 갖춘 업체\n'
        f'가. 직접 생산 확인 증명서({A})를 소지한 업체\n'
        f'나. 직접생산확인증명서({B})를 소지한 업체')
    _, facts = infer(rec, {'v10': '1', 'e10': ''}, CATALOG)
    for observation in facts['qualification']['direct_certificate_coverage']['observations']:
        assert observation['choice_governors']
        for ev in [observation['evidence'], observation['certificate_scope'], *observation['choice_governors']]:
            assert rec['docs'][ev['doc_index']]['text'][ev['start']:ev['end']] == ev['text']


def test_choice_governors_do_not_cross_documents_or_closed_source_sections():
    rec = record(f'가. 직접생산확인증명서({A})를 소지한 업체', purchases=A)
    rec['docs'][0]['text'] = ('1. 안내\n다음 중 어느 하나의 자격을 갖춘 업체\n'
        '2. 기타사항\n'+rec['docs'][0]['text'].replace('1.', '3.', 1).replace('2. 제출서류', '4. 제출서류'))
    rec['docs'].append({'doc_id': 'D1', 'type': '과업지시서',
        'text': '1. 입찰참가자격\n다음 중 어느 하나의 자격을 갖춘 업체\n2. 기타사항'})
    row, facts = infer(rec, {'v10': '1', 'e10': ''}, CATALOG)
    assert row['v10'] == '0'
    assert not facts['qualification']['direct_certificate_coverage']['observations'][0]['choice_governors']


@pytest.mark.parametrize('clause', [
    f'가. 직접생산확인증명서({A}) 또는 일반등록확인서를 소지한 업체',
    '다음 중 어느 하나의 자격을 갖춘 업체\n'
        f'가. 직접생산확인증명서({A})를 소지한 업체\n나. 일반등록확인서를 소지한 업체',
])
def test_optional_certificate_cannot_force_a_general_product_violation(clause):
    rec = record(clause, purchases=A)
    general = {'status': 'general', 'products': [{'code': '1111111111'}],
        'estimate_won': 150_000_000, 'uncertainty': []}
    row, facts = infer(rec, {'v12': '0', 'e12': ''}, CATALOG, product_override=general)
    assert row['v12'] == '0' and 'v12' not in facts['decisions']


@pytest.mark.parametrize('lead', ['예시: ', '참고용: ', '인용: '])
def test_a_validity_example_cannot_block_absence_as_an_operative_duty(lead):
    _, facts = inspect(lead+'직접생산확인증명서는 입찰마감일 전일까지 발급된 것으로서 유효기간 내에 있어야 함')
    assert not any(e.get('reason') == 'operative_certificate_validity_scope_unresolved'
        for e in facts['qualification']['unresolved_direct'])


@pytest.mark.parametrize('clause', [
    f'가. 예시: 직접생산확인증명서({A})를 소지한 업체',
    f'가. “직접생산확인증명서({A})를 소지한 업체”라는 문구는 참고 예시이다.',
    f'가. 인용: 직접생산확인증명서({A})를 소지한 업체',
])
def test_a_quoted_or_example_predicate_does_not_prove_required_coverage(clause):
    row, facts = inspect(clause, purchases=A)
    assert row['v10'] == '1' and 'v10' not in facts['decisions']


def test_quoting_a_certificate_name_does_not_quote_its_operative_predicate():
    row, facts = inspect(f'가. 「직접생산확인증명서({A})」를 소지한 업체', purchases=A)
    assert row['v10'] == '0'


@pytest.mark.parametrize('governor', [
    '다음 중 어느 하나를 갖춘 자',
    '아래의 자격 중 어느 하나에 해당하는 업체',
])
def test_choice_governor_word_order_does_not_change_its_child_relation(governor):
    row, facts = inspect(governor+'\n'+f'가. 직접생산확인증명서({A})를 소지한 업체\n'
        f'나. 직접생산확인증명서({B})를 소지한 업체')
    assert row['v10'] == '1' and 'v10' not in facts['decisions']


def test_other_certificate_validity_does_not_complete_a_production_date_predicate():
    _, facts = inspect('※ 직접생산확인증명서는 입찰마감일 전일까지 발급된 것이다.\n'
        '※ 소기업·소상공인 확인서는 유효기간 내에 있어야 합니다.')
    assert not any(e.get('reason') == 'operative_certificate_validity_scope_unresolved'
        for e in facts['qualification']['unresolved_direct'])


@pytest.mark.parametrize('clause', [
    '가. 입찰참가자는 해당 물품을 직접 생산하는 업체이어야 하며, 단순 유통 또는 OEM 방식의 납품은 허용하지 않음',
    '※ 직접생산여부 확인은 중소기업제품 공공구매종합정보망(www.smpp.go.kr)에서 확인 가능하여야 하며, 확인이 되지 않을 경우 입찰 참가 자격이 없습니다.',
])
def test_a_separate_production_obligation_survives_without_inventing_certificate_codes(clause):
    rec = record(clause, purchases=A)
    general = {'status': 'general', 'products': [{'code': '1111111111'}],
        'estimate_won': 150_000_000, 'uncertainty': []}
    row, facts = infer(rec, {'v12': '0', 'e12': ''}, CATALOG, product_override=general)
    coverage = facts['qualification']['direct_certificate_coverage']
    assert row['v12'] == '1' and facts['decisions']['v12']['value'] == 1
    assert not coverage['guaranteed_codes']
    assert any(e['production_required_in_every_branch'] for e in coverage['observations'])


def test_an_optional_manufacturing_branch_does_not_become_a_mandatory_certificate():
    rec = record('가. 등록증을 가진 업체 또는 해당 물품을 직접 생산하는 업체이어야 합니다.', purchases=A)
    general = {'status': 'general', 'products': [{'code': '1111111111'}],
        'estimate_won': 150_000_000, 'uncertainty': []}
    row, facts = infer(rec, {'v12': '0', 'e12': ''}, CATALOG, product_override=general)
    assert row['v12'] == '0' and 'v12' not in facts['decisions']


@pytest.mark.parametrize('connective,expected', [('또는', None), ('및', 0)])
def test_the_sibling_sme_consumer_uses_the_same_branch_coverage(connective, expected):
    from pathlib import Path
    from submission.pps.products import ProductFacts
    from submission.pps.sme import extract_sme_facts
    catalog = Path(__file__).resolve().parents[1]/'data_open/data/법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv'
    if not catalog.exists():
        pytest.skip('provided catalog unavailable')
    rec = record('가. 직접생산확인증명서(실물모형및전시물[6010989901]) '+connective+
        ' 직접생산확인증명서(전시회기획및대행서비스[8014198801])를 소지한 업체',
        purchases='6010989901,8014198801')
    facts = extract_sme_facts(rec, ProductFacts(catalog), product_override={
        'status': 'competition', 'uncertainty': [], 'identity_evidence': [],
        'supported_products': [{'code': '6010989901'}, {'code': '8014198801'}],
        'meta_codes': ['6010989901', '8014198801']})
    assert facts['decisions']['v10']['value'] == expected
