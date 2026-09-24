"""Equal-source-budget comparisons on a source-selected purchase cohort.

The review manifest is frozen before retrieval. Required evidence bundles,
missing-source scope and final applicability are deliberately separate fields.
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
from submission.pps.notice_search import NoticeSearch
from submission.pps.purchase_tables import purchase_tables
from submission.runtime import source_manifest
from tools.build_submission import build

QUERIES = [
    '이번 계약으로 구입하고 납품하는 물품 전체의 품명, 종류와 수량을 나열한 목록, 규격, 구성품',
    '납품 제품의 실제 용도와 재질, 성능과 사양, 개별 장비와 부속품의 관계',
    '제시한 제품이나 규격을 대체하거나 동등품을 납품할 수 있는 조건, 공통 적용사항과 예외',
]
ARMS = [('current', 'local', 'rrf'), ('lexical', 'local', 'rrf'),
        ('dense', 'local', 'rrf'), ('hybrid', 'local', 'rrf'),
        ('hybrid', 'atomic_purchase', 'rrf'), ('hybrid', 'atomic_purchase', 'evidence_refill')]


def save(path, data):
    with path.open('x', encoding='utf8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def included(result, ref):
    return any(s['doc_index'] == ref['doc_index'] and s['start'] <= ref['start']
               and s['end'] >= ref['end'] for s in result['spans'])


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--review', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    code, started = source_manifest(), time.monotonic()
    snapshot = build(args.output/'source_snapshot.zip')
    review = json.loads(args.review.read_text(encoding='utf8'))
    wanted = {r['id']: r for r in review['cases']}
    with gzip.open(args.input, 'rt', encoding='utf8') as f:
        recs = [r for r in map(json.loads, f) if r['id'] in wanted]
    assert len(recs) == len(wanted)
    for rec in recs:
        case = wanted[rec['id']]
        assert [hashlib.sha256(d['text'].encode()).hexdigest() for d in rec['docs']] == case['doc_sha256']
        for ref in case['related'] + case['required_bundle']:
            assert rec['docs'][ref['doc_index']]['text'][ref['start']:ref['end']] == ref['quote']
    save(args.output/'preregistered.json', {'source_manifest': code, 'input_sha256': sha(args.input),
        'source_snapshot': snapshot['source_fingerprint'], 'tool_sha256': sha(__file__),
        'review_sha256': sha(args.review), 'queries': QUERIES, 'arms': ARMS, 'budgets': [2048, 4096],
        'items': [9, 10, 11, 18], 'labels_read': False, 'gpu_used': False, 'new_gemma_calls': 0,
        'primary_comparisons': 'current/lexical/dense/hybrid; context/packing controls are separate',
        'source_tokens_include_headers_conditions_and_all_expansion': True})
    from transformers import AutoTokenizer
    from submission.pps.embeddings import BGEDenseEncoder
    tokenizer = AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    encoder = BGEDenseEncoder(ROOT/'models/bge-m3')
    results = []
    for n, rec in enumerate(recs):
        case = wanted[rec['id']]
        local = NoticeSearch(rec, tokenizer, encoder)
        atomic = NoticeSearch(rec, tokenizer, encoder, table_context='atomic_purchase')
        tables = [{'doc_index': di, **t} for di, d in enumerate(rec['docs']) for t in purchase_tables(d['text'])]
        arms = ARMS[n % len(ARMS):] + ARMS[:n % len(ARMS)]
        for budget in (2048, 4096):
            for method, context, policy in arms:
                tool = local if context == 'local' else atomic
                # Only the current notice's document embeddings and query
                # features are reused between context/packing controls.
                other = atomic if tool is local else local
                if tool.vectors is None and other.vectors is not None:
                    tool.vectors = other.vectors
                    tool._queries = dict(other._queries)
                began = time.monotonic()
                result = tool.search([9, 10, 11, 18], token_budget=budget, method=method,
                    queries=None if method == 'current' else QUERIES, selection_policy=policy)
                result['arm'] = '/'.join((method, context, policy))
                result['seconds'] = time.monotonic()-began
                result['review'] = {'provenance': case['provenance'],
                    'related_sentence_found': any(included(result, r) for r in case['related']),
                    'known_required_bundle_found': bool(case['required_bundle']) and all(included(result, r) for r in case['required_bundle']),
                    'full_decision_bundle_obtainable': case['full_decision_bundle_obtainable'],
                    'full_decision_bundle_found': case['full_decision_bundle_obtainable'] and bool(case['required_bundle']) and all(included(result, r) for r in case['required_bundle']),
                    'missing_or_uncertain_scope': case['missing_or_uncertain_scope'],
                    'final_identity_or_violation_certified': False}
                result['purchase_tables'] = [{k: v for k, v in t.items() if k != 'text'} for t in tables]
                results.append(result)
        print(json.dumps({'notices': n+1, 'total': len(recs), 'seconds': round(time.monotonic()-started, 2)}), flush=True)
    save(args.output/'results.json', results)
    assert code == source_manifest()
    summary = []
    for budget in (2048, 4096):
        for arm in ARMS:
            name = '/'.join(arm)
            group = [r for r in results if r['arm'] == name and r['source_token_budget'] == budget]
            summary.append({'budget': budget, 'arm': name, 'notices': len(group),
                'related_sentence_hits': sum(r['review']['related_sentence_found'] for r in group),
                'known_required_bundle_hits': sum(r['review']['known_required_bundle_found'] for r in group),
                'full_decision_bundle_hits': sum(r['review']['full_decision_bundle_found'] for r in group),
                'full_decision_bundle_obtainable': sum(r['review']['full_decision_bundle_obtainable'] for r in group),
                'source_tokens': sum(r['source_tokens'] for r in group),
                'all_provided_text_returned': sum(r['coverage']['all_provided_text_returned'] for r in group)})
    report = {'source_manifest': code, 'seconds': time.monotonic()-started, 'comparisons': len(results),
        'encoder': encoder.receipt, 'summary': summary, 'official_gold': False, 'final_F1_measured': False,
        'missing_scope_is_never_an_absence_claim': True, 'gpu_used': False, 'new_gemma_calls': 0}
    save(args.output/'report.json', report)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
