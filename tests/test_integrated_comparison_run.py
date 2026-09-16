"""A complete synthetic prepared/recorded/consumed round, with native tamper checks."""
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from tools.prepare_integrated_comparison import prepare,native_identity
from tools.run_integrated_comparison import consume,rows,verify_resolved_native,final_text_from_native
from submission.pps.prompts import output_schema
from submission.pps import specification_candidate_review as review
from tools.audit_specification_candidates import unknown_response


def write_rows(path,values):
    with gzip.open(path,'wt',encoding='utf8') as f:
        for value in values: f.write(json.dumps(value,ensure_ascii=False)+'\n')


def payload(packet):
    if packet['generation']['response_format']=='specification_candidates':
        result=unknown_response(packet['specification_inventory'])
        for value in result[review.NAME].values():
            value.update(permission_attribute='unknown',permission_effect='unknown')
        result.update(unresolved='관계 미확인',judgment={'reason':'관계 미확인','v':0,'e':0})
        return result
    schema=output_schema(packet['generation']['response_format'],len(packet['spans']),packet['items'])
    assert set(schema['properties'])=={'facts','judgments'}
    fields=schema['properties']['judgments']['properties']
    judgments=({'v':[0]*len(packet['items']),'e':[0]*len(packet['items'])} if set(fields)=={'v','e'}
               else {key:{'reason':'관계 미확인','v':0,'e':0} for key in fields})
    return {'facts':{key:'미확인' for key in schema['properties']['facts']['properties']},'judgments':judgments}


@pytest.fixture
def prepared_run(tmp_path):
    root=Path(__file__).resolve().parents[1]
    if not (root/'models/gemma-tokenizer').is_dir(): pytest.skip('Fixed tokenizer required')
    rec={'id':'synthetic','meta':{},'docs':[{'doc_id':'N1','type':'공고문',
        'text':'입찰 공고\n사업명: 시험장비 구매\n추정가격: 8,000만원\n품명: 시험장비\n모델명: Atlas R7'}]}
    input_path=tmp_path/'input.jsonl.gz';write_rows(input_path,[rec])
    folder=tmp_path/'prepared'
    prepare(input_path,root/'data_open/data',root/'models/gemma-tokenizer',None,folder,
            policies=('current','factual_lexical'))
    native=list(rows(folder/'native_packets.jsonl.gz'))
    run=tmp_path/'run';run.mkdir();resolved=[];captured=[]
    for p in native:
        text=json.dumps(payload(p),ensure_ascii=False)
        response={'text':text,'finish_reason':'stop','output_tokens':1,
                  'raw_output_sha256':hashlib.sha256(text.encode()).hexdigest()}
        resolved.append({'request_key':p['request_key'],'attempt':0,'response':response})
        captured.append({'request_key':p['request_key'],'attempt':0,'native':{
            'num_outputs':1,'prompt_token_ids':p['token_ids'],'raw_text':text,
            'finish_reason':'stop','output_token_ids':[1]}})
    write_rows(run/'call_00000_0_requests.jsonl.gz',native)
    write_rows(run/'call_00000_0_native.jsonl.gz',captured)
    write_rows(run/'call_00000_0_responses.jsonl.gz',resolved)
    write_rows(run/'resolved_responses.jsonl.gz',resolved)
    (run/'generation_summary.json').write_text(json.dumps({'unresolved_formats':{},'valid_responses':len(native)}))
    return root,folder,run,native,resolved


def test_all_fixed_recipes_get_complete_predictions_from_verified_native(prepared_run,tmp_path):
    root,folder,run,native,resolved=prepared_run
    proof=verify_resolved_native(run,native,resolved)
    assert proof['primary_attempts']==len(native)
    report=consume(folder,run,root/'data_open/data',tmp_path/'cpu')
    assert len(report)==16 and all(r['records']==1 for r in report.values())
    assert sum(r['derived_conditional_execution'] for r in report.values())==4
    assert sum(r['three_call_B3_control'] for r in report.values())==4
    assert report['current+no_L']['logical_calls']==3
    freeze=json.loads((tmp_path/'cpu/prediction_freeze.json').read_text(encoding='utf8'))
    assert freeze['labels_read'] is False and freeze['saved_old_model_responses_used']==0
    altered={**native[0],'record_id':'another notice'}
    assert native_identity(altered)!=native_identity(native[0])


