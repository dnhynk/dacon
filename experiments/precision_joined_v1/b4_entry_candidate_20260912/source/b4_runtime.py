"""Single-engine execution with durable native output and exact-input parse retries."""
from __future__ import annotations
import gzip
import hashlib
import json
import time
from pathlib import Path
from b4_entry import B4Pipeline,PROFILES,assemble,parse_error
from pps.data import records,write_csv,missing_evidence_items


class Journal:
    def __init__(self,output_dir):
        self.root=Path(output_dir)
        self.root.mkdir(parents=True,exist_ok=True)
        # Never overwrite any prior attempt or continue it blindly.
        if any(self.root.iterdir()):
            raise FileExistsError('Use an empty output directory; existing results are preserved')
        self.save('started.json',{'epoch':time.time(),'duplicate_execution_forbidden':True})

    def save(self,name,obj):
        path=self.root/name
        temp=path.with_suffix(path.suffix+'.partial')
        temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(path)

    def rows(self,name,rows):
        path=self.root/name
        temp=path.with_suffix(path.suffix+'.partial')
        with gzip.open(temp,'wt',encoding='utf-8') as stream:
            for row in rows:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
        temp.replace(path)


class NativeRecorder:
    def __init__(self,runner,journal):
        self.runner=runner;self.journal=journal
        self.original=runner.llm.generate
        self.active=None;self.captured=[]
        runner.llm.generate=self.capture

    def capture(self,*args,**kwargs):
        output=self.original(*args,**kwargs)
        self.captured=[]
        returned=time.time()
        for packet,row in zip(self.active['packets'],output):
            if not row.outputs:continue
            generated=row.outputs[0]
            self.captured.append({'request_key':packet['request_key'],'attempt':self.active['attempt'],
                'prompt_sha256':packet['prompt_sha256'],'token_ids_sha256':packet['token_ids_sha256'],
                'started_epoch':self.active['epoch'],'returned_epoch':returned,
                'native':{'request_id':row.request_id,'raw_text':generated.text,
                    'output_token_ids':list(generated.token_ids),'finish_reason':generated.finish_reason,
                    'cached_input_tokens':getattr(row,'num_cached_tokens',None)}})
        # Store every returned native object before the fixed runner parses it.
        self.journal.rows(self.active['name']+'_native.jsonl.gz',self.captured)
        if len(self.captured)!=len(output) or len(output)!=len(self.active['packets']):
            raise RuntimeError('Incomplete native results preserved; no automatic resubmission')
        return output

    def generate(self,packets,number,attempt):
        name=f'call_{number:05d}_{attempt}'
        self.journal.rows(name+'_requests.jsonl.gz',packets)
        self.active={'packets':packets,'attempt':attempt,'epoch':time.time(),'name':name}
        self.captured=[]
        response=self.runner.generate(packets,max_tokens=2048)
        assert len(response)==len(self.captured)==len(packets)
        for r,n in zip(response,self.captured):
            assert r['raw_output_sha256']==hashlib.sha256(n['native']['raw_text'].encode()).hexdigest()
        self.journal.rows(name+'_responses.jsonl.gz',[{'request_key':p['request_key'],'attempt':attempt,'response':r} for p,r in zip(packets,response)])
        self.journal.save(name+'.json',{'count':len(packets),'attempt':attempt,'batch':number,
            'seconds':time.time()-self.active['epoch'],'input_tokens':sum(len(p['token_ids']) for p in packets),
            'output_tokens':sum(r['output_tokens'] for r in response)})
        return response

    def close(self):
        self.runner.llm.generate=self.original


