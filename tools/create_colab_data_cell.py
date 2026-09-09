"""Prepare a private Colab transfer cell from the verified official archive.

The generated cell contains competition data. Keep it in the ignored runs/
directory and the participant's private notebook, never in a public repository.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import stat
import sys
import textwrap
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.prepare_data import SHA256


def build(archive):
    with Path(archive).open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != SHA256:
            raise ValueError("Official archive hash does not match")
    manifest, payload = {}, io.BytesIO()
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(payload, "w") as packed:
        for item in original.infolist():
            if item.is_dir() or item.filename == "train_unlabeled.jsonl.gz":
                continue
            name = item.filename
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                raise ValueError("Unsafe official archive member")
            if stat.S_ISLNK(item.external_attr >> 16) or name in manifest:
                raise ValueError("Symlink or duplicate archive member")
            data = original.read(item)
            manifest[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            target = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            target.compress_type = zipfile.ZIP_DEFLATED
            packed.writestr(target, data, compresslevel=9)
    raw = payload.getvalue()
    cell = "#@title 공식 개발 데이터 전송 및 파일별 SHA-256 검증\n"
    cell += "import base64, io, json, hashlib, zipfile\n"
    cell += "TRANSFER_MANIFEST=" + repr(manifest) + "\n"
    cell += "TRANSFER_SHA256=" + repr(hashlib.sha256(raw).hexdigest()) + "\n"
    cell += "OFFICIAL_ARCHIVE_SHA256=" + repr(SHA256) + "\n"
    cell += "TRANSFER_BASE64=(\n" + "\n".join(
        repr(line) for line in textwrap.wrap(base64.b64encode(raw).decode("ascii"), 16000)) + "\n)\n"
    cell += """raw=base64.b64decode(TRANSFER_BASE64, validate=True)
assert hashlib.sha256(raw).hexdigest()==TRANSFER_SHA256
root=WORK/'data_open'
root.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    assert set(z.namelist())==set(TRANSFER_MANIFEST)
    for name,info in TRANSFER_MANIFEST.items():
        target=(root/name).resolve()
        assert target.is_relative_to(root.resolve())
        data=z.read(name)
        assert len(data)==info['bytes'] and hashlib.sha256(data).hexdigest()==info['sha256'], name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
(WORK/'data_archive').mkdir(exist_ok=True)
(WORK/'data_archive'/'transfer_manifest.json').write_text(json.dumps({
    'official_archive_sha256':OFFICIAL_ARCHIVE_SHA256,
    'omitted':['train_unlabeled.jsonl.gz'],'files':TRANSFER_MANIFEST
},ensure_ascii=False,indent=2),encoding='utf-8')
print(f'Verified {len(TRANSFER_MANIFEST)} official files.')
execute([PY,'tools/inspect_data.py'])
del raw, TRANSFER_BASE64
"""
    compile(cell, "official_data_transfer", "exec")
    return cell, {"files": len(manifest), "zip_bytes": len(raw),
                  "zip_sha256": hashlib.sha256(raw).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, default=ROOT / "data_archive/open.zip")
    parser.add_argument("--output", type=Path, default=ROOT / "runs/colab_remote/reproducible_data_cell.py")
    args = parser.parse_args()
    source, report = build(args.archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(source, encoding="utf-8", newline="\n")
    print(json.dumps({"output": str(args.output), **report}))
