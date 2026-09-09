"""Inspect official data and freeze grouped development/holdout partitions."""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import hashlib
import importlib.util
import json
import random
import re
from pathlib import Path


def read_records(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def shingles(rec):
    text = "\n".join(d["text"] for d in rec["docs"] if d["type"] == "공고문")
    words = re.findall(r"[가-힣A-Za-z]+", text)
    return {" ".join(words[i:i + 5]) for i in range(len(words) - 4)}


def grouped_split(recs, labels, folds=5):
    parent = list(range(len(recs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    sets = [shingles(r) for r in recs]
    near = []
    for i in range(len(recs)):
        for j in range(i):
            a, b = sets[i], sets[j]
            if not a or not b or min(len(a), len(b)) / max(len(a), len(b)) < .8:
                continue
            intersection = len(a & b)
            jac = intersection / (len(a) + len(b) - intersection)
            if jac >= .8:
                parent[find(i)] = find(j)
                near.append([recs[j]["id"], recs[i]["id"], round(jac, 4)])
    groups = collections.defaultdict(list)
    for i in range(len(recs)):
        groups[find(i)].append(i)
    ys = [[int(labels[r["id"]][f"v{k}"]) for k in range(1, 25)] for r in recs]
    total = [sum(row[k] for row in ys) for k in range(24)]
    # Greedy multilabel stratification assigns the rarest remaining positives first.
    remaining = list(groups.values())
    random.Random(20260907).shuffle(remaining)
    fold_counts = [[0] * 24 for _ in range(folds)]
    fold_sizes = [0] * folds
    assignments = {}
    while remaining:
        remaining_counts = [sum(ys[i][k] for g in remaining for i in g) for k in range(24)]
        nonzero = [k for k, n in enumerate(remaining_counts) if n]
        rare = min(nonzero, key=lambda k: (remaining_counts[k], k)) if nonzero else None
        candidates = [g for g in remaining if rare is None or any(ys[i][rare] for i in g)]
        g = max(candidates, key=lambda g: (sum(sum(ys[i]) for i in g), len(g)))
        gy = [sum(ys[i][k] for i in g) for k in range(24)]
        def cost(f):
            overflow = max(0, fold_sizes[f] + len(g) - len(recs) / folds)
            balance = sum(((fold_counts[f][k] + gy[k]) ** 2 - fold_counts[f][k] ** 2) / max(total[k], 1) for k in range(24))
            return (overflow, fold_counts[f][rare] if rare is not None else fold_sizes[f], balance, fold_sizes[f], f)
        f = min(range(folds), key=cost)
        for i in g:
            assignments[recs[i]["id"]] = f
        fold_sizes[f] += len(g)
        fold_counts[f] = [a + b for a, b in zip(fold_counts[f], gy)]
        remaining.remove(g)
    return assignments, near, fold_counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("data_open"))
    ap.add_argument("--out", type=Path, default=Path("artifacts/audit"))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    recs = read_records(a.root / "dev.jsonl.gz")
    with (a.root / "dev_labels.csv").open(encoding="utf-8", newline="") as f:
        labels = {r["id"]: r for r in csv.DictReader(f)}
    split_path = a.out / "split.json"
    assignment, near, counts = grouped_split(recs, labels)
    payload = {"seed": 20260907, "holdout_fold": 0, "assignments": assignment,
               "near_duplicates": near, "positive_counts_per_fold": counts,
               "source_sha256": hashlib.sha256((a.root / "dev.jsonl.gz").read_bytes()).hexdigest()}
    if split_path.exists() and json.loads(split_path.read_text(encoding="utf-8")) != payload:
        raise RuntimeError("Frozen split changed; do not silently regenerate it")
    split_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, keep in [("development", lambda i: assignment[i] != 0), ("holdout", lambda i: assignment[i] == 0)]:
        with gzip.open(a.out / f"{name}.jsonl.gz", "wt", encoding="utf-8") as f:
            for r in recs:
                if keep(r["id"]):
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        with (a.out / f"{name}_labels.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(next(iter(labels.values()))))
            w.writeheader()
            w.writerows(r for i, r in labels.items() if keep(i))
    spec = importlib.util.spec_from_file_location("official_baseline", a.root / "baseline/script.py")
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    coverage = {f"v{k}": {"positive": 0, "has_evidence": 0, "baseline_visible": 0} for k in range(1, 25)}
    for r in recs:
        context = baseline.build_context(r, 4000)
        for k in range(1, 25):
            label, ev = labels[r["id"]][f"v{k}"], labels[r["id"]][f"e{k}"]
            coverage[f"v{k}"]["positive"] += int(label)
            coverage[f"v{k}"]["has_evidence"] += bool(ev)
            coverage[f"v{k}"]["baseline_visible"] += bool(ev) and ev in context
    lengths = sorted(sum(len(d["text"]) for d in r["docs"]) for r in recs)
    report = {"records": len(recs), "near_duplicate_pairs": near,
              "fold_sizes": dict(collections.Counter(assignment.values())), "positive_counts_per_fold": counts,
              "chars_quantiles": {str(q): lengths[int((len(lengths)-1)*q)] for q in [0, .25, .5, .75, .9, .95, 1]},
              "baseline_evidence_coverage": coverage}
    (a.out / "data_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # Only development examples are exported for prompt/rule authoring.
    lines = []
    for k in range(1, 25):
        lines.append(f"\n## v{k}\n")
        positives = [r for r in recs if assignment[r['id']] != 0 and labels[r['id']][f'v{k}'] == '1']
        for r in positives[:3]:
            ev = labels[r['id']][f'e{k}']
            lines.append(json.dumps({"id": r["id"], "meta": r["meta"], "evidence": ev,
                                     "completeness": r["input_completeness"]}, ensure_ascii=False))
    (a.out / "development_examples.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
