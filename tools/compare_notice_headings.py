"""Frozen-vector comparison of original-source heading context construction."""
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
from submission.pps.notice_search import NoticeSearch, factual_queries, heading_ancestry, is_heading
from submission.pps.retrieval import _source_units
from submission.runtime import source_manifest


def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))


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
    args = parser.parse_args()
    prior = json.loads((args.baseline / 'preregistered.json').read_text(encoding='utf-8'))
    assert prior['embedding_revision'] == BGE_REVISION
    assert sha(ROOT / 'models/gemma-tokenizer/tokenizer.json') == prior['tokenizer_sha256']
    controls = {(r['measurement']['task_id'], r['measurement']['arm']): r['retrieval']
                for r in rows(args.control / 'retrieval_results.jsonl.gz')}
    input_records = rows(args.inputs)
    records = {r['id']: r for r in input_records}
    assert len(records) == len(input_records)
    source = source_manifest()
    arms = {'A_lexical_legacy': ('lexical', 'legacy_ancestors'),
        'B_lexical_headings': ('lexical', 'ancestors'),
        'C_hybrid_legacy': ('hybrid', 'legacy_ancestors'),
        'D_hybrid_headings': ('hybrid', 'ancestors')}
    budget_arm = 'C_hybrid_context' if args.budget_policy == 'hybrid_used' else 'B_lexical_context'
    budgets = {c['task_id']: 4096 if args.budget_policy == 'fixed4096' else
               controls[(c['task_id'], budget_arm)]['source_tokens'] for c in prior['cases']}
    args.output.mkdir(parents=True, exist_ok=False)
    save(args.output / 'preregistered.json', {
        'hypothesis': 'Compact section numbers retain governing ancestors; decimal values and dates cannot reset them.',
        'source_sha256': source, 'tool_sha256': sha(Path(__file__)),
        'input_sha256': sha(args.inputs), 'case_manifest_sha256': sha(args.baseline / 'preregistered.json'),
        'control_sha256': sha(args.control / 'retrieval_results.jsonl.gz'),
        'embedding_revision': BGE_REVISION, 'arms': arms, 'budget_policy': args.budget_policy,
        'source_budgets': budgets, 'query_policy': 'Identical frozen factual queries; unchanged legacy lexical terms and RRF.',
        'context_difference': 'Only numbered heading discovery/ancestry; neighbor lines, provisos and pipe-table context unchanged.',
        'classification_labels_read': False, 'model_responses_read': False,
        'review_cases_exposed': True, 'new_gemma_calls': 0, 'GPU_used': False,
        'hyperparameter_sweep': False, 'structural_inventory': 'All supplied development sources; source structure is not legal accuracy.'})
    for path in (Path(__file__), ROOT / 'submission/pps/notice_search.py'):
        (args.output / path.name).write_bytes(path.read_bytes())
    began = time.monotonic()
    inventory = []
    for rec in input_records:
        for di, doc in enumerate(rec['docs']):
            text = doc['text']
            units = _source_units(text)
            before = heading_ancestry(text, units, compact_numbering=False)
            after = heading_ancestry(text, units)
            changes = []
            for i, (lo, hi) in enumerate(units):
                old, new = is_heading(text[lo:hi], compact_numbering=False), is_heading(text[lo:hi])
                if old != new:
                    changes.append({'start': lo, 'end': hi, 'quote': text[lo:hi], 'old_heading': old, 'new_heading': new})
            if before != after or changes:
                inventory.append({'record_id': rec['id'], 'doc_index': di,
                    'doc_sha256': hashlib.sha256(text.encode()).hexdigest(),
                    'heading_type_changes': changes,
                    'units_with_changed_ancestors': sum(a != b for a, b in zip(before, after))})
    save(args.output / 'structure_inventory.json', {'documents': inventory, 'input_records': len(records),
        'classification_labels_read': False, 'legal_or_relevance_gold': False})
    import numpy as np
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT / 'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    encoder = BGEDenseEncoder(ROOT / 'models/bge-m3')
    searches, measurements, cache_receipts = {}, [], []
    with gzip.open(args.output / 'retrieval_results.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for case in prior['cases']:
            rid = case['record_id']
            if rid not in searches:
                pair = {policy: NoticeSearch(records[rid], tokenizer, encoder, heading_context=policy)
                        for policy in ('legacy_ancestors', 'ancestors')}
                key = hashlib.sha256(rid.encode()).hexdigest()[:16]
                meta_path, vector_path = args.baseline / (key + '.chunks.json'), args.baseline / (key + '.vectors.npy')
                metadata = json.loads(meta_path.read_text(encoding='utf-8'))
                vectors = np.load(vector_path, allow_pickle=False)
                assert vectors.shape == (len(pair['ancestors'].chunks), 1024)
                assert np.isfinite(vectors).all() and np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5)
                queries = {}
                for search in pair.values():
                    assert metadata['record_id'] == rid and metadata['documents_sha256'] == search.doc_hashes
                    assert metadata['chunks'] == [asdict(s) for s in search.chunks]
                    search.vectors = vectors
                    search._queries = queries  # Same fixed queries on the same current notice.
                searches[rid] = pair
                cache_receipts.append({'record_id': rid, 'metadata_sha256': sha(meta_path), 'vectors_sha256': sha(vector_path)})
            assert list(factual_queries(case['items'])) == case['queries']
            for arm, (method, policy) in arms.items():
                search = searches[rid][policy]
                result = search.search(case['items'], method=method, token_budget=budgets[case['task_id']])
                if policy == 'legacy_ancestors' and args.budget_policy == 'fixed4096':
                    old = controls[(case['task_id'], 'B_lexical_context' if method == 'lexical' else 'C_hybrid_context')]
                    assert old['spans'] == result['spans'] and old['source_tokens'] == result['source_tokens'], 'Frozen control changed'
                hits = {}
                for anchor in case['required_bundle']:
                    assert anchor['doc_sha256'] == search.doc_hashes[anchor['doc_index']]
                    assert type(anchor['start']) is int and type(anchor['end']) is int
                    assert 0 <= anchor['start'] < anchor['end'] <= len(search.rec['docs'][anchor['doc_index']]['text'])
                    hits[anchor['evidence_id']] = any(s['doc_index'] == anchor['doc_index']
                        and s['start'] <= anchor['start'] and s['end'] >= anchor['end'] for s in result['spans'])
                measured = {'task_id': case['task_id'], 'record_id': rid, 'arm': arm,
                    'source_token_budget': budgets[case['task_id']], 'source_tokens': result['source_tokens'],
                    'any_anchor': any(hits.values()), 'complete_bundle': all(hits.values()),
                    'evidence_hits': hits, 'coverage': result['coverage']}
                measurements.append(measured)
                stream.write(json.dumps({'measurement': measured, 'retrieval': result}, ensure_ascii=False) + '\n')
            stream.flush()
            print('Compared:', case['task_id'], flush=True)
    assert source_manifest() == source
    aggregate = []
    for arm in arms:
        part = [m for m in measurements if m['arm'] == arm]
        aggregate.append({'arm': arm, 'cases': len(part), 'any_anchor': sum(m['any_anchor'] for m in part),
            'complete_bundle': sum(m['complete_bundle'] for m in part),
            'quote_hits': sum(sum(m['evidence_hits'].values()) for m in part),
            'mean_source_tokens': statistics.mean(m['source_tokens'] for m in part)})
    report = {'aggregates': aggregate, 'measurements': measurements, 'cache_receipts': cache_receipts,
        'encoder': encoder.receipt, 'seconds': time.monotonic() - began, 'source_unchanged': True,
        'old_4096_controls_reproduced': args.budget_policy == 'fixed4096',
        'classification_labels_read': False, 'new_gemma_calls': 0, 'fresh_f1': None,
        'limitation': 'Same source caps, not necessarily identical actual usage. Review anchors are exposed; structural changes are not relevance or absence certification.'}
    save(args.output / 'report.json', report)
    print(json.dumps({'aggregates': aggregate, 'seconds': report['seconds']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
