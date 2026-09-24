"""Freeze a fresh source/harness comparison without reading labels or outputs.

A reruns the preserved dense/facet input. B changes only original-source
selection. C proposes condition relations on B's source, to be joined to B with
abstentions preserved. All arms require new inference; no old answer is reused.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import digest, restored
from submission.pps import catalog_semantics as semantics
from submission.pps.catalog_condition_context import expand
from submission.pps.catalog_condition_review import ITEMS, _local_task_witnesses
from submission.pps.generation_contract import generation_schema
from submission.pps.knowledge import Knowledge
from submission.pps.prompts import Config, build_prompt, output_schema, token_ids
from submission.runtime import source_manifest
from tools.prepare_catalog_conditions import covering_refs, reading_capacity, sha
from tools.prepare_retrieval_contrast import write_rows
from tools.run_retrieval_contrast import read_rows, verify_packet_source

ARMS = ('A_prior_source_factored', 'B_complete_dependencies_factored', 'C_complete_dependencies_semantics')


def permissive_capacity(record, body, knowledge):
    """A mechanism probe assumes preservation, never certifies that assumption."""
    probe = reading_capacity(record, body, knowledge)
    payload = {'findings': probe.get('findings', []), 'unresolved_fields': [],
               'semantic_readings': [], 'permissions': []}
    spans = body['spans']
    for result in probe.get('details', {}).get('conditions', []):
        for issue in result.get('scope_issues', []):
            full = semantics.statement(record, issue)
            refs = covering_refs(record, spans, full)
            if not refs:
                continue
            for finding in payload['findings']:
                if finding['code'] == result['code']:
                    payload['permissions'].append({'code': finding['code'], 'field': finding['field'],
                        'source_units': refs, 'value_units': finding['value_units'],
                        'scope_units': finding['scope_units'], 'condition_scope_units': [],
                        'reason': 'CPU mechanism probe assumes field preservation; not a verified interpretation',
                        'effect': 'preserves_field'})
    if len(payload['permissions']) > 32:
        return {'status': 'schema_capacity_exceeded', 'model_calls': 0}
    packet = dict(body, family='C', generation={**body.get('generation', {}), 'response_format': semantics.FORMAT})
    row, detail = semantics.review(record, {'text': json.dumps(payload, ensure_ascii=False)}, packet, knowledge)
    return {'status': 'computed', 'assumed_response': payload, 'hypothetical_partial_updates': row,
        'details': detail, 'model_calls': 0, 'semantic_certification': False,
        'limit': 'All readable permissions hypothetically preserve the field. No human gold, Gemma output or exhaustive-structure proof.'}


def prepare(parent, output, data_dir, tokenizer_dir, parent_arm='D_seed_dense_fields_facets', *, source_roles=False):
    parent, output = Path(parent).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(parent):
        raise ValueError('Use a fresh output outside preserved evidence')
    names = ('contrast_freeze.json', 'contrast_packets.jsonl.gz', 'contrast_inputs.jsonl.gz')
    originals = {str(parent/n): sha(parent/n) for n in names}
    old_freeze = json.loads((parent/names[0]).read_text('utf8'))
    assert sha(parent/names[1]) == old_freeze['packets_sha256']
    assert sha(parent/names[2]) == old_freeze['inputs_sha256']
    inputs = read_rows(parent/names[2])
    old_packets = [p for p in read_rows(parent/names[1]) if p['arm'] == parent_arm]
    records = {r['id']: r for r in inputs}
    if len(records) != len(inputs) or {p['record_id'] for p in old_packets} != records.keys() or len(old_packets) != len(inputs):
        raise ValueError('Parent arm must have exactly one request per complete cohort notice')
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_dir, local_files_only=True, trust_remote_code=False)
    knowledge, config = Knowledge(data_dir), Config.load(ROOT/'submission/model/config.json')
    control_config = dataclasses.replace(config, response_format='factored', shared_prefix=False,
        sme_facts=False, product_facts=False, cross_source_facts=False, enable_thinking=True,
        max_output_tokens=2048, thinking_token_budget=0)
    code = source_manifest()
    prepared, audits, capacities = [], [], []
    for old in old_packets:
        record = records[old['record_id']]
        assert tuple(old['items']) == ITEMS and old['generation']['response_format'] == 'factored'
        verify_packet_source(old, record, tokenizer)
        assert token_ids(tokenizer, old['messages'], True) == old['token_ids']
        selection, audit = expand(record, old['source_search'], tokenizer, knowledge)
        if selection is None:
            raise ValueError('Cohort dependency overflow; do not silently drop this notice: '+record['id'])
        audits.append({'record_id': record['id'], **audit})
        control = build_prompt(record, knowledge, control_config, tokenizer, ITEMS, source_selection=selection)
        # Freeze one reader change: model instructions and static knowledge must
        # match the parent. Coverage diagnostics legitimately change with spans.
        assert control['messages'][0] == old['messages'][0], 'Control system changed beyond source selection'
        old_user, new_user = old['messages'][1]['content'], control['messages'][1]['content']
        marker = '\n\n[배포 법령 참고 발췌]'
        old_data = json.loads(old_user[len('[입력정보]\n'):old_user.index(marker)])
        new_data = json.loads(new_user[len('[입력정보]\n'):new_user.index(marker)])
        for key in ('발췌범위', '부재항목_검색진단'):
            old_data.pop(key); new_data.pop(key)
        assert old_data == new_data, 'Non-source control facts changed'
        source_marker = '\n\n[분석할 공고 및 첨부 원문 구간]\n'
        assert old_user.split(marker, 1)[1].split(source_marker, 1)[0] == new_user.split(marker, 1)[1].split(source_marker, 1)[0]
        semantic = semantics.prompt(record, selection, tokenizer, knowledge, source_roles=source_roles)
        capacities.append({'record_id': record['id'], **permissive_capacity(record, semantic, knowledge)})
        for arm, body, fmt in [(ARMS[0], restored(old), 'factored'), (ARMS[1], control, 'factored'),
                               (ARMS[2], semantic, semantics.FORMAT)]:
            body = copy.deepcopy(body)
            spans = [dataclasses.asdict(s) for s in body['spans']]
            packet = {**body, 'request_key': 'semantics:'+record['id']+':'+arm,
                'record_id': record['id'], 'case': record['id'], 'arm': arm,
                'family': 'C' if fmt == semantics.FORMAT else old['family'], 'spans': spans,
                'comparison_facts': None, 'source_sha256': digest(spans),
                'prompt_sha256': digest(body['messages']), 'token_ids_sha256': digest(body['token_ids']),
                'generation': {**old['generation'], **body.get('generation', {}), 'response_format': fmt},
                'schema_sha256': digest(output_schema(fmt, len(spans), ITEMS)),
                'generation_schema_sha256': digest(generation_schema(fmt, len(spans), ITEMS,
                    catalog_roles=body.get('generation', {}).get('catalog_roles'),
                    catalog_fields=body.get('generation', {}).get('catalog_fields')))}
            assert len(packet['token_ids']) + packet['generation']['max_output_tokens'] + 32 <= config.max_model_len
            verify_packet_source(packet, record, tokenizer)
            prepared.append(packet)
    assert source_manifest() == code and all(sha(p) == h for p, h in originals.items())
    output.mkdir(parents=True, exist_ok=False)
    write_rows(output/'contrast_packets.jsonl.gz', prepared)
    write_rows(output/'contrast_inputs.jsonl.gz', inputs)
    freeze = {'source_sha256': code, 'config': dataclasses.asdict(config),
        'control_prompt_config': dataclasses.asdict(control_config),
        'packets_sha256': sha(output/'contrast_packets.jsonl.gz'),
        'inputs_sha256': sha(output/'contrast_inputs.jsonl.gz'),
        'call_plan': [{'number': 0, 'request_keys': [p['request_key'] for p in prepared]}],
        'primary_requests': len(prepared), 'notices': len(inputs), 'items': list(ITEMS), 'arms': list(ARMS),
        'parent': originals, 'parent_arm': parent_arm, 'source_token_budget': 4096,
        'input_tokens_max': max(len(p['token_ids']) for p in prepared),
        'controls': {'A_B': 'Fresh inference, identical system/static facts/generation; source selection only changes.',
            'B_C': 'Identical original-source selection; changed fact-reading task plus deterministic condition consumption. Not format-only.',
            'partial_join': 'C omissions and semantic abstentions preserve fresh B, no per-record best answer.',
            'cohort': 'Entire previous four-notice diagnostic cohort, no label selection.',
            'repeats': 0, 'quality_rerolls': 0, 'old_model_responses_reused': False},
        'new_model_calls': 0, 'gpu_used': False, 'classification_labels_read': False,
        'production_enabled': False, 'source_roles_enabled': source_roles, 'status': 'cpu_prepared_not_launched'}
    for name, value in [('contrast_freeze.json', freeze), ('dependency_audit.json', audits),
                         ('permissive_capacity.json', capacities)]:
        with (output/name).open('x', encoding='utf8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
    return freeze


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, default=ROOT/'data_open/data')
    parser.add_argument('--tokenizer-dir', type=Path, default=ROOT/'models/gemma-tokenizer')
    parser.add_argument('--source-roles', action='store_true', help='Constrain complete original property/task/permission reference choices')
    args = parser.parse_args()
    result = prepare(args.parent, args.output, args.data_dir, args.tokenizer_dir, source_roles=args.source_roles)
    print(json.dumps({k: result[k] for k in ('status', 'notices', 'primary_requests', 'input_tokens_max')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
