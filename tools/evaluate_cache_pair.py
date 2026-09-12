"""RETIRED legacy cache-pair scorer; not a canonical full-chain comparison."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    from tools.legacy_entry import retired_main
    retired_main(Path(__file__).name)

from pps.data import ABSENCE, make_row, records, read_csv, validate_csv, write_csv
from pps.knowledge import Knowledge
from pps.pipeline import parse_output
from pps.rules import apply_rules
from tools.evaluate import evaluate
from tools.audit_output_contract import unique_object


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    part = path.with_suffix(".partial")
    part.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(part, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("Preserve existing evaluation; use a new directory")
    pair = args.pair_dir
    comparison = json.loads((pair / "comparison.json").read_text(encoding="utf-8"))
    if comparison["status"] not in {"PAIR_COMPLETED_EQUIVALENT", "PAIR_COMPLETED_WITH_DRIFT"}:
        raise ValueError("A failed or unmatched pair cannot support a valid comparison")
    packets = json.loads((pair / "frozen/frozen_requests.json").read_text(encoding="utf-8"))["requests"]
    packet_map = {(p["source_id"], p["group"]): p for p in packets}
    if len(packet_map) != 96:
        raise ValueError("Expected 96 frozen requests")
    ids = list(dict.fromkeys(p["source_id"] for p in packets))
    all_recs = {r["id"]: r for r in records(ROOT / "artifacts/audit/development.jsonl.gz")}
    recs = [all_recs[rid] for rid in ids]
    sources = {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / "pps").glob("*.py")}
    knowledge = Knowledge(ROOT / "data_open/data")
    arm_rows, times, evidence = {}, {}, {}
    # Both successful GPU arms must already exist before either is postprocessed/scored.
    captured = {size: json.loads((pair / f"arm{size}/calls.json").read_text(encoding="utf-8"))["calls"] for size in (32, 8)}
    for size, calls in captured.items():
        if (len(calls) != 96 or any(c["status"] != "MODEL_OUTPUT_VALIDATED" for c in calls)
                or {(c["source_id"], c["group"]) for c in calls} != set(packet_map)):
            raise ValueError("Incomplete/new failed model response; do not fill missing labels with zero")
        rows = {}
        started = time.perf_counter()
        for call in sorted(calls, key=lambda c: (c["record_rank"], c["group"])):
            packet = packet_map[call["source_id"], call["group"]]
            if any(call[k] != packet[k] for k in ("token_ids_sha256", "messages_sha256", "schema_sha256", "sampling_sha256")):
                raise ValueError("New output has a different frozen parent")
            rec = all_recs[call["source_id"]]
            for span in packet["spans"]:
                if rec["docs"][span["doc_index"]]["text"][span["start"]:span["end"]] != span["text"]:
                    raise ValueError("Evidence source changed")
            json.loads(call["final_answer_text"], object_pairs_hook=unique_object)
            vals, ev = parse_output(call["final_answer_text"], [SimpleNamespace(**s) for s in packet["spans"]], packet["items"])
            if any(vals[k-1] != call["labels"][f"v{k}"] for k in packet["items"]):
                raise ValueError("Independent parser differs from captured parsed labels")
            row, _ = apply_rules(rec, make_row(rec, vals, ev), knowledge)
            if call["group"] == 1:
                rows[rec["id"]] = row
            else:
                for k in packet["items"]:
                    for prefix in ("v", "e"):
                        rows[rec["id"]][f"{prefix}{k}"] = row[f"{prefix}{k}"]
        times[str(size)] = time.perf_counter()-started
        arm_rows[size] = [rows[rid] for rid in ids]
        positives = [(r, k) for r in arm_rows[size] for k in range(1, 25) if k not in ABSENCE and r[f"v{k}"]]
        evidence[str(size)] = {"positive_nonabsence": len(positives),
                               "nonempty_exact_source": sum(bool(r[f"e{k}"]) for r, k in positives),
                               "empty": sum(not r[f"e{k}"] for r, k in positives),
                               "legal_sufficiency": "NOT_CERTIFIED_BY_SUBSTRING_VALIDATION"}
    if sources != {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / "pps").glob("*.py")}:
        raise ValueError("Postprocessing source changed between arms")
    args.out.mkdir(parents=True)
    for size, rows in arm_rows.items():
        path = args.out / f"arm{size}.csv"
        write_csv(path, rows)
        validate_csv(path, recs)
    # Labels enter only after all new outputs and both rule passes are complete.
    truths = {r["id"]: r for r in read_csv(ROOT / "artifacts/audit/development_labels.csv")}
    label_path = args.out / "cohort_labels.csv"
    write_csv(label_path, [truths[rid] for rid in ids])
    metric = {str(s): evaluate(label_path, args.out / f"arm{s}.csv") for s in (32, 8)}
    indexed = {s: {r["id"]: r for r in arm_rows[s]} for s in (32, 8)}
    changes = []
    for rid in ids:
        for k in range(1, 25):
            key = f"v{k}"
            a, b, y = indexed[32][rid][key], indexed[8][rid][key], int(truths[rid][key])
            if a != b:
                changes.append({"id": rid, "item": key, "before": a, "after": b, "truth": y,
                                "effect": "fixed_FN" if y == b == 1 else "fixed_FP" if y == b == 0 else "new_FN" if y else "new_FP"})
    result = {"status": "SCORED_ACTUAL_NEW_GPU_PAIR", "records": len(ids), "cohort_ids": ids,
              "scope": "First32 frozen development notices only; not full160/200 or leaderboard",
              "per_arm": metric, "changes": changes,
              "corrections": sum(c["after"] == c["truth"] for c in changes),
              "regressions": sum(c["after"] != c["truth"] for c in changes),
              "evidence": evidence, "local_cpu_postprocessing_seconds": times,
              "cpu_timing_environment": "Windows coordinator; do not add to A100/L40S timing as a measured pipeline",
              "rules_source_hashes": sources, "pair_comparison_sha256": sha(pair / "comparison.json"),
              "policy": "Same reviewed rules applied from each fresh raw final answer; failed responses never become zero"}
    save(args.out / "evaluation.json", result)
    print(json.dumps({"records": len(ids), "f1_arm32": metric["32"]["macro_f1"], "f1_arm8": metric["8"]["macro_f1"],
                      "corrections": result["corrections"], "regressions": result["regressions"], "evidence": evidence}, ensure_ascii=False))


if __name__ == "__main__":
    main()
