"""Verdict-token confidence: located from output logprobs, journaled only when configured, never failing a run."""
import math
import sys
from types import ModuleType, SimpleNamespace

import pytest

from submission.pps.pipeline import VLLMRunner
from submission.pps.prompts import Config
from submission.pps.verdict_confidence import apply_thresholds, verdict_confidence


class Tokenizer:
    """Token ids index a table; two tokens decode to several characters (a multi-byte merge stand-in)."""
    TABLE = {1: '{"facts":{"a":"한글"},', 2: '"judgments":', 3: '{"v":[', 4: '0', 5: ',', 6: '1', 7: '],"e":[', 8: '2',
             9: ']}}', 10: '<|channel>', 11: 'thought', 12: '<channel|>'}

    def decode(self, ids, skip_special_tokens=True):
        return ''.join(self.TABLE[i] for i in ids if not (skip_special_tokens and self.TABLE[i].startswith('<')))


def logprob(token, value):
    return SimpleNamespace(logprob=value, decoded_token=token, rank=1)


def output(ids, digit_candidates):
    """Per-token candidate dicts; digit positions carry the given ('0', '1') logprob pairs in order."""
    rows, k = [], 0
    for i in ids:
        if i in (4, 6):
            lp0, lp1 = digit_candidates[k]
            k += 1
            rows.append({4: logprob('0', lp0), 6: logprob('1', lp1), 99: logprob('2', -9.)})
        else:
            rows.append({i: logprob(Tokenizer.TABLE[i], -0.01)})
    return rows


def test_confidence_located_through_suffix_decoding():
    ids = [1, 2, 3, 4, 5, 6, 5, 6, 7, 4, 5, 8, 5, 4, 9]      # "v":[0,1,1] "e":[0,2,0]
    pairs = [(-0.1, -2.5), (-3.0, -0.05), (-0.7, -0.7), (-0.01, -5.), (-0.01, -5.)]
    rows = verdict_confidence(ids, output(ids, pairs), Tokenizer(), (19, 20, 21))
    assert [r['item'] for r in rows] == [19, 20, 21]
    assert [r['verdict'] for r in rows] == [0, 1, 1]
    assert rows[0]['confidence'] == pytest.approx(1 / (1 + math.exp(-0.1 + 2.5)))
    assert rows[1]['confidence'] == pytest.approx(1 / (1 + math.exp(-3.0 + 0.05)))
    assert rows[2]['confidence'] == pytest.approx(0.5)
    assert rows[1]['lp0'] == -3.0 and rows[1]['lp1'] == -0.05


def test_duplicate_digit_tokens_keep_the_best_logprob_and_top_candidates_are_journaled():
    ids = [3, 6, 7, 4, 9]
    rows = [{3: logprob('{"v":[', -0.1)},
            {6: logprob('1', -0.02), 4: logprob('0', -4.0), 77: logprob('1', -39.0), 78: logprob('0', -39.5)},
            {7: logprob('],"e":[', -0.1)}, {4: logprob('0', -0.01)}, {9: logprob(']}}', -0.1)}]
    got = verdict_confidence(ids, rows, Tokenizer(), (24,))
    assert got[0]['lp1'] == -0.02 and got[0]['lp0'] == -4.0
    assert got[0]['confidence'] == pytest.approx(1 / (1 + math.exp(-4.0 + 0.02)))
    assert got[0]['top'][0] == ['1', -0.02] and len(got[0]['top']) == 4


def test_missing_alternative_and_malformed_inputs_degrade_to_none_or_empty():
    ids = [3, 6, 7, 4, 9]
    rows = [{3: logprob('{"v":[', -0.1)}, {6: logprob('1', -0.01)}, {7: logprob('],"e":[', -0.1)},
            {4: logprob('0', -0.01)}, {9: logprob(']}}', -0.1)}]
    got = verdict_confidence(ids, rows, Tokenizer(), (24,))
    assert got == [{'item': 24, 'verdict': 1, 'lp0': None, 'lp1': -0.01, 'confidence': None, 'top': [['1', -0.01]]}]
    assert verdict_confidence(ids, rows[:-1], Tokenizer(), (24,)) == []          # length mismatch
    assert verdict_confidence(ids, rows, Tokenizer(), (23, 24)) == []             # item count mismatch
    assert verdict_confidence([1, 2], [{}, {}], Tokenizer(), (1,)) == []           # no verdict array


