"""Build submission/model/corpus_line_hashes.u64 from the provided unlabeled corpus.

The file is the line index read by submission/pps/corpus_lines.py: sorted distinct
little-endian uint64 hashes of every normalized corpus line of at least
MIN_LINE_CHARS characters. The same corpus always yields the same bytes. CPU
only; no labels are read. The receipt is printed as JSON.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.corpus_lines import INDEX_PATH, MIN_LINE_CHARS, line_digest, normalize


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def build(corpus, output):
    import numpy as np
    corpus, output = Path(corpus), Path(output)
    digests, records = set(), 0
    with gzip.open(corpus, 'rt', encoding='utf-8') as handle:
        for row in handle:
            if not row.strip():
                continue
            records += 1
            for doc in json.loads(row).get('docs', []):
                for raw in doc.get('text', '').splitlines():
                    line = normalize(raw)
                    if len(line) >= MIN_LINE_CHARS:
                        digests.add(line_digest(line))
    if not digests:
        raise ValueError('The corpus holds no indexable line')
    index = np.unique(np.frombuffer(b''.join(digests), dtype='>u8').astype(np.uint64)).astype('<u8')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + '.partial')
    index.tofile(temporary)
    temporary.replace(output)
    return {'corpus': str(corpus), 'corpus_sha256': sha256(corpus), 'records': records,
            'hashes': int(len(index)), 'output': str(output), 'output_bytes': output.stat().st_size,
            'output_sha256': sha256(output)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=ROOT / 'data_open/train_unlabeled.jsonl.gz')
    parser.add_argument('--output', type=Path, default=INDEX_PATH)
    args = parser.parse_args(argv)
    print(json.dumps(build(args.corpus, args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
