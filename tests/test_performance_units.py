"""Condition continuations must preserve source scope and proof purpose."""
import pytest

from submission.pps.performance import performance_facts
from submission.pps.rules import apply_rules


def record(body):
    return {'id': 'condition-unit', 'meta': {
        '적용계약법': '지방계약법', '업무구분': '일반용역',
        '입찰추정가격': 80_000_000, '배정예산금액': 88_000_000},
        'docs': [{'doc_id': 'notice', 'type': '공고문',
                  'text': '2. 입찰 참가자격\n' + body}]}


def test_wrapped_condition_links_amount_purchaser_and_bidder_with_exact_offsets():
    rec = record('가. 최근 5년 이내 국가 및 공공기관에서 발주한\n'
                 '단일 계약 2억원(부가세 포함) 이상의\n'
                 '운영 용역 수행 실적을 보유한\n기관이어야 합니다.')
    facts = performance_facts(rec)
    candidate, = facts['candidates']
    assert candidate['status'] == 'mandatory'
    assert candidate['required_money']['won'] == 200_000_000
    assert candidate['purchaser'] == 'specific_purchaser_required'
    assert [facts['overlays'][f'v{k}']['value'] for k in (2, 3, 4)] == [1, 1, 1]
    for evidence in (candidate['evidence'], candidate['required_money']['evidence']):
        assert evidence['text'] == rec['docs'][0]['text'][evidence['start']:evidence['end']]


@pytest.mark.parametrize('boundary', [
    '나. ', '2) ', '(2) ', '[2] ', '② ', '○ ', '※ ', 'A. ', 'I. ', 'ㅇ ', '❍ ', '◦ ',
    '3. 계약방법\n', '| 별도 항목 | ', '\n',
])
def test_never_borrow_money_or_purchaser_across_item_section_table_or_paragraph(boundary):
    rec = record('가. 국가 및 공공기관에서 발주한 단일 계약 2억원 이상\n'
                 + boundary + '용역 수행 실적이 있는 업체')
    facts = performance_facts(rec)
    assert facts['overlays']['v3']['value'] is None
    assert facts['overlays']['v4']['value'] is None


@pytest.mark.parametrize('subject', ['업체', '자', '법인', '단체', '기관', '사업자', '법인(단체)'])
def test_mandatory_experience_subjects(subject):
    facts = performance_facts(record(f'가. 용역 수행 실적이 있는 {subject}이어야 합니다.'))
    assert facts['overlays']['v2']['value'] == 1


@pytest.mark.parametrize('body', [
    '2.1. 등록한 업체\n· 제출서류: 등록증 1부\n· 제출방법: 우편\n'
    '2.2. 단일 용역 2억원 이상 수행 실적이 있는 업체',
    '가. 등록한 업체\n※ 제출서류: 등록증 1부\n'
    '나. 단일 용역 2억원 이상 수행 실적이 있는 업체',
    '○ 등록한 업체\n※ 제출서류: 등록증 1부\n'
    '○ 단일 용역 2억원 이상 수행 실적이 있는 업체',
])
def test_documents_subsection_ends_at_next_peer_eligibility_item(body):
    facts = performance_facts(record(body))
    assert facts['candidates'][-1]['section_role'] == 'eligibility'
    assert facts['overlays']['v3']['value'] == 1


@pytest.mark.parametrize('heading', ['3. 제출서류', '3. 정량평가 기준'])
def test_top_level_documents_or_scoring_do_not_return_to_old_eligibility(heading):
    facts = performance_facts(record('가. 등록한 업체\n' + heading + '\n'
        '가. 실적증명서 1부\n나. 단일 용역 2억원 이상 수행 실적이 있는 기관'))
    assert facts['overlays']['v2']['value'] is None
    assert facts['overlays']['v3']['value'] is None


def test_documents_bullets_keep_their_role_until_the_parent_item_ends():
    facts = performance_facts(record(
        '2.1. 등록한 업체\n· 제출서류: 등록증 1부\n'
        '· 제출대상: 단일 용역 2억원 이상 실적이 있는 기관\n'
        '2.2. 전자입찰 등록한 업체'))
    assert facts['candidates'][0]['section_role'] == 'forms'
    assert facts['overlays']['v3']['value'] is None


def test_eligibility_heading_annotation_does_not_hide_parent_role():
    rec = record('○ 등록한 업체\n※ 제출서류: 등록증 1부\n'
                 '○ 단일 용역 2억원 이상 실적이 있는 기관')
    rec['docs'][0]['text'] = rec['docs'][0]['text'].replace(
        '2. 입찰 참가자격', '2. 입찰 참가자격(아래 조건을 모두 갖춘 자이어야 합니다.)')
    facts = performance_facts(rec)
    assert facts['candidates'][0]['section_role'] == 'eligibility'
    assert facts['overlays']['v3']['value'] == 1


def test_completed_requirement_does_not_absorb_certificate_issuance_note():
    facts = performance_facts(record(
        '가. 공공기관 등을 대상으로 교육 실적 3천만원 이상 수행한 업체\n'
        '(실적은 발주기관에서 직접 발급받은 증명서로 확인)'))
    assert facts['candidates'][0]['purchaser'] == 'unspecified'
    assert facts['overlays']['v4']['value'] is None


def test_forms_heading_at_eligibility_heading_level_is_a_new_section():
    rec = record('• 입찰참가자격\n등록한 업체\n'
                 '○ 제출서류\n1) 제안서 1부\n'
                 '※ 연구실적 등 업체를 알 수 있는 표시는 기재 금지')
    facts = performance_facts(rec)
    assert facts['candidates'][0]['section_role'] == 'forms'
    assert facts['overlays']['v2']['value'] is None


def test_numbered_document_children_do_not_end_a_bulleted_documents_note():
    facts = performance_facts(record(
        '가. 등록한 업체\n· 제출서류\n1) 제안서 1부\n'
        '2) 단일 용역 2억원 이상 실적이 있는 기관의 증명서\n'
        '나. 전자입찰 등록한 업체'))
    assert facts['candidates'][0]['section_role'] == 'forms'
    assert facts['overlays']['v2']['value'] is None


@pytest.mark.parametrize('left,right', [('<', '>'), ('〈', '〉'), ('[', ']')])
def test_explicitly_deleted_clause_is_not_reactivated_by_eligibility_heading(left, right):
    facts = performance_facts(record(
        f'가. {left}삭제{right} 단일 계약 2억원 이상 수행 실적을 보유한 기관{left}삭제{right}'))
    assert facts['candidates'][0]['status'] == 'unresolved_modality'
    assert facts['overlays']['v2']['value'] is None
    assert facts['overlays']['v3']['value'] is None


@pytest.mark.parametrize('adjective', ['사실적', '사실적인', '현실적'])
def test_adjective_ending_is_not_experience_in_pledge(adjective):
    rec = record(f'가. 제안서를 {adjective} 근거에 의해 작성하며 허위이면 참가자격 박탈을 감수합니다.')
    row, _ = apply_rules(rec, {'v2': 0, 'e2': '', 'v8': 0, 'e8': ''}, items=(2, 8))
    assert row['v2'] == row['v8'] == 0
    assert performance_facts(rec)['candidates'] == []


@pytest.mark.parametrize('compound', ['공사실적', '행사실적', '용역실적', '납품실적'])
def test_genuine_compound_experience_nouns_are_preserved(compound):
    facts = performance_facts(record(f'가. 단일 계약 2억원 이상 {compound}이 있는 업체'))
    assert facts['overlays']['v3']['value'] == 1
