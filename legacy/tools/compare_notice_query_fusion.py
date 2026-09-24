"""Compare factual-query aggregation with frozen current-notice BGE vectors."""
import argparse
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.embeddings import BGEDenseEncoder, BGE_REVISION
from submission.pps.notice_search import NoticeSearch, factual_queries
from submission.runtime import source_manifest


def read(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, obj):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--budget-policy', choices=('fixed4096', 'lexical_used', 'hybrid_used'), default='fixed4096')
    parser.add_argument('--comparison', choices=('query_fusion', 'evidence_selection', 'evidence_refill', 'query_lexical'), default='query_fusion')
    args = parser.parse_args()
    prior = json.loads((args.baseline/'preregistered.json').read_text(encoding='utf-8'))
    assert prior['embedding_revision'] == BGE_REVISION
    assert sha(ROOT/'models/gemma-tokenizer/tokenizer.json') == prior['tokenizer_sha256']
    controls = {(r['measurement']['task_id'], r['measurement']['arm']): r['retrieval']
        for r in read(args.control/'retrieval_results.jsonl.gz')}
    wanted = {c['record_id'] for c in prior['cases']}
    budget_arm = 'C_hybrid_context' if args.budget_policy == 'hybrid_used' else 'B_lexical_context'
    budgets = {c['task_id']: (4096 if args.budget_policy == 'fixed4096' else
        controls[(c['task_id'], budget_arm)]['source_tokens']) for c in prior['cases']}
    assert all(0 < n <= 4096 for n in budgets.values()), 'Matched controls need a nonempty source reading'
    records = {r['id']: r for r in read(args.inputs) if r['id'] in wanted}
    assert records.keys() == wanted
    args.output.mkdir(parents=True, exist_ok=False)
    source, began = source_manifest(), time.monotonic()
    arms = {'A_current': ('current', 'mean'), 'B_lexical_context': ('lexical', 'mean'),
        'C_hybrid_mean': ('hybrid', 'mean'), 'D_hybrid_best': ('hybrid', 'best'),
        'E_dense_mean': ('dense', 'mean')}
    selections = {arm: 'rrf' for arm in arms}
    explicit_queries = set()
    hypothesis = 'Independent fact questions should not dilute a one-query decisive BGE hit relative to a single lexical query. Use one best rank vote per retriever family.'
    if args.comparison == 'evidence_selection':
        arms = {'A_current': ('current', 'mean'), 'B_lexical_context': ('lexical', 'mean'),
            'C_hybrid_mean': ('hybrid', 'mean'), 'D_lexical_evidence': ('lexical', 'mean'),
            'E_hybrid_evidence': ('hybrid', 'mean'), 'F_hybrid_facet': ('hybrid', 'mean')}
        selections = {arm: ('evidence_cover' if arm in ('D_lexical_evidence', 'E_hybrid_evidence')
            else 'facet_cover' if arm == 'F_hybrid_facet' else 'rrf') for arm in arms}
        hypothesis = 'Score the actual union of complete source lines, credit overlapping chunks once, retain diminishing positive value for complementary passages of one question, and reconsider one greedy context through a bounded best exchange.'
    if args.comparison == 'evidence_refill':
        arms = {'A_current': ('current', 'mean'), 'B_lexical_context': ('lexical', 'mean'),
            'C_hybrid_mean': ('hybrid', 'mean'), 'D_hybrid_evidence': ('hybrid', 'mean'),
            'E_hybrid_refill': ('hybrid', 'mean')}
        selections = {arm: ('evidence_cover' if arm == 'D_hybrid_evidence'
            else 'evidence_refill' if arm == 'E_hybrid_refill' else 'rrf') for arm in arms}
        hypothesis = 'One context removal followed by several complete additions can escape a one-for-one local optimum; bounded to 4096 additional candidate evaluations, retaining the previous feasible incumbent. Same relevance objective, ranks, candidates, queries and source budgets; objective gain is not evidence-recall gain.'
    if args.comparison == 'query_lexical':
        arms = {'A_current': ('current', 'mean'), 'B_lexical_context': ('lexical', 'mean'),
            'C_hybrid_mean': ('hybrid', 'mean'), 'D_lexical_facts': ('lexical', 'mean'),
            'E_hybrid_facts': ('hybrid', 'mean')}
        selections = {arm: 'rrf' for arm in arms}
        explicit_queries = {'D_lexical_facts', 'E_hybrid_facts'}
        hypothesis = 'The same fixed fact questions already used by BGE can add current-notice lexical candidates and rank votes. Exercise the existing explicit-query lexical path without changing questions, context or RRF selection. Compare lexical-only and hybrid to attribute the added query route.'
    save(args.output/'preregistered.json', {
        'hypothesis': hypothesis, 'comparison': args.comparison, 'selection_policies': selections,
        'explicit_factual_query_lexical_arms': sorted(explicit_queries),
        'source_sha256': source, 'tool_sha256': sha(Path(__file__)), 'input_sha256': sha(args.inputs),
        'baseline_manifest_sha256': sha(args.baseline/'preregistered.json'),
        'control_sha256': sha(args.control/'retrieval_results.jsonl.gz'),
        'embedding_revision': BGE_REVISION, 'source_budget_policy':args.budget_policy,
        'source_budgets':budgets, 'arms': arms,
        'fixed_queries': {c['task_id']: c['queries'] for c in prior['cases']},
        'rank_k':60, 'depth_per_query':40, 'context_policy':'unchanged ancestors, table headers and contiguous conditions',
        'hyperparameter_sweep':False, 'review_cases_exposed':True, 'official_gold':False,
        'classification_labels_read':False, 'model_answers_read':False, 'gpu_used':False,
        'new_gemma_calls':0, 'cache_scope':'Exact current-notice chunks only, never other evaluation notices.'})
    # Freeze executable selection and measurement code before looking at hits.
    for path in (Path(__file__), ROOT/'submission/pps/notice_search.py', ROOT/'submission/pps/evidence_selection.py'):
        if path.exists():
            with (args.output/path.name).open('xb') as stream:
                stream.write(path.read_bytes())
    import numpy as np
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    encoder = BGEDenseEncoder(ROOT/'models/bge-m3')
    searches, measurements, caches = {}, [], []
    with gzip.open(args.output/'retrieval_results.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for case in prior['cases']:
            rid = case['record_id']
            if rid not in searches:
                search = NoticeSearch(records[rid], tokenizer, encoder)
                key = hashlib.sha256(rid.encode()).hexdigest()[:16]
                metadata_path = args.baseline/(key+'.chunks.json')
                vector_path = args.baseline/(key+'.vectors.npy')
                metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
                assert metadata['record_id'] == rid and metadata['documents_sha256'] == search.doc_hashes
                assert metadata['chunks'] == [asdict(s) for s in search.chunks]
                vectors = np.load(vector_path, allow_pickle=False)
                assert vectors.shape == (len(search.chunks), 1024) and np.isfinite(vectors).all()
                assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5)
                search.vectors = vectors
                searches[rid] = search
                caches.append({'record_id':rid, 'chunks_sha256':sha(metadata_path), 'vectors_sha256':sha(vector_path)})
            search = searches[rid]
            assert list(factual_queries(case['items'])) == case['queries']
            for anchor in case['required_bundle']:
                assert search.doc_hashes[anchor['doc_index']] == anchor['doc_sha256']
            for arm, (method, aggregation) in arms.items():
                budget = budgets[case['task_id']]
                result = search.search(case['items'], method=method, query_aggregation=aggregation,
                    selection_policy=selections[arm], token_budget=budget,
                    **({'queries': case['queries']} if arm in explicit_queries else {}))
                assert result['source_tokens'] <= budget
                if (args.budget_policy == 'fixed4096' and arm in ('A_current', 'B_lexical_context', 'C_hybrid_mean')
                        or args.budget_policy == 'lexical_used' and arm == 'B_lexical_context'
                        or args.budget_policy == 'hybrid_used' and arm == 'C_hybrid_mean'):
                    old = 'C_hybrid_context' if arm == 'C_hybrid_mean' else arm
                    assert result['spans'] == controls[(case['task_id'], old)]['spans'], 'Frozen control changed'
                hits = {a['evidence_id']: any(s['doc_index'] == a['doc_index'] and s['start'] <= a['start']
                    and s['end'] >= a['end'] for s in result['spans']) for a in case['required_bundle']}
                m = {'task_id':case['task_id'], 'record_id':rid, 'arm':arm, 'budget':budget,
                    'source_tokens':result['source_tokens'], 'any_anchor':any(hits.values()) if hits else None,
                    'complete_bundle':all(hits.values()) if hits else None,
                    'evidence_hits':hits, 'coverage':result['coverage']}
                measurements.append(m)
                stream.write(json.dumps({'measurement':m, 'retrieval':result}, ensure_ascii=False)+'\n')
            stream.flush()
            print(json.dumps({'case':case['task_id'], 'seconds':time.monotonic()-began}), flush=True)
    assert source_manifest() == source
    aggregates = []
    for arm in arms:
        part = [r for r in measurements if r['arm'] == arm]
        aggregates.append({'arm':arm, 'cases':len(part), 'anchored_cases':sum(bool(r['evidence_hits']) for r in part),
            'any_anchor':sum(r['any_anchor'] is True for r in part), 'complete_bundle':sum(r['complete_bundle'] is True for r in part),
            'quote_hits':sum(sum(r['evidence_hits'].values()) for r in part),
            'mean_source_tokens':statistics.mean(r['source_tokens'] for r in part)})
    report = {'aggregates':aggregates, 'measurements':measurements, 'cache_receipts':caches,
        'seconds':time.monotonic()-began, 'encoder':encoder.receipt, 'controls_reproduced':True,
        'classification_labels_read':False, 'new_gemma_calls':0, 'gpu_used':False,
        'official_macro_f1':None, 'adopted':False,
        'limitations':'Exposed human-review quote and context bundles, not legal gold or held-out accuracy. Zero-anchor cases have no quote-recall denominator; coverage remains separate. No absence proof or F1 effect inferred.'}
    save(args.output/'report.json', report)
    print(json.dumps(aggregates, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
