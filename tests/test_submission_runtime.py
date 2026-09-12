"""Submission contract tests use synthetic inputs only; no GPU or development labels."""
import gzip
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from submission import runtime
from submission.b4_entry import PROFILES
from submission.engine import configure_environment


def read_rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))


def packets(recs):
    rows = []
    for profile in PROFILES:
        first = int(profile[1:])
        items = list(range(first, min(first + 9, 25)))
        for rec in recs:
            rows.append({'record_id': rec['id'], 'request_key': f"{profile}:{rec['id']}",
                         'batch': profile, 'family': profile[0], 'items': items,
                         'spans': [], 'token_ids': [11, 22], 'prompt_sha256': 'synthetic',
                         'token_ids_sha256': 'synthetic', 'generation': {'response_format': 'compact'}})
    return rows


def test_order_uses_cohorts_and_last_l_phase():
    recs = [{'id': f'case-{99-i}'} for i in range(65)]
    plan = runtime.call_plan(recs, packets(recs))
    assert [b['profile'] for b in plan] == ['A1', 'A10', 'A19'] * 3 + ['L19'] * 3
    assert [len(b['request_keys']) for b in plan] == [32, 32, 32, 32, 32, 32, 1, 1, 1, 32, 32, 1]
    assert plan[0]['request_keys'][0].endswith('case-99')
    assert plan[3]['request_keys'][0].endswith('case-67')


def test_duplicate_execution_refused(tmp_path):
    runtime.Journal(tmp_path / 'output')
    with pytest.raises(FileExistsError):
        runtime.Journal(tmp_path / 'output')


def test_native_saved_before_parser_failure(tmp_path):
    packet = packets([{'id': 'native-test'}])[0]
    params = SimpleNamespace(temperature=0, seed=20260907)
    native = SimpleNamespace(request_id='0', num_cached_tokens=1, prompt_token_ids=[11, 22],
                             outputs=[SimpleNamespace(text='{broken', token_ids=[31, 32], finish_reason='stop')])
    def generate(*args, **kwargs):
        return [native]
    runner = SimpleNamespace(llm=SimpleNamespace(generate=generate))
    def parse_fails(batch, max_tokens):
        runner.llm.generate([{'prompt_token_ids': p['token_ids']} for p in batch], sampling_params=[params])
        raise ValueError('synthetic failure after native output')
    runner.generate = parse_fails
    journal = runtime.Journal(tmp_path / 'output')
    recorder = runtime.NativeRecorder(runner, journal)
    try:
        with pytest.raises(ValueError, match='synthetic failure'):
            recorder.generate([packet], 0, 0)
    finally:
        recorder.close()
    observed = read_rows(journal.root / 'call_00000_0_native.jsonl.gz')
    assert observed[0]['native']['raw_text'] == '{broken'
    assert observed[0]['native']['output_token_ids'] == [31, 32]
    assert (journal.root / 'call_00000_0_sampling.json').is_file()
    assert not (journal.root / 'call_00000_0_responses.jsonl.gz').exists()


def test_invalid_response_retained_without_retry_or_zero_fill(tmp_path, monkeypatch):
    config = SimpleNamespace(batch_size=32, require_positive_evidence=False)
    class SyntheticPipeline:
        def __init__(self, *args):
            self.config = config
        packets = staticmethod(packets)
        def consume(self, rec, packet, response):
            items = [20] if packet['family'] == 'L' else packet['items']
            return {f'{field}{k}': 0 if field == 'v' else '' for k in items for field in ('v', 'e')}, []
    monkeypatch.setattr(runtime, 'B4Pipeline', SyntheticPipeline)
    recs = [{'id': 'new-current-input'}]
    monkeypatch.setattr(runtime, 'records', lambda *args: iter(recs))
    calls = []
    def generation(batch, number, attempt):
        calls.append((batch[0]['batch'], attempt))
        return [{'text': '{' if p['batch'] == 'A1' else json.dumps({'v': [0] * len(p['items']), 'e': [0] * len(p['items'])}),
                 'finish_reason': 'stop', 'output_tokens': 1} for p in batch]
    with pytest.raises(ValueError, match='CSV withheld'):
        runtime.execute('unused', 'unused', tmp_path / 'output',
                        SimpleNamespace(config=config, tokenizer=object()), generation=generation)
    assert calls == [('A1', 0), ('A10', 0), ('A19', 0), ('L19', 0)]
    assert not (tmp_path / 'output/submission.csv').exists()
    report = json.loads((tmp_path / 'output/run_report.json').read_text(encoding='utf-8'))
    assert report['missing_bits']['final_B4'] == 9
    assert report['parse_retries'] == 0 and report['returned'] == 4
    rows = read_rows(tmp_path / 'output/final_B4.jsonl.gz')
    assert rows[0]['v1'] is None and rows[0]['v20'] == 0


def test_canonical_import_never_loads_root_pps():
    code = "import sys; from submission.main import main; assert 'pps' not in sys.modules; assert 'vllm' not in sys.modules; main(['--help'])"
    result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '--model-dir' in result.stdout


def test_environment_controls_scheduler_without_quantization_changes(monkeypatch):
    monkeypatch.delenv('VLLM_BATCH_INVARIANT', raising=False)
    monkeypatch.setenv('VLLM_ENABLE_V1_MULTIPROCESSING', '1')
    configure_environment()
    import os
    assert os.environ['VLLM_ENABLE_V1_MULTIPROCESSING'] == '0'
    assert os.environ['VLLM_USE_V2_MODEL_RUNNER'] == '0'
    assert os.environ['HF_HUB_OFFLINE'] == '1'
    assert os.environ.get('VLLM_BATCH_INVARIANT', '0') == '0'


def test_supported_default_cli_paths(tmp_path, monkeypatch):
    main = importlib.import_module('submission.main')
    import transformers
    for variable in ('PPS_DATA_DIR', 'PPS_MODEL_DIR', 'PPS_OUTPUT_DIR'):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(Path, 'is_dir', lambda self: True)
    monkeypatch.setattr(Path, 'is_file', lambda self: True)
    monkeypatch.setattr(Path, 'exists', lambda self: False)
    monkeypatch.setattr(transformers.AutoTokenizer, 'from_pretrained', lambda *args, **kwargs: object())
    observed = {}
    def execute(input_path, data_dir, output_dir, **kwargs):
        observed.update(input=str(input_path), data=str(data_dir), output=str(output_dir))
        return {'test_only': True}
    monkeypatch.setattr(main, 'execute', execute)
    main.main([])
    assert Path(observed['input']) == Path('data/test.jsonl.gz')
    assert Path(observed['data']) == Path('data') and Path(observed['output']) == Path('output')
