"""Re-render a scope diagnostic from verified saved retrieval, with no BGE calls."""
import argparse
from collections import Counter
import dataclasses
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import digest
from submission.pps.catalog_scope import ITEMS, prompt, source_review_blocker
from submission.pps.knowledge import Knowledge
from submission.pps.prompts import Config, output_schema
from submission.pps.generation_contract import preflight
from submission.runtime import source_manifest
from tools.prepare_retrieval_contrast import read_rows, write_rows
from tools.compare_notice_retrieval import CASE_ITEMS, hit


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    code = source_manifest()
    old = json.loads((args.prepared/'contrast_freeze.json').read_text(encoding='utf-8'))
    for name, key in [('contrast_packets.jsonl.gz', 'packets_sha256'), ('contrast_inputs.jsonl.gz', 'inputs_sha256')]:
        assert sha(args.prepared/name) == old[key]
    for name in ('notice_search.py', 'retrieval.py', 'embeddings.py', 'source_units.py'):
        relative = 'pps/' + name
        assert old['source_sha256'][relative] == code[relative], 'Retrieval or source unitization changed: '+relative
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    knowledge = Knowledge(ROOT/'data_open/data')
    config = Config.load(ROOT/'submission/model/config.json')
    records = read_rows(args.prepared/'contrast_inputs.jsonl.gz')
    baseline = {f'{p}{k}': '0' if p == 'v' else '' for k in ITEMS for p in ('v', 'e')}
    selected, omitted = {}, []
    for record in records:
        _, facts = knowledge.qualification_decisions(record, baseline)
        reason = source_review_blocker(record, facts)
        if reason:
            omitted.append({'id': record['id'], 'reason': reason})
        else:
            selected[record['id']] = record
    prereg = {'kind': 'catalog_scope_packet_rerender', 'previous_preparation': str(args.prepared),
        'previous_freeze_sha256': sha(args.prepared/'contrast_freeze.json'), 'source_sha256': code,
        'source_only_filter': 'Same pre-model gates as review(); no output or label is consulted.',
        'selected_notices': len(selected), 'omitted_notices': omitted,
        'model_responses_read': False, 'classification_labels_read': False, 'embedding_calls': 0,
        'retrieval_changed': False, 'prompt_changed': None,
        'boundary_changes': 'Current canonical consumer; complete source task content and source-only blockers. All text/range changes are measured below.',
        'comparison_budget': 'Same4096 original-source token cap. Actual spending reported separately; unused budget is not counted as read.'}
    (args.output/'preregistered.json').write_text(json.dumps(prereg, ensure_ascii=False, indent=2), encoding='utf-8')
    packets, changed_prompts = [], 0
    for previous in read_rows(args.prepared/'contrast_packets.jsonl.gz'):
        record = selected.get(previous['record_id'])
        if record is None:
            continue
        body = prompt(record, previous['source_search'], tokenizer, knowledge.products)
        spans = [dataclasses.asdict(span) for span in body['spans']]
        assert spans == previous['spans']
        assert len(body['token_ids']) + previous['generation']['max_output_tokens'] + 32 <= config.max_model_len
        current = {**previous, 'messages': body['messages'], 'token_ids': body['token_ids'],
            'prompt_sha256': digest(body['messages']), 'token_ids_sha256': digest(body['token_ids']),
            'spans': spans, 'source_sha256': digest(spans), 'catalog_scope': body['catalog_scope'],
            'coverage': body['coverage'], 'source_unitization': body['source_unitization'],
            'schema_sha256': digest(output_schema('catalog_scope', len(spans), ITEMS))}
        packets.append(current)
        changed_prompts += current['token_ids'] != previous['token_ids']
    prereg.update(prompt_changed=bool(changed_prompts), changed_rendered_prompts=changed_prompts,
                 retained_packets=len(packets))
    (args.output/'preregistered.json').write_text(json.dumps(prereg, ensure_ascii=False, indent=2), encoding='utf-8')
    grammar = preflight([(p['generation']['response_format'], len(p['spans']), p['items']) for p in packets])
    (args.output/'generation_grammar_preflight.json').write_text(json.dumps(grammar, ensure_ascii=False, indent=2), encoding='utf-8')
    write_rows(args.output/'contrast_packets.jsonl.gz', packets)
    write_rows(args.output/'contrast_inputs.jsonl.gz', list(selected.values()))
    methods = old['methods']
    assert len(packets) == len(selected) * len(methods)
    spending = {}
    for arm in methods:
        counts = [p['source_search']['source_tokens'] for p in packets if p['arm'] == arm]
        spending[arm] = {'sum': sum(counts), 'mean': statistics.mean(counts), 'min': min(counts), 'max': max(counts)}
    reference = ROOT/'research/human_review_0912/DACON_의미검토_최종.json'
    review = json.loads(reference.read_text(encoding='utf-8'))
    measurements = []
    for answer in review['answers']:
        if answer['record_id'] not in selected or not set(CASE_ITEMS[answer['task_id']]).intersection(ITEMS):
            continue
        record = selected[answer['record_id']]
        for evidence in answer['evidence']:
            doc = record['docs'][evidence['doc_index']]
            assert sha_text(doc['text']) == evidence['doc_sha256']
            assert doc['text'][evidence['start']:evidence['end']] == evidence['quote']
        for packet in packets:
            if packet['record_id'] != answer['record_id']:
                continue
            search = packet['source_search']
            hits = [hit(e, search) for e in answer['evidence']]
            measurements.append({'case': answer['task_id'], 'arm': packet['arm'], 'anchors': len(hits),
                'anchors_returned': sum(hits), 'any_anchor': any(hits), 'complete_bundle': bool(hits) and all(hits),
                'source_tokens': search['source_tokens'], 'source_coverage': search['coverage']})
    aggregate = []
    for arm in methods:
        rows = [m for m in measurements if m['arm'] == arm]
        aggregate.append({'arm': arm, 'cases': len(rows), 'any_anchor': sum(m['any_anchor'] for m in rows),
            'complete_bundle': sum(m['complete_bundle'] for m in rows),
            'anchors_returned': sum(m['anchors_returned'] for m in rows), 'anchors_total': sum(m['anchors'] for m in rows)})
    report = {'kind': 'saved_scope_retrieval_on_overlapping_exposed_human_reviews',
        'classification_labels_read': False, 'held_out': False, 'review_sha256': sha(reference),
        'selection_or_query_uses_review_answers': False, 'aggregates': aggregate, 'measurements': measurements,
        'source_tokens': spending, 'absence_labels_inferred': False,
        'limit': 'Human anchors describe their reviewed eligibility issue, not a gold label of every service identity. Full bundle is a location-based proxy, not semantic certification. Returned and missing original ranges are in every packet.'}
    (args.output/'retrieval_coverage.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    freeze = {**old, 'source_sha256': code, 'config': dataclasses.asdict(config),
        'packets_sha256': sha(args.output/'contrast_packets.jsonl.gz'),
        'inputs_sha256': sha(args.output/'contrast_inputs.jsonl.gz'),
        'call_plan': [{'number': n//18, 'request_keys': [p['request_key'] for p in packets[n:n+18]]}
                      for n in range(0, len(packets), 18)],
        'cases': len(selected), 'notices': len(selected), 'primary_requests': len(packets),
        'source_tokens': spending, 'input_tokens_total': sum(len(p['token_ids']) for p in packets),
        'input_tokens_max': max(len(p['token_ids']) for p in packets),
        'cpu_seconds': time.monotonic()-started,
        'previous_embedding_preparation_seconds': old.get('previous_embedding_preparation_seconds', old['cpu_seconds']),
        'embedding_calls_here': 0, 'filtered': dict(Counter(r['reason'] for r in omitted)),
        'preregistered_sha256': sha(args.output/'preregistered.json'),
        'retrieval_coverage_sha256': sha(args.output/'retrieval_coverage.json'),
        'generation_grammar_preflight_sha256': sha(args.output/'generation_grammar_preflight.json')}
    assert source_manifest() == code
    (args.output/'contrast_freeze.json').write_text(json.dumps(freeze, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: freeze[k] for k in ('notices', 'primary_requests', 'source_tokens', 'filtered', 'cpu_seconds')}, ensure_ascii=False, indent=2))
    print(json.dumps(aggregate, ensure_ascii=False))


def sha_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


if __name__ == '__main__':
    main()
