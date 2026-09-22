import argparse
from collections import Counter
import json

import pytest

from tools.independent_gold import cell_vet_cross as vet


def row(pid, item='v4', operation='written', family='gpt', near=False, split='dev'):
    return {'plant_id': pid, 'target_item': item, 'operation': operation, 'writer_family': family,
            'split': split, 'kind': 'edited', 'near_miss_items': [item] if near else [],
            'record': {'id': 'HOST--written-' + pid, 'meta': {}, 'docs': [{'text': 'source quote'}]}}


def test_sampling_cross_family_quotas_and_sealed_exclusion():
    rows = [row(str(i)) for i in range(9)] + [row('sealed', split='holdout')]
    rows += [row('n' + str(i), operation='near_miss:size', near=True) for i in range(8)]
    rows += [row('o' + str(i), item='v10', operation='retarget_procurement:v10') for i in range(10)]
    rows += [row('deleted', item='v18', operation='delete'), row('amount', item='v3', operation='transplant:v3')]
    chosen, inventory = vet.selection(rows)
    assert chosen == vet.selection(list(reversed(rows)))[0]
    assert not any(e['row']['plant_id'] == 'sealed' for e in chosen)
    counts = {r['stratum']: r['selected'] for r in inventory}
    assert counts['written:gpt:v4:violation'] == 3
    assert counts['near_miss:near_miss:size:v4:near_miss'] == 4
    assert counts['o7:retarget_procurement:v10:v10:violation'] == 6
    assert counts['written:claude:v23:near_miss'] == 0
    assert counts['changed_filter:delete:v18:violation'] == 1
    assert counts['changed_filter:transplant:v3:v3:violation'] == 1


def test_U_and_missing_never_become_success():
    assert vet.aggregate(Counter({'1': 7, 'U': 3}), 1, .7)['valid'] is True
    assert vet.aggregate(Counter({'0': 7, 'U': 3}), 0, .8)['valid'] is False
    partial = vet.aggregate(Counter({'0': 8, 'missing': 2}), 0, .8)
    assert partial['valid'] is None and partial['planned'] == 10


def test_prepare_blinds_ids_and_separates_cross_family_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(vet, 'ROOT', tmp_path)
    plants = tmp_path / 'plants.jsonl'
    plants.write_text('dev-only-test', encoding='utf-8')
    rows = [row('G', family='gpt'), row('C', family='claude'), row('D', operation='near_miss:size', near=True)]
    monkeypatch.setattr(vet.cell_score, '_rows', lambda path, split: rows if split == 'dev' else pytest.fail())
    monkeypatch.setattr(vet.base.full_record_context.fact_context.catalog_facts.CatalogIndex, 'load', lambda: object())
    monkeypatch.setattr(vet.base.full_record_context.qualification_context.qualification_facts.CatalogReference, 'load', lambda: object())
    def call(record, items, **kwargs):
        return {'system': 'rubric', 'prompt': json.dumps(record),
                'schema_json': json.dumps(vet.cell_prompts.group_schema(items)),
                'prompt_argument': 'decide', 'context_sha256': 'fake'}
    monkeypatch.setattr(vet.cell_prompts, 'build_group_call', call)
    output = tmp_path / 'runs/vet'
    output.parent.mkdir()
    vet.prepare(argparse.Namespace(plants=plants, output_dir=output))
    tasks = {f: [json.loads(line) for line in (output / f'tasks_{f}.jsonl').read_text().splitlines()]
             for f in ('gpt', 'claude')}
    plan = json.loads((output / 'plan_private.json').read_text())['tasks']
    for entry in plan:
        expected = ['claude'] if entry['writer_family'] == 'gpt' else ['gpt'] if entry['writer_family'] == 'claude' else ['claude', 'gpt']
        assert entry['teachers'] == expected
        for family in expected:
            task = next(t for t in tasks[family] if t['task_id'] == entry['task_id'])
            assert 'written' not in task['prompt'] and 'HOST' not in task['prompt']
            assert not {'writer_family', 'mode', 'operation', 'target_item', 'binding'} & task.keys()
    a = next(t for t in tasks['gpt'] if any(p['task_id'] == t['task_id'] and len(p['teachers']) == 2 for p in plan))
    b = next(t for t in tasks['claude'] if t['task_id'] == a['task_id'])
    assert a['prompt_sha256'] == b['prompt_sha256'] and a['prompt'] == b['prompt']
    result = vet.score(argparse.Namespace(output_dir=output, score_name='pending.json'))
    assert result['responses'] == 0
    score = json.loads((output / 'pending.json').read_text())
    assert all(r['valid'] is None for r in score['by_stratum'].values())
    external = output / 'responses_claude.jsonl'
    external.write_text('owned by a concurrent teacher run', encoding='utf-8')
    rows.extend(row('new-' + str(i), item='v18', operation='delete') for i in range(8))
    supplement = tmp_path / 'runs/supplement'
    result = vet.prepare(argparse.Namespace(plants=plants, output_dir=supplement,
                         exclude_plan=output / 'plan_private.json', max_new=2))
    assert result['unique_tasks'] == 2 and result['tasks_by_teacher'] == {'claude': 2, 'gpt': 2}
    supplement_plan = json.loads((supplement / 'plan_private.json').read_text())['tasks']
    assert not {p['task_id'] for p in plan} & {p['task_id'] for p in supplement_plan}
    assert external.read_text() == 'owned by a concurrent teacher run'
    (output / 'criteria.json').write_text('{}')
    with pytest.raises(ValueError, match='frozen criteria'):
        vet.score(argparse.Namespace(output_dir=output, score_name='refused.json'))


def test_response_rejects_binding_fabricated_quotes_and_reasonless_U():
    schema = vet.cell_prompts.group_schema(['v24'])
    ds = schema['properties']['records']['items']['properties']['annotation']['properties']['decisions']['properties']['v24']
    ds['required'].append('evidence_quotes')
    ds['properties']['evidence_quotes'] = {'type': 'array', 'items': {'type': 'string'}}
    decision = {'label': 1, 'confidence': 'H', 'rationale': 'source', 'premise_span_ids': ['s1'],
                'exception_analysis': None, 'completeness': 'complete', 'material_missing_information': None,
                'positive_evidence_span_id': 's1', 'evidence_quotes': ['source quote']}
    # Use the repository schema's actual completeness vocabulary.
    decision['completeness'] = ds['properties']['completeness']['enum'][0]
    task = {'model': 'gpt-6-astra', 'prompt_sha256': 'p', 'schema_json': json.dumps(schema)}
    task['prompt'] = 'BATCH_SOURCE_CONTEXT:\n' + json.dumps({'records': [{'source_span_text': {'s1': 'source quote'}}]})
    entry = {'record_id': 'N', 'items': ['v24'], 'documents': ['source quote']}
    response = {'model': task['model'], 'prompt_sha256': 'p', 'structured_output': {'records': [
        {'record_id': 'N', 'annotation': {'source_span_ids': ['s1'], 'decisions': {'v24': decision}}}]}}
    assert vet.validate_response(response, task, entry) == {'v24': 1}
    response['prompt_sha256'] = 'wrong'
    with pytest.raises(ValueError, match='binding'):
        vet.validate_response(response, task, entry)
    response['prompt_sha256'] = 'p'
    decision['evidence_quotes'] = ['invented quote']
    with pytest.raises(ValueError, match='substring'):
        vet.validate_response(response, task, entry)
    decision.update(label='U', evidence_quotes=[], positive_evidence_span_id=None)
    with pytest.raises(ValueError, match='U lacks'):
        vet.validate_response(response, task, entry)