def test_thinking_output_locates_the_answer_after_the_channel_marker():
    ids = [10, 11, 12, 3, 6, 7, 8, 9]
    rows = output(ids, [(-2., -0.1)])
    got = verdict_confidence(ids, rows, Tokenizer(), (10,), skip_special_tokens=False)
    assert got[0]['item'] == 10 and got[0]['verdict'] == 1 and got[0]['confidence'] > 0.8


def test_config_switch_bounds():
    assert Config().verdict_logprobs == 0
    assert Config(verdict_logprobs=20).verdict_logprobs == 20
    for bad in (-1, 21, True, 2.0):
        with pytest.raises(ValueError):
            Config(verdict_logprobs=bad)


def test_runner_requests_logprobs_and_journals_confidence_only_when_configured(monkeypatch):
    calls = []
    vllm = ModuleType('vllm')
    sampling = ModuleType('vllm.sampling_params')
    vllm.SamplingParams = lambda **kwargs: calls.append(kwargs) or kwargs
    sampling.StructuredOutputsParams = lambda **kwargs: kwargs
    for name, module in (('vllm', vllm), ('vllm.sampling_params', sampling)):
        monkeypatch.setitem(sys.modules, name, module)
    ids = [3, 6, 7, 8, 9]
    native = SimpleNamespace(outputs=[SimpleNamespace(text='{"v":[1],"e":[2]}', token_ids=ids, finish_reason='stop',
                                                      logprobs=output(ids, [(-2., -0.1)]))])
    prompts = [{'token_ids': [1, 2], 'spans': [], 'items': [24]}]
    for switch in (0, 5):
        runner = object.__new__(VLLMRunner)
        runner.config = Config(verdict_logprobs=switch)
        runner.tokenizer = Tokenizer()
        runner.llm = SimpleNamespace(generate=lambda *a, **kw: [native])
        calls.clear()
        result = runner.generate(prompts)[0]
        assert ('logprobs' in calls[0]) == bool(switch)
        assert ('verdict_confidence' in result) == bool(switch)
        if switch:
            assert calls[0]['logprobs'] == 5
            assert result['verdict_confidence'][0]['item'] == 24 and result['verdict_confidence'][0]['verdict'] == 1
    # A diagnostic failure is recorded, never raised.
    runner.tokenizer = SimpleNamespace(decode=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('boom')))
    result = runner.generate(prompts)[0]
    assert result['verdict_confidence'] == {'error': 'RuntimeError: boom'}
    assert result['text'] == '{"v":[1],"e":[2]}'


def test_threshold_gate_withdraws_only_low_confidence_model_positives():
    entries = [{'item': 2, 'confidence': 0.3}, {'item': 3, 'confidence': 0.9}, {'item': 4, 'confidence': None}]
    values = [0, 1, 1, 1, 0, 0, 0, 0, 0] + [0] * 15
    withdrawn = apply_thresholds(((2, 0.5), (3, 0.5), (4, 0.5), (9, 0.5)), entries, values, (1, 2, 3, 4, 5, 6, 7, 8, 9))
    assert withdrawn == [2] and values[1] == 0 and values[2] == 1 and values[3] == 1
    assert apply_thresholds((), entries, values, (2,)) == []
    assert apply_thresholds(((2, 0.5),), None, values, (2,)) == []


def test_threshold_config_validation():
    assert Config(verdict_logprobs=20, verdict_thresholds=((2, 0.35), (24, 0.5))).verdict_thresholds == ((2, 0.35), (24, 0.5))
    for bad in (((2, 0.5),), ):
        with pytest.raises(ValueError):
            Config(verdict_thresholds=bad)                      # needs verdict_logprobs
    for bad in (((0, 0.5),), ((2, 1.5),), ((2, 0.5), (2, 0.6)), ((2, True),), (2, 0.5)):
        with pytest.raises(ValueError):
            Config(verdict_logprobs=20, verdict_thresholds=bad)
