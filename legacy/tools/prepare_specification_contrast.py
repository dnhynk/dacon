"""Freeze a source-only V9 contract comparison, with no label-based selection."""
from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.b4_entry import digest
from submission.pps.generation_contract import generation_schema, validate_grammar
from submission.pps.knowledge import Knowledge
from submission.pps.notice_search import NoticeSearch
from submission.pps.prompts import Config, output_schema
from submission.pps.specification_scope import prompts
from submission.runtime import source_manifest

IDENTITY = re.compile(r'모델명|모델[ :：]|Chipset|브랜드[ :：]|제조사[ :：(]', re.I)
PERMISSION = re.compile(r'동등|동급|대체품|대체 제품')
RELATION_CUES = {
    'replacement': re.compile(r'(?:기존|설치|장착|사용중)[^\n]{0,60}(?:교체|교환)|(?:순정품|호환성)'),
    'component': re.compile(r'Chipset|칩셋|모든\s*부품|동일\s*(?:제조사|회사)[^\n]{0,35}(?:보증|세트)|'
                            r'^[ \t]*(?:\d{1,3}[.)][ \t]*)?(?:CPU|프로세서|수신보드)[ \t]*[:：]', re.I | re.M),
}


def read_rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))


def write_rows(path, values):
    with gzip.open(path, 'wt', encoding='utf-8') as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def cohort(records):
    inventory = []
    for record in records:
        kinds = {key: any(pattern.search(doc['text']) for doc in record['docs'])
                 for key, pattern in (('identity', IDENTITY), ('permission', PERMISSION))}
        source_hash = digest([doc['text'] for doc in record['docs']])
        rank = hashlib.sha256(('specification-scope-control-v1:' + source_hash).encode()).hexdigest()
        inventory.append({'id': record['id'], **kinds, 'source_sha256': source_hash, 'control_rank': rank})
    selected = {r['id'] for r in inventory if r['identity']}
    for permission in (True, False):
        controls = sorted((r for r in inventory if not r['identity'] and r['permission'] == permission),
                          key=lambda r: (r['control_rank'], r['source_sha256']))[:4]
        selected.update(r['id'] for r in controls)
    return [r for r in records if r['id'] in selected], inventory


def relation_cohort(records):
    """Source relations plus four source-hash controls; never select by errors."""
    _, inventory = cohort(records)
    indexed = {r['id']: r for r in records}
    for entry in inventory:
        record = indexed[entry['id']]
        entry['relation_witnesses'] = [
            {'kind': kind, 'doc_index': di, 'start': m.start(), 'end': m.end(), 'text': m[0]}
            for kind, pattern in RELATION_CUES.items()
            for di, doc in enumerate(record['docs']) for m in pattern.finditer(doc['text'])]
        entry['relation_candidate'] = bool(entry['relation_witnesses']) and (entry['identity'] or entry['permission'])
    selected = {r['id'] for r in inventory if r['relation_candidate']}
    for related in (True, False):
        controls = sorted((r for r in inventory if not r['relation_candidate']
            and bool(r['identity'] or r['permission']) == related),
            key=lambda r: (r['control_rank'], r['source_sha256']))[:2]
        selected.update(r['id'] for r in controls)
    return [r for r in records if r['id'] in selected], inventory


