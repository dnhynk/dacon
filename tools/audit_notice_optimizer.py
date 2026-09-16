"""Source-only optimizer/cost audit on a previously frozen notice cohort.

No labels, Gemma answers or human evidence locations are loaded. Relevance
objective and source coverage are diagnostics, never an accuracy measurement.
Document BGE vectors are freshly computed on CPU, once per current notice.
"""
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
from submission.pps.evidence_selection import EvidenceObjective
from submission.pps.notice_search import NoticeSearch, factual_queries
from submission.runtime import source_manifest


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, obj):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cohort = json.loads(args.cohort.read_text(encoding='utf-8'))
    assert sha(args.inputs) == cohort['input_sha256']
    with gzip.open(args.inputs, 'rt', encoding='utf-8') as stream:
        records = {r['id']: r for line in stream if (r := json.loads(line))['id'] in cohort['records']}
    assert set(records) == set(cohort['records'])
    args.output.mkdir(parents=True, exist_ok=False)
    source, began = source_manifest(), time.monotonic()
    policies = ['rrf', 'evidence_cover', 'evidence_refill']
    save(args.output/'preregistered.json', {
        'source_sha256': source, 'tool_sha256': sha(Path(__file__)),
        'cohort_manifest_sha256': sha(args.cohort), 'input_sha256': sha(args.inputs),
        'record_ids': cohort['records'], 'items': cohort['items'], 'source_budgets': cohort['source_budgets'],
        'policies': policies, 'embedding_revision': BGE_REVISION,
        'embedding_device': 'cpu', 'document_embedding_cache_reused': False,
        'query_policy': 'unchanged fixed fact questions; query embeddings cached within each notice',
        'selection_timing': 'fresh source-token cost cache per policy, rankings prepared separately',
        'policy_order': 'rotate by record, item and budget index',
        'labels_read': False, 'answers_read': False, 'human_evidence_locations_read': False,
        'new_gemma_calls': 0, 'gpu_used': False, 'legal_accuracy_measured': False,
        'hypothesis': 'Bounded removal and refill improves feasible source relevance objective without losing the incumbent; measure actual additional selection cost and full document encoding cost outside the exposed review panel.'})
    for path in (Path(__file__), ROOT/'submission/pps/evidence_selection.py', ROOT/'submission/pps/notice_search.py'):
        with (args.output/path.name).open('xb') as stream:
            stream.write(path.read_bytes())
    import numpy as np
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    encoder = BGEDenseEncoder(ROOT/'models/bge-m3')
    measurements, encoding = [], []
    with gzip.open(args.output/'retrieval_results.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for ri, rid in enumerate(cohort['records']):
            started = time.monotonic()
            search = NoticeSearch(records[rid], tokenizer, encoder)
            initialized = time.monotonic()
            search.vectors = encoder.encode([s.text for s in search.chunks])
            encoded = time.monotonic()
            key = hashlib.sha256(rid.encode()).hexdigest()[:16]
            save(args.output/(key+'.chunks.json'), {'record_id': rid,
                'documents_sha256': search.doc_hashes, 'chunks': [asdict(s) for s in search.chunks]})
            np.save(args.output/(key+'.vectors.npy'), search.vectors, allow_pickle=False)
            receipt = {'record_id': rid, 'chunks': len(search.chunks),
                'initialize_seconds': initialized-started, 'document_encode_seconds': encoded-initialized,
                'chunks_sha256': sha(args.output/(key+'.chunks.json')),
                'vectors_sha256': sha(args.output/(key+'.vectors.npy'))}
            print(json.dumps({'record_id': rid, 'document_encode_seconds': receipt['document_encode_seconds']}), flush=True)
            query_seconds = 0.
            for ti, item in enumerate(cohort['items']):
                t0 = time.monotonic()
                families = [search._lexical_lists((item,)), search._dense_lists(factual_queries((item,)))]
                query_seconds += time.monotonic() - t0
                objective = EvidenceObjective(search, families, search._fuse(families))
                for bi, budget in enumerate(cohort['source_budgets']):
                    shift = (ri + ti + bi) % len(policies)
                    results = {}
                    for policy in policies[shift:] + policies[:shift]:
                        search._token_cost.cache_clear()
                        t0 = time.monotonic()
                        result = search.search((item,), method='hybrid', token_budget=budget, selection_policy=policy)
                        seconds = time.monotonic() - t0
                        ranges = tuple((s['doc_index'], s['start'], s['end']) for s in result['spans'])
                        utility = objective.value(objective.covered(ranges))
                        assert search.token_cost(ranges) == result['source_tokens'] <= budget
                        assert not result['coverage']['absence_verified']
                        assert all(s['text'] == records[rid]['docs'][s['doc_index']]['text'][s['start']:s['end']]
                            for s in result['spans'])
                        packing = result['diagnostics'].get('packing', {})
                        m = {'record_id': rid, 'item': item, 'budget': budget, 'policy': policy,
                            'source_tokens': result['source_tokens'], 'utility': utility, 'seconds': seconds,
                            'candidate_chunks': result['diagnostics']['candidate_chunks'],
                            'refill_evaluations': packing.get('refill_evaluations', 0),
                            'refill_work_limited_trials': sum(t['work_limit_reached'] for t in packing.get('refill_trials', [])),
                            'refill_accepted': any(t['action'] == 'refill' for t in packing.get('selection_trace', []))}
                        results[policy] = m
                        measurements.append(m)
                        stream.write(json.dumps({'measurement': m, 'retrieval': result}, ensure_ascii=False)+'\n')
                    assert results['evidence_refill']['utility'] + 1e-12 >= results['evidence_cover']['utility']
            receipt['query_encode_and_rank_seconds'] = query_seconds
            receipt['record_total_seconds'] = time.monotonic() - started
            encoding.append(receipt)
            stream.flush()
            print(json.dumps({'record_id': rid, 'total_seconds': receipt['record_total_seconds'],
                'completed': ri + 1, 'records': len(records)}), flush=True)
    assert source == source_manifest()
    aggregate = []
    for policy in policies:
        rows = [m for m in measurements if m['policy'] == policy]
        aggregate.append({'policy': policy, 'searches': len(rows),
            'mean_source_tokens': statistics.mean(m['source_tokens'] for m in rows),
            'mean_utility': statistics.mean(m['utility'] for m in rows),
            'mean_selection_seconds': statistics.mean(m['seconds'] for m in rows),
            'max_selection_seconds': max(m['seconds'] for m in rows),
            'refills_accepted': sum(m['refill_accepted'] for m in rows),
            'refill_work_limited_trials': sum(m['refill_work_limited_trials'] for m in rows)})
    report = {'aggregates': aggregate, 'encoding': encoding, 'measurements': measurements,
        'encoder': encoder.receipt, 'total_seconds': time.monotonic()-began,
        'labels_read': False, 'answers_read': False, 'gpu_used': False, 'new_gemma_calls': 0,
        'not_review_panel': True, 'held_out_legal_accuracy': False, 'objective_nonregression': True,
        'limitations': 'Source-only development cohort excludes the 11 human-review notices. No new relevance gold, legal gold, absence verification or F1 measurement. CPU embedding and selection costs are local measurements, not L40S runtime certification.'}
    save(args.output/'report.json', report)
    print(json.dumps(aggregate, indent=2), flush=True)


if __name__ == '__main__':
    main()
