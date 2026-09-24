"""One fixed-budget sparse/dense/lexical comparison on exposed review cases.

No classification labels, generated answers, query tuning or GPU are used.
All ranking arms share the same current-notice chunks and context expansion.
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
from submission.pps.embeddings import BGEDenseEncoder, BGE_REVISION, SPARSE_HEAD_SHA256
from submission.pps.notice_search import NoticeSearch, factual_queries
from submission.runtime import source_manifest


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path, obj): Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def reference_pooling_check(encoder):
    """Compare actual fixed weights with the official vocabulary scatter-max equation."""
    import numpy as np
    torch = encoder.torch
    texts = ['입찰 전에 확약서를 제출해야 한다.', '확약서 확약서: 입찰 전에 제출할 필요가 없다.']
    actual = encoder.encode_features(texts)
    batch = encoder.tokenizer(texts, padding=True, return_tensors='pt')
    with torch.inference_mode():
        states = encoder.model(**batch).last_hidden_state
        dense = torch.nn.functional.normalize(states[:, 0].float(), p=2, dim=1)
        weights = torch.relu(encoder.sparse_linear(states.float())).squeeze(-1)
        vocabulary = torch.zeros((len(texts), encoder.model.config.vocab_size))
        vocabulary.scatter_reduce_(1, batch['input_ids'], weights, reduce='amax')
        vocabulary[:, sorted(t for t in encoder.ignored_tokens if t is not None)] = 0.
    expected = [{i: float(row[i]) for i in row.nonzero().flatten().tolist()} for row in vocabulary]
    for actual_row, expected_row in zip(actual['sparse'], expected):
        assert actual_row.keys() == expected_row.keys()
        assert np.allclose(list(actual_row.values()), list(expected_row.values()), atol=1e-5, rtol=1e-5)
    assert np.allclose(actual['dense'], dense.numpy(), atol=1e-5, rtol=1e-5)
    return {'passed': True, 'texts': texts, 'dense_max_delta': float(np.max(np.abs(actual['dense']-dense.numpy()))),
            'sparse_terms': [len(row) for row in expected], 'independent_reference_forwards': 1,
            'equation': 'ReLU fixed linear head; vocabulary scatter_reduce amax; CLS/EOS/PAD/UNK excluded; no sparse normalization.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    began = time.monotonic()
    prior = json.loads((args.baseline/'preregistered.json').read_text(encoding='utf-8'))
    assert digest(args.input) == prior['input_sha256']
    assert prior['embedding_revision'] == BGE_REVISION
    assert digest(ROOT/'models/gemma-tokenizer/tokenizer.json') == prior['tokenizer_sha256']
    with gzip.open(args.baseline/'retrieval_results.jsonl.gz', 'rt', encoding='utf-8') as stream:
        baseline = {(r['measurement']['task_id'], r['measurement']['budget'], r['measurement']['arm']): r
                    for r in map(json.loads, stream)}
    arms = {'A_current': 'current', 'C_hybrid_control': 'hybrid',
            'D_sparse_context': 'sparse', 'E_hybrid_sparse_context': 'hybrid_sparse'}
    frozen = {'hypothesis': 'The supplied learned token weights add complementary retrieval candidates to lexical+dense fusion within a fixed original-source budget.',
        'source_sha256': source_manifest(), 'tool_sha256': digest(__file__),
        'input_sha256': digest(args.input), 'baseline_manifest_sha256': digest(args.baseline/'preregistered.json'),
        'embedding_revision': BGE_REVISION, 'sparse_head_sha256': SPARSE_HEAD_SHA256,
        'primary_budget': 4096, 'budgets': [2048, 4096, 8192], 'arms': arms,
        'fusion': 'Equal family weights, per-family factual-query averaging, RRF k60 depth40. Same original context expansion.',
        'fixed_queries': {c['task_id']: factual_queries(c['items']) for c in prior['cases']},
        'metrics': prior['metrics'], 'hyperparameter_sweep': False, 'classification_labels_read': False,
        'model_answers_read': False, 'review_cases_exposed': True, 'official_gold': False,
        'held_out_cases': False, 'gpu_used': False, 'new_gemma_calls': 0,
        'cache_scope': 'Current-notice documents only; static factual queries shared only within that notice.'}
    save(args.output/'preregistered.json', frozen)
    import numpy as np
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    encoder = BGEDenseEncoder(ROOT/'models/bge-m3', sparse=True)
    reference = reference_pooling_check(encoder)
    save(args.output/'reference_pooling_check.json', reference)
    grouped = defaultdict(list)
    for case in prior['cases']:
        assert list(factual_queries(case['items'])) == case['queries']
        grouped[case['record_id']].append(case)
    recs = {r['id']: r for r in records(args.input) if r['id'] in grouped}
    assert recs.keys() == grouped.keys()
    measurements, receipts, reproduced = [], [], []
    with gzip.open(args.output/'retrieval_results.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for rid, cases in grouped.items():
            start = time.monotonic()
            search = NoticeSearch(recs[rid], tokenizer, encoder)
            key = hashlib.sha256(rid.encode()).hexdigest()[:16]
            index_path, vector_path = args.baseline/f'{key}.chunks.json', args.baseline/f'{key}.vectors.npy'
            old_index = json.loads(index_path.read_text(encoding='utf-8'))
            assert old_index['record_id'] == rid and old_index['documents_sha256'] == search.doc_hashes
            assert old_index['chunks'] == [asdict(s) for s in search.chunks]
            old_vectors = np.load(vector_path, allow_pickle=False)
            for case in cases:
                for e in case['required_bundle']:
                    assert search.doc_hashes[e['doc_index']] == e['doc_sha256']
                    assert 0 <= e['start'] < e['end'] <= len(recs[rid]['docs'][e['doc_index']]['text'])
                for budget in frozen['budgets']:
                    for arm, method in arms.items():
                        # Omitted explicit queries preserve the existing lexical control path.
                        result = search.search(case['items'], token_budget=budget, method=method)
                        assert result['source_tokens'] <= budget
                        if arm in ['A_current', 'C_hybrid_control']:
                            old_arm = 'A_current' if arm == 'A_current' else 'C_hybrid_context'
                            old = baseline[(case['task_id'], budget, old_arm)]['retrieval']
                            reproduced.append({'task_id': case['task_id'], 'budget': budget, 'arm': arm,
                                'spans_equal': result['spans'] == old['spans']})
                        hits = {e['evidence_id']: any(s['doc_index'] == e['doc_index'] and s['start'] <= e['start']
                            and s['end'] >= e['end'] for s in result['spans']) for e in case['required_bundle']}
                        row = {'task_id': case['task_id'], 'record_id': rid, 'budget': budget, 'arm': arm,
                            'evidence_hits': hits, 'any_review_anchor_hit': any(hits.values()),
                            'complete_review_bundle': all(hits.values()), 'source_tokens': result['source_tokens'],
                            'coverage': result['coverage']}
                        measurements.append(row)
                        stream.write(json.dumps({'measurement': row, 'retrieval': result}, ensure_ascii=False)+'\n')
                stream.flush()
            assert old_vectors.shape == search.vectors.shape
            receipts.append({'record_id': rid, 'chunks': len(search.chunks),
                'old_vectors_sha256': digest(vector_path), 'dense_max_delta': float(np.max(np.abs(old_vectors-search.vectors))),
                'dense_equivalent': bool(np.allclose(old_vectors, search.vectors, atol=1e-5, rtol=1e-5)),
                'seconds': time.monotonic()-start, 'sparse_nonzero_terms': sum(map(len, search.sparse_vectors))})
            np.save(args.output/f'{key}.vectors.npy', search.vectors, allow_pickle=False)
            save(args.output/f'{key}.chunks.json', old_index)
            save(args.output/f'{key}.sparse.json', {'record_id': rid, 'documents_sha256': search.doc_hashes,
                'sparse_head_sha256': SPARSE_HEAD_SHA256, 'vectors': search.sparse_vectors})
            print(json.dumps(receipts[-1]), flush=True)
    aggregates = []
    for budget in frozen['budgets']:
        for arm in arms:
            selected = [r for r in measurements if r['budget'] == budget and r['arm'] == arm]
            aggregates.append({'budget': budget, 'arm': arm, 'cases': len(selected),
                'any_anchor': sum(r['any_review_anchor_hit'] for r in selected),
                'bundle': sum(r['complete_review_bundle'] for r in selected),
                'quotes': sum(sum(r['evidence_hits'].values()) for r in selected),
                'mean_source_tokens': statistics.mean(r['source_tokens'] for r in selected)})
    assert frozen['source_sha256'] == source_manifest() and frozen['tool_sha256'] == digest(__file__)
    report = {'aggregates': aggregates, 'cases': measurements, 'receipts': receipts, 'encoder': encoder.receipt,
        'seconds': time.monotonic()-began, 'source_unchanged': True, 'reference_pooling': reference,
        'controls_reproduced': all(r['spans_equal'] for r in reproduced), 'control_comparisons': reproduced,
        'classification_labels_read': False, 'new_gemma_calls': 0, 'official_macro_f1': None,
        'interpretation': 'Original quotation recovery on 12 exposed cases only; source coverage is not a legal absence certificate or final inference effect.'}
    save(args.output/'report.json', report)
    print(json.dumps({'aggregates': aggregates, 'seconds': report['seconds'],
                      'controls_reproduced': report['controls_reproduced']}, indent=2))


if __name__ == '__main__': main()
