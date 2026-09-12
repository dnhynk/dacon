"""One local fixed-model engine with an explicit offline scheduling contract."""
from __future__ import annotations

import dataclasses
import enum
import hashlib
import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys
import time

from .pps.pipeline import VLLMRunner

REPRODUCIBILITY_SOURCE = 'https://docs.vllm.ai/en/v0.26.0/usage/reproducibility/'
POLICY = {
    'a_order': 'input-order cohorts of 32; A1, A10, A19 within each cohort',
    'l_order': 'after all A calls; L19 in input-order cohorts of 32',
    'engine_loads': 1,
    'prefix_cache': 'enabled; reused across all A and L calls; no reset',
    'historical_difference': 'Historical L responses used a separate engine with prior L1/L10 calls; those unused calls are not repeated.',
    'parse_retries': 0,
    'quality_retries': 0,
    'runtime_deadline': None,
    'batch_invariance': 'not enabled',
    'reproducibility_scope': 'Fixed offline scheduler, inputs and call history; no claim of equality across hardware or vLLM versions.',
    'official_source': REPRODUCIBILITY_SOURCE,
}


def serial(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, enum.Enum):
        return serial(value.value)
    if isinstance(value, dict):
        return {str(k): serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serial(v) for v in value]
    if dataclasses.is_dataclass(value):
        return {f.name: serial(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if hasattr(value, '__struct_fields__'):
        return {k: serial(getattr(value, k)) for k in value.__struct_fields__}
    return {'type': type(value).__name__, 'repr': repr(value)}


def configure_environment():
    if 'vllm' in sys.modules and os.environ.get('VLLM_ENABLE_V1_MULTIPROCESSING') != '0':
        raise RuntimeError('Set the canonical environment before importing vLLM; use a fresh process')
    if os.environ.get('VLLM_BATCH_INVARIANT', '0') != '0':
        raise RuntimeError('Batch invariance is outside this fixed execution contract')
    os.environ['VLLM_ENABLE_V1_MULTIPROCESSING'] = '0'
    os.environ['VLLM_USE_V2_MODEL_RUNNER'] = '0'
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['VLLM_NO_USAGE_STATS'] = '1'


def environment(model_dir):
    model = Path(model_dir)
    files = {}
    for path in sorted(model.iterdir()):
        if path.is_file():
            entry = {'bytes': path.stat().st_size}
            if path.suffix in ('.json', '.jinja') and entry['bytes'] < 10_000_000:
                entry['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            files[path.name] = entry
    try:
        gpu = subprocess.check_output(
            ['nvidia-smi', '--query-gpu=name,uuid,driver_version,memory.total,memory.used',
             '--format=csv,noheader'], text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        gpu = {'observation_error': str(exc)}
    names = ('VLLM_ENABLE_V1_MULTIPROCESSING', 'VLLM_USE_V2_MODEL_RUNNER',
             'VLLM_BATCH_INVARIANT', 'VLLM_WORKER_MULTIPROC_METHOD', 'VLLM_NO_USAGE_STATS',
             'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'CUDA_VISIBLE_DEVICES',
             'CUBLAS_WORKSPACE_CONFIG', 'PYTHONHASHSEED', 'OMP_NUM_THREADS')
    return {'epoch': time.time(), 'python': sys.version, 'model_dir': str(model.resolve()),
            'model_files': files, 'weight_shard_hashes_observed': False,
            'packages': {d.metadata['Name']: d.version for d in importlib.metadata.distributions()},
            'environment': {k: os.environ.get(k) for k in names}, 'gpu': gpu,
            'policy': POLICY}


class CanonicalRunner(VLLMRunner):
    def __init__(self, model_dir, config, journal):
        configure_environment()
        journal.save('environment.json', environment(model_dir))
        # Import only after configuring the documented offline process contract.
        import vllm
        if vllm.__version__.split('+')[0] != '0.26.0':
            raise RuntimeError(f'This execution contract requires vLLM 0.26.0; found {vllm.__version__}')
        super().__init__(model_dir, config)
        # Complete the single authorized run; the inherited research timer is not a kill policy.
        self.deadline = float('inf')
        engine_config = getattr(self.llm.llm_engine, 'vllm_config', None)
        model_config = getattr(engine_config, 'model_config', None)
        journal.save('engine.json', {
            'version': self.version, 'load_seconds': self.load_seconds,
            'config_repr': str(engine_config),
            'scheduler_config': serial(getattr(engine_config, 'scheduler_config', None)),
            'cache_config': serial(getattr(engine_config, 'cache_config', None)),
            'model_generation_config': model_config.try_get_generation_config() if model_config else None,
            'tokenizer_class': type(self.tokenizer).__name__,
            'tokenizer_chat_template': getattr(self.tokenizer, 'chat_template', None),
            'tokenizer_eos_token_id': getattr(self.tokenizer, 'eos_token_id', None),
            'checkpoint_identity': self.checkpoint_identity, 'policy': POLICY,
        })

    def close(self):
        self.llm.llm_engine.engine_core.shutdown()
