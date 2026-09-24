import copy

import pytest

from submission.pps.reference_coverage import assess
from tests.test_comparison import record
from tests.test_reference_coverage import TEXT, facts


@pytest.mark.parametrize('statement', [
    '- 기타 관계법령 및 제안요청서 서식에 따른 서류 일체 각 1부',
    '제안요청서에 따른 서류 일체를 제출하여야 한다.',
    '과업지시서에서 정한 서류 각 1부',
    '제안요청서상의 서류 전부를 제출한다.',
    '규격서의 서식에 의한 서류 각 2부를 제출해야 한다.',
    '제안요청서 서식에 따른\n서류 일체 각 1부',
    '제안요청서에 따른 서류\n전부를 제출하여야 한다.',
])
def test_unavailable_form_contents_prevent_certifying_no_requirement(statement):
    rec = record(TEXT + statement)
    before = copy.deepcopy(rec)
    result = facts(rec)
    assert not result['no_direct'] and not result['no_size']
    assert not result['reference_coverage']['qualification_deferrals_resolved']
    assert rec == before
    for reference in result['reference_coverage']['referenced_unavailable_docs']:
        ev = reference['evidence']
        assert rec['docs'][ev['doc_index']]['text'][ev['start']:ev['end']] == ev['text']
        assert reference['qualification_deferral']


@pytest.mark.parametrize('statement', [
    '제안요청서 서식에 따른 서류는 제출하지 않는다.',
    '제안요청서 서식에 따른 서류 각 1부를 제출하지 않는다.',
    '제안요청서 서식에 따른 서류는 제출할 필요가 없다.',
    '제안요청서 서식에 따른 서류는 제출할 수 있다.',
    '제안요청서 서식에 따른 서류는 작성 방법의 참고용이다.',
    '참고용 제안요청서 서식에 따른 서류 각 1부',
    '낙찰 후 제안요청서 서식에 따른 서류 각 1부',
    '낙찰자는 제안요청서 서식에 따른 서류 각 1부를 제출한다.',
    '계약상대자는 규격서에 따른 서류 일체를 제출한다.',
    '낙찰 후 제출서류\n제안요청서에 따른 서류 각 1부',
    '제안요청서 서식에 따른 서류\n\n각 1부',
    '제안요청서 서식에 따른 서류는 검토한다. 사업자등록증은 1부 제출한다.',
])
def test_nonoperative_form_reference_does_not_borrow_an_obligation(statement):
    result = assess(record(TEXT + statement))
    assert result['qualification_deferrals_resolved']
    assert not any(r['qualification_deferral'] for r in result['references'])


def test_present_document_resolves_availability_without_certifying_its_conditions():
    rec = record(TEXT + '제안요청서에 따른 서류 일체를 제출한다.', '별도 요구조건 검토가 필요한 문서')
    result = assess(rec)
    assert result['qualification_deferrals_resolved']
    assert result['references'][0]['qualification_deferral']
    assert result['reference_contents_inferred'] is False


def test_predicate_stays_with_the_original_document_occurrence():
    rec = record(TEXT + '납품방법은 과업지시서를 참조한다. 제안요청서에 따른 서류 각 1부')
    result = assess(rec)
    assert {r['referenced_role'] for r in result['references'] if r['qualification_deferral']} == {'제안요청서'}


def test_unavailable_forms_stop_new_absence_positive_without_rewriting_a_saved_positive():
    from submission.pps.knowledge import Knowledge
    from tests.test_independent_audit import DATA, synthetic_notice
    knowledge = Knowledge(DATA)
    rec = synthetic_notice(TEXT + '제안요청서 서식에 따른 서류 일체 각 1부',
        업무구분='물품(내자)', 세부품명번호목록='서적[5510151001]', 입찰추정가격=150_000_000)
    for existing in ('0', '1'):
        row, details = knowledge.qualification_decisions(rec, {'v16': existing, 'e16': ''})
        assert details['product']['status'] == 'general'
        assert not details['qualification']['no_size']
        # The general size block (DESIGN_B 1-2) reads only an observed size bound, not an unavailable form.
        assert 'v16' not in details['decisions'] and row['v16'] == '1'
