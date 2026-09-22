from __future__ import annotations

import argparse
import dataclasses
import difflib
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from .data import (EvidenceUnavailableError, clean_evidence, make_row, missing_evidence_items, records,
                   require_evidence, write_csv)
from .knowledge import Knowledge
from .prompts import Config, build_prompt, build_shared_prompts, output_schema, fact_fields
from .rules import apply_rules
from .checkpoint import Checkpoint
from .response_contract import loads as response_json, validate_items
from .generation_contract import generation_schema


def log(text):
    print(f"[pps] {text}", file=sys.stderr, flush=True)


def parse_output(text, spans, items=tuple(range(1, 25)), *, rec=None):
    validate_items(items)
    obj = response_json(text)
    if isinstance(obj, dict) and set(obj) == {"facts", "judgments"}:
        facts = obj["facts"]
        if (not isinstance(facts, dict) or set(facts) != set(fact_fields(items))
                or any(not isinstance(v, str) or not 1 <= len(v) <= 220 for v in facts.values())):
            raise ValueError("Invalid fact summary")
        obj = obj["judgments"]
    if isinstance(obj, dict) and set(obj) == {f"v{k}" for k in items}:
        judgments = [obj[f"v{k}"] for k in items]
        if any(not isinstance(item, dict) or set(item) != {"reason", "v", "e"}
               or not isinstance(item["reason"], str) or not 1 <= len(item["reason"]) <= 110
               for item in judgments):
            raise ValueError("Invalid named item judgment")
        values, refs = [item["v"] for item in judgments], [item["e"] for item in judgments]
    elif isinstance(obj, dict) and set(obj) == {"v", "e"}:
        values, refs = obj["v"], obj["e"]
    else:
        raise ValueError("Model response must contain exactly the requested item judgments")
    if not isinstance(values, list) or not isinstance(refs, list) or len(values) != len(items) or len(refs) != len(items):
        raise ValueError("Model response has an incorrect number of requested judgments")
    if any(type(v) is not int or v not in (0, 1) for v in values):
        raise ValueError("Invalid violation label from model")
    if any(type(i) is not int or not 0 <= i <= len(spans) for i in refs):
        raise ValueError("Invalid evidence reference from model")
    labels, evidence = [0] * 24, [""] * 24
    for k, value, ref in zip(items, values, refs):
        labels[k-1], evidence[k-1] = value, spans[ref-1].text if ref else ""
        if rec is not None and ref:
            span = spans[ref-1]
            evidence[k-1] = clean_evidence(
                span.text, rec, source=(span.doc_index, span.start, span.end))
    return labels, evidence


