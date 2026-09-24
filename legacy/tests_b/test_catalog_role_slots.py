"""Finite source slots must progress, preserve omissions, and keep old evidence readable."""
import copy
import json
from pathlib import Path

import jsonschema
import pytest

from submission.b4_entry import parse_error
from submission.pps import catalog_semantics as sem, catalog_source_roles as roles
from submission.pps.generation_contract import validate_grammar
from tests.test_catalog_condition_review import setup, finding
from tests.test_catalog_source_roles import attach, effective
from tests.test_catalog_semantics import obj, response


def multi():
    rec, packet, knowledge = setup('CPU 아키텍처: ARM\nCPU 개수: 1개\nCPU 기본주파수: 2.0GHz')
    return rec, attach(rec, packet), knowledge


def claims(packet):
    return [finding(packet), finding(packet, 'CPU 개수:', field='cpu_count'),
            finding(packet, 'CPU 기본주파수:', field='cpu_base_ghz')]


def test_source_identity_cannot_repeat_even_with_a_different_reason():
    _, packet, _ = multi()
    a, b, c = claims(packet)
    jsonschema.validate(obj(findings=[a,b,c]), effective(packet))
    other = {**a, 'reason': '같은 원문을 다른 이유로 재해석'}
    for repeated in ([a,a], [a,other], [a,b,c,a]):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(obj(findings=repeated), effective(packet))


def test_later_slots_can_be_selected_without_fabricating_earlier_facts():
    rec, packet, knowledge = multi()
    claim = claims(packet)[2]
    payload = obj(findings=[None,None,claim])
    original = copy.deepcopy(payload)
    jsonschema.validate(payload, effective(packet))
    assert parse_error(packet, response(payload)) is None
    decoded, trace = sem.decode(response(payload)['text'], packet['spans'],
                                source_roles=packet['generation']['catalog_roles'])
    assert decoded['findings'] == [claim]
    assert trace['explicit_null_slots'] == [{'group':'findings','slot':0},{'group':'findings','slot':1}]
    assert payload == original and trace['semantic_fields_changed'] is False
    row, _ = sem.review(rec, response(payload), packet, knowledge)
    assert row is None  # Omitting the decisive architecture is not a positive fact.


@pytest.mark.parametrize('values', [[], [None], [None,None,None]])
def test_empty_or_null_slots_do_not_convert_unknown_to_normal(values):
    rec, packet, knowledge = multi()
    payload = obj(findings=values)
    jsonschema.validate(payload, effective(packet))
    row, _ = sem.review(rec,response(payload),packet,knowledge)
    assert row is None


def test_wrong_slot_stays_a_semantic_abstention_without_format_reroll():
    rec,packet,knowledge=multi()
    payload=obj(findings=[claims(packet)[2]])
    assert parse_error(packet,response(payload)) is None
    row,log=sem.review(rec,response(payload),packet,knowledge)
    assert row is None and log['gate']=='model_role_selection_outside_prepared_candidates'


def test_old_version_one_mask_and_decoder_remain_readable():
    rec,packet,_=setup()
    old=roles.build(rec,packet['spans'],packet['catalog_conditions']['plan'],version=1)
    packet['catalog_conditions'].update(interpretation_mode=sem.FORMAT,source_roles=old)
    packet['generation']={'response_format':sem.FORMAT,'catalog_roles':old['generation']}
    assert roles.validate_prepared(rec,packet)==old['generation']
    payload=obj(findings=[finding(packet)]*2)
    jsonschema.validate(payload,effective(packet))
    assert parse_error(packet,response(payload)) is None
    with pytest.raises(jsonschema.ValidationError):
        sem.decode(response(obj(findings=[None]))['text'],packet['spans'],source_roles=old['generation'])


def test_each_original_permission_remains_independently_representable():
    rec,packet,_=setup(extra='동등 규격은 허용한다.\n다른 CPU도 선택할 수 있다.')
    attach(rec,packet)
    manifest=roles.slot_manifest(effective(packet))
    assert len(manifest['permissions'])==2
    assert {tuple(p['source_units']) for p in manifest['permissions']}=={
        tuple(refs) for refs in packet['generation']['catalog_roles']['permission_options']}
    assert len({json.dumps(p,sort_keys=True) for p in manifest['permissions']})==2


def test_slot_inventory_is_never_silently_truncated():
    with pytest.raises(ValueError,match='exceed wire capacity'):
        roles._slots([{'const':n} for n in range(3)],2)


def test_real_token_mask_blocks_a_second_identical_source_and_allows_progress():
    from transformers import AutoTokenizer
    import llguidance
    import llguidance.hf
    path=Path(__file__).resolve().parents[1]/'models/gemma-tokenizer'
    if not path.is_dir():pytest.skip('Fixed tokenizer is required')
    tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
    ll_tokenizer=llguidance.hf.from_tokenizer(tokenizer)
    _,packet,_=multi()
    grammar=llguidance.LLMatcher.grammar_from_json_schema(effective(packet),defaults={'whitespace_flexible':False})
    validate_grammar(effective(packet))
    a,b,c=claims(packet)
    for payload,allowed in [(obj(findings=[a,a]),False),(obj(findings=[a,b,c]),True),
                            (obj(findings=[None,None,c]),True),(obj(),True)]:
        matcher=llguidance.LLMatcher(ll_tokenizer,grammar)
        ids=tokenizer.encode(json.dumps(payload,ensure_ascii=False,separators=(',',':')),add_special_tokens=False)
        assert bool(matcher.consume_tokens(ids) and matcher.is_accepting())==allowed


def test_prompt_shows_slot_order_and_nullable_omission_contract():
    from transformers import AutoTokenizer
    import hashlib
    path=Path(__file__).resolve().parents[1]/'models/gemma-tokenizer'
    if not path.is_dir():pytest.skip('Fixed tokenizer is required')
    tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
    rec,_,knowledge=multi()
    selection={'record_id':rec['id'], 'source_token_budget':4096, 'coverage':{},
        'source_tokens':sum(len(tokenizer.encode(d['text'],add_special_tokens=False)) for d in rec['docs']),
        'documents':[{'doc_index':i,'doc_id':d['doc_id'],
            'doc_sha256':hashlib.sha256(d['text'].encode()).hexdigest()} for i,d in enumerate(rec['docs'])],
        'spans':[{'doc_index':i,'doc_type':d['type'],'start':0,'end':len(d['text']),'text':d['text']}
                 for i,d in enumerate(rec['docs'])]}
    body=sem.prompt(rec,selection,tokenizer,knowledge,source_roles=True)
    text=body['messages'][1]['content']
    assert 'source_role_slots' in text and '"type":"null"' in text
    assert body['generation']['catalog_roles']['version']==roles.VERSION
