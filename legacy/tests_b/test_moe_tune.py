"""Startup MoE kernel tuning: pure helpers and the parent-side contract; no GPU."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from submission import moe_tune
from submission.moe_tune import (DEFAULT_KEYS, FIELDS, PROBE_TOKENS, assemble, coarse_candidates, decide,
                                 moe_shape, only_fields, refinements, tune_moe_kernels)

ROOT = Path(__file__).resolve().parents[1]


def test_shape_comes_from_the_fixed_checkpoint_config():
    shape = moe_shape(ROOT / 'models/gemma-tokenizer')
    assert shape == {'experts': 128, 'topk': 8, 'hidden': 2816, 'intermediate': 704}


def test_shape_rejects_incomplete_configs(tmp_path):
    (tmp_path / 'config.json').write_text(json.dumps({'text_config': {'num_experts': 4}}), encoding='utf-8')
    with pytest.raises(ValueError):
        moe_shape(tmp_path)


def test_candidates_are_distinct_complete_and_bounded():
    coarse = coarse_candidates()
    assert len(coarse) == 16 and len({tuple(c[f] for f in FIELDS) for c in coarse}) == 16
    assert all(set(c) == set(FIELDS) for c in coarse)
    seen = {tuple(c[f] for f in FIELDS) for c in coarse}
    refined = refinements(coarse[0], set(seen))
    assert refined and all(set(c) == set(FIELDS) for c in refined)
    assert all(tuple(c[f] for f in FIELDS) not in seen for c in refined)
    assert all(32 <= c['BLOCK_SIZE_N'] <= 256 for c in refined)


def test_decision_requires_a_clear_gain_and_agreement():
    assert decide(10., 9., True)
    assert not decide(10., 9.6, True)
    assert not decide(10., 9., False)
    assert not decide(None, 9., True) and not decide(10., None, True)


def test_assembled_json_keeps_defaults_for_small_batches_and_six_fields_only():
    default = {'BLOCK_SIZE_M': 16, 'BLOCK_SIZE_N': 64, 'BLOCK_SIZE_K': 128, 'GROUP_SIZE_M': 1,
               'SPLIT_K': 1, 'num_warps': 4, 'num_stages': 4}
    tuned = {'BLOCK_SIZE_M': 128, 'BLOCK_SIZE_N': 256, 'BLOCK_SIZE_K': 64, 'GROUP_SIZE_M': 16,
             'num_warps': 8, 'num_stages': 3}
    body = assemble({m: default for m in DEFAULT_KEYS}, {PROBE_TOKENS[0]: tuned})
    assert set(body) == {str(m) for m in (*DEFAULT_KEYS, PROBE_TOKENS[0])}
    assert all(set(v) == set(FIELDS) for v in body.values())
    assert body[str(PROBE_TOKENS[0])] == only_fields(tuned) and body['1'] == only_fields(default)


class Journal:
    def __init__(self, root):
        self.root = root
        self.saved = {}

    def save(self, name, obj):
        self.saved[name] = obj


def fake_run(report_path, folder, *, status='written', returncode=0, raise_timeout=False):
    def run(command, **kwargs):
        if raise_timeout:
            import subprocess
            raise subprocess.TimeoutExpired(command, kwargs.get('timeout'))
        assert command[1:4] == ['-B', '-m', 'submission.moe_tune']
        assert 'PYTHONPATH' in kwargs['env'] and kwargs['timeout'] > 0
        file = folder / 'E=128,N=704,device_name=Synthetic_GPU,dtype=int8_w8a16.json'
        folder.mkdir(parents=True, exist_ok=True)
        if status == 'written':
            file.write_text('{}', encoding='utf-8')
        report_path.write_text(json.dumps({'status': status, 'file': str(file), 'file_name': file.name,
                                           'device': 'Synthetic GPU', 'seconds': 1.0}), encoding='utf-8')
        return SimpleNamespace(returncode=returncode)
    return run


def test_parent_applies_only_a_written_file(tmp_path, monkeypatch):
    monkeypatch.delenv('VLLM_TUNED_CONFIG_FOLDER', raising=False)
    journal = Journal(tmp_path)
    monkeypatch.setattr(moe_tune.subprocess, 'run',
                        fake_run(tmp_path / 'moe_tuning_report.json', tmp_path / 'moe_tuned_configs'))
    outcome = tune_moe_kernels(tmp_path / 'model', journal, seconds=30)
    import os
    assert outcome['status'] == 'applied' and os.environ['VLLM_TUNED_CONFIG_FOLDER'] == str(tmp_path / 'moe_tuned_configs')
    assert journal.saved['moe_tuning.json']['report']['status'] == 'written'


def test_parent_leaves_the_engine_alone_without_a_clear_win(tmp_path, monkeypatch):
    monkeypatch.delenv('VLLM_TUNED_CONFIG_FOLDER', raising=False)
    journal = Journal(tmp_path)
    monkeypatch.setattr(moe_tune.subprocess, 'run',
                        fake_run(tmp_path / 'moe_tuning_report.json', tmp_path / 'moe_tuned_configs', status='not_written'))
    outcome = tune_moe_kernels(tmp_path / 'model', journal, seconds=30)
    import os
    assert outcome['status'] == 'measured_not_applied' and 'VLLM_TUNED_CONFIG_FOLDER' not in os.environ


@pytest.mark.parametrize('failure', ['timeout', 'launch'])
def test_parent_survives_subprocess_failures(tmp_path, monkeypatch, failure):
    monkeypatch.delenv('VLLM_TUNED_CONFIG_FOLDER', raising=False)
    journal = Journal(tmp_path)
    if failure == 'timeout':
        monkeypatch.setattr(moe_tune.subprocess, 'run',
                            fake_run(tmp_path / 'r.json', tmp_path / 'c', raise_timeout=True))
    else:
        def boom(*args, **kwargs):
            raise OSError('no python')
        monkeypatch.setattr(moe_tune.subprocess, 'run', boom)
    outcome = tune_moe_kernels(tmp_path / 'model', journal, seconds=30)
    import os
    assert outcome['status'] in {'timeout', 'launch_failed'} and 'VLLM_TUNED_CONFIG_FOLDER' not in os.environ
    assert 'moe_tuning.json' in journal.saved


def test_disabled_tuning_records_a_skip(tmp_path):
    journal = Journal(tmp_path)
    assert tune_moe_kernels(tmp_path, journal, enabled=False)['status'] == 'skipped'