def _repair_evidence_locator(rec, row, response, spans, items, values, evidence):
    """Repair only a model-owned citation after all judgment decisions.

    A fact's explicit S locator and a unique long literal source match must
    agree. Never infer a violation, invent a quote, or replace a rule's witness.
    """
    from .model_citation import cited_indices, _span_source
    fields = {
        # The joint v1/v9 fact mixes licensing, institutional limits and models;
        # a literal match alone cannot identify which condition supports v1.
        **{k: '필수실적_배점구별_금액비교' for k in (2, 3, 4, 8)},
        **{k: '지역범위_금액상한_예외' for k in (5, 6, 7)},
        19: '확약서발급주체_보유시점_제출시점',
        21: '공동계약방식_최소비율',
        22: '사전설명회_제안서마감_날짜차이',
        23: '사전설명회_제안서마감_날짜차이',
        24: '본문과메타의동일필드차이',
    }
    obj = response_json(response['text'])
    facts = obj.get('facts', {}) if isinstance(obj, dict) else {}
    repairs = []
    for k in items:
        if (k not in fields or not values[k-1] or row[f'v{k}'] != 1
                or row[f'e{k}'] != evidence[k-1]):
            continue
        summary = facts.get(fields[k], '') if isinstance(facts, dict) else ''
        if not isinstance(summary, str):
            continue
        candidates, already_grounded = {}, False
        for index in cited_indices(summary, len(spans)):
            span = spans[index]
            text = _span_source(rec, span)
            if text is None:
                continue
            for match in difflib.SequenceMatcher(None, summary, span.text, autojunk=False).get_matching_blocks():
                anchor = span.text[match.b:match.b + match.size].strip()
                if len(anchor) < 20 or len(re.findall('[가-힣]', anchor)) < 8:
                    continue
                if re.sub(r'\s+', '', anchor) in re.sub(r'\s+', '', row[f'e{k}']):
                    already_grounded = True
                if span.text.count(anchor) != 1:
                    # One S locator can still contain repeated candidate clauses.
                    already_grounded = True
                    continue
                # Quote the complete source paragraph, never a clipped predicate.
                start, end = span.start + match.b, span.start + match.b + match.size
                lo = text.rfind('\n\n', 0, start) + 2
                if lo == 1:
                    lo = 0
                hi = text.find('\n\n', end)
                if hi < 0:
                    hi = len(text)
                if hi - lo > 500:
                    continue
                source = (span.doc_index, lo, hi)
                quote = clean_evidence(text[lo:hi], rec, source=source)
                if quote and anchor in quote:
                    candidates[source] = (quote, index + 1)
        if already_grounded or len(candidates) != 1:
            continue
        source, (quote, number) = next(iter(candidates.items()))
        if quote == row[f'e{k}']:
            continue
        if k in (2, 3, 4, 8):
            from .performance import performance_facts, validate_model_witness
            facts = performance_facts(rec)
            roles = {c['status'] for c in facts['candidates']
                     if c['evidence']['doc_index'] == source[0]
                     and c['evidence']['start'] < source[2]
                     and c['evidence']['end'] > source[1]}
            if roles & {'scoring', 'forms_or_submission'}:
                continue  # Mixed-purpose paragraphs are also unsuitable for locator repair.
            candidate = {**row, f'e{k}': quote}
            if validate_model_witness(rec, candidate, k, facts) is not None:
                continue  # Do not replace a missing proof with a scored/form-only clause.
        repairs.append({'source': 'literal_fact_citation_repair', 'item': k,
                        'previous_evidence': row[f'e{k}'], 'evidence': quote,
                        'span_number': number, 'source_range': source,
                        'judgment_preserved': True, 'semantic_verdict_verified': False})
        row[f'e{k}'] = quote
    return repairs


def _fill_missing_evidence(rec, row, response, items):
    """Fill only empty positive citations; never assign a judgment value."""
    repairs = []
    for k in (2, 8, 24):
        if k not in items or row[f'v{k}'] != 1 or row[f'e{k}']:
            continue
        if k == 24:
            from .comparison import compare, missing_evidence
            obj = response_json(response['text'])
            facts = obj.get('facts', {})
            claim = facts.get('본문과메타의동일필드차이') if isinstance(facts, dict) else None
            candidate = missing_evidence(rec, compare(rec), claim)
        else:
            from .performance import performance_facts, missing_evidence
            candidate = missing_evidence(rec, k, performance_facts(rec))
        if candidate is None:
            continue
        quote = candidate['evidence']
        di, lo, hi = candidate['source_range']
        if (not quote or len(quote) > 500 or quote.startswith(('=', '+', '@'))
                or rec['docs'][di]['text'][lo:hi] != quote):
            continue
        row[f'e{k}'] = quote
        repairs.append({'source': 'confirmed_observation_evidence_fill', 'item': k,
                        **candidate, 'judgment_preserved': True})
    return repairs


