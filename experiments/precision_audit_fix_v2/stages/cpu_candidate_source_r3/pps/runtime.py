"""Production wall-clock boundary including imports, loading and generation.

Research programs importing VLLMRunner do not start this supervisor. It owns
only the process it launches and that child's descendants. Checkpoints survive
a hard stop; unfinished records are never published as normal zero rows.
"""
from __future__ import annotations
import argparse
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


class WindowsJob:
    def __init__(self, process):
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', w.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', w.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', w.DWORD), ('SchedulingClass', w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('ReadOperationCount', 'WriteOperationCount',
                'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
        class Extended(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', IO),
                        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.CreateJobObjectW.argtypes = (ctypes.c_void_p, w.LPCWSTR)
        api.CreateJobObjectW.restype = w.HANDLE
        api.SetInformationJobObject.argtypes = (w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD)
        api.SetInformationJobObject.restype = w.BOOL
        api.AssignProcessToJobObject.argtypes = (w.HANDLE, w.HANDLE)
        api.AssignProcessToJobObject.restype = w.BOOL
        api.TerminateJobObject.argtypes = (w.HANDLE, w.UINT)
        api.TerminateJobObject.restype = w.BOOL
        api.CloseHandle.argtypes = (w.HANDLE,)
        api.CloseHandle.restype = w.BOOL
        self.api, self.handle = api, api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        try:
            if not api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not api.AssignProcessToJobObject(self.handle, w.HANDLE(int(process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def terminate(self):
        if not self.api.TerminateJobObject(self.handle, 124):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def supervise(command, seconds, *, cwd=None, status_path=None, gated=False, grace_seconds=5):
    if seconds <= 0:
        raise ValueError('A positive production runtime budget is required')
    started = time.monotonic()
    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE if gated else subprocess.DEVNULL, **flags)
    job = None
    timed_out = False
    try:
        if os.name == 'nt':
            job = WindowsJob(process)
        if gated:
            process.stdin.write(b'GO\n')
            process.stdin.close()
        # Reserve a small cleanup interval inside the total hard limit.
        grace = min(grace_seconds, seconds / 5)
        try:
            code = process.wait(timeout=max(.01, seconds - grace - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == 'nt':
                job.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=max(.01, seconds - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                if os.name == 'nt':
                    job.terminate()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            code = 124
    finally:
        # Never leave descendants from our own worker behind, even on errors.
        if job is not None:
            job.close()
        elif os.name != 'nt':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.kill()  # Assignment failed before the gated worker began.
        if process.poll() is None:
            process.wait(timeout=2)
    report = {'exit_code': code, 'timed_out': timed_out, 'elapsed_seconds': time.monotonic() - started,
              'production_budget_seconds': seconds, 'worker_pid': process.pid,
              'unfinished_rows_filled_with_zero': False, 'checkpoints_preserved': True}
    if status_path is not None:
        path = Path(status_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
        temporary.replace(path)
    return report


def main():
    argv = sys.argv[1:]
    if argv[:1] == ['--worker']:
        if sys.stdin.buffer.readline() != b'GO\n':
            raise RuntimeError('Production worker was not released by its parent')
        sys.argv = [sys.argv[0], *argv[1:]]
        from .pipeline import main as worker_main
        worker_main()
        return
    # Parse only the values required to establish the outer process boundary.
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'model/config.json')
    parser.add_argument('--output-dir', default=os.environ.get('PPS_OUTPUT_DIR'))
    args, _ = parser.parse_known_args(argv)
    config = json.loads(args.config.read_text(encoding='utf-8'))
    budget = config.get('total_runtime_seconds', 7200)
    status = Path(args.output_dir) / 'runtime_status.json' if args.output_dir else None
    entry = Path(__file__).resolve().parents[1] / 'script.py'
    report = supervise([sys.executable, '-B', str(entry), '--worker', *argv], budget,
        status_path=status, gated=True)
    raise SystemExit(report['exit_code'])


if __name__ == '__main__':
    main()
