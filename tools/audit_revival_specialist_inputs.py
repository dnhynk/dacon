"""Check broad held specialists on the already frozen normal source selections.

This prepares no runnable GPU launch. The current runtime stays frozen while a
separate comparison runs; later integration must reproduce these prompt checks.
"""
import argparse
from collections import Counter
import dataclasses
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import B4Pipeline, digest
from submission.pps.catalog_scope import eligible, prompt as scope_prompt
from submission.pps.prompts import build_prompt
from submission.pps.generation_contract import generation_schema, prepared_preflight
from submission.runtime import Journal, source_manifest
from tools.run_integrated_comparison import load, rows, sha


def selection(record, packet, tokenizer):
    if packet.get('source_search') is not None:
        return packet['source_search']
    tokens = sum(len(tokenizer.encode(s['text'], add_special_tokens=False)) for s in packet['spans'])
    return {'record_id': record['id'], 'method': 'frozen_normal_source',
        'spans': packet['spans'], 'source_tokens': tokens, 'source_token_budget': max(1, tokens),
        'documents': [{'doc_index': di, 'doc_id': d['doc_id'],
                       'doc_sha256': hashlib.sha256(d['text'].encode()).hexdigest()}
                      for di, d in enumerate(record['docs'])],
        'coverage': {**packet['coverage'], 'absence_verified': False},
        'diagnostics': {'same_normal_source': True}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prepared', 'output', 'data-dir', 'tokenizer'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--policy', default='current')
    parser.add_argument('--software-context', choices=('matched', 'relations_only'), default='matched')
    args = parser.parse_args()
    freeze, records, native, plan = load(args.prepared)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True, trust_remote_code=False)
    pipe = B4Pipeline(args.data_dir, tokenizer)
    journal = Journal(args.output)
    prior = {(p['record_id'], p['experiment_role']): p
             for p in rows(args.prepared/'canonical_packets.jsonl.gz')
             if p['experiment_policy'] == args.policy}
    configs = dataclasses.replace(pipe.route.config, response_format='software_refs')
    if args.software_context == 'relations_only':
        # The specialist extracts notice relations; the CPU still evaluates
        # price/floor rules. Preserve notice source and original meta, omitting
        # static threshold excerpts and general product-catalog hints.
        configs = dataclasses.replace(configs, legal_chars=0, product_facts=False)
    packets, observations = [], []
    def capture(record, arm, body, source, original, fmt, out_tokens):
        spans = [dataclasses.asdict(s) for s in body['spans']]
        fits = len(body['token_ids']) + out_tokens + 32 <= pipe.config.max_model_len
        observations.append({'id': record['id'], 'arm': arm, 'fits_context': fits,
            'input_tokens': len(body['token_ids']), 'source_tokens': source['source_tokens'],
            'source_cap': source['source_token_budget'], 'source_units': len(spans),
            'original_packet': original['request_key']})
        if fits:
            packets.append({'request_key': arm+':'+record['id'], 'record_id': record['id'],
                'items': body['items'], 'messages': body['messages'], 'token_ids': body['token_ids'],
                'spans': spans, 'source_search': source,
                'catalog_scope': body.get('catalog_scope'), 'source_unitization': body.get('source_unitization'),
                'prompt_sha256': digest(body['messages']), 'token_ids_sha256': digest(body['token_ids']),
                'generation': {'response_format': fmt, 'thinking_budget': 0, 'max_output_tokens': out_tokens},
                'generation_schema_sha256': digest(generation_schema(fmt, len(spans), body['items']))})
    for record in records:
        _, facts = pipe.knowledge.qualification_decisions(record, {f'v{i}':'0' for i in range(10,19)})
        if eligible(record, facts['product']):
            old = prior[record['id'], 'A1']; src = selection(record, old, tokenizer)
            for arm, explain in (('scope_control', False), ('scope_explicit_contract', True)):
                body = scope_prompt(record, src, tokenizer, pipe.knowledge.products, explain_contract=explain)
                capture(record, arm, body, src, old, 'catalog_scope', 1536)
        old = prior[record['id'], 'L19']; src = selection(record, old, tokenizer)
        try:
            body = build_prompt(record, pipe.knowledge, configs, tokenizer, (20,), source_selection=src)
        except ValueError as exc:
            if not str(exc).startswith(('Software fact task exceeds context', 'Verified search result exceeds')):
                raise
            observations.append({'id':record['id'], 'arm':'software_refs', 'fits_context':False,
                                 'reason':str(exc), 'no_silent_source_shrinking':True})
        else:
            capture(record, 'software_refs', body, src, old, 'software_refs', 2048)
    if source_manifest() != freeze['source_sha256']:
        raise ValueError('Canonical source changed during specialist input audit')
    proof = prepared_preflight(packets)
    journal.rows('checked_prompts.jsonl.gz', packets)
    journal.save('observations.json', observations)
    report = {'prepared_input_freeze_sha256': sha(args.prepared/'input_freeze.json'),
        'source_sha256': freeze['source_sha256'], 'source_policy': args.policy,
        'software_context':args.software_context,
        'whole_cohort_records':len(records), 'checked_prompts':len(packets),
        'arm_counts':dict(Counter(r['arm'] for r in observations)),
        'unavailable': [r for r in observations if not r['fits_context']],
        'max_input_tokens':max(len(p['token_ids']) for p in packets),
        'input_tokens':sum(len(p['token_ids']) for p in packets),
        'effective_schemas':proof, 'labels_read':False, 'new_gemma_calls':0,
        'runtime_integration_pending':True, 'original_source_selection_unchanged':True,
        'not_runnable_gpu_payload':True}
    journal.save('report.json', report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('source_sha256','effective_schemas')}, indent=2))


if __name__ == '__main__':
    main()
