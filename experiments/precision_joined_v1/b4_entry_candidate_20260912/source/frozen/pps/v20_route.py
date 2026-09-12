"""Uniform fourth call using the original v7 producer; replace only v20/e20.

This module accepts current input, current baseline rows and a model runner.
It never reads research responses, labels, record lists or past predictions.
The namespaced producer preserves its original retrieval, schema and rules.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import time
from pathlib import Path

from v20_legacy.knowledge import Knowledge as LegacyKnowledge
from v20_legacy.pipeline import VLLMRunner as LegacyVLLMRunner
from v20_legacy.pipeline import _response_row as legacy_response_row
from v20_legacy.prompts import Config as LegacyConfig
from v20_legacy.prompts import build_shared_prompts

from .data import read_csv, records, write_csv
from .pipeline import log, run as run_base

GROUPS = (tuple(range(1, 10)), tuple(range(10, 19)), tuple(range(19, 25)))
ROUTE_ITEMS = GROUPS[2]
ENGINE_FIELDS = ('max_model_len', 'quantization', 'gpu_memory_utilization',
                 'max_num_seqs', 'seed', 'enable_thinking', 'thinking_token_budget')


def legacy_config():
    config = LegacyConfig.load(Path(__file__).resolve().parents[1] / 'model/v20_legacy.json')
    if tuple(map(tuple, config.judgment_groups)) != GROUPS or not config.shared_prefix:
        raise ValueError('Uniform route requires the original three-group shared prompt')
    if config.response_format != 'factored' or config.thinking_budget_for(ROUTE_ITEMS) != 0:
        raise ValueError('Original route schema and thinking allocation must be retained')
    return config


class SharedModelRunner(LegacyVLLMRunner):
    """Use the already loaded engine with the original per-call sampling/schema.

    No second VLLMRunner constructor/LLM load is executed. Legacy generate()
    constructs its own original SamplingParams, including the factored schema.
    """

    def __init__(self, base_runner, config):
        for field in ENGINE_FIELDS:
            if getattr(base_runner.config, field) != getattr(config, field):
                raise ValueError(f'Cannot share an engine with different {field}')
        self.base_runner = base_runner
        self.config = config
        self.llm = base_runner.llm
        self.tokenizer = base_runner.tokenizer
        self.version = base_runner.version
        self.load_seconds = 0.0

    @property
    def deadline(self):
        return getattr(self.base_runner, 'deadline', float('inf'))


class UniformV20Route:
    def __init__(self, data_dir, tokenizer):
        self.config = legacy_config()
        self.knowledge = LegacyKnowledge(data_dir)
        self.tokenizer = tokenizer

    def prompt(self, record):
        # Build ALL original groups before selecting this call: shared source
        # selection and context fitting depend on the complete bundle.
        bundle = build_shared_prompts(record, self.knowledge, self.config,
                                      self.tokenizer, GROUPS)
        prompt = bundle[2]
        if tuple(prompt['items']) != ROUTE_ITEMS:
            raise ValueError('Unexpected original producer item group')
        return prompt

    def consume(self, record, response, prompt):
        if response.get('finish_reason') not in {'stop', 'eos_token', 'mock'}:
            raise ValueError('Uniform route requires a complete model response')
        row, details = legacy_response_row(record, response, prompt, ROUTE_ITEMS,
            self.config, self.knowledge, ROUTE_ITEMS)
        return {'v20': int(row['v20']), 'e20': row['e20']}, details

    def apply(self, input_records, base_rows, runner, *, trace_path=None):
        if len(input_records) != len(base_rows):
            raise ValueError('Current baseline output count differs from current input')
        if any(rec['id'] != row['id'] for rec, row in zip(input_records, base_rows)):
            raise ValueError('Current baseline output order differs from current input')
        rows = copy.deepcopy(base_rows)
        consumed = input_tokens = output_tokens = 0
        maximum_input = 0
        start = time.monotonic()
        stream = Path(trace_path).open('w', encoding='utf-8') if trace_path else None
        try:
            for offset in range(0, len(input_records), self.config.batch_size):
                batch = input_records[offset:offset + self.config.batch_size]
                prompts = [self.prompt(rec) for rec in batch]
                responses = runner.generate(prompts, max_tokens=self.config.max_output_tokens)
                if len(responses) != len(prompts):
                    raise RuntimeError('Missing uniform route responses; final CSV not written')
                for index, (record, prompt, response) in enumerate(zip(batch, prompts, responses)):
                    if stream:
                        # Save raw returned answers before parsing can fail.
                        stream.write(json.dumps({'id': record['id'], 'items': ROUTE_ITEMS,
                            'prompt_sha256': hashlib.sha256(json.dumps(prompt['messages'],
                                ensure_ascii=False).encode()).hexdigest(),
                            'response': response}, ensure_ascii=False) + '\n')
                        stream.flush()
                    replacement, _ = self.consume(record, response, prompt)
                    rows[offset + index].update(replacement)
                    consumed += 1
                    count = len(prompt['token_ids']) if prompt['token_ids'] is not None else 0
                    input_tokens += count
                    maximum_input = max(maximum_input, count)
                    output_tokens += response['output_tokens']
                log(f'uniform v20: {consumed}/{len(input_records)}; {time.monotonic()-start:.1f}s')
        finally:
            if stream:
                stream.close()
        if consumed != len(input_records):
            raise RuntimeError('Every notice must consume its extra response')
        if any(row[key] != base[key] for row, base in zip(rows, base_rows)
               for key in base if key not in {'v20', 'e20'}):
            raise AssertionError('Uniform route changed a field outside v20/e20')
        return rows, {'response_consumptions': consumed, 'input_tokens_total': input_tokens,
            'input_tokens_max': maximum_input, 'output_tokens_total': output_tokens,
            'seconds': round(time.monotonic()-start, 3), 'outside_v20_e20_changes': 0,
            'config': dataclasses.asdict(self.config)}


def run(input_path, output_path, data_dir, config, runner, *, limit=None,
        trace=False, route_runner=None):
    """Execute automatic three-call baseline then uniform original-v7 v20 call.

    route_runner is an injection point for CPU verification. Ordinary inference
    reuses runner.llm through SharedModelRunner and loads no additional model.
    Baseline rows are produced by run_base during THIS invocation.
    """
    if tuple(map(tuple, config.judgment_groups)) != GROUPS or config.focus_groups:
        raise ValueError('Four-call candidate requires the fixed three-call baseline')
    output_path = Path(output_path)
    if runner.is_mock and output_path.name == 'submission.csv':
        raise ValueError('Mock verification must not produce submission.csv')
    recs = list(records(input_path, limit))
    if not recs:
        raise ValueError('No input records')
    route = UniformV20Route(data_dir, runner.tokenizer)
    if route_runner is None:
        route_runner = runner if runner.is_mock else SharedModelRunner(runner, route.config)
    start = time.monotonic()
    baseline_output = output_path.parent / 'base_route' / output_path.name
    base_report = run_base(input_path, baseline_output, data_dir, config, runner,
                           limit=limit, trace=trace)
    rows, route_report = route.apply(recs, read_csv(baseline_output), route_runner,
        trace_path=output_path.parent / 'v20_trace.jsonl' if trace else None)
    write_csv(output_path, rows, recs=recs, require_positive_evidence=config.require_positive_evidence)
    is_replay = bool(getattr(runner, 'is_replay', False))
    model_calls = 0 if runner.is_mock or is_replay else base_report['normal_model_calls'] + len(recs)
    report = {'candidate': 'automatic_B2_v1_plus_uniform_original_v7_v20',
        'records': len(recs), 'mode': 'historical_replay' if is_replay else ('mock' if runner.is_mock else 'fixed_model'),
        'normal_calls_per_notice': 4, 'extra_calls_per_notice': 1,
        'response_consumptions': 4 * len(recs), 'new_model_calls': model_calls,
        'single_loaded_engine': route_runner is runner or isinstance(route_runner, SharedModelRunner),
        'baseline_report': str(baseline_output.parent / 'run_report.json'),
        'uniform_v20': route_report, 'csv_validation': 'PASS',
        'pipeline_seconds': round(time.monotonic()-start, 3),
        'estimated_full_gpu_seconds': None, 'official_score': None}
    (output_path.parent / 'run_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
