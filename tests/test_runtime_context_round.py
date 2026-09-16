"""Run the new source/cache preparation through the actual canonical controller."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_integrated_comparison_run import payload
from tools.audit_specification_candidates import unknown_response
from tools import prepare_runtime_context as context
from tools import run_runtime_revival as revival


def test_context_normal_and_explicit_repeat_probes_share_one_engine(tmp_path,monkeypatch):
    root=Path(__file__).resolve().parents[1]
    if not (root/'models/gemma-tokenizer').is_dir():pytest.skip('Fixed tokenizer required')
    record={'id':'synthetic','meta':{'업무구분':'물품(내자)','소관구분':'국가기관'},
        'docs':[{'doc_id':'n','type':'공고문','text':'입찰공고\n사업명: 시험장비 구매\n1. 구성품\nACME Raven 1개, USB-C 케이블 2개\n2. 자격\n입찰참가자격 제한 없음'}],
        'input_completeness':{'완전관측':True},'dropped_doc_counts':{}}
    source=tmp_path/'input.jsonl';source.write_text(json.dumps(record,ensure_ascii=False)+'\n',encoding='utf8')
    prepared=tmp_path/'prepared'
    report=context.prepare(source,root/'data_open/data',root/'models/gemma-tokenizer',root/'models/bge-m3',prepared,
                           arms=('purchase_context','current'),cache_notices=1)
    assert report['policies']==9 and report['probe_roles']['cache2']==report['probe_roles']['cache32']==3
    declared=revival.read(prepared/'recipes.json')
    assert not any(':cache' in k for keys in declared.values() for k in keys)
    # Exercise the same source/absence coverage audit used on the real cohort.
    from tools.audit_integrated_sources import audit
    review=tmp_path/'review.json';review.write_text(json.dumps({'cases':[]}),encoding='utf8')
    observed=audit(prepared,review,root/'models/gemma-tokenizer',tmp_path/'source_audit')
    assert observed['all_source_token_caps_verified']
    lifecycle=[]
    class Engine:
        load_seconds=0
        def __init__(self,model,config,journal):
            self.config=config;self.llm=SimpleNamespace(generate=self.native);lifecycle.append('load')
        def native(self,inputs,**kwargs):
            results=[]
            for p in self.active:
                if p['generation']['response_format']=='specification_candidates':
                    obj=unknown_response(p['specification_inventory'])
                    for value in obj['specification_candidate_reviews_v1'].values():
                        value.update(permission_attribute='unknown',permission_effect='unknown')
                    obj.update(unresolved='미확인',judgment={'reason':'관계 미확인','v':0,'e':0})
                else:obj=payload(p)
                text=json.dumps(obj,ensure_ascii=False)
                results.append(SimpleNamespace(request_id=p['request_key'],prompt_token_ids=p['token_ids'],num_cached_tokens=0,
                    outputs=[SimpleNamespace(text=text,token_ids=[1],finish_reason='stop')]))
            return results
        def generate(self,packets,max_tokens):
            self.active=packets
            if packets[0]['request_key'].startswith('probe:'):
                assert (tmp_path/'run/normal/submission.csv').is_file()
            return [{'text':r.outputs[0].text,'raw_output_sha256':hashlib.sha256(r.outputs[0].text.encode()).hexdigest(),
                'output_tokens':1,'finish_reason':'stop','cached_input_tokens':0} for r in
                self.llm.generate([{'prompt_token_ids':p['token_ids']} for p in packets],sampling_params=[])]
        def close(self):lifecycle.append('close')
    from submission import engine
    monkeypatch.setattr(engine,'CanonicalRunner',Engine)
    monkeypatch.setattr(engine,'configure_environment',lambda:None)
    completed=revival.run(prepared,root/'data_open/data',root/'models/gemma-tokenizer',tmp_path/'run')
    assert lifecycle==['load','close'] and completed['diagnostic_valid']==report['probe_requests']
    consumed=revival.consume(prepared,tmp_path/'run',root/'data_open/data',tmp_path/'consumed')
    assert len(consumed)==9 and sum(p['standalone_normal_execution'] for p in consumed.values())==1
    assert consumed[context.NORMAL]['sha256']==revival.sha256(tmp_path/'run/normal/submission.csv')
    from tools.verify_runtime_recovery import verify
    verified=verify(prepared,tmp_path/'run',tmp_path/'verification')
    assert verified['status']=='PASS'
    from tools.audit_cache_repeats import audit as audit_repeats
    repeats=audit_repeats(prepared,tmp_path/'run',tmp_path/'consumed',tmp_path/'verification',tmp_path/'repeats')
    assert repeats['repeats_never_selected_in_recipes'] and not repeats['labels_read']
    for group in repeats['groups'].values():
        assert group['planned']==group['complete']==group['same_output_token_ids']==3
        assert group['same_raw_judgments']==3 and group['changed_cpu_bits']==0
