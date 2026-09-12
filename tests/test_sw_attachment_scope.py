"""Moving the same operative disclosure between supplied docs preserves facts."""
import pytest

from submission.pps.other_checks import sw_check


DISCLOSURE = '본 사업은 소프트웨어진흥법 제48조에 따른 사업금액별 참여 제한이 적용되며 대기업 참여가 제한됩니다.'


def rec(kind, wording=DISCLOSURE, complete=True):
    return {'meta': {'소관구분': '지방정부', '업무구분': '일반용역'},
            'docs': [{'type': '공고문', 'text': '본 사업은 소프트웨어 사업입니다.'},
                     {'type': kind, 'text': wording}],
            'input_completeness': {'완전관측': complete},
            'dropped_doc_counts': {} if complete else {'규격서': 1}}


@pytest.mark.parametrize('kind', ['공고문', '제안요청서', '과업지시서', '규격서', '기타'])
def test_same_disclosure_survives_attachment_role_move(kind):
    decision = sw_check(rec(kind))
    assert decision['value'] == 0
    proof = decision['facts']['floor_disclosure'][0]
    assert proof['quote'] == DISCLOSURE and proof['doc_type'] == kind


@pytest.mark.parametrize('kind', ['과업지시서', '규격서', '기타'])
def test_exception_in_attachment_needs_applicability_review(kind):
    decision = sw_check(rec(kind, '본 사업은 소프트웨어진흥법 제48조 제3항의 하한제도 적용 예외입니다.'))
    assert decision['value'] is None
    assert decision['facts']['exception_disclosure']


def test_a_law_citation_alone_does_not_create_disclosure():
    decision = sw_check(rec('과업지시서', '관련 법률: 소프트웨어진흥법 제48조.'))
    assert not decision['facts']['floor_disclosure']
    assert decision['value'] == 1


def test_dropped_attachment_is_not_legal_absence():
    decision = sw_check(rec('과업지시서', '첨부 세부 자료는 별도 제공.', complete=False))
    assert decision['value'] is None


def test_contradictory_attachment_does_not_certify_normal():
    record = rec('과업지시서')
    record['docs'].append({'type': '규격서', 'text': '소프트웨어진흥법 제48조 하한제도 적용 여부는 미정입니다.'})
    assert sw_check(record)['value'] is None


@pytest.mark.parametrize('kind', ['공고문', '제안요청서', '과업지시서', '규격서'])
def test_preceding_example_disclaimer_prevents_false_normal(kind):
    example = '다음 문구는 작성 예시이며 본 사업에는 적용하지 않는다.\n' + DISCLOSURE
    decision = sw_check(rec(kind, example))
    assert decision['value'] is None
    assert not decision['facts']['floor_disclosure']
    assert decision['facts']['unresolved_disclosures'][0]['quote'] == example
