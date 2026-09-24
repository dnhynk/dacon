"""Fixed-vector comparison of parent-heading context at one source-token cap."""
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


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))


def save(path, obj):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    prior = json.loads((args.baseline / 'preregistered.json').read_text(encoding='utf-8'))
    assert sha(args.input) == prior['input_sha256']
    assert prior['embedding_revision'] == BGE_REVISION
    assert sha(ROOT / 'models/gemma-tokenizer/tokenizer.json') == prior['tokenizer_sha256']
    assert all(list(factual_queries(c['items'])) == c['queries'] for c in prior['cases'])
    code = source_manifest()
    budget = prior['primary_budget']
    baseline = {(r['measurement']['task_id'], r['measurement']['arm']): r['retrieval']
        for r in read_rows(args.baseline / 'retrieval_results.jsonl.gz') if r['measurement']['budget'] == budget}
    save(args.output / 'preregistered.json', {
        'hypothesis': 'Explicit parent headings prevent loss of governing conditions under the same original-source budget.',
        'source_sha256': code, 'tool_sha256': sha(Path(__file__)), 'primary_budget': budget,
        'input_sha256': sha(args.input), 'baseline_manifest_sha256': sha(args.baseline / 'preregistered.json'),
        'embedding_revision': BGE_REVISION, 'queries_and_vectors_fixed': True,
        'classification_labels_read': False, 'model_answers_read': False, 'new_gemma_calls': 0,
        'exposed_development_cases': True, 'official_gold': False, 'hyperparameter_sweep': False})
    import numpy as np
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT / 'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    encoder = BGEDenseEncoder(ROOT / 'models/bge-m3')
    groups = defaultdict(list)
    for case in prior['cases']:
        groups[case['record_id']].append(case)
    recs = {r['id']: r for r in records(args.input) if r['id'] in groups}
    measurements, cache = [], []
    with gzip.open(args.output / 'retrieval_results.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for record_id, cases in groups.items():
            near = NoticeSearch(recs[record_id], tokenizer, encoder, heading_context='nearest')
            full = NoticeSearch(recs[record_id], tokenizer, encoder, heading_context='ancestors')
            key = hashlib.sha256(record_id.encode()).hexdigest()[:16]
            index_path = args.baseline / (key + '.chunks.json')
            vector_path = args.baseline / (key + '.vectors.npy')
            metadata = json.loads(index_path.read_text(encoding='utf-8'))
            assert metadata['record_id'] == record_id and metadata['documents_sha256'] == near.doc_hashes
            assert metadata['chunks'] == [asdict(s) for s in near.chunks]
            vectors = np.load(vector_path, allow_pickle=False)
            assert vectors.shape == (len(near.chunks), 1024) and np.isfinite(vectors).all()
            assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5)
            near.vectors = full.vectors = vectors
            cache.append({'record_id': record_id, 'chunks_sha256': sha(index_path), 'vectors_sha256': sha(vector_path)})
            for case in cases:
                outputs = {'A_current': near.search(case['items'], token_budget=budget, method='current')}
                for method in ('lexical', 'hybrid'):
                    control = near.search(case['items'], token_budget=budget, method=method)
                    old_arm = 'C_hybrid_context' if method == 'hybrid' else 'A_lexical_context'
                    assert control['spans'] == baseline[(case['task_id'], old_arm)]['spans'], 'Old context/ranking not reproduced'
                    full._queries = near._queries  # Same notice and exact chunk vectors only.
                    outputs[method + '_nearest'] = control
                    outputs[method + '_ancestors'] = full.search(case['items'], token_budget=budget, method=method)
                for arm, result in outputs.items():
                    assert result['source_tokens'] <= budget
                    hits = {}
                    for witness in case['required_bundle']:
                        assert near.doc_hashes[witness['doc_index']] == witness['doc_sha256']
                        hits[witness['evidence_id']] = any(s['doc_index'] == witness['doc_index'] and
                            s['start'] <= witness['start'] and s['end'] >= witness['end'] for s in result['spans'])
                    measurement = {'task_id': case['task_id'], 'record_id': record_id, 'arm': arm,
                        'source_tokens': result['source_tokens'], 'any_anchor': any(hits.values()),
                        'complete_bundle': all(hits.values()), 'evidence_hits': hits, 'coverage': result['coverage']}
                    measurements.append(measurement)
                    stream.write(json.dumps({'measurement': measurement, 'retrieval': result}, ensure_ascii=False) + '\n')
            print(json.dumps({'record': record_id, 'measurements': len(measurements), 'seconds': time.monotonic()-started}), flush=True)
    assert code == source_manifest()
    aggregates = []
    for arm in sorted({m['arm'] for m in measurements}):
        part = [m for m in measurements if m['arm'] == arm]
        aggregates.append({'arm': arm, 'cases': len(part), 'any_anchor': sum(m['any_anchor'] for m in part),
            'complete_bundle': sum(m['complete_bundle'] for m in part),
            'quote_hits': sum(sum(m['evidence_hits'].values()) for m in part),
            'source_tokens_mean': statistics.mean(m['source_tokens'] for m in part)})
    report = {'aggregates': aggregates, 'measurements': measurements, 'cache': cache,
        'cpu_seconds': time.monotonic()-started, 'encoder': encoder.receipt,
        'original_controls_reproduced': True, 'new_gemma_calls': 0, 'classification_labels_read': False,
        'limitation': 'Exposed quote-location coverage, not semantic identity, legal gold, absence proof or final F1.'}
    save(args.output / 'report.json', report)
    print(json.dumps(aggregates, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
