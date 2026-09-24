"""Physical line wraps must preserve the observed scope of a floor disclosure."""
import pytest

from submission.pps.other_checks import sw_check


def record(disclosure, *, attachment=False):
    declaration = '본 사업은 소프트웨어사업이다.'
    docs = [{'doc_id': 'N1', 'type': '공고문', 'text': declaration}]
    if attachment:
        docs.append({'doc_id': 'R1', 'type': '제안요청서', 'text': disclosure})
    else:
        docs[0]['text'] += '\n' + disclosure
    return {'id': 'layout-control', 'meta': {'소관구분': '국가기관'},
            'docs': docs, 'input_completeness': {'완전관측': True},
            'dropped_doc_counts': {}}


@pytest.mark.parametrize('text', [
    '소프트웨어진흥법 제48조에 따른\n사업금액별 참여 제한을 적용한다.',
    '소프트웨어진흥법 제48조\n○ 적용사항: 사업금액별 참여 제한',
    '소프트웨어진흥법 제48조에\n따라 사업금액별 참여 제한을 적용한다.',
])
@pytest.mark.parametrize('attachment', [False, True])
def test_connected_floor_disclosure_preserves_source_coordinates(text, attachment):
    rec = record(text, attachment=attachment)
    result = sw_check(rec)
    assert result['value'] == 0
    proofs = result['facts']['floor_disclosure']
    assert proofs
    assert any('제48조' in p['quote'] and '사업금액별' in p['quote'] for p in proofs)
    for proof in proofs:
        original = rec['docs'][proof['doc_index']]['text']
        assert original[proof['start']:proof['end']] == proof['quote']


@pytest.mark.parametrize('text, expected', [
    ('소프트웨어진흥법 제48조를 참고한다.', 1),
    ('소프트웨어진흥법 제48조를 참고한다.\n'
     '다른 사업의 예시: 사업금액별 참여 제한을 적용한다.', 1),
    ('1. 관련 법령: 소프트웨어진흥법 제48조\n'
     '2. 지역 제한: 사업금액별 참여 제한을 적용한다.', 1),
    ('아래는 작성 예시이며 본 사업에는 적용하지 않는다.\n'
     '소프트웨어진흥법 제48조에 따른\n사업금액별 참여 제한을 적용한다.', None),
    ('소프트웨어진흥법 제48조에 따른\n사업금액별 참여 제한을 적용하지 않는다.', None),
    ('소프트웨어진흥법 제48조에 따라 사업금액별 참여 제한을\n적용하지 않는다.', None),
    ('소프트웨어진흥법 제48조에 따른\n\n사업금액별 참여 제한을 적용한다.', 1),
    ('소프트웨어진흥법 제48조에 따른\n○ 다른 조건: 사업금액별 참여 제한을 적용한다.', 1),
    ('소프트웨어진흥법 제48조 제3항의\n예외를 적용한다.', None),
    ('소프트웨어진흥법 제48조 제4항에 따라\n상호출자제한 대기업의 참여를 제한한다.', None),
])
def test_wrap_is_not_authority_to_merge_independent_or_nonoperative_clauses(text, expected):
    assert sw_check(record(text))['value'] is expected


def test_application_cannot_borrow_a_legal_basis_from_another_document():
    rec = record('소프트웨어진흥법 제48조를 참고한다.')
    rec['docs'].append({'doc_id': 'R1', 'type': '제안요청서',
                        'text': '사업금액별 참여 제한을 적용한다.'})
    assert not sw_check(rec)['facts']['floor_disclosure']