def _response_row(rec, response, prompt, items, config, knowledge, final_items):
    values, evidence = parse_output(response["text"], prompt["spans"], items, rec=rec)
    row = make_row(rec, values, evidence)
    rule_details = []
    if config.source_verified_services and set(items).intersection(range(10, 19)):
        knowledge = knowledge.for_response(rec, response)
        rule_details.append({"source": "automatic_service_identity", "details": knowledge.provider_log})
    if config.rule_checks:
        comparison = prompt.get('comparison_facts')
        if config.cross_source_facts and 24 in items:
            # A stored prompt preserves what the model read. Rule execution
            # must use the current extractor on the supplied source record.
            from .comparison import compare
            comparison = compare(rec)
            rule_details.append({'source': 'cross_source_facts', 'computed_from_current_record': True,
                                 'packet_facts_equal_current': prompt.get('comparison_facts') == comparison})
        if comparison is not None and 24 in items:
            from .comparison import reject_unsupported_comparison_claim
            guard = reject_unsupported_comparison_claim(rec, row, response, comparison)
            if guard is not None:
                row['v24'], row['e24'] = guard['value'], guard['evidence']
                rule_details.append(guard)
        row, applied_rules = apply_rules(rec, row, knowledge, comparison=comparison, items=items)
        rule_details.extend(applied_rules)
    qualification_items = set(items).intersection(range(10, 19))
    if config.qualification_checks and qualification_items:
        candidate, facts = knowledge.qualification_decisions(rec, row)
        for k in qualification_items:
            row[f"v{k}"], row[f"e{k}"] = int(candidate[f"v{k}"]), candidate[f"e{k}"]
        rule_details.append({"source": "supplied_catalog_qualification_v2",
                             "items": sorted(qualification_items), "facts": facts})
        from .model_fact_overlay import overlay
        row, joined = overlay(rec, row, response, facts, qualification_items)
        rule_details.append({"source": "fallible_model_fact_source_predicate_join", "details": joined})
        from .fact_consistency import apply as apply_consistency
        row, consistency = apply_consistency(row, response,
            {k: values[k-1] for k in qualification_items}, product=facts.get('product'),
            qualification=facts.get('qualification'))
        if consistency:
            rule_details.append({'source': 'model_applicability_consistency', 'details': consistency})
    if config.rule_checks and 9 in items:
        from .model_citation import repair_v9
        row, citation = repair_v9(rec, row, response, prompt['spans'], items=items)
        if citation is not None:
            rule_details.append(citation)
        from .specification_table_fields import apply_v9 as apply_flattened_model_table
        row, table_field = apply_flattened_model_table(rec, row, items=items)
        if table_field is not None:
            rule_details.append(table_field)
    rule_details.extend(_repair_evidence_locator(
        rec, row, response, prompt['spans'], items, values, evidence))
    rule_details.extend(_fill_missing_evidence(rec, row, response, items))
    from .evidence_selection import select_verdict_evidence
    rule_details.extend(select_verdict_evidence(rec, row, rule_details, items))
    # Only this pass's items are final here; other grouped items may be unset.
    if config.require_positive_evidence:
        require_evidence(row, final_items)
    else:
        missing = missing_evidence_items(row, final_items)
        if missing:
            # The official CSV contract permits empty evidence when unavailable.
            # Preserve the independently obtained judgment; never invent a quote.
            rule_details.append({"source": "evidence_validation", "status": "unavailable",
                                 "items": missing, "labels_preserved": True})
    return row, rule_details


