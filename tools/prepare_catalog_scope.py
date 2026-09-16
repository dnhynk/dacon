"""Prepare a label-free, same-source-budget service-scope retrieval contrast.

Selection uses current source uncertainty, never an error list or model answer.
The entire supplied service catalog is fixed across arms. BGE runs only on CPU.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import digest
from submission.pps.catalog_scope import ITEMS, QUERIES, eligible, prompt
from submission.pps.data import records
from submission.pps.knowledge import Knowledge
from submission.pps.notice_search import NoticeSearch
from submission.pps.prompts import Config, output_schema
from submission.runtime import source_manifest
from tools.prepare_retrieval_contrast import write_rows


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--methods', nargs='+', choices=['current', 'lexical', 'hybrid'],
                        default=['current', 'lexical', 'hybrid'])
    args = parser.parse_args()
    if len(args.methods) != len(set(args.methods)):
        parser.error('Methods must be distinct')
    args.output.mkdir(parents=True, exist_ok=False)
    code = source_manifest()
    started = time.monotonic()
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer',
        local_files_only=True, trust_remote_code=False)
    knowledge = Knowledge(ROOT/'data_open/data')
    config = Config.load(ROOT/'submission/model/config.json')
    chosen, selection_log = [], []
    baseline = {f'{field}{k}': '0' if field == 'v' else '' for k in ITEMS for field in ('v', 'e')}
    for record in records(args.input):
        _, facts = knowledge.qualification_decisions(record, baseline)
        selected = eligible(record, facts['product'])
        selection_log.append({'id': record['id'], 'selected': selected,
            'state': facts['product']['status'], 'mechanism': facts['product']['mechanism'],
            'uncertainty': facts['product']['uncertainty']})
        if selected:
            chosen.append(record)
    preregistered = {'kind': 'optional_service_catalog_scope_review', 'input_sha256': sha(args.input),
        'source_sha256': code, 'selection_policy': 'all general-service records with unknown or heuristic-family product scope',
        'selected_records': len(chosen), 'selection': selection_log, 'methods': args.methods,
        'source_budget': 4096, 'factual_queries': QUERIES, 'model_responses_read': False,
        'labels_read': False, 'complete_static_service_catalog': True,
        'new_model_calls': 0, 'production_enabled': False,
        'limitation': 'Partial review of exposed development source; classification is a fallible model fact, not official gold.'}
    (args.output/'preregistered.json').write_text(json.dumps(preregistered, ensure_ascii=False, indent=2), encoding='utf-8')
    encoder = None
    if 'hybrid' in args.methods:
        from submission.pps.embeddings import BGEDenseEncoder
        encoder = BGEDenseEncoder(ROOT/'models/bge-m3')
    packets = []
    for ci, record in enumerate(chosen):
        search = NoticeSearch(record, tokenizer, encoder)
        arms = args.methods[ci % len(args.methods):] + args.methods[:ci % len(args.methods)]
        for arm in arms:
            selection = search.search(ITEMS, token_budget=4096, method=arm, queries=QUERIES)
            body = prompt(record, selection, tokenizer, knowledge.products)
            spans = [dataclasses.asdict(s) for s in body['spans']]
            if len(body['token_ids']) + 1536 + 32 > config.max_model_len:
                raise ValueError('Scope review exceeds fixed model context')
            packets.append({'request_key': f"scope:{record['id']}:{arm}", 'record_id': record['id'],
                'case': record['id'], 'arm': arm, 'family': 'Q', 'items': list(ITEMS),
                'messages': body['messages'], 'token_ids': body['token_ids'],
                'prompt_sha256': digest(body['messages']), 'token_ids_sha256': digest(body['token_ids']),
                'spans': spans, 'source_sha256': digest(spans), 'source_search': selection,
                'source_unitization': body['source_unitization'], 'catalog_scope': body['catalog_scope'],
                'comparison_facts': None, 'coverage': body['coverage'],
                'generation': {'response_format': 'catalog_scope', 'thinking_budget': 0, 'max_output_tokens': 1536},
                'schema_sha256': digest(output_schema('catalog_scope', len(spans), ITEMS))})
        print(json.dumps({'prepared': ci+1, 'total': len(chosen), 'seconds': round(time.monotonic()-started, 2)}), flush=True)
    write_rows(args.output/'contrast_packets.jsonl.gz', packets)
    write_rows(args.output/'contrast_inputs.jsonl.gz', chosen)
    freeze = {'source_sha256': code, 'config': dataclasses.asdict(config),
        'packets_sha256': sha(args.output/'contrast_packets.jsonl.gz'),
        'inputs_sha256': sha(args.output/'contrast_inputs.jsonl.gz'),
        'call_plan': [{'number': n//18, 'request_keys': [p['request_key'] for p in packets[n:n+18]]}
                      for n in range(0, len(packets), 18)],
        'cases': len(chosen), 'notices': len(chosen), 'primary_requests': len(packets),
        'source_token_budget': 4096, 'classification_labels_read': False,
        'input_tokens_total': sum(len(p['token_ids']) for p in packets),
        'input_tokens_max': max(len(p['token_ids']) for p in packets),
        'methods': args.methods, 'cpu_seconds': time.monotonic()-started,
        'control': 'Same full service catalog, output schema, fixed model and CPU consumer. Same original source token cap; retrieval alone differs.',
        'new_full160_score': False, 'official_macro_f1': None}
    if source_manifest() != code or sha(args.input) != preregistered['input_sha256']:
        raise RuntimeError('Source or input changed during preparation')
    (args.output/'contrast_freeze.json').write_text(json.dumps(freeze, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in freeze.items() if k not in {'source_sha256', 'config', 'call_plan'}}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
