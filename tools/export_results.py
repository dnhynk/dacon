"""Export only experiment results and reviewable source, without model weights."""
from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path


def export(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    files = []
    for folder, patterns in {"pps": ["*.py"], "model": ["*.json"], "tools": ["*.py"],
                             "runs": ["*.json", "*.jsonl", "*.csv", "*.log"],
                             "artifacts": ["*.json", "candidate*.zip"]}.items():
        for pattern in patterns:
            files.extend((root / folder).rglob(pattern))
    for name in ("script.py", "requirements.txt", "requirements-gpu.txt", "requirements-gpu.lock", "README.md"):
        if (root / name).is_file():
            files.append(root / name)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for path in sorted(set(files)):
            if path.resolve().is_relative_to(root) and path.resolve() != output and path.is_file():
                z.write(path, str(path.relative_to(root)).replace("\\", "/"))
    print(json.dumps({"archive": str(output), "bytes": output.stat().st_size}))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--output", type=Path, default=Path("dacon_results.zip"))
    a = p.parse_args()
    export(a.root, a.output)
