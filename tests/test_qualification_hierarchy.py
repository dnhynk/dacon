"""Visible section paths must preserve the applicable source governor."""
import copy

import pytest

from submission.pps.qualification import inventory, qualification_facts
from tests.test_qualification_heading_roles import notice, CLAUSE


def facts(rec):
    original = copy.deepcopy(rec)
    result = qualification_facts(rec, inventory(rec))
    assert rec == original
    def check(value):
        if isinstance(value, dict):
            if {'doc_index','start','end','text'} <= value.keys():
                assert rec['docs'][value['doc_index']]['text'][value['start']:value['end']] == value['text']
            for v in value.values():
                check(v)
        elif isinstance(value, list):
            for v in value:
                check(v)
    check(result)
    return result


@pytest.mark.parametrize('parent,child', [
    ('3. 입찰참가자격','3.1. 기업규모'),
    ('3. 입찰참가자격','3.1 기업규모'),
    ('3.1 입찰참가자격','3.1.1. 기업규모'),
    ('3. 입찰참가자격','3-1. 기업규모'),
    ('3 | 입찰참가자격','3.1. 기업규모'),
])
def test_descendant_preserves_the_operative_qualification_parent(parent, child):
    q = facts(notice(parent, child+'\n'+CLAUSE))
    assert q['allowed'] == ['micro','small']
    assert len(q['eligibility_sections']) == 1
    assert child in q['eligibility_sections'][0]['evidence']['text']
    entry = next(e for e in q['active_size'] if e['size'])
    assert entry['heading']['text'] == parent
    assert [h['text'] for h in entry['heading_ancestors']] == [parent,child]


@pytest.mark.parametrize('child', ['4.1. 기업규모','4. 기업규모','2.1. 기업규모'])
def test_orphan_and_sibling_do_not_inherit_the_earlier_qualification(child):
    q = facts(notice('3. 입찰참가자격', '가. 일반 업체\n'+child+'\n'+CLAUSE))
    assert not q['active_size']


@pytest.mark.parametrize('barrier', ['3.1. 작성 예시', '3.1. 낙찰 후 계약조건', '3.1. 평가기준'])
def test_descendant_of_an_example_or_later_stage_does_not_regain_eligibility(barrier):
    q = facts(notice('3. 입찰참가자격', barrier+'\n3.1.1. 기업규모\n'+CLAUSE))
    assert not q['active_size']


def test_sibling_recovers_eligibility_after_a_child_submission_section():
    q = facts(notice('3. 입찰참가자격',
        '3.1. 제출서류\n소기업확인서 사본\n3.2. 기업규모\n'+CLAUSE))
    assert q['allowed'] == ['micro','small']
    assert len(q['active_size']) == 1
    assert q['active_size'][0]['heading']['text'] == '3. 입찰참가자격'


@pytest.mark.parametrize('child', ['5.1. 기업 확인서','5.1 기업 확인서','5-1. 기업 확인서',
    '5.1. 제출서류 목록\n5.1.1. 기업 확인서'])
def test_required_list_descendants_block_absence_without_certifying_possession(child):
    rec = notice('3. 입찰참가자격', '가. 일반 업체')
    rec['docs'].append({'type':'제안요청서','doc_id':'rfp','text':
        '5. 필수 제출서류\n'+child+'\n중소기업확인서 및 직접생산확인증명서 각 1부\n6. 기타사항'})
    q = facts(rec)
    assert not q['active_direct'] and not q['active_size']
    assert not q['no_direct'] and not q['no_size']
    assert q['unresolved_direct'] and q['unresolved_size']
    assert q['unresolved_direct'][0]['governor']['text'] == '5. 필수 제출서류'


@pytest.mark.parametrize('child', ['5.1. 제출서류 작성 예시','5.1. 제출서류 (해당 시)',
    '6.1. 제출서류'])
def test_example_conditional_or_orphan_forms_do_not_borrow_requiredness(child):
    rec = notice('3. 입찰참가자격', '가. 일반 업체')
    rec['docs'].append({'type':'제안요청서','doc_id':'rfp','text':
        '5. 필수 제출서류\n'+child+'\n중소기업확인서 및 직접생산확인증명서 각 1부\n7. 기타사항'})
    q = facts(rec)
    assert not q['unresolved_direct'] and not q['unresolved_size']
    assert not q['active_direct'] and not q['active_size']


@pytest.mark.parametrize('in_attachment', [False,True])
def test_one_closed_qualification_does_not_certify_another_unclosed_scope(in_attachment):
    rec = notice('3. 입찰참가자격', '가. 일반 업체')
    tail = '\n6. 입찰참가자격\n가. 다음 페이지에 계속'
    if in_attachment:
        rec['docs'].append({'type':'제안요청서','doc_id':'rfp','text':tail})
    else:
        rec['docs'][0]['text'] += tail
    q = facts(rec)
    assert q['closed_eligibility'] and q['unclosed_eligibility']
    assert not q['no_size'] and not q['no_direct']


@pytest.mark.parametrize('boundary', ['4 | 입찰보증금 및 세입조치','❍ 계약조건',
    '4.1. 낙찰자 결정방법','4 | | 입찰의 무효'])
def test_explicit_other_stage_does_not_expand_the_qualification_to_later_requirements(boundary):
    q = facts(notice('3. 입찰참가자격', '가. 일반 업체\n'+boundary+'\n'+CLAUSE))
    assert not q['active_size']
    assert boundary not in q['eligibility_sections'][0]['evidence']['text']


@pytest.mark.parametrize('scalar', ['2.5 GHz','2026.4.15. 공고일','1.2 mm','3.14억원','1.5 V','2.3 세트'])
def test_quantities_and_dates_cannot_change_the_governing_parent(scalar):
    q = facts(notice('3. 입찰참가자격', scalar+'\n'+CLAUSE))
    assert q['allowed'] == ['micro','small']


@pytest.mark.parametrize('label', ['기타','계약 체결','협상 및\n계약 체결'])
def test_generic_cells_and_process_labels_cannot_close_the_qualification(label):
    q = facts(notice('3. 입찰참가자격', label+'\n'+CLAUSE))
    assert q['allowed'] == ['micro','small']


def test_an_unresolved_scope_preserves_the_model_value_instead_of_proving_normality():
    from types import SimpleNamespace
    from submission.pps.qualification import infer
    rec = notice('3. 입찰참가자격', '가. 일반 업체')
    rec['docs'][0]['text'] += '\n6. 입찰참가자격\n다음 페이지에 계속'
    rec['meta'].pop('세부품명번호목록')
    rec['docs'][0]['text'] = rec['docs'][0]['text'].replace('농림수산연구조사서비스', '미확정 과업')
    # No supplied catalog identity: this guard may abstain, but cannot decide 0.
    row, detail = infer(rec, {'v10':'1','e10':''}, SimpleNamespace(products={}))
    assert row['v10'] == '1' and 'v10' not in detail['decisions']
