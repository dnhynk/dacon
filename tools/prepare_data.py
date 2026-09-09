from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path

URL = "https://cfiles.dacon.co.kr/competitions/236754/open.zip"
SHA256 = "260e3f5e2aecb716ba4282b39a72e70348475b549b2b9701d225d73a5b18a3c1"


def prepare(root=Path("data_open"), archive=Path("data_archive/open.zip")):
    root, archive = Path(root).resolve(), Path(archive)
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        temporary = archive.with_suffix(".zip.part")
        with urllib.request.urlopen(URL, timeout=60) as r, temporary.open("wb") as f:
            while block := r.read(4 * 1024 * 1024):
                f.write(block)
        temporary.replace(archive)
    with archive.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    if digest != SHA256:
        raise ValueError("Official archive hash changed; inspect the new release before using it")
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            if not (root / member.filename).resolve().is_relative_to(root):
                raise ValueError("Unsafe archive path")
            if ((member.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("Symlink in archive")
        z.extractall(root)
    print(json.dumps({"source": URL, "sha256": digest, "directory": str(root)}))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=Path("data_open"))
    p.add_argument("--archive", type=Path, default=Path("data_archive/open.zip"))
    a = p.parse_args()
    prepare(a.root, a.archive)
