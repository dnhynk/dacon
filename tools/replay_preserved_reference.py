"""Replay the preserved .751807 CPU reference into a NEW local directory.

Requires private original data/responses. Loads no model and never generates new
answers. This command is deliberately separate from the submission entrypoint.
"""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import sys
import time
from pathlib import Path

from project_status import ROOT, read_json, sha256, verify_state


def lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return list(map(json.loads, stream))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory below artifacts/reference_replays/")
    args = parser.parse_args()
    output = args.output.resolve()
    allowed = (ROOT / "artifacts/reference_replays").resolve()
    if output == allowed or not output.is_relative_to(allowed) or output.exists():
        raise SystemExit("Choose a new child directory under artifacts/reference_replays/; existing results are never overwritten.")
    state = read_json(ROOT / "docs/STATE.json")
    verification = verify_state(ROOT, state)
    if not verification["ok"]:
        raise SystemExit("Preserved artifacts unavailable or changed. Run tools/project_status.py --verify; no output was created.")
    reference = next(r for r in state["records"] if r["id"] == "historical_b4_751")
    frozen = ROOT / "experiments/precision_joined_v1/source"
    trial = ROOT / "experiments/precision_joined_v1/full160_b4_actual_20260912"
    historical = ROOT / "runs/review_sprint_20260909/v7_actual_evaluation_20260909"
    sys.path.insert(0, str(frozen))
    from pps.data import write_csv
    from pps.knowledge import Knowledge
    from pps.pipeline import _response_row
    from pps.prompts import Config
    from pps.retrieval import Span
    from pps.v20_route import UniformV20Route
    import pps.pipeline
    if not Path(pps.pipeline.__file__).resolve().is_relative_to(frozen):
        raise SystemExit("Wrong pps module imported; run this tool in a fresh Python process.")
    records = lines(trial / "panel_input.jsonl.gz")
    recs = {r["id"]: r for r in records}
    packets = lines(trial / "packets.jsonl.gz")
    assert len(records) == len(recs) == 160 and len(packets) == 640
    raw = {}
    for family, folder in (("A", "compact_full/results/v7_fact_compact"),
                           ("L", "baseline_full/results/v7_batch32_baseline")):
        rows = lines(historical / folder / "raw_responses.jsonl.gz")
        raw[family] = {r["messages_sha256"]: r for r in rows}
        assert len(rows) == len(raw[family]) == 480
    cfg = Config.load(frozen / "model/config.json")
    knowledge = Knowledge(ROOT / "data_open/data")
    route = UniformV20Route(ROOT / "data_open/data", None)
    predicted = {rid: {"id": rid} for rid in recs}
    consumed = 0
    started = time.monotonic()
    print("CPU replay only: consuming640 preserved responses; no model will load.", file=sys.stderr, flush=True)
    for packet in packets:
        old = raw[packet["family"]][packet["prompt_sha256"]]
        assert old["items"] == packet["items"] and old["spans"] == packet["spans"]
        assert old.get("comparison_facts") == packet.get("comparison_facts")
        prompt = {**packet, "spans": [Span(**s) for s in packet["spans"]]}
        rid, items = packet["record_id"], tuple(packet["items"])
        if packet["family"] == "L":
            values, _ = route.consume(recs[rid], old["response"], prompt)
            assert set(values) == {"v20", "e20"}
        else:
            row, _ = _response_row(recs[rid], old["response"], prompt, items, cfg, knowledge, items)
            values = {f"{field}{k}": row[f"{field}{k}"] for k in items for field in ("v", "e")}
        predicted[rid].update(values)
        consumed += 1
        if consumed % 160 == 0:
            print(f"CPU replay {consumed}/640", file=sys.stderr, flush=True)
    assert consumed == 640 and all(len(r) == 49 for r in predicted.values())
    output.mkdir(parents=True, exist_ok=False)
    prediction_path = output / "historical_b4_replayed.csv"
    write_csv(prediction_path, list(predicted.values()), recs=records, require_positive_evidence=False)
    actual = sha256(prediction_path)
    assert actual == reference["sha256"], "CPU replay did not match the preserved bytes; investigate, do not replace the reference."
    spec = importlib.util.spec_from_file_location("reference_scorer", ROOT / "tools/evaluate.py")
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    metrics = scorer.evaluate(ROOT / "artifacts/audit/development_labels.csv", prediction_path)
    assert abs(metrics["macro_f1"] - reference["macro_f1"]) < 1e-12
    report = {"kind": "historical_response_cpu_replay", "records": 160,
              "historical_responses_consumed": consumed, "new_model_calls": 0,
              "macro_f1": metrics["macro_f1"], "fp": sum(x["fp"] for x in metrics["per_item"].values()),
              "fn": sum(x["fn"] for x in metrics["per_item"].values()),
              "byte_identical_to_preserved_reference": True, "prediction_sha256": actual,
              "seconds": time.monotonic() - started, "official_score": None,
              "note": "Restores the historical CPU result, not the fresh-Gemma score. No original or submission artifact modified."}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
