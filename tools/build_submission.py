"""Build the submission zip from the canonical runtime: script.py, requirements.txt and the pps_c package.

    python tools/build_submission.py <name> [NAME=VALUE ...]   # writes artifacts/rebuild_c/<name>/submit.zip, prints sha256

NAME=VALUE builds a probe package: that assignment in pps_c/switches.py is replaced (it must appear exactly once), so the
probe differs from the canonical package only in switches.py.

The zip is then checked by extracting it to a scratch directory and running `script.py --mode mock` on the
organizer's 10-record sample, so the archive layout and imports are exercised exactly as the server runs them.
"""
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'submission'
name, sets = sys.argv[1], sys.argv[2:]
out = ROOT / 'artifacts/rebuild_c' / name / 'submit.zip'
if not out.resolve().is_relative_to((ROOT / 'artifacts/rebuild_c').resolve()):
    raise SystemExit('Build name must stay under artifacts/rebuild_c.')
if out.exists():
    raise SystemExit(f'Refusing to overwrite preserved archive: {out}. Use a fresh build name.')
# Organizer-provided data stays out of git; the package carries this local copy as the catalog fallback.
if not (SRC / 'pps_c/assets/catalog.csv').is_file():
    raise SystemExit('Missing submission/pps_c/assets/catalog.csv: copy data_open/data/법령패키지/중기부고시/'
                     '중기부고시_경쟁제품_세부품명.csv there before building.')
out.parent.mkdir(parents=True, exist_ok=True)


def switches_bytes():
    b = (SRC / 'pps_c/switches.py').read_bytes()
    for kv in sets:
        k, v = kv.split('=', 1)
        pat = re.compile(rb'^' + re.escape(k.encode()) + rb' = [^\r\n]*', re.M)
        assert len(pat.findall(b)) == 1, f'{k}: expected exactly one assignment in switches.py'
        b = pat.sub(lambda m: f'{k} = {v}'.encode(), b)
    return b


req = '# The evaluation image provides every runtime dependency; do not override vllm, torch, transformers or xgrammar.\n'
with zipfile.ZipFile(out, 'x', zipfile.ZIP_DEFLATED) as z:
    z.write(SRC / 'script.py', 'script.py')
    z.writestr('requirements.txt', req)
    for p in sorted((SRC / 'pps_c').rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
            if sets and p.name == 'switches.py':
                z.writestr(p.relative_to(SRC).as_posix(), switches_bytes())
            else:
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
raise SystemExit(0 if ok else 1)