def prepare(input_path, output, budget=4096, batch_size=16, method='lexical', relations=False, candidate_reviews=False):
    if relations and candidate_reviews:
        raise ValueError('Select one bounded structured intervention per preparation')
    if output.exists():
        raise ValueError('Preserve prior inputs; choose a fresh output directory')
    records = read_rows(input_path)
    if not records or len({r['id'] for r in records}) != len(records):
        raise ValueError('Empty or duplicate source notices')
    selected, inventory = (relation_cohort if relations or candidate_reviews else cohort)(records)
    code = source_manifest()
    input_hash = sha(input_path)
    output.mkdir(parents=True, exist_ok=False)
    save(output / 'selection_preregistered.json', {'source_sha256': code, 'input_sha256': input_hash,
        'selection': ('All source relation-cue notices with identity or permission evidence; '
            'plus2 hashed identity/permission and2 hashed no-cue controls.' if relations or candidate_reviews else
            'All source notices with explicit identity field cues; plus4 hashed permission-only and4 hashed no-cue controls.'),
        'selection_inventory': inventory, 'selected_ids': [r['id'] for r in selected],
        'source_token_cap': budget, 'source_policy': f'Fixed {method} lexical selector, identical original source in both arms.',
        'hypothesis': ('Requiring a separate review for every source-discovered syntax candidate reduces skipped names and mistaken permission scope.'
            if candidate_reviews else 'Explicit product/component/replacement links and consistency attributes improve relation consumption '
            'over both fixed factored and original typed-scope controls.' if relations else
            'Typed product role and permission target/attribute reduce unsupported scope generalization.'),
        'previous_goal_turn': 'progress: deployed applicability/citation boundary fixes, froze CPU evidence, identified scope-consumption failures.',
        'holdout_read': False, 'classification_labels_read': False, 'GPU_allocated': False,
        'adoption': 'Experimental contract only; default producer unchanged. Fresh model results required.'})
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT / 'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    knowledge = Knowledge(ROOT / 'data_open/data')
    config = dataclasses.replace(Config.load(ROOT / 'submission/model/config.json'),
                                 max_output_tokens=2048 if relations or candidate_reviews else 1200)
    packets, selections, schema_keys = [], {}, set()
    arms = (('specification_scope', 'specification_candidates') if candidate_reviews else
            ('factored', 'specification_scope', 'specification_relations') if relations else ('factored', 'specification_scope'))
    for i, rec in enumerate(selected):
        selection = NoticeSearch(rec, tokenizer).search((9,), token_budget=budget, method=method)
        selections[rec['id']] = selection
        pair = prompts(rec, knowledge, config, tokenizer, selection)
        if candidate_reviews:
            from submission.pps.specification_candidate_review import matched_prompts
            candidate_pair = matched_prompts(rec, knowledge, config, tokenizer, selection)
            assert candidate_pair['specification_scope'] == pair['specification_scope']
            pair = candidate_pair
        if relations:
            from submission.pps.specification_relations import matched_prompts
            graph_pair = matched_prompts(rec, knowledge, config, tokenizer, selection)
            assert graph_pair['specification_scope'] == pair['specification_scope']
            pair.update(graph_pair)
        assert pair[arms[0]]['spans'] == pair[arms[1]]['spans']
        if not candidate_reviews:
            assert pair[arms[0]]['messages'][1] == pair[arms[1]]['messages'][1]
        assert all(pair[a]['spans'] == pair[arms[0]]['spans'] and
                   (candidate_reviews or pair[a]['messages'][1] == pair[arms[0]]['messages'][1]) for a in arms)
        for arm in arms[i % len(arms):] + arms[:i % len(arms)]:
            prompt = pair[arm]
            spans = [dataclasses.asdict(s) for s in prompt['spans']]
            contract = generation_schema(arm, len(spans), (9,),
                specification_inventory=prompt['generation'].get('specification_inventory'))
            key = digest(contract)
            if key not in schema_keys:
                validate_grammar(contract)
                schema_keys.add(key)
            packets.append({'request_key': f'spec:{rec["id"]}:{arm}', 'record_id': rec['id'],
                'case': rec['id'], 'arm': arm, 'family': 'A', 'items': [9],
                'messages': prompt['messages'], 'token_ids': prompt['token_ids'],
                'prompt_sha256': digest(prompt['messages']), 'token_ids_sha256': digest(prompt['token_ids']),
                'source_sha256': digest(spans), 'spans': spans, 'source_layout': 'finite_units',
                'source_search': selection, 'comparison_facts': None, 'coverage': prompt['coverage'],
                'generation': prompt['generation'], 'generation_schema_sha256': digest(contract),
                'schema_sha256': digest(output_schema(arm, len(spans), (9,))),
                **({'specification_inventory': prompt['specification_inventory']}
                   if 'specification_inventory' in prompt else {})})
    # Four declared repeated cases, selected only by source hash. These are
    # extra observations, never candidates from which to pick a better answer.
    ranks = {r['id']: r['control_rank'] for r in inventory}
    repeat_ids = sorted((r['id'] for r in selected), key=lambda rid: ranks[rid])[:2 if relations or candidate_reviews else 4]
    repeats = []
    for packet in list(packets):
        if packet['record_id'] in repeat_ids:
            repeated = {**packet, 'request_key': packet['request_key'] + ':repeat',
                        'arm': packet['arm'] + '_repeat'}
            packets.append(repeated)
            repeats.append({'primary': packet['request_key'], 'repeat': repeated['request_key']})
    write_rows(output / 'contrast_inputs.jsonl.gz', selected)
    write_rows(output / 'contrast_packets.jsonl.gz', packets)
    write_rows(output / 'selections.jsonl.gz', [{'record_id': rid, 'selection': s} for rid, s in selections.items()])
    assert source_manifest() == code and sha(input_path) == input_hash
    freeze = {'source_sha256': code, 'input_sha256': input_hash, 'config': dataclasses.asdict(config),
        'packets_sha256': sha(output / 'contrast_packets.jsonl.gz'), 'inputs_sha256': sha(output / 'contrast_inputs.jsonl.gz'),
        'call_plan': [{'number': i // batch_size, 'request_keys': [p['request_key'] for p in packets[i:i+batch_size]]}
                      for i in range(0, len(packets), batch_size)],
        'cases': len(selected), 'notices': len(selected), 'primary_requests': len(packets),
        'arms': arms, 'repeated_pairs': repeats, 'source_token_budget': budget, 'source_method': method,
        'source_tokens_per_case': {rid: s['source_tokens'] for rid, s in selections.items()},
        'input_tokens_total': sum(len(p['token_ids']) for p in packets),
        'input_tokens_max': max(len(p['token_ids']) for p in packets),
        'unit_counts': {p['record_id']: len(p['spans']) for p in packets},
        'classification_labels_read': False, 'holdout_read': False, 'new_model_calls': 0,
        'controls': ('Identical original text and source token budget; source-derived candidate metadata adds prompt tokens. '
            'Same model/rubric/non-schema sampling; candidate list and exhaustive output contract are the intervention.' if candidate_reviews else
            'Identical original text and rendered user input, fixed model/rubric/sampling. Output task and schema differ.'),
        'consumption': 'Both retain their own raw model judgment; structured output receives additional provenance/relationship diagnostics.',
        'measurement_scope': 'Exposed development, selected by source only. Mixed v9 joins are not whole160 fresh inference.',
        'official_score': None}
    if candidate_reviews:
        freeze['candidate_review_intervention'] = True
    save(output / 'contrast_freeze.json', freeze)
    print(json.dumps({k: freeze[k] for k in ('cases', 'primary_requests', 'input_tokens_total', 'input_tokens_max')}, ensure_ascii=False))
    return freeze


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-budget', type=int, default=4096)
    parser.add_argument('--method', choices=('current', 'lexical'), default='lexical')
    parser.add_argument('--relations', action='store_true', help='Three-arm product relation comparison with source-only relation cohort')
    parser.add_argument('--candidate-reviews', action='store_true', help='Require each discovered specification candidate to be reviewed')
    parser.add_argument('--batch-size', type=int, default=16)
    args = parser.parse_args()
    prepare(args.input.resolve(), args.output.resolve(), args.source_budget,
            batch_size=args.batch_size, method=args.method, relations=args.relations, candidate_reviews=args.candidate_reviews)
