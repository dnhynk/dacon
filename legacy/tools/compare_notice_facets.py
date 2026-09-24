"""Compare fixed-query rank fusion and facet coverage, reusing exact local vectors.

No labels, prior model answers or other notices enter either ranking function.
The existing review cases remain exposed development diagnostics.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.data import records
from submission.pps.embeddings import BGEDenseEncoder, BGE_REVISION
from submission.pps.notice_search import NoticeSearch, factual_queries
from submission.runtime import source_manifest


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path, obj): Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return list(map(json.loads, f))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    prior = json.loads((args.baseline/'preregistered.json').read_text(encoding='utf-8'))
    assert digest(args.input) == prior['input_sha256']
    assert prior['embedding_revision'] == BGE_REVISION
    assert digest(ROOT/'models/gemma-tokenizer/tokenizer.json') == prior['tokenizer_sha256']
    baseline = {(r['measurement']['task_id'], r['measurement']['budget'], r['measurement']['arm']): r
                for r in rows(args.baseline/'retrieval_results.jsonl.gz')}
    frozen = {'hypothesis': 'Diminishing per-factual-query rank utility covers complementary facts within the same original-source token cap.',
        'source_sha256': source_manifest(), 'tool_sha256': digest(__file__), 'baseline': str(args.baseline),
        'baseline_manifest_sha256': digest(args.baseline/'preregistered.json'), 'input_sha256': digest(args.input),
        'primary_budget': 4096, 'budgets': [2048, 4096, 8192],
        'arms': ['A_current_recorded', 'C_hybrid_rrf_control', 'D_hybrid_facets', 'D_hybrid_facets_no_context'],
        'selection': 'Greedy marginal max reciprocal-rank utility per added source token; equal family/query weight; then original RRF fill.',
        'fixed_queries': {c['task_id']: factual_queries(c['items']) for c in prior['cases']},
        'hyperparameter_sweep': False, 'classification_labels_read': False, 'model_answers_read': False,
        'review_cases_exposed': True, 'official_gold': False, 'new_gemma_calls': 0,
        'document_vectors': 'Reuse only exact same current-notice chunks, hashes and pinned-model vectors. Not a runtime shared evaluation corpus.'}
    save(args.output/'preregistered.json', frozen)
    import numpy as np
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    encoder = BGEDenseEncoder(ROOT/'models/bge-m3')
    groups = defaultdict(list)
    for case in prior['cases']: groups[case['record_id']].append(case)
    recs = {r['id']: r for r in records(args.input) if r['id'] in groups}
    measurements, cache_receipts = [], []
    with gzip.open(args.output/'retrieval_results.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for rid, cases in groups.items():
            rec = recs[rid]
            search = NoticeSearch(rec, tokenizer, encoder)
            key = hashlib.sha256(rid.encode()).hexdigest()[:16]
            index_path, vector_path = args.baseline/f'{key}.chunks.json', args.baseline/f'{key}.vectors.npy'
            metadata = json.loads(index_path.read_text(encoding='utf-8'))
            assert metadata['record_id'] == rid and metadata['documents_sha256'] == search.doc_hashes
            assert metadata['chunks'] == [asdict(s) for s in search.chunks]
            vectors = np.load(vector_path, allow_pickle=False)
            assert vectors.shape == (len(search.chunks), 1024) and np.isfinite(vectors).all()
            assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5)
            search.vectors = vectors
            cache_receipts.append({'record_id': rid, 'chunks_sha256': digest(index_path), 'vectors_sha256': digest(vector_path)})
            for case in cases:
                for e in case['required_bundle']:
                    assert search.doc_hashes[e['doc_index']] == e['doc_sha256']
                for budget in frozen['budgets']:
                    outputs = {'A_current_recorded': baseline[(case['task_id'], budget, 'A_current')]['retrieval']}
                    for arm, policy, context in [('C_hybrid_rrf_control', 'rrf', True),
                            ('D_hybrid_facets', 'facet_cover', True), ('D_hybrid_facets_no_context', 'facet_cover', False)]:
                        outputs[arm] = search.search(case['items'], token_budget=budget, method='hybrid',
                                                     selection_policy=policy, expand_context=context)
                    control = baseline[(case['task_id'], budget, 'C_hybrid_context')]['retrieval']
                    assert control['spans'] == outputs['C_hybrid_rrf_control']['spans'], 'Original fixed-vector ranking not reproduced'
                    for arm, result in outputs.items():
                        hits = {e['evidence_id']: any(s['doc_index'] == e['doc_index'] and s['start'] <= e['start']
                                    and s['end'] >= e['end'] for s in result['spans']) for e in case['required_bundle']}
                        m = {'task_id': case['task_id'], 'record_id': rid, 'budget': budget, 'arm': arm,
                            'evidence_hits': hits, 'any_review_anchor_hit': any(hits.values()),
                            'complete_review_bundle': all(hits.values()), 'source_tokens': result['source_tokens'],
                            'coverage': result['coverage']}
                        measurements.append(m)
                        stream.write(json.dumps({'measurement': m, 'retrieval': result}, ensure_ascii=False)+'\n')
                stream.flush()
            print(json.dumps({'record': rid, 'measurements': len(measurements), 'seconds': time.monotonic()-start}), flush=True)
    aggregates = []
    for budget in frozen['budgets']:
        for arm in frozen['arms']:
            subset = [m for m in measurements if m['budget'] == budget and m['arm'] == arm]
            aggregates.append({'budget': budget, 'arm': arm, 'cases': len(subset),
                'any_anchor': sum(m['any_review_anchor_hit'] for m in subset),
                'bundle': sum(m['complete_review_bundle'] for m in subset),
                'quotes': sum(sum(m['evidence_hits'].values()) for m in subset),
                'mean_source_tokens': statistics.mean(m['source_tokens'] for m in subset)})
    assert frozen['source_sha256'] == source_manifest()
    report = {'aggregates': aggregates, 'cache_receipts': cache_receipts, 'cases': measurements,
        'seconds': time.monotonic()-start, 'encoder': encoder.receipt, 'original_control_reproduced': True,
        'classification_labels_read': False, 'new_gemma_calls': 0, 'official_macro_f1': None,
        'interpretation': 'Measured original quotation coverage only; no inference or semantic-certification claim.'}
    save(args.output/'report.json', report)
    print(json.dumps({'aggregates': aggregates, 'seconds': report['seconds']}, indent=2))


if __name__ == '__main__': main()
