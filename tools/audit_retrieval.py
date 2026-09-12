"""RETIRED root-pps retrieval audit; not the canonical A/L input producers."""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if __name__ == "__main__":
    from tools.legacy_entry import retired_main
    retired_main(Path(__file__).name)

from pps.data import records, read_csv
from pps.knowledge import Knowledge
from pps.prompts import Config, build_prompt


def visible(evidence, rec, ranges):
    if not evidence:
        return False
    for i, doc in enumerate(rec["docs"]):
        start = doc["text"].find(evidence)
        while start >= 0:
            if any(lo <= start and start + len(evidence) <= hi for lo, hi in ranges.get(i, [])):
                return True
            start = doc["text"].find(evidence, start + 1)
    return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", default="artifacts/audit/development.jsonl.gz")
    p.add_argument("--labels", default="artifacts/audit/development_labels.csv")
    p.add_argument("--data-dir", default="data_open/data")
    p.add_argument("--tokenizer-dir", default="models/gemma-tokenizer")
    p.add_argument("--out", type=Path, default=Path("artifacts/audit/retrieval_report.json"))
    a = p.parse_args()
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(a.tokenizer_dir, local_files_only=True)
    recs = list(records(a.input))
    labels = {r["id"]: r for r in read_csv(a.labels)}
    knowledge = Knowledge(a.data_dir)
    report = {}
    for mode, budget in [("head", 4000), ("retrieval", 4000), ("head", 14000), ("retrieval", 14000)]:
        config = dataclasses.replace(Config(), mode=mode, document_chars=budget)
        counts = {f"v{k}": {"evidence_examples": 0, "visible": 0} for k in range(1, 25)}
        token_counts, source_coverage = [], []
        for rec in recs:
            prompt = build_prompt(rec, knowledge, config, tokenizer)
            token_counts.append(len(prompt["token_ids"]))
            source_coverage.append(prompt["coverage"]["fraction"])
            for k in range(1, 25):
                ev = labels[rec["id"]][f"e{k}"]
                if ev:
                    counts[f"v{k}"]["evidence_examples"] += 1
                    counts[f"v{k}"]["visible"] += visible(ev, rec, prompt["coverage"]["ranges"])
        total = sum(c["evidence_examples"] for c in counts.values())
        covered = sum(c["visible"] for c in counts.values())
        result = {"evidence_examples": total, "visible": covered, "coverage": covered/total if total else None,
                  "tokens_mean": sum(token_counts)/len(token_counts), "tokens_max": max(token_counts),
                  "source_coverage_mean": sum(source_coverage)/len(source_coverage), "per_item": counts}
        report[f"{mode}_{budget}"] = result
        print(mode, budget, json.dumps({k: v for k,v in result.items() if k != "per_item"}), flush=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
