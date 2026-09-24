"""Integrated reviews retain unknowns, source ranges and their output ownership."""
import copy
import dataclasses
import json
from types import SimpleNamespace

import pytest

from submission.b4_entry import B4Pipeline, assemble, parse_error
from submission.runtime import call_plan
from submission.pps.software_facts import decide
from submission.pps.retrieval import Span
from tests.test_integrated_candidates import tokenizer, pipeline, rec
from tests.test_software_refs import source, response


def observed(packet):
    return {(s['doc_index'],s['start']+i,c) for s in packet['spans']
            for i,c in enumerate(s['text']) if not c.isspace()}


def test_broad_review_packets_preserve_normal_source_and_do_not_change_base(tokenizer,rec):
    rec=copy.deepcopy(rec)
    rec['meta']={'업무구분':'일반용역','소관구분':'국가기관'}
    rec['docs'][0]['text']='공고문\n사업명: 실태조사 연구 용역\n1. 입찰참가자격\n가. 연구기관\n2. 계약방법\n일반경쟁'
    before=copy.deepcopy(rec)
    base={p['batch']:p for p in pipeline(tokenizer).packets([rec])}
    pipe=pipeline(tokenizer,catalog_review='explicit',software_review='relations',specification_review='gated_candidates')
    packets=pipe.packets([rec]);index={p['batch']:p for p in packets}
    assert set(index)=={'A1','A10','A19','L19','Q10','W20','S9'}
    for role,p in base.items():assert p==index[role]
    assert observed(index['Q10'])==observed(base['A1'])
    assert observed(index['W20'])==observed(base['L19'])
    assert index['Q10']['catalog_scope']['all_supplied_service_rows_present']
    assert len(call_plan([rec],packets))==7 and rec==before


def test_task_hybrid_changes_only_Q_with_same_source_cap_and_original_coordinates(tokenizer,rec):
    import numpy as np
    class Encoder:
        receipt={'model':'synthetic-fixed','device':'cpu'}
        def encode(self,texts):
            values=np.zeros((len(texts),3),dtype=np.float32)
            for i,text in enumerate(texts):
                values[i]=[1.,len(text)%7,len(text)%11]
            values/=np.linalg.norm(values,axis=1,keepdims=True)
            return values
    rec=copy.deepcopy(rec)
    rec['meta']={'업무구분':'일반용역','소관구분':'국가기관'}
    rec['docs'][0]['text']=('공고문\n용역개요: 폐기물 수집 및 처리(폐콘크리트\n900톤)\n'
        '다만 지정폐기물은 과업에서 제외한다.\n입찰참가자격: 연구기관\n계약방법: 일반경쟁')
    shared=pipeline(tokenizer,catalog_review='explicit').packets([rec])
    pipe=pipeline(tokenizer,catalog_review='explicit',catalog_source_policy='task_hybrid',
                  catalog_task_groups=True,encoder=Encoder())
    enhanced=pipe.packets([rec])
    left={p['batch']:p for p in shared};right={p['batch']:p for p in enhanced}
    assert set(left)==set(right)=={'A1','A10','A19','L19','Q10'}
    assert all(left[k]==right[k] for k in ('A1','A10','A19','L19'))
    q=right['Q10'];base=left['Q10']
    assert q!=base and q['generation']==base['generation']
    assert q['source_search']['source_tokens']<=base['source_search']['source_tokens']
    diag=q['source_search']['diagnostics']['integrated_catalog_producer']
    assert diag['policy']=='task_hybrid' and diag['task_groups'] is True
    assert q['task_field_groups'] and q['coverage']['absence_verified'] is False
    for span in q['spans']:
        doc=rec['docs'][span['doc_index']]['text']
        assert span['text']==doc[span['start']:span['end']]
    assert pipe.packets([rec])==enhanced


def test_partial_scope_and_unknown_software_do_not_zero_fill_independent_fields():
    rec={'id':'synthetic'}
    packets=[{'family':f,'request_key':k,'record_id':rec['id']} for f,k in
             [('A','base'),('L','legacy'),('Q','catalog'),('W','software')]]
    rows={'base':{'v10':1,'v11':1,'v17':1,'v20':1},'legacy':{'v20':1,'e20':''},
          'catalog':{'v10':0,'e10':''},'software':{}}
    b3,b4=assemble([rec],packets,rows)
    assert b4['synthetic']['v10']==0
    assert b4['synthetic']['v11']==b4['synthetic']['v17']==b4['synthetic']['v20']==1
    rows['catalog']=None
    assert assemble([rec],packets,rows)[1]['synthetic']['v10']==1
    rows['catalog']={'v9':0}
    with pytest.raises(ValueError,match='items10..18'):assemble([rec],packets,rows)


