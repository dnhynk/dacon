"""Table relations must belong to one original row and its own columns."""
import pytest

from submission.pps.qualification import inventory
from submission.pps.qualification_tables import certificate_rows
from submission.pps.table_structure import pipe_cells, pipe_separators
from tests.test_qualification_heading_roles import notice, CLAUSE
from tests.test_qualification_hierarchy import facts


@pytest.mark.parametrize('certificate,field', [
    ('직접생산확인증명서','active_direct'), ('소기업확인서','active_size')])
@pytest.mark.parametrize('direction', ['next', 'previous'])
def test_other_certificate_row_cannot_supply_the_possession_predicate(certificate, field, direction):
    target = certificate+' | 해당 없음 | 미제출'
    other = '사업자등록증 | 전체 입찰자 | 보유하여야 한다.'
    rows = [target,other] if direction == 'next' else [other,target]
    rec = notice('3. 입찰참가자격', '자격서류 | 적용대상 | 비고\n'+'\n'.join(rows))
    q = facts(rec)
    assert not q[field]
    assert all('\n' not in e['evidence']['text'] for e in q['inventory'])


@pytest.mark.parametrize('certificate,field', [
    ('직접생산확인증명서','active_direct'), ('소기업확인서','active_size')])
def test_same_row_explicit_bidder_possession_remains_operative(certificate, field):
    q = facts(notice('3. 입찰참가자격', '자격서류 | 적용대상 | 비고\n'+
        certificate+' | 전체 입찰자 | 보유하여야 한다.\n사업자등록증 | 전체 입찰자 | 제출'))
    assert len(q[field]) == 1


@pytest.mark.parametrize('target', ['해당 없음','제조사','낙찰 후 계약상대자','해당 시', ''])
def test_same_row_predicate_does_not_erase_target_or_stage(target):
    q = facts(notice('3. 입찰참가자격', '자격서류 | 적용대상 | 비고\n'+
        '직접생산확인증명서 | '+target+' | 보유하여야 한다.'))
    assert not q['active_direct']


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('fenced', [False, True])
def test_required_and_conditional_columns_survive_reordering_and_empty_cells(reverse, fenced):
    lines = (['제출서류 | 해당 시 | 필수', '중소기업확인서 | | ○', '직접생산확인증명서 | ○ |']
        if reverse else ['제출서류 | 필수 | 해당 시', '중소기업확인서 | ○ |', '직접생산확인증명서 | | ○'])
    if fenced:
        lines = ['| '+line+' |' for line in lines]
    q = facts(notice('3. 입찰참가자격', '\n'.join(lines)))
    assert not q['active_size'] and not q['active_direct']
    assert not q['no_size'] and not q['no_direct']
    assert q['unresolved_size'][0]['table_status'] == 'required_submission'
    assert q['unresolved_direct'][0]['table_status'] == 'conditional_submission'
    assert not q['unresolved_size'][0]['bidder_possession_certified']
    assert len(q['unresolved_size'][0]['cells']) == 3


@pytest.mark.parametrize('row,state', [
    ('중소기업확인서 | ○ | ○','marks_unresolved'),
    ('중소기업확인서 | △ |','marks_unresolved'),
    ('중소기업확인서 | |','unfilled_checklist'),
    ('중소기업확인서 | ○','column_alignment_unresolved'),
    ('중소기업확인서 | ○ | | 추가','column_alignment_unresolved'),
])
def test_conflicts_and_missing_cells_are_not_filled_to_prove_presence_or_absence(row, state):
    q = facts(notice('3. 입찰참가자격', '제출서류 | 필수 | 해당 시\n'+row))
    assert not q['active_size'] and not q['no_size']
    assert q['unresolved_size'][0]['table_status'] == state


def test_explicit_negative_column_cannot_borrow_required_parent_title():
    q = facts(notice('3. 입찰참가자격',
        '3.1. 필수 제출서류\n서류명 | 필수 | 해당 시\n중소기업확인서 | X |'))
    assert not q['unresolved_size'] and not q['active_size']


@pytest.mark.parametrize('parent', ['3.1. 제출서류 작성 예시','3.1. 낙찰 후 계약조건',
    '3.1. 평가기준','3.1. 선정 후 제출서류'])
