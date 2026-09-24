"""Package the one canonical runtime, never an experiment or saved prediction.

The archive is the source plus one binary file, the corpus line index. Building
is not a claim of GPU, score, or L40S validation. Existing archives are
immutable; use a new output path for a record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILES = (
    "submission/model/config.json",
    "submission/model/original_a.json",
    "submission/model/v20_legacy.json",
)
# Packaged even while corpus_seen_filter is off, so one config line turns the filter on.
# It is binary, so it is not a text source and the notebook does not embed it.
INDEX_FILE = "submission/model/corpus_line_hashes.u64"
PACKAGE_DIRS = ("submission", "submission/pps", "submission/original_a", "submission/v20_legacy")


def source_files(root=ROOT):
    """Only Python modules in the declared package and three fixed configs."""
    root = Path(root)
    names = {"script.py", "requirements.txt", "submission/requirements.txt", *CONFIG_FILES}
    for directory in PACKAGE_DIRS:
        names.update(p.relative_to(root).as_posix() for p in (root / directory).glob("*.py"))
    names.update(("submission/__init__.py", "submission/main.py"))
    return tuple(sorted(names))


def source_payload(root=ROOT, *, with_index=False):
    root = Path(root).resolve()
    payload = {}
    for name in (*source_files(root), *((INDEX_FILE,) if with_index else ())):
        path = root / name
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError(f"Source must stay in this repository: {name}")
        payload[name] = path.read_bytes()
    for name in CONFIG_FILES:
        if not isinstance(json.loads(payload[name]), dict):
            raise ValueError(f"Expected an object config: {name}")
    return payload


def payload_manifest(content):
    files = {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
             for name, data in sorted(content.items())}
    fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"schema_version": 2, "entrypoint": "script.py",
            "runtime_package": "submission", "source_fingerprint": fingerprint,
            "l40s_runtime_verified": False, "submission_uploaded": False, "files": files}


def build(output, *, root=ROOT):
    output = Path(output)
    receipt = output.with_suffix(".manifest.json")
    if output.exists() or receipt.exists():
        raise FileExistsError(f"Preserve the existing build; use a new output path: {output}")
    content = source_payload(root, with_index=True)
    manifest = payload_manifest(content)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream, zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(content.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 12, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    manifest["archive_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    manifest["archive_bytes"] = output.stat().st_size
    with receipt.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/submission.zip")
    args = parser.parse_args()
    print(json.dumps({"archive": str(args.output), **build(args.output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
