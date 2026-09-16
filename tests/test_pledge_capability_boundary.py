"""A capacity to submit does not establish completed pre-bid possession."""
import copy

import pytest

from submission.pps.other_checks import pledge_check
from submission.pps.rules import apply_rules
from tests.test_pledge_modality import notice


@pytest.mark.parametrize('predicate', [
    '제출이 가능한 업체', '제출 가능한 업체이어야 한다.',
    '제출할 수 있는 업체',
])
def test_capacity_quote_does_not_support_a_model_pre_bid_obligation(predicate):
    quote = '제조사의 정품공급 및 기술지원 확약서를 ' + predicate
    rec = notice(quote)
    original = copy.deepcopy(rec)
    row, trace = apply_rules(rec, {'v19': 1, 'e19': quote}, items=(19,))
    assert row['v19'] == 0 and row['e19'] == ''
    guard = next(x for x in trace if x['source'] == 'pledge_witness_validation')
    assert guard['semantic_value'] is None and guard['absence_verified'] is False
    assert guard['rejected_witness'] == quote
    assert rec == original
    assert pledge_check(rec)['value'] is None  # Not full-document normality.


@pytest.mark.parametrize('predicate', [
    '제출할 수 있는 업체이어야 한다.', '제출이 가능하다.',
    '보유할 수 있다.', '발급받을 수 있다.',
])
def test_cross_line_capacity_does_not_turn_a_later_requirement_into_an_early_one(predicate):
    text = ('1. 계약 체결 시 제출서류\n가. 제조사의 기술지원확약서\n'
            '해당 확약서는 입찰 전에 ' + predicate)
    result = pledge_check(notice(text))
    assert result['value'] == 0
    assert result['facts']['cross_line_reference_events']['bound'] == []


@pytest.mark.parametrize('predicate', [
    '발급받을 예정이다.', '제출 시점은 미정이다.',
])
def test_an_unresolved_reference_blocks_an_all_later_certificate(predicate):
    text = ('1. 계약 체결 시 제출서류\n가. 제조사의 기술지원확약서\n'
            '해당 확약서는 입찰 전에 ' + predicate)
    assert pledge_check(notice(text))['value'] is None


def test_other_source_early_possession_survives_a_bad_capacity_witness():
    quote = '제조사의 기술지원확약서를 제출할 수 있는 업체'
    rec = notice(quote + '\n제조사의 기술지원확약서는 입찰 전에 보유하여야 한다.')
    row, trace = apply_rules(rec, {'v19': 1, 'e19': quote}, items=(19,))
    assert row['v19'] == 1 and '보유하여야' in row['e19']
    assert any(x['source'] == 'pledge_witness_validation' for x in trace)


@pytest.mark.parametrize('quote', [
    '제조사의 기술지원확약서는 입찰 전에 보유하여야 하며 사본 제출이 가능한 업체',
    '제조사의 기술지원확약서를 제출할 수 있는 업체라는 해석은 잘못이다.',
    '제조사의 기술지원확약서를 제출할 수 있는 업체가 아니라 직접 보유한 업체',
    '제조사의 기술지원확약서 1부',
    '제조자증명서 또는 판매대리점 계약서 원본을 입찰 전에 제출한다.',
])
def test_a_capacity_fragment_or_an_unresolved_document_cannot_clear_the_model(quote):
    row, trace = apply_rules(notice(quote), {'v19': 1, 'e19': quote}, items=(19,))
    assert row['v19'] == 1
    assert not any(x['source'] == 'pledge_witness_validation' for x in trace)


def test_every_original_occurrence_must_have_the_capacity_only_context():
    quote = '제조사의 기술지원확약서를 제출할 수 있는 업체'
    rec = notice(quote + '\n' + quote + '이며 입찰 전까지 보유하여야 한다.')
    row, trace = apply_rules(rec, {'v19': 1, 'e19': quote}, items=(19,))
    assert not any(x['source'] == 'pledge_witness_validation' for x in trace)


def test_a_truncated_or_nonoriginal_witness_is_not_repaired_by_substring():
    quote = '제조사의 기술지원확약서를 제출할 수 있는 업체'
    rec = notice(quote)
    for invalid in ('', quote + '로 제한한다.'):
        row, trace = apply_rules(rec, {'v19': 1, 'e19': invalid}, items=(19,))
        assert row['v19'] == 1
        assert not any(x['source'] == 'pledge_witness_validation' for x in trace)


def test_reference_receipt_contains_only_required_early_actions():
    text = ('1. 계약 체결 시 제출서류\n가. 제조사의 기술지원확약서\n'
            '해당 확약서는 입찰 전에 보유하여야 하며 제출할 필요 없다.')
    result = pledge_check(notice(text))
    assert result['value'] == 1
    events = [e for e in result['facts']['pledges'][0]['events']
              if e.get('binding') == 'explicit_same_pledge_reference_in_same_list']
    assert [e['action'] for e in events] == ['hold']


def test_normal_consumer_enforces_the_capacity_boundary():
    import json
    from pathlib import Path
    from submission.b4_entry import B4Pipeline
    from submission.pps.prompts import fact_fields
    quote = '제조사의 기술지원확약서를 제출할 수 있는 업체'
    rec = notice(quote)
    packet = {'family': 'A', 'items': [19],
              'spans': [{'doc_index': 0, 'doc_type': '공고문',
                         'start': 0, 'end': len(quote), 'text': quote}],
              'generation': {'response_format': 'fact_compact'}}
    response = {'finish_reason': 'stop', 'text': json.dumps({
        'facts': dict.fromkeys(fact_fields((19,)), '원문 검토'),
        'judgments': {'v': [1], 'e': [1]}}, ensure_ascii=False)}
    row, trace = B4Pipeline(Path(__file__).resolve().parents[1] / 'data_open/data', None).consume(
        rec, packet, response)
    assert row['v19'] == 0 and row['e19'] == ''
