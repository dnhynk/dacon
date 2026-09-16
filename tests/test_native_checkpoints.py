"""A runtime loss must not turn partial or mixed native data into completion."""
import gzip
import json
import zipfile

import pytest

from tools.checkpoint_native_run import checkpoint


def batch(tmp_path, index=0, *, complete=True, flat=False):
    folder=tmp_path/'runtime_run' if flat else tmp_path/'runtime_run/normal'
    folder.mkdir(parents=True,exist_ok=True)
    prefix=f'call_{index:05d}_0'
    for kind in ('requests','native','responses'):
        value={'request_key':f'q{index}','value':kind}
        (folder/f'{prefix}_{kind}.jsonl.gz').write_bytes(gzip.compress((json.dumps(value)+'\n').encode()))
    (folder/f'{prefix}_sampling.json').write_text('{}')
    if complete:
        (folder/f'{prefix}.json').write_text(json.dumps({'count':1,'attempt':0,'batch':index}))
    return folder,prefix


def test_snapshot_preserves_committed_and_failed_native_without_certifying_completion(tmp_path):
    folder,_=batch(tmp_path)
    batch(tmp_path,1,complete=False)
    (folder/'call_00002_0_native.jsonl.gz.partial').write_bytes(b'unfinished')
    output=tmp_path/'checkpoints/first.zip'
    receipt=checkpoint(tmp_path/'runtime_run',output)
    assert receipt['committed_response_attempts']==1
    assert not receipt['cohort_completion_certified']
    assert not receipt['external_recovery_confirmed']
    with zipfile.ZipFile(output) as stream:
        assert stream.testzip() is None
        names=stream.namelist()
        assert 'normal/call_00001_0_native.jsonl.gz' in names
        assert not any(n.endswith('.partial') for n in names)
        manifest=json.loads(stream.read('checkpoint_manifest.json'))
        assert manifest['uncommitted_native_batches']==['normal/call_00001_0']
    with pytest.raises(ValueError,match='already exists'):
        checkpoint(tmp_path/'runtime_run',output)


def test_cumulative_checkpoint_does_not_change_a_previous_download(tmp_path):
    batch(tmp_path)
    first=tmp_path/'first.zip'
    checkpoint(tmp_path/'runtime_run',first)
    before=first.read_bytes()
    batch(tmp_path,1)
    assert checkpoint(tmp_path/'runtime_run',tmp_path/'second.zip')['committed_response_attempts']==2
    assert first.read_bytes()==before


def test_corrupt_or_different_native_identity_is_not_archived_as_valid(tmp_path):
    folder,prefix=batch(tmp_path)
    path=folder/f'{prefix}_native.jsonl.gz'
    path.write_bytes(gzip.compress(b'{"request_key":"another"}\n'))
    with pytest.raises(ValueError,match='identities'):
        checkpoint(tmp_path/'runtime_run',tmp_path/'checkpoint.zip')
    path.write_bytes(b'not a gzip file')
    with pytest.raises(gzip.BadGzipFile):
        checkpoint(tmp_path/'runtime_run',tmp_path/'corrupt.zip')


def test_a_committed_marker_with_missing_sidecars_is_an_error(tmp_path):
    folder,prefix=batch(tmp_path)
    (folder/f'{prefix}_responses.jsonl.gz').unlink()
    with pytest.raises(ValueError,match='sidecar'):
        checkpoint(tmp_path/'runtime_run',tmp_path/'checkpoint.zip')


def test_source_logs_partial_csv_and_unrelated_files_are_not_checkpoint_payload(tmp_path):
    folder,_=batch(tmp_path)
    (folder/'secret.txt').write_text('not part of the native journal')
    (folder/'submission.csv').write_text('unfinished CSV')
    (folder/'runtime.log').write_text('being appended')
    output=tmp_path/'checkpoint.zip'
    checkpoint(tmp_path/'runtime_run',output)
    with zipfile.ZipFile(output) as stream:
        assert not set(stream.namelist()) & {'normal/secret.txt','normal/submission.csv','normal/runtime.log'}


def test_flat_canonical_journal_is_checkpointed_without_nested_phase(tmp_path):
    folder,_=batch(tmp_path,flat=True)
    (folder/'input_freeze.json').write_text('{}')
    output=tmp_path/'flat.zip'
    receipt=checkpoint(tmp_path/'runtime_run',output)
    assert receipt['committed_response_attempts']==1
    with zipfile.ZipFile(output) as stream:
        names=set(stream.namelist())
        assert 'root/call_00000_0_native.jsonl.gz' in names
        assert 'root/input_freeze.json' in names
        manifest=json.loads(stream.read('checkpoint_manifest.json'))
        assert manifest['batches'][0]['phase']=='root'
