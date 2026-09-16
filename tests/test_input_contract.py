"""Raw input states, declared coverage and pre-generation validation."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from submission.pps.data import records
from submission.pps.input_contract import coverage,diagnostics,metadata_states,provided_complete
from submission.pps.qualification import inventory,qualification_facts
from submission.pps.other_checks import complete
from submission.pps.reference_coverage import assess
from tests.test_qualification_heading_roles import notice


def rec():
    r=notice('3. 입찰참가자격','가. 일반 업체')
    r['input_completeness']={'공고문_실재':True,'추출_성공':True,'무탈락':True,'완전관측':True}
    r.update(anon_applied=True,assembly_policy_version='assembly-v3-0820')
    return r


@pytest.mark.parametrize('field',['공고문_실재','추출_성공','무탈락'])
def test_aggregate_true_cannot_erase_a_false_component_in_any_completeness_consumer(field):
    r=rec()
    r['input_completeness'][field]=False
    original=copy.deepcopy(r)
    q=qualification_facts(r,inventory(r))
    assert not q['complete'] and not q['no_size'] and not q['no_direct']
    assert not complete(r) and not assess(r)['declared_input_complete']
    assert not provided_complete(r) and coverage(r)['contradictions']
    assert r==original


@pytest.mark.parametrize('bad',[None,'true',1,0,[],{}])
def test_nonboolean_component_cannot_establish_complete_observation(bad):
    r=rec()
    r['input_completeness']['추출_성공']=bad
    assert not provided_complete(r) and coverage(r)['type_errors']


def test_complete_false_is_not_promoted_from_other_true_components():
    r=rec()
    r['input_completeness']['완전관측']=False
    assert not provided_complete(r)


def test_positive_dropped_count_blocks_coverage_even_with_conflicting_aggregate():
    r=rec()
    r['dropped_doc_counts']={'제안요청서':1}
    c=coverage(r)
    assert not c['provided_complete'] and c['positive_dropped_count_observed']
    assert len(c['contradictions'])==2


def test_legacy_aggregate_declaration_is_retained_with_missing_components_visible():
    r=rec()
    r['input_completeness']={'완전관측':True}
    c=coverage(r)
    assert c['provided_complete']
    assert c['missing_component_fields']==['공고문_실재','추출_성공','무탈락']
    assert c['selected_model_input_complete']=='not_established_by_input_metadata'


@pytest.mark.parametrize('field,value,state',[
    ('지역제한여부',None,'null_value'),('지역제한여부','미입력','unregistered'),
    ('지역제한여부','해당 없음','explicit_not_applicable'),('지역제한여부','N','known_negative'),
    ('지역제한여부','Y','known_positive'),('지역제한여부','','empty_string'),
    ('지역제한여부',False,'unrecognized_flag_value'),('지역제한여부',0,'unrecognized_flag_value'),
    ('입찰추정가격',0,'present_value'),('계약방법','N','present_value'),
])
def test_presence_states_are_field_specific_and_do_not_rewrite_registration(field,value,state):
    r=rec()
    r['meta'][field]=value
    original=copy.deepcopy(r)
    observed=metadata_states(r)[field]
    assert observed=={'state':state,'present':True,'raw_value':value}
    assert r==original


def test_an_absent_field_is_not_a_present_null_or_an_unregistered_value():
    r=rec()
    r['meta'].pop('지역제한여부',None)
    assert metadata_states(r)['지역제한여부']=={'state':'missing_field','present':False,'raw_value':None}


@pytest.mark.parametrize('anon,version',[(False,'assembly-v3-0820'),(True,'future-assembly'),(False,'future-assembly')])
def test_anonymization_and_assembly_values_are_diagnostics_not_prediction_features(anon,version):
    r=rec()
    base=qualification_facts(r,inventory(r))
    r.update(anon_applied=anon,assembly_policy_version=version)
    current=qualification_facts(r,inventory(r))
    assert current==base
    d=diagnostics(r)
    assert not d['anon_applied']['used_for_prediction'] and not d['assembly']['used_for_prediction']
    assert not d['management_type_errors']
    assert d['assembly']['status']==('observed_version' if version=='assembly-v3-0820' else 'unrecognized_version_fields_checked')


@pytest.mark.parametrize('field,value',[
    ('anon_applied','true'),('anon_applied',1),('assembly_policy_version',3),('assembly_policy_version',''),
    ('input_completeness',None),('input_completeness',{'완전관측':True,'추출_성공':'false'}),
    ('dropped_doc_counts',None),('dropped_doc_counts',{'규격서':False}),
    ('dropped_doc_counts',{'규격서':-1}),('dropped_doc_counts',{'규격서':1.5}),
])
def test_present_invalid_management_fields_are_rejected_at_the_input_boundary(tmp_path,field,value):
    r=rec()
    r[field]=value
    path=tmp_path/'input.jsonl'
    path.write_text(json.dumps(r,ensure_ascii=False)+'\n',encoding='utf8')
    with pytest.raises(ValueError,match='Invalid input management'):
        list(records(path))


@pytest.mark.parametrize('raw',[
    '{"id":"one","id":"two","meta":{},"docs":[]}',
    '{"id":"one","meta":{"지역제한여부":"N","지역제한여부":"Y"},"docs":[]}',
    '{"id":"one","meta":{"금액":NaN},"docs":[]}',
    '{"id":"one","meta":{"금액":Infinity},"docs":[]}',
    '{"id":"one","meta":{"금액":1e999},"docs":[]}',
])
def test_ambiguous_or_nonfinite_json_cannot_silently_replace_the_input(tmp_path,raw):
    path=tmp_path/'input.jsonl'
    path.write_text(raw+'\n',encoding='utf8')
    with pytest.raises(ValueError,match='Duplicate input JSON|Non-finite input JSON'):
        list(records(path))


@pytest.mark.parametrize('r',[[],None,{'id':'one','meta':{},'docs':[None]}])
def test_malformed_outer_and_document_shapes_produce_input_errors(tmp_path,r):
    path=tmp_path/'input.jsonl'
    path.write_text(json.dumps(r)+'\n',encoding='utf8')
    with pytest.raises(ValueError):
        list(records(path))


@pytest.mark.parametrize('limit',[True,False,1.0,0,-1])
def test_limit_is_an_explicit_positive_integer(tmp_path,limit):
    with pytest.raises(ValueError,match='positive integer'):
        list(records(tmp_path/'unused',limit))


def test_unknown_fields_and_versions_and_explicit_negative_are_preserved(tmp_path):
    r=rec()
    r.update(assembly_policy_version='future-assembly',anon_applied=False,new_management={'value':None})
    r['meta'].update(정보화사업여부='미입력',긴급공고여부='N',추가필드={'value':'해당 없음'})
    path=tmp_path/'input.jsonl'
    payload=json.dumps(r,ensure_ascii=False)+'\n'
    path.write_text(payload,encoding='utf8')
    assert list(records(path))==[r]
    assert path.read_text('utf8')==payload


def test_inconsistent_coverage_is_observable_without_discarding_independent_source_facts(tmp_path):
    r=rec()
    r['input_completeness']['추출_성공']=False
    r['docs'][0]['text']=r['docs'][0]['text'].replace('가. 일반 업체','가. 중소기업 확인서를 보유한 업체이어야 한다.')
    path=tmp_path/'input.jsonl'
    path.write_text(json.dumps(r,ensure_ascii=False)+'\n',encoding='utf8')
    loaded=list(records(path))[0]
    q=qualification_facts(loaded,inventory(loaded))
    assert q['active_size'] and not q['complete'] and not q['no_direct']


def test_auxiliary_sme_absence_agrees_with_the_qualification_guard_on_an_unclosed_scope():
    from submission.pps.knowledge import Knowledge
    from tests.test_service_identity import DATA
    r=rec()
    r['docs'][0]['text']+='\n6. 입찰참가자격\n다음 페이지에 계속'
    q=qualification_facts(r,inventory(r))
    s=Knowledge(DATA).sme_record_facts(r)
    assert not q['no_size'] and not q['no_direct']
    assert not s['absence_proof']['no_size_requirement'] and not s['absence_proof']['no_direct_requirement']
    assert s['absence_proof']['unclosed_eligibility']


def test_invalid_input_is_rejected_before_engine_creation(tmp_path):
    from submission.runtime import execute
    r=rec()
    r['anon_applied']='true'
    path=tmp_path/'input.jsonl'
    path.write_text(json.dumps(r,ensure_ascii=False)+'\n',encoding='utf8')
    def forbidden(*args):
        pytest.fail('Engine allocated for an invalid input')
    with pytest.raises(ValueError,match='Invalid input management'):
        execute(path,tmp_path/'unused',tmp_path/'output',tokenizer=object(),runner_factory=forbidden)


def test_runtime_freezes_raw_file_identity_and_states_without_changing_packets(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from submission import runtime
    from tests.test_submission_runtime import packets as synthetic_packets
    config=SimpleNamespace(batch_size=32,require_positive_evidence=False)
    r=rec()
    r['meta'].update(정보화사업여부='미입력',긴급공고여부='N')
    path=tmp_path/'input.jsonl'
    path.write_text(json.dumps(r,ensure_ascii=False)+'\n',encoding='utf8')
    original_hash=hashlib.sha256(path.read_bytes()).hexdigest()
    expected={p['request_key']:p for p in synthetic_packets([r])}
    class SyntheticPipeline:
        def __init__(self,*args):
            self.config=config
        packets=staticmethod(synthetic_packets)
        def consume(self,record,packet,response):
            items=[20] if packet['family']=='L' else packet['items']
            return {f'{prefix}{k}':0 if prefix=='v' else '' for k in items for prefix in ('v','e')},[]
    monkeypatch.setattr(runtime,'B4Pipeline',SyntheticPipeline)
    def generate(batch,number,attempt):
        assert all(p==expected[p['request_key']] for p in batch)
        return [{'text':json.dumps({'v':[0]*len(p['items']),'e':[0]*len(p['items'])}),
            'finish_reason':'stop','output_tokens':1} for p in batch]
    out=tmp_path/'output'
    runtime.execute(path,tmp_path/'unused',out,
        SimpleNamespace(config=config,tokenizer=object()),generation=generate)
    contract=json.loads((out/'input_contract.json').read_text('utf8'))
    freeze=json.loads((out/'input_freeze.json').read_text('utf8'))
    assert contract['original_input_sha256']==freeze['original_input_sha256']==original_hash
    assert freeze['input_contract_sha256']==hashlib.sha256((out/'input_contract.json').read_bytes()).hexdigest()
    states=contract['records'][0]['metadata_states']
    assert states['정보화사업여부']['state']=='unregistered'
    assert states['긴급공고여부']['state']=='known_negative'
    assert not contract['prediction_features_added']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==original_hash
