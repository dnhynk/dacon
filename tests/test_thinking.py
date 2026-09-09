"""The native budget path must yield a complete final answer, without thought logs."""
import sys
from types import ModuleType, SimpleNamespace

import pytest

from pps.pipeline import VLLMRunner
from pps.prompts import Config


def test_budget_requires_thinking_and_final_answer_room():
    for budget in (-1, True, 640):
        with pytest.raises(ValueError):
            Config(enable_thinking=True, thinking_token_budget=budget)
    with pytest.raises(ValueError):
        Config(thinking_token_budget=100)
    assert Config(enable_thinking=True, thinking_token_budget=100).max_output_tokens == 640
    with pytest.raises(ValueError):
        Config(thinking_items=(10,))


def test_selected_items_keep_one_native_prompt_mode_but_separate_budgets():
    cfg=Config(enable_thinking=True,thinking_token_budget=100,thinking_items=(10,11,12))
    assert cfg.thinking_budget_for((1,2,3))==0
    assert cfg.thinking_budget_for((10,11,12))==100
    assert cfg.thinking_budget_for((19,20,21))==0
    assert cfg.enable_thinking  # Every group still shares the same template prefix.


def test_native_final_answer_split_and_incomplete_thought_redaction(monkeypatch):
    calls = []
    vllm = ModuleType("vllm")
    sampling = ModuleType("vllm.sampling_params")
    utils = ModuleType("vllm.reasoning.gemma4_utils")
    vllm.SamplingParams = lambda **kwargs: calls.append(kwargs) or kwargs
    sampling.StructuredOutputsParams = lambda **kwargs: kwargs
    utils.parse_thinking_output = lambda text: (
        {"thinking": "private diagnostic content", "answer": '{"v":[1],"e":[0]}'}
        if "<channel|>" in text else {"thinking": None, "answer": text})
    for name, module in (("vllm",vllm),("vllm.sampling_params",sampling),
                         ("vllm.reasoning.gemma4_utils",utils)):
        monkeypatch.setitem(sys.modules,name,module)
    runner=object.__new__(VLLMRunner)
    runner.config=Config(enable_thinking=True,thinking_token_budget=100)
    runner.tokenizer=SimpleNamespace(encode=lambda *a,**kw:[1],
        convert_tokens_to_ids=lambda text: {"<|channel>":10,"<channel|>":20}[text])
    returned=[SimpleNamespace(outputs=[SimpleNamespace(
        text="<|channel>thought\nprivate diagnostic content<channel|>{}",
        token_ids=[10,11,12,20,30],finish_reason="stop")]),
        SimpleNamespace(outputs=[SimpleNamespace(text="<|channel>thought\nunfinished private text",
            token_ids=[10,11,12],finish_reason="length")])]
    runner.llm=SimpleNamespace(generate=lambda *a,**kw:returned)
    prompts=[{"token_ids":[1,2],"spans":[],"items":[1]}]*2
    results=runner.generate(prompts)
    assert all(c["thinking_token_budget"]==100 and not c["skip_special_tokens"] for c in calls)
    assert results[0]["text"]=='{"v":[1],"e":[0]}'
    assert results[0]["thinking_tokens"]==2
    assert results[1]["text"]=="" and results[1]["finish_reason"]=="length"
    assert "private" not in str(results)
