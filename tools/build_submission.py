"""Create a small source-only candidate archive; never include data, secrets or weights."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pps.prompts import Config

FILES = ("script.py", "requirements.txt", "pps/__init__.py", "pps/data.py", "pps/knowledge.py",
         "pps/retrieval.py", "pps/prompts.py", "pps/pipeline.py", "pps/rubrics.py", "pps/rules.py", "pps/products.py",
         "pps/temporal.py", "pps/performance.py", "pps/sme.py", "pps/other_checks.py",
         "pps/legal_context.py", "pps/qualification.py", "pps/comparison.py")


def build(output, config=None, root=ROOT):
    root, output = Path(root), Path(output)
    content = {name: (root / name).read_bytes() for name in FILES}
    if config is None:
        config = json.loads((root / "model/config.json").read_text(encoding="utf-8"))
    Config(**config)
    content["model/config.json"] = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")
    manifest = {"l40s_runtime_verified": False, "submission_uploaded": False,
                "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in content.items()}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(content.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 8, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    manifest["archive_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    manifest["archive_bytes"] = output.stat().st_size
    output.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--experiment-dir", type=Path)
    p.add_argument("--output", type=Path, default=Path("artifacts/candidate_unverified_l40s.zip"))
    a = p.parse_args()
    config = None
    if a.experiment_dir:
        selection = json.loads((a.experiment_dir / "selection.json").read_text(encoding="utf-8"))
        completed = json.loads((a.experiment_dir / "completed.json").read_text(encoding="utf-8"))
        environment = json.loads((a.experiment_dir / "environment.json").read_text(encoding="utf-8"))
        if selection["selected"] != completed["selected"] or selection["config"] is None:
            raise ValueError("The official prompt won. Review those results before packaging a revised candidate.")
        for name, expected in environment["source_sha256"].items():
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
                raise ValueError(f"Source changed since GPU evaluation: {name}")
        config = selection["config"]
    result = build(a.output, config)
    print(json.dumps({"archive": str(a.output), **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
