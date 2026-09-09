"""Reparse frozen model answers before recomputing rules; no model result cache lookup."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pps.data import make_row, records, validate_csv, write_csv
from pps.knowledge import Knowledge
from pps.pipeline import parse_output
from pps.rules import apply_rules
from tools.audit_output_contract import unique_object
from runs.cache_replay.benchmark import stored_spans

SNAPSHOT = "260e3f5e2aecb716ba4282b39a72e70348475b549b2b9701d225d73a5b18a3c1"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stage_key(kind, inputs, parents, config, snapshot_hash=SNAPSHOT):
    fields = {"node_type": kind, "node_version": 1, "canonical_input_hash": digest(inputs),
              "parent_output_hashes": parents, "config_hash": digest(config),
              "data_scope": "frozen_development_160_no_labels", "snapshot_hash": snapshot_hash}
    return {**fields, "key": digest(fields)}


def replay(trace, report_path, input_path, out, expected, boundary_path):
    if out.exists():
        raise ValueError("Use a new output directory")
    boundary = json.loads(boundary_path.read_text(encoding="utf-8"))
    if boundary.get("status") != "FROZEN_AUDITED_SUCCESS":
        raise ValueError("Failed/partial/unaudited boundary cannot be reused")
    for role, path in (("trace", trace), ("report", report_path), ("input", input_path)):
        if file_digest(path) != boundary[f"{role}_sha256"]:
            raise ValueError(f"Changed {role} invalidates the frozen model-output boundary")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("mock") is not False or report.get("retries") != 0:
        raise ValueError("Only complete real runs without retry prompt replacement are supported")
    run_root = report_path.parents[2]
    selection = json.loads((run_root / "selection.json").read_text(encoding="utf-8"))
    environment = json.loads((run_root / "environment.json").read_text(encoding="utf-8"))
    if report["config"] != selection["config"]:
        raise ValueError("Report policy differs from the original saved selection; model outputs cannot stand in for a new policy")
    provenance = environment["model_provenance"]
    if (provenance["model"] != "google/gemma-4-26B-A4B-it" or
            provenance["revision"] != "4d7ae4984b7db7de8f8457170b3f1a419ee76d52" or
            provenance.get("tokenizer_only", True)):
        raise ValueError("Original fixed-model provenance is not verified")
    recs = list(records(input_path))
    by_id = {r["id"]: r for r in recs}
    rows = {mode: {r["id"]: None for r in recs} for mode in ("saved_checks", "current_checks")}
    coverage = Counter()
    knowledge = Knowledge(ROOT / "data_open/data")
    source_hashes = {p.name: file_digest(p) for p in sorted((ROOT / "pps").glob("*.py"))}
    assets = [ROOT / "data_open/data/항목표.json", ROOT / "data_open/data/정답스키마_디코딩.json",
              *sorted((ROOT / "data_open/data/법령패키지").rglob("*"))]
    asset_hashes = {p.relative_to(ROOT).as_posix(): file_digest(p) for p in assets if p.is_file()}
    snapshot_hash = digest(asset_hashes)
    calls, parents = 0, []
    started = time.perf_counter()
    for line in trace.read_text(encoding="utf-8").splitlines():
        obj = json.loads(line, object_pairs_hook=unique_object)
        if obj.get("error") or obj["response"]["finish_reason"] != "stop":
            raise ValueError("Failed/partial model output cannot be reused as success")
        if hashlib.sha256(json.dumps(obj["messages"], ensure_ascii=False).encode()).hexdigest() != obj["prompt_sha256"]:
            raise ValueError("Frozen parent message was altered")
        response = obj["response"]
        if report["config"].get("enable_thinking") and not response.get("thinking_close_marker"):
            raise ValueError("Incomplete native thinking response")
        rec, items = by_id[obj["id"]], obj["items"]
        spans = stored_spans(obj["messages"])
        for span in spans:
            if rec["docs"][span["doc_index"]]["text"][span["start"]:span["end"]] != span["text"]:
                raise ValueError("Frozen parent source span was altered")
        json.loads(response["text"], object_pairs_hook=unique_object)
        labels, evidence = parse_output(response["text"], [SimpleNamespace(**s) for s in spans], items)
        raw = make_row(rec, labels, evidence)
        saved = dict(raw)
        for check in obj["rule_checks"]:
            k = check["item"]
            saved[f"v{k}"], saved[f"e{k}"] = check["value"], check["evidence"]
        current, _ = apply_rules(rec, raw, knowledge)
        for mode, row in (("saved_checks", saved), ("current_checks", current)):
            if obj["pass"] == 0:
                if rows[mode][rec["id"]] is not None:
                    raise ValueError("Duplicate first pass")
                rows[mode][rec["id"]] = row
            else:
                if rows[mode][rec["id"]] is None:
                    raise ValueError("Missing first pass")
                for k in items:
                    for prefix in ("v", "e"):
                        rows[mode][rec["id"]][f"{prefix}{k}"] = row[f"{prefix}{k}"]
        for k in items:
            coverage[(rec["id"], k)] += 1
        parents.append({"id": rec["id"], "items": items, "prompt": obj["prompt_sha256"],
                        "answer": hashlib.sha256(response["text"].encode()).hexdigest()})
        calls += 1
    if any(coverage[(r["id"], k)] != 1 for r in recs for k in range(1, 25)):
        raise ValueError("Requested item coverage incomplete or duplicated")
    if calls != report["normal_model_calls"]:
        raise ValueError("Original successful call count differs")
    if source_hashes != {p.name: file_digest(p) for p in sorted((ROOT / "pps").glob("*.py"))}:
        raise ValueError("Rules/parser source changed during CPU replay")
    out.mkdir(parents=True)
    for mode in rows:
        dest = out / f"{mode}.csv"
        write_csv(dest, [rows[mode][r["id"]] for r in recs])
        validate_csv(dest, recs)
    original_rows = validate_csv(expected, recs)
    reconstructed = validate_csv(out / "saved_checks.csv", recs)
    if original_rows != reconstructed:
        raise ValueError("Saved-check boundary replay differs from the original successful CSV")
    model_node = stage_key("frozen_successful_model_answers", parents,
                           [file_digest(trace), file_digest(input_path), file_digest(run_root / "environment.json")],
                           {"run_config": report["config"], "model_runtime": environment}, snapshot_hash)
    rule_node = stage_key("recomputed_rules_from_raw_answers", parents,
                          [model_node["key"], snapshot_hash], source_hashes, snapshot_hash)
    result = {"status": "CPU_REPLAY_COMPLETE_NOT_NEW_GPU_INFERENCE", "gpu_calls_executed": 0,
              "successful_prior_model_calls_reused": calls, "cpu_seconds": time.perf_counter()-started,
              "saved_check_replay_equals_original_rows": True,
              "saved_check_replay_equals_original_bytes": file_digest(out / "saved_checks.csv") == file_digest(expected),
              "current_rules_start_from": "parsed raw final answer, before any previous rule override",
              "source_trace_sha256": file_digest(trace), "source_report_sha256": file_digest(report_path),
              "source_input_sha256": file_digest(input_path), "code_sha256": file_digest(__file__),
              "frozen_boundary_sha256": file_digest(boundary_path),
              "provided_archive_sha256": SNAPSHOT, "static_asset_hashes": asset_hashes,
              "nodes": [model_node, rule_node], "outputs": {mode: file_digest(out / f"{mode}.csv") for mode in rows}}
    tmp = out / "manifest.part"
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, out / "manifest.json")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("trace", "report", "input", "out", "expected", "boundary"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    result = replay(args.trace, args.report, args.input, args.out, args.expected, args.boundary)
    print(json.dumps({k: v for k, v in result.items() if k != "nodes"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
