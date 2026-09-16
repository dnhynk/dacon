"""Preserve a browser download after checking an independently observed digest.

This tool never executes recovered code, reads labels, or updates project state.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import zipfile


def recover(source: Path, output: Path, expected_sha256: str, expected_bytes: int):
    if output.exists():
        raise ValueError('Preserve existing evidence; choose a new output directory')
    data = source.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256 or len(data) != expected_bytes:
        raise ValueError('Download does not match the browser-observed SHA256 and size')
    output.mkdir(parents=True, exist_ok=False)
    archive = output / source.name
    with archive.open('xb') as stream:
        stream.write(data)
    recovered = output / 'recovered'
    with zipfile.ZipFile(archive) as stream:
        members = stream.infolist()
        names = [m.filename for m in members]
        if len(names) != len(set(n.casefold() for n in names)):
            raise ValueError('Duplicate archive paths, including Windows case aliases')
        for member in members:
            name = member.filename
            parts = PurePosixPath(name).parts
            if (not parts or PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive
                    or '..' in parts or '\\' in name or ':' in name
                    or any(p.endswith((' ', '.')) for p in parts)
                    or stat.S_ISLNK(member.external_attr >> 16)
                    or not (recovered / name).resolve().is_relative_to(recovered.resolve())):
                raise ValueError('Unsafe archive path: ' + name)
        bad = stream.testzip()
        if bad:
            raise ValueError('Archive CRC failed: ' + bad)
        recovered.mkdir(exist_ok=False)
        stream.extractall(recovered)
    files = {p.relative_to(recovered).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(recovered.rglob('*')) if p.is_file()}
    receipt = {'download': str(source.resolve()), 'archive_sha256': actual,
               'bytes': len(data), 'archive_members': len(names), 'zip_crc': 'PASS',
               'observed_via': 'Official Chrome Colab status cell',
               'recovered_utc': datetime.now(timezone.utc).isoformat(),
               'files_sha256': files, 'labels_read': False, 'recovered_code_executed': False}
    with (output / 'receipt.json').open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    return {k: v for k, v in receipt.items() if k != 'files_sha256'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--bytes', type=int, required=True)
    a = p.parse_args()
    print(json.dumps(recover(a.source, a.output, a.sha256, a.bytes), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
