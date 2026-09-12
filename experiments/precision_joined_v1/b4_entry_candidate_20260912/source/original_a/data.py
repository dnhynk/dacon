from __future__ import annotations

import csv
import gzip
import json
import os
import unicodedata
from pathlib import Path

ITEMS = tuple(f"v{i}" for i in range(1, 25))
ABSENCE = frozenset({10, 11, 16, 18, 20})
COLUMNS = ["id", *ITEMS, *(f"e{i}" for i in range(1, 25))]


def records(path, limit=None):
    if limit is not None and limit < 1:
        raise ValueError("limit must be a positive integer")
    opener = gzip.open if str(path).endswith(".gz") else open
    seen = set()
    with opener(path, "rt", encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            rec = json.loads(line)
            if not isinstance(rec.get("id"), str) or not rec["id"] or rec["id"] in seen:
                raise ValueError(f"Invalid or duplicate record id at line {n}")
            seen.add(rec["id"])
            if not isinstance(rec.get("meta"), dict) or not isinstance(rec.get("docs"), list):
                raise ValueError(f"Invalid record shape: {rec['id']}")
            for doc in rec["docs"]:
                if not all(isinstance(doc.get(k), str) for k in ("doc_id", "type", "text")):
                    raise ValueError(f"Invalid document in {rec['id']}")
                doc["text"] = unicodedata.normalize("NFC", doc["text"])
            if not any(d["type"] == "공고문" for d in rec["docs"]):
                raise ValueError(f"Missing notice in {rec['id']}")
            yield rec
            if limit is not None and len(seen) >= limit:
                break


class EvidenceUnavailableError(ValueError):
    """A positive judgment lacks a usable citation; it is not a negative label."""

    def __init__(self, record_id, items):
        self.record_id = record_id
        self.items = tuple(items)
        super().__init__(f"{record_id}: positive items need citable source evidence: "
                         + ", ".join(f"v{k}" for k in self.items))


def _evidence_occurrences(value, rec, source):
    if source is not None:
        doc_index, start, end = source
        text = rec["docs"][doc_index]["text"]
        if text[start:end] == value:
            yield text, start
        return
    for doc in rec["docs"]:
        text = doc["text"]
        start = text.find(value)
        while start >= 0:
            yield text, start
            start = text.find(value, start + 1)


def clean_evidence(value, rec, *, source=None):
    """Return a source quote, retaining operators even at an unsafe span start.

    source, when supplied, is the selected (document index, start, end). Never
    borrow context from another occurrence to repair that selected span.
    """
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFC", value)
    if not value.strip():
        return ""
    # Verify the whole proposed quote before truncation, so a source-crossing
    # or fabricated suffix cannot be hidden by the 500-character limit.
    for candidate in dict.fromkeys((value, value.strip())):
        for text, start in _evidence_occurrences(candidate, rec, source):
            if candidate[0] not in "=+@":
                quote = candidate[:500]
                if quote.strip():
                    return quote
                continue
            # Extend left within this document instead of deleting +, = or @.
            # Keep the entire selected span: making room must not cut its tail.
            left = max(0, start - (500 - len(candidate)))
            for lo in range(left, start):
                if text[lo] not in "=+@" and (lo == 0 or text[lo - 1].isspace()):
                    return text[lo:start + len(candidate)]
    return ""


def missing_evidence_items(row, items=range(1, 25)):
    return [k for k in items if k not in ABSENCE
            and row[f"v{k}"] in (1, "1")
            and (not row[f"e{k}"] or not row[f"e{k}"].strip())]


def require_evidence(row, items=range(1, 25)):
    missing = missing_evidence_items(row, items)
    if missing:
        raise EvidenceUnavailableError(row["id"], missing)


def make_row(rec, values, evidence):
    if len(values) != 24 or len(evidence) != 24:
        raise ValueError("Expected exactly 24 predictions and evidence entries")
    row = {"id": rec["id"]}
    for k, (v, ev) in enumerate(zip(values, evidence), 1):
        if type(v) is not int or v not in (0, 1):
            raise ValueError(f"v{k}: label must be the integer 0 or 1")
        row[f"v{k}"] = v
        row[f"e{k}"] = "" if not v or k in ABSENCE else clean_evidence(ev, rec)
    return row


def write_csv(path, rows, *, recs=None, require_positive_evidence=True):
    if type(require_positive_evidence) is not bool:
        raise ValueError("require_positive_evidence must be boolean")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\r\n")
            w.writeheader()
            w.writerows(rows)
        if recs is not None:
            validate_csv(temporary, recs, require_positive_evidence=require_positive_evidence)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            raise ValueError("Expected id,v1..v24,e1..e24 in that order; no BOM")
        rows = list(reader)
    if any(None in row or any(v is None for v in row.values()) for row in rows):
        raise ValueError("CSV rows have inconsistent column counts")
    return rows


def validate_csv(path, recs, *, require_positive_evidence=True):
    if type(require_positive_evidence) is not bool:
        raise ValueError("require_positive_evidence must be boolean")
    rows = read_csv(path)
    by_id = {rec["id"]: rec for rec in recs}
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(by_id):
        raise ValueError("Submission ids must match input ids exactly and be unique")
    for row in rows:
        rec = by_id[row["id"]]
        for k in range(1, 25):
            value, ev = row[f"v{k}"], row[f"e{k}"]
            if value not in ("0", "1"):
                raise ValueError(f"{row['id']} v{k}: invalid label")
            if ev and (value == "0" or k in ABSENCE):
                raise ValueError(f"{row['id']} e{k}: forbidden evidence")
            if len(ev) > 500 or unicodedata.normalize("NFC", ev) != ev:
                raise ValueError(f"{row['id']} e{k}: length/normalization error")
            if ev and (ev[0] in "=+@" or not any(ev in d["text"] for d in rec["docs"])):
                raise ValueError(f"{row['id']} e{k}: not an exact document substring")
        if require_positive_evidence:
            require_evidence(row)
    return rows
