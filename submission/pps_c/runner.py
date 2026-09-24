"""The fixed model through vLLM offline (evaluation image vllm 0.26.0), grammar-bound JSON output."""
from __future__ import annotations

import os
import sys
import time

SEED = 20260925


def log(msg):
    print(f'[pps_c] {msg}', file=sys.stderr, flush=True)


def configure_environment():
    # Must run before vLLM is imported (vLLM 0.26 reads these at import).
    os.environ['VLLM_ENABLE_V1_MULTIPROCESSING'] = '0'
    os.environ['VLLM_USE_V2_MODEL_RUNNER'] = '0'
    os.environ.setdefault('VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS', '0')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['VLLM_NO_USAGE_STATS'] = '1'


class Engine:
    def __init__(self, model_dir, max_model_len=16384, max_num_seqs=48, gpu_memory_utilization=0.9,
                 max_num_batched_tokens=8192, quantization='int8_per_channel_weight_only', thinking=False):
        t0 = time.time()
        configure_environment()
        from vllm import LLM
        import vllm
        self.version = vllm.__version__
        self.thinking = thinking
        structured = {'backend': 'guidance', 'disable_any_whitespace': True}
        extra = {}
        if thinking:
            # vLLM 0.26 enforces a thinking budget only with the Gemma 4 reasoning delimiters configured on the engine;
            # the JSON grammar then applies after the reasoning channel closes.
            from vllm.config import ReasoningConfig
            structured.update(reasoning_parser='gemma4', enable_in_reasoning=False)
            extra['reasoning_config'] = ReasoningConfig(reasoning_start_str='<|channel>', reasoning_end_str='<channel|>')
        self.llm = LLM(model=str(model_dir), tokenizer=str(model_dir), quantization=quantization, dtype='auto',
                       max_model_len=max_model_len, gpu_memory_utilization=gpu_memory_utilization,
                       max_num_seqs=max_num_seqs, max_num_batched_tokens=max_num_batched_tokens, seed=SEED,
                       enable_prefix_caching=True, trust_remote_code=False, structured_outputs_config=structured,
                       limit_mm_per_prompt={'image': 0, 'audio': 0, 'video': 0}, **extra)
        self.tok = self.llm.get_tokenizer()
        self.max_model_len = max_model_len
        self.load_seconds = time.time() - t0
        log(f'vLLM {self.version} loaded in {self.load_seconds:.1f}s (thinking={thinking})')

    def token_ids(self, messages, thinking=False):
        ids = self.tok.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, enable_thinking=thinking)
        if hasattr(ids, 'keys'):
            ids = ids['input_ids']
        if ids and isinstance(ids[0], list):
            ids = ids[0]
        return list(ids)

    def generate(self, batch):
        """batch: [(token_ids, schema, max_tokens[, thinking_budget])] → [(text, finish_reason, output_tokens)]."""
        from vllm import SamplingParams
        from vllm.sampling_params import StructuredOutputsParams
        params = []
        for req in batch:
            _, schema, mt = req[:3]
            budget = req[3] if len(req) > 3 else 0
            kw = {}
            if self.thinking:
                kw = {'thinking_token_budget': budget, 'skip_special_tokens': False}
            params.append(SamplingParams(temperature=0.0, seed=SEED, max_tokens=mt + budget,
                                         structured_outputs=StructuredOutputsParams(json=schema, disable_any_whitespace=True),
                                         **kw))
        outs = self.llm.generate([{'prompt_token_ids': req[0]} for req in batch], params, use_tqdm=False)
        res = []
        for o in outs:
            c = o.outputs[0] if o.outputs else None
            res.append((c.text if c else '', c.finish_reason if c else 'none', len(c.token_ids) if c else 0))
        return res


class MockEngine:
    """No model: every request returns an all-불명 answer, so CPU defaults decide (tests and dry runs)."""
    load_seconds = 0.0
    max_model_len = 16384

    def token_ids(self, messages, thinking=False):
        return list(range(sum(len(m['content']) for m in messages) // 2))

    def generate(self, batch):
        import json
        out = []
        for req in batch:
            schema = req[1]
            obj = {}
            for key, spec in schema['properties'].items():
                if spec.get('type') == 'object':
                    obj[key] = {k: ('-' if v.get('type') == 'string' and 'enum' not in v else '불명')
                                for k, v in spec['properties'].items()}
                else:
                    obj[key] = '불명'
            out.append((json.dumps(obj, ensure_ascii=False), 'stop', 0))
        return out
