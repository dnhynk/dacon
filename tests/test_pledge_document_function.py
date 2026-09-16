"""Issuer/dealership proof is distinct from a purchase-specific undertaking."""
import copy
import pytest

from submission.pps.pledge_document_function import review
from submission.pps.rules import apply_rules
from tests.test_pledge_modality import notice


CERTIFICATES = ('원제조자인 SAMPLE社에서 발행하는 제조자증명서 또는 원제조자와 공급자 간의 '
                '판매대리점 계약서 또는 판매대리점이 발행하는 공급자증명서를 '
                '입찰마감일 전일까지 제출하여야 한다.')


@pytest.mark.parametrize('separator', [' ', '\n', '\n\n'])
def test_an_existing_issuer_dealership_relation_is_not_a_supply_promise(separator):
    quote = CERTIFICATES.replace(' 또는 ', separator+'또는 ')
    rec = notice(quote)
    original = copy.deepcopy(rec)
    row, trace = apply_rules(rec, {'v19':1, 'e19':quote}, items=(19,))
    assert row['v19'] == 0 and row['e19'] == ''
    rejected = next(x for x in trace if x['reason']=='model_witness_only_establishes_identity_or_dealership')
    assert rejected['semantic_value'] is None and not rejected['absence_verified']
    assert rejected['rejected_witness'] == quote
    assert rec == original


@pytest.mark.parametrize('text', [
    '제조자증명서 또는 판매대리점 계약서를 입찰 전에 제출한다.',
    '제조사에서 발행하는 제조자증명서를 입찰 전에 제출한다.',
    '공급자증명서 제출 가능 업체',
])
def test_titles_or_an_issuer_alone_do_not_certify_document_function(text):
    assert review(notice(text), text) is None


@pytest.mark.parametrize('extra', [
    '제조자증명서에는 제조사가 이 구매물품의 공급을 보장한다는 내용을 포함한다.',
    '제조자증명서는 별첨 서식에 따라 작성한다.',
    '해당 증명서에는 계약기간의 기술지원 보증을 기재한다.',
    '공급자증명서 양식은 별도로 배부한다.',
    '제조자증명서\n\n당사는 이 구매물품의 지속적인 공급을 보장합니다.',
])
def test_related_content_or_an_unresolved_form_cannot_be_ignored(extra):
    rec = notice(CERTIFICATES)
    rec['docs'].append({'doc_id':'R1','type':'규격서','text':extra})
    assert review(rec, CERTIFICATES) is None


def test_manufacturer_mention_cannot_supply_another_issuers_verb():
    quote = ('원제조자와 공급자 간의 판매대리점 계약서 및 제조자증명서를 제출한다. '
             '제조사는 심사에 참여하며 입찰자에서 발행하는 제조자증명서를 사용한다.')
    assert review(notice(quote),quote) is None


def test_an_independent_early_undertaking_survives_rejected_certificate_witness():
    rec = notice(CERTIFICATES + '\n\n제조사의 기술지원확약서는 입찰 전에 보유하여야 한다.')
    row, _ = apply_rules(rec, {'v19':1,'e19':CERTIFICATES}, items=(19,))
    assert row['v19'] == 1 and '보유하여야' in row['e19']


def test_receipt_responsibility_and_bid_deposit_are_different_document_functions():
    rec = notice(CERTIFICATES+'\n접수 여부 미확인으로 발생한 책임은 참가자에게 있다.\n4. 입찰보증금')
    assert review(rec,CERTIFICATES) is not None


def test_the_same_quote_in_a_supply_content_context_is_not_erased():
    rec = notice(CERTIFICATES + '\n\n' + CERTIFICATES + '\n이 증명서는 물품공급을 보증한다.')
    assert review(rec,CERTIFICATES) is None


def test_nonoriginal_quote_and_negative_model_claim_are_not_reinterpreted():
    rec = notice(CERTIFICATES)
    assert review(rec,CERTIFICATES+' 진위를 확인한다.') is None
    row, trace = apply_rules(rec,{'v19':0,'e19':''},items=(19,))
    assert row['v19'] == 0
    assert not any(x.get('reason')=='model_witness_only_establishes_identity_or_dealership' for x in trace)


def test_feature_support_and_partnership_confirmations_are_not_pledges():
    quote = ('[제출서류]\n규격에 대한 기능이 지원될 수 있음을 증명할 수 있는 제조사 확인 문서 1식\n'
             '제조사 파트너십 인증 확인 문서 1식\n'
             '규격에 대한 기능이 지원할 수 있음을 명시한 입찰사 확인 서류 1식')
    rec = notice(quote)
    row, trace = apply_rules(rec, {'v19': 1, 'e19': quote}, items=(19,))
    assert row['v19'] == 0 and row['e19'] == ''
    rejected = next(x for x in trace
                    if x.get('reason') == 'model_witness_only_establishes_feature_support_capability')
    assert rejected['semantic_value'] is None and not rejected['absence_verified']


def test_a_feature_support_confirmation_with_a_supply_commitment_is_not_rejected():
    quote = ('기능 지원이 가능함을 증명하는 제조사 확인 문서에는 제조사가 '
             '물품공급을 보장한다는 확약을 포함한다.')
    assert review(notice(quote), quote) is None
