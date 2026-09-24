"""Prepare a paired condition-reader contrast on identical original source."""
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
from submission.pps import catalog_semantics as sem
from submission.pps.generation_contract import generation_schema
from submission.pps.knowledge import Knowledge
from submission.pps.prompts import output_schema
from submission.runtime import source_manifest
from tools.prepare_retrieval_contrast import write_rows
from tools.run_retrieval_contrast import read_rows, verify_packet_source


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(parent, output, data_dir, tokenizer_dir, *, observed_facts=False, obligation_framing=False):
    if obligation_framing and not observed_facts:
        raise ValueError('Obligation framing requires observed facts')
    if output.exists() or output.resolve().is_relative_to(parent.resolve()):
        raise ValueError('Choose a fresh output outside existing preparation')
    paths = [parent / n for n in ('contrast_freeze.json', 'contrast_packets.jsonl.gz', 'contrast_inputs.jsonl.gz')]
    before = {p.as_posix(): sha(p) for p in paths}
    prior = json.loads(paths[0].read_text(encoding='utf-8'))
    code = source_manifest()
    if not observed_facts:
        assert prior['source_sha256'] == code
    assert sha(paths[1]) == prior['packets_sha256'] and sha(paths[2]) == prior['inputs_sha256']
    inputs = read_rows(paths[2])
    records = {r['id']: r for r in inputs}
    parents = [p for p in read_rows(paths[1]) if p['generation'].get('catalog_roles') is not None]
    if obligation_framing:
        parents = [p for p in parents if p.get('catalog_conditions', {}).get('source_observations') is not None
            and 'obligation_questions' not in p.get('catalog_conditions', {})]
    assert len(records) == len(inputs) == len(parents) and {p['record_id'] for p in parents} == records.keys()
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_dir, local_files_only=True, trust_remote_code=False)
    knowledge = Knowledge(data_dir)
    arms = (('A_complete_source_role_choices', 'B_observed_local_properties') if observed_facts else
            ('A_current_semantic_reader', 'B_complete_source_role_choices'))
    if obligation_framing:
        arms = ('A_observed_local_properties', 'B_observed_constraint_questions')
    packets, costs = [], []
    for old in parents:
        rec = records[old['record_id']]
        verify_packet_source(old, rec, tokenizer)
        pair = []
        for enabled, arm in zip((False, True), arms):
            body = sem.prompt(rec, old['source_search'], tokenizer, knowledge,
                source_roles=True if observed_facts else enabled,
                source_observations=observed_facts and (enabled or obligation_framing),
                source_obligations=obligation_framing and enabled)
            spans = [dataclasses.asdict(s) for s in body['spans']]
            assert spans == old['spans']
            gen = {key: value for key, value in old['generation'].items() if key != 'catalog_roles'}
            gen.update(body.get('generation', {}))
            packet = {**body, 'request_key': 'source_roles:' + rec['id'] + ':' + arm,
                'record_id': rec['id'], 'case': rec['id'], 'arm': arm, 'family': 'C',
                'spans': spans, 'source_sha256': digest(spans), 'prompt_sha256': digest(body['messages']),
                'token_ids_sha256': digest(body['token_ids']), 'comparison_facts': None, 'generation': gen,
                'schema_sha256': digest(output_schema(sem.FORMAT, len(spans), body['items'])),
                'generation_schema_sha256': digest(generation_schema(sem.FORMAT, len(spans), body['items'],
                    catalog_roles=gen.get('catalog_roles'), catalog_fields=gen.get('catalog_fields')))}
            assert len(packet['token_ids']) + gen['max_output_tokens'] + 32 <= prior['config']['max_model_len']
            if (not enabled if observed_facts else enabled):
                assert packet['messages'] == old['messages'] and packet['token_ids'] == old['token_ids']
            verify_packet_source(packet, rec, tokenizer)
            pair.append(packet)
        assert pair[0]['catalog_conditions']['plan'] == pair[1]['catalog_conditions']['plan']
        if observed_facts:
            assert pair[0]['generation'] == pair[1]['generation']
            assert pair[0]['generation_schema_sha256'] == pair[1]['generation_schema_sha256']
            shown = pair[1]['catalog_conditions']['source_observations']
            costs.append({'record_id': rec['id'], 'source_tokens': old['source_search']['source_tokens'],
                'control_tokens': len(pair[0]['token_ids']), 'candidate_tokens': len(pair[1]['token_ids']),
                'added_prompt_tokens': len(pair[1]['token_ids']) - len(pair[0]['token_ids']),
                'observed_values': len(shown['observations']),
                'unresolved_values': len(shown['unresolved_values']),
                'generation_schema_unchanged': True, 'parent_control_prompt_identical': True})
            if obligation_framing:
                assert pair[0]['catalog_conditions']['source_observations'] == shown
                costs[-1]['obligation_questions'] = len(pair[1]['catalog_conditions']['obligation_questions']['questions'])
        packets.extend(pair)
    assert source_manifest() == code and all(sha(Path(p)) == h for p, h in before.items())
    output.mkdir(parents=True, exist_ok=False)
    write_rows(output / 'contrast_packets.jsonl.gz', packets)
    write_rows(output / 'contrast_inputs.jsonl.gz', inputs)
    freeze = {'source_sha256': code, 'config': prior['config'], 'parent_sha256': before,
        'packets_sha256': sha(output / 'contrast_packets.jsonl.gz'),
        'inputs_sha256': sha(output / 'contrast_inputs.jsonl.gz'),
        'call_plan': [{'number': 0, 'request_keys': [p['request_key'] for p in packets]}],
        'primary_requests': len(packets), 'notices': len(inputs), 'arms': list(arms),
        'items': list(range(10, 19)), 'source_token_budget': prior['source_token_budget'],
        'input_tokens_max': max(len(p['token_ids']) for p in packets),
        'controls': {'source': 'Exactly identical original spans and source order for each pair.',
            'static': 'Same supplied notes, field definitions, current CPU, tokenizer and non-schema generation settings.',
            'intervention': ('Source-bound local parsed values and their explanatory instructions only; same role grammar.'
                if observed_facts else 'Role-choice prompt plus complete-role generation mask, not a mask-only comparison.'),
            'partial_join': 'Compare both fresh partial outputs against the same separately frozen baseline; omissions preserve it.',
            'classification': 'Any join using old factored responses is a mixed-response diagnostic, not a full fresh score.',
            'cohort': 'All four existing notices, no label-driven inclusion or exclusion.',
            'quality_rerolls': 0, 'old_responses_reused_for_condition_arms': False},
        'new_model_calls': 0, 'gpu_used': False, 'classification_labels_read': False,
        'production_enabled': False, 'status': 'cpu_prepared_not_launched'}
    if observed_facts:
        freeze['observed_fact_costs'] = costs
        freeze['controls']['source_budget_is_not_total_input_budget'] = True
    if obligation_framing:
        freeze['controls']['intervention'] = 'Questions make the observed value/range the explicit obligation operand;source, values, role grammar, decoder and CPU consumer unchanged.'
        freeze['obligation_framing'] = True
    with (output / 'contrast_freeze.json').open('x', encoding='utf-8') as stream:
        json.dump(freeze, stream, ensure_ascii=False, indent=2)
    return {k: freeze[k] for k in ('status', 'primary_requests', 'notices', 'input_tokens_max')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data_open/data')
    parser.add_argument('--tokenizer-dir', type=Path, default=ROOT / 'models/gemma-tokenizer')
    parser.add_argument('--observed-facts', action='store_true',
        help='Compare the existing complete-role reader with source-derived local observations; same generation grammar.')
    parser.add_argument('--obligation-framing', action='store_true',
        help='Use the observed-facts reader as control; clarify the operand of each modality decision.')
    args = parser.parse_args()
    print(json.dumps(prepare(args.parent, args.output, args.data_dir, args.tokenizer_dir,
                             observed_facts=args.observed_facts, obligation_framing=args.obligation_framing), ensure_ascii=False))


if __name__ == '__main__':
    main()
