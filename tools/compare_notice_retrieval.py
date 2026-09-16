"""Actual fixed-model retrieval measurement; no Gemma inference or label scoring.

The reviewed cases are exposed development supervision, not an independent gold
set. Every quote/offset/hash is verified before any model or retrieval runs.
"""
from __future__ import annotations

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
from submission.pps.embeddings import BGEDenseEncoder, BGE_MODEL, BGE_REVISION
from submission.pps.notice_search import NoticeSearch, factual_queries

# Topic assignment only; no answer, case-specific query or required quote goes
# into search. Fixed before the first ranking experiment.
CASE_ITEMS = {'HR-01': (9,), 'HR-02': (10, 11, 16, 18), 'HR-03': (11, 16, 18),
    'HR-04': (20,), 'HR-05': (20,), 'HR-06': (20,), 'HR-07': (11, 13, 16, 18),
    'HR-08': (11, 16, 18), 'HR-09': (19,), 'HR-10': (20,), 'HR-11': (20,), 'HR-12': (20,)}
ARMS = {'A_current': ('current', True), 'A_lexical_context': ('lexical', True),
        'B_dense_context': ('dense', True), 'C_hybrid_context': ('hybrid', True),
        'C_hybrid_no_context': ('hybrid', False)}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def hit(evidence, result):
    """Legacy single-span quote availability, not union-of-excerpts reading coverage.

    Historical receipts retain this definition. For new reading/bundle
    comparisons use tools.source_coverage.covered with the original record.
    """
    return any(s['doc_index'] == evidence['doc_index'] and s['start'] <= evidence['start']
               and s['end'] >= evidence['end'] for s in result['spans'])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--review', type=Path, required=True)
    p.add_argument('--tokenizer', type=Path, required=True)
    p.add_argument('--embed-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--budgets', type=int, nargs='+', default=[2048, 4096, 8192])
    args = p.parse_args(argv)
    if not args.budgets or any(b <= 0 for b in args.budgets) or len(set(args.budgets)) != len(args.budgets):
        p.error('budgets must be distinct positive integers')
    args.output.mkdir(parents=True, exist_ok=False)
    began = time.monotonic()
    review = json.loads(args.review.read_text(encoding='utf-8'))
    answers = review['answers']
    assert {a['task_id'] for a in answers} == set(CASE_ITEMS)
    wanted = {a['record_id'] for a in answers}
    recs = {r['id']: r for r in records(args.input) if r['id'] in wanted}
    assert recs.keys() == wanted
    bundle_manifest = []
    for a in answers:
        rec = recs[a['record_id']]
        for e in a['evidence']:
            doc = rec['docs'][e['doc_index']]
            assert doc['doc_id'] == e['doc_id']
            assert hashlib.sha256(doc['text'].encode()).hexdigest() == e['doc_sha256']
            assert doc['text'][e['start']:e['end']] == e['quote']
        bundle_manifest.append({'task_id': a['task_id'], 'record_id': a['record_id'],
            'resolution': a['resolution'], 'items': CASE_ITEMS[a['task_id']],
            'queries': factual_queries(CASE_ITEMS[a['task_id']]),
            'required_bundle': [{k: e[k] for k in ('evidence_id', 'doc_index', 'doc_id', 'doc_sha256', 'start', 'end', 'role')}
                                for e in a['evidence']],
            'unresolved_information': a.get('missing_information', []),
            'scope_limits': a.get('scope_limits', [])})
    source_files = [ROOT / 'submission/pps' / n for n in ('notice_search.py', 'retrieval.py', 'embeddings.py')]
    source_files.append(Path(__file__))
    freeze = {'experiment': 'notice_retrieval_v1', 'input_sha256': digest(args.input),
        'review_sha256': digest(args.review), 'source_sha256': {str(p.relative_to(ROOT)): digest(p) for p in source_files},
        'tokenizer_sha256': digest(args.tokenizer / 'tokenizer.json'),
        'embedding_model': BGE_MODEL, 'embedding_revision': BGE_REVISION,
        'budgets': args.budgets, 'primary_budget': 4096, 'arms': ARMS,
        'metrics': {
            'any_review_anchor_hit': 'At least one complete original reviewed quotation returned at its exact original location.',
            'complete_review_bundle': 'All current support/context/limit quotations returned. Conservative measured proxy for decision evidence, not semantic certification.',
            'anchor_recall': 'Fraction of required quotations fully returned; per-quote and per-case denominators are separate.',
            'absence_scope': 'Actual returned document ranges and provided-source fraction; indexed documents are not treated as reviewed. No absence labels inferred.',
            'budget': 'Sum of Gemma tokens in coalesced original-source ranges, no special tokens. Same caps for all arms; actual spending also reported.'},
        'cases': bundle_manifest, 'development_cases': True, 'official_gold': False,
        'held_out_cases': False, 'classification_labels_read': False, 'gemma_calls': 0,
        'tuning_on_this_run': False, 'case_answer_retrieval': False}
    save(args.output / 'preregistered.json', freeze)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True, trust_remote_code=False)
    encoder = BGEDenseEncoder(args.embed_dir, threads=7, batch_size=8, max_length=512)
    result_rows = []
    grouped = defaultdict(list)
    for a in answers:
        grouped[a['record_id']].append(a)
    with gzip.open(args.output / 'retrieval_results.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for record_id, cases in grouped.items():
            record_began = time.monotonic()
            search = NoticeSearch(recs[record_id], tokenizer, encoder)
            for a in cases:
                for budget in args.budgets:
                    for arm, (method, expand) in ARMS.items():
                        result = search.search(CASE_ITEMS[a['task_id']], token_budget=budget,
                                               method=method, expand_context=expand)
                        assert result['source_tokens'] <= budget
                        hits = {e['evidence_id']: hit(e, result) for e in a['evidence']}
                        row = {'task_id': a['task_id'], 'record_id': record_id, 'arm': arm,
                            'budget': budget, 'resolution': a['resolution'],
                            'source_tokens': result['source_tokens'], 'evidence_hits': hits,
                            'any_review_anchor_hit': any(hits.values()), 'complete_review_bundle': all(hits.values()),
                            'coverage': result['coverage'],
                            'source_fraction': result['coverage']['returned_chars'] / max(1, result['coverage']['provided_chars'])}
                        result_rows.append(row)
                        stream.write(json.dumps({'measurement': row, 'retrieval': result}, ensure_ascii=False) + '\n')
                        stream.flush()
            # Per-notice vector cache is an evidence artifact; never a shared
            # evaluation corpus. The runtime encoder does not load these files.
            import numpy as np
            safe_name = hashlib.sha256(record_id.encode()).hexdigest()[:16]
            np.save(args.output / f'{safe_name}.vectors.npy', search.vectors, allow_pickle=False)
            save(args.output / f'{safe_name}.chunks.json', {'record_id': record_id,
                'documents_sha256': search.doc_hashes, 'chunks': [asdict(s) for s in search.chunks]})
            print(json.dumps({'record': record_id, 'completed_cases': len(cases), 'chunks': len(search.chunks),
                'seconds': time.monotonic() - record_began, 'encoder_seconds': encoder.receipt['encode_seconds']}, ensure_ascii=False), flush=True)
            save(args.output / 'progress.json', {'measurements': len(result_rows), 'encoder': encoder.receipt})
    aggregates = []
    for budget in args.budgets:
        for arm in ARMS:
            selected = [r for r in result_rows if r['budget'] == budget and r['arm'] == arm]
            n = len(selected)
            aggregates.append({'budget': budget, 'arm': arm, 'cases': n,
                'any_review_anchor_hit': sum(r['any_review_anchor_hit'] for r in selected),
                'complete_review_bundle': sum(r['complete_review_bundle'] for r in selected),
                'anchors_returned': sum(sum(r['evidence_hits'].values()) for r in selected),
                'anchors_total': sum(len(r['evidence_hits']) for r in selected),
                'source_tokens_mean': statistics.mean(r['source_tokens'] for r in selected),
                'source_tokens_min': min(r['source_tokens'] for r in selected),
                'source_tokens_max': max(r['source_tokens'] for r in selected),
                'source_fraction_mean': statistics.mean(r['source_fraction'] for r in selected),
                'all_provided_text_returned_cases': sum(r['coverage']['all_provided_text_returned'] for r in selected)})
    assert all(digest(ROOT / name) == sha for name, sha in freeze['source_sha256'].items()), 'Source changed while running'
    import psutil
    report = {'aggregates': aggregates, 'cases': result_rows, 'encoder': encoder.receipt,
        'seconds': time.monotonic() - began, 'process_memory': psutil.Process().memory_info()._asdict(),
        'source_unchanged': True, 'gemma_calls': 0, 'macro_f1': None,
        'limitations': ['12 exposed development cases, 11 notices; no independent generalization or legal-gold claim.',
            'All reviewed quotation groups are required; their recovery does not resolve unobserved legal/technical conditions.',
            'Same source-token caps, actual token use reported; context control isolates expansion from dense ranking.',
            'CPU throughput is this Windows CPU with 7 threads, not measured L40S evaluation-server throughput.']}
    save(args.output / 'report.json', report)
    print(json.dumps({'aggregates': aggregates, 'seconds': report['seconds'], 'gemma_calls': 0}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
