"""Corpus-seen evidence filter (config switch ``corpus_seen_filter``, default off).

A positive cell is cleared when every source line its quote overlaps also occurs
in the provided unlabeled corpus. The line index is built from that corpus by
tools/build_corpus_line_index.py and ships as model/corpus_line_hashes.u64:
sorted distinct little-endian uint64 values, each the big-endian integer of the
8-byte BLAKE2b digest of a normalized line.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

from .data import ABSENCE

INDEX_PATH = Path(__file__).resolve().parents[1] / 'model/corpus_line_hashes.u64'
MIN_LINE_CHARS = 12
# Absence items carry no quote; a v24 positive made by editing meta quotes an untouched line.
# v5, v13, v14 and v15 positives are often made by moving the amount or narrowing the size
# clause, so their quote is untouched corpus boilerplate too (official dev v5 2/7, v13 1/6 hits).
EXCLUDED_ITEMS = ABSENCE | {24} | {5, 13, 14, 15}
_SPACE = re.compile(r'\s+')


def normalize(raw):
    """NFKC without any whitespace; digits are kept as written."""
    return _SPACE.sub('', unicodedata.normalize('NFKC', raw))


def line_digest(line):
    return hashlib.blake2b(line.encode('utf-8'), digest_size=8).digest()


def load(path=None):
    """The sorted index; a missing, empty, truncated or unsorted file is an error."""
    import numpy as np
    path = Path(INDEX_PATH if path is None else path)
    index = np.fromfile(path, dtype='<u8')
    if not len(index) or path.stat().st_size != index.nbytes or not bool((index[1:] > index[:-1]).all()):
        raise ValueError('Corpus line index must hold sorted distinct uint64 hashes')
    return index


def load_for(pipeline):
    """The index when the pipeline's config turns the filter on, else None."""
    return load() if getattr(getattr(pipeline, 'config', None), 'corpus_seen_filter', False) else None


def quoted_lines(record, quote):
    """Normalized lines of at least MIN_LINE_CHARS that the quote overlaps in the first document containing it."""
    for doc in record['docs']:
        text = doc['text']
        at = text.find(quote)
        if at < 0:
            continue
        start = text.rfind('\n', 0, at) + 1
        end = text.find('\n', at + len(quote))
        lines = (normalize(raw) for raw in text[start:len(text) if end < 0 else end].splitlines())
        return [line for line in lines if len(line) >= MIN_LINE_CHARS]
    return []


def seen(index, lines):
    """Whether every normalized line is in the sorted index."""
    import numpy as np
    if not len(index):
        return False
    wanted = np.frombuffer(b''.join(map(line_digest, lines)), dtype='>u8').astype(np.uint64)
    at = np.minimum(np.searchsorted(index, wanted), len(index) - 1)
    return bool((index[at] == wanted).all())


def apply(record, row, index):
    """Clear each quoted positive whose overlapped lines are all in the index; return the cleared item numbers.

    A quote that cannot be located, or that overlaps only lines shorter than
    MIN_LINE_CHARS, leaves its cell unchanged.
    """
    dropped = []
    for k in range(1, 25):
        if k in EXCLUDED_ITEMS or row[f'v{k}'] != 1 or not row[f'e{k}']:
            continue
        lines = quoted_lines(record, row[f'e{k}'])
        if lines and seen(index, lines):
            row[f'v{k}'], row[f'e{k}'] = 0, ''
            dropped.append(k)
    return dropped
