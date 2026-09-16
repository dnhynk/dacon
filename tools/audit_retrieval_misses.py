"""Explain exposed review misses without tuning queries or reading labels.

Human ranges are used only after reproducing the frozen search control. Oracle
range costs describe feasibility, never a deployable retrieval score.
"""
import argparse
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.embeddings import BGEDenseEncoder, BGE_REVISION
from submission.pps.notice_search import NoticeSearch, factual_queries
from submission.pps.retrieval import Span
from submission.runtime import source_manifest


def read(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def covers(ranges, anchor):
    return any(di == anchor['doc_index'] and lo <= anchor['start'] and hi >= anchor['end']
        for di, lo, hi in ranges)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Choose a new audit directory')
    prior = json.loads((args.baseline/'preregistered.json').read_text(encoding='utf-8'))
    assert prior['embedding_revision'] == BGE_REVISION
    assert sha(ROOT/'models/gemma-tokenizer/tokenizer.json') == prior['tokenizer_sha256']
    controls = {r['measurement']['task_id']: r['retrieval'] for r in read(args.control/'retrieval_results.jsonl.gz')
        if r['measurement']['arm'] == 'C_hybrid_context'}
    cases = prior['cases']
    wanted = {c['record_id'] for c in cases}
    records = {r['id']: r for r in read(args.inputs) if r['id'] in wanted}
    assert records.keys() == wanted
    code, began = source_manifest(), time.monotonic()
    args.output.mkdir(parents=True, exist_ok=False)
    import numpy as np
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    encoder = BGEDenseEncoder(ROOT/'models/bge-m3')
    searches, details = {}, []
    for case in cases:
        rid = case['record_id']
        if rid not in searches:
            search = NoticeSearch(records[rid], tokenizer, encoder)
            key = hashlib.sha256(rid.encode()).hexdigest()[:16]
            metadata = json.loads((args.baseline/(key+'.chunks.json')).read_text(encoding='utf-8'))
            assert metadata['record_id'] == rid and metadata['documents_sha256'] == search.doc_hashes
            assert metadata['chunks'] == [asdict(s) for s in search.chunks]
            vectors = np.load(args.baseline/(key+'.vectors.npy'), allow_pickle=False)
            assert vectors.shape == (len(search.chunks), 1024) and np.isfinite(vectors).all()
            assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5)
            search.vectors = vectors
            searches[rid] = search
        search = searches[rid]
        result = search.search(case['items'], token_budget=4096, method='hybrid')
        assert result['spans'] == controls[case['task_id']]['spans'], 'Frozen control changed'
        queries = factual_queries(case['items'])
        assert list(queries) == case['queries']
        lexical = search._lexical_lists(case['items'])
        dense = search._dense_lists(queries)
        order = search._fuse([lexical, dense])
        rank_maps = {name: [{index: rank for rank, index in enumerate(ranking, 1)} for ranking in rankings]
            for name, rankings in (('lexical', lexical), ('dense', dense), ('fused', [order]))}
        shown = [(s['doc_index'], s['start'], s['end']) for s in result['spans']]
        anchors = []
        oracle_quotes, oracle_context = [], []
        for anchor in case['required_bundle']:
            di, lo, hi = anchor['doc_index'], anchor['start'], anchor['end']
            assert search.doc_hashes[di] == anchor['doc_sha256']
            text = records[rid]['docs'][di]['text'][lo:hi]
            span = Span(di, records[rid]['docs'][di]['type'], lo, hi, text)
            oracle_quotes.append((di, lo, hi))
            oracle_context.extend(search._context(span))
            containing = [i for i, ranges in enumerate(search._contexts) if covers(ranges, anchor)]
            overlapping = [i for i, s in enumerate(search.chunks)
                if s.doc_index == di and s.start < hi and lo < s.end]
            ranks = {name: min((r[i] for r in rankings for i in containing if i in r), default=None)
                for name, rankings in rank_maps.items()}
            selected = result['diagnostics']['selected_candidates']
            skipped = result['diagnostics']['budget_skipped_candidates']
            anchors.append({'evidence_id': anchor['evidence_id'], 'quote': text, 'hit': covers(shown, anchor),
                'quote_tokens': search.token_cost([(di, lo, hi)]), 'original_range': [di, lo, hi],
                'chunk_overlap_count': len(overlapping), 'whole_quote_context_count': len(containing),
                'best_whole_quote_context_rank': ranks,
                'minimum_whole_quote_context_tokens': min((search.token_cost(search._contexts[i]) for i in containing), default=None),
                'covering_candidates_in_fused_order': [i for i in order if i in containing],
                'covering_candidates_selected': [i for i in selected if i in containing],
                'covering_candidates_budget_skipped': [i for i in skipped if i in containing]})
        detail = {'task_id': case['task_id'], 'record_id': rid, 'source_tokens': result['source_tokens'],
            'query_count': len(queries), 'candidate_chunks': len(order),
            'original_bundle_quote_lower_bound_tokens': search.token_cost(oracle_quotes),
            'oracle_expanded_bundle_tokens': search.token_cost(oracle_context), 'anchors': anchors}
        details.append(detail)
        print(json.dumps({'task_id': case['task_id'], 'misses': [a['evidence_id'] for a in anchors if not a['hit']],
            'oracle_expanded_bundle_tokens': detail['oracle_expanded_bundle_tokens']}, ensure_ascii=False), flush=True)
    assert source_manifest() == code
    report = {'kind': 'exposed_review_retrieval_failure_attribution', 'source_sha256': code,
        'input_sha256': sha(args.inputs), 'baseline_sha256': sha(args.baseline/'preregistered.json'),
        'control_sha256': sha(args.control/'retrieval_results.jsonl.gz'), 'controls_reproduced': True,
        'classification_labels_read': False, 'model_answers_read': False, 'new_gemma_calls': 0, 'gpu_used': False,
        'embedding_revision': BGE_REVISION, 'encoder': encoder.receipt, 'seconds': time.monotonic()-began,
        'limitations': 'Human review locations used for attribution only. Oracle costs are not retrieval performance or complete legal evidence. No query or ranking selection based on the oracle.',
        'details': details}
    with (args.output/'report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
