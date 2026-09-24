"""Worker processes prepare canonical packets and consume responses; no GPU or labels."""
import json
import time
from pathlib import Path

import pytest

from submission.prep import PreparationPool, rules_only_row
from submission.pps.prompts import fact_fields

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data_open/data'
TOKENIZER = ROOT / 'models/gemma-tokenizer'
OPTIONS = {'specification_review': 'current', 'catalog_review': 'current'}


def notice(rid):
    body = ('1. 사업개요\n용역명: 업무 개선 지원 용역\n추정가격: 8,000만원\n'
            '2. 입찰참가자격\n가. 해당 업종을 등록한 업체\n'
            '3. 과업내용\n운전원을 제공하여야 한다.\n')
    return {'id': rid, 'meta': {'업무구분': '일반용역', '적용계약법': '국가계약법', '계약방법': '제한경쟁',
                                '입찰추정가격': 80_000_000},
            'docs': [{'type': '공고문', 'doc_id': rid + '-notice', 'text': body}],
            'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


def response(items, value=0):
    return {'finish_reason': 'stop', 'output_tokens': 100,
            'text': json.dumps({'facts': {field: '불명확' for field in fact_fields(items)},
                                'judgments': {'v': [value] * len(items), 'e': [0] * len(items)}})}


def wait(pool, count, timeout=300):
    collected = []
    deadline = time.monotonic() + timeout
    while len(collected) < count and time.monotonic() < deadline:
        collected.extend(pool.poll(timeout=.5))
    assert len(collected) == count, collected
    return collected


@pytest.mark.skipif(not (DATA.is_dir() and TOKENIZER.is_dir()), reason='local development data and tokenizer required')
def test_worker_process_prepares_packets_fallback_rows_and_consumes(tmp_path):
    pool = PreparationPool(DATA, TOKENIZER, OPTIONS, workers=1, encoder='none')
    pool.start()
    try:
        pool.submit({'kind': 'prepare', 'id': 0, 'record': notice('pool-a')})
        pool.submit({'kind': 'prepare', 'id': 1, 'record': notice('pool-b')})
        results = {r['id']: r for r in wait(pool, 2)}
        assert not any(r.get('error') for r in results.values()), results
        packets = results[0]['packets']
        assert [p['batch'] for p in packets] == ['A1', 'A10', 'A19', 'L19']
        assert set(results[0]['fallback_rows']) == {'A1', 'A10', 'A19'}
        row, details = results[0]['fallback_rows']['A10']
        assert set(row) == {f'{f}{k}' for k in range(10, 19) for f in ('v', 'e')}
        assert details[0]['source'] == 'source_only_judgment'
        a1 = packets[0]
        pool.submit({'kind': 'consume', 'id': 'c', 'record': notice('pool-a'), 'packet': a1,
                     'response': response(tuple(a1['items']))})
        pool.submit({'kind': 'consume', 'id': 'bad', 'record': notice('pool-a'), 'packet': a1,
                     'response': {'finish_reason': 'stop', 'output_tokens': 1, 'text': '{'}})
        consumed = {r['id']: r for r in wait(pool, 2)}
        assert consumed['c']['parse_error'] is None and consumed['c']['cpu_error'] is None
        assert set(consumed['c']['row']) >= {f'v{k}' for k in range(1, 10)}
        assert consumed['bad']['parse_error'] and consumed['bad']['row'] is None
        assert pool.receipt()['ready_workers'] == [0] and pool.outstanding == 0
    finally:
        pool.close()


@pytest.mark.skipif(not (DATA.is_dir() and TOKENIZER.is_dir()), reason='local development data and tokenizer required')
def test_inline_pool_matches_worker_rules_rows():
    from transformers import AutoTokenizer
    from submission.b4_entry import B4Pipeline
    tokenizer = AutoTokenizer.from_pretrained(TOKENIZER, local_files_only=True, trust_remote_code=False)
    pipe = B4Pipeline(DATA, tokenizer, **OPTIONS)
    row, details = rules_only_row(pipe, notice('inline'), range(19, 25))
    assert set(row) == {f'{f}{k}' for k in range(19, 25) for f in ('v', 'e')}
    assert all(row[f'v{k}'] in (0, 1) for k in range(19, 25))
    pool = PreparationPool(DATA, TOKENIZER, OPTIONS, workers=0, inline_pipeline=pipe)
    pool.start()
    pool.submit({'kind': 'rules', 'id': 'r', 'record': notice('inline'), 'items': list(range(19, 25))})
    result = pool.poll()[0]
    assert result['row'] == row and pool.inline


def test_pool_rejects_negative_workers_and_unstarted_submission():
    with pytest.raises(ValueError):
        PreparationPool('d', 't', workers=-1)
    pool = PreparationPool('d', 't', workers=0)
    with pytest.raises(RuntimeError):
        pool.submit({'kind': 'rules', 'id': 1})
