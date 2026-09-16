"""Budget-matched lexical/dense/hybrid candidates on a fixed source cohort.

This reports candidate discovery and cost, not product identity or final F1.
The input source queries are frozen before BGE is loaded. No labels or model
answers are read. The static catalog is the only cross-notice vector asset.
"""
import argparse
import csv
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.catalog_candidates import CatalogCandidates
from submission.pps.products import CODE, scope_spans, non_task_scope_role
from submission.pps.retrieval import Span
from submission.pps.source_units import unitize
from submission.runtime import source_manifest
from tools.build_submission import build


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    with path.open('x', encoding='utf8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    code, started = source_manifest(), time.monotonic()
    snapshot = build(args.output/'source_snapshot.zip')
    selected = json.loads(args.cohort.read_text(encoding='utf8'))['source_selected_goods']
    wanted = {r['id'] for r in selected}
    recs = []
    with gzip.open(args.input, 'rt', encoding='utf8') as f:
        for record in map(json.loads, f):
            if record['id'] in wanted:
                recs.append(record)
    assert len(recs) == len(wanted)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    catalog_path = ROOT/'data_open/data/법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv'
    with catalog_path.open(encoding='utf-8-sig', newline='') as f:
        catalog_rows = list(csv.DictReader(f))
    queries = {}
    for rec in recs:
        scopes = [s for s in scope_spans(rec) if non_task_scope_role(s['text']) is None]
        spans = [Span(s['doc_index'], rec['docs'][s['doc_index']]['type'], s['start'], s['end'], s['text']) for s in scopes]
        units = [s for s in unitize(spans) if len(s.text.strip()) >= 4]
        queries[rec['id']] = {'source_units': list(map(asdict, units)),
            'queries': [s.text for s in units], 'source_tokens': sum(len(tokenizer.encode(s.text, add_special_tokens=False)) for s in spans),
            'required_codes': sorted(set(CODE.findall(str(rec['meta'].get('세부품명번호목록') or ''))))}
        if not units:
            raise ValueError('No actual source query for '+rec['id'])
    save(args.output/'preregistered.json', {'source_sha256': code, 'input_sha256': sha(args.input),
        'source_snapshot': snapshot['source_fingerprint'], 'tool_sha256': sha(__file__),
        'cohort_sha256': sha(args.cohort), 'catalog_file_sha256': sha(catalog_path),
        'catalog_budget': 2048, 'max_candidates': 24,
        'methods': ['lexical', 'dense', 'hybrid'], 'queries': queries,
        'query_policy': 'Identical original scope fields and units across arms; no title-only identity gate.',
        'scope_limit': 'Current scope spans can omit item tables; candidate results are not complete-purchase verification.',
        'labels_read': False, 'new_gemma_calls': 0, 'gpu_used': False,
        'no_retrieval_failure_as_absence': True})
    from submission.pps.embeddings import BGEDenseEncoder
    encoder = BGEDenseEncoder(ROOT/'models/bge-m3')
    index = CatalogCandidates(catalog_rows, encoder)
    results = []
    for n, rec in enumerate(recs):
        q = queries[rec['id']]
        methods = ['lexical', 'dense', 'hybrid']
        methods = methods[n % 3:] + methods[:n % 3]
        for method in methods:
            began = time.monotonic()
            result = index.search(q['queries'], tokenizer, token_budget=2048, method=method,
                                  required_codes=q['required_codes'], max_candidates=24)
            result.update(record_id=rec['id'], seconds=time.monotonic()-began,
                          original_source_tokens=q['source_tokens'])
            assert len(tokenizer.encode(index.render_rows(result['candidates']), add_special_tokens=False)) == result['catalog_tokens'] <= 2048
            results.append(result)
        print(json.dumps({'completed_notices': n+1, 'total': len(recs),
            'seconds': round(time.monotonic()-started, 2)}, ensure_ascii=False), flush=True)
    import numpy as np
    np.save(args.output/'static_catalog_vectors.npy', index._vectors, allow_pickle=False)
    save(args.output/'static_catalog_rows.json', {'catalog_sha256': index.catalog_sha256, 'rows': index.rows})
    save(args.output/'results.json', results)
    assert source_manifest() == code
    report = {'notices': len(recs), 'comparisons': len(results), 'catalog_rows': len(index.rows),
        'complete_groups': len(index.complete_groups()), 'encoder': encoder.receipt,
        'static_vectors_sha256': sha(args.output/'static_catalog_vectors.npy'),
        'source_sha256': code, 'seconds': time.monotonic()-started, 'gpu_used': False,
        'score_measured': False, 'queries_identical_across_arms': True,
        'results': [{'id': rid, 'methods': {r['method']: [c['code'] for c in r['candidates']]
                     for r in results if r['record_id'] == rid}} for rid in sorted(wanted)]}
    save(args.output/'report.json', report)
    print(json.dumps({k: v for k, v in report.items() if k not in {'results', 'source_sha256'}}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
