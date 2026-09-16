"""Replay a frozen whole-Q task-context run through an explicitly changed CPU consumer.

Generation inputs and native responses stay immutable.  ``replay`` freezes all
current-consumer predictions before ``score`` may read exposed development
labels.  A caller must name the complete source-manifest delta, which prevents
an unrelated worktree edit from entering the comparison silently.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from submission.b4_entry import B4Pipeline, assemble, parse_error
from submission.pps.data import read_csv, write_csv
from submission.runtime import Journal, sha256, source_manifest
from tools.consume_task_context import projected, replacement_packets
from tools.evaluate import compare
from tools.run_integrated_comparison import verify_resolved_native
from tools.run_runtime_revival import read, rows, verify_prepared


LABEL_SHA256 = "9e277dd7d72bd4c9cdca386dbac2d4d2ce4d26b9997f0f46907a1237a09c06ce"


def _source_delta(before, after):
    return sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))


def _baseline_delta(before_path, after_path):
    before = {row['id']: row for row in read_csv(before_path)}
    after = {row['id']: row for row in read_csv(after_path)}
    if set(before) != set(after):
        raise AssertionError('Pre-Q baseline record IDs changed')
    return [{'record_id': record_id, 'field': field,
             'before': before[record_id][field], 'after': after[record_id][field]}
            for record_id in before for field in before[record_id]
            if field != 'id' and before[record_id][field] != after[record_id][field]]


def _verify_code_only_baseline_delta(changes, adaptations):
    """Admit only cells produced by a current plan that no longer calls a model."""
    code_only = {item['record_id']: item for item in adaptations
                 if item['receipt']['mode'] == 'current_code_only'}
    for change in changes:
        adaptation = code_only.get(change['record_id'])
        if adaptation is None:
            raise AssertionError('Baseline changed without a code-only source-plan receipt: '
                                 + repr(change))
        if change['field'] not in adaptation['row']:
            raise AssertionError('Changed baseline cell is outside the deterministic source row: '
                                 + repr(change))
        if str(adaptation['row'][change['field']]) != str(change['after']):
            raise AssertionError('Baseline delta does not equal the deterministic source value: '
                                 + repr(change))
    return True


def _parse_expected_baseline_changes(values):
    changes = []
    for value in values:
        if isinstance(value, dict):
            required = {'record_id', 'field', 'before', 'after'}
            if set(value) != required or not value['record_id'] or not value['field']:
                raise ValueError('Structured baseline change must contain only '
                                 'record_id, field, before, and after')
            if any(not isinstance(value[key], str) for key in required):
                raise ValueError('Structured baseline change values must be strings')
            changes.append(dict(value))
            continue
        # Evidence is part of the CSV cell contract and commonly contains
        # colons (for example ``제출기간 : 14:00``).  Only the first three
        # separators belong to the declaration envelope.  Use the structured
        # JSON form when both before and after can contain colons.
        parts = value.split(':', 3)
        if len(parts) != 4 or not parts[0] or not parts[1]:
            raise ValueError('Expected baseline change must be RECORD_ID:FIELD:BEFORE:AFTER')
        changes.append(dict(record_id=parts[0], field=parts[1], before=parts[2], after=parts[3]))
    return changes


def replay(prepared, run, base, expected_baseline, frozen_consumed, output, allowed_changes,
           *, allow_deterministic_baseline_change=False, expected_baseline_changes=()):
    frozen = read(prepared / "contrast_freeze.json")
    refs = read(prepared / "scoring_references.json")
    current_source = source_manifest()
    changed = _source_delta(frozen["source_sha256"], current_source)
    if changed != sorted(set(allowed_changes)):
        raise ValueError(f"Current consumer source delta is {changed!r}, expected {sorted(set(allowed_changes))!r}")
    if not changed:
        raise ValueError("Use consume_task_context.py when the producer and consumer are unchanged")
    assert all(sha256(ROOT / name) == digest for name, digest in refs["files"].items())
    assert sha256(prepared / "contrast_packets.jsonl.gz") == frozen["packets_sha256"]
    assert sha256(prepared / "contrast_inputs.jsonl.gz") == frozen["inputs_sha256"]

    packets = rows(prepared / "contrast_packets.jsonl.gz")
    records = rows(prepared / "full_join_inputs.jsonl.gz")
    by_id = {record["id"]: record for record in records}
    assert len(by_id) == len(records) == 160
    assert rows(run / "current_packets.jsonl.gz") == packets
    assert rows(run / "current_inputs.jsonl.gz") == rows(prepared / "contrast_inputs.jsonl.gz")
    assert read(run / "input_freeze.json") == frozen
    resolved = rows(run / "resolved_responses.jsonl.gz")
    remote = {result["request_key"]: result for result in rows(run / "contrast_results.jsonl.gz")}
    assert len(remote) == len(packets) == 470
    native = verify_resolved_native(run, packets, projected(resolved))
    assert native["primary_attempts"] == 470
    assert not native["unreturned_requests"] and not native["unparsed_native_attempts"]
    assert not native["unrequested_primary_keys"]
    fresh = {result["request_key"]: result for result in resolved}
    assert set(fresh) == set(remote) == {packet["request_key"] for packet in packets}

    old_prepared = base / "source_questions_v1/prepared_01"
    old_run = base / "gpu_runtime_v28/final_recovery_01/recovered/runtime_run"
    verify_prepared(old_prepared, require_current_source=False)
    assert rows(old_prepared / "current_inputs.jsonl.gz") == records
    old_primary = rows(old_prepared / "primary_packets.jsonl.gz")
    old_probes = rows(old_prepared / "probe_packets.jsonl.gz")
    lookup = {packet["request_key"]: packet for packet in old_primary + old_probes}
    keys = read(old_prepared / "recipes.json")[refs["fixed_base_policy"]]
    skipped = read(old_run / "normal/skipped_requests.json")
    old_normal = rows(old_run / "normal/resolved_responses.jsonl.gz")
    old_probe = rows(old_run / "probes/resolved_responses.jsonl.gz")
    old_responses = {result["request_key"]: result for result in old_normal + old_probe}
    native_base = {
        "normal": verify_resolved_native(old_run / "normal",
            [packet for packet in old_primary if packet["request_key"] not in skipped], projected(old_normal)),
        "probes": verify_resolved_native(old_run / "probes", old_probes, projected(old_probe)),
    }

    pipe = B4Pipeline(ROOT / "data_open/data", None)
    consumed = {}
    decisions = []
    source_plan_adaptations = []
    for key in keys:
        packet = lookup[key]
        if key in skipped:
            from submission.skips import validate, consume as consume_skip
            validate(packet, skipped[key])
            row, trace = consume_skip(by_id[packet["record_id"]], packet, skipped[key], pipe.knowledge)
        else:
            response = old_responses[key]["response"]
            error = parse_error(packet, response)
            if error:
                assert packet["batch"] in {"Q10", "S9", "W20"}
                row, trace = None, {"recorded_optional_format_failure": error}
            else:
                from tools.source_plan_replay import consume_saved
                row, trace, adaptation = consume_saved(
                    pipe, by_id[packet["record_id"]], packet, response)
                if adaptation:
                    source_plan_adaptations.append({'record_id': packet['record_id'],
                        'request_key': key, 'receipt': adaptation, 'row': row})
        consumed[key] = row
        decisions.append({"request_key": key, "record_id": packet["record_id"],
            "row": row, "details": trace, "origin": "fixed_saved_response_current_consumer"})
    _, baseline = assemble(records, [lookup[key] for key in keys], consumed)
    journal = Journal(output)
    baseline_path = output / "current_consumer_baseline.csv"
    write_csv(baseline_path, [baseline[record["id"]] for record in records], recs=records,
              require_positive_evidence=pipe.config.require_positive_evidence)
    baseline_changes = _baseline_delta(expected_baseline, baseline_path)
    baseline_change_basis = None
    if baseline_changes:
        declared_changes = _parse_expected_baseline_changes(expected_baseline_changes)
        if declared_changes:
            sort_key = lambda item: (item['record_id'], item['field'], item['before'], item['after'])
            if sorted(declared_changes, key=sort_key) != sorted(baseline_changes, key=sort_key):
                raise AssertionError('Observed pre-Q baseline delta differs from exact declared delta')
            baseline_change_basis = 'explicit_exact_cell_delta'
        elif allow_deterministic_baseline_change:
            _verify_code_only_baseline_delta(baseline_changes, source_plan_adaptations)
            baseline_change_basis = 'current_source_plan_became_code_only'
        else:
            raise AssertionError("Current-consumer base does not reproduce the declared pre-Q reference")

    base_packets = [lookup[key] for key in keys]
    q_ids = {packet["record_id"] for packet in base_packets if packet["family"] == "Q"}
    assert q_ids == {packet["record_id"] for packet in packets} and len(q_ids) == 94
    predictions = {}
    response_changes = []
    arm_csv_changes = []
    for arm in frozen["methods"]:
        part = [packet for packet in packets if packet["arm"] == arm]
        assert len(part) == 94 and {packet["record_id"] for packet in part} == q_ids
        gates = Counter()
        for packet in part:
            key = packet["request_key"]
            response = fresh[key]["response"]
            assert parse_error(packet, response) is None
            row, trace = pipe.consume(by_id[packet["record_id"]], packet, response)
            consumed[key] = row
            gates[trace["gate"]] += 1
            if row != remote[key]["row"]:
                response_changes.append({"request_key": key, "record_id": packet["record_id"],
                    "arm": arm, "before": remote[key]["row"], "after": row})
            decisions.append({"request_key": key, "record_id": packet["record_id"], "arm": arm,
                "row": row, "details": trace, "origin": "fresh_Q_response_current_consumer"})
        _, values = assemble(records, replacement_packets(base_packets, part), consumed)
        path = output / f"{arm}.csv"
        write_csv(path, [values[record["id"]] for record in records], recs=records,
                  require_positive_evidence=pipe.config.require_positive_evidence)
        prior = frozen_consumed / f"{arm}.csv"
        before = {row["id"]: row for row in read_csv(prior)}
        after = {row["id"]: row for row in read_csv(path)}
        changed_cells = [{"id": rid, "item": f"v{item}", "before": before[rid][f"v{item}"],
                          "after": after[rid][f"v{item}"]}
                         for rid in before for item in range(1, 25)
                         if before[rid][f"v{item}"] != after[rid][f"v{item}"]]
        arm_csv_changes.append({"arm": arm, "changed_cells": changed_cells})
        predictions[arm] = {"path": path.name, "sha256": sha256(path), "records": len(after),
                            "gates": dict(gates)}

    journal.rows("decisions.jsonl.gz", decisions)
    journal.save("native_verification.json", {"fresh_Q": native, "fixed_base": native_base})
    journal.save("consumer_changes.json", {"response_rows": response_changes,
        "assembled_predictions": arm_csv_changes})
    immutable = {path.as_posix(): sha256(path) for path in (
        prepared / "contrast_freeze.json", prepared / "scoring_references.json",
        prepared / "full_join_inputs.jsonl.gz", run / "resolved_responses.jsonl.gz",
        run / "contrast_results.jsonl.gz", run / "contrast_summary.json",
        expected_baseline, frozen_consumed / "prediction_freeze.json")}
    assert source_manifest() == current_source
    result = {"kind": "fresh_Q_responses_changed_current_CPU_consumer_replay",
        "producer_source_sha256": frozen["source_sha256"], "consumer_source_sha256": current_source,
        "changed_source_files": changed, "input_sha256": immutable, "predictions": predictions,
        "baseline_sha256": sha256(baseline_path), "fresh_Q_responses": 470,
        "baseline_changes": baseline_changes,
        "baseline_change_basis": baseline_change_basis,
        "source_plan_adaptations": [dict(item, row=None) for item in source_plan_adaptations],
        "deterministic_baseline_change_explicitly_allowed": bool(baseline_changes),
        "source_and_consumer_fixed_before_generation": False, "new_model_calls": 0,
        "labels_read": False, "old_Q_overlays_removed_before_join": True,
        "per_notice_arm_selection": False, "official_score": None,
        "limitation": "All V29 responses replayed through the later declared CPU-only consumer delta; not fresh inference of the combined runtime or an official score."}
    journal.save("prediction_freeze.json", result)
    return result


def score(consumed, labels, output):
    frozen = read(consumed / "prediction_freeze.json")
    assert frozen["kind"] == "fresh_Q_responses_changed_current_CPU_consumer_replay"
    assert not frozen["labels_read"] and frozen["old_Q_overlays_removed_before_join"]
    assert source_manifest() == frozen["consumer_source_sha256"]
    assert all(sha256(Path(path)) == digest for path, digest in frozen["input_sha256"].items())
    assert sha256(labels) == LABEL_SHA256
    journal = Journal(output)
    measurements = {}
    paths = {arm: consumed / item["path"] for arm, item in frozen["predictions"].items()}
    for arm, path in paths.items():
        assert sha256(path) == frozen["predictions"][arm]["sha256"]
        result = compare(labels, path, [consumed / "current_consumer_baseline.csv",
                                        *([paths["shared_plain"]] if arm != "shared_plain" else [])])
        journal.save(f"{arm}_metrics.json", result)
        candidate = result["candidate"]
        measurements[arm] = {"macro_f1": candidate["macro_f1"],
            "fp": sum(row["fp"] for row in candidate["per_item"].values()),
            "fn": sum(row["fn"] for row in candidate["per_item"].values()),
            "path": path.as_posix(), "sha256": sha256(path)}
    report = {"kind": frozen["kind"], "measurements": measurements,
        "predictions_sha256": sha256(consumed / "prediction_freeze.json"),
        "labels_sha256": sha256(labels), "new_model_calls": 0,
        "full_standalone_fresh_inference": False, "official_score": None,
        "limitation": frozen["limitation"]}
    journal.save("report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    rp = sub.add_parser("replay")
    for name in ("prepared", "run", "base", "expected-baseline", "frozen-consumed", "output"):
        rp.add_argument("--" + name, type=Path, required=True)
    rp.add_argument("--allow-source-change", action="append", default=[])
    rp.add_argument("--allow-deterministic-baseline-change", action="store_true")
    rp.add_argument("--expect-baseline-change", action="append", default=[])
    rp.add_argument("--expect-baseline-changes-json", type=Path)
    sp = sub.add_parser("score")
    for name in ("consumed", "labels", "output"):
        sp.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.operation == "replay":
        declared = list(args.expect_baseline_change)
        if args.expect_baseline_changes_json:
            loaded = read(args.expect_baseline_changes_json.resolve())
            if not isinstance(loaded, list):
                raise ValueError('Expected baseline changes JSON must be a list')
            declared.extend(loaded)
        result = replay(args.prepared.resolve(), args.run.resolve(), args.base.resolve(),
                        args.expected_baseline.resolve(), args.frozen_consumed.resolve(),
                        args.output.resolve(), args.allow_source_change,
                        allow_deterministic_baseline_change=args.allow_deterministic_baseline_change,
                        expected_baseline_changes=declared)
    else:
        result = score(args.consumed.resolve(), args.labels.resolve(), args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
