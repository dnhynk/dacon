"""One local fixed-model engine with an explicit offline scheduling contract."""
from __future__ import annotations

import dataclasses
import enum
import hashlib
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import time

from .pps.pipeline import VLLMRunner

REPRODUCIBILITY_SOURCE = 'https://docs.vllm.ai/en/v0.26.0/usage/reproducibility/'
POLICY = {
    'a_order': 'input-order cohorts of config.a_cohort_size (default32); A1, A10, A19 within each cohort',
    'l_order': 'after all A calls; L19 in input-order cohorts of 32',
    'engine_loads': 1,
    'text_only': 'Canonical config disables image/audio/video input capacity; all submitted inputs are original text tokens. Reprofiled cache capacity is recorded, not claimed numerically identical to older multimodal defaults.',
    'prefix_cache': 'enabled; reused across all A and L calls; no reset',
    'historical_difference': 'Historical L responses used a separate engine with prior L1/L10 calls; those unused calls are not repeated.',
    'parse_retries': 'config.max_response_retries, only invalid format/termination, after all primary calls',
    'recovery_inputs': 'full original tokens, spans and schema; thinking budget zero; no document shrinking',
    'quality_retries': 0,
    'structured_output': 'Fixed guidance backend; engine-level compact JSON whitespace; CPU actual-token progress check before weights. Long JSON whitespace stalls abort after native evidence is saved.',
    'runtime_deadline': 'cohort executor: checked between batches, config.total_runtime_seconds minus 15s. stream executor: submissions stop at deadline minus margin, in-flight requests are aborted after a grace period, and the CSV is always written from source-only rows for whatever the engine did not answer',
    'stream_executor': 'default entry: record-major streaming; A1, A10, A19 of one record enter the engine adjacently for prefix reuse; optional/expensive profiles are dropped per record by a deadline projection; the cohort executor remains available with --executor cohort',
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
    # The CUDA-graph memory estimate is a documented vLLM knob that only moves
    # about 0.2GiB between the activation reserve and the KV cache; skipping it
    # removes one profiling pass from the engine load.
    os.environ.setdefault('VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS', '0')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['VLLM_NO_USAGE_STATS'] = '1'


def native_toolchain_preflight():
    """Activate this interpreter's console tools before expensive CUDA loading.

    Launching a venv's python by absolute path does not activate its bin directory.
    FlashInfer invokes ninja by name, even when the Python package is installed.
    """
    scripts = str(Path(sysconfig.get_path('scripts')).resolve())
    old = os.environ.get('PATH', '').split(os.pathsep)
    key = lambda value: os.path.normcase(os.path.abspath(value))
    os.environ['PATH'] = os.pathsep.join([scripts, *(p for p in old if p and key(p) != key(scripts))])
    ninja = shutil.which('ninja')
    if ninja is None:
        raise RuntimeError('Native toolchain preflight: ninja executable unavailable; model not loaded')
    try:
        version = subprocess.check_output([ninja, '--version'], text=True,
                                          stderr=subprocess.STDOUT, timeout=10).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('Native toolchain preflight: ninja could not run; model not loaded') from exc
    return {'scripts_directory': scripts, 'ninja_executable': ninja, 'ninja_version': version,
            'ninja_sha256': hashlib.sha256(Path(ninja).read_bytes()).hexdigest()}


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
             'VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS', 'VLLM_TUNED_CONFIG_FOLDER', 'PPS_MOE_TUNING',
             'PPS_EMBED_DIR', 'PPS_EMBED_DEVICE', 'PPS_PREP_WORKERS',
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
        journal.save('native_toolchain.json', native_toolchain_preflight())
        from .pps.generation_contract import preflight
        journal.save('generation_grammar_preflight.json', preflight())
        from transformers import AutoTokenizer
        from .pps.generation_contract import progress_preflight
        tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False)
        journal.save('generation_progress_preflight.json', progress_preflight(tokenizer))
        journal.save('environment.json', environment(model_dir))
        # Import only after configuring the documented offline process contract.
        import vllm
        if vllm.__version__.split('+')[0] != '0.26.0':
            raise RuntimeError(f'This execution contract requires vLLM 0.26.0; found {vllm.__version__}')
        super().__init__(model_dir, config)
        engine_config = getattr(self.llm.llm_engine, 'vllm_config', None)
        model_config = getattr(engine_config, 'model_config', None)
        structured = getattr(engine_config, 'structured_outputs_config', None)
        if (getattr(structured, 'backend', None) != 'guidance'
                or getattr(structured, 'disable_any_whitespace', None) is not True):
            self.close()
            raise RuntimeError('Actual engine JSON grammar options differ from CPU-verified settings')
        journal.save('engine.json', {
            'version': self.version, 'load_seconds': self.load_seconds,
            'config_repr': str(engine_config),
            'scheduler_config': serial(getattr(engine_config, 'scheduler_config', None)),
            'cache_config': serial(getattr(engine_config, 'cache_config', None)),
            'structured_outputs_config': serial(structured),
            'model_generation_config': model_config.try_get_generation_config() if model_config else None,
            'tokenizer_class': type(self.tokenizer).__name__,
            'tokenizer_chat_template': getattr(self.tokenizer, 'chat_template', None),
            'tokenizer_eos_token_id': getattr(self.tokenizer, 'eos_token_id', None),
            'checkpoint_identity': self.checkpoint_identity, 'policy': POLICY,
        })

    def close(self):
        self.llm.llm_engine.engine_core.shutdown()
