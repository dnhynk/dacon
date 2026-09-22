"""Focused overlay invariants; no model, labels, network or GPU."""
import copy
import dataclasses
import json
from types import SimpleNamespace

import pytest

from submission.pps import focused_verify as fv
from submission.pps.prompts import Config
from submission.b4_entry import parse_error
from submission.stream import RecordState, StreamingExecutor, StreamOptions, TIERS


def job(family='size'):
    source = dict(s=1, doc_index=0, doc_type='공고문', doc_id='D0', start=0, end=25,
                  text='입찰 참가자격은 소기업과 소상공인으로 제한한다.')
    source['end'] = len(source['text'])
    return dict(family=family, record_id='local', candidate_id='local:size:0', candidate=source,
                context=[source], selection={'lexical_score': 2},
                premises={'입찰추정가격': 150000000, '계약방법': '제한경쟁', '낙찰방법': '협상에의한계약',
                          'CPU_지역제한상한_meta만': {'below_ceiling': 230000000, 'above_ceiling': 230000000}})


def answer(j, item=15):
    read = dict(s=1, restriction='소기업_소상공인', role='participation_requirement', quote=j['candidate']['text'])
    return dict(read=read, items={f'v{i}':dict(v=int(i==item),s=1 if i==item else None,reason='')
                                 for i in fv.prompt.FAMILIES[j['family']]})


def packet(j):
    return dict(focused_job=j, focused_cpu={'product_status':'general'}, items=[13,14,15,17],
                generation={'response_format':'focused_verify'})


@pytest.mark.parametrize('mutation', ['citation','multiple','nonparticipation','unknown_product','nonpositive'])
def test_agreement_abstains(mutation):
    j=job();a=answer(j);cpu={'product_status':'general'}
    if mutation=='citation':a['items']['v15']['s']=None
    if mutation=='multiple':a['items']['v14']['v']=1
    if mutation=='nonparticipation':a['read']['role']='document_notice'
    if mutation=='unknown_product':cpu['product_status']=None
    if mutation=='nonpositive':a['items']['v15']['v']=0
    assert fv.decide(j,a,cpu)[0] is None


def test_source_bound_positive_and_evidence():
    j=job();a=answer(j)
    rec={'docs':[{'text':j['candidate']['text']}]}
    response={'text':json.dumps(a),'finish_reason':'stop'}
    assert parse_error(packet(j),response) is None
    row,proof=fv.consume(rec,packet(j),response)
    assert row=={'v15':1,'e15':j['candidate']['text']}
    assert proof['citation']==j['candidate']
    rec['docs'][0]['text']='different source'
    with pytest.raises(ValueError,match='original source'):
        fv.consume(rec,packet(j),response)


@pytest.mark.parametrize('mutation',['quote','token','source','extra','truncated'])
def test_format_and_literal_failures(mutation):
    j=job();a=answer(j)
    if mutation=='quote':a['read']['quote']='fabricated'
    if mutation=='token':a['read']['restriction']='illegal_enum'
    if mutation=='source':a['read']['s']=999
    if mutation=='extra':a['extra']=1
    response={'text':json.dumps(a),'finish_reason':'length' if mutation=='truncated' else 'stop'}
    assert parse_error(packet(j),response)


def test_gate_skips_existing_fact_or_any_family_positive():
    j=job();parsed={f:[] for f in fv.FAMILIES};cpu={'product_status':'general'}
    assert fv.gate_job(j,parsed,{},cpu) is None
    assert fv.gate_job(j,parsed,{'v14':1},cpu)=='already_positive'
    parsed['size']=[j['candidate']]
    assert fv.gate_job(j,parsed,{},cpu)=='parsed_participation'


def test_explicit_lawful_veto_is_source_linked():
    j=job();cpu={'product_status':'general','zeros':[{'item':15,'reason':'identified_purchase_outside_conditional_catalog'}],
                'qualification_spans':[j['candidate']]}
    assert fv.decide(j,answer(j),cpu)[0] is None
    cpu['qualification_spans']=[]
    assert fv.decide(j,answer(j),cpu)==(15,'accepted')


def test_cpu_facts_uses_final_product_override():
    details=[[{'source':'source','facts':{'product':{'status':'competition'}}}],
             {'product':{'status':'general'},'decisions':{'v15':{'value':0,'reason':'x'}}}]
    cpu=fv.cpu_facts(details)
    assert cpu['product_status']=='general'
    assert cpu['zeros']==[{'item':15,'value':0,'reason':'x'}]


def test_default_off_and_parameter_validation():
    assert Config().focused_verify is False
    assert fv.prepare(SimpleNamespace(config=Config()),None,None,None)==[]
    for kw in ({'focused_verify':1},{'focused_verify_top_k':3},{'focused_verify_neighbors':0},
               {'focused_verify_seconds_per_record':1}):
        with pytest.raises(ValueError):Config(**kw)


def test_native_sampling_uses_focused_schema_and_thinking_budget(monkeypatch):
    import sys
    from submission.pps.pipeline import VLLMRunner
    captured={}
    def sampling(**kw):captured.update(kw);return kw
    monkeypatch.setitem(sys.modules,'vllm',SimpleNamespace(SamplingParams=sampling))
    monkeypatch.setitem(sys.modules,'vllm.sampling_params',SimpleNamespace(StructuredOutputsParams=lambda **kw:kw))
    runner=object.__new__(VLLMRunner);runner.config=Config(enable_thinking=True)
    j=job();p=packet(j);p['generation'].update(schema=fv.prompt.COMBINED['size'],thinking_budget=384,max_output_tokens=1024)
    runner.sampling_params(p)
    assert captured['thinking_token_budget']==384 and captured['max_tokens']==1024
    assert captured['structured_outputs']['json']==fv.prompt.COMBINED['size']


