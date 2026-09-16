from types import SimpleNamespace

import pytest

from submission.pps.contracting_principal import review
from submission.pps.qualification import infer
from tests.test_comparison import record


PRIVATE_CHAIN = (
    '본 입찰공고는 민간행사사업보조사업으로 민간행사사업자인 축제위원회에서 시행하는 사업입니다.\n'
    '위원회로부터 입찰의뢰 받아 시에서는 계약대상자 결정을 위해 입찰을 대행하는 사업입니다.\n'
    '계약상대자로 결정된 자는 민간행사사업자인 축제위원회와 직접 계약을 체결하여야 합니다.\n'
)


def notice(text=PRIVATE_CHAIN):
    rec = record(text + '1. 입찰참가자격\n가. 다음 조건을 모두 충족한 자\n2. 계약조건',
                 업무구분='일반용역', 적용계약법='지방계약법')
    rec['docs'][0]['doc_id'] = 'D0'
    return rec


def test_complete_private_contract_chain_excludes_public_purchase_checks():
    rec = notice()
    finding = review(rec)
    assert finding['status'] == 'private_contracting_principal'
    assert finding['public_body_is_only_bid_agent']
    assert finding['outside_public_purchase_checks']
    assert len(finding['evidence']) == 3
    baseline = {f'v{i}': '1' for i in range(10, 19)} | {f'e{i}': 'model' for i in range(10, 19)}
    row, details = infer(rec, baseline, SimpleNamespace(products={}))
    assert [row[f'v{i}'] for i in range(10, 19)] == ['0'] * 9
    assert all(details['decisions'][f'v{i}']['reason'] ==
               'verified_external_principal_outside_public_purchase_checks'
               for i in range(10, 19))
    assert details['contracting_principal'] == finding


@pytest.mark.parametrize('text', [
    # A private event alone says nothing about the buyer in this contract.
    '민간행사사업보조사업으로 추진한다. 발주기관과 계약을 체결한다.',
    # Bid assistance without an explicit direct private contract is incomplete.
    '민간행사사업자가 시행하며 시에서는 입찰만 대행한다.',
    # A public buyer may directly contract for work involving a private event.
    '민간행사사업보조사업이다. 시에서 입찰을 대행하고 낙찰자는 시와 직접 계약을 체결한다.',
])
def test_partial_or_public_contract_wording_is_not_reclassified(text):
    finding = review(notice(text))
    assert finding['status'] == 'unresolved_or_public_contract'
    assert not finding['private_contracting_principal_verified']
    assert not finding['outside_public_purchase_checks']


SUBSIDY_CHAIN = (
    '본 공고는 지방계약법 제8조에 따라 시에서 계약(입찰)대행하는 건입니다.\n'
    '낙찰자는 보조사업자 관내 복지시설과 직접 계약 체결하시기 바랍니다.\n'
    '용역 계약, 관리, 감독, 대금 지급 등의 권한과 의무는 위 보조사업자에게 있습니다.\n'
)


def test_complete_subsidy_recipient_contract_chain_excludes_public_purchase_checks():
    rec = notice(SUBSIDY_CHAIN)
    finding = review(rec)
    assert finding['status'] == 'subsidy_recipient_contracting_principal'
    assert finding['external_contracting_principal_verified']
    assert finding['outside_public_purchase_checks']
    assert not finding['private_contracting_principal_verified']
    baseline = {f'v{i}': '1' for i in range(10, 19)} | {
        f'e{i}': 'model' for i in range(10, 19)}
    row, details = infer(rec, baseline, SimpleNamespace(products={}))
    assert [row[f'v{i}'] for i in range(10, 19)] == ['0'] * 9
    assert details['contracting_principal'] == finding


@pytest.mark.parametrize('text', [
    '본 사업은 보조사업자가 시행하고 시가 계약(입찰)대행합니다.',
    '계약(입찰)대행 건이며 낙찰자는 보조사업자와 직접 계약 체결합니다.',
    '낙찰자는 보조사업자와 직접 계약 체결하지만 시가 대금을 지급합니다.',
])
def test_partial_subsidy_recipient_chain_is_not_reclassified(text):
    finding = review(notice(text))
    assert finding['status'] == 'unresolved_or_public_contract'
    assert not finding['outside_public_purchase_checks']
