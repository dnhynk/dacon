"""Freeze fresh Gemma input from measured search results, without reading labels."""
import argparse
import dataclasses
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import digest
from submission.pps.data import records
from submission.pps.knowledge import Knowledge
from submission.pps.prompts import Config, build_prompt, output_schema
from submission.runtime import source_manifest


def read_rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))


def write_rows(path, rows):
    with gzip.open(path, 'wt', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--retrieval', type=Path, required=True)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--case-manifest', type=Path, help='Original frozen human-review case manifest for a later search comparison')
    p.add_argument('--arms', nargs='+', help='Exactly the measured search arms to compare')
    p.add_argument('--exclude-identical-source', action='store_true')
    p.add_argument('--batch-size', type=int, default=18)
    args = p.parse_args()
    if not 1 <= args.batch_size <= 18:
        raise ValueError('Pilot batches must have between 1 and 18 requests')
    args.output.mkdir(parents=True, exist_ok=False)
    registered = json.loads((args.retrieval / 'preregistered.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(args.input.read_bytes()).hexdigest() == registered['input_sha256']
    native_inputs = read_rows(args.input) if args.input.name.endswith('.jsonl.gz') else records(args.input)
    inputs = {r['id']: r for r in native_inputs}
    if args.case_manifest:
        assert hashlib.sha256(args.case_manifest.read_bytes()).hexdigest() == registered['baseline_manifest_sha256']
        cases = json.loads(args.case_manifest.read_text(encoding='utf-8'))['cases']
    else:
        cases = registered['cases']
    found = {}
    for row in read_rows(args.retrieval / 'retrieval_results.jsonl.gz'):
        m = row['measurement']
        budget = registered.get('source_budgets', {}).get(m['task_id'], 4096)
        if m['budget'] == budget:
            key = (m['task_id'], m['arm'])
            assert key not in found, 'Ambiguous retrieval arm'
            found[key] = row['retrieval']
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT / 'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    knowledge = Knowledge(ROOT / 'data_open/data')
    config = dataclasses.replace(Config.load(ROOT / 'submission/model/config.json'),
        shared_prefix=False, sme_facts=False, product_facts=False, cross_source_facts=False,
        response_format='factored', thinking_items=(), thinking_token_budget=0)
    arms = tuple(args.arms or ('A_current', 'A_lexical_context', 'C_hybrid_context'))
    if len(arms) < 2 or len(set(arms)) != len(arms):
        raise ValueError('Select at least two distinct arms')
    packets, skipped, included = [], [], []
    for ci, case in enumerate(cases):
        items = tuple(case['items'])
        rec = inputs[case['record_id']]
        selections = [found[(case['task_id'], arm)] for arm in arms]
        assert all(s['source_token_budget'] == selections[0]['source_token_budget'] for s in selections)
        if args.exclude_identical_source and all(s['spans'] == selections[0]['spans'] for s in selections):
            skipped.append({'case': case['task_id'], 'reason': 'identical original source spans across arms'})
            continue
        included.append(case['task_id'])
        controls = []
        # Rotate arm order by case to avoid always giving one arm the earliest
        # scheduler/cache position. No sampling or quality rerolls.
        for arm in arms[ci % len(arms):] + arms[:ci % len(arms)]:
            selection = found[(case['task_id'], arm)]
            prompt = build_prompt(rec, knowledge, config, tokenizer, items, source_selection=selection)
            spans = [dataclasses.asdict(s) for s in prompt['spans']]
            generation = {'response_format': config.response_format, 'thinking_budget': 0,
                          'max_output_tokens': config.max_output_tokens}
            controls.append({'system': prompt['messages'][0], 'generation': generation, 'items': items})
            from submission.pps.generation_contract import generation_schema
            packets.append({'request_key': f"contrast:{case['task_id']}:{arm}", 'record_id': rec['id'],
                'case': case['task_id'], 'arm': arm, 'family': 'L' if items == (20,) else 'A',
                'items': list(items), 'messages': prompt['messages'], 'token_ids': prompt['token_ids'],
                'prompt_sha256': digest(prompt['messages']), 'token_ids_sha256': digest(prompt['token_ids']),
                'source_sha256': digest(spans), 'spans': spans, 'source_search': selection,
                'comparison_facts': None, 'coverage': prompt['coverage'], 'generation': generation,
                'generation_schema_sha256': digest(generation_schema(config.response_format, len(spans), items)),
                'schema_sha256': digest(output_schema(config.response_format, len(spans), items))})
        assert all(c == controls[0] for c in controls)
    if not packets:
        raise ValueError('No changed source inputs require fresh inference')
    plan = [{'number': n // args.batch_size, 'request_keys': [p['request_key'] for p in packets[n:n+args.batch_size]]}
            for n in range(0, len(packets), args.batch_size)]
    write_rows(args.output / 'contrast_packets.jsonl.gz', packets)
    recs = [r for r in inputs.values() if r['id'] in {p['record_id'] for p in packets}]
    write_rows(args.output / 'contrast_inputs.jsonl.gz', recs)
    freeze = {'source_sha256': source_manifest(), 'config': dataclasses.asdict(config),
        'packets_sha256': hashlib.sha256((args.output / 'contrast_packets.jsonl.gz').read_bytes()).hexdigest(),
        'inputs_sha256': hashlib.sha256((args.output / 'contrast_inputs.jsonl.gz').read_bytes()).hexdigest(),
        'call_plan': plan, 'cases': len(included), 'included_cases': included, 'skipped_cases': skipped,
        'notices': len(recs), 'primary_requests': len(packets),
        'arms': arms, 'source_token_budgets': {c: found[(c, arms[0])]['source_token_budget'] for c in included},
        'classification_labels_read': False,
        'rubric': 'v6 shared across arms', 'postprocessing': 'same canonical B4Pipeline.consume for all arms',
        'evidence_metric_source': str(args.retrieval), 'official_score': False,
        'measured_retrieval_sha256': hashlib.sha256((args.retrieval/'retrieval_results.jsonl.gz').read_bytes()).hexdigest(),
        'measurement_manifest_sha256': hashlib.sha256((args.retrieval/'preregistered.json').read_bytes()).hexdigest(),
        'input_tokens': {arm: sum(len(p['token_ids']) for p in packets if p['arm'] == arm) for arm in arms},
        'input_tokens_max': max(len(p['token_ids']) for p in packets),
        'source_tokens': {arm: sum(p['source_search']['source_tokens'] for p in packets if p['arm'] == arm) for arm in arms},
        'controls': 'System instructions, per-case items, fixed-model config, generation, CPU consumer identical. S enum size follows real returned source spans. Metadata and static catalog hints are constant within each case.',
        'limitations': '12 exposed review cases / 11 notices; this is a diagnostic fresh inference contrast, not full160 or official Macro F1.'}
    (args.output / 'contrast_freeze.json').write_text(json.dumps(freeze, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in freeze.items() if k not in {'source_sha256','config','call_plan'}}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
