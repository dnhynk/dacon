"""Freeze a fresh three-arm purchase retrieval contrast; labels stay unopened."""
import argparse
import dataclasses
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.b4_entry import digest
from submission.pps.generation_contract import generation_schema,validate_grammar
from submission.pps.knowledge import Knowledge
from submission.pps.prompts import Config,build_prompt,output_schema
from submission.runtime import source_manifest
from tools.audit_purchase_feedback import save,sha
from tools.build_submission import build
from tools.prepare_retrieval_contrast import read_rows,write_rows


def main():
    p=argparse.ArgumentParser()
    for key in ('input','review','output'):
        p.add_argument('--'+key,type=Path,required=True)
    for key in ('baseline','feedback','frontier','routes'):
        p.add_argument('--'+key,type=Path)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    code=source_manifest();snapshot=build(args.output/'source_snapshot.zip')
    cases=json.loads(args.review.read_text(encoding='utf8'))['cases']
    inputs={r['id']:r for r in read_rows(args.input)}
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer',local_files_only=True,trust_remote_code=False)
    knowledge=Knowledge(ROOT/'data_open/data')
    config=dataclasses.replace(Config.load(ROOT/'submission/model/config.json'),shared_prefix=False,
        sme_facts=False,product_facts=False,cross_source_facts=False,response_format='factored',
        thinking_items=(),thinking_token_budget=0)
    if args.routes is not None:
        if any(p is not None for p in (args.baseline,args.feedback,args.frontier)):
            raise ValueError('Use the default comparison paths or a declared route file')
        declared=json.loads(args.routes.read_text(encoding='utf8'))
        routes=[(r['name'],Path(r['directory']),r['arm']) for r in declared['routes']]
        assert len(routes)==3 and len({r[0] for r in routes})==3
        import re
        assert all(re.fullmatch(r'[A-Za-z0-9_]+',r[0]) for r in routes)
    else:
        assert all(p is not None for p in (args.baseline,args.feedback,args.frontier))
        declared={}
        routes=[('A_current',args.baseline,'current/local/rrf'),
                ('B_seed_lexical',args.feedback,'seed/no_catalog/lexical'),
                ('C_catalog_feedback',args.frontier,'context_blocks/rank_frontier/lexical')]
    items=(9,10,11,18);arms=[r[0] for r in routes];found={};provenance={}
    for name,path,route in routes:
        protocol=json.loads((path/'preregistered.json').read_text(encoding='utf8'))
        assert protocol['input_sha256']==sha(args.input) and protocol['review_sha256']==sha(args.review)
        for result in json.loads((path/'results.json').read_text(encoding='utf8')):
            if result['arm']==route and result['source_token_budget']==4096:
                key=result['record_id'],name
                assert key not in found;found[key]=result
        provenance[name]={'results_path':str(path/'results.json'),'results_sha256':sha(path/'results.json'),
                          'protocol_sha256':sha(path/'preregistered.json'),'retrieval_arm':route}
    save(args.output/'preregistered.json',{'source_sha256':code,'source_fingerprint':snapshot['source_fingerprint'],
        'tool_sha256':sha(__file__),'input_sha256':sha(args.input),'review_sha256':sha(args.review),
        'selection':'All twelve previously source-selected mixed/unknown goods notices, unchanged.',
        'arms':arms,'routes':provenance,'items':items,'source_token_budget':4096,
        'classification_labels_read':False,'holdout_read':False,'gpu_used':False,
        'hypothesis':declared.get('hypothesis','Source bundles gained by purchase seeds and catalog-driven BGE notice search improve actual model consumption.'),
        'catalog_selection_scores_are_not_model_facts':True,'adaptive_search_questions_not_shown_as_source':True,
        'original_source_budget_includes_all_intermediate_reading':True,
        'static_knowledge_and_full_source_code_name_diagnostics_fixed_across_arms':True,
        'candidate_selection':declared.get('candidate_selection','Equal full-bundle coverage with fewer catalog candidates and complete conditions; not selected by F1.'),
        **({'declared_routes_sha256':sha(args.routes)} if args.routes else {})})
    packets=[];schemas=set();ranked=[]
    for ci,case in enumerate(cases):
        rid=case['id'];rec=inputs[rid]
        assert [hashlib.sha256(d['text'].encode()).hexdigest() for d in rec['docs']]==case['doc_sha256']
        ranked.append((hashlib.sha256(('purchase-feedback-repeat-v1:'+digest([d['text'] for d in rec['docs']])).encode()).hexdigest(),rid))
        systems=[]
        for arm in arms[ci%len(arms):]+arms[:ci%len(arms)]:
            selection=found[rid,arm]
            prompt=build_prompt(rec,knowledge,config,tokenizer,items,source_selection=selection)
            spans=[dataclasses.asdict(s) for s in prompt['spans']];systems.append(prompt['messages'][0])
            schema=generation_schema('factored',len(spans),items)
            if digest(schema) not in schemas:validate_grammar(schema);schemas.add(digest(schema))
            packets.append({'request_key':f'purchase:{rid}:{arm}','record_id':rid,'case':rid,'arm':arm,'family':'A',
                'items':list(items),'messages':prompt['messages'],'token_ids':prompt['token_ids'],
                'prompt_sha256':digest(prompt['messages']),'token_ids_sha256':digest(prompt['token_ids']),
                'source_sha256':digest(spans),'spans':spans,'source_search':selection,
                'comparison_facts':None,'coverage':prompt['coverage'],
                'generation':{'response_format':'factored','thinking_budget':0,'max_output_tokens':2048},
                'generation_schema_sha256':digest(schema),'schema_sha256':digest(output_schema('factored',len(spans),items))})
        assert all(s==systems[0] for s in systems)
    repeats=[];repeat_ids={rid for _,rid in sorted(ranked)[:2]}
    for packet in list(packets):
        if packet['record_id'] in repeat_ids:
            repeat={**packet,'request_key':packet['request_key']+':repeat','arm':packet['arm']+'_repeat'}
            packets.append(repeat);repeats.append({'primary':packet['request_key'],'repeat':repeat['request_key']})
    selected=[inputs[c['id']] for c in cases]
    write_rows(args.output/'contrast_inputs.jsonl.gz',selected)
    write_rows(args.output/'contrast_packets.jsonl.gz',packets)
    assert source_manifest()==code
    freeze={'source_sha256':code,'config':dataclasses.asdict(config),'packets_sha256':sha(args.output/'contrast_packets.jsonl.gz'),
        'inputs_sha256':sha(args.output/'contrast_inputs.jsonl.gz'),'primary_requests':len(packets),
        'unique_requests':len(cases)*len(arms),'repeated_pairs':repeats,'arms':arms,'cases':len(cases),
        'included_cases':[c['id'] for c in cases],'notices':len(selected),'items':items,
        'call_plan':[{'number':n//18,'request_keys':[p['request_key'] for p in packets[n:n+18]]} for n in range(0,len(packets),18)],
        'input_tokens_total':sum(len(p['token_ids']) for p in packets),'input_tokens_max':max(len(p['token_ids']) for p in packets),
        'source_token_budget':4096,'allow_adaptive_queries':True,'classification_labels_read':False,'holdout_read':False,
        'controls':'Same model, generation, rubric, item set, metadata, static knowledge hints, canonical consumer and original token cap.',
        'candidate_catalog_not_directly_in_model_prompt':True,'full_fresh_inference':False,'official_score':None,
        'measurement':'Fresh paired 48 notice/item judgments per arm; optional mixed joins to both preserved response populations.',
        'repeats':'Two source-hash-selected notices, every arm; report variation without choosing the better response.',
        'known_limit':'Fixed whole-source code/name diagnostics and CPU consumer scan all supplied source in every arm; source reading alone does not certify absence.'}
    save(args.output/'contrast_freeze.json',freeze)
    print(json.dumps({k:freeze[k] for k in ('notices','unique_requests','primary_requests','input_tokens_total','input_tokens_max')},ensure_ascii=False))


if __name__=='__main__':main()
