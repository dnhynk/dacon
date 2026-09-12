"""RETIRED legacy output-contract audit; historical helpers are retained."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    from tools.legacy_entry import retired_main
    retired_main(Path(__file__).name)

from pps.data import ABSENCE, clean_evidence, records, validate_csv
from pps.pipeline import parse_output
from pps.prompts import fact_fields
from runs.cache_replay.benchmark import stored_spans


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate_key:{key}")
        result[key] = value
    return result


def text_stats(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    counts = Counter(lines)
    return {
        "characters": len(text),
        "whitespace_fraction": sum(c.isspace() for c in text) / max(1, len(text)),
        "max_whitespace_run": max((len(m[0]) for m in re.finditer(r"\s+", text)), default=0),
        "max_newline_run": max((len(m[0]) for m in re.finditer(r"\n+", text)), default=0),
        "repeated_nonempty_line_count": sum(n - 1 for n in counts.values()),
    }


def audit(trace, report_path, input_path, predictions):
    recs = {r["id"]: r for r in records(input_path)}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("mock") is not False:
        raise ValueError("A mock run cannot support output-quality conclusions")
    native = report["config"].get("enable_thinking", False)
    seen = Counter()
    totals = Counter()
    finishes = Counter()
    entries, failures = [], []
    raw_evidence = Counter()
    for n, line in enumerate(trace.read_text(encoding="utf-8").splitlines(), 1):
        obj = json.loads(line, object_pairs_hook=unique_object)
        rid = obj["id"]
        if rid not in recs:
            raise ValueError("Trace contains a record outside the requested data scope")
        response = obj["response"]
        text = response.get("text", "")
        stat = text_stats(text)
        item = {"line": n, "id": rid, "items": obj.get("items"), **stat,
                "finish_reason": response.get("finish_reason"),
                "output_tokens": response.get("output_tokens"),
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "issues": []}
        finishes[str(response.get("finish_reason"))] += 1
        totals["calls"] += 1
        totals["generated_tokens"] += response.get("output_tokens", 0)
        totals["answer_tokens"] += response.get("answer_tokens", response.get("output_tokens", 0))
        totals["thinking_tokens"] += response.get("thinking_tokens", 0)
        totals["cached_input_tokens"] += response.get("cached_input_tokens") or 0
        if obj.get("error"):
            item["issues"].append(obj["error"])
        if response.get("finish_reason") != "stop":
            item["issues"].append("non_stop_finish")
        if native:
            if response.get("thinking_close_marker") is not True:
                item["issues"].append("unclosed_thinking")
            if response.get("thinking_tokens") != response.get("thinking_budget"):
                item["issues"].append("thinking_count_differs_from_requested_budget")
            totals["thinking_close_markers"] += int(response.get("thinking_close_marker") is True)
        if "<|channel>" in text or "<channel|>" in text:
            item["issues"].append("thinking_delimiter_in_final_text")
        # Diagnostic indicators, not proof of a stall or a semantic error.
        if stat["max_whitespace_run"] >= 256 or (len(text) >= 256 and stat["whitespace_fraction"] >= .95):
            item["issues"].append("whitespace_stall_indicator")
        try:
            parsed = json.loads(text, object_pairs_hook=unique_object)
            totals["json_parse_success"] += 1
            expected = {f"v{k}" for k in obj["items"]}
            if not isinstance(parsed, dict) or set(parsed) != {"facts", "judgments"}:
                raise ValueError("factored_root_keys")
            if not isinstance(parsed["facts"], dict) or set(parsed["facts"]) != set(fact_fields(obj["items"])):
                raise ValueError("required_fact_keys")
            if not isinstance(parsed["judgments"], dict) or set(parsed["judgments"]) != expected:
                raise ValueError("required_item_keys")
            spans = stored_spans(obj["messages"])
            vals, evidence = parse_output(text, [SimpleNamespace(**s) for s in spans], obj["items"])
            for span in spans:
                doc = recs[rid]["docs"][span["doc_index"]]
                if doc["text"][span["start"]:span["end"]] != span["text"]:
                    raise ValueError("span_not_exact_source_offset")
            totals["strict_contract_success"] += 1
            for k in obj["items"]:
                seen[(rid, k)] += 1
                if vals[k-1] and k not in ABSENCE:
                    raw_evidence["positive_nonabsence"] += 1
                    ev = evidence[k-1]
                    raw_evidence["selected_nonempty"] += bool(ev)
                    raw_evidence["selected_source_exact"] += bool(ev) and any(ev in d["text"] for d in recs[rid]["docs"])
                    raw_evidence["usable_after_csv_cleaning"] += bool(clean_evidence(ev, recs[rid]))
                    raw_evidence["empty_reference"] += not bool(ev)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            item["issues"].append(f"contract:{type(exc).__name__}:{str(exc)[:90]}")
        entries.append(item)
        if item["issues"]:
            failures.append({k: item[k] for k in ("line", "id", "items", "issues")})
    missing = [{"id": rid, "item": k, "count": seen[(rid, k)]}
               for rid in recs for k in range(1, 25) if seen[(rid, k)] != 1]
    csv_evidence = None
    if predictions:
        rows = validate_csv(predictions, list(recs.values()))
        ev = Counter()
        for row in rows:
            for k in range(1, 25):
                if row[f"v{k}"] == "1" and k not in ABSENCE:
                    ev["positive_nonabsence"] += 1
                    ev["nonempty_source_exact"] += bool(row[f"e{k}"])
                    ev["empty_evidence"] += not bool(row[f"e{k}"])
        csv_evidence = dict(ev)
    result = {
        "status": "AUDITED_SAVED_MODEL_OUTPUTS_NOT_NEW_INFERENCE", "data_scope": "frozen_development_160",
        "records": len(recs), "totals": dict(totals), "finish_reasons": dict(finishes),
        "reported_retries": report.get("retries"), "reported_normal_model_calls": report.get("normal_model_calls"),
        "max_whitespace_run": max((e["max_whitespace_run"] for e in entries), default=0),
        "max_newline_run": max((e["max_newline_run"] for e in entries), default=0),
        "max_whitespace_fraction": max((e["whitespace_fraction"] for e in entries), default=0),
        "max_repeated_nonempty_line_count": max((e["repeated_nonempty_line_count"] for e in entries), default=0),
        "issue_calls": failures, "missing_or_duplicate_requested_items": missing,
        "raw_positive_evidence": dict(raw_evidence), "final_csv_evidence": csv_evidence,
        "limitations": [
            "Native runs preserve final-answer text and raw token/boundary/hash diagnostics, not raw thought text; historical thought whitespace/repetition cannot be checked.",
            "Exact substring/offset validity is measured; legal sufficiency and relevance are separate and not certified by this audit.",
            "A single stored final response is not token-stream stall telemetry. No observed whitespace/length indicators does not measure per-token stalls.",
        ],
        "inputs": {str(p.resolve()): sha(p) for p in (trace, report_path, input_path, *([predictions] if predictions else []))},
        "audit_code_sha256": sha(__file__),
    }
    if totals["calls"] != report["normal_model_calls"]:
        result["report_call_count_mismatch"] = True
    return result, entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("trace", "report", "input", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--predictions", type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("Use a new audit directory to preserve prior evidence")
    result, entries = audit(args.trace, args.report, args.input, args.predictions)
    args.out.mkdir(parents=True)
    for name, value in (("summary", result), ("calls", entries)):
        path = args.out / f"{name}.json"
        tmp = path.with_suffix(".part")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    print(json.dumps({k: v for k, v in result.items() if k not in ("inputs", "limitations")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
