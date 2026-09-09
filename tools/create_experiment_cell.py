"""Create a reviewable source-only Colab update/experiment cell, without credentials."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.create_colab_notebook import SOURCES


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--mode", choices=("head", "retrieval"), default="retrieval")
    p.add_argument("--smoke-only", action="store_true")
    p.add_argument("--out", type=Path)
    a = p.parse_args()
    config_path = a.config.resolve()
    config_name = config_path.relative_to(ROOT).as_posix()
    names = list(dict.fromkeys([*SOURCES, config_name]))
    payload = {name: (ROOT / name).read_text(encoding="utf-8") for name in names}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    encoded = base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    digest = hashlib.sha256(raw).hexdigest()
    name = config_path.stem
    extra = ["--config", config_name, "--skip-official", "--development-only", "--modes", a.mode]
    if a.smoke_only:
        extra.append("--smoke-only")
    code = f'''#@title {name} 개발 실험
assert RUNTIME_OK, '런타임 검사가 통과해야 합니다.'
import base64, zlib, shutil, hashlib
raw = zlib.decompress(base64.b64decode({encoded!r}))
assert hashlib.sha256(raw).hexdigest() == {digest!r}
updates = json.loads(raw.decode('utf-8'))
for name, content in updates.items():
    target = WORK / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    assert target.read_text(encoding='utf-8') == content
print('Updated source files:', ', '.join(updates))
os.environ['PATH'] = str(PY.parent) + os.pathsep + os.environ.get('PATH', '')
assert shutil.which('ninja')
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S') + {'_' + name!r}
experiment_dir = WORK / 'runs' / stamp
log_path = WORK / 'runs' / f'{{stamp}}.log'
args = [PY, 'tools/run_experiments.py', '--model-dir', 'models/gemma',
        '--out', experiment_dir, '--max-minutes', '60'] + {extra!r}
try:
    execute(args, log_file=log_path)
finally:
    execute([PY, 'tools/export_results.py'])
    from google.colab import files
    files.download(str(WORK / 'dacon_results.zip'))
'''
    compile(code, f"{name}_cell.py", "exec")
    destination = a.out or ROOT / "runs/colab_remote" / f"{name}_cell.py"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(code, encoding="utf-8", newline="\n")
    manifest = {n: hashlib.sha256(t.encode("utf-8")).hexdigest() for n, t in payload.items()}
    destination.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"cell": str(destination), "files": len(payload), "characters": len(code),
                      "payload_sha256": digest, "smoke_only": a.smoke_only}))


if __name__ == "__main__":
    main()
