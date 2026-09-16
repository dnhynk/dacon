"""Freeze one canonical source-question candidate and a full-cohort A10 control.

Other profiles retain the same source, prompt and generation contracts. The
normal path is standalone; the A10 control holds the other new responses fixed
and is reported as a component comparison, never a second standalone run.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from submission.b4_entry import B4Pipeline, PROFILES, digest
from submission.pps.data import records
from submission.pps.generation_contract import prepared_preflight
from submission.pps.legal_query_contract import verify_prepared as verify_legal
from submission.pps.source_questions import validate as validate_questions
from submission.runtime import Journal, call_plan, sha256, source_manifest
from tools.prepare_runtime_context import native_identity, source_cost

OPTIONS = {'source_policy': 'purchase_context', 'legal_policy': 'direct_production',
    'specification_review': 'gated_source_candidates', 'catalog_review': 'explicit',
    'software_review': 'current', 'a10_thinking_budget': 768, 'a_cohort_size': 32,
    'a10_question_policy': 'source_questions'}
NORMAL = 'source_questions768_catalog_explicit_v9_gated'
CONTROL = 'current_questions768_catalog_explicit_v9_gated'


def prepare(input_path, data_dir, tokenizer_dir, output):
    from transformers import AutoTokenizer
    import llguidance  # noqa: F401 -- fail before preparing an unexecutable grammar
    began = time.monotonic()
    journal = Journal(output)
    code = source_manifest()
    recs = list(records(input_path))
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_dir, local_files_only=True, trust_remote_code=False)
    candidate = B4Pipeline(data_dir, tokenizer, **OPTIONS)
    control = B4Pipeline(data_dir, tokenizer, **{**OPTIONS, 'a10_question_policy': 'current'})
    journal.save('preregistered.json', {'normal_policy': NORMAL, 'options': OPTIONS,
        'config': dataclasses.asdict(candidate.config), 'records': len(recs),
        'record_ids': [r['id'] for r in recs], 'labels_read': False, 'quality_rerolls': 0,
        'maximum_format_retries': candidate.config.max_response_retries,
        'normal_primary_order_unaffected_by_probes': True,
        'source_budget': 'same exact original-source spans in every profile; no additional quoted source',
        'control_scope': 'full-cohort new A10 control, other new normal responses fixed; not standalone',
        'normal_code_only_calls_are_not_model_responses': True,
        'normal_csv_frozen_before_control_calls': True})
    bundles, probes, source_report = [], [], []
    controls, aliases = {}, {}
    for position, rec in enumerate(recs):
        base = {p['batch']: p for p in control.bundle(rec)}
        bundle = candidate.bundle(rec)
        current = {p['batch']: p for p in bundle}
        assert base.keys() == current.keys()
        for profile in base:
            if profile != 'A10' and base[profile] != current[profile]:
                raise ValueError('Source questions changed another profile: ' + profile)
            if current[profile]['spans'] != base[profile]['spans']:
                raise ValueError('Source questions changed original source selection')
        a10 = current['A10']
        if 'source_questions' in a10:
            validate_questions(rec, a10, candidate.knowledge)
        verify_legal(a10, rec, candidate.knowledge, tokenizer)
        # A code-only packet can have identical base tokens but has no native
        # response. It cannot stand in for a requested control model call.
        code_only = 'source_questions' in a10 and not a10['source_questions']['model_items']
        logical = 'probe:current_questions:' + base['A10']['request_key']
        if not code_only and native_identity(a10) == native_identity(base['A10']):
            controls[rec['id']] = a10['request_key']
            aliases[logical] = a10['request_key']
        else:
            probes.append({**copy.deepcopy(base['A10']), 'request_key': logical,
                           'probe_role': 'current_questions', 'input_position': position})
            controls[rec['id']] = logical
        bundles.append(bundle)
        source_report.append({'record_id': rec['id'],
            'original_source_tokens': source_cost(a10, tokenizer),
            'base_A10_input_tokens': len(base['A10']['token_ids']),
            'candidate_A10_input_tokens': len(a10['token_ids']),
            'model_items': a10.get('source_questions', {}).get('model_items', a10['items']),
            'source_fixed_items': sorted(a10.get('source_questions', {}).get('fixed', {})),
            'code_only': code_only, 'fallback': a10.get('source_questions_fallback'),
            'conditional_products': len(a10.get('source_questions', {}).get('condition_questions', [])),
            'source_scope': a10.get('source_questions', {}).get('source_purchase_status')})
        if (position + 1) % 20 == 0:
            journal.progress(phase='source_questions_preparation', notices=position + 1,
                             total=len(recs), gpu_allocated=False, seconds=time.monotonic() - began)
    primary = [p for profile in (*PROFILES, 'Q10', 'W20', 'S9')
               for bundle in bundles for p in bundle if p['batch'] == profile]
    normal_keys = [p['request_key'] for p in primary]
    control_keys = [controls[p['record_id']] if p['batch'] == 'A10' else p['request_key'] for p in primary]
    plan = [{'role': 'current_questions', 'profile': 'A10',
             'request_keys': [p['request_key'] for p in probes[offset:offset+32]]}
            for offset in range(0, len(probes), 32)]
    for name, values in (('current_inputs.jsonl.gz', recs), ('primary_packets.jsonl.gz', primary),
                         ('probe_packets.jsonl.gz', probes)):
        journal.rows(name, values)
    journal.save('recipes.json', {NORMAL: normal_keys, CONTROL: control_keys})
    journal.save('contrasts.json', [('unresolved_source_questions', CONTROL, NORMAL)])
    journal.save('native_aliases.json', aliases)
    journal.save('probe_plan.json', {'batches': plan, 'normal_parent_repeats_never_used_in_recipes': True})
    journal.save('normal_call_plan.json', call_plan(recs, primary, a_cohort_size=32))
    journal.save('source_budget_report.json', source_report)
    report = {'records': len(recs), 'primary_prepared': len(primary), 'probe_requests': len(probes),
        'policies': 2, 'normal_code_only_A10': sum(r['code_only'] for r in source_report),
        'context_fallbacks': sum(r['fallback'] is not None for r in source_report),
        'model_judgments': sum(len(r['model_items']) for r in source_report),
        'source_fixed_judgments': sum(len(r['source_fixed_items']) for r in source_report),
        'base_A10_input_tokens': sum(r['base_A10_input_tokens'] for r in source_report),
        'candidate_A10_input_tokens_excluding_code_only': sum(r['candidate_A10_input_tokens'] for r in source_report if not r['code_only']),
        'max_input_tokens': max(len(p['token_ids']) for p in primary + probes),
        'seconds': time.monotonic() - began, 'labels_read': False, 'new_model_calls': 0,
        'gpu_allocated': False, 'normal_options': OPTIONS}
    journal.save('preparation_report.json', report)
    journal.save('generation_preflight.json', prepared_preflight(primary + probes))
    if source_manifest() != code:
        raise RuntimeError('Canonical source changed during preparation')
    journal.save('input_freeze.json', {'source_sha256': code, 'original_input_sha256': sha256(input_path),
        'records': len(recs), 'labels_read': False,
        'files': {p.name: sha256(p) for p in journal.root.iterdir() if p.is_file()}})
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('input', 'data-dir', 'tokenizer-dir', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.input, args.data_dir, args.tokenizer_dir, args.output),
                     ensure_ascii=False, indent=2))
