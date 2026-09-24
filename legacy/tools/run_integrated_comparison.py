"""Generate frozen canonical alternatives once; consume and score later on CPU."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.b4_entry import B4Pipeline, assemble, digest, parse_error, restored
from submission.pps.data import write_csv
from submission.pps.generation_contract import generation_schema, prepared_preflight
from submission.pps.prompts import token_ids, verified_search_spans
from submission.runtime import Journal, NativeRecorder, format_recovery_packet, require_generation_progress, source_manifest


def rows(path):
    with gzip.open(path,'rt',encoding='utf8') as f:
        for line in f: yield json.loads(line)


def read(path): return json.loads(path.read_text(encoding='utf8'))


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def final_text_from_native(raw_text, response):
    """Reproduce the frozen final-channel projection, never choose another answer.

    The sentinel cleanup follows vLLM v0.26.0 gemma4_utils._clean_answer.
    Thinking-enabled canonical generation with no closing tag emits no final
    answer. The projection is checked in addition to the raw-text digest.
    """
    if 'thinking_close_marker' not in response:
        return raw_text
    closed = '<channel|>' in raw_text
    if response['thinking_close_marker'] is not closed:
        raise ValueError('Recorded thinking boundary differs from native output')
    if not closed:
        return ''
    answer = raw_text.split('<channel|>', 1)[1].strip()
    for sentinel in ('<turn|>', '<eos>'):
        if answer.endswith(sentinel):
            answer = answer[:-len(sentinel)].rstrip()
    return answer


def load(prepared, *, require_same_source=True):
    prepared=Path(prepared); freeze=read(prepared/'input_freeze.json')
    if require_same_source and source_manifest()!=freeze['source_sha256']:
        raise ValueError('Prepared canonical runtime has changed')
    for name,expected in freeze['files'].items():
        if Path(name).name!=name or sha(prepared/name)!=expected:
            raise ValueError('Prepared artifact identity differs: '+name)
    records=list(rows(prepared/'current_inputs.jsonl.gz'))
    native=list(rows(prepared/'native_packets.jsonl.gz'))
    plan=read(prepared/'call_plan.json')['batches']
    keys=[p['request_key'] for p in native]
    planned=[key for b in plan for key in b['request_keys']]
    if (len(records)!=freeze['records'] or len(native)!=freeze['unique_model_calls']
            or len(keys)!=len(set(keys)) or sorted(keys)!=sorted(planned)):
        raise ValueError('Prepared input or call plan is incomplete')
    return freeze,records,native,plan


def verify(prepared,data_dir,tokenizer):
    freeze,recs,packets,plan=load(prepared)
    pipe=B4Pipeline(data_dir,tokenizer); by_id={r['id']:r for r in recs}
    for p in packets:
        rec=by_id[p['record_id']]
        if p['token_ids']!=token_ids(tokenizer,p['messages'],pipe.config.enable_thinking):
            raise ValueError('Actual tokenizer differs from frozen input')
        if digest(p['messages'])!=p['prompt_sha256'] or digest(p['token_ids'])!=p['token_ids_sha256']:
            raise ValueError('Prepared prompt or tokens were modified')
        if p.get('source_search') is not None:
            selected=verified_search_spans(rec,p['source_search'],tokenizer)
            if p.get('source_layout')=='finite_units':
                from submission.pps.source_units import unitize
                selected=unitize(selected)
            if selected!=restored(p)['spans']:
                raise ValueError('Prepared source units differ from source selection')
        for s in p['spans']:
            doc=rec['docs'][s['doc_index']]
            if s['text']!=doc['text'][s['start']:s['end']] or s['doc_type']!=doc['type']:
                raise ValueError('Prepared source is not the original document range')
        if p['generation']['response_format']=='specification_candidates':
            from submission.pps.specification_candidate_review import validate_prepared
            validate_prepared(rec,restored(p))
        if p.get('legal_control') is not None:
            from submission.pps.legal_query_contract import verify_prepared
            verify_prepared(p,rec,pipe.knowledge,tokenizer)
        effective=generation_schema(p['generation']['response_format'],len(p['spans']),p['items'],
            specification_inventory=p['generation'].get('specification_inventory'))
        if digest(effective)!=p['generation_schema_sha256']:
            raise ValueError('Effective schema differs from frozen generation')
    proof=prepared_preflight(packets)
    return freeze,recs,packets,plan,pipe,proof


def verify_resolved_native(run,native,resolved):
    """Check first valid selection against every recorded native attempt."""
    index={p['request_key']:p for p in native}; observed={}; raw={}; requests={}
    for path in sorted(run.glob('call_*_requests.jsonl.gz')):
        number,attempt=map(int,path.name.split('_')[1:3])
        for packet in rows(path):
            key=packet['request_key']; pair=(key,attempt)
            if key not in index or pair in requests:
                raise ValueError('Unexpected or duplicated recorded request')
            expected=index[key] if attempt==0 else format_recovery_packet(index[key],attempt)
            if packet!=expected: raise ValueError('Recorded request differs from frozen primary or bounded recovery')
            requests[pair]=packet
    for path in sorted(run.glob('call_*_native.jsonl.gz')):
        for record in rows(path):
            pair=(record['request_key'],record['attempt'])
            if pair in raw or pair not in requests: raise ValueError('Unexpected native attempt')
            value=record['native']
            if value['num_outputs']!=1 or value['prompt_token_ids']!=requests[pair]['token_ids']:
                raise ValueError('Native source tokens or response count differ')
            raw[pair]=value
    for path in sorted(run.glob('call_*_responses.jsonl.gz')):
        for record in rows(path):
            pair=(record['request_key'],record['attempt']); response=record['response']
            if pair in observed or pair not in raw: raise ValueError('Unexpected recorded parsed response')
            value=raw[pair]
            if (response['raw_output_sha256']!=hashlib.sha256(value['raw_text'].encode()).hexdigest()
                    or response['text']!=final_text_from_native(value['raw_text'],response)
                    or response['finish_reason']!=value['finish_reason']
                    or response['output_tokens']!=len(value['output_token_ids'])):
                raise ValueError('Parsed/native response identity differs')
            observed[pair]=record
    # An interrupted later diagnostic must not invalidate an earlier complete
    # policy. Keep unreturned/unparsed attempts explicit; never synthesize them.
    for chosen in resolved:
        key,attempt=chosen['request_key'],chosen['attempt']
        if observed.get((key,attempt))!=chosen: raise ValueError('Selected response differs from recorded attempt')
        if any((key,a) not in observed for a in range(attempt)):
            raise ValueError('Cannot skip an unobserved earlier attempt')
        valid=[a for (k,a),record in observed.items() if k==key and parse_error(requests[k,a],record['response']) is None]
        if not valid or attempt!=min(valid): raise ValueError('Selected answer is not the first valid response')
    return {'planned_primary_attempts':len(index),'primary_attempts':sum(a==0 for k,a in raw),
            'all_parsed_attempts':len(observed),'unreturned_requests':sorted(set(requests)-set(raw)),
            'unparsed_native_attempts':sorted(set(raw)-set(observed)),
            'unrequested_primary_keys':sorted(set(index)-{k for k,a in requests if a==0}),
            'first_valid_selection_verified':True,'all_recorded_native_tokens_and_parsed_response_hashes_verified':True}


def infer(prepared,data_dir,model_dir,output,*,maximum_format_retries=1,wall_seconds=7200):
    from transformers import AutoTokenizer
    from submission.engine import CanonicalRunner, configure_environment
    if type(maximum_format_retries) is not int or not 0<=maximum_format_retries<=2:
        raise ValueError('Invalid bounded format recovery policy')
    journal=Journal(output); began=time.monotonic(); runner=recorder=None
    configure_environment()
    tokenizer=AutoTokenizer.from_pretrained(model_dir,local_files_only=True,trust_remote_code=False)
    freeze,recs,packets,plan,pipe,proof=verify(prepared,data_dir,tokenizer)
    journal.save('input_freeze.json',freeze)
    journal.save('generation_preflight.json',{'effective_schemas':proof,'labels_read':False})
    journal.save('policy.json',{'maximum_format_retries':maximum_format_retries,'quality_rerolls':0,
        'wall_seconds':wall_seconds,'cpu_consumption':'after GPU release in a separate local process',
        'prepared_path':str(prepared),'before_engine_seconds':time.monotonic()-began})
    resolved={}; errors={}; primary=0; retries=0
    try:
        runner=CanonicalRunner(model_dir,pipe.config,journal)
        runner.deadline=min(getattr(runner,'deadline',float('inf')),began+wall_seconds-30)
        recorder=NativeRecorder(runner,journal)
        index={p['request_key']:p for p in packets}
        def capture(batch,number,attempt):
            nonlocal primary,retries
            if time.monotonic()>=runner.deadline:
                raise TimeoutError('Fixed comparison deadline reached; preserve completed native results')
            responses=recorder.generate(batch,number,attempt)
            if len(responses)!=len(batch): raise RuntimeError('Missing native response')
            require_generation_progress(responses)
            for packet,response in zip(batch,responses):
                key=packet['request_key']; error=parse_error(packet,response)
                errors[key]=error
                if error is None:
                    if key in resolved: raise ValueError('A valid response may not be replaced')
                    resolved[key]={'request_key':key,'attempt':attempt,'response':response}
            if attempt: retries+=len(batch)
            else: primary+=len(batch)
            journal.save('response_errors.json',errors)
            journal.progress(phase='integrated_generation',batch=number,attempt=attempt,
                primary_returned=primary,primary_total=len(packets),format_retries=retries,
                valid=len(resolved),elapsed_seconds=round(time.monotonic()-began,3))
        for planned in plan:
            capture([index[k] for k in planned['request_keys']],planned['number'],0)
        number=len(plan)
        for attempt in range(1,maximum_format_retries+1):
            pending=[p for p in packets if p['request_key'] not in resolved]
            if not pending: break
            for offset in range(0,len(pending),32):
                capture([format_recovery_packet(p,attempt) for p in pending[offset:offset+32]],number,attempt)
                number+=1
        journal.rows('resolved_responses.jsonl.gz',list(resolved.values()))
        summary={'primary_requests':primary,'format_retries':retries,'valid_responses':len(resolved),
            'unresolved_formats':{k:v for k,v in errors.items() if v is not None},
            'elapsed_seconds':time.monotonic()-began,'engine_load_seconds':runner.load_seconds,
            'source_unchanged':source_manifest()==freeze['source_sha256'],'quality_rerolls':0,
            'labels_read':False,'whole_fresh_predictions_assembled':False,'official_score':None}
        journal.save('generation_summary.json',summary)
        if not summary['source_unchanged']: raise ValueError('Runtime changed during inference')
        return summary
    except BaseException:
        journal.rows('resolved_responses_partial.jsonl.gz',list(resolved.values()))
        journal.save('failure.json',{'traceback':traceback.format_exc(),'primary_returned':primary,
            'valid':len(resolved),'format_retries':retries})
        raise
    finally:
        if recorder is not None: recorder.close()
        if runner is not None: runner.close()


def consume(prepared,run,data_dir,output, *, replay_current_cpu=False):
    """All frozen combinations, never a per-notice selection based on a label."""
    prepared,run=Path(prepared),Path(run)
    freeze,recs,native,plan=load(prepared, require_same_source=not replay_current_cpu)
    consumer_source=source_manifest()
    journal=Journal(output); began=time.monotonic()
    complete_run=(run/'generation_summary.json').is_file()
    summary=read(run/('generation_summary.json' if complete_run else 'failure.json'))
    response_path=run/('resolved_responses.jsonl.gz' if complete_run else 'resolved_responses_partial.jsonl.gz')
    resolved=list(rows(response_path))
    responses={r['request_key']:r for r in resolved}
    if (len(responses)!=len(resolved) or not set(responses)<={p['request_key'] for p in native}
            or len(responses)!=summary.get('valid_responses',summary.get('valid'))):
        raise ValueError('Resolved response population differs from frozen native requests')
    journal.save('native_verification.json',verify_resolved_native(run,native,resolved))
    mapping=read(prepared/'recipes.json'); aliases=mapping['native_aliases']
    by_id={r['id']:r for r in recs}; native_by_key={p['request_key']:p for p in native}
    from tools.prepare_integrated_comparison import native_identity
    pipe=B4Pipeline(data_dir,None); packets={}; consumed={}; details=[]
    for packet in rows(prepared/'canonical_packets.jsonl.gz'):
        key=packet['request_key']; native_key=aliases[key]
        if native_identity(packet)!=native_identity(native_by_key[native_key]):
            raise ValueError('Alias crosses notices or differs from the actual frozen model input')
        if native_key in responses:
            response=responses[native_key]['response']
            error=parse_error(packet,response)
            if error: raise ValueError('Invalid aliased response: '+error)
            row,trace=pipe.consume(by_id[packet['record_id']],packet,response)
        else:
            row=None
            trace=[{'unresolved_native_key':native_key,
                'reason':summary.get('unresolved_formats',{}).get(native_key,'not_returned_or_not_resolved'),
                'no_prediction_inferred':True}]
        packets[key]=packet; consumed[key]=row
        details.append({'request_key':key,'native_request_key':native_key,'record_id':packet['record_id'],
            'row':row,'details':trace,'attempt':responses[native_key]['attempt'] if native_key in responses else None})
    if set(packets)!=set(aliases) or len(packets)!=freeze['logical_calls']:
        raise ValueError('Incomplete logical comparison packet population')
    recipes=dict(mapping['recipes']); gating=[]
    policies=read(prepared/'preregistered.json')['source_policies']
    role_keys={(p['record_id'],p['experiment_policy'],p['experiment_role']):key for key,p in packets.items()}
    for policy in policies:
        # B3 is an existing canonical output. Audit the contribution of the
        # fourth L call uniformly, without any record/label-dependent choice.
        for law in ('','+law'):
            recipes[policy+law+'+no_L']=[key for key in mapping['recipes'][policy+law]
                                       if packets[key]['family']!='L']
        extra=[]
        for rec in recs:
            a=role_keys[rec['id'],policy,'A1'];s=role_keys.get((rec['id'],policy,'S9'))
            shared_v9=consumed[a]['v9'] if consumed[a] is not None else None
            needed=s is not None and (shared_v9==1 or bool(packets[s]['specification_inventory']['candidates']))
            if needed:extra.append(s)
            gating.append({'record_id':rec['id'],'policy':policy,'shared_v9':shared_v9,
                'candidates':len(packets[s]['specification_inventory']['candidates']) if s else None,
                'specialist_called_in_derived_policy':needed})
        for law in ('','+law'):
            recipes[policy+law+'+gated_v9']=mapping['recipes'][policy+law]+extra
    journal.save('derived_review_gating.json',{'rule':'shared CPU v9 positive OR nonempty original-source candidate inventory',
        'predeclared_before_fresh_inference':True,'rows':gating,'actual_adaptive_deployment_executed':False})
    journal.save('applied_recipes.json',recipes)
    predictions={}
    for name,keys in recipes.items():
        if len(keys)!=len(set(keys)): raise ValueError('Duplicate logical call in a recipe')
        missing=[key for key in keys if consumed[key] is None]
        if missing:
            predictions[name]={'status':'incomplete_no_csv','records_expected':len(recs),
                'unresolved_logical_keys':missing,'logical_calls':len(keys),
                'derived_conditional_execution':name.endswith('+gated_v9'),
                'three_call_B3_control':name.endswith('+no_L')}
            continue
        selected=[packets[key] for key in keys]
        _,b4=assemble(recs,selected,consumed)
        path=journal.root/(name+'.csv')
        write_csv(path,[b4[r['id']] for r in recs],recs=recs,
                  require_positive_evidence=pipe.config.require_positive_evidence)
        predictions[name]={'status':'complete','path':path.name,'sha256':sha(path),'records':len(recs),
            'logical_calls':len(keys),'unique_native_calls':len({aliases[k] for k in keys}),
            'derived_conditional_execution':name.endswith('+gated_v9'),
            'three_call_B3_control':name.endswith('+no_L')}
    journal.rows('consumed.jsonl.gz',details)
    if source_manifest()!=consumer_source:
        raise RuntimeError('CPU source changed during consumption; no valid freeze')
    journal.save('prediction_freeze.json',{'source_sha256':consumer_source,
        'prepared_source_sha256':freeze['source_sha256'],
        'current_cpu_replay':replay_current_cpu,
        'prepared_freeze_sha256':sha(prepared/'input_freeze.json'),
        'resolved_responses_sha256':sha(response_path),
        'applied_recipes_sha256':sha(journal.root/'applied_recipes.json'),
        'predictions':predictions,'labels_read':False,
        'saved_old_model_responses_used':len(responses) if replay_current_cpu else 0,
        'all_declared_combinations_complete':all(p['status']=='complete' for p in predictions.values()),
        'measurement':('saved-response current-CPU replay; original frozen prompts and native responses'
            if replay_current_cpu else 'whole development cohort, fresh prepared-input inference; fixed same-notice exact-input aliases'),
        'deployment_call_order_verified':False,'cpu_seconds':time.monotonic()-began})
    return predictions


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='operation',required=True)
    for name in ('infer','consume','replay','verify'):
        p=sub.add_parser(name); p.add_argument('--prepared',type=Path,required=True)
        p.add_argument('--data-dir',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
        if name in ('consume','replay'): p.add_argument('--run',type=Path,required=True)
        else: p.add_argument('--model-dir',type=Path,required=True)
        if name=='infer': p.add_argument('--wall-seconds',type=int,default=7200)
    args=parser.parse_args()
    if args.operation=='infer': result=infer(args.prepared,args.data_dir,args.model_dir,args.output,wall_seconds=args.wall_seconds)
    elif args.operation in ('consume','replay'):
        result=consume(args.prepared,args.run,args.data_dir,args.output,replay_current_cpu=args.operation=='replay')
    else:
        from transformers import AutoTokenizer
        tokenizer=AutoTokenizer.from_pretrained(args.model_dir,local_files_only=True,trust_remote_code=False)
        result={'effective_schemas':verify(args.prepared,args.data_dir,tokenizer)[-1],'labels_read':False}
        with args.output.open('x',encoding='utf8') as stream: json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False,indent=2))
