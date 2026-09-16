"""Measure source syntax discovery and model citation separately, without labels.

The saved answers can expose skipped candidate references. They cannot measure
the final effect of supplying this new inventory to a model.
"""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.generation_contract import validate_grammar
from submission.pps.notice_search import merge_ranges
from submission.pps.retrieval import Span
from submission.pps.specification_candidates import NAME, inventory, review_schema, decode_review
from submission.runtime import source_manifest
from tools.score_specification_contrast import primary_and_repeats


def covered(location, locations, record):
    if location is None:
        return False
    return any(di == location['doc_index'] and lo <= location['start'] and hi >= location['end']
        for di, lo, hi in merge_ranges([(s['doc_index'], s['start'], s['end']) for s in locations], record['docs']))


def citation_audit(plan, facts, record):
    """A cited parent sentence counts as a citation, never as a child relation."""
    result = []
    for candidate in plan['candidates']:
        products = [{'product': i, 'specificity': p['specificity'], 'role': p['role'],
                     'requirement': p['requirement'], 'relation': p.get('relation')}
            for i, p in enumerate(facts['products'], 1)
            if covered(candidate['value_source'], p['source_locations'], record)]
        result.append({'candidate': candidate['key'], 'value_fully_cited_by_products': products,
            'separate_candidate_review_verified': False, 'semantic_bundle_verified': False})
    return {'inventory_claim': facts.get('product_inventory'), 'candidates': result,
            'value_cited': sum(bool(r['value_fully_cited_by_products']) for r in result)}


def unknown_response(plan):
    result = {NAME: {c['key']: {'specificity': 'unknown', 'role': 'unknown',
        'requirement': 'unknown', 'scope_sources': [], 'permission_sources': [],
        'permission_scope': 'unclear', 'exception_sources': []} for c in plan['candidates']}}
    observed = plan.get('source_context')
    if observed is not None:
        from submission.pps.specification_context import NAME as CONTEXT_NAME
        reviews = {p['key']: {
            'relevance': 'unknown', 'target_candidates': [], 'target_level': 'unknown',
            'attribute': 'unknown', 'effect': 'unknown', 'target_sources': []}
            for p in observed['permission_observations']}
        # The execution schema presents the permission-first review before the
        # candidate-first review.  Keep synthetic audit answers in that same
        # canonical order so they also exercise the constrained decoder.
        result = {CONTEXT_NAME: reviews, **result}
    return result


def read_jsonl(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--packets', type=Path, required=True)
    parser.add_argument('--readings', type=Path, required=True)
    parser.add_argument('--freeze', type=Path, required=True)
    parser.add_argument('--arm', default='specification_scope')
    parser.add_argument('--tokenizer-dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Preserve completed outputs; select a fresh output directory')
    records = read_jsonl(args.inputs)
    if len({r['id'] for r in records}) != len(records):
        raise ValueError('Duplicate input records')
    records = {r['id']: r for r in records}
    freeze = json.loads(args.freeze.read_text(encoding='utf-8'))
    if (hashlib.sha256(args.inputs.read_bytes()).hexdigest() != freeze['inputs_sha256']
            or hashlib.sha256(args.packets.read_bytes()).hexdigest() != freeze['packets_sha256']):
        raise ValueError('Inputs or packets differ from the pre-inference freeze')
    all_packets, repeats = primary_and_repeats(read_jsonl(args.packets), freeze)
    packets = [p for p in all_packets if p['arm'] == args.arm]
    if not packets or len({p['record_id'] for p in packets}) != len(packets):
        raise ValueError('Expected exactly one selected packet per notice')
    readings = json.loads(args.readings.read_text(encoding='utf-8'))
    keys = [r['request_key'] for r in readings]
    if len(set(keys)) != len(keys) or set(keys) != {p['request_key'] for p in all_packets}:
        raise ValueError('Saved primary readings must join exactly with prepared requests')
    tokenizer = None
    if args.tokenizer_dir:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer_dir, local_files_only=True, trust_remote_code=False)
    results, counts, arms = [], Counter(), {}
    for packet in packets:
        record = records[packet['record_id']]
        units = [Span(**s) for s in packet['spans']]
        plan = inventory(record, units)
        counts.update(c['syntax'] for c in plan['candidates'])
        audit = {}
        for reading in readings:
            if reading['record_id'] != record['id'] or not reading['structured_facts']:
                continue
            original = next(p for p in all_packets if p['request_key'] == reading['request_key'])
            if original['spans'] != packet['spans']:
                raise ValueError('Different source ranges cannot be called a matched citation comparison')
            if original['generation']['response_format'] == 'specification_scope':
                from submission.pps.specification_scope import decode
            elif original['generation']['response_format'] == 'specification_relations':
                from submission.pps.specification_relations import decode
            else:
                raise ValueError('Unsupported saved structured format')
            if decode(reading['native_text'], units, record) != reading['structured_facts']:
                raise ValueError('Current original-address decoder differs from the saved facts')
            measured = citation_audit(plan, reading['structured_facts'], record)
            audit[reading['arm']] = measured
            arms.setdefault(reading['arm'], Counter()).update(value_cited=measured['value_cited'])
        cost = {'measured_model_output': False}
        try:
            schema = review_schema(plan)
            validate_grammar(schema)
            example = json.dumps(unknown_response(plan), ensure_ascii=False, separators=(',', ':'))
            receipt = decode_review(example, plan, record, units)
            assert receipt['declared_candidates_answered'] == len(plan['candidates'])
            cost['grammar_status'] = 'PASS'
            if tokenizer:
                cost['synthetic_all_unknown_response_tokens'] = len(tokenizer.encode(example, add_special_tokens=False))
        except ValueError as exc:
            cost.update(grammar_status='NOT_READY', reason=str(exc))
        results.append({'record_id': record['id'], 'inventory': plan, 'citation_audit': audit, 'cost': cost})
    summary = {'notices': len(results), 'with_declared_candidates': sum(bool(r['inventory']['candidates']) for r in results),
        'candidates': sum(len(r['inventory']['candidates']) for r in results), 'syntax_counts': dict(counts),
        'bound_value_candidates': sum(c['value_source'] is not None for r in results for c in r['inventory']['candidates']),
        'largest_inventory': max(len(r['inventory']['candidates']) for r in results),
        'citation_counts_by_arm': {arm: dict(count) for arm, count in arms.items()},
        'predeclared_repeats_excluded': len(repeats), 'source_token_budget': freeze['source_token_budget'],
        'source_expansion_performed': False, 'gold_or_labels_read': False, 'gpu_allocated': False,
        'human_bundle_coverage': None, 'fresh_inference_gain': None, 'official_score': None,
        'source_sha256': source_manifest(),
        'input_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.inputs, args.packets, args.readings, args.freeze)}}
    args.output.mkdir(parents=True, exist_ok=False)
    save(args.output / 'inventories.json', results)
    save(args.output / 'summary.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in ('source_sha256', 'input_sha256')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
