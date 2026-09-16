"""Integration contracts use original source and never development labels."""
import copy
import dataclasses
from pathlib import Path

import pytest

from submission.b4_entry import B4Pipeline, PROFILES, assemble, restored
from submission.pps.prompts import Config, verified_search_spans, token_ids
from submission.runtime import call_plan


@pytest.fixture(scope='module')
def tokenizer():
    from transformers import AutoTokenizer
    path = Path(__file__).resolve().parents[1] / 'models/gemma-tokenizer'
    if not path.is_dir():
        pytest.skip('Fixed local tokenizer needed')
    return AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)


@pytest.fixture
def rec():
    return {'id':'synthetic', 'meta':{}, 'docs':[
        {'doc_id':'N1', 'type':'공고문', 'text':'입찰 공고\n사업명: 시험장비 구매\n추정가격: 8,000만원\n'
            '입찰참가자격: 소기업 또는 소상공인\n직접생산확인증명서가 필요하다.'},
        {'doc_id':'S1', 'type':'규격서', 'text':'규격서\n품명: 시험장비\n모델명: Atlas R7\n'
            '동등 이상의 장비를 납품할 수 있다.'}]}


def pipeline(tokenizer, **kwargs):
    # This helper is the explicit historical control for producer comparisons.
    control = dict(source_policy='current', specification_review='current',
                   legal_policy='current', catalog_review='current', software_review='current',
                   catalog_source_policy='shared', catalog_task_groups=False)
    return B4Pipeline(Path(__file__).resolve().parents[1] / 'data_open/data', tokenizer, **{**control, **kwargs})


@pytest.mark.parametrize('field,value', [('notice_source_policy','invalid'),
    ('specification_review',True), ('legal_source_policy','similarity_is_violation'),
    ('catalog_source_policy','violation_similarity'),('catalog_task_groups','yes')])
def test_unknown_policies_rejected(field,value):
    with pytest.raises(ValueError):
        dataclasses.replace(Config(), **{field:value})


@pytest.mark.parametrize('kwargs', [{'source_policy':'factual_lexical'},
    {'specification_review':'candidates'}, {'legal_policy':'direct_production'}])
def test_preserved_strategy_cannot_silently_enable_new_producers(tokenizer,kwargs):
    with pytest.raises(ValueError):
        pipeline(tokenizer,input_strategy='preserved',**kwargs)


def test_preserved_control_validates_all_overrides_together(tokenizer,rec):
    p=pipeline(tokenizer,input_strategy='preserved')
    packets=p.packets([rec])
    assert [x['batch'] for x in packets] == list(PROFILES)
    assert all(x['input_strategy'] == 'preserved' for x in packets)
    assert packets[0]['rubric_version'] == 'v4'


def test_explicit_control_still_four_calls_and_no_new_model_features(tokenizer,rec):
    p=pipeline(tokenizer)
    packets=p.packets([rec])
    assert [x['batch'] for x in packets] == list(PROFILES)
    assert all('source_search' not in x and 'legal_control' not in x for x in packets)
    assert not p.specialist_preparation and p._source_encoder is None
    assert len(call_plan([rec],packets)) == 4


def test_default_executes_the_validated_integrated_producer(tokenizer,rec):
    import numpy as np
    class Encoder:
        def encode(self,texts):
            return np.tile(np.array([[1.,0.]],dtype=np.float32),(len(texts),1))
    data = Path(__file__).resolve().parents[1] / 'data_open/data'
    default = B4Pipeline(data, tokenizer, encoder=Encoder())
    explicit = pipeline(tokenizer, source_policy='purchase_context', legal_policy='direct_production',
                        specification_review='gated_source_candidates', catalog_review='explicit',
                        catalog_source_policy='task_hybrid',catalog_task_groups=True,
                        a10_thinking_budget=768, a_cohort_size=32,encoder=Encoder())
    assert default.packets([rec]) == explicit.packets([rec])
    assert default.config.catalog_source_policy=='task_hybrid' and default.config.catalog_task_groups


