"""Request local downloads of cumulative live native evidence at fixed intervals.

The injected download function must use the browser's supported file transfer.
A requested download is never claimed to be an externally verified recovery.
This tool does not launch, resume, change, or stop inference.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from tools.checkpoint_native_run import checkpoint, committed_count


def recover_progress(run, output, download, *, min_new=64, interval=300, now=None):
    run, output = Path(run), Path(output)
    observed = time.time() if now is None else now
    # A call receipt is the native recorder's commit marker.  Both the
    # historical normal/probes tree and the flat canonical journal are valid.
    count = committed_count(run)
    prior = sorted(output.glob('native_*.receipt.json')) if output.exists() else []
    latest = json.loads(prior[-1].read_text(encoding='utf8')) if prior else None
    old_count = latest['committed_response_attempts'] if latest else 0
    if count > old_count and (latest is None or count-old_count >= min_new
                             or observed-latest['created_epoch'] >= interval
                             or (run/'complete.json').exists()
                             or (run/'generation_summary.json').exists()
                             or (run/'failure.json').exists()):
        latest = checkpoint(run, output/f'native_{count:06d}.zip')
    if latest is None:
        return {'checkpoint_available': False, 'committed_response_attempts': count}
    archive = output/latest['file']
    marker = archive.with_suffix('.download_requested.json')
    result = {**latest, 'checkpoint_available': True,
              'download_requested': marker.exists(), 'external_recovery_confirmed': False}
    if not marker.exists():
        # Surface the expected digest and size before triggering transfer.
        print('NATIVE_CHECKPOINT_RECEIPT', json.dumps(result, ensure_ascii=False), flush=True)
        download(str(archive))
        with marker.open('x', encoding='utf8') as stream:
            json.dump({'requested_epoch': observed, 'sha256': latest['sha256'],
                       'external_recovery_confirmed': False}, stream)
        result['download_requested'] = True
    return result
