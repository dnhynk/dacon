"""A valid JSON Schema can still be unsupported by the generation backend."""
import json

import jsonschema
import pytest

from submission.pps.generation_contract import generation_schema, preflight, validate_grammar
from submission.pps.prompts import output_schema
from submission.pps.catalog_scope import validate
from submission.pps.retrieval import Span


def test_unsupported_original_constraint_is_detected_without_loading_a_model():
    with pytest.raises(ValueError, match='uniqueItems'):
        validate_grammar(output_schema('catalog_scope', 7, tuple(range(10, 19))))
    result = preflight()
    assert result['status'] == 'PASS' and len(result['cases']) == 20
    assert {c['source_units'] for c in result['cases'] if c['format'] == 'catalog_conditions'} == {1, 512}
    assert not result['model_loaded'] and not result['gpu_allocated']


@pytest.mark.parametrize('field', ['whole_task_units', 'relationship'])
def test_compatible_generation_does_not_weaken_duplicate_reference_validation(field):
    obj = {'purchase_kind': 'service', 'whole_task_units': [1], 'task_summary': '연구 자료 분석',
           'catalog_relation': 'unknown', 'relationships': [], 'unresolved_scope': True}
    if field == 'whole_task_units':
        obj[field] = [1, 1]
    else:
        obj['relationships'] = [{'code': '8014190201', 'role': 'uncertain', 'source_units': [1, 1]}]
    jsonschema.validate(obj, generation_schema('catalog_scope', 1, tuple(range(10, 19))))
    with pytest.raises(jsonschema.ValidationError):
        validate(json.dumps(obj), [Span(0, '공고문', 0, 1, '가')])


@pytest.mark.parametrize('form,items', [('compact', (1, 2)), ('fact_compact', (10, 11)),
    ('factored', (19, 20)), ('reasoned', (24,)), ('software_refs', (20,)), ('software_facts', (20,))])
def test_existing_generation_grammars_are_unchanged(form, items):
    assert generation_schema(form, 43, items) == output_schema(form, 43, items)


def test_generating_scope_schema_does_not_mutate_the_validation_schema():
    generation_schema('catalog_scope', 1, tuple(range(10, 19)))
    strict = output_schema('catalog_scope', 1, tuple(range(10, 19)))
    assert strict['properties']['whole_task_units']['uniqueItems'] is True


def test_unsupported_grammar_stops_before_environment_or_model_loading(monkeypatch):
    from types import SimpleNamespace
    from submission import engine
    from submission.pps import generation_contract
    monkeypatch.setattr(engine, 'configure_environment', lambda: None)
    monkeypatch.setattr(engine, 'native_toolchain_preflight', lambda: {})
    def failed_preflight():
        raise ValueError('unsupported grammar')
    monkeypatch.setattr(generation_contract, 'preflight', failed_preflight)
    def unexpected(*args, **kwargs):
        pytest.fail('Environment inspection/model loading preceded grammar validation')
    monkeypatch.setattr(engine, 'environment', unexpected)
    monkeypatch.setattr(engine.VLLMRunner, '__init__', unexpected)
    with pytest.raises(ValueError, match='unsupported grammar'):
        engine.CanonicalRunner('unused', None, SimpleNamespace(save=lambda *args: None))
