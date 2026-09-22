"""Mine how organizer dev positives differ from their nearest unlabeled-pool sibling.

Read-only, zero model calls.  Each dev notice is matched to the pool notice that
shares most of its rare normalized lines; the 공고문 texts are then aligned line
by line and every changed block is classified (numeric-only, anonymization-token
only, insert, delete, replace).  Official positive cells are linked to the block
holding their evidence.  The output is an analysis aid for building planted
notices; a sibling is a nearest neighbour, not a proven original, so amount/date
differences may be natural family variation rather than organizer edits.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import gzip
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEV_INPUT = ROOT / "data_open" / "dev.jsonl.gz"
DEV_LABELS = ROOT / "data_open" / "dev_labels.csv"
POOL_INPUT = ROOT / "data_open" / "train_unlabeled.jsonl.gz"
SCHEMA_VERSION = "dacon.independent.cell_edit_mining.v1"
ITEMS = tuple(f"v{number}" for number in range(1, 25))
MIN_LINE_CHARS = 25
MAX_DEV_DOCUMENT_FREQUENCY = 3
MAX_REPORT_LINE_CHARS = 220

_SPACE_RE = re.compile(r"\s+")
_DIGIT_RE = re.compile(r"\d+")
_TOKEN_RE = re.compile(r"\[[^\[\]\n]{1,120}\]")
_MARKER_RE = re.compile(r"^(?:[가-하]\.|\d{1,2}[\.\)]|[①-⑳]|[○ㅇ◦❍•·□■▪\-\*])")
_PAIR_SIMILARITY = 0.6
_MAX_ALIGN_CELLS = 4_000


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _new_bytes(path: pathlib.Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(body)


def _squash(text: str) -> str:
    return _SPACE_RE.sub("", text)


def normalized_lines(record: Mapping[str, Any]) -> set[str]:
    lines = set()
    for document in record["docs"]:
        for line in document["text"].split("\n"):
            squashed = _squash(line)
            if len(squashed) >= MIN_LINE_CHARS:
                lines.add(_DIGIT_RE.sub("#", squashed))
    return lines


def _records(path: pathlib.Path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def find_siblings(
    dev_records: Mapping[str, Mapping[str, Any]], pool_path: pathlib.Path,
) -> dict[str, dict[str, Any]]:
    """Best pool neighbour per dev notice by containment of its rare lines."""

    dev_lines = {record_id: normalized_lines(record) for record_id, record in dev_records.items()}
    frequency = Counter(line for lines in dev_lines.values() for line in lines)
    index: dict[str, list[str]] = defaultdict(list)
    rare_total: Counter[str] = Counter()
    for record_id, lines in dev_lines.items():
        for line in lines:
            if frequency[line] <= MAX_DEV_DOCUMENT_FREQUENCY:
                index[line].append(record_id)
                rare_total[record_id] += 1
    best: dict[str, tuple[int, str | None]] = {record_id: (0, None) for record_id in dev_records}
    for pool_record in _records(pool_path):
        shared: Counter[str] = Counter()
        for line in normalized_lines(pool_record):
            for record_id in index.get(line, ()):
                shared[record_id] += 1
        for record_id, count in shared.items():
            if count > best[record_id][0]:
                best[record_id] = (count, pool_record["id"])
    return {
        record_id: {
            "pool_id": pool_id, "shared_rare_lines": count,
            "rare_lines": rare_total[record_id],
            "containment": count / rare_total[record_id] if rare_total[record_id] else 0.0,
        }
        for record_id, (count, pool_id) in best.items()
    }


def _notice_lines(record: Mapping[str, Any]) -> list[str]:
    return [
        line.strip()
        for document in record["docs"] if document.get("type") == "공고문"
        for line in document["text"].split("\n") if line.strip()
    ]


def classify_block(removed: Sequence[str], added: Sequence[str]) -> str:
    if removed and added:
        base = _squash("".join(removed))
        edited = _squash("".join(added))
        if _TOKEN_RE.sub("", base) == _TOKEN_RE.sub("", edited):
            return "token_only"
        if _DIGIT_RE.sub("#", base) == _DIGIT_RE.sub("#", edited):
            return "numeric_only"
        if _DIGIT_RE.sub("#", _TOKEN_RE.sub("", base)) == _DIGIT_RE.sub("#", _TOKEN_RE.sub("", edited)):
            return "numeric_token_only"
        if _MARKER_RE.sub("", base, count=1) == _MARKER_RE.sub("", edited, count=1):
            return "marker_only"
        return "replace"
    return "delete" if removed else "insert"


def align_block(removed: Sequence[str], added: Sequence[str]) -> list[tuple[list[str], list[str]]]:
    """Split one changed run into order-preserving line pairs, deletions and insertions.

    difflib reports consecutive changed lines as a single replace run, which would
    hide a deleted clause next to a perturbed amount.  Lines are paired only when
    similar enough; everything unpaired is a pure deletion or insertion.
    """

    if not removed or not added or len(removed) * len(added) > _MAX_ALIGN_CELLS:
        return [(list(removed), list(added))]
    similarity = [
        [difflib.SequenceMatcher(None, old, new, autojunk=False).ratio() for new in added]
        for old in removed
    ]
    rows, columns = len(removed), len(added)
    score = [[0.0] * (columns + 1) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        for j in range(1, columns + 1):
            best = max(score[i - 1][j], score[i][j - 1])
            if similarity[i - 1][j - 1] >= _PAIR_SIMILARITY:
                best = max(best, score[i - 1][j - 1] + similarity[i - 1][j - 1])
            score[i][j] = best
    pieces: list[tuple[list[str], list[str]]] = []
    i, j = rows, columns
    while i > 0 or j > 0:
        if (i > 0 and j > 0 and similarity[i - 1][j - 1] >= _PAIR_SIMILARITY
                and score[i][j] == score[i - 1][j - 1] + similarity[i - 1][j - 1]):
            pieces.append(([removed[i - 1]], [added[j - 1]]))
            i, j = i - 1, j - 1
        elif j > 0 and (i == 0 or score[i][j] == score[i][j - 1]):
            pieces.append(([], [added[j - 1]]))
            j -= 1
        else:
            pieces.append(([removed[i - 1]], []))
            i -= 1
    pieces.reverse()
    merged: list[tuple[list[str], list[str]]] = []
    for old, new in pieces:
        if merged and bool(old) != bool(new) and bool(merged[-1][0]) == bool(old) and bool(merged[-1][1]) == bool(new):
            merged[-1][0].extend(old)
            merged[-1][1].extend(new)
        else:
            merged.append((list(old), list(new)))
    return merged


def _changed_fragments(removed: str, added: str) -> list[dict[str, str]]:
    matcher = difflib.SequenceMatcher(None, removed, added, autojunk=False)
    return [
        {"from": removed[i1:i2], "to": added[j1:j2]}
        for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"
    ]


def diff_pair(base: Mapping[str, Any], edited: Mapping[str, Any]) -> dict[str, Any]:
    base_lines = _notice_lines(base)
    edited_lines = _notice_lines(edited)
    matcher = difflib.SequenceMatcher(None, base_lines, edited_lines, autojunk=False)
    blocks = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        for removed, added in align_block(base_lines[i1:i2], edited_lines[j1:j2]):
            block: dict[str, Any] = {
                "kind": classify_block(removed, added), "removed": removed, "added": added,
            }
            if block["kind"] == "replace" and len(removed) == 1 and len(added) == 1:
                block["fragments"] = _changed_fragments(removed[0], added[0])
            blocks.append(block)
    base_meta = base.get("meta", {})
    edited_meta = edited.get("meta", {})
    return {
        "notice_line_ratio": matcher.ratio(),
        "blocks": blocks,
        "block_kinds": dict(Counter(block["kind"] for block in blocks)),
        "meta_changes": {
            key: [base_meta.get(key), edited_meta.get(key)]
            for key in sorted(set(base_meta) | set(edited_meta))
            if base_meta.get(key) != edited_meta.get(key)
        },
        "doc_types": {
            "base": [document.get("type") for document in base["docs"]],
            "edited": [document.get("type") for document in edited["docs"]],
        },
    }


def link_evidence(label_row: Mapping[str, str], blocks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """For each official positive, the changed block (if any) that holds its evidence."""

    links: dict[str, Any] = {}
    for item in ITEMS:
        if label_row[item] != "1":
            continue
        evidence = _squash(label_row["e" + item[1:]])
        link: dict[str, Any] = {"has_evidence": bool(evidence), "block": None, "block_kind": None}
        if evidence:
            for number, block in enumerate(blocks):
                added = _squash("".join(block["added"]))
                if evidence in added or (added and added in evidence):
                    link.update({"block": number, "block_kind": block["kind"]})
                    break
        links[item] = link
    return links


def mine(*, min_containment: float) -> dict[str, Any]:
    dev_records = {record["id"]: record for record in _records(DEV_INPUT)}
    with DEV_LABELS.open(encoding="utf-8", newline="") as handle:
        labels = {row["id"]: row for row in csv.DictReader(handle)}
    siblings = find_siblings(dev_records, POOL_INPUT)
    wanted = {
        match["pool_id"] for match in siblings.values()
        if match["pool_id"] is not None and match["containment"] >= min_containment
    }
    pool = {record["id"]: record for record in _records(POOL_INPUT) if record["id"] in wanted}
    rows = []
    for record_id, match in siblings.items():
        positives = [item for item in ITEMS if labels[record_id][item] == "1"]
        row: dict[str, Any] = {"dev_id": record_id, "positives": positives, **match}
        if match["pool_id"] in pool and match["containment"] >= min_containment:
            row["diff"] = diff_pair(pool[match["pool_id"]], dev_records[record_id])
            row["evidence_links"] = link_evidence(labels[record_id], row["diff"]["blocks"])
        rows.append(row)
    rows.sort(key=lambda row: (-row["containment"], row["dev_id"]))
    return {"schema_version": SCHEMA_VERSION, "min_containment": min_containment, "rows": rows}


def _clip(text: str) -> str:
    return text if len(text) <= MAX_REPORT_LINE_CHARS else text[:MAX_REPORT_LINE_CHARS] + "…"


def report_markdown(result: Mapping[str, Any], *, positives_only: bool) -> str:
    parts = ["# Dev notice vs nearest pool sibling: changed 공고문 blocks", ""]
    for row in result["rows"]:
        if "diff" not in row or (positives_only and not row["positives"]):
            continue
        diff = row["diff"]
        parts.append(
            f"## {row['dev_id']} vs {row['pool_id']} | containment {row['containment']:.2f} "
            f"| positives {','.join(row['positives']) or '-'}"
        )
        parts.append(f"- block kinds: {diff['block_kinds']}; docs {diff['doc_types']}")
        parts.append(f"- meta changes: {canonical_json(diff['meta_changes'])}")
        parts.append(f"- evidence links: {canonical_json(row['evidence_links'])}")
        for number, block in enumerate(diff["blocks"]):
            if block["kind"] in {"numeric_only", "token_only", "numeric_token_only", "marker_only"}:
                continue
            parts.append(f"- [{number}] {block['kind']}")
            if "fragments" in block:
                parts.append(f"    fragments: {_clip(canonical_json(block['fragments']))}")
            for line in block["removed"][:4]:
                parts.append(f"    - {_clip(line)}")
            for line in block["added"][:4]:
                parts.append(f"    + {_clip(line)}")
        parts.append("")
    return "\n".join(parts)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--min-containment", type=float, default=0.5)
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    if output.exists() or ROOT / "runs" not in output.parents:
        print("mining output must be fresh under repository runs/", file=sys.stderr)
        return 2
    result = mine(min_containment=args.min_containment)
    body = "".join(canonical_json(row) + "\n" for row in result["rows"])
    _new_bytes(output / "pairs.jsonl", body.encode("utf-8"))
    _new_bytes(output / "positives.md", report_markdown(result, positives_only=True).encode("utf-8"))
    _new_bytes(output / "all.md", report_markdown(result, positives_only=False).encode("utf-8"))
    paired = [row for row in result["rows"] if "diff" in row]
    print(json.dumps({
        "dev_records": len(result["rows"]), "paired": len(paired),
        "paired_positive_notices": sum(bool(row["positives"]) for row in paired),
        "paired_negative_notices": sum(not row["positives"] for row in paired),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
