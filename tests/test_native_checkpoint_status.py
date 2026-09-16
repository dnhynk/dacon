"""Exercise the live status/download hook with a real native journal fixture."""
import json

import pytest

from tests.test_native_checkpoints import batch
from tools.native_checkpoint_status import recover_progress


def test_first_commit_downloads_once_and_waits_for_declared_increment(tmp_path):
    sent=[];run=tmp_path/'runtime_run';out=tmp_path/'snapshots'
    assert not recover_progress(run,out,sent.append)['checkpoint_available']
    batch(tmp_path)
    first=recover_progress(run,out,sent.append)
    assert len(sent)==1 and first['committed_response_attempts']==1
    assert first['download_requested'] and not first['external_recovery_confirmed']
    recover_progress(run,out,sent.append)
    batch(tmp_path,1)
    recover_progress(run,out,sent.append,now=first['created_epoch']+100)
    assert len(sent)==1
    second=recover_progress(run,out,sent.append,now=first['created_epoch']+301)
    assert len(sent)==2 and second['committed_response_attempts']==2


def test_download_failure_keeps_checkpoint_available_for_next_status(tmp_path):
    batch(tmp_path);run=tmp_path/'runtime_run';out=tmp_path/'snapshots'
    def unavailable(_):raise RuntimeError('transfer unavailable')
    with pytest.raises(RuntimeError,match='unavailable'):
        recover_progress(run,out,unavailable)
    assert len(list(out.glob('*.zip')))==1
    assert not list(out.glob('*.download_requested.json'))
    sent=[];receipt=recover_progress(run,out,sent.append)
    assert len(sent)==1 and receipt['download_requested']


def test_declared_increment_or_run_end_forces_a_new_cumulative_download(tmp_path):
    batch(tmp_path);run=tmp_path/'runtime_run';out=tmp_path/'snapshots';sent=[]
    first=recover_progress(run,out,sent.append,min_new=2)
    batch(tmp_path,1);batch(tmp_path,2)
    recover_progress(run,out,sent.append,min_new=2,now=first['created_epoch']+1)
    assert len(sent)==2
    batch(tmp_path,3)
    (run/'failure.json').write_text(json.dumps({'status':'stopped'}))
    result=recover_progress(run,out,sent.append,min_new=2,now=first['created_epoch']+2)
    assert len(sent)==3 and result['committed_response_attempts']==4
    assert not result['cohort_completion_certified']


def test_status_counts_and_downloads_a_flat_canonical_journal(tmp_path):
    batch(tmp_path,flat=True);run=tmp_path/'runtime_run';out=tmp_path/'snapshots';sent=[]
    result=recover_progress(run,out,sent.append)
    assert result['committed_response_attempts']==1
    assert len(sent)==1 and sent[0].endswith('native_000001.zip')


def test_flat_generation_summary_forces_the_last_small_checkpoint(tmp_path):
    batch(tmp_path,flat=True);run=tmp_path/'runtime_run';out=tmp_path/'snapshots';sent=[]
    first=recover_progress(run,out,sent.append,min_new=64)
    batch(tmp_path,1,flat=True)
    recover_progress(run,out,sent.append,min_new=64,now=first['created_epoch']+1)
    assert len(sent)==1
    (run/'generation_summary.json').write_text('{}')
    result=recover_progress(run,out,sent.append,min_new=64,now=first['created_epoch']+2)
    assert len(sent)==2 and result['committed_response_attempts']==2
