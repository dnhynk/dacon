"""Source-only condition retrieval ablation; no labels or Gemma inference."""
import argparse
from collections import defaultdict
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.runtime import source_manifest
from submission.pps.catalog_condition_search import query_plan
from submission.pps.notice_search import NoticeSearch, FACT_QUERIES
from submission.pps.purchase_reading import followup_queries, seed_reading


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path):
    with gzip.open(path, 'rt', encoding='utf8') as f:
        return list(map(json.loads, f))


def hit(anchor, result):
    return any(s['doc_index'] == anchor['doc_index'] and s['start'] <= anchor['start']
               and s['end'] >= anchor['end'] for s in result['spans'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('input', 'review', 'inventory', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--source-budget', type=int, default=4096)
    parser.add_argument('--selection-policy', choices=('rrf', 'facet_cover', 'evidence_cover', 'evidence_refill'), default='rrf')
    parser.add_argument('--retain-purchase-seed', action='store_true')
    parser.add_argument('--vectors-from', type=Path)
    args = parser.parse_args()
    if args.source_budget < 1:
        parser.error('Positive source budget required')
    args.output.mkdir(parents=True, exist_ok=False)
    def save(name, value):
        with (args.output/name).open('x', encoding='utf8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
    code = source_manifest()
    hashes = {str(p): sha(p) for p in (args.input, args.review, args.inventory, Path(__file__))}
    review = json.loads(args.review.read_text(encoding='utf8'))
    cases = {r['id']: r for r in review['cases']}
    records = {r['id']: r for r in read_rows(args.input) if r['id'] in cases}
    inventory = {r['id']: r['product'] for r in read_rows(args.inventory)}
    assert records.keys() == cases.keys() and records.keys() <= inventory.keys()
    plans = {}
    for rid, case in cases.items():
        rec = records[rid]
        assert case['doc_sha256'] == [hashlib.sha256(d['text'].encode()).hexdigest() for d in rec['docs']]
        for anchor in case['anchors']:
            assert rec['docs'][anchor['doc_index']]['text'][anchor['start']:anchor['end']] == anchor['quote']
        rows = [{'code': p['code'], 'name': p['name'], 'parent': '', 'condition': p['note']}
                for p in inventory[rid]['products'] if p['listed'] and p['note']]
        plans[rid] = {'rows': rows, 'plan': query_plan(rows)}
    arms = [('current', 'current', False)] + [
        (method + '_' + policy, method, policy == 'fields')
        for method in ('lexical', 'dense', 'hybrid') for policy in ('notes', 'fields')]
    save('preregistered.json', dict(source_sha256=code, input_sha256=hashes, arms=arms,
        plans=plans, source_budget=args.source_budget, selection_policy=args.selection_policy,
        retain_purchase_seed=args.retain_purchase_seed,
        vectors_from=str(args.vectors_from) if args.vectors_from else None,
        matched_spend_controls='Each fields arm also runs under its notes-arm actual source token expenditure.',
        labels_read=False, new_gemma_calls=0, gpu_used=False, source_review_kind=review['kind'],
        metrics={'any_related_sentence': 'Any original related anchor at its exact source coordinate.',
                 'known_context_bundle': 'All registered known source anchors, including scope/permission, returned.',
                 'full_decision_bundle': 'Only counted when the source review marks decisive information complete; missing information is never repaired by retrieval.',
                 'absence_scope': 'Returned and unreturned document coordinates, not keyword absence.'}))
    from transformers import AutoTokenizer
    from submission.pps.embeddings import BGEDenseEncoder
    import numpy as np
    started = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    encoder = BGEDenseEncoder(ROOT/'models/bge-m3')
    cache_hashes = {}
    if args.vectors_from:
        cache_report = json.loads((args.vectors_from/'report.json').read_text(encoding='utf8'))
        assert cache_report['encoder']['local_provenance'] == encoder.receipt['local_provenance']
        assert cache_report['encoder']['required_revision'] == encoder.receipt['required_revision']
    measurements = []
    with gzip.open(args.output/'results.jsonl.gz', 'wt', encoding='utf8') as stream:
        for rid, rec in records.items():
            case = cases[rid]
            search = NoticeSearch(rec, tokenizer, encoder)
            key = hashlib.sha256(rid.encode()).hexdigest()[:16]
            if args.vectors_from:
                meta_path = args.vectors_from/f'{key}.chunks.json'
                vec_path = args.vectors_from/f'{key}.vectors.npy'
                metadata = json.loads(meta_path.read_text(encoding='utf8'))
                assert metadata == {'id': rid, 'doc_sha256': search.doc_hashes,
                                    'chunks': [asdict(s) for s in search.chunks]}
                search.vectors = np.load(vec_path, allow_pickle=False)
                assert search.vectors.shape == (len(search.chunks), 1024)
                assert np.isfinite(search.vectors).all()
                cache_hashes.update({str(p): sha(p) for p in (meta_path, vec_path)})
            required = ()
            if args.retain_purchase_seed:
                seed = seed_reading(search, token_budget=args.source_budget)
                required = tuple((s['doc_index'], s['start'], s['end']) for s in seed['spans'])
            groups = {'specification': FACT_QUERIES['specification'],
                      'eligibility': FACT_QUERIES['eligibility'],
                      'purchase_candidates': followup_queries({'candidates': plans[rid]['rows']})}
            assert groups['purchase_candidates'] and plans[rid]['plan']['queries']
            fields = {**groups, 'designation_conditions': plans[rid]['plan']['queries']}
            spent = {}
            per_case_arms = arms + [(method+'_fields_matched', method, True)
                                  for method in ('lexical', 'dense', 'hybrid')]
            for arm, method, atomic in per_case_arms:
                budget = spent[method] if arm.endswith('_matched') else args.source_budget
                result = search.search(tuple(range(10, 19)), token_budget=budget, method=method,
                    **({} if method == 'current' else {'query_groups': fields if atomic else groups,
                        'selection_policy': args.selection_policy, 'required_ranges': required}))
                assert result['source_tokens'] <= budget and not result['coverage']['absence_verified']
                if arm.endswith('_notes'):
                    spent[method] = result['source_tokens']
                hits = {a['name']: hit(a, result) for a in case['anchors']}
                full_known = all(hits.values())
                measurement = dict(id=rid, arm=arm, source_budget=budget,
                    source_tokens=result['source_tokens'], hits=hits,
                    any_related_sentence=any(hits[a['name']] for a in case['anchors'] if a['role']=='related'),
                    known_context_bundle=full_known,
                    full_decision_bundle=full_known and case['decisive_information_complete'],
                    missing_information=case['missing_information'])
                measurements.append(measurement)
                stream.write(json.dumps({'measurement': measurement, 'retrieval': result}, ensure_ascii=False)+'\n')
                stream.flush()
            # This is evidence for the same current document only, not a shared
            # retrieval corpus. Keep vectors for later ablations without reruns.
            np.save(args.output/f'{key}.vectors.npy', search.vectors, allow_pickle=False)
            save(f'{key}.chunks.json', {'id': rid, 'doc_sha256': search.doc_hashes,
                                      'chunks': [asdict(s) for s in search.chunks]})
            print(json.dumps({'record': rid, 'measurements': len(measurements),
                              'seconds': time.monotonic()-started}), flush=True)
    summary = []
    grouped = defaultdict(list)
    for m in measurements:
        grouped[m['arm']].append(m)
    for arm, selected in grouped.items():
        summary.append(dict(arm=arm, cases=len(selected),
            **{key: sum(m[key] for m in selected) for key in ('any_related_sentence', 'known_context_bundle',
                'full_decision_bundle', 'source_tokens')},
            anchors_returned=sum(sum(m['hits'].values()) for m in selected),
            anchors_total=sum(len(m['hits']) for m in selected)))
    assert source_manifest() == code and all(sha(Path(p)) == h for p, h in {**hashes, **cache_hashes}.items())
    save('report.json', dict(source_sha256=code, summary=summary, measurements=measurements,
        encoder=encoder.receipt, reused_vector_sha256=cache_hashes, seconds=time.monotonic()-started, labels_read=False,
        gpu_used=False, new_gemma_calls=0, macro_f1=None,
        limitation='Source-only agent review, not human gold or unseen evaluation. Known-context recovery does not settle product scope, source ambiguity or missing attributes. Changed inputs require fresh Gemma for final-effect measurement.'))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