def test_native_damage_prevents_any_scoreable_prediction(prepared_run,tmp_path):
    root,folder,run,native,resolved=prepared_run
    values=list(rows(run/'call_00000_0_native.jsonl.gz'))
    values[0]['native']['raw_text']='changed native answer'
    write_rows(run/'call_00000_0_native.jsonl.gz',values)
    with pytest.raises(ValueError,match='identity differs'):
        consume(folder,run,root/'data_open/data',tmp_path/'blocked')
    assert not list((tmp_path/'blocked').glob('*.csv'))


def test_changed_cpu_requires_explicit_replay_and_records_both_sources(prepared_run,tmp_path,monkeypatch):
    from tools import run_integrated_comparison as runner
    root, folder, run, native, resolved = prepared_run
    original = runner.source_manifest()
    changed = {**original, 'synthetic_cpu_change.py':'changed'}
    monkeypatch.setattr(runner,'source_manifest',lambda: changed)
    with pytest.raises(ValueError,match='runtime has changed'):
        consume(folder,run,root/'data_open/data',tmp_path/'not-fresh')
    consume(folder,run,root/'data_open/data',tmp_path/'replay',replay_current_cpu=True)
    freeze=json.loads((tmp_path/'replay/prediction_freeze.json').read_text(encoding='utf8'))
    assert freeze['current_cpu_replay'] and freeze['saved_old_model_responses_used']==len(native)
    assert freeze['source_sha256']==changed and freeze['prepared_source_sha256']==original
    assert 'saved-response' in freeze['measurement']


def test_final_answer_damage_cannot_hide_behind_a_correct_raw_digest(prepared_run,tmp_path):
    root,folder,run,native,resolved=prepared_run
    recorded=list(rows(run/'call_00000_0_responses.jsonl.gz'))
    recorded[0]['response']['text']='{"facts":{},"judgments":{}}'
    write_rows(run/'call_00000_0_responses.jsonl.gz',recorded)
    with pytest.raises(ValueError,match='identity differs'):
        consume(folder,run,root/'data_open/data',tmp_path/'damaged-final')
    assert not list((tmp_path/'damaged-final').glob('*.csv'))


@pytest.mark.parametrize('raw,closed,expected', [
    ('<|channel>thought\nanalysis<channel|> {"v":0} <turn|>',True,'{"v":0}'),
    ('thought\nanalysis<channel|> {"v":0} <eos><turn|>',True,'{"v":0}'),
    ('<|channel>thought\nunfinished',False,''),
])
def test_native_projection_retains_only_the_recorded_final_channel(raw,closed,expected):
    assert final_text_from_native(raw,{'thinking_close_marker':closed})==expected


def test_invalid_specialist_does_not_hide_complete_base_policies(prepared_run,tmp_path):
    root,folder,run,native,resolved=prepared_run
    failed=next(p['request_key'] for p in native if p['experiment_role']=='S9')
    recorded=list(rows(run/'call_00000_0_responses.jsonl.gz'))
    captured=list(rows(run/'call_00000_0_native.jsonl.gz'))
    for row in recorded:
        if row['request_key']==failed:
            row['response'].update(text='{}',raw_output_sha256=hashlib.sha256(b'{}').hexdigest())
    for row in captured:
        if row['request_key']==failed:row['native']['raw_text']='{}'
    selected=[r for r in resolved if r['request_key']!=failed]
    write_rows(run/'call_00000_0_responses.jsonl.gz',recorded)
    write_rows(run/'call_00000_0_native.jsonl.gz',captured)
    write_rows(run/'resolved_responses.jsonl.gz',selected)
    (run/'generation_summary.json').write_text(json.dumps({'valid_responses':len(selected),
        'unresolved_formats':{failed:'Invalid required candidate response'}}))
    report=consume(folder,run,root/'data_open/data',tmp_path/'partial')
    assert report['current']['status']==report['factual_lexical']['status']=='complete'
    assert report['current+v9']['status']=='incomplete_no_csv'
    assert not (tmp_path/'partial/current+v9.csv').exists()
    assert (tmp_path/'partial/current.csv').exists()
