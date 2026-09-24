"""Exercise preparation, normal execution, native recording and all fixed joins."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from tests.test_integrated_comparison_run import payload
from tools import run_runtime_revival as revival


def test_one_engine_normal_run_precedes_probes_and_replays_exactly(tmp_path,monkeypatch):
    root=Path(__file__).resolve().parents[1]
    import pytest
    if not (root/'models/gemma-tokenizer').is_dir():pytest.skip('Fixed tokenizer required')
    rec={'id':'synthetic','meta':{'업무구분':'일반용역','소관구분':'국가기관'},
        'docs':[{'doc_id':'N1','type':'공고문','text':'공고문\n사업명: 지역 실태 연구 용역\n1. 입찰참가자격\n가. 연구기관\n2. 제출방법\n전자제출'}],
        'input_completeness':{'완전관측':True},'dropped_doc_counts':{}}
    original=tmp_path/'input.jsonl';original.write_text(json.dumps(rec,ensure_ascii=False)+'\n',encoding='utf8')
    prepared=tmp_path/'prepared'
    revival.prepare(original,root/'data_open/data',root/'models/gemma-tokenizer',prepared)
    calls=[];lifecycle=[]
    class Engine:
        load_seconds=0
        def __init__(self,model,config,journal):
            lifecycle.append('load');self.config=config
            self.llm=SimpleNamespace(generate=self.native)
        def native(self,inputs,**kwargs):
            output=[]
            for p in self.active:
                fmt=p['generation']['response_format']
                if fmt=='catalog_scope':obj={'purchase_kind':'unknown','whole_task_units':[],
                    'task_summary':'자료 범위 미확정','catalog_relation':'unknown','relationships':[],'unresolved_scope':True}
                elif fmt=='software_refs':obj={'software_refs_v2':{'relations':[],
                    'disclosure':{'status':'absent_in_excerpt','witnesses':[]},'unresolved':[]}}
                else:obj=payload(p)
                text=json.dumps(obj,ensure_ascii=False)
                output.append(SimpleNamespace(request_id=p['request_key'],prompt_token_ids=p['token_ids'],
                    num_cached_tokens=0,outputs=[SimpleNamespace(text=text,token_ids=[1],finish_reason='stop')]))
            return output
        def generate(self,packets,max_tokens):
            self.active=packets;calls.extend(p['request_key'] for p in packets)
            if any(k.startswith('probe:') for k in calls[-len(packets):]):
                assert (tmp_path/'run/normal/prediction_freeze.json').is_file()
            outputs=self.llm.generate([{'prompt_token_ids':p['token_ids']} for p in packets],sampling_params=[])
            return [{'text':o.outputs[0].text,'raw_output_sha256':hashlib.sha256(o.outputs[0].text.encode()).hexdigest(),
                     'output_tokens':1,'finish_reason':'stop','cached_input_tokens':0} for o in outputs]
        def close(self):lifecycle.append('close')
    from submission import engine
    monkeypatch.setattr(engine,'CanonicalRunner',Engine)
    monkeypatch.setattr(engine,'configure_environment',lambda:None)
    result=revival.run(prepared,root/'data_open/data',root/'models/gemma-tokenizer',tmp_path/'run')
    assert lifecycle==['load','close'] and result['engine_loads']==1
    assert result['normal_report']['skipped_requests']==1
    assert calls[-2:]==['probe:A:synthetic:10','probe:Q:synthetic:10']
    from tools.verify_runtime_recovery import verify
    recovery=verify(prepared,tmp_path/'run',tmp_path/'recovery_verification')
    assert recovery['status']=='PASS' and recovery['single_engine_shutdown_verified']
    assert recovery['normal_selected_valid']==6 and recovery['probe_selected_valid']==2
    predictions=revival.consume(prepared,tmp_path/'run',root/'data_open/data',tmp_path/'consumed')
    assert len(predictions)==26 and all(p['status']=='complete' for p in predictions.values())
    assert (tmp_path/'consumed'/predictions[revival.NORMAL]['path']).read_bytes()==(tmp_path/'run/normal/submission.csv').read_bytes()
    from tools.score_runtime_revival import score,sha
    cpu=tmp_path/'consumed';normal=cpu/predictions[revival.NORMAL]['path']
    labels=tmp_path/'synthetic_labels.csv';labels.write_bytes(normal.read_bytes())
    baseline=tmp_path/'references.json'
    baseline.write_text(json.dumps({'references':[{'id':'synthetic','prediction':str(normal),'sha256':sha(normal)}],
        'exposed_development_labels_sha256':sha(labels),'normal_policy':revival.NORMAL,
        'recipes_sha256':sha(prepared/'recipes.json')}),encoding='utf8')
    scored=score(cpu,prepared,baseline,labels,tmp_path/'scored')
    assert len(scored['measurements'])==26
    assert sum(p['standalone_normal_execution'] for p in scored['measurements'].values())==1
    assert scored['measurements'][revival.NORMAL]['fp']==scored['measurements'][revival.NORMAL]['fn']==0
    from tools.analyze_runtime_revival import analyze
    analysis=analyze(prepared,tmp_path/'run',cpu,tmp_path/'scored',tmp_path/'analysis')
    assert analysis['policies']==26 and analysis['labels_reopened'] is False
    assert sum(v['observed_calls_including_recovery'] for v in analysis['profile_cost'].values())==8
    assert all(v['recorded_batches']==1 for v in analysis['profile_cost'].values())
    # Reusing native responses after CPU edits must never obtain a fresh score
    # label, even if every output happens to be identical to original inference.
    replay_path=tmp_path/'cpu_replay'
    generation_source=revival.source_manifest()
    monkeypatch.setattr(revival,'source_manifest',lambda:{**generation_source,'synthetic_cpu_edit':'changed'})
    with pytest.raises(ValueError,match='Canonical source differs'):
        revival.consume(prepared,tmp_path/'run',root/'data_open/data',tmp_path/'blocked_consumption')
    replayed=revival.consume(prepared,tmp_path/'run',root/'data_open/data',replay_path,cpu_replay=True)
    assert not any(r['standalone_normal_execution'] for r in replayed.values())
    with pytest.raises(ValueError,match='Not the frozen'):
        score(replay_path,prepared,baseline,tmp_path/'unopened_missing_labels.csv',tmp_path/'blocked_replay')
    replay_score=score(replay_path,prepared,baseline,labels,tmp_path/'replay_scored',cpu_replay=True)
    assert replay_score['kind']=='saved_response_current_cpu_replay'
    assert not any(r['standalone_normal_execution'] for r in replay_score['measurements'].values())
    # Corruption in even a diagnostic policy must be detected before labels,
    # rather than silently dropping that policy or shrinking its cohort.
    victim=cpu/'thinking768_catalog_control_software_none_v9_none.csv'
    victim.write_bytes(victim.read_bytes()+b'corrupt')
    with pytest.raises(ValueError,match='Frozen CSV changed'):
        score(cpu,prepared,baseline,tmp_path/'unopened_missing_labels.csv',tmp_path/'blocked')
    assert not (tmp_path/'blocked').exists()
