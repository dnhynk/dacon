import copy

import pytest

from tools.score_specification_contrast import primary_and_repeats, sampler_without_schema


def packet(key, arm, source='same source'):
    return {'request_key': key, 'record_id': 'r', 'arm': arm,
        'messages': [{'role': 'user', 'content': source}], 'items': [9]}


def fixture():
    p = [packet('a', 'factored'), packet('b', 'specification_scope'), packet('c', 'factored_repeat')]
    f = {'arms': ['factored', 'specification_scope'], 'notices': 1,
        'repeated_pairs': [{'primary': 'a', 'repeat': 'c'}]}
    return p, f


def test_repeats_are_excluded_by_preregistered_identity():
    packets, freeze = fixture()
    first, repeated = primary_and_repeats(packets, freeze)
    assert [p['request_key'] for p in first] == ['a', 'b']
    assert repeated == freeze['repeated_pairs']


@pytest.mark.parametrize('change', ['source', 'missing_arm', 'undeclared', 'duplicate'])
def test_changed_or_incomplete_comparisons_rejected(change):
    packets, freeze = fixture()
    if change == 'source':
        packets[-1]['messages'][0]['content'] = 'different source'
    elif change == 'missing_arm':
        packets = [packets[0], packets[2]]
    elif change == 'undeclared':
        packets.append(packet('d', 'new_arm'))
    else:
        packets.append(copy.deepcopy(packets[0]))
    with pytest.raises(ValueError):
        primary_and_repeats(packets, freeze)


def test_sampler_comparison_retains_every_setting_except_schema():
    a = {'temperature': 0.0, 'seed': 42, 'max_tokens': 1200, 'structured_outputs': {'json': {'a': 1}, 'disable_any_whitespace': True}}
    b = copy.deepcopy(a)
    b['structured_outputs']['json'] = {'b': 2}
    assert sampler_without_schema(a) == sampler_without_schema(b)
    b['max_tokens'] = 600
    assert sampler_without_schema(a) != sampler_without_schema(b)
    assert a['structured_outputs']['json'] == {'a': 1}


def shared_execution(tmp_path):
    import json
    def write(name, value):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf8')
    order = ['catalog', 'specification']
    summary = {'primary_requests': 44}
    write('execution_exit.json', {'returncode': 0, 'wall_budget_exhausted': False, 'seconds': 600})
    write('engine_bootstrap/environment.json', {'packages': {}})
    write('engine_bootstrap/engine.json', {'load_seconds': 400})
    write('authority.json', {'phase_order': order, 'maximum_engine_loads': 1})
    write('complete.json', {'phase_order': order, 'engine_loads': 1,
        'phases': {'catalog': {'primary_requests': 8}, 'specification': summary}})
    write('specification_run/phase_started.json', {'phase': 'specification',
        'previous_phases': ['catalog'], 'shared_engine': '../engine_bootstrap', 'engine_loads_here': 0})
    write('specification_run/phase_complete.json', {'phase': 'specification',
        'prior_phases': ['catalog'], 'engine_loads_here': 0, 'seconds': 70, 'summary': summary})
    write('specification_run/contrast_output/contrast_summary.json', summary)
    return write


def test_shared_phase_keeps_one_engine_and_prior_cache_history(tmp_path):
    from tools.score_specification_contrast import execution_context
    shared_execution(tmp_path)
    env, engine, completion, context = execution_context(tmp_path / 'specification_run', tmp_path)
    assert engine['load_seconds'] == 400 and completion['seconds'] == 600
    assert context['engine_loads_in_whole_execution'] == 1 and context['engine_loads_in_phase'] == 0
    assert context['prior_phases'] == ['catalog'] and context['phase_seconds'] == 70
    assert context['duration_is_shared_not_additive'] is True


@pytest.mark.parametrize('change', ['parent', 'timeout', 'order', 'prior', 'summary', 'loads', 'own_engine', 'pointer'])
def test_shared_phase_rejects_wrong_or_incomplete_provenance(tmp_path, change):
    import json
    from tools.score_specification_contrast import execution_context
    write = shared_execution(tmp_path)
    run = tmp_path / 'specification_run'
    def modify(name, key, value):
        data = json.loads((tmp_path / name).read_text('utf8'))
        data[key] = value
        write(name, data)
    if change == 'parent': run = tmp_path / 'elsewhere' / 'specification_run'
    elif change == 'timeout': modify('execution_exit.json', 'wall_budget_exhausted', True)
    elif change == 'order': modify('complete.json', 'phase_order', ['specification', 'catalog'])
    elif change == 'prior': modify('specification_run/phase_started.json', 'previous_phases', [])
    elif change == 'summary': modify('specification_run/phase_complete.json', 'summary', {})
    elif change == 'loads': modify('complete.json', 'engine_loads', 2)
    elif change == 'own_engine': modify('specification_run/phase_started.json', 'engine_loads_here', 1)
    else: modify('specification_run/phase_started.json', 'shared_engine', '../other_engine')
    with pytest.raises(ValueError): execution_context(run, tmp_path)


def test_existing_single_execution_does_not_require_phase_files(tmp_path):
    from tools.score_specification_contrast import execution_context
    shared_execution(tmp_path)
    _, _, _, context = execution_context(tmp_path)
    assert context['shared_engine'] is False
