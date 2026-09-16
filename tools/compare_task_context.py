"""Compare Q task reading at the exact current-notice raw source token cap.

Source structure, human quotations/bundles, provided-document reading coverage,
and prompt capacity are separate measures. No classification labels or saved
model answers are read. This tool cannot measure a new input's final F1.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.catalog_scope import ITEMS, QUERIES, prompt
from submission.pps.knowledge import Knowledge
from submission.pps.notice_search import NoticeSearch
from submission.pps.qualification import inventory, qualification_facts
from submission.pps.source_units import unitize
from submission.pps.specialist_packets import packet
from submission.pps.task_context import TaskContextSearch
from submission.runtime import Journal, source_manifest, sha256
from tools.run_runtime_revival import read, rows, verify_prepared
from tools.source_coverage import covered


def document_vectors(search, encoder, output, cache=None):
    """Research replay cache bound to this exact notice and encoder identity.

    This cache contains no labels, answers or corpus-level statistics. It is
    outside the submission runtime and never substitutes another notice's text.
    Save each completed notice so a later context-only change need not run the
    same transformer again. Every reused file is checked against its receipt.
    """
    import numpy as np
    key = hashlib.sha256(search.rec['id'].encode()).hexdigest()[:16]
    identity = dict(record_id=search.rec['id'], documents_sha256=search.doc_hashes,
        chunks=[asdict(s) for s in search.chunks],
        encoder={k: encoder.receipt[k] for k in ('model', 'required_revision', 'dtype',
                                                'pooling', 'tokenizer_sha256', 'max_length')},
        encoder_source_sha256=sha256(ROOT / 'submission/pps/embeddings.py'))
    reused = False
    meta_path = None if cache is None else cache / (key + '.json')
    if meta_path is not None and meta_path.is_file():
        meta = read(meta_path)
        vector_path = cache / (key + '.npy')
        if meta['identity'] != identity or sha256(vector_path) != meta['vectors_sha256']:
            raise ValueError('Cached document vectors differ from the current notice or model')
        vectors = np.load(vector_path, allow_pickle=False)
        reused = True
    else:
        vectors = encoder.encode([s.text for s in search.chunks])
    if (vectors.shape != (len(search.chunks), 1024) or not np.isfinite(vectors).all()
            or not np.allclose(np.linalg.norm(vectors, axis=1), 1., atol=1e-5)):
        raise ValueError('Invalid current-notice BGE document vectors')
    output.mkdir(parents=True, exist_ok=True)
    vector_path = output / (key + '.npy')
    with vector_path.open('xb') as stream:
        np.save(stream, vectors, allow_pickle=False)
    receipt = dict(identity=identity, vectors_sha256=sha256(vector_path), reused=reused,
                   reused_receipt=None if not reused else str(meta_path))
    with (output / (key + '.json')).open('x', encoding='utf8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    search.vectors = vectors
    return dict(record_id=search.rec['id'], reused=reused,
                receipt=(output / (key + '.json')).as_posix(), vectors_sha256=receipt['vectors_sha256'])


def reading(record, selected, fields, sections, review):
    spans = selected['spans']
    observed = [dict(**field, complete=covered(record, spans, field)) for field in fields]
    human = []
    for case in review['cases']:
        if case['record_id'] != record['id'] or not set(case['items']) & set(ITEMS):
            continue
        hits = {}
        for ref in case['required_bundle']:
            doc = record['docs'][ref['doc_index']]
            if (doc['doc_id'] != ref['doc_id']
                    or hashlib.sha256(doc['text'].encode()).hexdigest() != ref['doc_sha256']):
                raise ValueError('Human review source identity changed')
            hits[ref['evidence_id']] = covered(record, spans, ref)
        supports = [r for r in case['required_bundle'] if r['role'] == 'support']
        human.append(dict(task_id=case['task_id'], hits=hits,
            related_sentence=any(hits[r['evidence_id']] for r in supports),
            known_bundle=bool(hits) and all(hits.values()),
            resolved_bundle=case['resolution'] == 'resolved' and bool(hits) and all(hits.values()),
            resolution=case['resolution'], unresolved_information=case['unresolved_information']))
    return dict(task_fields=observed, human=human,
        eligibility_sections=[dict(evidence=s['evidence'], complete=covered(record, spans, s['evidence']))
                              for s in sections],
        absence_verified=False, semantic_task_identity_certified=False)


def compare(prepared, review_path, output, *, method='lexical', vector_cache=None):
    from transformers import AutoTokenizer
    verify_prepared(prepared, require_current_source=False)
    journal = Journal(output)
    began, source = time.monotonic(), source_manifest()
    records = rows(prepared / 'current_inputs.jsonl.gz')
    controls = {p['record_id']: p for p in rows(prepared / 'primary_packets.jsonl.gz') if p['family'] == 'Q'}
    review = read(review_path)
    tokenizer = AutoTokenizer.from_pretrained(ROOT / 'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    products = Knowledge(ROOT / 'data_open/data').products
    journal.save('preregistered.json', dict(source_sha256=source, tool_sha256=sha256(__file__),
        prepared_sha256=sha256(prepared / 'input_freeze.json'), review_sha256=sha256(review_path),
        record_count=len(records), active_Q_count=len(controls), method=method,
        arms=['shared_source', 'task_queries', 'task_context', 'task_reserved'],
        prompt_profiles=['unchanged_Q', 'complete_field_groups'],
        budget='Exact current Q raw-source token count. Baseline spans, messages, tokens and S numbers remain unchanged. Candidate may not spend more.',
        hypotheses=['Task questions can recover actual work beyond eligibility-dominated shared search.',
                    'Complete field context avoids selection of just a label or value.',
                    'Reserving complete task context can improve coverage but may displace exceptions; measure both.'],
        classification_labels_read=False, saved_model_answers_read=False, new_gemma_calls=0,
        gpu_used=False, official_score=None,
        vector_cache=None if vector_cache is None else str(vector_cache)))
    journal.save('task_context_source.json', {p: (ROOT / 'submission' / p).read_text(encoding='utf8')
        for p in ('pps/task_context.py', 'pps/task_scope.py', 'pps/catalog_scope.py', 'pps/specialist_packets.py')})
    (output / 'compare_task_context.py').write_bytes(Path(__file__).read_bytes())
    encoder = None
    if method == 'hybrid':
        from submission.pps.embeddings import BGEDenseEncoder
        encoder = BGEDenseEncoder(ROOT / 'models/bge-m3')
    results, offered, measurements, caches, totals = [], [], [], [], defaultdict(lambda: defaultdict(int))
    for ordinal, rec in enumerate(records, 1):
        if rec['id'] not in controls:
            continue
        q = controls[rec['id']]
        base = NoticeSearch(rec, tokenizer, encoder)
        enhanced = TaskContextSearch(rec, tokenizer, encoder)
        assert base.chunks == enhanced.chunks
        if method == 'hybrid':
            caches.append(document_vectors(base, encoder, output / 'vectors', vector_cache))
        cap = q['source_search']['source_tokens']
        if not cap:
            raise ValueError('Cannot compare an empty Q source selection')
        selections = {'shared_source': copy.deepcopy(q['source_search'])}
        selections['task_queries'] = base.search(ITEMS, token_budget=cap, method=method,
            queries=QUERIES, selection_policy='evidence_cover')
        # These caches are current-notice only. Both paths use identical chunk
        # texts and vectors; no reuse of predictions or other notice content.
        enhanced.vectors, enhanced._queries = base.vectors, base._queries
        selections['task_context'] = enhanced.select(cap, method=method, reserve_fields=False)
        selections['task_reserved'] = enhanced.select(cap, method=method, reserve_fields=True)
        sections = qualification_facts(rec, inventory(rec))['eligibility_sections']
        for arm, selected in selections.items():
            assert selected['source_tokens'] <= cap
            observed = reading(rec, selected, enhanced.fields, sections, review)
            results.append(dict(record_id=rec['id'], arm=arm, cap=cap, selection=selected, reading=observed))
            row = dict(record_id=rec['id'], arm=arm, cap=cap, source_tokens=selected['source_tokens'],
                task_fields=len(observed['task_fields']),
                task_fields_read=sum(f['complete'] for f in observed['task_fields']),
                with_any_complete_field=any(f['complete'] for f in observed['task_fields']),
                eligibility_sections=len(sections),
                eligibility_sections_read=sum(s['complete'] for s in observed['eligibility_sections']),
                human_cases=len(observed['human']),
                human_related_sentence=sum(h['related_sentence'] for h in observed['human']),
                human_known_bundle=sum(h['known_bundle'] for h in observed['human']),
                human_resolved_bundle=sum(h['resolved_bundle'] for h in observed['human']),
                provided_chars=sum(len(d['text']) for d in rec['docs']),
                returned_chars=sum(len(s['text']) for s in selected['spans']))
            for grouped in (False, True):
                body = prompt(rec, selected, tokenizer, products, explain_contract=True, task_groups=grouped)
                if arm == 'shared_source' and not grouped:
                    assert body['messages'] == q['messages']
                    assert body['token_ids'] == q['token_ids']
                    assert [asdict(s) for s in body['spans']] == q['spans']
                key = 'groups' if grouped else 'plain'
                fits = len(body['token_ids']) + 1536 + 32 <= 16384
                row[key + '_prompt_tokens'] = len(body['token_ids'])
                row[key + '_fits'] = fits
                if grouped:
                    row['groups'] = len(body['task_field_groups'])
                    row['selectable_groups'] = sum(g['selectable'] for g in body['task_field_groups'])
                if fits:
                    offered.append(dict(record_id=rec['id'], arm=arm, grouped=grouped,
                        packet=packet(rec, body, selected, family='Q', profile='Q10', fmt='catalog_scope',
                                      output_tokens=1536, max_model_len=16384)))
            measurements.append(row)
            for k, v in row.items():
                if type(v) in (int, bool):
                    totals[arm][k] += v
        journal.save('progress.json', dict(last_record=rec['id'], records_seen=ordinal,
            active_records_finished=len(measurements) // 4, seconds=time.monotonic() - began))
        # Immutable per-notice checkpoint; interrupted comparisons stay partial
        # but preserve source selection, offered inputs and expensive vectors.
        checkpoint = 'notice_' + hashlib.sha256(rec['id'].encode()).hexdigest()[:16] + '.json'
        journal.save(checkpoint, dict(record_id=rec['id'], results=results[-4:],
            measurements=measurements[-4:], offered=[v for v in offered if v['record_id'] == rec['id']]))
        print(json.dumps(dict(id=rec['id'], finished=len(measurements) // 4,
            fields=len(enhanced.fields), cap=cap)), flush=True)
    assert source_manifest() == source
    assert sha256(__file__) == read(output / 'preregistered.json')['tool_sha256']
    assert len(measurements) == 4 * len(controls)
    journal.rows('selections.jsonl.gz', results)
    journal.rows('offered_packets.jsonl.gz', offered)
    report = dict(records=len(records), Q_records=len(controls), method=method,
        measurements=measurements, totals={k: dict(v) for k, v in totals.items()},
        source_sha256=source, seconds=time.monotonic() - began,
        encoder=None if encoder is None else encoder.receipt,
        vector_receipts=caches,
        new_model_calls=0, gpu_used=False, classification_labels_read=False,
        limitation='Human source quotations are exposed semantic reviews, not official gold. Structural fields/eligibility coverage do not certify whole task or absence. Same raw cap, actual spend reported. Prompt candidates have no fresh F1 yet.')
    journal.save('report.json', report)
    return {k: v for k, v in report.items() if k not in ('measurements', 'source_sha256')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--method', choices=('lexical', 'hybrid'), default='lexical')
    parser.add_argument('--vector-cache', type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.prepared, args.review, args.output, method=args.method, vector_cache=args.vector_cache),
                     ensure_ascii=False, indent=2))
