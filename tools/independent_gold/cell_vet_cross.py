"""Prepare/score dev-only cross-family blind vetting; deliberately has no call command."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.independent_gold import cell_prompts, cell_score, cell_vet
from tools.independent_gold import claude_full_record_annotator as base

CRITERIA = {
    'violation_teacher_one_rate_min': 0.70,
    'near_miss_teacher_zero_rate_min': 0.80,
    'U_policy': 'U remains in the denominator and never counts as the expected label',
    'missing_policy': 'report missing separately; no pass/fail verdict until the stratum is complete',
    'failure_action': 'defect analysis by operation and writer family; do not silently relabel',
}
MODELS = {'claude': 'claude-opus-5', 'gpt': 'gpt-6-astra'}
# G1 v9: small-quote host exclusions, size deletion/narrowing/broadening,
# v4/v9 clause eligibility, decimal amounts, and all transplant insertion anchors.
FILTER_ITEMS = ('v4', 'v9', 'v10', 'v11', 'v13', 'v15', 'v16', 'v17', 'v18')


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def file_hash(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def selection(rows, *, filter_scope='v9'):
    buckets = defaultdict(list)
    for row in rows:
        if row['split'] != 'dev' or row['kind'] not in {'edited', 'transplanted'}:
            continue
        item, op = row['target_item'], row['operation']
        mode = 'near_miss' if row.get('near_miss_items') else 'violation'
        if op == 'written':
            buckets[('written', row['writer_family'], item, mode)].append(row)
        else:
            if row.get('near_miss_items'):
                buckets[('near_miss', op, item, mode)].append(row)
            if 'retarget' in op or op.startswith('O7'):
                buckets[('o7', op, item, mode)].append(row)
            affected = (item in ('v4', 'v9', 'v13', 'v17') if filter_scope == 'core'
                        else (item in FILTER_ITEMS or op.startswith('transplant:')) and not op.startswith('retarget_procurement'))
            if affected:
                buckets[('changed_filter', op, item, mode)].append(row)
    # Include absent written strata explicitly instead of silently claiming full coverage.
    for family in ('gpt', 'claude'):
        for item in cell_score.ITEMS:
            for mode in ('violation', 'near_miss'):
                buckets.setdefault(('written', family, item, mode), [])
    selected, inventory = {}, []
    for stratum, candidates in sorted(buckets.items()):
        quota = 3 if stratum[0] == 'written' else 6 if stratum[0] == 'o7' else 4
        chosen = sorted(candidates, key=lambda r: cell_vet._rank('cross-v10', *stratum, r['plant_id']))[:quota]
        name = ':'.join(stratum)
        inventory.append({'stratum': name, 'available': len(candidates), 'requested': quota,
                          'selected': len(chosen), 'shortfall': max(0, quota - len(chosen))})
        for row in chosen:
            key = (row['plant_id'], cell_prompts.group_of(row['target_item']))
            if key not in selected:
                selected[key] = {'row': row, 'strata': []}
            selected[key]['strata'].append(name)
    return list(selected.values()), inventory


def prepare(args):
    output = args.output_dir.resolve()
    if output.exists() or ROOT / 'runs' not in output.parents:
        raise ValueError('output must be fresh under runs/')
    rows = cell_score._rows(args.plants, 'dev')  # Filter before JSON decoding; sealed records never enter.
    filter_scope = getattr(args, 'filter_scope', 'v9')
    chosen, inventory = selection(rows, filter_scope=filter_scope)
    supplement = None
    if getattr(args, 'exclude_plan', None):
        parent = json.loads(args.exclude_plan.read_text(encoding='utf-8'))['tasks']
        excluded = {entry['task_id'] for entry in parent}
        def task_id(entry):
            row = entry['row']
            return 't-' + cell_vet._rank('cross-blind', row['plant_id'], cell_prompts.group_of(row['target_item']))[:24]
        chosen = [entry for entry in chosen if task_id(entry) not in excluded]
        candidates = len(chosen)
        chosen.sort(key=lambda entry: cell_vet._rank('supplement-cap', task_id(entry)))
        chosen = chosen[:args.max_new]
        assert all(entry['row']['operation'] != 'written' for entry in chosen)
        supplement = {'parent_plan_sha256': file_hash(args.exclude_plan), 'parent_tasks': len(excluded),
                      'new_candidates': candidates, 'max_unique_tasks': args.max_new,
                      'unique_new_tasks': len(chosen), 'task_id_overlap': 0,
                      'selection': 'v9 affected operations; exclude parent task IDs then fixed supplement-cap hash rank'}
        for entry in inventory:
            entry['selected_in_supplement'] = sum(entry['stratum'] in chosen_entry['strata'] for chosen_entry in chosen)
    output.mkdir(parents=True)
    rubric = base.annotation_rubric(base.RUBRIC_PATH.read_text(encoding='utf-8'))
    criteria = {'fixed_utc': datetime.now(timezone.utc).isoformat(), 'criteria': CRITERIA,
                'models': MODELS, 'selection_inventory': inventory,
                'selection_seed': 'cross-v10', 'filter_scope': filter_scope,
                'supplement': supplement, 'split': 'dev', 'new_model_calls': 0}
    write_json(output / 'criteria.json', criteria)  # Immutable criteria written before task generation.
    catalog = base.full_record_context.fact_context.catalog_facts.CatalogIndex.load()
    qualifications = base.full_record_context.qualification_context.qualification_facts.CatalogReference.load()
    tasks, plan = {'claude': [], 'gpt': []}, []
    for entry in chosen:
        row = entry['row']
        group = cell_prompts.group_of(row['target_item'])
        task_id = 't-' + cell_vet._rank('cross-blind', row['plant_id'], group)[:24]
        record = copy.deepcopy(row['record'])
        original_id = record['id']
        record['id'] = 'N-' + cell_vet._rank('notice-alias', row['plant_id'])[:20]
        call = cell_prompts.build_group_call(record, cell_prompts.GROUPS[group], rubric=rubric,
                                            catalog_index=catalog, qualification_catalog=qualifications)
        schema = json.loads(call['schema_json'])
        decisions = schema['properties']['records']['items']['properties']['annotation']['properties']['decisions']
        for item_schema in decisions['properties'].values():
            item_schema['required'].append('evidence_quotes')
            item_schema['properties']['evidence_quotes'] = {'type': 'array', 'items': {'type': 'string', 'minLength': 1}}
        call['schema_json'] = base.canonical_json(schema)
        call['system'] += ('\nFor each decision return evidence_quotes as exact substrings of the supplied documents. '
                           'For a binary label provide at least one supporting quote; for U provide an item-specific '
                           'material_missing_information reason and any available supporting quotes.')
        prompt_sha = base.sha256_object({k: call[k] for k in ('system', 'prompt', 'schema_json', 'prompt_argument')})
        family = row.get('writer_family', 'deterministic') if row['operation'] == 'written' else 'deterministic'
        teachers = ['claude'] if family == 'gpt' else ['gpt'] if family == 'claude' else ['claude', 'gpt']
        public = {'task_id': task_id, 'group': group, 'record_id': record['id'],
                  'items': list(cell_prompts.GROUPS[group]), **call, 'prompt_sha256': prompt_sha,
                  'expected_output': 'JSON matching schema_json; 1/0/U, exact evidence_quotes, U reason'}
        # Only these task files are sent to teachers. The private plan must never be attached.
        for teacher in teachers:
            tasks[teacher].append({**public, 'model': MODELS[teacher]})
        plan.append({'task_id': task_id, 'record_id': record['id'], 'source_record_id': original_id,
                     'group': group, 'items': public['items'], 'target_item': row['target_item'],
                     'operation': row['operation'], 'writer_family': family,
                     'writer_model': row.get('writer_model'), 'writer_round': row.get('writer_round', 'W5' if family != 'deterministic' else None),
                     'mode': 'near_miss' if row.get('near_miss_items') else 'violation',
                     'teachers': teachers, 'strata': entry['strata'], 'prompt_sha256': prompt_sha,
                     'documents': [d['text'] for d in record['docs']]})
    for family, entries in tasks.items():
        with (output / f'tasks_{family}.jsonl').open('x', encoding='utf-8') as handle:
            for task in sorted(entries, key=lambda t: cell_vet._rank('presentation', t['task_id'])):
                handle.write(base.canonical_json(task) + '\n')
        (output / f'responses_{family}.jsonl').write_text('', encoding='utf-8')
    write_json(output / 'plan_private.json', {'tasks': plan})
    manifest = {**criteria, 'criteria_sha256': file_hash(output / 'criteria.json'),
                'plants_sha256': file_hash(args.plants), 'rubric_sha256': base.sha256_text(rubric),
                'plan_sha256': file_hash(output / 'plan_private.json'),
                'tasks_by_teacher': {f: len(t) for f, t in tasks.items()},
                'unique_tasks': len(plan), 'deterministic_shared_tasks': sum(len(p['teachers']) == 2 for p in plan),
                'tasks_sha256': {f: file_hash(output / f'tasks_{f}.jsonl') for f in tasks}}
    write_json(output / 'manifest.json', manifest)
    write_json(output / 'response_format.json', {
        'one_json_object_per_line': {'task_id': 'copy from task', 'model': 'exact task.model',
            'prompt_sha256': 'copy from task', 'structured_output': 'object matching task.schema_json'},
        'policy': 'one final response per task; preserve raw native receipts separately; duplicates rejected',
        'teacher_files_only': ['tasks_claude.jsonl', 'tasks_gpt.jsonl'],
        'private_do_not_send': ['plan_private.json', 'manifest.json', 'criteria.json', 'response_format.json']})
    return {k: manifest[k] for k in ('tasks_by_teacher', 'unique_tasks', 'deterministic_shared_tasks')}


def aggregate(counts, expected, threshold):
    answered = counts['0'] + counts['1'] + counts['U']
    rate = counts[str(expected)] / answered if answered else None
    complete = counts['missing'] == 0 and answered > 0
    return {'n': answered, 'planned': answered + counts['missing'], 'missing': counts['missing'],
            'U': counts['U'], 'teacher_one_rate': counts['1'] / answered if answered else None,
            'teacher_zero_rate': counts['0'] / answered if answered else None,
            'expected_rate': rate, 'threshold': threshold, 'complete': complete,
            'valid': rate >= threshold if complete else None}


class CitationFormError(ValueError):
    """Labels are schema-valid but the span/quote bookkeeping is not; scored as U, never as the expected label."""


def validate_response(response, task, entry):
    import jsonschema
    if response.get('model') != task['model'] or response.get('prompt_sha256') != task['prompt_sha256']:
        raise ValueError('response model/prompt binding differs')
    structured = response['structured_output']
    jsonschema.validate(structured, json.loads(task['schema_json']))
    labels = cell_prompts.decisions_of(structured, entry['record_id'], entry['items'])
    annotation = structured['records'][0]['annotation']
    context = json.loads(task['prompt'].split('BATCH_SOURCE_CONTEXT:\n', 1)[1])
    registry = context['records'][0]['source_span_text']
    declared = annotation['source_span_ids']
    if len(set(declared)) != len(declared) or not set(declared) <= registry.keys():
        raise CitationFormError('declared source spans are duplicate or unknown')
    used = set()
    for item, decision in annotation['decisions'].items():
        quotes = decision['evidence_quotes']
        premises = decision['premise_span_ids']
        if not set(premises) <= set(declared):
            raise CitationFormError('premise spans were not declared')
        used.update(premises)
        evidence_id = decision['positive_evidence_span_id']
        if evidence_id is not None and evidence_id not in premises:
            raise CitationFormError('positive evidence span is not a premise')
        if labels[item] != 'U' and not quotes:
            raise CitationFormError('binary decision lacks source quote')
        if any(not q.strip() or not any(q in doc for doc in entry['documents']) for q in quotes):
            raise CitationFormError('evidence quote is not a source substring')
        if labels[item] == 'U' and not (decision['material_missing_information'] or '').strip():
            raise CitationFormError('U lacks a material missing-information reason')
        if labels[item] != 'U' and not premises:
            raise CitationFormError('binary decision lacks source premises')
    if used != set(declared):
        raise CitationFormError('declared source spans include unused spans')
    return labels


def score(args):
    output = args.output_dir
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    if file_hash(output / 'criteria.json') != manifest['criteria_sha256'] or file_hash(output / 'plan_private.json') != manifest['plan_sha256']:
        raise ValueError('frozen criteria/plan changed')
    criteria = json.loads((output / 'criteria.json').read_text(encoding='utf-8'))['criteria']
    plan = json.loads((output / 'plan_private.json').read_text(encoding='utf-8'))['tasks']
    answers, by_stratum, grouped, disagreements = {}, defaultdict(Counter), defaultdict(Counter), []
    format_invalid = []
    for family in MODELS:
        path = output / f'tasks_{family}.jsonl'
        if file_hash(path) != manifest['tasks_sha256'][family]:
            raise ValueError('frozen teacher tasks changed')
        with path.open(encoding='utf-8') as handle:
            tasks = {r['task_id']: r for r in map(json.loads, handle)}
        entries = {e['task_id']: e for e in plan if family in e['teachers']}
        with (output / f'responses_{family}.jsonl').open(encoding='utf-8') as handle:
            for line in handle:
                if not line.strip():
                    continue
                response = json.loads(line)
                tid = response['task_id']
                if tid not in tasks or (family, tid) in answers:
                    raise ValueError('unknown, wrong-family or duplicate response task')
                try:
                    answers[family, tid] = validate_response(response, tasks[tid], entries[tid])
                except CitationFormError as exc:
                    answers[family, tid] = {item: 'U' for item in entries[tid]['items']}
                    format_invalid.append({'task_id': tid, 'teacher': family, 'error': str(exc)})
    for entry in plan:
        mode, item = entry['mode'], entry['target_item']
        expected = int(mode == 'violation')
        for teacher in entry['teachers']:
            labels = answers.get((teacher, entry['task_id']))
            label = str(labels[item]) if labels else 'missing'
            for stratum in entry['strata']:
                by_stratum[teacher + ':' + stratum][label] += 1
            group = teacher + ':' + (entry['writer_family'] if entry['operation'] == 'written' else entry['operation']) + ':' + mode
            grouped[group][label] += 1
            if labels and labels[item] != expected:
                disagreements.append({'task_id': entry['task_id'], 'teacher': teacher, 'item': item,
                                      'mode': mode, 'label': labels[item]})
    def results(groups):
        return {name: aggregate(counts, int(name.endswith(':violation')),
                    criteria['violation_teacher_one_rate_min' if name.endswith(':violation') else 'near_miss_teacher_zero_rate_min'])
                for name, counts in sorted(groups.items())}
    agreement = Counter()
    for entry in plan:
        if len(entry['teachers']) != 2:
            continue
        left, right = (answers.get((f, entry['task_id'])) for f in ('claude', 'gpt'))
        agreement['planned'] += 1
        if left is None or right is None:
            agreement['missing_pair'] += 1
            continue
        a, b = left[entry['target_item']], right[entry['target_item']]
        agreement['answered_pairs'] += 1
        agreement['exact_agree_including_U'] += int(a == b)
        agreement['both_binary'] += int(a != 'U' and b != 'U')
        agreement['binary_agree'] += int(a != 'U' and b != 'U' and a == b)
    result = {'responses': len(answers), 'criteria': criteria, 'by_stratum': results(by_stratum),
              'by_writer_or_operation': results(grouped), 'deterministic_agreement': dict(agreement),
              'disagreements': disagreements, 'format_invalid_scored_as_U': format_invalid,
              'independent_gold': False}
    write_json(output / args.score_name, result)
    return {'responses': len(answers), 'strata': len(by_stratum), 'score_path': str(output / args.score_name)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--plants', type=Path, required=True)
    prep.add_argument('--output-dir', type=Path, required=True)
    prep.add_argument('--filter-scope', choices=('core', 'v9'), default='v9')
    prep.add_argument('--exclude-plan', type=Path, help='private parent plan; exclude its task IDs for a pre-call supplement')
    prep.add_argument('--max-new', type=int, default=40)
    scoring = sub.add_parser('score')
    scoring.add_argument('--output-dir', type=Path, required=True)
    scoring.add_argument('--score-name', default='score.json')
    args = parser.parse_args()
    print(json.dumps({'prepare': prepare, 'score': score}[args.command](args), ensure_ascii=False))


if __name__ == '__main__':
    main()
