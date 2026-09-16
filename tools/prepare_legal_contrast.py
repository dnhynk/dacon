"""Freeze current A10 versus one bounded legal-tool reading; no labels or GPU."""
from __future__ import annotations

import argparse
import copy
import dataclasses
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import B4Pipeline, digest
from submission.pps.generation_contract import generation_schema
from submission.pps.legal_query_contract import prepare_legal_arm, verify_prepared
from submission.pps.prompts import verified_search_spans
from submission.runtime import source_manifest


def write_rows(path, rows):
    with gzip.open(path, 'wt', encoding='utf8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    manifest = json.loads(args.cases.read_text('utf8'))
    assert hashlib.sha256(args.input.read_bytes()).hexdigest() == manifest['input_sha256']
    assert manifest['classification_labels_read'] is False
    with gzip.open(args.input, 'rt', encoding='utf8') as stream:
        records = {r['id']: r for r in map(json.loads, stream)}
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    pipeline = B4Pipeline(ROOT/'data_open/data', tokenizer, input_strategy='audited')
    prepared, observations, originals = [], [], {}
    for ci, case in enumerate(manifest['cases']):
        record = records[case['record_id']]
        assert record['id'] not in originals, 'One legal topic per source notice in this pilot'
        control = next(p for p in pipeline.bundle(record) if p['family'] == 'A' and p['items'][0] == 10)
        source_tokens = sum(len(tokenizer.encode(s['text'], add_special_tokens=False)) for s in control['spans'])
        selection = {'record_id': record['id'], 'method': 'canonical_shared_source_freeze',
            'documents': [{'doc_index': i, 'doc_id': doc['doc_id'],
                'doc_sha256': hashlib.sha256(doc['text'].encode()).hexdigest()}
                for i, doc in enumerate(record['docs'])],
            'spans': copy.deepcopy(control['spans']), 'source_tokens': source_tokens,
            'source_token_budget': source_tokens,
            'diagnostics': {'notice_retrieval_changed': False}}
        verified_search_spans(record, selection, tokenizer)
        pair = []
        for arm, topic in (('A_current', None), ('B_legal_dependencies', case['topic'])):
            packet = prepare_legal_arm(control, record, pipeline.knowledge, tokenizer, topic=topic)
            packet.update(request_key=f"legal:{case['case']}:{arm}", case=case['case'], arm=arm,
                          source_search=copy.deepcopy(selection))
            packet['generation_schema_sha256'] = digest(generation_schema(
                packet['generation']['response_format'], len(packet['spans']), packet['items']))
            assert len(packet['token_ids']) + packet['generation']['max_output_tokens'] + 32 <= pipeline.config.max_model_len
            verify_prepared(packet, record, pipeline.knowledge, tokenizer)
            pair.append(packet)
        a, b = pair
        for key in ('spans', 'source_search', 'generation', 'items', 'schema_sha256',
                    'generation_schema_sha256', 'comparison_facts', 'coverage'):
            assert a[key] == b[key]
        assert a['messages'] == control['messages'] and a['token_ids'] == control['token_ids']
        assert a['legal_control']['unchanged_message_parts_sha256'] == b['legal_control']['unchanged_message_parts_sha256']
        assert b['legal_reading']['specified_dependency_units_covered'], 'Only fully acquired specified plans enter this pilot'
        originals[record['id']] = a
        prepared.extend(pair if ci % 2 == 0 else pair[::-1])
        observations.append({'case': case['case'], 'record_id': record['id'], 'topic': case['topic'],
            'notice_source_tokens_each_arm': source_tokens,
            'legal_source_tokens': [a['legal_control']['source_token_budget'], b['legal_reading']['source_tokens']],
            'input_tokens': [len(a['token_ids']), len(b['token_ids'])],
            'specified_plan_units_covered_in_B': True,
            'legal_basis_complete': False, 'primary_item': case['primary_item']})
    repeats = []
    for rid in manifest.get('repeat_control_records', []):
        original = originals[rid]
        repeat = copy.deepcopy(original)
        repeat.update(request_key=original['request_key']+':repeat', arm='A_repeat')
        for key in ('messages', 'token_ids', 'generation', 'schema_sha256', 'generation_schema_sha256', 'spans'):
            assert repeat[key] == original[key]
        prepared.append(repeat)
        repeats.append({'primary': original['request_key'], 'repeat': repeat['request_key'],
                        'selection_rule': 'Always use the first response; repeat is a diagnostic only'})
    if not prepared:
        raise ValueError('No paired requests')
    # The first two cases form a format-only pilot, then batches of <=16. Repeat
    # controls occur later and are never selected as a better-scoring answer.
    batches = [prepared[:4]] + [prepared[n:n+16] for n in range(4, len(prepared), 16)]
    args.output.mkdir(parents=True, exist_ok=False)
    write_rows(args.output/'contrast_packets.jsonl.gz', prepared)
    write_rows(args.output/'contrast_inputs.jsonl.gz', [records[r] for r in originals])
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    freeze = {'kind': 'legal_dependency_contrast_v1', 'source_sha256': source_manifest(),
        'config': dataclasses.asdict(pipeline.config),
        'packets_sha256': sha(args.output/'contrast_packets.jsonl.gz'),
        'inputs_sha256': sha(args.output/'contrast_inputs.jsonl.gz'),
        'case_manifest_sha256': sha(args.cases), 'parent_input_sha256': sha(args.input),
        'call_plan': [{'number': i, 'request_keys': [p['request_key'] for p in batch]}
                      for i, batch in enumerate(batches)],
        'cases': observations, 'notices': len(originals), 'primary_requests': len(prepared),
        'repeat_controls': repeats, 'classification_labels_read': False,
        'controls': 'Current canonical A10 control, identical notice source, system, rubric, generation, schema and CPU consumer. Only legal block changes.',
        'promotion_scope': 'Primary v10 hypothesis only; inspect all nine outputs for tradeoffs. No automatic overlay or default promotion.',
        'legal_budget': 'Identical original-source-token ceiling and character ceiling; unused tokens are not filled with unrelated text.',
        'limitations': 'Source-selected exposed development diagnostic. Not full160, unseen confirmation, official F1, or a complete legal proof.',
        'new_model_calls': 0, 'gpu_used': False,
        'input_tokens_total': sum(len(p['token_ids']) for p in prepared),
        'input_tokens_max': max(len(p['token_ids']) for p in prepared)}
    with (args.output/'contrast_freeze.json').open('x', encoding='utf8') as stream:
        json.dump(freeze, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: freeze[k] for k in ('notices', 'primary_requests', 'repeat_controls', 'input_tokens_total', 'input_tokens_max')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
