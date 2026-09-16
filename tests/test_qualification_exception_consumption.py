"""An observed exception is a review requirement, never an automatic waiver."""
import copy

import pytest

from submission.pps.qualification import infer
from tests.test_catalog_scope_contract import setup


@pytest.mark.parametrize('baseline_value', ('0', '1'))
def test_upper_band_nonprofit_alternative_does_not_admit_large_commercial_firms(baseline_value):
    exception = '단, 판로지원법 시행령 제2조의3에 해당하는 비영리법인은 입찰참여가 가능합니다.'
    rec, _, _, knowledge = setup(price=320_000_000)
    rec['docs'][0]['text'] = rec['docs'][0]['text'].replace('\n2. 계약조건', '\n'+exception+'\n2. 계약조건')
    baseline = {'v14': baseline_value, 'e14': 'preserved model evidence'}
    _, source = knowledge.qualification_decisions(rec, baseline)
    product = copy.deepcopy(source['product'])
    product['status'] = 'general'
    result, facts = infer(rec, baseline, knowledge._product_facts, product_override=product)
    assert facts['qualification']['exceptions']
    assert facts['qualification']['commercial_size_exception_review'] == []
    assert facts['decisions']['v14']['value'] == 1
    assert result['v14'] == '1'


@pytest.mark.parametrize('baseline_value', ('0', '1'))
def test_upper_band_unresolved_priority_exception_remains_deferred(baseline_value):
    exception = '본 입찰은 판로지원법 시행령 제2조의3의 적용 여부를 검토하여야 합니다.'
    rec, _, _, knowledge = setup(price=320_000_000)
    rec['docs'][0]['text'] = rec['docs'][0]['text'].replace('\n2. 계약조건', '\n'+exception+'\n2. 계약조건')
    baseline = {'v14': baseline_value, 'e14': 'preserved model evidence'}
    _, source = knowledge.qualification_decisions(rec, baseline)
    product = copy.deepcopy(source['product'])
    product['status'] = 'general'
    result, facts = infer(rec, baseline, knowledge._product_facts, product_override=product)
    assert 'v14' not in facts['decisions']
    assert result['v14'] == baseline_value and result['e14'] == baseline['e14']
    assert facts['deferred_decisions']['v14']['waiver_certified'] is False
    assert facts['deferred_decisions']['v14']['evidence'][0]['text'] == exception


@pytest.mark.parametrize('tail', ('', '\n비영리법인은 참가불가합니다.'))
def test_no_exception_or_explicitly_denied_alternative_retains_upper_band_rule(tail):
    rec, _, _, knowledge = setup(price=320_000_000)
    rec['docs'][0]['text'] = rec['docs'][0]['text'].replace('\n2. 계약조건', tail+'\n2. 계약조건')
    _, source = knowledge.qualification_decisions(rec, {})
    product = copy.deepcopy(source['product'])
    product['status'] = 'general'
    _, facts = infer(rec, {}, knowledge._product_facts, product_override=product)
    assert facts['decisions']['v14']['value'] == 1


def test_nonprofit_alternative_cannot_erase_observed_medium_enterprise_permission():
    rec, _, _, knowledge = setup(price=50_000_000)
    rec['docs'][0]['text'] = rec['docs'][0]['text'].replace(
        '\n2. 계약조건',
        '\n판로지원법 시행령 제2조의3에 해당하는 비영리법인은 입찰참가 가능합니다.'
        '\n2. 계약조건')
    _, source = knowledge.qualification_decisions(rec, {'v17': '0', 'e17': ''})
    product = copy.deepcopy(source['product'])
    product['status'] = 'general'
    result, facts = infer(rec, {'v17': '0', 'e17': ''},
                          knowledge._product_facts, product_override=product)
    assert facts['qualification']['exceptions'][0]['kind'] == 'nonprofit_alternative'
    assert facts['qualification']['v17_exception_review'] == []
    assert facts['decisions']['v17']['value'] == 1
    assert result['v17'] == '1' and result['e17'] in rec['docs'][0]['text']


def test_unresolved_priority_exception_still_blocks_medium_permission_conclusion():
    rec, _, _, knowledge = setup(price=50_000_000)
    rec['docs'][0]['text'] = rec['docs'][0]['text'].replace(
        '\n2. 계약조건',
        '\n판로지원법 시행령 제2조의3 적용 여부를 검토하여야 합니다.'
        '\n2. 계약조건')
    _, source = knowledge.qualification_decisions(rec, {'v17': '0', 'e17': ''})
    product = copy.deepcopy(source['product'])
    product['status'] = 'general'
    result, facts = infer(rec, {'v17': '0', 'e17': ''},
                          knowledge._product_facts, product_override=product)
    assert facts['qualification']['v17_exception_review']
    assert 'v17' not in facts['decisions'] and result['v17'] == '0'