def test_column_header_does_not_erase_example_or_later_stage_parent(parent):
    q = facts(notice('3. 입찰참가자격', parent+
        '\n제출서류 | 필수 | 해당 시\n중소기업확인서 | ○ |\n직접생산확인증명서 | ○ |'))
    assert not q['active_size'] and not q['active_direct']
    assert not q['unresolved_size'] and not q['unresolved_direct']


@pytest.mark.parametrize('qualifier,state', [('협력업체인 경우','conditional_submission'),
    ('전체 입찰자','required_submission')])
def test_row_scope_is_retained_even_with_required_mark(qualifier, state):
    q = facts(notice('3. 입찰참가자격',
        '서류명 | 필수 | 적용대상\n중소기업확인서 | ○ | '+qualifier))
    assert q['unresolved_size'][0]['table_status'] == state
    assert not q['active_size']


def test_different_table_header_replaces_column_ownership():
    text = ('서류명 | 필수 | 해당 시\n중소기업확인서 | ○ |\n'
        '서류명 | 해당 시 | 필수\n직접생산확인증명서 | ○ |')
    q = facts(notice('3. 입찰참가자격', text))
    assert q['unresolved_size'][0]['table_status'] == 'required_submission'
    assert q['unresolved_direct'][0]['table_status'] == 'conditional_submission'


def test_unknown_or_duplicate_header_cannot_select_a_convenient_column():
    q = facts(notice('3. 입찰참가자격',
        '서류명 | 필수 | 필수\n중소기업확인서 | ○ | X'))
    assert not q['active_size'] and not q['no_size']
    assert q['unresolved_size'][0]['table_status'] == 'column_alignment_unresolved'


def test_table_does_not_continue_across_plain_text_or_blank_line():
    for separator in ('\n설명 문장\n', '\n\n'):
        text = '서류명 | 필수 | 해당 시'+separator+'중소기업확인서 | ○ |'
        assert certificate_rows(text) == {}


def test_plain_wrapped_qualification_and_anonymous_attributes_are_not_table_rows():
    rec = notice('3. 입찰참가자격', CLAUSE)
    q = facts(rec)
    assert q['allowed'] == ['micro','small']
    token = '[수요기관(기초자치단체)|지역=r1]'
    text = '가. '+token+'의 소기업확인서를\n보유한 업체이어야 한다.'
    assert pipe_separators(text) == []
    assert len(inventory(notice('3. 입찰참가자격', text))[0][0]['evidence']['text'].splitlines()) == 2


def test_anonymous_region_attributes_stay_inside_one_source_cell():
    text = '자격서류 | [지역:r1|단위=기초|광역=서울특별시] | 비고'
    cells = pipe_cells(text,0,len(text))
    assert len(cells) == 3
    assert cells[1]['text'] == '[지역:r1|단위=기초|광역=서울특별시]'
    assert all(text[c['start']:c['end']] == c['text'] for c in cells)


def test_markdown_separator_is_not_a_certificate_row():
    q = facts(notice('3. 입찰참가자격',
        '| 서류명 | 필수 | 해당 시 |\n| --- | :---: | ---: |\n| 중소기업확인서 | ○ | |'))
    assert len(q['unresolved_size']) == 1
    assert q['unresolved_size'][0]['table_status'] == 'required_submission'


def test_submission_column_cannot_turn_its_same_row_into_eligibility():
    q = facts(notice('3. 입찰참가자격',
        '제출서류 | 적용대상 | 비고\n직접생산확인증명서 | 전체 입찰자 | 보유하여야 한다.'))
    assert not q['active_direct']


def test_required_parent_survives_a_submission_target_table_without_marks():
    q = facts(notice('3. 입찰참가자격',
        '3.1. 필수 제출서류\n제출서류 | 적용대상 | 비고\n직접생산확인증명서 | 전체 입찰자 | 사본'))
    assert q['unresolved_direct'] and not q['no_direct']
    assert not q['active_direct']


def test_certificate_named_in_a_note_is_not_the_document_required_by_that_row():
    q = facts(notice('3. 입찰참가자격',
        '서류명 | 필수 | 비고\n사업자등록증 | ○ | 소기업확인서와 구분'))
    assert not q['unresolved_size'] and not q['active_size']
