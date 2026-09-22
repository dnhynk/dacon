"""Read-only operational progress for the organizer's unlabeled 20k annotation runs.

This is not a gold-label report. It reads only run metadata, receipts, summaries,
and successful checkpoint envelopes; it never opens the organizer records or labels.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path("runs/self_label_20000_20260918")
TARGET = 20_000
RUN_NAME = re.compile(r"^(?:provisional_(?:sol|claude)_shard\d{3}(?:_|$)|recovery_claude_[A-Za-z0-9_-]+$)")
SUPERVISOR_SCHEMA = "dacon.independent.claude_shard_supervisor_plan.v1"
SOURCE_BASENAME = "train_unlabeled.jsonl.gz"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _source_name(path: Any) -> str:
    return str(path).replace("\\", "/").rsplit("/", 1)[-1]


def _discover(root: Path) -> tuple[list[Path], int]:
    runs: set[Path] = set()
    excluded = 0
    for child in root.iterdir() if root.is_dir() else ():
        if not child.is_dir():
            continue
        if child.is_symlink():
            excluded += 1
            continue
        if RUN_NAME.match(child.name):
            runs.add(child)
        else:
            excluded += 1
        plan = _read_json(child / "plan.json")
        if plan and plan.get("schema_version") == SUPERVISOR_SCHEMA:
            shards = child / "shards"
            if shards.is_symlink():
                continue
            for shard in shards.iterdir() if shards.is_dir() else ():
                if shard.is_dir() and not shard.is_symlink() and re.fullmatch(r"shard-\d{3}-of-200", shard.name):
                    runs.add(shard)
    return sorted(runs), excluded


def _manifest_details(path: Path) -> tuple[str, list[str], str, str] | None:
    manifest = _read_json(path / "run_manifest.json")
    if not manifest or manifest.get("phase") != "unlabeled_20000":
        return None
    if "cohort_plan" in manifest:
        provider = "sol"
        selected = manifest.get("cohort_plan", {}).get("selected_ids")
        source = manifest.get("record_input", {})
        bundle = manifest.get("imported_source_bundle", {}).get("bundle_sha256")
    else:
        provider = "claude"
        selected = manifest.get("selected_ids")
        source = {"path": manifest.get("input_path"), "sha256": manifest.get("input_sha256")}
        bundle = manifest.get("source_bundle", {}).get("bundle_sha256")
        if manifest.get("execute") is False:
            return None
    if (
        _source_name(source.get("path")) != SOURCE_BASENAME
        or not isinstance(source.get("sha256"), str)
        or not isinstance(bundle, str)
        or not isinstance(selected, list)
        or not selected
        or any(not isinstance(value, str) or not value for value in selected)
        or len(set(selected)) != len(selected)
    ):
        return None
    return provider, selected, bundle, source["sha256"]


def _receipt_class(provider: str, receipt: dict[str, Any]) -> str:
    if receipt.get("status") == "ok":
        return "ok"
    if provider == "claude":
        return "content" if receipt.get("error_class") == "content" else "safety"
    errors = receipt.get("validation_errors")
    audit = receipt.get("event_audit") or {}
    if (
        isinstance(errors, list)
        and bool(errors)
        and all(isinstance(error, str) and error.startswith("invalid_full_record_output:") for error in errors)
        and isinstance(audit, dict)
        and not audit.get("unsafe_items")
        and not audit.get("service_errors")
    ):
        return "content"
    return "safety"


def _checkpoint_rows(path: Path, selected: set[str], anomalies: list[str]) -> dict[str, list[tuple[tuple[str, str], ...]]]:
    rows: dict[str, list[tuple[tuple[str, str], ...]]] = defaultdict(list)
    checkpoint = path / "full_records.jsonl"
    if not checkpoint.is_file():
        return rows
    try:
        with checkpoint.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    row = json.loads(line)
                    record_id = row["id"]
                    cells = row["ledger"]["cells"]
                    decisions = tuple(sorted((cell["item"], str(cell["label"])) for cell in cells))
                    if (record_id not in selected or row.get("status") not in {"ok", "provisional_unqualified"}
                            or len(decisions) != 24 or len({item for item, _ in decisions}) != 24):
                        raise ValueError("checkpoint identity or item count mismatch")
                    rows[record_id].append(decisions)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    anomalies.append(f"{path.name}:invalid_checkpoint_line:{line_number}")
    except (OSError, UnicodeError):
        anomalies.append(f"{path.name}:unreadable_checkpoint")
    return rows


def collect(root: Path = ROOT, *, with_codex_quota: bool = False) -> dict[str, Any]:
    """Take a best-effort snapshot of immutable metadata and live run envelopes."""
    runs, excluded = _discover(root)
    anomalies: list[str] = []
    provider_stats: dict[str, dict[str, Any]] = {}
    all_vectors: dict[str, dict[str, list[tuple[tuple[str, str], ...]]]] = {
        "sol": defaultdict(list), "claude": defaultdict(list)
    }
    for provider in ("sol", "claude"):
        provider_stats[provider] = {
            "runs": 0,
            "shards": {"complete_verified": 0, "complete_inferred": 0, "partial": 0},
            "records": {"attempted_unique": 0, "ok_unique": 0, "ids_with_content_error_receipt": 0,
                        "ids_with_safety_error_receipt": 0},
            "receipt_attempts": {"ok": 0, "content": 0, "safety": 0},
            "source_bundles": [],
        }
    selected_by_provider: dict[str, Counter[str]] = {"sol": Counter(), "claude": Counter()}
    attempted_run_counts: dict[str, Counter[str]] = {"sol": Counter(), "claude": Counter()}
    attempted_by_provider: dict[str, set[str]] = {"sol": set(), "claude": set()}
    ok_by_provider: dict[str, set[str]] = {"sol": set(), "claude": set()}
    content_by_provider: dict[str, set[str]] = {"sol": set(), "claude": set()}
    safety_by_provider: dict[str, set[str]] = {"sol": set(), "claude": set()}
    bundles: dict[str, dict[str, dict[str, Any]]] = {"sol": {}, "claude": {}}
    input_hashes: set[str] = set()

    for run in runs:
        details = _manifest_details(run)
        if details is None:
            excluded += 1
            anomalies.append(f"{run.name}:not_admitted_unlabeled_manifest")
            continue
        provider, selected, bundle, input_hash = details
        selected_set = set(selected)
        input_hashes.add(input_hash)
        stat = provider_stats[provider]
        stat["runs"] += 1
        selected_by_provider[provider].update(selected)
        bundle_stat = bundles[provider].setdefault(bundle, {"sha256": bundle, "runs": 0, "attempted_ids": set(), "ok_ids": set()})
        bundle_stat["runs"] += 1
        run_attempted: set[str] = set()
        run_content: set[str] = set()
        run_safety: set[str] = set()
        run_receipts: dict[str, set[str]] = defaultdict(set)
        for receipt_path in sorted((run / "tasks").glob("*/v1-24/attempts/attempt-*/receipt.json")):
            receipt = _read_json(receipt_path)
            if receipt is None:
                anomalies.append(f"{run.name}:unreadable_receipt")
                continue
            record_id = receipt.get("record_id") if provider == "claude" else receipt.get("id")
            if record_id not in selected_set:
                anomalies.append(f"{run.name}:receipt_id_outside_manifest")
                continue
            classification = _receipt_class(provider, receipt)
            stat["receipt_attempts"][classification] += 1
            run_attempted.add(record_id)
            run_receipts[record_id].add(classification)
            if classification == "content":
                run_content.add(record_id)
            if classification == "safety":
                run_safety.add(record_id)
        checkpoints = _checkpoint_rows(run, selected_set, anomalies)
        run_ok = set(checkpoints)
        for record_id, vectors in checkpoints.items():
            all_vectors[provider][record_id].extend(vectors)
            if "ok" not in run_receipts.get(record_id, set()):
                anomalies.append(f"{run.name}:checkpoint_without_ok_receipt")
        if any("ok" in classes and record_id not in run_ok for record_id, classes in run_receipts.items()):
            anomalies.append(f"{run.name}:ok_receipt_without_checkpoint")
        attempted_by_provider[provider].update(run_attempted)
        attempted_run_counts[provider].update(run_attempted)
        ok_by_provider[provider].update(run_ok)
        content_by_provider[provider].update(run_content)
        safety_by_provider[provider].update(run_safety)
        bundle_stat["attempted_ids"].update(run_attempted)
        bundle_stat["ok_ids"].update(run_ok)
        summary = _read_json(run / "run_summary.json")
        if provider == "claude":
            complete = bool(
                summary
                and summary.get("records_selected") == len(selected)
                and summary.get("calls_attempted") == len(run_attempted) == len(selected)
                and summary.get("safety_errors") == 0
                and summary.get("tasks_ok") == len(run_ok)
                and len(run_ok) == len(selected)
            )
            stat["shards"]["complete_verified" if complete else "partial"] += 1
        else:
            # The Sol runner has no durable summary; receipt coverage is only an inference.
            complete = len(run_attempted) == len(selected) and len(run_ok) == len(selected)
            stat["shards"]["complete_inferred" if complete else "partial"] += 1

    for provider in ("sol", "claude"):
        stat = provider_stats[provider]
        stat["records"] = {
            "attempted_unique": len(attempted_by_provider[provider]),
            "ok_unique": len(ok_by_provider[provider]),
            "ids_with_content_error_receipt": len(content_by_provider[provider]),
            "ids_with_safety_error_receipt": len(safety_by_provider[provider]),
        }
        stat["source_bundles"] = [
            {"sha256": key, "runs": value["runs"], "attempted_unique": len(value["attempted_ids"]),
             "ok_unique": len(value["ok_ids"])}
            for key, value in sorted(bundles[provider].items())
        ]
    conflicting_within = {
        provider: sum(len(set(vectors)) > 1 for vectors in all_vectors[provider].values())
        for provider in ("sol", "claude")
    }
    conflicting_cross = sum(
        bool(set(sol_vectors) - set(all_vectors["claude"][record_id])
             or set(all_vectors["claude"][record_id]) - set(sol_vectors))
        for record_id, sol_vectors in all_vectors["sol"].items()
        if record_id in all_vectors["claude"]
    )
    if len(input_hashes) > 1:
        anomalies.append("multiple_organizer_input_hashes")
    report: dict[str, Any] = {
        "schema_version": "dacon.independent.unlabeled_progress.v1",
        "observed_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {"target_records": TARGET, "designation": "provisional_operational_progress_not_gold",
                  "live_snapshot_is_not_atomic": True,
                  "read_only_artifacts": ["run_manifest.json", "receipt.json", "full_records.jsonl", "run_summary.json"]},
        "providers": provider_stats,
        "coverage": {
            "attempted_unique_any": len(attempted_by_provider["sol"] | attempted_by_provider["claude"]),
            "ok_unique_any": len(ok_by_provider["sol"] | ok_by_provider["claude"]),
            "ok_by_both": len(ok_by_provider["sol"] & ok_by_provider["claude"]),
            "selected_duplicate_ids_within_provider": {
                provider: sum(count > 1 for count in selected_by_provider[provider].values())
                for provider in ("sol", "claude")
            },
            "attempted_duplicate_ids_across_runs": {
                provider: sum(count > 1 for count in attempted_run_counts[provider].values())
                for provider in ("sol", "claude")
            },
            "decision_conflict_ids_within_provider": conflicting_within,
            "decision_conflict_ids_cross_provider": conflicting_cross,
        },
        "excluded_non_unlabeled_directories": excluded,
        "anomalies": {"count": len(anomalies), "sample": anomalies[:10]},
    }
    if with_codex_quota:
        try:
            from tools.independent_gold.codex_quota_read import read_weekly_quota
        except ModuleNotFoundError as exc:
            if exc.name != "tools":
                raise
            # Support the documented `python -B tools/independent_gold/...py` form.
            from codex_quota_read import read_weekly_quota
        report["codex_quota"] = read_weekly_quota()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--with-codex-quota", action="store_true", help="Separately read official Codex quota metadata")
    args = parser.parse_args()
    print(json.dumps(collect(args.root, with_codex_quota=args.with_codex_quota), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