def test_deferred_uniform_software_negative_is_an_empty_update():
    rec={'id':'synthetic'}
    packets=[{'family':'A','request_key':'base','record_id':rec['id']},
             {'family':'L','request_key':'legacy','record_id':rec['id']}]
    base={**{f'v{i}':0 for i in range(1,25)}, **{f'e{i}':'' for i in range(1,25)},
          'v20':1,'e20':'independent A'}
    _, b4=assemble([rec],packets,{'base':base,'legacy':{}})
    assert b4['synthetic']['v20']==1
    assert b4['synthetic']['e20']=='independent A'


@pytest.mark.parametrize('condition,expected', [('ordinary',1),('floor',0),('exception',None),('missing',None)])
def test_software_can_delegate_disclosure_scan_without_claiming_model_read_all_source(condition,expected):
    text='계약업체는 발주처의 사용권을 1년 연장해야 한다.'
    tail={'ordinary':'납품 기간은 1년이다.',
          'floor':'소프트웨어진흥법 제48조에 따른\n사업금액별 참여 제한을 적용한다.',
          'exception':'소프트웨어진흥법 제48조 제3항의\n예외를 적용한다.',
          'missing':'나머지 자료는 제공하지 않았다.'}[condition]
    rec,_=source(text+'\n'+tail)
    if condition=='missing':rec['input_completeness']['완전관측']=False
    spans=[Span(0,'공고문',0,len(text),text)]
    raw=json.dumps(response([1]),ensure_ascii=False)
    result=decide(rec,raw,spans,expected_format='software_refs',absence_scope='source_scan')
    assert result['value'] is expected
    assert not result['coverage']['all_supplied_text_read']
    scan=result['code_disclosure_scan']
    assert scan['documents'][0]['characters']==len(rec['docs'][0]['text'])
    assert not scan['model_excerpt_expanded'] and not scan['semantic_completeness_certified']
    if condition=='ordinary':
        assert decide(rec,raw,spans,expected_format='software_refs')['value'] is None


def test_optional_software_unknown_is_empty_update_not_a_negative():
    rec,spans=source('제공 방법은 추후 협의한다.')
    raw={'text':json.dumps(response([1],role='unknown')),'finish_reason':'stop'}
    packet={'family':'W','items':[20],'spans':[dataclasses.asdict(s) for s in spans],
            'generation':{'response_format':'software_refs'}}
    assert parse_error(packet,raw) is None
    row,details=B4Pipeline.__new__(B4Pipeline).consume(rec,packet,raw)
    assert row=={} and details[0]['decision']['value'] is None


def test_normal_runtime_gates_specialist_after_A_and_never_fabricates_skipped_responses(tmp_path,monkeypatch):
    from submission import runtime
    from tests.test_submission_runtime import packets as base_packets,read_rows
    recs=[{'id':s} for s in ('positive','inventory','empty')]
    config=SimpleNamespace(batch_size=32,require_positive_evidence=False,
        specification_review='gated_candidates',catalog_review='current',software_review='current')
    class Pipeline:
        specialist_preparation=[]
        def __init__(self,*args):self.config=config
        def packets(self,records):
            result=base_packets(records)
            for rec in records:
                result.append({'record_id':rec['id'],'request_key':'S9:'+rec['id'],'family':'A','batch':'S9',
                    'items':[9],'spans':[],'generation':{'response_format':'specification_candidates'},
                    'specification_inventory':{'candidates':[{}] if rec['id']=='inventory' else []}})
            return result
        def consume(self,rec,packet,response):
            if packet['batch']=='S9':return {},[]
            items=[20] if packet['family']=='L' else packet['items']
            return {f'{f}{i}':int(i==9 and rec['id']=='positive') if f=='v' else ''
                    for i in items for f in ('v','e')},[]
    monkeypatch.setattr(runtime,'B4Pipeline',Pipeline)
    monkeypatch.setattr(runtime,'records',lambda *args:iter(recs))
    # This test isolates scheduling and journaling; parser contracts have their
    # own actual-schema tests, so these are deliberately synthetic responses.
    monkeypatch.setattr(runtime,'parse_error',lambda *args:None)
    generated=[]
    def generate(batch,*args):
        generated.extend(p['request_key'] for p in batch)
        return [{'text':'synthetic','finish_reason':'stop','output_tokens':1} for p in batch]
    report=runtime.execute('unused','unused',tmp_path/'output',
        SimpleNamespace(config=config,tokenizer=object()),generation=generate)
    assert report['prepared_requests']==15 and report['primary_requests']==14 and report['skipped_requests']==1
    assert 'S9:positive' in generated and 'S9:inventory' in generated and 'S9:empty' not in generated
    stored=read_rows(tmp_path/'output/resolved_responses.jsonl.gz')
    assert len(stored)==14 and all(r['request_key']!='S9:empty' for r in stored)
    skipped=json.loads((tmp_path/'output/skipped_requests.json').read_text(encoding='utf8'))
    assert skipped['S9:empty']['model_called'] is False
