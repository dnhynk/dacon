"""Build the submission zip from the canonical runtime: script.py, requirements.txt and the pps_c package.

    python tools/build_submission.py <name>        # writes artifacts/rebuild_c/<name>/submit.zip and prints its sha256

The zip is then checked by extracting it to a scratch directory and running `script.py --mode mock` on the
organizer's 10-record sample, so the archive layout and imports are exercised exactly as the server runs them.
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'submission'
name = sys.argv[1]
out = ROOT / 'artifacts/rebuild_c' / name / 'submit.zip'
out.parent.mkdir(parents=True, exist_ok=True)
req = '# The evaluation image provides every runtime dependency; do not override vllm, torch, transformers or xgrammar.\n'
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    z.write(SRC / 'script.py', 'script.py')
    z.writestr('requirements.txt', req)
    for p in sorted((SRC / 'pps_c').rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
            z.write(p, p.relative_to(SRC).as_posix())
digest = hashlib.sha256(out.read_bytes()).hexdigest()
with tempfile.TemporaryDirectory() as tmp:
    zipfile.ZipFile(out).extractall(tmp)
    os.makedirs(Path(tmp) / 'data', exist_ok=True)
    env = dict(os.environ, PPS_DATA_DIR=str(ROOT / 'data_open/data'), PPS_OUTPUT_DIR=str(Path(tmp) / 'output'),
               PYTHONIOENCODING='utf-8')
    r = subprocess.run([sys.executable, 'script.py', '--mode', 'mock'], cwd=tmp, env=env, capture_output=True, text=True,
                       encoding='utf-8')
    ok = r.returncode == 0 and (Path(tmp) / 'output/submission.csv').exists()
    print(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else '')
print({'zip': str(out), 'sha256': digest, 'bytes': out.stat().st_size, 'mock_run_from_zip': 'PASS' if ok else 'FAIL'})