def test_optional_profiles_wait_for_core_and_drop_first(tmp_path, monkeypatch):
    from test_stream_executor import SyntheticPipeline, CONFIG, Clock, FakeRunner, ClockedPool, record, valid
    from submission.runtime import Journal
    pipeline=SyntheticPipeline(q10=['local'])
    pipeline.config=SimpleNamespace(**vars(CONFIG),focused_verify=True,focused_verify_seconds_per_record=.1619)
    clock=Clock();runner=FakeRunner(lambda p,a:valid(p,0),clock)
    pool=ClockedPool(pipeline,clock)
    options=StreamOptions(total_runtime_seconds=100000,tier_ceiling=2)
    executor=StreamingExecutor([record('local')],pipeline,runner,pool,Journal(tmp_path),options,started_at=clock(),clock=clock)
    pool.start();state=executor.states[0]
    state.tier=TIERS[2]['name'];state.planned=['A1','A19','Q10']
    state.rows={'A1':{},'A19':{}}
    executor._maybe_finalize(state)
    assert not state.focused_decided and not state.finalized
    state.rows['Q10']={}
    executor.focused_budget_seconds=2.
    captured=[];monkeypatch.setattr(pool,'submit',lambda task:captured.append(task))
    executor._maybe_finalize(state)
    assert captured[0]['kind']=='focused_prepare'
    assert not state.finalized
    p=dict(batch='FV:size:1',token_ids=[1]*1500,generation={'max_output_tokens':1024})
    executor.focused_budget_seconds=.1619
    executor._submit_focused(state,[p])
    assert executor.counts['focused_budget_drops']==1
    assert executor.policy.current==2 and not runner.submitted
    executor.focused_budget_seconds=100
    monkeypatch.setattr(executor.policy,'projection',lambda submitted:dict(a=.00015,b=.00035,need={2:1000},time_left=1))
    executor._submit_focused(state,[p])
    assert executor.counts['focused_budget_drops']==2
    assert executor.policy.current==2 and not runner.submitted
    executor.native_out.close();executor.consumed_out.close();executor.packets_out.close();executor.records_out.close()


def test_conflicting_same_clause_abstains_and_existing_positive_survives():
    state=RecordState(0,{'id':'local'})
    state.rows={'A1':{},'A10':{'v15':1,'e15':'existing'},'A19':{}}
    proof=dict(reason='accepted',family='size',citation=job()['candidate'],item=14,evidence='new')
    state.focused_proofs={'FV:size:1':proof,'FV:size:2':{**proof,'item':15}}
    executor=object.__new__(StreamingExecutor)
    import collections
    executor.counts=collections.Counter()
    row=executor._assemble(state)
    assert row['v14']==0 and row['v15']==1 and row['e15']=='existing'


def test_stream_runs_bounded_focused_after_core(tmp_path,monkeypatch):
    from test_stream_executor import SyntheticPipeline,CONFIG,record,valid,run,Clock,FakeRunner,csv_rows
    class Pipeline(SyntheticPipeline):
        def consume(self,rec,pkt,response):
            return fv.consume(rec,pkt,response) if pkt['family']=='FV' else super().consume(rec,pkt,response)
    recs=[record(f'local{i}') for i in range(8)]
    for rec in recs:rec['docs'][0]['text']=job()['candidate']['text']
    pipeline=Pipeline(q10=[r['id'] for r in recs])
    pipeline.config=SimpleNamespace(**vars(CONFIG),focused_verify=True,focused_verify_seconds_per_record=.1619)
    def prepare(pipe,rec,base,details):
        assert base['v15']==0
        j=job();j['record_id']=rec['id']
        return [dict(packet(j),batch='FV:size:1',family='FV',record_id=rec['id'],
            request_key='FV:'+rec['id'],token_ids=[1]*1000,prompt_sha256='p',token_ids_sha256='t',spans=[],
            generation={'response_format':'focused_verify','max_output_tokens':1024})]
    monkeypatch.setattr(fv,'prepare',prepare)
    def respond(pkt,attempt):
        return (json.dumps(answer(pkt['focused_job'])),'stop') if pkt['family']=='FV' else valid(pkt,0)
    clock=Clock();runner=FakeRunner(respond,clock);runner.config=pipeline.config
    report,runner=run(tmp_path,recs,pipeline,respond,options=StreamOptions(total_runtime_seconds=100000,tier_ceiling=2),clock=clock,runner=runner)
    rows=list(csv_rows(tmp_path/'output/submission.csv').values())
    assert 0<report['focused_verify']['requests']<len(recs)
    assert report['focused_verify']['reserved_seconds']<=len(recs)*.1619
    assert sum(int(r['v15']) for r in rows)==report['focused_verify']['requests']
    for rid in [r['id'] for r in recs]:
        if 'FV:'+rid+'#0' in runner.submitted:
            focus=runner.submitted.index('FV:'+rid+'#0')
            assert all(runner.submitted.index(f'{family}:{rid}:{item}#0')<focus
                       for family,item in [('A',1),('A',19),('Q',10)])
