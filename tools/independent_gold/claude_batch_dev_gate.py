"""Read-only Gate 1 diagnostic for ten verified Claude dev200 batch windows.

Replays the exact raw windows through the offline scorer before applying the
current protocol's minimum candidate thresholds.  Passing is *not* gold
qualification, legal-semantic certification, or a generalization estimate.
No model is called and no artifact is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from tools.independent_gold import score_verified_batch_dev200 as scorer
from tools.independent_gold import verify_claude_batch_pilot as verifier


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS_ROOT = ROOT / "runs"
PROTOCOL = ROOT / "runs" / "self_label_20000_20260918" / "PROTOCOL.md"
SCHEMA = "dacon.independent.claude_batch_dev_gate.v1"
SUPERVISOR_SCHEMA = "dacon.independent.claude_dev200_batch_supervisor.v1"
MACRO_MIN = 0.90
ITEM_MIN = 0.70
ITEMS = tuple(f"v{index}" for index in range(1, 25))
ABSENCE_ITEMS = frozenset({"v10", "v11", "v16", "v18", "v20"})
WINDOW_STARTS = tuple(range(0, 200, 20))
SOURCE_FILES = {
    "supervisor": ROOT / "tools" / "independent_gold" / "claude_dev200_batch_supervisor.py",
    "pilot": ROOT / "tools" / "independent_gold" / "claude_batch_pilot.py",
    "archiver": ROOT / "tools" / "independent_gold" / "archive_source_bundle.py",
    "verifier": pathlib.Path(verifier.__file__),
    "scorer": pathlib.Path(scorer.__file__),
}


class DevGateError(ValueError):
    """Provenance or metric evidence is insufficient for the gate."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DevGateError(message)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha_object(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha_file(path: pathlib.Path) -> str:
    _require(path.is_file() and not path.is_symlink(), f"missing or linked file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _bad_constant(value: str) -> Any:
    raise DevGateError(f"nonfinite JSON constant: {value}")


def _json_file(path: pathlib.Path) -> dict[str, Any]:
    _sha_file(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_pairs,
                           parse_constant=_bad_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DevGateError(f"invalid JSON artifact: {path}") from exc
    _require(isinstance(value, dict), f"JSON artifact is not an object: {path}")
    return value


def _safe_root(score_report: pathlib.Path) -> pathlib.Path:
    absolute = score_report.absolute()
    _require(not any(path.is_symlink() for path in (absolute, *absolute.parents)),
             "score report has a linked path component")
    path = absolute.resolve()
    runs = RUNS_ROOT.resolve()
    _require(path.name == "score_report.json" and path.is_file(), "score_report.json required")
    root = path.parent
    _require(root != runs and root.is_relative_to(runs), "score report is outside runs")
    return root


def _tree_hash(path: pathlib.Path) -> str:
    _require(path.is_dir() and not path.is_symlink(), f"missing or linked artifact tree: {path}")
    entries: list[tuple[str, str]] = []
    for child in path.rglob("*"):
        _require(not child.is_symlink(), f"linked artifact: {child}")
        if child.is_file():
            entries.append((child.relative_to(path).as_posix(), _sha_file(child)))
    _require(bool(entries), f"empty artifact tree: {path}")
    return _sha_object(sorted(entries))


def _protocol_identity() -> dict[str, str]:
    source = PROTOCOL.read_text(encoding="utf-8")
    normalized = re.sub(r"\s+", " ", source)
    expected = (
        "Candidate annotator: under its frozen tuple, official-dev positive-class "
        "Macro-F1 >= 0.90, every item F1 >= 0.70, no abstentions, and exact-source "
        "evidence for all non-absence positives."
    )
    _require(expected in normalized, "protocol Gate 1 text differs from pinned thresholds")
    return {"path": str(PROTOCOL.resolve()), "sha256": _sha_file(PROTOCOL),
            "section": "Promotion gates / 1. Candidate annotator",
            "threshold_version": "2026-09-18_PROTOCOL_Gate1"}


def _frozen_tuple_from_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    fields = scorer.FROZEN_FIELDS
    _require(all(key in plan for key in fields), "supervisor plan frozen tuple incomplete")
    cli = plan.get("cli")
    _require(isinstance(cli, Mapping) and isinstance(cli.get("executable_sha256"), str)
             and isinstance(cli.get("version_output"), str), "supervisor plan CLI identity incomplete")
    return {**{key: plan[key] for key in fields},
            "cli_executable_sha256": cli["executable_sha256"],
            "cli_version_output": cli["version_output"]}


def _checked_metrics(report: Mapping[str, Any]) -> dict[str, Any]:
    _require(report.get("schema_version") == scorer.SCHEMA, "wrong scorer report schema")
    _require(report.get("status") in {"metrics_only_pending_quality_policy_not_gold",
                                      "failed_unresolved_cells"}, "wrong scorer report status")
    _require(report.get("qualified_for_gold_generation") is False
             and report.get("quality_threshold_applied") is False
             and report.get("model_calls_by_scorer") == 0, "scorer report overclaims qualification")
    _require(report.get("records") == 200 and report.get("cells_expected") == 4800
             and report.get("cells_verified") == 4800, "score report is not exact 200/4800")
    per_item = report.get("per_item")
    _require(isinstance(per_item, Mapping) and set(per_item) == set(ITEMS),
             "score report is not full 24-item coverage")
    unresolved = report.get("unresolved_cells")
    errors = report.get("error_cells")
    _require(isinstance(unresolved, list) and isinstance(errors, list),
             "score report error or U cells missing")
    _require(all(isinstance(row, Mapping) for row in (*unresolved, *errors)),
             "score report U/error cell is not an object")
    macro_values: list[float] = []
    item_scores: dict[str, float | None] = {}
    total_u = total_fp = total_fn = total_positive = total_nonabsence_positive = 0
    for item in ITEMS:
        metric = per_item[item]
        _require(isinstance(metric, Mapping), f"{item}: invalid metrics")
        keys = ("gold_positive", "gold_negative", "tp", "tn", "fp", "fn", "u",
                "u_gold_positive", "u_gold_negative")
        _require(all(type(metric.get(key)) is int and metric[key] >= 0 for key in keys),
                 f"{item}: invalid cell counts")
        _require(metric["gold_positive"] + metric["gold_negative"] == 200,
                 f"{item}: official class counts differ")
        _require(sum(metric[key] for key in ("tp", "tn", "fp", "fn", "u")) == 200,
                 f"{item}: confusion counts differ")
        _require(metric["tp"] + metric["fn"] + metric["u_gold_positive"] == metric["gold_positive"]
                 and metric["tn"] + metric["fp"] + metric["u_gold_negative"] == metric["gold_negative"]
                 and metric["u_gold_positive"] + metric["u_gold_negative"] == metric["u"],
                 f"{item}: confusion matrix internally inconsistent")
        total_u += metric["u"]
        total_fp += metric["fp"]
        total_fn += metric["fn"]
        total_positive += metric["tp"] + metric["fp"]
        if item not in ABSENCE_ITEMS:
            total_nonabsence_positive += metric["tp"] + metric["fp"]
        denominator = 2 * metric["tp"] + metric["fp"] + metric["fn"]
        expected = None if metric["u"] else (0.0 if denominator == 0 else 2 * metric["tp"] / denominator)
        observed = metric.get("positive_f1")
        if expected is None:
            _require(observed is None, f"{item}: U must suppress F1")
        else:
            _require(type(observed) in (int, float) and math.isfinite(observed)
                     and math.isclose(observed, expected, rel_tol=0, abs_tol=1e-12),
                     f"{item}: positive F1 differs from counts")
            macro_values.append(expected)
        item_scores[item] = expected
    _require(total_u == len(unresolved) and total_fp + total_fn == len(errors),
             "reported U/error cell lists differ from counts")
    _require(len({(row.get("id"), row.get("item")) for row in unresolved}) == len(unresolved)
             and len({(row.get("id"), row.get("item")) for row in errors}) == len(errors),
             "duplicate U/error cells")
    expected_macro = None if total_u else sum(macro_values) / len(ITEMS)
    observed_macro = report.get("macro_positive_f1")
    if expected_macro is None:
        _require(observed_macro is None and report["status"] == "failed_unresolved_cells",
                 "U report must suppress macro score")
    else:
        _require(type(observed_macro) in (int, float) and math.isfinite(observed_macro)
                 and math.isclose(observed_macro, expected_macro, rel_tol=0, abs_tol=1e-12)
                 and report["status"] == "metrics_only_pending_quality_policy_not_gold",
                 "macro score differs from item scores")
    return {"macro_positive_f1": expected_macro, "minimum_item_positive_f1":
            min((score for score in item_scores.values() if score is not None), default=None),
            "per_item_positive_f1": item_scores, "unresolved_cells": total_u,
            "false_positives": total_fp, "false_negatives": total_fn,
            "positive_predictions": total_positive,
            "nonabsence_positive_predictions": total_nonabsence_positive}


def _evidence_summary(root: pathlib.Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    """Count cited positives after scorer has replayed every raw source record."""
    positives = nonabsence = 0
    for start in WINDOW_STARTS:
        run = root / "runs" / f"window-{start:03d}-{start + 19:03d}"
        manifest = _json_file(run / "run_manifest.json")
        _require(manifest.get("selected_ids") == plan["windows"][start // 20]["selected_ids"],
                 f"window {start}: selected IDs differ from plan")
        batch_size = manifest.get("batch_size")
        _require(type(batch_size) is int and 1 <= batch_size <= 5, "invalid batch size")
        for local_index, record_id in enumerate(manifest["selected_ids"]):
            _require(isinstance(record_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", record_id) is not None,
                     "unsafe record ID in row path")
            row = _json_file(run / f"batch-{local_index // batch_size:03d}" / f"{record_id}.row.json")
            ledger = row.get("ledger")
            _require(isinstance(ledger, Mapping), "missing evidence ledger")
            spans = ledger.get("source_spans")
            cells = ledger.get("cells")
            _require(isinstance(spans, list) and isinstance(cells, list) and len(cells) == 24,
                     "incomplete evidence ledger")
            span_by_id = {span.get("span_id"): span for span in spans if isinstance(span, Mapping)}
            _require(len(span_by_id) == len(spans), "duplicate source span ID")
            _require({cell.get("item") for cell in cells if isinstance(cell, Mapping)} == set(ITEMS),
                     "evidence ledger items differ")
            for cell in cells:
                if cell["label"] != 1:
                    continue
                positives += 1
                if cell["item"] in ABSENCE_ITEMS:
                    _require(cell.get("positive_evidence_span_id") is None,
                             "absence positive has an exported evidence span")
                    continue
                nonabsence += 1
                span_id = cell.get("positive_evidence_span_id")
                _require(isinstance(span_id, str) and span_id in span_by_id,
                         "non-absence positive lacks source span")
                _require(span_id in cell.get("premise_span_ids", []),
                         "positive source span is not a premise")
                quote = span_by_id[span_id].get("quote")
                _require(isinstance(quote, str) and quote.strip() and len(quote) <= 500,
                         "positive evidence quote missing or longer than 500 characters")
    return {"positive_cells": positives, "nonabsence_positive_cells": nonabsence,
            "nonabsence_positive_structural_exact_source_status":
            "verified_by_full_raw_replay_and_positive_span_audit",
            "legal_semantic_truth_certified": False}


def evaluate(score_report_path: pathlib.Path) -> dict[str, Any]:
    """Reverify a supervisor score report and apply only protocol Gate 1 minima."""
    root = _safe_root(score_report_path)
    protocol = _protocol_identity()
    _require(ABSENCE_ITEMS == scorer.pilot.base.full_record_output.ABSENCE_ITEMS,
             "gate absence-item set differs from source-output validator")
    gate_code_sha = _sha_file(pathlib.Path(__file__).resolve())
    source_hashes_before = {key: _sha_file(path.resolve()) for key, path in SOURCE_FILES.items()}
    report_path = root / "score_report.json"
    score_report_sha = _sha_file(report_path)
    stored = _json_file(report_path)
    plan = _json_file(root / "plan.json")
    score_receipt = _json_file(root / "score_receipt.json")
    _require(plan.get("schema_version") == SUPERVISOR_SCHEMA
             and plan.get("phase") == "blind_dev200_teacher_quality_diagnostic_only_not_gold"
             and plan.get("qualification") == "none_not_gold"
             and plan.get("output_root") == str(root)
             and plan.get("window_starts") == list(WINDOW_STARTS),
             "supervisor plan identity or phase differs")
    _require(plan.get("plan_sha256") == _sha_object({key: value for key, value in plan.items()
                                                    if key != "plan_sha256"}),
             "supervisor plan self-hash differs")
    _require(plan.get("source_hashes") == source_hashes_before,
             "supervisor planned code hashes differ from current scorer/verifier/source")
    _require(score_receipt == {
        "schema_version": SUPERVISOR_SCHEMA, "plan_sha256": plan["plan_sha256"],
        "report_sha256": score_report_sha, "status": stored.get("status"),
        "gold_qualification": False,
    }, "score receipt does not bind exact report and plan")
    _require(stored.get("verification_code_sha256") == {
        "scorer_sha256": source_hashes_before["scorer"],
        "verifier_sha256": source_hashes_before["verifier"],
    }, "score report scorer/verifier source hashes differ")
    _require(stored.get("frozen_teacher_tuple_sha256") == _sha_object(_frozen_tuple_from_plan(plan)),
             "score report frozen teacher tuple differs from plan")
    _require(stored.get("organizer_input_sha256") == plan.get("input_sha256"),
             "score report organizer input differs from plan")
    windows = stored.get("windows")
    _require(isinstance(windows, list) and len(windows) == 10 and
             isinstance(plan.get("windows"), list) and len(plan["windows"]) == 10
             and all(isinstance(row, Mapping) for row in (*windows, *plan["windows"])),
             "exact ten score and plan windows required")
    pairs: list[tuple[pathlib.Path, pathlib.Path]] = []
    for index, start in enumerate(WINDOW_STARTS):
        tag = f"window-{start:03d}-{start + 19:03d}"
        run, archive = root / "runs" / tag, root / "archives" / tag
        proof = windows[index]
        _require(isinstance(proof, Mapping) and proof.get("start_index") == start
                 and proof.get("run_dir") == str(run)
                 and proof.get("source_archive") == str(archive)
                 and proof.get("records_verified") == 20,
                 f"window {start}: score proof/path differs")
        _require(plan["windows"][index].get("start_index") == start
                 and len(plan["windows"][index].get("selected_ids", [])) == 20,
                 f"window {start}: plan selection differs")
        completed = _json_file(root / "completed" / f"{tag}.json")
        _require(isinstance(completed.get("verifier_proof"), Mapping),
                 f"window {start}: completed verifier proof invalid")
        _require(completed.get("schema_version") == SUPERVISOR_SCHEMA
                 and completed.get("status") == "executed_rows_structurally_verified_not_gold"
                 and completed.get("plan_sha256") == plan["plan_sha256"]
                 and completed.get("start_index") == start
                 and completed.get("run_dir") == str(run)
                 and completed.get("source_archive") == str(archive)
                 and completed.get("run_manifest_sha256") == proof.get("run_manifest_sha256")
                 and completed.get("verifier_proof", {}).get("archive") == proof.get("archive")
                 and completed.get("gold_qualification") is False,
                 f"window {start}: completed receipt does not match score proof")
        _require(completed.get("run_tree_sha256") == _tree_hash(run)
                 and completed.get("archive_tree_sha256") == _tree_hash(archive),
                 f"window {start}: run/archive bytes differ from completed receipt")
        pairs.append((run, archive))
    # score_windows replays raw stdout and exact archived source before opening
    # official labels.  It invokes no model and returns a metrics-only report.
    replayed = scorer.score_windows(pairs)
    _require(replayed == stored, "fresh raw replay and saved score report differ")
    metric_summary = _checked_metrics(replayed)
    evidence = _evidence_summary(root, plan)
    _require(evidence["positive_cells"] == metric_summary["positive_predictions"]
             and evidence["nonabsence_positive_cells"]
             == metric_summary["nonabsence_positive_predictions"],
             "positive evidence audit coverage differs from verified metrics")
    _require(_sha_file(report_path) == score_report_sha
             and _sha_file(PROTOCOL) == protocol["sha256"]
             and _sha_file(pathlib.Path(__file__).resolve()) == gate_code_sha
             and {key: _sha_file(path.resolve()) for key, path in SOURCE_FILES.items()}
             == source_hashes_before, "gate inputs or source code changed during evaluation")
    for start, (run, archive) in zip(WINDOW_STARTS, pairs):
        tag = f"window-{start:03d}-{start + 19:03d}"
        completed = _json_file(root / "completed" / f"{tag}.json")
        _require(completed["run_tree_sha256"] == _tree_hash(run)
                 and completed["archive_tree_sha256"] == _tree_hash(archive),
                 f"window {start}: raw artifacts changed during gate")
    reasons: list[str] = []
    if metric_summary["unresolved_cells"]:
        reasons.append("unresolved_U_cells")
    macro = metric_summary["macro_positive_f1"]
    if macro is None or macro < MACRO_MIN:
        reasons.append("macro_positive_F1_below_0.90_or_unavailable")
    below_items = [item for item, value in metric_summary["per_item_positive_f1"].items()
                   if value is None or value < ITEM_MIN]
    if below_items:
        reasons.append("item_positive_F1_below_0.70_or_unavailable")
    passed = not reasons
    return {
        "schema_version": SCHEMA,
        "status": "gate1_minimum_dev_and_structural_source_pass_not_gold" if passed
                  else "gate1_minimum_dev_or_structure_fail_not_gold",
        "candidate_gate1_minimum_pass": passed,
        "qualified_for_gold_generation": False,
        "gold_or_semantic_truth_certified": False,
        "threshold_source": protocol,
        "thresholds": {"macro_positive_f1_min": MACRO_MIN, "each_item_positive_f1_min": ITEM_MIN,
                       "unresolved_cells_required": 0, "records_required": 200,
                       "cells_required": 4800,
                       "nonabsence_positive_evidence": "source-exact structural citation, <=500 chars"},
        "metrics": metric_summary,
        "evidence": evidence,
        "below_item_threshold": below_items,
        "failure_reasons": reasons,
        "provenance": {"score_report_sha256": score_report_sha,
                       "supervisor_plan_sha256": plan["plan_sha256"],
                       "gate_code_sha256": gate_code_sha,
                       "source_code_sha256": source_hashes_before,
                       "raw_windows_replayed": 10,
                       "model_calls_by_gate": 0},
        "interpretation": "Minimum dev200 competence and structural source citation only. Official dev was used during development; this is not unseen accuracy, legal semantic correctness, independent verifier qualification, or gold promotion.",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score-report", type=pathlib.Path, required=True,
                        help="Supervisor's score_report.json, with sibling plan/score receipt/completed windows")
    args = parser.parse_args(argv)
    try:
        result = evaluate(args.score_report)
    except (DevGateError, scorer.BatchDevScoreError, verifier.BatchVerificationError,
            OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        print(json.dumps({"schema_version": SCHEMA, "status": "gate1_integrity_unverified_not_gold",
                          "candidate_gate1_minimum_pass": False,
                          "qualified_for_gold_generation": False,
                          "gold_or_semantic_truth_certified": False,
                          "reason": f"{type(exc).__name__}: {exc}", "model_calls_by_gate": 0},
                         ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["candidate_gate1_minimum_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
