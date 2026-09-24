"""Freeze a source-selected Q output-order pilot with identical model inputs."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import dataclasses
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import digest, restored
from submission.pps.catalog_scope import ITEMS, source_review_blocker, whole_task_witnesses
from submission.pps.generation_contract import generation_schema, preflight
from submission.pps.knowledge import Knowledge
from submission.pps.prompts import Config, token_ids, output_schema
from submission.runtime import source_manifest
from tools.prepare_retrieval_contrast import read_rows, write_rows
from tools.run_retrieval_contrast import verify_packet_source


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=18)
    args = parser.parse_args()
    if not 1 <= args.limit <= 18:
        parser.error('This pilot is bounded to at most18notices')
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    code = source_manifest()
    old = json.loads((args.prepared/'contrast_freeze.json').read_text(encoding='utf-8'))
    for name, key in [('contrast_packets.jsonl.gz','packets_sha256'), ('contrast_inputs.jsonl.gz','inputs_sha256')]:
        assert sha(args.prepared/name) == old[key]
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    config = Config.load(ROOT/'submission/model/config.json')
    knowledge = Knowledge(ROOT/'data_open/data')
    records = read_rows(args.prepared/'contrast_inputs.jsonl.gz')
    original_packets = read_rows(args.prepared/'contrast_packets.jsonl.gz')
    originals = {p['record_id']: p for p in original_packets if p['arm'] == 'current'}
    assert len(originals) == sum(p['arm'] == 'current' for p in original_packets)
    baseline = {f'{p}{k}': '0' if p == 'v' else '' for k in ITEMS for p in ('v','e')}
    selected, log = [], []
    for record in records:
        previous = originals[record['id']]
        _, source = knowledge.qualification_decisions(record, baseline)
        blocker = source_review_blocker(record, source)
        witnesses = whole_task_witnesses(record, restored(previous)['spans'], range(1, len(previous['spans'])+1))
        usable = [w for w in witnesses if len(w['selected_units']) <= 12]
        reason = blocker or ('complete_task_not_available_within_reference_bound' if not usable else None)
        chosen = reason is None and len(selected) < args.limit
        log.append({'record_id': record['id'], 'selected': chosen,
                    'reason': reason or ('source_order_limit_reached' if not chosen else 'complete_source_task_available'),
                    'complete_task_witnesses_available': len(usable)})
        if chosen:
            selected.append(record)
    if len(selected) != args.limit:
        raise ValueError('Not enough source-eligible records for the preregistered cohort')
    methods = ('control','facts_first')
    packets = []
    for index, record in enumerate(selected):
        previous = originals[record['id']]
        verify_packet_source(previous, record, tokenizer)
        assert token_ids(tokenizer, previous['messages'], True) == previous['token_ids']
        assert digest(previous['messages']) == previous['prompt_sha256']
        assert digest(previous['token_ids']) == previous['token_ids_sha256']
        assert previous['source_search']['source_tokens'] <= 4096
        assert previous['generation'] == {'response_format':'catalog_scope','thinking_budget':0,'max_output_tokens':1536}
        assert previous['schema_sha256'] == digest(output_schema('catalog_scope', len(previous['spans']), ITEMS))
        assert len(previous['token_ids'])+1536+32 <= config.max_model_len
        pair = []
        for arm in methods:
            packet = copy.deepcopy(previous)
            packet.update(arm=arm, request_key=f'order:{record["id"]}:{arm}',
                          original_request_key=previous['request_key'])
            if arm == 'facts_first':
                packet['generation']['schema_order'] = 'catalog_facts_first'
            schema = generation_schema('catalog_scope', len(packet['spans']), ITEMS,
                                       schema_order=packet['generation'].get('schema_order'))
            packet['generation_schema_sha256'] = digest(schema)
            pair.append(packet)
        for key in ('messages','token_ids','spans','schema_sha256','source_search','catalog_scope'):
            assert pair[0][key] == pair[1][key] == previous[key]
        assert pair[0]['generation_schema_sha256'] != pair[1]['generation_schema_sha256']
        # Each batch contains matched pairs; rotate which order is submitted first.
        packets.extend(pair if index % 2 == 0 else pair[::-1])
    prereg = {'kind':'fixed_input_scope_generation_order_pilot', 'previous_preparation':str(args.prepared),
        'previous_freeze_sha256':sha(args.prepared/'contrast_freeze.json'),
        'selection':'First source-input-order eligible notices with a complete task witness visible within12references in the saved current search; no labels or model responses are read by selection.',
        'selection_log':log, 'notices':len(selected), 'primary_requests':len(packets),
        'classification_labels_read':False, 'selection_model_responses_read':False,
        'development_exposed':True, 'held_out':False, 'embedding_calls':0,
        'source_token_cap':4096, 'prompt_and_token_changes_per_pair':0,
        'changed_variable':'Structured generation JSON property and required-field order only.',
        'arms':{'control':list(generation_schema('catalog_scope',1,ITEMS)['properties']),
                'facts_first':list(generation_schema('catalog_scope',1,ITEMS,schema_order='catalog_facts_first')['properties'])},
        'quality_rerolls':0, 'decision':'Compare all format results, selected task coverage, internally consistent scope conclusions, and full-baseline joined F1. No per-notice best-arm selection. A pilot is not a new full inference score or evidence of generalization.'}
    (args.output/'preregistered.json').write_text(json.dumps(prereg,ensure_ascii=False,indent=2),encoding='utf-8')
    grammar = preflight([(p['generation']['response_format'],len(p['spans']),p['items'],
                          p['generation'].get('schema_order')) for p in packets])
    (args.output/'generation_grammar_preflight.json').write_text(json.dumps(grammar,ensure_ascii=False,indent=2),encoding='utf-8')
    write_rows(args.output/'contrast_packets.jsonl.gz',packets)
    write_rows(args.output/'contrast_inputs.jsonl.gz',selected)
    counts = [originals[r['id']]['source_search']['source_tokens'] for r in selected]
    spending = {'sum':sum(counts),'mean':statistics.mean(counts),'min':min(counts),'max':max(counts)}
    freeze = {'kind':prereg['kind'], 'source_sha256':code, 'config':dataclasses.asdict(config),
        'packets_sha256':sha(args.output/'contrast_packets.jsonl.gz'),
        'inputs_sha256':sha(args.output/'contrast_inputs.jsonl.gz'),
        'call_plan':[{'number':n//18,'request_keys':[p['request_key'] for p in packets[n:n+18]]}
                     for n in range(0,len(packets),18)],
        'cases':len(selected),'notices':len(selected),'primary_requests':len(packets),'methods':list(methods),
        'source_token_budget':4096,'source_tokens':{arm:spending for arm in methods},
        'input_tokens_total':sum(len(p['token_ids']) for p in packets),
        'input_tokens_max':max(len(p['token_ids']) for p in packets),
        'classification_labels_read':False,'embedding_calls_here':0,
        'new_full160_score':False,'official_macro_f1':None,
        'preregistered_sha256':sha(args.output/'preregistered.json'),
        'generation_grammar_preflight_sha256':sha(args.output/'generation_grammar_preflight.json'),
        'cpu_seconds':time.monotonic()-started}
    assert source_manifest() == code
    (args.output/'contrast_freeze.json').write_text(json.dumps(freeze,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:freeze[k] for k in ('notices','primary_requests','source_tokens','cpu_seconds')},ensure_ascii=False))
    print(json.dumps({'selection_reasons':dict(Counter(r['reason'] for r in log))},ensure_ascii=False))


if __name__ == '__main__':
    main()