class VLLMRunner:
    is_mock = False

    def __init__(self, model_dir, config):
        start = time.monotonic()
        if not Path(model_dir).is_dir():
            raise ValueError("PPS_MODEL_DIR must be an existing local model directory")
        # Offline by construction: no model IDs, outside models, adapters or API calls.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["VLLM_NO_USAGE_STATS"] = "1"
        os.environ.setdefault("VLLM_WORKER_MULTIPROC_METHOD", "spawn")
        from vllm import LLM
        import vllm
        self.config = config
        self.deadline = start + config.total_runtime_seconds - 15
        model_path = Path(model_dir).resolve()
        self.checkpoint_identity = {'model_dir': str(model_path), 'files': {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in model_path.glob('*.json')
            if p.stat().st_size < 10_000_000}}
        self.version = vllm.__version__
        from .generation_contract import engine_options
        extra = {'structured_outputs_config': engine_options(config.enable_thinking)}
        if config.text_only:
            # This submission supplies only text tokens. Do not profile or
            # reserve encoder input capacity for unused audio/images/video.
            # vLLM0.26 documented multi-modal input limits; recorded in engine.json.
            extra['limit_mm_per_prompt'] = {'image': 0, 'audio': 0, 'video': 0}
        if config.thinking_token_budget is not None:
            # vLLM 0.26 only enforces this budget in its V1 GPU model runner.
            # Use native delimiters from the fixed Gemma4 tokenizer/parser.
            from vllm.config import ReasoningConfig
            os.environ["VLLM_USE_V2_MODEL_RUNNER"] = "0"
            extra["reasoning_config"] = ReasoningConfig(
                reasoning_start_str="<|channel>", reasoning_end_str="<channel|>")
        self.llm = LLM(model=str(model_dir), tokenizer=str(model_dir),
                       quantization=config.quantization, dtype="auto",
                       max_model_len=config.max_model_len,
                       gpu_memory_utilization=config.gpu_memory_utilization,
                       max_num_seqs=config.max_num_seqs, seed=config.seed,
                       max_num_batched_tokens=config.max_num_batched_tokens,
                       enable_prefix_caching=True, trust_remote_code=False, **extra)
        self.tokenizer = self.llm.get_tokenizer()
        self.load_seconds = time.monotonic() - start
        log(f"Loaded vLLM {self.version} in {self.load_seconds:.1f}s")

    def sampling_params(self, p, max_tokens=None):
        """One request's exact sampling/grammar contract; shared by both executors."""
        from vllm import SamplingParams
        from vllm.sampling_params import StructuredOutputsParams
        schema = (p['generation']['schema'] if p.get('generation', {}).get('response_format') == 'focused_verify'
                  else generation_schema(p.get('generation', {}).get('response_format', self.config.response_format),
                      len(p['spans']), p['items'], schema_order=p.get('generation', {}).get('schema_order'),
                      catalog_roles=p.get('generation', {}).get('catalog_roles'),
                      catalog_fields=p.get('generation', {}).get('catalog_fields'),
                      specification_inventory=p.get('generation', {}).get('specification_inventory')))
        return SamplingParams(temperature=0., seed=self.config.seed,
                              max_tokens=p.get('generation', {}).get('max_output_tokens', max_tokens or self.config.max_output_tokens),
                              skip_special_tokens=not self.config.enable_thinking,
                              thinking_token_budget=p.get('generation', {}).get('thinking_budget', self.config.thinking_budget_for(p["items"])),
                              structured_outputs=StructuredOutputsParams(
                                  json=schema,
                                  disable_any_whitespace=True))

    def response_from_native(self, row, prompt):
        """Parse one native vLLM result exactly as the cohort executor does."""
        if not row.outputs:
            raise RuntimeError("vLLM returned no normal response")
        response = row.outputs[0]
        final_text = response.text
        from .generation_contract import json_whitespace_stall
        answer_raw = response.text.split('<channel|>', 1)[-1] if self.config.enable_thinking else response.text
        diagnostics = {'raw_output_sha256': hashlib.sha256(response.text.encode()).hexdigest(),
                       'generation_stall': json_whitespace_stall(answer_raw)
                           if response.finish_reason == 'length' else None}
        if self.config.enable_thinking:
            from vllm.reasoning.gemma4_utils import parse_thinking_output
            split = parse_thinking_output(response.text)
            closed = "<channel|>" in response.text
            # An unterminated thought is never a final answer or saved text.
            final_text = (split.get("answer") or "") if closed else ""
            token_list = list(response.token_ids)
            start_id = self.tokenizer.convert_tokens_to_ids("<|channel>")
            end_id = self.tokenizer.convert_tokens_to_ids("<channel|>")
            start_at = token_list.index(start_id) if start_id in token_list else -1
            end_at = token_list.index(end_id) if end_id in token_list else len(token_list)
            diagnostics.update({"thinking_detected": bool(split.get("thinking")),
                           "thinking_characters": len(split.get("thinking") or ""),
                           "thinking_close_marker": closed,
                           "thinking_tokens": max(0, end_at-start_at-1) if start_at >= 0 else 0,
                           "thinking_budget": prompt.get('generation', {}).get('thinking_budget', self.config.thinking_budget_for(prompt["items"])),
                           "answer_tokens": len(self.tokenizer.encode(final_text, add_special_tokens=False)),
                           "raw_output_sha256": hashlib.sha256(response.text.encode()).hexdigest()})
        return {"text": final_text, "finish_reason": response.finish_reason,
                "output_tokens": len(response.token_ids),
                "cached_input_tokens": getattr(row, "num_cached_tokens", None), **diagnostics}

    def generate(self, prompts, max_tokens=None):
        if getattr(self, "deadline", float("inf")) <= time.monotonic():
            raise TimeoutError("Experiment time budget reached; completed results have been saved")
        sp = [self.sampling_params(p, max_tokens) for p in prompts]
        output = self.llm.generate([{"prompt_token_ids": p["token_ids"]} for p in prompts],
                                   sampling_params=sp, use_tqdm=False)
        if len(output) != len(prompts):
            raise RuntimeError("vLLM returned an unexpected number of responses")
        return [self.response_from_native(row, prompt) for row, prompt in zip(output, prompts)]

    # Streaming executor contract: the same engine, fed one request at a time.
    def submit(self, request_id, packet):
        from vllm.sampling_params import RequestOutputKind
        params = self.sampling_params(packet)
        params.output_kind = RequestOutputKind.FINAL_ONLY
        self.llm.llm_engine.add_request(request_id, {"prompt_token_ids": packet["token_ids"]}, params)

    def step(self):
        return [row for row in self.llm.llm_engine.step() if getattr(row, 'finished', False)]

    def unfinished(self):
        return self.llm.llm_engine.has_unfinished_requests()

    def abort(self, request_ids):
        self.llm.llm_engine.abort_request(list(request_ids))


