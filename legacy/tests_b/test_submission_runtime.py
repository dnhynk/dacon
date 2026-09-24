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


def test_native_toolchain_activates_own_environment_idempotently(tmp_path, monkeypatch):
    from submission import engine
    import os
    scripts = tmp_path / 'isolated' / 'Scripts'
    scripts.mkdir(parents=True)
    executable = scripts / 'ninja.exe'
    executable.write_bytes(b'synthetic executable identity')
    monkeypatch.setattr(engine.sysconfig, 'get_path', lambda name: str(scripts))
    monkeypatch.setenv('PATH', str(tmp_path / 'unrelated'))
    def find(name):
        assert name == 'ninja'
        assert os.environ['PATH'].split(os.pathsep)[0] == str(scripts.resolve())
        return str(executable)
    monkeypatch.setattr(engine.shutil, 'which', find)
    calls = []
    def check(command, **kwargs):
        calls.append(command)
        assert kwargs['timeout'] == 10
        return '1.13.2\n'
    monkeypatch.setattr(engine.subprocess, 'check_output', check)
    first = engine.native_toolchain_preflight()
    second = engine.native_toolchain_preflight()
    assert first == second and first['ninja_version'] == '1.13.2'
    assert os.environ['PATH'].split(os.pathsep).count(str(scripts.resolve())) == 1
    assert calls == [[str(executable), '--version']] * 2


@pytest.mark.parametrize('broken', [False, True])
def test_missing_or_broken_ninja_fails_before_model_import(tmp_path, monkeypatch, broken):
    from submission import engine
    monkeypatch.setattr(engine.shutil, 'which', lambda name: 'broken-ninja' if broken else None)
    def fails(*args, **kwargs):
        raise FileNotFoundError('synthetic native executable failure')
    monkeypatch.setattr(engine.subprocess, 'check_output', fails)
    def loaded_too_early(*args, **kwargs):
        pytest.fail('Model environment accessed before native toolchain preflight')
    monkeypatch.setattr(engine, 'environment', loaded_too_early)
    with pytest.raises(RuntimeError, match='Native toolchain preflight'):
        engine.CanonicalRunner(tmp_path, object(), SimpleNamespace(save=loaded_too_early))


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
    (tmp_path / 'output/lost+found').mkdir(parents=True)    # a platform entry is not a prior run
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


@pytest.mark.parametrize('failure', ['recovers', 'exhausted', 'cpu'])
def test_format_recovery_after_primaries_preserves_sources_and_never_retries_cpu_errors(tmp_path, monkeypatch, failure):
    config = SimpleNamespace(batch_size=32, require_positive_evidence=False, max_response_retries=2)
    class SyntheticPipeline:
        def __init__(self, *args):
            self.config = config
        packets = staticmethod(packets)
        def consume(self, rec, packet, response):
            if failure == 'cpu' and packet['batch'] == 'A1':
                raise ValueError('synthetic deterministic CPU defect')
            items = [20] if packet['family'] == 'L' else packet['items']
            return {f'{field}{k}': 0 if field == 'v' else '' for k in items for field in ('v', 'e')}, []
    monkeypatch.setattr(runtime, 'B4Pipeline', SyntheticPipeline)
    recs = [{'id': 'recovery-case'}]
    monkeypatch.setattr(runtime, 'records', lambda *args: iter(recs))
    originals = {p['request_key']: p for p in packets(recs)}
    calls = []
    def generation(batch, number, attempt):
        calls.append((batch[0]['batch'], attempt))
        for p in batch:
            assert p['token_ids'] == originals[p['request_key']]['token_ids']
            assert p['spans'] == originals[p['request_key']]['spans']
            if attempt:
                assert p['generation']['thinking_budget'] == 0
                assert p['generation']['response_format'] == originals[p['request_key']]['generation']['response_format']
        return [{'text': '{' if p['batch'] == 'A1' and failure != 'cpu' and (attempt == 0 or failure == 'exhausted')
                 else json.dumps({'v': [0] * len(p['items']), 'e': [0] * len(p['items'])}),
                 'finish_reason': 'stop', 'output_tokens': 1} for p in batch]
    args = ('unused', 'unused', tmp_path / 'output', SimpleNamespace(config=config, tokenizer=object()))
    if failure == 'recovers':
        report = runtime.execute(*args, generation=generation)
        assert report['primary_requests'] == 4 and report['parse_retries'] == 1
        assert (tmp_path / 'output/submission.csv').exists()
    else:
        with pytest.raises(ValueError, match='CSV withheld'):
            runtime.execute(*args, generation=generation)
        assert not (tmp_path / 'output/submission.csv').exists()
    assert calls[:4] == [('A1', 0), ('A10', 0), ('A19', 0), ('L19', 0)]
    assert calls[4:] == ([] if failure == 'cpu' else [('A1', 1)] if failure == 'recovers' else [('A1', 1), ('A1', 2)])
    selected = read_rows(tmp_path / 'output/resolved_responses.jsonl.gz')
    assert selected[0]['attempt'] == (0 if failure == 'cpu' else 1 if failure == 'recovers' else 2)
    primary = read_rows(tmp_path / 'output/first_part_00000.jsonl.gz')
    if failure != 'cpu':
        assert primary[0]['response']['text'] == '{'


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
        observed.update(input=str(input_path), data=str(data_dir), output=str(output_dir), executor='cohort')
        return {'test_only': True}
    def execute_stream(input_path, data_dir, output_dir, **kwargs):
        observed.update(input=str(input_path), data=str(data_dir), output=str(output_dir), executor='stream',
                        options=kwargs['options'])
        kwargs['pool'].close()
        return {'test_only': True}
    monkeypatch.setattr(main, 'execute', execute)
    monkeypatch.setattr(main, 'execute_stream', execute_stream)
    main.main([])
    assert observed['executor'] == 'stream' and observed['options'].tier_ceiling == 2
    assert observed['options'].tier_plan == 'adaptive'
    main.main(['--tier-plan', 'fixed'])
    assert observed['options'].tier_plan == 'fixed' and observed['options'].tier_ceiling == 2
    assert Path(observed['input']) == Path('data/test.jsonl.gz')
    assert Path(observed['data']) == Path('data') and Path(observed['output']) == Path('output')
    main.main(['--executor', 'cohort'])
    assert observed['executor'] == 'cohort'