@pytest.mark.parametrize('policy', ['factual_lexical','evidence_cover'])
def test_source_policy_keeps_original_cap_schema_generation_and_locality(tokenizer,rec,policy):
    import numpy as np
    class DeterministicEncoder:
        def __init__(self): self.calls=[]
        def encode(self,texts):
            self.calls.append(tuple(texts))
            return np.tile(np.array([[1.,0.]],dtype=np.float32),(len(texts),1))
    encoder=DeterministicEncoder()
    before=copy.deepcopy(rec)
    controls=pipeline(tokenizer).packets([rec])
    p=pipeline(tokenizer,source_policy=policy,encoder=encoder)
    first=p.packets([rec])
    if policy=='factual_lexical': assert not encoder.calls
    else: assert encoder.calls
    for a,b in zip(controls,first):
        assert a['items']==b['items'] and a['generation']==b['generation']
        cap=sum(len(tokenizer.encode(s['text'],add_special_tokens=False)) for s in a['spans'])
        selected=verified_search_spans(rec,b['source_search'],tokenizer)
        assert selected==restored(b)['spans']
        assert b['source_search']['source_tokens']<=cap
        assert b['source_search']['diagnostics']['integrated_producer']['current_source_token_cap']==cap
        assert b['token_ids']==token_ids(tokenizer,b['messages'],p.config.enable_thinking)
        assert b['source_search']['coverage']['absence_verified'] is False
    assert rec==before
    assert p.packets([rec])==first


def test_specialist_and_legal_options_change_only_their_declared_output(tokenizer,rec):
    p=pipeline(tokenizer,specification_review='candidates',legal_policy='direct_production')
    packets=p.packets([rec]); index={x['batch']:x for x in packets}
    base={x['batch']:x for x in pipeline(tokenizer).packets([rec])}
    assert index['A1']==base['A1'] and index['A19']==base['A19'] and index['L19']==base['L19']
    assert index['A10']['spans']==base['A10']['spans']
    assert index['A10']['generation']==base['A10']['generation']
    from submission.pps.legal_query_contract import verify_prepared
    verify_prepared(index['A10'],rec,p.knowledge,tokenizer)
    specialist=index['S9']
    from submission.pps.source_units import unitize
    from submission.pps.specification_candidate_review import validate_prepared
    assert restored(specialist)['spans']==unitize(restored(base['A1'])['spans'])
    validate_prepared(rec,restored(specialist))
    assert specialist['generation']['thinking_budget']==0
    assert len(call_plan([rec],packets))==5
    rows={x['request_key']:{f'v{i}':0 for i in x['items']} for x in packets}
    rows[index['L19']['request_key']]={'v20':0,'e20':''}
    rows[specialist['request_key']]={'v9':1,'e9':rec['docs'][1]['text']}
    _,merged=assemble([rec],packets,rows)
    assert merged[rec['id']]['v9']==1 and merged[rec['id']]['v10']==0


def test_specialist_capacity_fallback_retains_base_and_records_reason(tokenizer,rec,monkeypatch):
    from submission.pps import specification_candidate_review as review
    def capacity(*args,**kwargs):
        raise ValueError('Candidate review input exceeds the common context budget')
    monkeypatch.setattr(review,'matched_prompts',capacity)
    p=pipeline(tokenizer,specification_review='candidates')
    packets=p.packets([rec])
    assert [x['batch'] for x in packets]==list(PROFILES)
    assert packets[0]['specialist_fallback']['status']=='shared_judgment_retained'
    assert len(call_plan([rec],packets))==4
    def invalid(*args,**kwargs): raise ValueError('Search source identity mismatch')
    monkeypatch.setattr(review,'matched_prompts',invalid)
    with pytest.raises(ValueError,match='identity mismatch'):
        p.packets([rec])


@pytest.mark.parametrize('mutation', ['duplicate','unrecognized','missing_base','wrong_specialist_item'])
def test_plan_rejects_lost_or_misdeclared_calls(tokenizer,rec,mutation):
    packets=pipeline(tokenizer,specification_review='candidates').packets([rec])
    if mutation=='duplicate': packets.append(copy.deepcopy(packets[-1]))
    elif mutation=='unrecognized': packets[-1]['batch']='undeclared'
    elif mutation=='missing_base': packets.pop(1)
    else: packets[-1]['items']=[20]
    with pytest.raises(ValueError): call_plan([rec],packets)