class MockRunner:
    is_mock = True
    load_seconds = 0.
    version = "mock-no-quality-estimate"

    def __init__(self, tokenizer=None):
        self.tokenizer = tokenizer

    def generate(self, prompts, max_tokens=None):
        return [{"text": json.dumps({"v": [0] * len(p["items"]), "e": [0] * len(p["items"])}),
                 "finish_reason": "mock", "output_tokens": 0} for p in prompts]


def _generate_resilient(runner, prompts, max_tokens):
    try:
        responses = runner.generate(prompts, max_tokens=max_tokens)
        if len(responses) != len(prompts):
            raise RuntimeError("Missing model responses")
        return responses
    except TimeoutError:
        raise
    except Exception as exc:
        if len(prompts) == 1:
            return [{"text": "", "finish_reason": "error", "output_tokens": 0,
                     "error": f"{type(exc).__name__}: {exc}"}]
        middle = len(prompts) // 2
        log(f"Batch failed; retrying in two smaller batches ({len(prompts)} records)")
        return (_generate_resilient(runner, prompts[:middle], max_tokens)
                + _generate_resilient(runner, prompts[middle:], max_tokens))


def prompt_batches(recs, groups, knowledge, config, tokenizer):
    if config.shared_prefix:
        for offset in range(0, len(recs), config.batch_size):
            batch = recs[offset:offset+config.batch_size]
            bundles = [build_shared_prompts(r, knowledge, config, tokenizer, groups) for r in batch]
            for pass_n,items in enumerate(groups):
                yield pass_n,items,offset,batch,[bundle[pass_n] for bundle in bundles]
    else:
        for pass_n,items in enumerate(groups):
            for offset in range(0, len(recs), config.batch_size):
                batch = recs[offset:offset+config.batch_size]
                yield pass_n,items,offset,batch,[build_prompt(r,knowledge,config,tokenizer,items) for r in batch]


