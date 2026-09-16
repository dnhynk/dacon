"""Complete goods inventory, retrieval candidates and scope consumption stay distinct."""
import copy
import gzip
import json
from pathlib import Path

import numpy as np
import pytest

from submission.b4_entry import B4Pipeline, parse_error, restored
from submission.pps.goods_scope import decode_response


DATA = Path(__file__).resolve().parents[1] / 'data_open/data'


class Encoder:
    receipt = {'model': 'synthetic-fixed', 'device': 'cpu'}

    def encode(self, texts):
        values = np.ones((len(texts), 3), dtype=np.float32)
        for index, text in enumerate(texts):
            values[index] = [1., len(text) % 11 + 1, text.count('달력') + 1]
        return values / np.linalg.norm(values, axis=1, keepdims=True)


def dev22():
    with gzip.open(DATA.parent / 'dev.jsonl.gz', 'rt', encoding='utf8') as source:
        for line in source:
            record = json.loads(line)
            if record['id'] == 'PPS-DEV-22':
                return record
    raise AssertionError('Development fixture missing')


@pytest.fixture(scope='module')
def tokenizer():
    from transformers import AutoTokenizer
    path = Path(__file__).resolve().parents[1] / 'models/gemma-tokenizer'
    if not path.is_dir():
        pytest.skip('Fixed local tokenizer needed')
    return AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)


@pytest.fixture(scope='module')
def prepared(tokenizer):
    record = dev22()
    pipe = B4Pipeline(DATA, tokenizer, encoder=Encoder())
    packets = pipe.bundle(record)
    packet = next(value for value in packets if value['batch'] == 'Q10')
    return record, pipe, packet


def outside_response(packet):
    goods = packet['goods_scope']
    obj = {'purchase_kind': 'goods',
        'whole_purchase_units': goods['whole_source_units'][:1],
        'item_relations': [{'item_id': item['item_id'],
            'relation': 'outside_supplied_goods_catalog', 'catalog_row_id': 0,
            'source_units': item['name_source_units']}
            for item in goods['prepared_inventory']],
        'unresolved_scope': False}
    return {'text': json.dumps(obj, ensure_ascii=False, separators=(',', ':')),
            'finish_reason': 'stop'}


def test_goods_packet_keeps_same_raw_source_cap_and_complete_catalog_directory(prepared):
    record, _, packet = prepared
    goods = packet['goods_scope']
    assert packet['generation']['response_format'] == 'goods_scope'
    assert len(packet['token_ids']) + packet['generation']['max_output_tokens'] + 32 <= 16384
    assert packet['source_search']['source_tokens'] <= (
        packet['source_search']['diagnostics']['integrated_goods_catalog_producer'][
            'shared_Q_source_token_cap'])
    assert len(goods['directory']) == 587
    assert goods['all_supplied_goods_rows_present']
    assert len(goods['prepared_inventory']) == 13
    assert [item['name'] for item in goods['prepared_inventory']][-4:] == [
        '약달력(월간)', '허리보호대', '욕창예방 바디로션', '미끄럼방지매트']
    assert goods['catalog_candidates']['identity_certified'] is False
    assert goods['catalog_candidates']['selection_policy'] == 'semantic_frontier'
    assert goods['catalog_candidates']['candidate_frontier_depths'] == {
        'lexical': 3, 'dense': 3}
    assert goods['catalog_candidates']['required_lookups'] == [{
        'code': '5310230601', 'listed': False, 'row_ids': []}]
    assert goods['retrieval_is_not_absence_proof']
    queries = goods['catalog_candidates']['queries']
    dense_queries = goods['catalog_candidates']['dense_queries']
    assert len(queries) == len(dense_queries) == 13
    assert queries[9] == '약달력(월간)'
    assert dense_queries[9].startswith('약달력(월간) 실제 용도:')
    assert '약 복용' in dense_queries[9] and '허리보호대' not in dense_queries[9]
    assert queries[12] == '미끄럼방지매트'
    assert dense_queries[12].startswith('미끄럼방지매트 실제 용도:')
    assert '노인 대상자들이 보행 시' in dense_queries[12]
    prompt_candidates = json.loads(packet['messages'][1]['content'].split(
        '혼합 검색의 상세 비교 후보(전체 디렉터리를 대체하지 않음):\n', 1)[1].split(
        '\n현재 공고 원문:', 1)[0])
    assert all('retrieval_hints' in row for row in prompt_candidates)
    assert any(any(hint.startswith('I10:') for hint in row['retrieval_hints'])
               for row in prompt_candidates)
    prompt = packet['messages'][0]['content']
    assert '배송·설치·교육·검수·보안·하자보수' in prompt
    assert '별도로 계약대상이 되는 독립된 용역' in prompt
    assert '실내용 생활매트와 도로 건설자재' in prompt
    lookup = json.loads(packet['messages'][1]['content'].split(
        '원문 등록코드의 제공 고시 정확조회(전체 묶음 판정이 아님):\n', 1)[1].split(
        '\n제공 고시 전체 물품 명칭 디렉터리', 1)[0])
    assert lookup == [{'code': '5310230601', 'listed': False, 'row_ids': []}]
    for span in packet['spans']:
        assert span['text'] == record['docs'][span['doc_index']]['text'][span['start']:span['end']]