def test_cli_refuses_only_a_prior_run_in_the_output_directory(tmp_path, monkeypatch):
    main = importlib.import_module('submission.main')
    data, model, output = tmp_path / 'data', tmp_path / 'model', tmp_path / 'output'
    for path in (data, model, output / 'lost+found'):
        path.mkdir(parents=True)
    (data / 'test.jsonl.gz').write_bytes(b'')
    monkeypatch.setattr(main, 'configure_environment', lambda: None)
    calls = []
    def execute_stream(input_path, data_dir, output_dir, **kwargs):
        calls.append(output_dir)
        kwargs['pool'].close()
        return {'test_only': True}
    monkeypatch.setattr(main, 'execute_stream', execute_stream)
    argv = ['--data-dir', str(data), '--model-dir', str(model), '--output-dir', str(output)]
    main.main(argv)
    assert calls == [output]
    (output / 'started.json').write_text('{}', encoding='utf-8')
    with pytest.raises(SystemExit):
        main.main(argv)
    assert calls == [output]


def test_budget_observer_can_stop_after_saved_prefix_without_rerunning_or_filling(tmp_path, monkeypatch):
    config = SimpleNamespace(batch_size=32, require_positive_evidence=False)
    class SyntheticPipeline:
        def __init__(self, *args):
            self.config = config
        packets = staticmethod(packets)
        def consume(self, rec, packet, response):
            items = [20] if packet['family'] == 'L' else packet['items']
            return {f'{f}{k}': 0 if f == 'v' else '' for k in items for f in ('v', 'e')}, []
    monkeypatch.setattr(runtime, 'B4Pipeline', SyntheticPipeline)
    monkeypatch.setattr(runtime, 'records', lambda *args: iter([{'id': 'prefix'}]))
    calls = []
    def generate(batch, number, attempt):
        calls.append(number)
        return [{'text': json.dumps({'v': [0] * len(p['items']), 'e': [0] * len(p['items'])}),
                 'finish_reason': 'stop', 'output_tokens': 1} for p in batch]
    def stop_after_prefix(path, batch):
        assert (path / f"final_part_{batch['number']:05d}.jsonl.gz").is_file()
        if batch['number'] == 2:
            raise TimeoutError('Pilot review did not authorize more computation')
    with pytest.raises(TimeoutError, match='Pilot review'):
        runtime.execute('unused', 'unused', tmp_path / 'output',
            SimpleNamespace(config=config, tokenizer=object()), generation=generate,
            batch_observer=stop_after_prefix)
    assert calls == [0, 1, 2]
    assert not (tmp_path / 'output/submission.csv').exists()
    assert (tmp_path / 'output/failure.json').is_file()


def test_whitespace_stall_stops_after_preserving_first_batch(tmp_path, monkeypatch):
    config = SimpleNamespace(batch_size=32, require_positive_evidence=False, max_response_retries=2)
    class SyntheticPipeline:
        def __init__(self, *args):
            self.config = config
        packets = staticmethod(packets)
        def consume(self, *args):
            pytest.fail('Stalled response must not reach judgment consumption')
    monkeypatch.setattr(runtime, 'B4Pipeline', SyntheticPipeline)
    monkeypatch.setattr(runtime, 'records', lambda *args: iter([{'id': 'stall'}]))
    calls = []
    def generate(batch, number, attempt):
        calls.append((number, attempt))
        return [{'text': '{', 'finish_reason': 'length', 'output_tokens': 1536,
                 'generation_stall': {'kind': 'outside_json_string_whitespace_loop',
                                      'trailing_characters': 7000}} for _ in batch]
    with pytest.raises(RuntimeError, match='JSON generation stalled'):
        runtime.execute('unused', 'unused', tmp_path/'output',
            SimpleNamespace(config=config, tokenizer=object()), generation=generate)
    assert calls == [(0, 0)]
    saved = read_rows(tmp_path/'output/first_part_00000.jsonl.gz')
    assert saved[0]['response']['generation_stall']['trailing_characters'] == 7000
    assert (tmp_path/'output/failure.json').is_file()
    assert not (tmp_path/'output/submission.csv').exists()
