"""Citations can be repaired without changing a verdict or using label data."""
import json

import pytest

from submission.pps.data import make_row
from submission.pps.pipeline import _repair_evidence_locator
from submission.pps.retrieval import Span


CLAUSE = '참가자는 제조업체가 발급한 기술지원 확약서를 입찰서 제출마감일까지 제출하여야 합니다.'
FIELD = '확약서발급주체_보유시점_제출시점'


def setup_case(text=None, summary=None, previous='공고 안내', positive=1):
    text = text or '공고 안내\n\n' + CLAUSE
    rec = {'id': 'case', 'docs': [{'doc_id': 'd', 'type': '공고문', 'text': text}]}
    spans = [Span(0, '공고문', 0, len(text), text)]
    values, evidence = [0] * 18 + [positive] + [0] * 5, [''] * 18 + [previous] + [''] * 5
    row = make_row(rec, values, evidence)
    response = {'text': json.dumps({'facts': {FIELD: summary or f'S1에서 {CLAUSE}'}})}
    return rec, row, response, spans, values, evidence


@pytest.mark.parametrize('previous', ['', '공고 안내'])
def test_explicit_locator_and_unique_literal_clause_repair_missing_or_wrong_quote(previous):
    rec, row, response, spans, values, evidence = setup_case(previous=previous)
    before = {k: v for k, v in row.items() if k.startswith('v')}
    log = _repair_evidence_locator(rec, row, response, spans, (19,), values, evidence)
    assert row['e19'] == CLAUSE and log[0]['judgment_preserved']
    assert before == {k: v for k, v in row.items() if k.startswith('v')}
    assert row['e19'] in rec['docs'][0]['text']


def test_long_selected_span_retains_the_actual_clause_beyond_500_characters():
    rec, row, response, spans, values, evidence = setup_case(text='공고 안내' * 110 + '\n\n' + CLAUSE)
    _repair_evidence_locator(rec, row, response, spans, (19,), values, evidence)
    assert row['e19'] == CLAUSE


@pytest.mark.parametrize('summary', [CLAUSE, 'S999에서 ' + CLAUSE, 'S1 참가자격이 필요함'])
def test_no_repair_without_bounded_locator_and_long_literal_match(summary):
    rec, row, response, spans, values, evidence = setup_case(summary=summary)
    before = dict(row)
    assert not _repair_evidence_locator(rec, row, response, spans, (19,), values, evidence)
    assert row == before


def test_no_repair_for_ambiguous_distinct_occurrences():
    rec, row, response, spans, values, evidence = setup_case(text='공고 안내\n\n'+CLAUSE+'\n\n'+CLAUSE,
                                                          summary='S1, S2 ' + CLAUSE)
    text = rec['docs'][0]['text']
    second = text.rfind(CLAUSE)
    spans[:] = [Span(0, '공고문', 0, second, text[:second]),
                Span(0, '공고문', second, len(text), text[second:])]
    assert not _repair_evidence_locator(rec, row, response, spans, (19,), values, evidence)


def test_one_locator_with_repeated_clause_is_ambiguous():
    rec, row, response, spans, values, evidence = setup_case(text='공고 안내\n\n'+CLAUSE+'\n\n'+CLAUSE)
    assert not _repair_evidence_locator(rec, row, response, spans, (19,), values, evidence)


def test_already_relevant_wrapped_source_quote_is_preserved_verbatim():
    wrapped = CLAUSE.replace('확약서를 ', '확약서를\n\n')
    rec, row, response, spans, values, evidence = setup_case(text=wrapped, previous=wrapped)
    rec['docs'].append({'doc_id': 'copy', 'type': '공고문', 'text': CLAUSE})
    spans.append(Span(1, '공고문', 0, len(CLAUSE), CLAUSE))
    response = {'text': json.dumps({'facts': {FIELD: 'S2 '+CLAUSE}})}
    assert not _repair_evidence_locator(rec, row, response, spans, (19,), values, evidence)
    assert row['e19'] == wrapped


def test_missing_performance_proof_cannot_be_filled_with_scoring_clause():
    from tests.test_performance import notice
    clause = '최근 3년 이내 학교 교육 프로그램 운영 실적 2억원 이상인 업체 배점 10점'
    rec = notice(clause, heading='3. 정량평가 기준')
    rec['id'] = 'scoring-case'
    text = rec['docs'][0]['text']
    spans = [Span(0, '공고문', 0, len(text), text)]
    values, evidence = [0]*24, ['']*24
    values[7] = 1
    row = make_row(rec, values, evidence)
    response = {'text': json.dumps({'facts': {'필수실적_배점구별_금액비교': 'S1 ' + clause}})}
    assert not _repair_evidence_locator(rec, row, response, spans, (8,), values, evidence)
    assert row['v8'] == 1 and row['e8'] == ''


def test_mixed_institution_and_model_fact_cannot_replace_a_valid_staffing_quote():
    staff = '공고일 현재 정규직 기술인력 20명 이상을 상시 고용하는 업체로 제한합니다.'
    licence = '교육 관련 법령에 따른 평생교육시설 등록을 완료하고 신고증을 보유한 업체'
    rec, row, response, spans, values, evidence = setup_case(text=staff+'\n\n'+licence)
    values, evidence = [1]+[0]*23, [staff]+['']*23
    row = make_row(rec, values, evidence)
    response = {'text': json.dumps({'facts': {'기관시설인력제한_특정모델': 'S1 '+licence}})}
    assert not _repair_evidence_locator(rec, row, response, spans, (1,), values, evidence)
    assert row['e1'] == staff and row['v1'] == 1


@pytest.mark.parametrize('mutation', ['rule_witness', 'negative', 'invalid_source', 'already_grounded', 'oversize_clause'])
def test_preserves_rule_decisions_and_fails_closed(mutation):
    rec, row, response, spans, values, evidence = setup_case()
    if mutation == 'rule_witness': row['e19'] = CLAUSE
    elif mutation == 'negative': row['v19'], row['e19'] = 0, ''
    elif mutation == 'invalid_source': spans[0] = Span(0, '규격서', 0, len(spans[0].text), spans[0].text)
    elif mutation == 'already_grounded': row['e19'] = evidence[18] = CLAUSE
    elif mutation == 'oversize_clause':
        text = CLAUSE + ' 추가 계약 내용' * 80
        rec['docs'][0]['text'] = text
        spans[0] = Span(0, '공고문', 0, len(text), text)
    before = dict(row)
    assert not _repair_evidence_locator(rec, row, response, spans, (19,), values, evidence)
    assert row == before