def execute(input_path,data_dir,output_dir,runner,*,limit=None,generation=None):
    """generation is a label-free CPU test injection; normal CLI uses NativeRecorder."""
    journal=Journal(output_dir)
    recs=list(records(input_path,limit))
    pipeline=B4Pipeline(data_dir,runner.tokenizer)
    if runner.config!=pipeline.config:
        raise ValueError('The frozen consumer/engine configuration must be retained')
    start=time.monotonic()
    packets=pipeline.packets(recs)
    by_id={r['id']:r for r in recs}
    journal.rows('current_inputs.jsonl.gz',recs)
    journal.rows('current_packets.jsonl.gz',packets)
    recorder=None
    if generation is None:
        recorder=NativeRecorder(runner,journal);generation=recorder.generate
    first_rows={};final_rows={};first_calls=[];final_calls=[]
    submitted=primary=retried=0;number=0
    try:
        for profile in PROFILES:
            group=[p for p in packets if p['batch']==profile]
            for offset in range(0,len(group),pipeline.config.batch_size):
                batch=group[offset:offset+pipeline.config.batch_size]
                # Inference exceptions preserve earlier data; they never trigger a quality retry.
                submitted+=len(batch);primary+=len(batch)
                first=generation(batch,number,0)
                if len(first)!=len(batch):raise RuntimeError('Missing primary responses')
                journal.rows(f'first_part_{number:05d}.jsonl.gz',[{'request_key':p['request_key'],'response':r} for p,r in zip(batch,first)])
                errors=[parse_error(p,r) for p,r in zip(batch,first)]
                indices=[i for i,e in enumerate(errors) if e]
                final=list(first)
                if indices:
                    retry_packets=[batch[i] for i in indices]
                    submitted+=len(indices);retried+=len(indices)
                    retry=generation(retry_packets,number,1)
                    if len(retry)!=len(indices):raise RuntimeError('Missing parse retry responses')
                    for i,r in zip(indices,retry):final[i]=r
                entries=[]
                for p,r1,r2,error1 in zip(batch,first,final,errors):
                    details2=None;error2=None
                    for dest,resp in [(first_rows,r1),(final_rows,r2)]:
                        err=parse_error(p,resp);row=details=None
                        if err is None:
                            try:row,details=pipeline.consume(by_id[p['record_id']],p,resp)
                            except (ValueError,TypeError,KeyError) as exc:err='CPU: '+type(exc).__name__+': '+str(exc)
                        dest[p['request_key']]=row
                        if dest is final_rows:details2,error2=details,err
                    entries.append({'request_key':p['request_key'],'first_parse_error':error1,
                        'retries':int(error1 is not None),'error':error2,'row':final_rows[p['request_key']],
                        'rule_details':details2,'final_response':r2})
                journal.rows(f'final_part_{number:05d}.jsonl.gz',entries)
                journal.save('progress.json',{'profile':profile,'primary_saved':primary,'submitted':submitted,'parse_retries':retried})
                number+=1
        first_b3,first_b4=assemble(recs,packets,first_rows)
        final_b3,final_b4=assemble(recs,packets,final_rows)
        variants={'first_B3':first_b3,'first_B4':first_b4,'final_B3':final_b3,'final_B4':final_b4}
        missing={name:sum(row[f'v{k}'] is None for row in rows.values() for k in range(1,25)) for name,rows in variants.items()}
        for name,rows in variants.items():
            journal.rows(name+'.jsonl.gz',list(rows.values()))
        report={'records':len(recs),'primary_requests':primary,'requests_with_retries':submitted,
            'parse_retries':retried,'new_model_calls':submitted if recorder is not None else 0,
            'mode':'fixed_model' if recorder is not None else 'injected_cpu_validation',
            'single_engine':True,'missing_bits':missing,'official_score':None,
            'seconds':time.monotonic()-start,'l40s_time_verified':False,
            'missing_required_evidence':sum(len(missing_evidence_items(row)) for row in final_b4.values())}
        journal.save('run_report.json',report)
        if missing['final_B4']:
            raise ValueError('Unresolved required predictions; submission CSV withheld instead of filling zero')
        write_csv(journal.root/'submission.csv',list(final_b4.values()),recs=recs,
            require_positive_evidence=pipeline.config.require_positive_evidence)
        if not missing['final_B3']:
            write_csv(journal.root/'B3.csv',list(final_b3.values()),recs=recs,
                require_positive_evidence=pipeline.config.require_positive_evidence)
        return report
    except BaseException as exc:
        journal.save('failure.json',{'type':type(exc).__name__,'message':str(exc),'submitted':submitted,
            'primary':primary,'parse_retries':retried,'no_missing_zero_fill':True})
        raise
    finally:
        if recorder is not None:recorder.close()
