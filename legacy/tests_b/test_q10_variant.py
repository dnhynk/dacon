import copy
import dataclasses
import hashlib
import json
from collections import Counter
from types import SimpleNamespace

import jsonschema
import pytest

from submission.pps.catalog_scope import (
    ITEMS, prompt, purchase_roles_schema, validate_purchase_roles, review,
)
from submission.pps.prompts import Config
from submission.pps.retrieval import Span, q10_purchase_candidates
from tests.test_catalog_scope_contract import setup, response


class Tokenizer:
    def encode(self, text, add_special_tokens=False):
        return list(text)

    def apply_chat_template(self, messages, **kwargs):
        return list(''.join(m['content'] for m in messages))


def selection(rec, tokenizer, start=0, end=None):
    text = rec['docs'][0]['text']
    end = len(text) if end is None else end
    span = Span(0, rec['docs'][0]['type'], start, end, text[start:end])
    count = len(tokenizer.encode(span.text))
    return {'record_id': rec['id'], 'spans': [dataclasses.asdict(span)],
            'source_tokens': count, 'source_token_budget': count,
            'documents': [{'doc_index': i, 'doc_id': d['doc_id'],
                           'doc_sha256': hashlib.sha256(d['text'].encode()).hexdigest()}
                          for i, d in enumerate(rec['docs'])],
            'coverage': {'absence_verified': False}}


def test_default_and_explicit_current_are_identical():
    rec, _, _, knowledge = setup()
    tokenizer = Tokenizer()
    src = selection(rec, tokenizer)
    before = copy.deepcopy(src)
    implicit = prompt(rec, src, tokenizer, knowledge.products, explain_contract=True)
    explicit = prompt(rec, src, tokenizer, knowledge.products, explain_contract=True, q10_variant='current')
    assert implicit == explicit and src == before
    assert Config().q10_variant == 'current'
    with pytest.raises(ValueError, match='Q10'):
        Config(q10_variant='typo')


@pytest.mark.parametrize('explain', [False, True])
def test_on_preserves_existing_source_and_adds_missing_purchase_field(explain):
    rec, _, _, knowledge = setup()
    tokenizer = Tokenizer()
    src = selection(rec, tokenizer, start=rec['docs'][0]['text'].index('1.'))
    before = copy.deepcopy(src)
    off = prompt(rec, src, tokenizer, knowledge.products, explain_contract=explain)
    on = prompt(rec, src, tokenizer, knowledge.products, explain_contract=explain,
                q10_variant='purchase_roles')
    assert src == before
    assert len(on['token_ids']) <= len(off['token_ids']) + 300
    assert len(on['token_ids']) + 1536 + 32 <= 16384
    assert any('용역명:' in s.text for s in on['spans'])
    for old in off['spans']:
        assert all(any(s.doc_index == old.doc_index and s.start <= n < s.end
                       for s in on['spans']) for n in range(old.start, old.end))
    assert on['catalog_scope']['q10_variant'] == 'purchase_roles'


@pytest.mark.parametrize('change', ['unlisted', 'duplicate', 'listed_without_whole', 'outside_with_whole'])
def test_variant_rejects_contract_contradictions_without_label_repair(change):
    rec, packet, obj, knowledge = setup()
    catalog = packet['catalog_scope']['catalog']
    code = catalog[0]['code']
    if change == 'unlisted':
        obj['relationships'] = [{'code': '9999999999', 'role': 'certificate_only', 'source_units': [1]}]
    elif change == 'duplicate':
        obj['relationships'] = [{'code': code, 'role': role, 'source_units': [1]}
                                for role in ('whole', 'certificate_only')]
        obj['catalog_relation'] = 'listed_category'
    elif change == 'listed_without_whole':
        obj['catalog_relation'] = 'listed_category'
    else:
        obj['relationships'] = [{'code': code, 'role': 'whole', 'source_units': [1]}]
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        validate_purchase_roles(obj, packet['spans'], catalog)
    packet['catalog_scope']['q10_variant'] = 'purchase_roles'
    result, log = review(rec, response(obj), packet, knowledge)
    assert result is None and log['gate'] == 'q10_variant_contract_violation'


