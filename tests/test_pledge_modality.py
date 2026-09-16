"""Independent source controls for action-specific pledge obligations."""
import pytest

from submission.pps.other_checks import pledge_check


def notice(text):
    return {'id': 'synthetic-pledge-modality',
            'meta': {'적용계약법': '국가계약법', '소관구분': '국가기관', '업무구분': '물품(내자)'},
            'docs': [{'type': '공고문', 'text': text}],
            'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


@pytest.mark.parametrize('predicate', [
    '제출할 필요가 없다.', '제출할 의무가 없다.',
    '제출 대상에서 제외한다.', '제출 의무는 없다.',
    '제출하지 않아도 된다.', '제출할 필요 없다.',
])
def test_explicit_waiver_is_not_a_pre_bid_obligation(predicate):
    text = '입찰자는 제조사의 기술지원확약서를 입찰 시 ' + predicate
    result = pledge_check(notice(text))
    assert result['value'] == 0


@pytest.mark.parametrize('text', [
    '제조사의 기술지원확약서를 입찰 시 제출하여야 한다는 이전 조항은 적용하지 않는다.',
    '제조사의 기술지원확약서를 입찰 시 제출할 필요가 없다는 해석은 타당하지 않다.',
    '제조사의 기술지원확약서는 입찰 시 제출하여야 한다. 위 제출 의무는 철회한다.',
    '예시: 제조사의 기술지원확약서를 입찰 시 제출하여야 한다.',
])
def test_quoted_withdrawn_or_denied_claim_does_not_force_a_verdict(text):
    assert pledge_check(notice(text))['value'] is None


@pytest.mark.parametrize('text', [
    '제조사의 기술지원확약서는 입찰 시 보유하여야 하며 입찰 시 제출할 필요 없다.',
    '제조사의 기술지원확약서는 입찰 전에 발급받아야 하며 입찰 시 제출할 필요가 없다.',
    '제조사의 기술지원확약서는 입찰 시 제출하여야 한다. 보증서 제출 의무는 없다.',
    '입찰자는 제조사의 기술지원확약서를 입찰 시 제출하여야 한다.',
])
def test_affirmative_early_action_survives_other_action_or_object_waiver(text):
    assert pledge_check(notice(text))['value'] == 1


def test_another_document_cannot_supply_an_early_possession_action():
    text = '제조사의 기술지원확약서는 입찰 시 제출할 필요 없다. 보증서는 입찰 시 보유하여야 한다.'
    assert pledge_check(notice(text))['value'] == 0


def test_only_later_pledge_remains_negative():
    text = '낙찰자는 제조사의 기술지원확약서를 계약 체결 시 제출하여야 한다.'
    assert pledge_check(notice(text))['value'] == 0


@pytest.mark.parametrize('reference,expected', [
    ('해당 확약서는 입찰 전에 제출할 필요가 없다.', 0),
    ('해당 확약서는 입찰 전에 발급받을 의무가 없다.', 0),
    ('해당 확약서는 입찰 전에 보유하여야 하며 제출할 필요 없다.', 1),
])
def test_cross_line_reference_uses_the_same_action_modality_contract(reference, expected):
    text = '1. 계약 체결 시 제출서류\n가. 제조사의 기술지원확약서\n' + reference
    assert pledge_check(notice(text))['value'] == expected


def test_separate_document_cannot_supply_the_pledge_issuer():
    text = '제조사의 보증서는 계약 시 제출한다. 기술지원확약서는 입찰 시 제출하여야 한다.'
    assert pledge_check(notice(text))['value'] is None


@pytest.mark.parametrize('phrase', ['제출 대상에서 제외하지 않는다.', '제출 의무가 없지 않다.'])
def test_negation_of_waiver_is_not_silently_a_waiver(phrase):
    assert pledge_check(notice('제조사의 기술지원확약서는 입찰 시 ' + phrase))['value'] is None


def test_withdrawn_cross_line_reference_does_not_certify_all_later():
    text = ('1. 계약 체결 시 제출서류\n가. 제조사의 기술지원확약서\n'
            '해당 확약서는 입찰 전에 제출하여야 한다는 이전 조항은 적용하지 않는다.')
    assert pledge_check(notice(text))['value'] is None


@pytest.mark.parametrize('raw', [0, 1])
@pytest.mark.parametrize('text,expected', [
    ('제조사의 기술지원확약서는 입찰 시 제출할 필요가 없다.', 0),
    ('제조사의 기술지원확약서는 입찰 시 보유하여야 하며 제출할 필요 없다.', 1),
])
def test_actual_consumer_uses_the_source_bound_action_decision(raw, text, expected):
    import json
    from pathlib import Path
    from submission.b4_entry import B4Pipeline
    from submission.pps.prompts import fact_fields
    rec=notice(text)
    packet={'family':'A','items':[19],
            'spans':[{'doc_index':0,'doc_type':'공고문','start':0,'end':len(text),'text':text}],
            'generation':{'response_format':'fact_compact'}}
    response={'finish_reason':'stop','text':json.dumps({
        'facts':dict.fromkeys(fact_fields((19,)),'원문 검토'),
        'judgments':{'v':[raw],'e':[1 if raw else 0]}},ensure_ascii=False)}
    row,_=B4Pipeline(Path(__file__).resolve().parents[1]/'data_open/data',None).consume(rec,packet,response)
    assert row['v19']==expected
    if expected:
        assert row['e19']==text
