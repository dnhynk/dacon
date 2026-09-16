"""Source purpose and modality must survive the model/CPU boundary."""
import pytest

from submission.pps.performance import performance_facts
from submission.pps.rules import apply_rules


def record(text):
    return {'id': 'synthetic-purpose', 'meta': {'업무구분': '일반용역',
        '적용계약법': '지방계약법', '입찰추정가격': 80_000_000,
        '배정예산금액': 88_000_000},
        'docs': [{'doc_id': 'original', 'type': '공고문', 'text': text}]}


@pytest.mark.parametrize('statement', [
    '단일 용역 수행 실적 2억원 이상이 있는 업체이어야 한다는 요건은 삭제한다.',
    '단일 용역 수행 실적 2억원 이상이 있는 업체이어야 한다는 주장은 사실이 아니다.',
    '단일 용역 수행 실적 2억원 이상이 있는 업체일 필요는 없다.',
    '예시: 단일 용역 수행 실적 2억원 이상이 있는 업체이어야 한다.',
])
def test_withdrawn_or_nonasserted_experience_never_overrides_model(statement):
    rec = record('2. 입찰 참가자격\n가. '+statement)
    f = performance_facts(rec)
    assert not any(c['status'] == 'mandatory' for c in f['candidates'])
    out, _ = apply_rules(rec, {'v2': 0, 'e2': '', 'v3': 0, 'e3': ''}, items=(2, 3))
    assert out['v2'] == out['v3'] == 0


def test_certificate_waiver_does_not_waive_actual_experience():
    rec = record('2. 입찰 참가자격\n가. 실적 증명서를 제출할 필요가 없으나, '
                 '단일 용역 수행 실적 2억원 이상이 있는 업체이어야 한다.')
    out, _ = apply_rules(rec, {'v2': 0, 'e2': '', 'v3': 0, 'e3': ''}, items=(2, 3))
    assert out['v2'] == out['v3'] == 1


@pytest.mark.parametrize('heading,quote', [
    ('3. 정량적 평가기준', '최근 3년 단일 건 2억원 이상의 완료 실적만 인정'),
    ('【별지서식 7】', '최근 3년 단일 건 2억원 이상의 실적만 기재'),
    ('4. 제출서류', '납품실적증명서 원본 1부'),
])
def test_scoring_or_form_witness_is_not_an_eligibility_proof(heading, quote):
    rec = record('2. 입찰 참가자격\n가. 적법하게 등록한 업체\n'+heading+'\n'+quote)
    out, trace = apply_rules(rec, {'v2': 1, 'e2': quote}, items=(2,))
    assert out['v2'] == 0
    guard = next(x for x in trace if x['source'] == 'performance_witness_validation')
    assert guard['semantic_value'] is None
    assert not guard['absence_verified']
    assert guard['rejected_witness'] == quote


def test_other_operative_condition_still_proves_violation():
    q = '최근 3년 단일 건 2억원 이상의 완료 실적만 인정'
    rec = record('2. 입찰 참가자격\n가. 단일 용역 실적 3억원 이상이 있는 업체\n'
                 '3. 정량평가\n'+q)
    out, _ = apply_rules(rec, {'v2': 1, 'e2': q}, items=(2,))
    assert out['v2'] == 1 and '3억원' in out['e2']


@pytest.mark.parametrize('heading,quote', [
    ('4. 제출서류', '실적증명서 1부: 단일 용역 실적 2억원 이상이 있는 업체에 한함'),
    ('2. 사업개요', '최근 3년 단일 건 2억원 이상의 완료 실적만 인정'),
])
def test_a_form_heading_or_unresolved_context_cannot_disprove_a_requirement(heading, quote):
    rec = record(heading+'\n'+quote)
    out, _ = apply_rules(rec, {'v2': 1, 'e2': quote}, items=(2,))
    assert out['v2'] == 1


def test_same_quote_in_a_different_operative_section_is_not_a_scoring_only_witness():
    q = '최근 3년 단일 건 2억원 이상의 완료 실적만 인정'
    rec = record('2. 입찰 참가자격\n'+q+'\n3. 정량평가\n'+q)
    out, _ = apply_rules(rec, {'v2': 1, 'e2': q}, items=(2,))
    assert out['v2'] == 1


@pytest.mark.parametrize('separator', ['\n\n', '\n', ' | '])
def test_flattened_certificate_name_field_cannot_make_experience_mandatory(separator):
    quote = separator.join([
        '최근 3년간 2억원 이상의 실적만 기재',
        '수행용역 실적증명서',
        '신 청 인',
        '업 체 명(상호)',
        '대표자',
        '증명서용도',
        '입찰참가용',
    ])
    rec = record('2. 입찰 참가자격\n등록한 업체\n【별지서식 7】\n' + quote)
    out, trace = apply_rules(rec, {'v2': 1, 'e2': quote, 'v3': 1, 'e3': quote}, items=(2, 3))
    assert out['v2'] == out['v3'] == 0
    assert all(not t['absence_verified'] for t in trace if t['source'] == 'performance_witness_validation')


def test_real_requirement_survives_a_certificate_name_field_in_same_quote():
    quote = ('실적증명서\n업체명(상호)\n'
             '입찰 참가자는 단일 용역 2억원 이상 수행 실적을 보유한 업체이어야 한다.')
    rec = record('【별지서식 7】\n' + quote)
    out, _ = apply_rules(rec, {'v2': 1, 'e2': quote}, items=(2,))
    assert out['v2'] == 1
