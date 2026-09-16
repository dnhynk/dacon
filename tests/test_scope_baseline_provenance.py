"""A saved baseline and fresh diagnostic must share verified source and consumer."""
import json
from pathlib import PureWindowsPath

import pytest

from tools.score_catalog_scope import sha, verified_replay_path


def fixture(tmp_path):
    baseline, replay = tmp_path/'baseline', tmp_path/'replay'
    baseline.mkdir()
    replay.mkdir()
    paths = []
    for name in ('current_inputs.jsonl.gz', 'current_packets.jsonl.gz', 'resolved_responses.jsonl.gz'):
        path = baseline/name
        path.write_bytes(name.encode())
        paths.append(path)
    prediction = replay/'B4.csv'
    prediction.write_text('fixed prediction', encoding='utf-8')
    manifest = {'kind': 'saved_response_current_cpu_replay', 'new_model_calls': 0,
        'source_sha256': {'pps/pipeline.py': 'consumer-v1'},
        'input_sha256': {str(PureWindowsPath('C:/preserved')/p.name): sha(p) for p in paths},
        'prediction_sha256': {'B4.csv': sha(prediction)}}
    (replay/'freeze.json').write_text(json.dumps(manifest), encoding='utf-8')
    return baseline, replay, manifest


def test_preserved_windows_paths_can_be_verified_after_archive_relocation(tmp_path):
    baseline, replay, manifest = fixture(tmp_path)
    assert verified_replay_path(baseline, replay, manifest['source_sha256']) == replay/'B4.csv'


@pytest.mark.parametrize('target', ['current_inputs.jsonl.gz', 'current_packets.jsonl.gz',
                                     'resolved_responses.jsonl.gz', 'B4.csv'])
def test_a_changed_notice_packet_response_or_prediction_is_rejected(tmp_path, target):
    baseline, replay, manifest = fixture(tmp_path)
    ((replay if target == 'B4.csv' else baseline)/target).write_bytes(b'changed')
    with pytest.raises(AssertionError):
        verified_replay_path(baseline, replay, manifest['source_sha256'])


def test_a_different_consumer_cannot_be_called_a_matched_baseline(tmp_path):
    baseline, replay, _ = fixture(tmp_path)
    with pytest.raises(AssertionError):
        verified_replay_path(baseline, replay, {'pps/pipeline.py': 'different'})


def test_duplicate_basename_alias_cannot_hide_an_unrelated_input(tmp_path):
    baseline, replay, manifest = fixture(tmp_path)
    manifest['input_sha256']['/other/current_inputs.jsonl.gz'] = sha(baseline/'current_inputs.jsonl.gz')
    (replay/'freeze.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(AssertionError):
        verified_replay_path(baseline, replay, manifest['source_sha256'])
