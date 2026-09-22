"""Observation packets stay stable while comparison policy changes."""
import pytest

from submission.pps.comparison import compare, positive_decision, priority_ranges, prompt_packet
from submission.pps.retrieval import Span
from submission.pps.temporal import contract_fields, industry_fields
from tests.test_comparison import record


def displayed(rec, packet, spans=None):
    if spans is None:
        spans = [Span(i, d['type'], 0, len(d['text']), d['text'])
                 for i, d in enumerate(rec['docs'])]
    return {f['field']: f for f in prompt_packet(packet, spans, rec=rec)['fields']}


@pytest.mark.parametrize('prefix', [
    '소액수의 견적제출 안내공고\n',
    '장비 운영(일반경쟁·1억원미만)\n',
])
def test_unresolved_method_retains_observation_and_priority(prefix):
    rec = record(prefix + '입찰방법: 제한경쟁', 계약방법='일반경쟁')
    packet = compare(rec)
    observation = next(f for f in packet['source_observations'] if f['field'] == 'competition_method')
    assert observation['scope'] == 'whole'
    assert (0, observation['start'], observation['end']) in priority_ranges(packet)
    field = displayed(rec, packet)['계약방법']
    assert field['comparison'] == 'method_relation_unresolved'
    assert field['observed'] == [dict(value='제한경쟁', doc_type='공고문', scope='whole',
                                    basic_level=False, alternative=False, S=[1], source_shown=True)]
    assert positive_decision(rec, packet) is None
    # The v10 caller can still explicitly request unresolved method facts.
    assert contract_fields(rec) == []
    assert contract_fields(rec, include_unresolved=True)[0]['assertion_scope_unresolved']


def test_modifier_extraction_changes_conclusion_without_inventing_observation():
    rec = record('입찰방법: 일반(단가)경쟁입찰', 계약방법='제한경쟁')
    packet = compare(rec)
    assert not packet['source_observations']
    assert priority_ranges(packet) == []
    assert positive_decision(rec, packet)['value'] == 1
    field = displayed(rec, packet)['계약방법']
    assert field['comparison'] == 'different' and field['observed'] == []
    assert displayed(rec, packet, [])['계약방법']['comparison'] == 'source_omitted'


def test_wrapped_license_policy_does_not_move_the_observed_predicate():
    text = ('다음 각호 중 어느 하나에 해당하는 업체\n'
            '1) 처리업(업종코드: 1234,\n\n처리시설을 갖춘 경우)과 운반업(업종코드: 5678) 허가를 받은 업체\n'
            '2) 처리업(업종코드: 1234,\n\n장비를 갖춘 경우) 허가를 받은 업체')
    rec = record(text, 면허업종제한목록='처리업(1234)')
    packet = compare(rec)
    assert {f['value'] for f in packet['facts']} == {'1234', '5678'}
    observed = industry_fields(rec, observation_only=True)
    assert {f['value'] for f in observed} == {'5678'}
    assert priority_ranges(packet) == [(f['doc_index'], f['start'], f['end']) for f in observed]
    field = displayed(rec, packet)['면허업종제한목록']
    assert field['comparison'] == 'and_or_scope_unresolved'
    assert [f['value'] for f in field['observed']] == ['5678']
    assert positive_decision(rec, packet) is None


def test_observation_mode_preserves_small_total_quotation_value():
    rec = record('입찰방법: 소액(총액)수의, 제한경쟁')
    assert contract_fields(rec, observation_only=True)[0]['value'] == '수의계약'


def test_observation_packet_does_not_change_when_metadata_changes():
    rec = record('입찰방법: 제한경쟁\n운송업(업종코드: 3456)으로 등록한 업체')
    before = compare(rec)
    rec['meta'].update(계약방법='일반경쟁', 면허업종제한목록='운송업(7890)')
    after = compare(rec)
    assert before['source_observations'] == after['source_observations']
    assert priority_ranges(before) == priority_ranges(after)
    assert before['comparisons'] != after['comparisons']