def test_all_outside_goods_relation_is_a_guarded_general_purchase(prepared):
    record, pipe, packet = prepared
    response = outside_response(packet)
    assert parse_error(packet, response) is None
    row, log = pipe.consume(record, packet, response)
    assert row['v10'] == row['v11'] == 0 and row['v18'] == 1
    assert log['product']['status'] == 'general'
    assert log['product']['mechanism'] == 'fallible_complete_goods_catalog_review'
    assert log['source_scope_promoted']


def test_one_actual_listed_item_prevents_outside_purchase_claim(prepared):
    record, pipe, packet = prepared
    response = outside_response(packet)
    obj = json.loads(response['text'])
    calendar = next(row for row in packet['goods_scope']['directory'] if row['name'] == '달력')
    obj['item_relations'][-4].update(
        relation='listed_category', catalog_row_id=calendar['row_id'])
    response['text'] = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
    row, log = pipe.consume(record, packet, response)
    assert row['v18'] == 0 and row['v10'] == row['v11'] == 1
    assert log['product']['status'] == 'competition'


@pytest.mark.parametrize('mutation,gate', (
    ('missing_item', 'item_relations_not_complete_and_unique'),
    ('wrong_source', 'item_relation_without_its_original_source'),
    ('unknown', 'goods_identity_or_scope_unresolved'),
))
def test_incomplete_or_unsupported_relations_abstain(prepared, mutation, gate):
    record, pipe, packet = prepared
    response = outside_response(packet)
    obj = json.loads(response['text'])
    if mutation == 'missing_item':
        obj['item_relations'].pop()
    elif mutation == 'wrong_source':
        obj['item_relations'][0]['source_units'] = obj['item_relations'][1]['source_units']
    else:
        obj['item_relations'][0]['relation'] = 'unknown'
        obj['unresolved_scope'] = True
    response['text'] = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
    result, log = pipe.consume(record, packet, response)
    assert result is None and log['gate'] == gate


def test_response_adapter_only_removes_duplicate_source_addresses(prepared):
    _, _, packet = prepared
    response = outside_response(packet)
    obj = json.loads(response['text'])
    obj['item_relations'][0]['source_units'] *= 2
    canonical, normalization = decode_response(
        json.dumps(obj, ensure_ascii=False), restored(packet)['spans'])
    assert canonical['item_relations'][0]['source_units'] == (
        packet['goods_scope']['prepared_inventory'][0]['name_source_units'])
    assert normalization['changes'] and not normalization['semantic_fields_changed']
