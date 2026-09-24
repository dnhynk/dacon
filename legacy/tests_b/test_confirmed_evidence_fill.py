"""Empty quotes may use confirmed observations without changing judgments."""
import json

import pytest

from submission.pps import comparison, performance, pipeline
from submission.pps.data import make_row


def record(text):
    return {'id': 'case', 'meta': {}, 'docs': [{'type': '공고문', 'text': text}]}


def packet(text, field='estimated_price', status='different'):
    return {'comparisons': [{'field': field, 'status': status,
                            'comparable_fact_indices': [0]}],
            'facts': [{'doc_index': 0, 'start': 0, 'end': len(text)}]}


@pytest.mark.parametrize('field,claim', [
    ('budget', '예산=상이'), ('estimated_price', '추정가격=상이'),
    ('competition_method', '계약방법=상이'), ('region', '지역=상이'),
    ('industry', '업종=상이')])
def test_same_field_confirmed_difference(field, claim):
    text = '공고문 원문 관측'
    result = comparison.missing_evidence(record(text), packet(text, field), claim)
    assert result['evidence'] == text


@pytest.mark.parametrize('claim,status', [
    ('예산=상이', 'different'), ('추정가격=동일', 'different'),
    ('추정가격=미확정', 'different'), ('추정가격=상이', 'basis_unresolved'),
    (None, 'different')])
def test_no_guess_for_unrelated_denied_or_unresolved_claim(claim, status):
    assert comparison.missing_evidence(record('원문'), packet('원문', status=status), claim) is None


@pytest.mark.parametrize('text', ['='+'원문', '+원문', '@원문', '가'*501])
def test_invalid_quote_is_not_truncated_or_sanitized(text):
    assert comparison.missing_evidence(record(text), packet(text), '추정가격=상이') is None


def facts(text, region=None, count=1, status='mandatory'):
    return {'candidates': [{'status': status, 'evidence': {
        'doc_index': 0, 'start': 0, 'end': len(text)}} for _ in range(count)],
        'explicit_no_experience_restriction': [],
        'operative_regions': [] if region is None else [{'evidence': region}]}


def test_single_mandatory_clause_and_contiguous_region():
    clause = '최근 3년간 용역을 수행한 실적이 있는 업체'
    text = clause + '\n\n경기도에 본사를 둔 업체'
    f = facts(clause, {'doc_index': 0, 'start': len(clause)+2, 'end': len(text)})
    assert performance.missing_evidence(record(text), 2, f)['evidence'] == clause
    result = performance.missing_evidence(record(text), 8, f)
    assert result['evidence'] == text and result['region_included']


@pytest.mark.parametrize('other_doc', [False, True])
def test_far_or_other_document_region_is_not_concatenated(other_doc):
    clause = '필수 실적 조항'
    text = clause + '가'*501 + '지역 제한'
    f = facts(clause, {'doc_index': int(other_doc), 'start': 510, 'end': len(text)})
    result = performance.missing_evidence(record(text), 8, f)
    assert result['evidence'] == clause and not result['region_included']


@pytest.mark.parametrize('count,status', [(0, 'mandatory'), (2, 'mandatory'),
                                            (1, 'scoring'), (1, 'forms_or_submission')])
def test_no_ambiguous_or_scoring_experience(count, status):
    assert performance.missing_evidence(record('실적'), 2, facts('실적', count=count, status=status)) is None


def test_pipeline_only_changes_empty_positive_evidence(monkeypatch):
    rec = record('원문')
    monkeypatch.setattr(comparison, 'compare', lambda rec: packet('원문'))
    response = {'text': json.dumps({'facts': {'본문과메타의동일필드차이': '추정가격=상이'}})}
    for value, quote in [(0, ''), (1, '기존 근거'), (1, '')]:
        row = make_row(rec, [0]*23+[value], ['']*23+[quote])
        # make_row enforces source quotes; set this pre-existing witness directly.
        row['e24'] = quote
        before = dict(row)
        result = pipeline._fill_missing_evidence(rec, row, response, (24,))
        assert all(row[f'v{k}'] == before[f'v{k}'] for k in range(1, 25))
        assert bool(result) == (value == 1 and not quote)
        assert row['e24'] == ('원문' if result else quote)


def test_real_extracted_estimate_can_supply_quote_without_changing_decision():
    rec = record('추정가격: 90,000,000원')
    rec['meta'] = {'입찰추정가격': 100_000_000}
    p = comparison.compare(rec)
    before = comparison.positive_decision(rec, p)
    assert before and before['value'] == 1
    result = comparison.missing_evidence(rec, p, '추정가격=상이(본문과 메타 차이)')
    assert result and result['evidence'] == rec['docs'][0]['text']
    assert comparison.positive_decision(rec, p) == before


def test_real_title_tag_and_source_coordinates():
    rec = record('공고명: 시설 관리 용역(제한경쟁·1억원미만)')
    rec['meta'] = {'계약방법': '제한경쟁', '입찰추정가격': 120_000_000,
                   '배정예산금액': 132_000_000}
    result = comparison.missing_evidence(rec, comparison.compare(rec), '제목 금액 구간과 등록 금액 상이')
    assert result['field'] == 'title_tag'
    assert result['evidence'] == rec['docs'][0]['text']


def test_real_single_experience_requirement():
    clause = '가. 최근 3년 이내 유사 용역을 1건 이상 수행한 실적이 있는 업체'
    rec = record('2. 입찰 참가자격\n'+clause)
    result = performance.missing_evidence(rec, 2, performance.performance_facts(rec))
    assert result and result['evidence'] == clause