def test_valid_whole_relation_and_empty_outside_remain_compatible():
    _, packet, obj, _ = setup()
    catalog = packet['catalog_scope']['catalog']
    validate_purchase_roles(obj, packet['spans'], catalog)
    obj.update(catalog_relation='listed_category', relationships=[
        {'code': catalog[0]['code'], 'role': 'whole', 'source_units': [1]}])
    validate_purchase_roles(obj, packet['spans'], catalog)
    assert purchase_roles_schema(1, catalog)['required'] == [
        'purchase_kind', 'whole_task_units', 'task_summary', 'catalog_relation', 'relationships', 'unresolved_scope']


def test_purchase_retrieval_keeps_table_value_and_ignores_masked_field():
    rec, _, _, _ = setup()
    rec['docs'][0]['text'] = ('공고명: [공고명]\n사업개요 | 폐기물 운반 및 처리\n'
                             '용역명 | 용역개요 | 용역기간\n현장인력 공급 | 인부 공급 | 1년')
    candidates = q10_purchase_candidates(rec)
    assert any('사업개요 | 폐기물 운반 및 처리' == c['text'] for c in candidates)
    assert any('현장인력 공급' in c['text'] and '용역명' in c['text'] for c in candidates)
    assert not any(c['text'] == '공고명: [공고명]' for c in candidates)


def test_named_notice_heading_is_a_reading_candidate_but_generic_heading_is_not():
    rec, _, _, _ = setup()
    rec['docs'][0]['text'] = '용역 입찰 공고\n하천 정비용역 입찰 공고[긴급]\n공고명: [공고명]'
    candidates = q10_purchase_candidates(rec)
    assert any(c['text'] == '하천 정비용역 입찰 공고[긴급]' for c in candidates)
    assert not any(c['text'] == '용역 입찰 공고' for c in candidates)


def test_oversized_source_is_not_silently_truncated():
    from submission.pps.specialist_packets import packet
    rec, _, _, knowledge = setup()
    tokenizer = Tokenizer()
    src = selection(rec, tokenizer)
    with pytest.raises(ValueError, match='Optional specialist exceeds'):
        body = prompt(rec, src, tokenizer, knowledge.products, q10_variant='purchase_roles',
                      explain_contract=True, max_model_len=1600)
        packet(rec, body, src, family='Q', profile='Q10', fmt='catalog_scope',
               output_tokens=1536, max_model_len=1600)


def test_canonical_catalog_packet_passes_config_and_keeps_wire_schema():
    from submission.pps.specialist_packets import catalog_packet
    from submission.pps.generation_contract import generation_schema
    from submission.b4_entry import digest
    rec, _, _, knowledge = setup()
    tokenizer = Tokenizer()
    src = selection(rec, tokenizer)
    pipe = SimpleNamespace(knowledge=knowledge, tokenizer=tokenizer,
        config=Config(input_strategy='audited', catalog_review='explicit', q10_variant='purchase_roles'),
        catalog_source_preparation=Counter())
    packet = catalog_packet(pipe, rec, {'source_search': src})
    assert packet['catalog_scope']['q10_variant'] == 'purchase_roles'
    assert packet['generation']['response_format'] == 'catalog_scope'
    assert packet['generation_schema_sha256'] == digest(generation_schema('catalog_scope', len(packet['spans']), ITEMS))
    assert len(packet['token_ids']) + packet['generation']['max_output_tokens'] + 32 <= 16384


def test_shorter_contract_does_not_bypass_baseline_source_budget_reduction():
    rec, _, _, knowledge = setup()
    tokenizer = Tokenizer()
    src = selection(rec, tokenizer)
    off = prompt(rec, src, tokenizer, knowledge.products, explain_contract=True)
    cap = len(off['token_ids']) + 1536 + 32 - 1
    on = prompt(rec, src, tokenizer, knowledge.products, explain_contract=True,
                q10_variant='purchase_roles', max_model_len=cap)
    assert on == off  # Oversized transient body is rejected by packet(), as off.
