"""An independent previous duty must not own a following comparison field."""
import copy

import pytest

from submission.pps.comparison import compare, positive_decision


REGION = '라. 주된 영업소 소재지가 경기도 또는 서울특별시에 있는 업체이어야 합니다.'
METHOD = '라. 계약방법: 일반경쟁'


def record(text):
    return {'id': 'synthetic-comparison-governor', 'meta': {
        '제한지역코드목록': '경기도', '계약방법': '제한경쟁'},
        'docs': [{'doc_id': 'D0', 'type': '공고문', 'text': text}]}


@pytest.mark.parametrize('prefix', [
    '다. 본 용역은 공동수급을 허용하지 않습니다.',
    '다. 공동수급: 불가',
    '3) 하도급은 허용하지 않는다.',
    '○ 계약보증금은 납부하지 않습니다.',
    '제안서 제출은 우편으로 하지 않습니다.',
    '입찰보증금: 면제 대상이 아닌 업체는 납부하여야 한다.',
])
@pytest.mark.parametrize('body,field', [(REGION, 'region'), (METHOD, 'competition_method')])
def test_preceding_independent_duty_does_not_remove_source_difference(prefix, body, field):
    r = record(prefix + '\n' + body)
    original = copy.deepcopy(r)
    packet = compare(r)
    comparison = next(c for c in packet['comparisons'] if c['field'] == field)
    assert comparison['status'] == 'different'
    decision = positive_decision(r, packet)
    assert decision is not None and decision['item'] == 24
    assert decision['evidence'] == body
    assert r == original
    source = packet['facts'][comparison['comparable_fact_indices'][0]]
    assert source['start'] == len(prefix) + 1
    assert r['docs'][0]['text'][source['start']:source['end']] == body


@pytest.mark.parametrize('prefix', [
    '다음은 작성 예시이며 본 공고에 적용하지 않는다.',
    '예시:',
    '아래 조건은 적용하지 않습니다.',
    '위 지역제한은 적용하지 않는다.',
    '공동수급은 허용하지 않고 아래 지역제한도 적용하지 않는다.',
    '공동수급은 다음 조건을 충족하는 경우에만 가능하다.',
    '미확정 사항은 다음과 같으며 그대로 적용하지 않는다.',
])
def test_related_or_unresolved_prefix_still_blocks_forced_region_difference(prefix):
    r = record(prefix + '\n' + REGION)
    assert positive_decision(r, compare(r)) is None


@pytest.mark.parametrize('opening,closing', [
    ('예시:\n', ''),
    ('1. 작성 예시\n', ''),
    ('“참고 문구\n', '\n”'),
    ('[작성 양식\n', '\n]'),
])
def test_outer_example_or_quotation_is_not_escaped_by_independent_duty(opening, closing):
    text = opening + '다. 본 용역은 공동수급을 허용하지 않습니다.\n' + REGION + closing
    r = record(text)
    assert positive_decision(r, compare(r)) is None


def test_explicit_return_from_example_allows_current_competition_field():
    text = ('1. 작성 예시\n공동수급은 허용하지 않습니다.\n계약방법: 제한경쟁\n'
            '2. 실제 공고내용\n다. 본 용역은 공동수급을 허용하지 않습니다.\n' + METHOD)
    r = record(text)
    packet = compare(r)
    # The historical example remains a separate unresolved observation; the
    # current source may supply its own distinct, correctly bounded value.
    current = [x for x in packet['facts'] if x['field'] == 'competition_method' and x['start'] == text.rfind(METHOD)]
    assert len(current) == 1 and current[0]['scope'] == 'whole'


@pytest.mark.parametrize('ending', [
    '계약방법: 제한경쟁이 아닌 일반경쟁으로 진행한다.',
    '지역제한 없이 주된 영업소 소재지가 경기도 또는 서울특별시에 있는 업체도 참가할 수 있다.',
])
def test_negation_inside_target_clause_is_preserved(ending):
    r = record('공동수급은 허용하지 않습니다.\n' + ending)
    assert positive_decision(r, compare(r)) is None


@pytest.mark.parametrize('tail', [
    '로 제한하지 않습니다.',
    '로 한정하지 않습니다.',
    '로\n제한하지 않습니다.',
    '\n로 제한하지 않습니다.',
    '이어야 한다고 가정한 작성 예시입니다.',
])
def test_withdrawal_after_the_matching_bidder_noun_is_not_cut_off(tail):
    body = '주된 영업소 소재지가 경기도 또는 서울특별시에 있는 업체' + tail
    r = record(body)
    assert positive_decision(r, compare(r)) is None


def test_following_other_duty_cannot_cancel_office_predicate():
    body = '주된 영업소 소재지가 경기도 또는 서울특별시에 있는 업체이어야 하며 공동수급은 허용하지 않습니다.'
    r = record(body)
    assert positive_decision(r, compare(r)) is not None


def test_wrapped_reference_withdraws_the_same_region_requirement():
    r = record(REGION + '\n위 지역제한은 적용하지 않습니다.')
    assert positive_decision(r, compare(r)) is None


@pytest.mark.parametrize('separator', [' ', '\n'])
def test_separate_later_duty_does_not_cancel_completed_region_sentence(separator):
    r = record(REGION + separator + '공동수급은 허용하지 않습니다.')
    assert positive_decision(r, compare(r)) is not None


def test_same_line_reference_withdraws_the_completed_region_sentence():
    r = record(REGION + ' 위 지역제한은 적용하지 않습니다.')
    assert positive_decision(r, compare(r)) is None


def test_excessive_related_continuations_fail_closed():
    r = record(REGION + '\n' + '\n'.join(['위 조건을 확인해야 합니다.'] * 5))
    packet = compare(r)
    assert positive_decision(r, packet) is None
    fact = next(x for x in packet['facts'] if x['field'] == 'region')
    assert fact['scope'] == 'assertion_unresolved' and fact['source_context_truncated'] is True


@pytest.mark.parametrize('tail', [
    '이어야 합니다.(지사투찰 불허, 공동도급 불허)',
    '(지사투찰 불허)',
    '이어야 합니다.（공동수급은 허용하지 않습니다.）',
    '로서 발주기관에서 제시하는 물품의 제품번호 및 규격과 동등하거나 동등 이상의 성능 및 품질을 유지하는 규격품으로 납품하여야 합니다.',
    '로서 납품 물품의 규격은 기준 성능 미만으로 변경할 수 없습니다.',
])
def test_other_duty_qualifiers_do_not_negate_the_region(tail):
    body = '주된 영업소 소재지가 경기도 또는 서울특별시에 있는 업체' + tail
    rec = record(body)
    packet = compare(rec)
    assert positive_decision(rec, packet) is not None
    fact = next(x for x in packet['facts'] if x['field'] == 'region')
    assert fact['scope'] == 'whole'
    assert rec['docs'][0]['text'][fact['start']:fact['end']] == body


@pytest.mark.parametrize('tail', [
    '(지사투찰 불허, 위 지역제한은 적용하지 않습니다.)',
    '(지사투찰 불허, 단순 작성 예시)',
    '(위 지역제한은 적용하지 않습니다.)',
    '로서 납품 물품은 동등 이상 규격이나 위 지역제한은 적용하지 않습니다.',
    '로서 다음 조건이 확정되지 않았으므로 적용하지 않습니다.',
])
def test_related_or_unknown_trailing_qualifier_is_preserved(tail):
    rec = record('주된 영업소 소재지가 경기도 또는 서울특별시에 있는 업체' + tail)
    assert positive_decision(rec, compare(rec)) is None
