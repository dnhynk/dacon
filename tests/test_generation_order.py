"""Output order is an opt-in sampler constraint, never a weaker consumer."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from submission.pps.generation_contract import generation_schema, preflight, progress_preflight
from submission.pps.pipeline import VLLMRunner

ITEMS = tuple(range(10, 19))
FACT_ORDER = ('task_summary', 'purchase_kind', 'whole_task_units',
              'relationships', 'catalog_relation', 'unresolved_scope')


def test_order_changes_only_serialization_order_and_default_remains_control():
    original = generation_schema('catalog_scope', 3, ITEMS)
    proposed = generation_schema('catalog_scope', 3, ITEMS, schema_order='catalog_facts_first')
    assert tuple(proposed['properties']) == FACT_ORDER
    assert proposed['required'] == list(FACT_ORDER)
    assert tuple(original['properties']) != FACT_ORDER
    assert original['properties'] == proposed['properties']
    proposed['required'] = original['required']
    assert proposed == original


@pytest.mark.parametrize('form,order', [('catalog_scope', 'typo'), ('compact', 'catalog_facts_first')])
def test_misspelled_or_inapplicable_order_fails_before_generation(form, order):
    with pytest.raises(ValueError, match='Unsupported generation schema order'):
        generation_schema(form, 3, ITEMS, schema_order=order)


def test_preflight_preserves_both_orders_with_identical_validation_contract():
    cases = preflight([('catalog_scope', 3, ITEMS),
                       ('catalog_scope', 3, ITEMS, 'catalog_facts_first')])['cases']
    assert len(cases) == 2
    assert cases[0]['validation_schema_sha256'] == cases[1]['validation_schema_sha256']
    assert cases[0]['generation_schema_ordered_sha256'] != cases[1]['generation_schema_ordered_sha256']


def test_actual_sampler_receives_each_packet_order(monkeypatch):
    captured = []
    def generate(inputs, sampling_params, use_tqdm):
        captured.extend(sampling_params)
        return [SimpleNamespace(outputs=[SimpleNamespace(text='{}', finish_reason='stop', token_ids=[1])])
                for _ in inputs]
    monkeypatch.setitem(sys.modules, 'vllm', SimpleNamespace(SamplingParams=lambda **kwargs: kwargs))
    monkeypatch.setitem(sys.modules, 'vllm.sampling_params',
                        SimpleNamespace(StructuredOutputsParams=lambda **kwargs: kwargs))
    runner = VLLMRunner.__new__(VLLMRunner)
    runner.config = SimpleNamespace(seed=0, max_output_tokens=128, enable_thinking=False,
        response_format='catalog_scope', thinking_budget_for=lambda items: 0)
    runner.llm = SimpleNamespace(generate=generate)
    packets = [{'token_ids': [1, 2], 'items': ITEMS, 'spans': [object()],
                'generation': {'schema_order': order}} for order in (None, 'catalog_facts_first')]
    runner.generate(packets)
    assert tuple(captured[0]['structured_outputs']['json']['properties'])[0] == 'purchase_kind'
    assert tuple(captured[1]['structured_outputs']['json']['properties']) == FACT_ORDER
    assert captured[0]['max_tokens'] == captured[1]['max_tokens'] == 128
    assert captured[0]['thinking_token_budget'] == captured[1]['thinking_token_budget'] == 0


def test_actual_token_mask_requires_fact_prefix_and_remains_live():
    from transformers import AutoTokenizer
    import llguidance
    import llguidance.hf
    path = Path(__file__).resolve().parents[1]/'models/gemma-tokenizer'
    if not path.is_dir():
        pytest.skip('Fixed local tokenizer required')
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    grammar = llguidance.LLMatcher.grammar_from_json_schema(
        generation_schema('catalog_scope', 3, ITEMS, schema_order='catalog_facts_first'),
        defaults={'whitespace_flexible': False})
    encode = lambda text: tokenizer.encode(text, add_special_tokens=False)
    ll_tokenizer = llguidance.hf.from_tokenizer(tokenizer)
    matcher = llguidance.LLMatcher(ll_tokenizer, grammar)
    prefix = encode('{"task_summary":"자료 분석",')
    assert matcher.validate_tokens(prefix) == len(prefix)
    wrong = encode('{"purchase_kind":"service",')
    assert matcher.validate_tokens(wrong) < len(wrong)
    report = progress_preflight(tokenizer, schema_order='catalog_facts_first')
    assert report['whitespace_tokens_accepted_effective'] == 0
    assert report['complete_objects_accepted'] == 2
