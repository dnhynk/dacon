"""Prepare and run the canonical integrated runtime, then fixed diagnostic calls.

The primary run uses submission.runtime.execute unchanged in call order. Probe
calls start only after its CSV is frozen and share its single loaded engine.
No labels are read, no valid response is rerolled, and all combinations are
declared before generation. Probe joins are not standalone execution scores.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import gzip
import itertools
import json
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.b4_entry import B4Pipeline,assemble,digest,parse_error
from submission.pps.data import records,write_csv
from submission.pps.generation_contract import prepared_preflight
from submission.pps.specialist_packets import catalog_packet
from submission.runtime import (Journal,NativeRecorder,execute,source_manifest,sha256,
                                format_recovery_packet,require_generation_progress)
from tools.run_integrated_comparison import verify_resolved_native

OPTIONS={'source_policy':'current','legal_policy':'direct_production',
         'specification_review':'gated_candidates','catalog_review':'explicit',
         'catalog_source_policy':'shared','catalog_task_groups':False,
         'software_review':'relations','a10_thinking_budget':0}
NORMAL='thinking0_catalog_explicit_software_relations_v9_gated'


def read(path):return json.loads(Path(path).read_text(encoding='utf8'))


def rows(path):
    with gzip.open(path,'rt',encoding='utf8') as stream:
        return list(map(json.loads,stream))


def verify_prepared(prepared,*,require_current_source=True):
    frozen=read(prepared/'input_freeze.json')
    if require_current_source and source_manifest()!=frozen['source_sha256']:
        raise ValueError('Canonical source differs from the prepared runtime')
    for name,expected in frozen['files'].items():
        if Path(name).name!=name or sha256(prepared/name)!=expected:
            raise ValueError('Frozen runtime input identity differs: '+name)
    return frozen


def recipes(recs,primary,probes):
    by_role={(p['record_id'],p['batch']):p['request_key'] for p in primary}
    extras={(p['record_id'],p['probe_role']):p['request_key'] for p in probes}
    result={}
    for thinking,catalog,software,v9 in itertools.product((0,768),('none','control','explicit'),('none','relations'),('none','gated')):
        name=f'thinking{thinking}_catalog_{catalog}_software_{software}_v9_{v9}'
        selected=[]
        for rec in recs:
            rid=rec['id']
            selected += [by_role[rid,'A1'],extras[rid,'thinking768'] if thinking else by_role[rid,'A10'],
                         by_role[rid,'A19'],by_role[rid,'L19']]
            if catalog=='control' and (rid,'scope_control') in extras:
                selected.append(extras[rid,'scope_control'])
            elif catalog=='explicit' and (rid,'Q10') in by_role:
                selected.append(by_role[rid,'Q10'])
            if software=='relations':selected.append(by_role[rid,'W20'])
            if v9=='gated' and (rid,'S9') in by_role:selected.append(by_role[rid,'S9'])
        result[name]=selected
    # Existing B3 controls use the same three A responses, never item labels.
    family={p['request_key']:p['family'] for p in primary+probes}
    for name in ('thinking0_catalog_none_software_none_v9_none',NORMAL):
        result[name+'_no_L']=[k for k in result[name] if family[k]!='L']
    return result


def prepare(input_path,data_dir,tokenizer_dir,output):
    from transformers import AutoTokenizer
    journal=Journal(output);began=time.monotonic();source=source_manifest()
    recs=list(records(input_path));tokenizer=AutoTokenizer.from_pretrained(tokenizer_dir,local_files_only=True,trust_remote_code=False)
    pipe=B4Pipeline(data_dir,tokenizer,**OPTIONS)
    journal.save('preregistered.json',{'options':OPTIONS,'normal_policy':NORMAL,
        'records':len(recs),'record_ids':[r['id'] for r in recs],'config':dataclasses.asdict(pipe.config),
        'labels_read':False,'quality_rerolls':0,'maximum_format_retries':pipe.config.max_response_retries,
        'execution':'canonical primary runtime, then fixed A10 thinking768 and Q control probes',
        'normal_primary_order_unaffected_by_probes':True,
        'source_budget':'same primary A and L source for optional reviews; no source truncation'})
    primary=pipe.packets(recs);probes=[]
    by_id={r['id']:r for r in recs}
    scope_control=SimpleNamespace(config=dataclasses.replace(pipe.config,catalog_review='control'),
                                 tokenizer=tokenizer,knowledge=pipe.knowledge,_source_encoder=None,
                                 catalog_source_preparation={'eligible_notices':0,'searched_notices':0,
                                     'seconds':0.,'context_budget_reductions':0,'shared_fallbacks':0,
                                     'source_token_cap_total':0,'source_tokens_total':0})
    for p in primary:
        if p['batch']=='A10':
            q=copy.deepcopy(p);q['generation']['thinking_budget']=768
            q.update(request_key='probe:'+q['request_key'],probe_role='thinking768')
            probes.append(q)
        elif p['batch']=='A1':
            q=catalog_packet(scope_control,by_id[p['record_id']],p)
            if q is not None:
                q.update(request_key='probe:'+q['request_key'],probe_role='scope_control')
                probes.append(q)
    probes.sort(key=lambda p:(('thinking768','scope_control').index(p['probe_role']),
                              next(i for i,r in enumerate(recs) if r['id']==p['record_id'])))
    proof=prepared_preflight(primary+probes)
    declared=recipes(recs,primary,probes)
    for name,values in (('current_inputs.jsonl.gz',recs),('primary_packets.jsonl.gz',primary),('probe_packets.jsonl.gz',probes)):
        journal.rows(name,values)
    journal.save('recipes.json',declared)
    journal.save('generation_preflight.json',proof)
    journal.save('preparation_report.json',{'records':len(recs),'primary_prepared':len(primary),'probe_requests':len(probes),
        'primary_profiles':{role:sum(p['batch']==role for p in primary) for role in ('A1','A10','A19','L19','Q10','W20','S9')},
        'maximum_primary_requests':len(primary)+len(probes),'policies':len(declared),
        'max_input_tokens':max(len(p['token_ids']) for p in primary+probes),
        'input_tokens_upper_bound':sum(len(p['token_ids']) for p in primary+probes),
        'seconds':time.monotonic()-began,'labels_read':False,'new_model_calls':0})
    if source_manifest()!=source:raise RuntimeError('Source changed during preparation')
    journal.save('input_freeze.json',{'source_sha256':source,'original_input_sha256':sha256(input_path),
        'records':len(recs),'labels_read':False,'files':{p.name:sha256(p) for p in journal.root.iterdir() if p.is_file()}})
    return read(journal.root/'preparation_report.json')


def run(prepared,data_dir,model_dir,output,wall_seconds=6600):
    from transformers import AutoTokenizer
    from submission.engine import CanonicalRunner,configure_environment
    frozen=verify_prepared(prepared);registration=read(prepared/'preregistered.json')
    root=Journal(output);began=time.monotonic();holder=[];recorder=None
    expected=rows(prepared/'primary_packets.jsonl.gz');probes=rows(prepared/'probe_packets.jsonl.gz')
    configure_environment()
    tokenizer=AutoTokenizer.from_pretrained(model_dir,local_files_only=True,trust_remote_code=False)
    resolved={};errors={};primary_count=retries=0;probe_journal=None
    def factory(config,journal):
        if holder:raise RuntimeError('A second engine load is forbidden')
        if digest(rows(journal.root/'current_packets.jsonl.gz'))!=digest(expected):
            raise ValueError('Normal runtime regenerated different prepared packets')
        if digest(dataclasses.asdict(config))!=digest(registration['config']):
            raise ValueError('Normal runtime configuration differs from preparation')
        if time.monotonic()>=began+wall_seconds-30:raise TimeoutError('Controller budget exhausted before engine loading')
        engine=CanonicalRunner(model_dir,config,journal);holder.append(engine)
        engine.deadline=min(getattr(engine,'deadline',float('inf')),began+wall_seconds-30)
        return engine
    try:
        primary_report=execute(prepared/'current_inputs.jsonl.gz',data_dir,root.root/'normal',
            tokenizer=tokenizer,runner_factory=factory,started_at=began,close_runner=False,**registration['options'])
        root.save('normal_completed.json',{'report':primary_report,
            'prediction_sha256':sha256(root.root/'normal/submission.csv'),
            'diagnostic_probes_not_started':True})
        probe_journal=Journal(root.root/'probes');recorder=NativeRecorder(holder[0],probe_journal)
        def capture(batch,number,attempt):
            nonlocal primary_count,retries
            if time.monotonic()>=holder[0].deadline:raise TimeoutError('Runtime probe budget exhausted')
            responses=recorder.generate(batch,number,attempt)
            if len(responses)!=len(batch):raise RuntimeError('Missing diagnostic native response')
            require_generation_progress(responses)
            for packet,response in zip(batch,responses):
                key=packet['request_key'];error=parse_error(packet,response);errors[key]=error
                if error is None:
                    if key in resolved:raise ValueError('Valid diagnostic response may not be replaced')
                    resolved[key]={'request_key':key,'response':response,'attempt':attempt}
            if attempt:retries+=len(batch)
            else:primary_count+=len(batch)
            probe_journal.progress(phase='fixed_diagnostic_probes',primary_returned=primary_count,
                primary_total=len(probes),valid=len(resolved),format_retries=retries)
        number=0
        if (prepared/'probe_plan.json').is_file():
            plan=read(prepared/'probe_plan.json')['batches']
            by_key={p['request_key']:p for p in probes}
            planned_keys=[key for batch in plan for key in batch['request_keys']]
            if len(planned_keys)!=len(set(planned_keys)) or set(planned_keys)!=set(by_key):
                raise ValueError('Fixed probe plan must consume every declared probe exactly once')
            for planned in plan:
                capture([by_key[key] for key in planned['request_keys']],number,0);number+=1
        else:
            for role in ('thinking768','scope_control'):
                selected=[p for p in probes if p['probe_role']==role]
                for offset in range(0,len(selected),32):
                    capture(selected[offset:offset+32],number,0);number+=1
        for attempt in range(1,registration['maximum_format_retries']+1):
            pending=[p for p in probes if p['request_key'] not in resolved]
            for offset in range(0,len(pending),32):
                capture([format_recovery_packet(p,attempt) for p in pending[offset:offset+32]],number,attempt);number+=1
        probe_journal.rows('resolved_responses.jsonl.gz',list(resolved.values()))
        probe_journal.save('generation_summary.json',{'primary_requests':primary_count,'valid_responses':len(resolved),
            'format_retries':retries,'unresolved_formats':{k:v for k,v in errors.items() if v}})
        if source_manifest()!=frozen['source_sha256']:raise RuntimeError('Source changed during native execution')
        result={'normal_report':primary_report,'diagnostic_primary_requests':primary_count,
            'diagnostic_valid':len(resolved),'diagnostic_format_retries':retries,'engine_loads':len(holder),
            'elapsed_seconds':time.monotonic()-began,'source_unchanged':True,'labels_read':False,'quality_rerolls':0}
        root.save('complete.json',result)
        return result
    except BaseException:
        if probe_journal is not None:
            probe_journal.rows('resolved_responses_partial.jsonl.gz',list(resolved.values()))
        root.save('failure.json',{'traceback':traceback.format_exc(),'primary_probes_returned':primary_count,
                                 'valid_probes':len(resolved),'quality_rerolls':0})
        raise
    finally:
        if recorder is not None:recorder.close()
        if holder:
            holder[0].close()
            root.save('engine_shutdown.json',{'status':'shutdown_returned','engine_loads':len(holder)})


def consume(prepared,run_dir,data_dir,output,*,cpu_replay=False):
    frozen=verify_prepared(prepared,require_current_source=not cpu_replay);source=source_manifest()
    if not cpu_replay and source!=frozen['source_sha256']:
        raise ValueError('Original consumption requires the generation source; use explicit CPU replay')
    journal=Journal(output)
    recs=rows(prepared/'current_inputs.jsonl.gz');by_id={r['id']:r for r in recs}
    primary=rows(prepared/'primary_packets.jsonl.gz');probes=rows(prepared/'probe_packets.jsonl.gz')
    skipped=read(run_dir/'normal/skipped_requests.json') if (run_dir/'normal/skipped_requests.json').is_file() else {}
    lookup={p['request_key']:p for p in primary+probes}
    for key,entry in skipped.items():
        from submission.skips import validate
        if key not in lookup:
            raise ValueError('Skipped request is absent from prepared packets')
        validate(lookup[key], entry)
    normal_responses=rows(run_dir/'normal/resolved_responses.jsonl.gz')
    probe_responses=rows(run_dir/'probes/resolved_responses.jsonl.gz')
    if {r['request_key'] for r in normal_responses}!={p['request_key'] for p in primary}-skipped.keys():
        raise ValueError('Normal execution did not account for all requested and skipped packets')
    # The normal runtime also retains packet/CPU-error metadata in its ledger.
    # Verify the identical native selection projection, keeping unresolved
    # optional format failures separate from selected valid native answers.
    verified_normal=[{k:r[k] for k in ('request_key','attempt','response')} for r in normal_responses
                     if parse_error(lookup[r['request_key']],r['response']) is None]
    journal.save('native_verification.json',{
        'normal':verify_resolved_native(run_dir/'normal',[p for p in primary if p['request_key'] not in skipped],verified_normal),
        'probes':verify_resolved_native(run_dir/'probes',probes,probe_responses)})
    responses={r['request_key']:r for r in normal_responses+probe_responses}
    if len(responses)!=len(normal_responses)+len(probe_responses):raise ValueError('Repeated native request key')
    registration=read(prepared/'preregistered.json')
    normal_policy=registration['normal_policy']
    pipe=B4Pipeline(data_dir,None,**registration['options'])
    consumed={};details=[]
    for packet in primary+probes:
        key=packet['request_key']
        if key in skipped:
            from submission.skips import consume as consume_skip
            row,trace=consume_skip(by_id[packet['record_id']],packet,skipped[key],pipe.knowledge)
        elif key in responses:
            error=parse_error(packet,responses[key]['response'])
            if error:
                if packet not in primary or packet['batch'] not in {'S9','Q10','W20'}:
                    raise ValueError('Invalid required resolved native response: '+error)
                row,trace=None,[{'source':'recorded_optional_format_failure','error':error,
                                'independent_judgment_retained':True}]
            else:
                if cpu_replay:
                    from tools.source_plan_replay import consume_saved
                    row,trace,_=consume_saved(pipe,by_id[packet['record_id']],packet,
                                              responses[key]['response'])
                else:
                    row,trace=pipe.consume(by_id[packet['record_id']],packet,responses[key]['response'])
        else:row,trace=None,[{'unreturned_required_request':key}]
        consumed[key]=row;details.append({'request_key':key,'record_id':packet['record_id'],'row':row,'details':trace})
    declared=read(prepared/'recipes.json');predictions={}
    for name,keys in declared.items():
        # A semantic Q/W abstention is an optional update. An unreturned call
        # cannot be silently treated as an evaluated diagnostic policy.
        missing=[k for k in keys if k not in responses and k not in skipped]
        if missing:predictions[name]={'status':'incomplete_no_csv','missing':missing};continue
        _,values=assemble(recs,[lookup[k] for k in keys],consumed)
        path=journal.root/(name+'.csv')
        write_csv(path,[values[r['id']] for r in recs],recs=recs,require_positive_evidence=pipe.config.require_positive_evidence)
        predictions[name]={'status':'complete','path':path.name,'sha256':sha256(path),'records':len(recs),
            'executed_calls':sum(k not in skipped for k in keys),'standalone_normal_execution':name==normal_policy and not cpu_replay}
    reproduced=predictions[normal_policy]['sha256']==sha256(run_dir/'normal/submission.csv')
    if not cpu_replay and not reproduced:
        raise ValueError('Reconsumption does not reproduce the normal integrated runtime output')
    if source_manifest()!=source:raise RuntimeError('Source changed during CPU consumption')
    journal.rows('consumed.jsonl.gz',details)
    journal.save('prediction_freeze.json',{'source_sha256':source,'prepared_sha256':sha256(prepared/'input_freeze.json'),
        'normal_prediction_sha256':sha256(run_dir/'normal/submission.csv'),'normal_reproduced_exactly':reproduced,
        'recipes_sha256':sha256(prepared/'recipes.json'),'predictions':predictions,'labels_read':False,
        'source_and_cpu_frozen_before_generation':not cpu_replay,'only_normal_policy_is_standalone_execution':not cpu_replay,
        'kind':'saved_response_current_cpu_replay' if cpu_replay else 'original_frozen_normal_consumption',
        'generation_source_sha256':frozen['source_sha256'],'new_model_calls':0})
    return predictions


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='operation',required=True)
    for name in ('prepare','run','consume','replay'):
        p=sub.add_parser(name);p.add_argument('--data-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
        if name=='prepare':p.add_argument('--input',type=Path,required=True);p.add_argument('--tokenizer',type=Path,required=True)
        else:p.add_argument('--prepared',type=Path,required=True)
        if name=='run':p.add_argument('--model-dir',type=Path,required=True);p.add_argument('--wall-seconds',type=int,default=6600)
        if name in {'consume','replay'}:p.add_argument('--run-dir',type=Path,required=True)
    args=parser.parse_args()
    if args.operation=='prepare':result=prepare(args.input,args.data_dir,args.tokenizer,args.output)
    elif args.operation=='run':result=run(args.prepared,args.data_dir,args.model_dir,args.output,args.wall_seconds)
    else:result=consume(args.prepared,args.run_dir,args.data_dir,args.output,cpu_replay=args.operation=='replay')
    print(json.dumps(result,ensure_ascii=False,indent=2))
