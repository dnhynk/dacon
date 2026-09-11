"""Durable notice-local completed responses, bound to exact inputs and code."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


class Checkpoint:
    def __init__(self, directory, identity, *, resume=False):
        self.path = Path(directory)
        self.path.mkdir(parents=True, exist_ok=True)
        self.identity = digest(identity)
        manifest = self.path / 'manifest.json'
        journal = self.path / 'responses.jsonl'
        self.entries = {}
        if manifest.exists():
            if not resume:
                raise ValueError('Checkpoint already exists; choose a new output directory or explicitly resume')
            saved = json.loads(manifest.read_text(encoding='utf-8'))
            if saved['identity_sha256'] != self.identity:
                raise ValueError('Checkpoint input/config/source/model identity mismatch')
            if journal.exists():
                raw = journal.read_bytes()
                lines = raw.splitlines(keepends=True)
                good_bytes = 0
                for index, line in enumerate(lines):
                    # A process kill may interrupt only the final append.
                    # Remove that incomplete suffix before the next append.
                    if not line.endswith(b'\n') and index == len(lines)-1:
                        break
                    entry = json.loads(line)
                    if digest(entry['payload']) != entry['sha256']:
                        raise ValueError('Checkpoint response integrity mismatch')
                    self.entries[entry['key']] = entry['payload']
                    good_bytes += len(line)
                if good_bytes != len(raw):
                    with journal.open('r+b') as f:
                        f.truncate(good_bytes)
        else:
            if journal.exists():
                raise ValueError('Response journal has no identity manifest')
            manifest.write_text(json.dumps({'identity_sha256': self.identity, 'identity': identity},
                                ensure_ascii=False, indent=2), encoding='utf-8')
        self.file = journal.open('ab')

    @staticmethod
    def key(record, items, prompt):
        return digest({'record': record, 'items': list(items), 'messages': prompt['messages']})

    def get(self, key):
        return self.entries.get(key)

    def put(self, key, payload):
        line = json.dumps({'key': key, 'payload': payload, 'sha256': digest(payload)},
                          ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
        self.file.write(line)
        self.file.flush()
        os.fsync(self.file.fileno())
        self.entries[key] = payload

    def close(self):
        self.file.close()
