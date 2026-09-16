"""Constrained output must make progress under the actual engine settings."""
import dataclasses
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from submission.pps import generation_contract as contract
from submission.pps.pipeline import VLLMRunner
from submission.pps.prompts import Config


@pytest.mark.parametrize('thinking', [False, True])
def test_whitespace_constraint_reaches_engine_configuration(tmp_path, monkeypatch, thinking):
    observed = {}
    def llm(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(get_tokenizer=lambda: object())
    monkeypatch.setitem(sys.modules, 'vllm', SimpleNamespace(LLM=llm, __version__='0.26.0'))
    monkeypatch.setitem(sys.modules, 'vllm.config', SimpleNamespace(ReasoningConfig=lambda **kw: kw))
    config = Config.load(Path(__file__).resolve().parents[1]/'submission/model/config.json')
    config = dataclasses.replace(config, enable_thinking=thinking,
        thinking_token_budget=config.thinking_token_budget if thinking else None,
        thinking_items=config.thinking_items if thinking else ())
    VLLMRunner(tmp_path, config)
    options = observed.get('structured_outputs_config', {})
    assert options.get('disable_any_whitespace') is True
    assert options.get('backend') == 'guidance'
    if thinking:
        assert options['reasoning_parser'] == 'gemma4' and options['enable_in_reasoning'] is False


@pytest.fixture(scope='module')
def tokenizer():
    from transformers import AutoTokenizer
    path = Path(__file__).resolve().parents[1]/'models/gemma-tokenizer'
    if not path.is_dir():
        pytest.skip('Fixed local tokenizer required for real token-mask regression')
    return AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)


@pytest.mark.parametrize('response_format', ['catalog_scope', 'catalog_conditions', 'catalog_semantics'])
def test_actual_tokenizer_accepts_numbers_but_rejects_json_whitespace_loop(tokenizer, response_format):
    report = contract.progress_preflight(tokenizer, response_format=response_format)
    assert report['status'] == 'PASS'
    assert report['unbounded_whitespace_tokens_accepted_control'] == 40
    assert report['whitespace_tokens_accepted_effective'] == 0
    assert report['complete_objects_accepted'] == 2


@pytest.mark.parametrize('text,stalled', [
    ('{"refs":[' + '    \n'*100, True),
    ('{"refs":[' + ' \n'*4, False),
    ('{"quote":"' + ' '*500, False),
    ('{"quote":"escaped \\\" words", "refs":[' + ' \n'*100, True),
    ('ordinary text' + ' '*500, False),
])
def test_whitespace_stall_detection_distinguishes_string_content(text, stalled):
    assert bool(contract.json_whitespace_stall(text)) is stalled
