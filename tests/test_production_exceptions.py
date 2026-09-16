"""A disclosed waiver, submission shortcut and denial have different objects."""
import pytest

from submission.pps.production_exceptions import exception_observations


@pytest.mark.parametrize(('clause', 'action'), [
    ('직접생산확인증명서를 별도로 요구하지 않습니다.', 'possession_or_requirement'),
    ('직접 생산 확인서를 보유할 필요가 없습니다.', 'possession_or_requirement'),
    ('직접생산확인증명서의 보유는 면제합니다.', 'possession_or_requirement'),
    ('직접생산확인서 제출은 생략합니다.', 'submission'),
    ('직접생산확인증명서 제출 요구를 면제합니다.', 'submission'),
    ('직접생산확인서는 보유해야 하며, 사본 제출은 생략합니다.', 'submission'),
    ('직접생산확인증명서는 추정가격 1.5천만 원으로 면제합니다.', 'unclear'),
])
def test_action_and_original_offsets_are_preserved_without_certifying_scope(clause, action):
    rec = {'docs': [{'doc_id': 'D1', 'type': '공고문', 'text': '앞문장\n'+clause+'\n뒷문장'}]}
    rows = exception_observations(rec)
    assert len(rows) == 1 and rows[0]['action'] == action
    assert not rows[0]['waiver_certified'] and not rows[0]['item_scope_certified']
    ev = rows[0]['evidence']
    assert rec['docs'][0]['text'][ev['start']:ev['end']] == ev['text'] == clause


@pytest.mark.parametrize('clause', [
    '직접생산확인증명서를 보유해야 합니다.',
    '직접생산확인증명서는 면제하지 않습니다.',
    '직접생산확인증명서를 면제할 수 없습니다.',
    '직접생산확인증명서는 생략할 수 없습니다.',
    '직접생산확인증명서 면제는 없습니다.',
    '직접생산확인증명서는 보유해야 하나 중소기업확인서는 불필요합니다.',
    '직접생산확인서를 보유해야 합니다. 제출일에 소기업확인서는 면제합니다.',
    '중소기업확인서 제출은 면제됩니다.',
])
def test_denial_or_an_exception_for_a_different_object_is_not_a_waiver(clause):
    assert exception_observations({'docs': [{'type': '공고문', 'text': clause}]}) == []


def test_unrecovered_line_order_is_not_silently_joined():
    assert not exception_observations({'docs': [{'type': '공고문',
        'text': '직접생산확인증명서\n중소기업확인서\n면제합니다.'}]})
