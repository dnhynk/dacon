"""Fixed-budget BGE-M3 token-vector comparison on exposed review source ranges.

No labels, saved model answers, query tuning or GPU use. All candidates expand
through the same source-context policy, and every retained token is charged.
"""
import argparse
from collections import defaultdict
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.pps.data import records
from submission.pps.embeddings import BGEDenseEncoder,BGE_REVISION,COLBERT_HEAD_SHA256,colbert_similarity
from submission.pps.notice_search import NoticeSearch,factual_queries
from submission.runtime import source_manifest


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path,value):
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)


def read(path):
    with gzip.open(path,'rt',encoding='utf-8') as stream:return list(map(json.loads,stream))


def reference_check(encoder):
    """Independent dense/linear projection, mask, L2, and torch einsum checks."""
    import numpy as np
    torch=encoder.torch
    texts=['입찰 전 제출','상용 소스는 제출하지 않는다. 예외 조항을 함께 확인한다.']
    actual=encoder.encode_features(texts)
    batch=encoder.tokenizer(texts,padding=True,return_tensors='pt')
    with torch.inference_mode():
        hidden=encoder.model(**batch).last_hidden_state
        dense=torch.nn.functional.normalize(hidden[:,0].float(),p=2,dim=-1)
        projected=torch.nn.functional.linear(hidden[:,1:].float(),encoder.colbert_linear.weight,encoder.colbert_linear.bias)
        projected=projected*batch['attention_mask'][:,1:,None].float()
        projected=torch.nn.functional.normalize(projected,p=2,dim=-1)
        expected=[row[:int(mask.sum())-1].numpy() for row,mask in zip(projected,batch['attention_mask'])]
        q,p=map(torch.from_numpy,expected)
        score=float(torch.einsum('in,jn->ij',q,p).max(-1).values.mean())
    assert np.allclose(actual['dense'],dense.numpy(),atol=1e-5,rtol=1e-5)
    assert all(a.shape==b.shape and np.allclose(a,b,atol=1e-5,rtol=1e-5) for a,b in zip(actual['colbert'],expected))
    actual_score=colbert_similarity(*actual['colbert'])
    assert abs(actual_score-score)<1e-5
    return {'passed':True,'shapes':[list(v.shape) for v in actual['colbert']],
        'special_tokens':'CLS removed, EOS retained, padding removed by attention mask',
        'max_vector_delta':max(float(np.max(np.abs(a-b))) for a,b in zip(actual['colbert'],expected)),
        'torch_einsum_score':score,'numpy_score':actual_score,
        'reference_url':'https://github.com/FlagOpen/FlagEmbedding/blob/master/FlagEmbedding/inference/embedder/encoder_only/m3.py',
        'projection_reference_url':'https://github.com/FlagOpen/FlagEmbedding/blob/master/FlagEmbedding/finetune/embedder/encoder_only/m3/modeling.py'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--context-control',type=Path,required=True)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    prior=json.loads((args.baseline/'preregistered.json').read_text(encoding='utf-8'))
    assert sha(args.input)==prior['input_sha256'] and prior['embedding_revision']==BGE_REVISION
    assert sha(ROOT/'models/gemma-tokenizer/tokenizer.json')==prior['tokenizer_sha256']
    assert all(list(factual_queries(c['items']))==c['queries'] for c in prior['cases'])
    args.output.mkdir(parents=True,exist_ok=False)
    code=source_manifest()
    started=time.monotonic()
    arms={'A_current':'current','B_lexical_context':'lexical','C_hybrid_context':'hybrid',
          'D_colbert_context':'colbert','E_hybrid_colbert_context':'hybrid_colbert'}
    budget=4096
    save(args.output/'preregistered.json',{'hypothesis':'Token-level maximum matches recover complementary conditions within the same original-source budget.',
        'source_sha256':code,'tool_sha256':sha(__file__),'input_sha256':sha(args.input),
        'baseline_manifest_sha256':sha(args.baseline/'preregistered.json'),
        'context_control_sha256':sha(args.context_control/'retrieval_results.jsonl.gz'),
        'primary_budget':budget,'arms':arms,'fixed_queries':{c['task_id']:c['queries'] for c in prior['cases']},
        'embedding_revision':BGE_REVISION,'colbert_head_sha256':COLBERT_HEAD_SHA256,
        'fusion':'Equal family weight, factual-query averaging, RRF k60 depth40; lexical+dense+ColBERT for arm E.',
        'hyperparameter_sweep':False,'classification_labels_read':False,'model_answers_read':False,
        'exposed_development_cases':True,'official_gold':False,'new_gemma_calls':0,'gpu_used':False,
        'cache_scope':'Current notice only; no cross-notice document embeddings or statistics.',
        'metrics':'Any human anchor, complete human evidence bundle, each quote hit; document coverage separately, never absence certification.'})
    import numpy as np
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer',local_files_only=True,trust_remote_code=False)
    encoder=BGEDenseEncoder(ROOT/'models/bge-m3',colbert=True)
    save(args.output/'reference_check.json',reference_check(encoder))
    controls={(r['measurement']['task_id'],r['measurement']['arm']):r['retrieval']
        for r in read(args.context_control/'retrieval_results.jsonl.gz')}
    grouped=defaultdict(list)
    for case in prior['cases']:grouped[case['record_id']].append(case)
    recs={r['id']:r for r in records(args.input) if r['id'] in grouped}
    assert recs.keys()==grouped.keys()
    measurements,receipts=[],[]
    with gzip.open(args.output/'retrieval_results.jsonl.gz','wt',encoding='utf-8') as stream:
        for rid,cases in grouped.items():
            began=time.monotonic()
            search=NoticeSearch(recs[rid],tokenizer,encoder)
            key=hashlib.sha256(rid.encode()).hexdigest()[:16]
            metadata=json.loads((args.baseline/f'{key}.chunks.json').read_text(encoding='utf-8'))
            assert metadata['record_id']==rid and metadata['documents_sha256']==search.doc_hashes
            assert metadata['chunks']==[asdict(s) for s in search.chunks]
            previous_vectors=np.load(args.baseline/f'{key}.vectors.npy',allow_pickle=False)
            for case in cases:
                for anchor in case['required_bundle']:
                    assert search.doc_hashes[anchor['doc_index']]==anchor['doc_sha256']
                for arm,method in arms.items():
                    result=search.search(case['items'],token_budget=budget,method=method)
                    assert result['source_tokens']<=budget
                    if method in ('current','lexical','hybrid'):
                        old={'current':'A_current','lexical':'lexical_ancestors','hybrid':'hybrid_ancestors'}[method]
                        assert result['spans']==controls[(case['task_id'],old)]['spans'],'Existing control changed'
                    hits={a['evidence_id']:any(s['doc_index']==a['doc_index'] and s['start']<=a['start'] and s['end']>=a['end'] for s in result['spans']) for a in case['required_bundle']}
                    measured={'task_id':case['task_id'],'record_id':rid,'arm':arm,'budget':budget,
                        'source_tokens':result['source_tokens'],'any_anchor':any(hits.values()),
                        'complete_bundle':all(hits.values()),'evidence_hits':hits,'coverage':result['coverage']}
                    measurements.append(measured)
                    stream.write(json.dumps({'measurement':measured,'retrieval':result},ensure_ascii=False)+'\n')
                stream.flush()
            assert previous_vectors.shape==search.vectors.shape
            assert np.allclose(previous_vectors,search.vectors,atol=1e-5,rtol=1e-5)
            with (args.output/f'{key}.colbert.npz').open('xb') as vectors:
                np.savez_compressed(vectors,**{f'c{i}':v for i,v in enumerate(search.colbert_vectors)})
            save(args.output/f'{key}.chunks.json',metadata)
            receipts.append({'record_id':rid,'chunks':len(search.chunks),'seconds':time.monotonic()-began,
                'control_dense_max_delta':float(np.max(np.abs(previous_vectors-search.vectors))),
                'colbert_token_vectors':sum(len(v) for v in search.colbert_vectors),
                'colbert_vector_bytes':sum(v.nbytes for v in search.colbert_vectors),
                'colbert_scoring':search.colbert_receipt,
                'saved_vectors_sha256':sha(args.output/f'{key}.colbert.npz')})
            print(json.dumps(receipts[-1]),flush=True)
    assert source_manifest()==code
    aggregates=[]
    for arm in arms:
        rows=[r for r in measurements if r['arm']==arm]
        aggregates.append({'arm':arm,'cases':len(rows),'any_anchor':sum(r['any_anchor'] for r in rows),
            'complete_bundle':sum(r['complete_bundle'] for r in rows),
            'quote_hits':sum(sum(r['evidence_hits'].values()) for r in rows),
            'mean_source_tokens':statistics.mean(r['source_tokens'] for r in rows)})
    report={'aggregates':aggregates,'measurements':measurements,'receipts':receipts,'encoder':encoder.receipt,
        'seconds':time.monotonic()-started,'controls_reproduced':True,'new_gemma_calls':0,'classification_labels_read':False,
        'official_macro_f1':None,'limitation':'12 exposed human-review cases; source coverage does not establish absence, semantic interpretation or final F1.'}
    save(args.output/'report.json',report)
    print(json.dumps({'aggregates':aggregates,'seconds':report['seconds']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
