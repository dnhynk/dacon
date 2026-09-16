"""Create cumulative native-evidence checkpoints for a live runtime journal.

Only atomically committed request/native/response batches are copied. The
checkpoint never claims cohort completion, runs a model, resumes a process,
selects a different answer, or reads labels. Completed checkpoint ZIPs should
be downloaded while the remote runtime is alive; remote-only copies cannot
survive the loss of an ephemeral machine.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import time
import zipfile


_CALL = re.compile(r'call_\d{5,}_\d+$')
_STATIC = ('started.json', 'source_manifest.json', 'engine.json', 'environment.json',
           'current_inputs.jsonl.gz', 'current_packets.jsonl.gz', 'call_plan.json',
           # ``run_frozen_canonical`` writes one journal directly at the run
           # root.  Its immutable inputs live in the separately sealed
           # preparation, but these receipts are still needed to verify the
           # engine and the exact generation contract after recovery.
           'input_freeze.json', 'generation_preflight.json',
           'generation_grammar_preflight.json',
           'generation_progress_preflight.json', 'execution_policy.json',
           'native_toolchain.json', 'skipped_requests.json',
           'response_errors.json', 'generation_summary.json',
           'resolved_responses.jsonl.gz', 'resolved_responses_partial.jsonl.gz',
           'engine_shutdown.json')


def journal_folders(run):
    """Return every supported journal layout without guessing completion.

    Historical comparison runners use ``normal/`` and ``probes/``.  The
    canonical frozen whole-cohort runner intentionally has one flat journal.
    A root is considered flat only when it has a native-call artifact, so an
    ordinary nested run is not duplicated under a second archive prefix.
    """
    run = Path(run)
    folders = [(name, run / name) for name in ('normal', 'probes')
               if (run / name).is_dir()]
    if any(run.glob('call_*')):
        folders.append(('root', run))
    return folders


def committed_count(run):
    """Count atomically committed response attempts in any supported layout."""
    return sum(json.loads(marker.read_text(encoding='utf8'))['count']
               for _, folder in journal_folders(run)
               for marker in folder.glob('call_*.json')
               if _CALL.fullmatch(marker.stem))


def _read(path, root):
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Checkpoint source must be a regular file within the run')
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(data) != after.st_size:
        raise ValueError('Source changed during checkpoint read')
    if path.suffix == '.gz':
        for line in gzip.decompress(data).splitlines():
            json.loads(line)
    elif path.suffix == '.json':
        json.loads(data)
    return data


def checkpoint(run, output):
    run, output = Path(run).resolve(), Path(output).absolute()
    receipt_path = output.with_suffix('.receipt.json')
    if output.exists() or receipt_path.exists() or output.with_suffix('.partial').exists():
        raise ValueError('Checkpoint output already exists; use a fresh path')
    payload = {}
    batches = []
    incomplete = []
    folders = journal_folders(run)
    for phase, folder in folders:
        for name in _STATIC:
            path = folder / name
            if path.is_file():
                payload[f'{phase}/{name}'] = _read(path, run)
        # The per-call .json receipt is committed after native and projected
        # responses. Later calls have different names and cannot alter them.
        for marker in sorted(folder.glob('call_*.json')):
            if not _CALL.fullmatch(marker.stem):
                continue
            names = [marker.name, *(marker.stem+suffix for suffix in (
                '_requests.jsonl.gz', '_native.jsonl.gz', '_responses.jsonl.gz', '_sampling.json'))]
            if not all((folder/name).is_file() for name in names):
                raise ValueError('Committed native batch is missing a sidecar: '+marker.stem)
            data = {f'{phase}/{name}': _read(folder/name, run) for name in names}
            meta = json.loads(data[f'{phase}/{marker.name}'])
            parsed = {suffix: [json.loads(line) for line in gzip.decompress(
                data[f'{phase}/{marker.stem}_{suffix}.jsonl.gz']).splitlines()]
                for suffix in ('requests', 'native', 'responses')}
            keys = [row['request_key'] for row in parsed['requests']]
            if (len(set(keys)) != len(keys) or len(keys) != meta['count']
                    or any([r['request_key'] for r in parsed[kind]] != keys
                           for kind in ('native', 'responses'))):
                raise ValueError('Committed batch identities are inconsistent')
            batches.append({'phase':phase,'name':marker.stem,'count':len(keys),
                            'attempt':meta['attempt'],'batch':meta['batch']})
            payload.update(data)
        committed = {entry['name'] for entry in batches if entry['phase']==phase}
        # A crashed/unfinished attempt may already have native data. Preserve
        # it explicitly as uncommitted evidence; never count it as resolved.
        for native in sorted(folder.glob('call_*_native.jsonl.gz')):
            prefix = native.name.removesuffix('_native.jsonl.gz')
            if prefix not in committed:
                payload[f'{phase}/{native.name}'] = _read(native, run)
                for suffix in ('_requests.jsonl.gz','_sampling.json'):
                    path = folder/(prefix+suffix)
                    if path.is_file():
                        payload[f'{phase}/{path.name}'] = _read(path, run)
                incomplete.append(f'{phase}/{prefix}')
        for name in ('progress.json','failure.json'):
            path = folder/name
            if path.is_file():
                payload[f'observed/{phase}/{name}'] = _read(path, run)
    if not batches and not incomplete:
        raise ValueError('No native evidence is available to checkpoint')
    manifest = {'kind':'partial_native_evidence_checkpoint','created_epoch':time.time(),
        'batches':batches,'committed_response_attempts':sum(b['count'] for b in batches),
        'uncommitted_native_batches':incomplete,
        'files':{name:hashlib.sha256(data).hexdigest() for name,data in payload.items()},
        'cohort_completion_certified':False,'quality_selection_performed':False,
        'external_recovery_confirmed':False,'labels_read':False,'new_model_calls':0}
    payload['checkpoint_manifest.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf8')
    output.parent.mkdir(parents=True,exist_ok=True)
    partial=output.with_suffix('.partial')
    with partial.open('xb') as stream, zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in sorted(payload.items()):
            archive.writestr(name,data)
    partial.rename(output)
    receipt={'file':output.name,'bytes':output.stat().st_size,
        'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
        'committed_response_attempts':manifest['committed_response_attempts'],
        'uncommitted_native_batches':incomplete,'cohort_completion_certified':False,
        'external_recovery_confirmed':False,'created_epoch':manifest['created_epoch']}
    with receipt_path.open('x',encoding='utf8') as stream:
        json.dump(receipt,stream,ensure_ascii=False,indent=2)
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(checkpoint(args.run,args.output),ensure_ascii=False,indent=2))