def consume_with_retries(rec, response, prompt, items, config, knowledge, runner, final_items):
    """Two bounded, real task retries also cover native thinking failures."""
    attempts = []
    active_config = config
    for attempt in range(config.max_response_retries + 1):
        try:
            allowed = {'stop', 'eos_token'} | ({'mock'} if runner.is_mock else set())
            if response.get('finish_reason') not in allowed:
                raise ValueError('No complete final answer: ' + str(response.get('finish_reason')))
            row, details = _response_row(rec, response, prompt, items, active_config, knowledge, final_items)
            if attempts:
                details.append({'source': 'bounded_response_recovery', 'attempts': attempts})
            return row, details, response, prompt, attempt
        except (ValueError, TypeError, KeyError) as exc:
            attempts.append({'attempt': attempt, 'error': str(exc),
                             'finish_reason': response.get('finish_reason')})
            if attempt == config.max_response_retries:
                raise RuntimeError(f'No valid complete response for {rec["id"]}, items {list(items)} after {attempt} retries: {exc}') from exc
            if time.monotonic() >= getattr(runner, 'deadline', float('inf')):
                raise TimeoutError('No runtime budget left for a task-relevant retry') from exc
            # Keep native delimiters, bound the thought budget to zero, and
            # reserve the remaining tokens for a complete structured answer.
            active_config = dataclasses.replace(config,
                response_format='compact' if attempt else config.response_format,
                document_chars=max(1760, config.document_chars // 2),
                max_output_tokens=512 if attempt else max(1024, config.max_output_tokens),
                thinking_token_budget=0 if config.enable_thinking else None,
                thinking_items=(), judgment_groups=(tuple(items),), focus_groups=())
            prompt = build_prompt(rec, knowledge, active_config, runner.tokenizer, items)
            prompt['generation'] = {'response_format': active_config.response_format,
                'thinking_budget': active_config.thinking_budget_for(items),
                'max_output_tokens': active_config.max_output_tokens}
            response = _generate_resilient(runner, [prompt], active_config.max_output_tokens)[0]


def _saved_prompt(prompt):
    return {**prompt, 'spans': [dataclasses.asdict(span) for span in prompt['spans']]}


def _restored_prompt(prompt):
    from .retrieval import Span
    return {**prompt, 'spans': [Span(**span) for span in prompt['spans']]}


def run(input_path, output_path, data_dir, config, runner, limit=None, trace=False):
    start = time.monotonic()
    recs = list(records(input_path, limit))
    if not recs:
        raise ValueError("No input records")
    knowledge = Knowledge(data_dir)
    output_path = Path(output_path)
    if runner.is_mock and output_path.name == "submission.csv":
        raise ValueError("Mock results must use mock_submission.csv, never a real submission filename")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = {r["id"]: make_row(r, [0]*24, ['']*24) for r in recs}
    completed_items = {r['id']: set() for r in recs}
    normal_calls = {r["id"]: 0 for r in recs}
    prompt_lengths, output_lengths, coverages = [], [], []
    cached_tokens, shared_prefixes = [], []
    thinking_outputs, thinking_characters, answer_tokens, thinking_tokens_max = 0, 0, 0, 0
    thinking_outputs_expected = 0
    retries = reused_responses = 0
    failures = []
    if config.judgment_groups:
        groups = [tuple(g) for g in config.judgment_groups]
        flattened = [k for group in groups for k in group]
        if (config.focus_groups or any(type(k) is not int for k in flattened)
                or sorted(flattened) != list(range(1, 25)) or any(not g for g in groups)):
            raise ValueError("Judgment groups must partition all 24 items exactly once")
    else:
        groups = [tuple(range(1, 25)), *[tuple(g) for g in config.focus_groups]]
    identity_config = dataclasses.asdict(config)
    identity_config.pop('checkpoint_resume', None)
    identity = {'records': recs, 'config': identity_config, 'mock': runner.is_mock,
                'runner': runner.version, 'model': getattr(runner, 'checkpoint_identity', {}),
                'source': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in Path(__file__).resolve().parent.glob('*.py')}}
    journal = Checkpoint(output_path.parent / (output_path.name + '.checkpoint'), identity,
                         resume=config.checkpoint_resume)
    trace_path = output_path.parent / "trace.jsonl"
    trace_file = trace_path.open("w", encoding="utf-8") if trace else None
    try:
        for pass_n, items, offset, batch_recs, prompts in prompt_batches(recs, groups, knowledge, config, runner.tokenizer):
            final_items = [k for k in items if not any(k in g for g in groups[pass_n + 1:])]
            keys = [journal.key(rec, items, prompt) for rec, prompt in zip(batch_recs, prompts)]
            saved = [journal.get(key) for key in keys]
            fresh_prompts = [prompt for prompt, cached in zip(prompts, saved) if cached is None]
            fresh = iter(_generate_resilient(runner, fresh_prompts, config.max_output_tokens) if fresh_prompts else [])
            for rec, prompt, key, cached in zip(batch_recs, prompts, keys, saved):
                if cached is not None:
                    row, rule_details, response = cached['row'], cached['rule_details'], cached['response']
                    prompt = _restored_prompt(cached['prompt'])
                    reused_responses += int(not runner.is_mock)
                else:
                    response = next(fresh)
                    try:
                        row, rule_details, response, prompt, retry_count = consume_with_retries(
                            rec, response, prompt, items, config, knowledge, runner, final_items)
                        retries += retry_count
                    except RuntimeError as exc:
                        failures.append({'id': rec['id'], 'items': list(items), 'error': str(exc)})
                        log(str(exc))
                        continue
                    journal.put(key, {'row': row, 'rule_details': rule_details, 'response': response,
                                      'prompt': _saved_prompt(prompt)})
                normal_calls[rec['id']] += int(not runner.is_mock)
                for k in items:
                    rows[rec['id']][f'v{k}'] = row[f'v{k}']
                    rows[rec['id']][f'e{k}'] = row[f'e{k}']
                completed_items[rec['id']].update(items)
                prompt_lengths.append(len(prompt["token_ids"]) if prompt["token_ids"] is not None else None)
                output_lengths.append(response["output_tokens"])
                if response.get("cached_input_tokens") is not None:
                    cached_tokens.append(response["cached_input_tokens"])
                if prompt.get("shared_prefix_tokens") is not None:
                    shared_prefixes.append(prompt["shared_prefix_tokens"])
                thinking_outputs += int(response.get("thinking_detected", False))
                thinking_outputs_expected += int(config.enable_thinking and
                    prompt.get('generation', {}).get('thinking_budget', config.thinking_budget_for(items)) != 0)
                thinking_characters += response.get("thinking_characters", 0)
                thinking_tokens_max = max(thinking_tokens_max, response.get("thinking_tokens", 0))
                answer_tokens += response.get("answer_tokens", response["output_tokens"])
                coverages.append(prompt["coverage"]["fraction"])
                if trace_file:
                    trace_file.write(json.dumps({"id": rec["id"], "pass": pass_n, "items": items,
                                                 "response": response, "coverage": prompt["coverage"],
                                                 "prompt_sha256": hashlib.sha256(json.dumps(prompt["messages"], ensure_ascii=False).encode()).hexdigest(),
                                                 "messages": prompt["messages"], "rule_checks": rule_details,
                                                 "legal_diagnostics": prompt.get("legal_diagnostics")}, ensure_ascii=False) + "\n")
                    trace_file.flush()
            log(f"pass {pass_n+1}/{len(groups)}: {min(offset+len(batch_recs),len(recs))}/{len(recs)}; {time.monotonic()-start:.1f}s")
    finally:
        journal.close()
        progress = {'complete_records': sum(len(items)==24 for items in completed_items.values()),
                    'records': len(recs), 'successful_responses': sum(normal_calls.values()),
                    'failures': failures, 'official_csv_complete': False,
                    'missing_items': {rid: sorted(set(range(1, 25))-items)
                                      for rid, items in completed_items.items() if len(items)<24}}
        (journal.path / 'progress.json').write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding='utf-8')
        if trace_file:
            trace_file.close()
    if failures or any(len(items) < 24 for items in completed_items.values()):
        raise ValueError(f'Incomplete predictions; completed responses preserved in {journal.path}')
    if not runner.is_mock and any(n < 1 for n in normal_calls.values()):
        raise RuntimeError("Every notice must have at least one successful fixed-model response")
    missing_evidence_counts = {str(k): 0 for k in range(1, 25)}
    missing_evidence_records = 0
    for row in rows.values():
        missing = missing_evidence_items(row)
        missing_evidence_records += bool(missing)
        for k in missing:
            missing_evidence_counts[str(k)] += 1
    if missing_evidence_records:
        log(f"Preserved judgments with unavailable source evidence in {missing_evidence_records} records")
    write_csv(output_path, [rows[r["id"]] for r in recs], recs=recs,
              require_positive_evidence=config.require_positive_evidence)
    elapsed = time.monotonic() - start
    token_lengths = [n for n in prompt_lengths if n is not None]
    report = {"config": dataclasses.asdict(config), "mock": runner.is_mock, "records": len(recs),
              "runtime_version": runner.version, "load_seconds": runner.load_seconds,
              "pipeline_seconds": round(elapsed, 3), "normal_model_calls": sum(normal_calls.values()),
              "new_normal_model_calls": sum(normal_calls.values()) - reused_responses,
              "reused_normal_responses": reused_responses,
              "retries": retries, "input_tokens_total": sum(token_lengths),
              "input_tokens_max": max(token_lengths, default=None), "output_tokens_total": sum(output_lengths),
              "thinking_outputs": thinking_outputs, "thinking_characters_total": thinking_characters,
              "thinking_outputs_expected": thinking_outputs_expected,
              "thinking_tokens_max": thinking_tokens_max,
              "cache_metrics_available": len(cached_tokens) == len(prompt_lengths),
              "cached_input_tokens_total": sum(cached_tokens),
              "shared_prefix_tokens_mean": sum(shared_prefixes)/len(shared_prefixes) if shared_prefixes else None,
              "answer_tokens_total": answer_tokens,
              "source_coverage_mean": round(sum(coverages)/len(coverages),4),
              "csv_validation": "PASS", "output": str(output_path),
              "positive_evidence_missing_records": missing_evidence_records,
              "positive_evidence_missing_by_item": missing_evidence_counts,
              "estimated_1853_seconds_in_this_environment": None if runner.is_mock or reused_responses else round(runner.load_seconds+elapsed/len(recs)*1853,1)}
    (output_path.parent / "run_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    progress['official_csv_complete'] = not runner.is_mock
    progress['csv_written'] = str(output_path)
    (journal.path / 'progress.json').write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "model/config.json")
    parser.add_argument("--data-dir", default=os.environ.get("PPS_DATA_DIR"))
    parser.add_argument("--output-dir", default=os.environ.get("PPS_OUTPUT_DIR"))
    parser.add_argument("--model-dir", default=os.environ.get("PPS_MODEL_DIR"))
    parser.add_argument("--input")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--tokenizer-dir")
    parser.add_argument("--trace", action="store_true", help="Local development traces; disabled in submitted runtime")
    args = parser.parse_args()
    if not args.data_dir or not args.output_dir:
        parser.error("Set PPS_DATA_DIR/PPS_OUTPUT_DIR, or supply --data-dir/--output-dir for local work")
    config = Config.load(args.config)
    if args.mock:
        tokenizer = None
        if args.tokenizer_dir:
            from transformers import AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(args.tokenizer_dir, local_files_only=True)
        runner = MockRunner(tokenizer)
    else:
        if not args.model_dir:
            parser.error("Set PPS_MODEL_DIR to the local competition model snapshot")
        runner = VLLMRunner(args.model_dir, config)
    output_path = Path(args.output_dir) / ("mock_submission.csv" if args.mock else "submission.csv")
    report = run(args.input or Path(args.data_dir) / "test.jsonl.gz", output_path, args.data_dir,
                 config, runner, limit=args.limit, trace=args.trace)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
