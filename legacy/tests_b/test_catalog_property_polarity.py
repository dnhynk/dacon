"""A model cannot borrow another clause's negation or reverse an explicit denial."""
import pytest

from submission.pps import catalog_semantics as semantics
from tests.test_catalog_semantics import video, reading, obj, response


def interpret(line, polarity):
    rec, packet, knowledge = video(line)
    _, details = semantics.review(rec, response(obj(semantic_readings=[reading(packet, line, polarity=polarity)])), packet, knowledge)
    return details['conditions'][0]['semantic_candidates'][0]


@pytest.mark.parametrize('line', [
    '모든 납품영상에는 기관의 로고를 포함하지 않는다.',
    '모든 납품영상에는 기관의 로고를 삽입하지 않습니다.',
    '모든 납품영상에 발주기관 식별정보는 미포함이다.',
    '모든 납품영상에는 기관의 로고를 의무적으로 포함하지 않는다.',
])
def test_affirmed_cannot_reverse_an_explicit_bound_denial(line):
    result = interpret(line, 'affirmed')
    assert result['value'] is None and result['issue']


@pytest.mark.parametrize('line', [
    '모든 납품영상에는 기관의 로고를 포함하며 영상 길이는 제한하지 않는다.',
    '모든 납품영상에는 기관의 로고를 포함한다. 영상 길이는 제한하지 않는다.',
    '모든 납품영상에는 기관의 로고를 삽입하고 추가 자막은 넣지 않는다.',
])
def test_denial_is_bound_to_the_named_property_not_another_negative_clause(line):
    result = interpret(line, 'denied')
    assert result['value'] is None and result['issue']


@pytest.mark.parametrize('line', [
    '모든 납품영상에는 기관의 로고를 포함하지 않을 수 없다.',
    '모든 납품영상에는 기관의 로고를 포함하지 않는 것은 아니다.',
])
def test_double_negation_does_not_support_a_single_negative_property(line):
    result = interpret(line, 'denied')
    assert result['value'] is None and result['issue']


def test_reading_the_first_affirmative_clause_is_not_blocked_by_the_other_negative():
    result = interpret('모든 납품영상에는 기관의 로고를 포함하며 영상 길이는 제한하지 않는다.', 'affirmed')
    assert result['value'] is True and result['issue'] is None


def test_bound_negative_clause_is_not_blocked_by_other_affirmative_content():
    result = interpret('모든 납품영상에는 기관의 로고를 포함하지 않으며 교육 자막을 넣는다.', 'denied')
    assert result['value'] is False and result['issue'] is None


def test_an_adverb_does_not_detach_the_property_from_its_explicit_denial():
    result = interpret('모든 납품영상에는 기관의 로고를 별도로 삽입하지 않는다.', 'denied')
    assert result['value'] is False and result['issue'] is None


def test_unrelated_negation_cannot_complete_both_negative_catalog_branches():
    line = '모든 납품영상에는 기관의 로고를 포함하며 영상 길이는 제한하지 않는다.'
    rec, packet, knowledge = video('모든 납품영상은 공공기관 홍보용이 아니다.\n'+line)
    payload = obj(semantic_readings=[reading(packet, '홍보용이 아니다', field='public_agency_promotion', polarity='denied'),
                                     reading(packet, line, polarity='denied')])
    row, details = semantics.review(rec, response(payload), packet, knowledge)
    assert row is None and details['conditions'][0]['status'] == 'unknown'


def test_opposite_property_clauses_within_one_line_are_not_merged_into_one_fact():
    line = '납품영상에 기관의 로고를 포함하며 다른 영상에 기관의 로고를 포함하지 않는다.'
    for polarity in ('affirmed', 'denied'):
        assert interpret(line, polarity)['value'] is None


def test_polarity_relation_offsets_refer_to_exact_original_text():
    line = '모든 납품영상에는 기관의 로고를 포함하지 않으며 교육 자막을 넣는다.'
    result = interpret(line, 'denied')
    audit = result['interpretations'][0]['original_polarity_review']
    assert not audit['semantic_truth_certified']
    assert all(line[r['start']:r['end']] == r['text'] for r in audit['relations'])
