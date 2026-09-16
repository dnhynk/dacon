"""A code comparison keeps the condition of its own bidder-registration clause."""
import pytest

from submission.pps.comparison import compare, positive_decision
from tests.test_comparison import record


def industry(rec):
    packet = compare(rec)
    return next(c for c in packet['comparisons'] if c['field'] == 'industry'), packet


@pytest.mark.parametrize('text', [
    '가. 부정당업자에 해당하지 않는 업체이어야 한다.\n'
    '나. 전세버스운송사업(업종코드: 5805)으로 등록한 업체이어야 한다.\n'
    '다. 소기업 또는 소상공인 확인서를 보유한 업체이어야 한다.',
    '전세버스운송사업(업종코드: 5805)으로 등록한 업체이며 소기업 또는 소상공인 확인서를 보유해야 한다.',
    '부정당업자에 해당하지 않으며 전세버스운송사업(업종코드: 5805)으로 등록한 업체이어야 한다.',
])
def test_unrelated_negation_and_size_or_do_not_corrupt_industry_comparison(text):
    rec = record(text, 면허업종제한목록='여객운송사업(5804)')
    outcome, packet = industry(rec)
    assert outcome['status'] == 'different'
    assert positive_decision(rec, packet)['value'] == 1
    for i in outcome['fact_indices']:
        f = packet['facts'][i]
        assert rec['docs'][f['doc_index']]['text'][f['start']:f['end']]


@pytest.mark.parametrize('text', [
    '전세버스운송사업(업종코드: 5805) 또는 다른 운송사업으로 등록한 업체이어야 한다.',
    '전세버스운송사업(업종코드: 5805) 등록은 요구하지 않는 업체도 참가할 수 있다.',
    '전세버스운송사업(업종코드: 5805)으로 등록한 업체이어야 하며 그 조건은 철회한다.',
    '업체는 전세버스운송사업(업종코드: 5805)으로 등록하지 않으며 소기업 확인서만 보유하면 된다.',
    '업체는 전세버스운송사업(업종코드: 5805)으로 등록하지 아니하고 소기업 확인서만 보유하면 된다.',
])
def test_own_alternative_negation_or_withdrawal_never_proves_code_mismatch(text):
    rec = record(text, 면허업종제한목록='5804')
    outcome, packet = industry(rec)
    assert outcome['status'] != 'different'
    assert positive_decision(rec, packet) is None


def test_registration_flag_without_a_code_is_not_a_value_mismatch():
    rec = record('전세버스운송사업(업종코드: 5805)으로 등록한 업체이어야 한다.',
                 면허업종제한목록=None, 업종제한여부='N')
    outcome, packet = industry(rec)
    assert outcome['status'] == 'metadata_missing_or_unparsed'
    assert positive_decision(rec, packet) is None
