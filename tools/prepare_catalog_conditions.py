"""Prepare a condition-consumption diagnostic from fixed source selections.

No retrieval rerun, labels, model outputs or GPU. This is a changed task/output
contract, not a measured score improvement or a prompt-format-only ablation.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from submission.b4_entry import digest
from submission.pps.catalog_condition_review import (
    FORMAT, ITEMS, _covers, _local_task_witnesses, _subject_candidates,
    condition_plan, property_readings, prompt, review,
)
from submission.pps.catalog_condition_facts import source_facts
from submission.pps.catalog_scope import whole_task_witnesses
from submission.pps.generation_contract import generation_schema
from submission.pps.knowledge import Knowledge
from submission.pps.prompts import Config, output_schema, token_ids
from submission.runtime import source_manifest
from tools.prepare_retrieval_contrast import write_rows
from tools.run_retrieval_contrast import read_rows, verify_packet_source


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def covering_refs(record, spans, evidence):
    refs = [i for i, s in enumerate(spans, 1) if s.doc_index == evidence['doc_index']
            and s.start < evidence['end'] and s.end > evidence['start']]
    return refs if len(refs) <= 16 and _covers(record, spans, refs, evidence) else []


def reading_capacity(record, body, knowledge):
    """Best-case links for recognized values only; not verified human readings.

    This deliberately assumes each admissible local source-name link is right.
    If it still cannot compute a condition, mere schema-valid model output on
    these sources cannot resolve it in this consumer. No claim of full factual
    coverage or semantic correctness follows from a nonempty result.
    """
    spans = body['spans']
    anchors = whole_task_witnesses(record, spans, list(range(1, len(spans)+1)))
    anchors += _subject_candidates(record)
    findings = []
    for product in body['catalog_conditions']['plan']:
        fields = [f['field'] for f in product['fields']]
        for observation in property_readings(record, product['name'], fields)['observations']:
            refs = covering_refs(record, spans, observation['evidence'])
            if observation['issue'] or not refs:
                continue
            for anchor in anchors:
                scope = covering_refs(record, spans, anchor)
                if not scope:
                    continue
                finding = {'code': product['code'], 'field': observation['field'],
                    'value_units': refs, 'scope_units': scope, 'condition_units': [],
                    'scope': 'whole_named_purchase', 'modality': 'required',
                    'reason': 'CPU capacity probe: relation assumed, not observed Gemma output'}
                if _local_task_witnesses(record, spans, finding, observation):
                    findings.append(finding)
                    break
    if len(findings) > 32:
        return {'status': 'schema_capacity_exceeded', 'finding_count': len(findings),
                'model_calls': 0, 'semantic_certification': False}
    packet = dict(body, family='C', generation={'response_format': FORMAT})
    response = {'text': json.dumps({'findings': findings, 'unresolved_fields': []}, ensure_ascii=False)}
    row, details = review(record, response, packet, knowledge)
    return {'status': 'computed', 'finding_count': len(findings), 'findings': findings,
        'hypothetical_partial_updates': row, 'details': details, 'model_calls': 0,
        'semantic_certification': False,
        'interpretation': 'Recognized source facts with optimistic local scope links; not model inference, gold or an admissible-structure proof.'}


def prepare(parent, output, data_dir, tokenizer_dir):
    parent, output = Path(parent).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(parent):
        raise ValueError('Use a new output outside the preserved preparation')
    tracked = [parent/name for name in ('contrast_freeze.json', 'contrast_packets.jsonl.gz', 'contrast_inputs.jsonl.gz')]
    originals = {p.as_posix(): sha(p) for p in tracked}
    freeze = json.loads(tracked[0].read_text(encoding='utf-8'))
    if sha(tracked[1]) != freeze['packets_sha256'] or sha(tracked[2]) != freeze['inputs_sha256']:
        raise ValueError('Preserved preparation checksum mismatch')
    packets, inputs = read_rows(tracked[1]), read_rows(tracked[2])
    records = {r['id']: r for r in inputs}
    if not packets or len(records) != len(inputs) or len({p['request_key'] for p in packets}) != len(packets):
        raise ValueError('Empty or duplicate preparation identities')
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_dir, local_files_only=True, trust_remote_code=False)
    knowledge, config = Knowledge(data_dir), Config.load(ROOT/'submission/model/config.json')
    code = source_manifest()
    prepared, capacities, mapping, source_inventory = [], [], {}, []
    for record in inputs:
        _, facts = knowledge.qualification_decisions(record, {})
        inventory = []
        for product in condition_plan(facts['product']):
            fields = [f['field'] for f in product['fields']]
            inventory.append({'plan': product,
                'previous_fields': source_facts(record, product['name'], fields),
                'current_fields': property_readings(record, product['name'], fields)})
        source_inventory.append({'record_id': record['id'], 'products': inventory,
            'new_subject_candidates': _subject_candidates(record)})
    for old in packets:
        if tuple(old['items']) != ITEMS:
            raise ValueError('Condition diagnostic only supports the fixed items10..18')
        record = records[old['record_id']]
        verify_packet_source(old, record, tokenizer)
        if token_ids(tokenizer, old['messages'], True) != old['token_ids']:
            raise ValueError('Parent rendered tokens do not match the actual tokenizer')
        body = prompt(record, old['source_search'], tokenizer, knowledge)
        max_output = old['generation']['max_output_tokens']
        if len(body['token_ids']) + max_output + 32 > config.max_model_len:
            raise ValueError('Condition diagnostic exceeds the unchanged model context')
        key = 'catalog_contract:'+old['request_key']
        mapping[old['request_key']] = key
        spans = [dataclasses.asdict(s) for s in body['spans']]
        packet = {**body, 'request_key': key, 'record_id': record['id'], 'case': old['case'],
            'arm': old['arm'], 'family': 'C', 'spans': spans,
            'comparison_facts': None, 'source_sha256': digest(spans),
            'prompt_sha256': digest(body['messages']), 'token_ids_sha256': digest(body['token_ids']),
            'generation': {**body['generation'], 'thinking_budget': 0, 'max_output_tokens': max_output},
            'schema_sha256': digest(output_schema(FORMAT, len(spans), ITEMS)),
            'generation_schema_sha256': digest(generation_schema(FORMAT, len(spans), ITEMS,
                catalog_fields=body['generation']['catalog_fields']))}
        verify_packet_source(packet, record, tokenizer)
        prepared.append(packet)
        capacities.append({'request_key': key, 'record_id': record['id'], 'arm': old['arm'],
            'source_search_unchanged': body['source_search'] == old['source_search'],
            'original_source_tokens': body['source_search']['source_tokens'],
            'old_input_tokens': len(old['token_ids']), 'new_input_tokens': len(body['token_ids']),
            **reading_capacity(record, body, knowledge)})
    plan = [{'number': batch['number'], 'request_keys': [mapping[k] for k in batch['request_keys']]}
            for batch in freeze['call_plan']]
    if sorted(k for b in plan for k in b['request_keys']) != sorted(mapping.values()):
        raise ValueError('Parent call plan omitted or repeated a request')
    if source_manifest() != code or any(sha(p) != h for p, h in originals.items()):
        raise RuntimeError('Source or preserved inputs changed during preparation')
    output.mkdir(parents=True, exist_ok=False)
    write_rows(output/'contrast_packets.jsonl.gz', prepared)
    write_rows(output/'contrast_inputs.jsonl.gz', inputs)
    result = {'source_sha256': code, 'config': dataclasses.asdict(config),
        'packets_sha256': sha(output/'contrast_packets.jsonl.gz'),
        'inputs_sha256': sha(output/'contrast_inputs.jsonl.gz'), 'call_plan': plan,
        'primary_requests': len(prepared), 'notices': len(inputs), 'items': list(ITEMS),
        'arms': freeze['arms'], 'parent': originals, 'parent_request_mapping': mapping,
        'parent_logical_request_aliases': freeze.get('logical_request_aliases', []),
        'input_tokens_max': max(len(p['token_ids']) for p in prepared),
        'source_searches_unchanged': True, 'new_model_calls': 0, 'gpu_used': False,
        'classification_labels_read': False, 'production_enabled': False,
        'status': 'cpu_prepared_diagnostic_not_launched',
        'control': 'Reuse every fixed source selection and ordering. New condition-reading task and schema; not format-only ablation.',
        'limit': 'Partial updates require a separately frozen baseline and valid new inference before any final F1 comparison.'}
    for name, obj in [('contrast_freeze.json', result), ('source_inventory.json', source_inventory),
                      ('reading_capacity.json', capacities)]:
        with (output/name).open('x', encoding='utf-8') as stream:
            json.dump(obj, stream, ensure_ascii=False, indent=2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, default=ROOT/'data_open/data')
    parser.add_argument('--tokenizer-dir', type=Path, default=ROOT/'models/gemma-tokenizer')
    args = parser.parse_args()
    result = prepare(args.parent, args.output, args.data_dir, args.tokenizer_dir)
    print(json.dumps({k: result[k] for k in ('status', 'primary_requests', 'notices', 'input_tokens_max',
        'source_searches_unchanged', 'new_model_calls', 'gpu_used')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
