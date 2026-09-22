"""Offline, post-verification dev200 metrics for ten blind 20-record batch runs.

Every exact source archive and raw model artifact is replayed before this
module opens the official answer file.  It makes no model call, applies no
numeric quality threshold, and never designates a batch teacher as gold.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import pathlib
import statistics
import sys
from collections.abc import Mapping, Sequence
from typing import Any

try:
    from tools.independent_gold import claude_batch_pilot as pilot
    from tools.independent_gold import verify_claude_batch_pilot as verifier
except ModuleNotFoundError:  # Direct script invocation.
    import claude_batch_pilot as pilot  # type: ignore[no-redef]
    import verify_claude_batch_pilot as verifier  # type: ignore[no-redef]


SCHEMA = "dacon.independent.verified_batch_dev200_metrics.v1"
DEV_INPUT = pilot.DEV_INPUT
DEV_LABELS = pilot.ROOT / "data_open" / "dev_labels.csv"
RUNS_ROOT = pilot.ROOT / "runs"
ITEMS = tuple(f"v{index}" for index in range(1, 25))
WINDOW_STARTS = tuple(range(0, 200, 20))
FROZEN_FIELDS = (
    "mode", "batch_size", "model", "observed_model", "rubric_sha256",
    "rubric_source_sha256", "system_sha256", "input_sha256", "source_bundle",
    "pilot_source_sha256", "max_budget_usd", "timeout_seconds",
)


class BatchDevScoreError(ValueError):
    """The 200-record metric boundary cannot be trusted."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BatchDevScoreError(message)


def _sha_file(path: pathlib.Path) -> str:
    _require(path.is_file() and not path.is_symlink(), f"missing or symlinked file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_snapshot(root: pathlib.Path) -> dict[str, str]:
    _require(root.is_dir() and not root.is_symlink(), f"missing or symlinked directory: {root}")
    snapshot: dict[str, str] = {}
    for path in root.rglob("*"):
        _require(not path.is_symlink(), f"symlinked audit artifact: {path}")
        if path.is_file():
            snapshot[path.relative_to(root).as_posix()] = _sha_file(path)
    _require(bool(snapshot), f"empty audit artifact tree: {root}")
    return snapshot


def _checked_scanned_path(raw: pathlib.Path, *, labels: pathlib.Path) -> pathlib.Path:
    """Reject broad, linked, or label-containing paths before any tree read."""
    absolute = raw.absolute()
    _require(not any(part.is_symlink() for part in (absolute, *absolute.parents)),
             f"linked run/archive path is forbidden: {raw}")
    path = absolute.resolve()
    root = RUNS_ROOT.resolve()
    _require(path.is_dir() and path != root and path.is_relative_to(root),
             f"run/archive must be a child directory of repository runs: {raw}")
    _require(not labels.is_relative_to(path),
             f"official dev labels cannot be inside a scanned tree: {raw}")
    return path


def _organizer_ids() -> list[str]:
    path = DEV_INPUT.resolve()
    _require(path.is_file(), "organizer dev input missing")
    opener = gzip.open if path.suffix.lower() == ".gz" else pathlib.Path.open
    ids: list[str] = []
    with opener(path, "rb") as handle:
        for number, raw in enumerate(handle, 1):
            _require(bool(raw.strip()), f"organizer dev line {number} is blank")
            try:
                row = json.loads(raw.decode("utf-8"), object_pairs_hook=verifier._pairs,
                                 parse_constant=verifier._bad_constant)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BatchDevScoreError(f"organizer dev line {number} is invalid JSON") from exc
            record_id = row.get("id") if isinstance(row, Mapping) else None
            _require(isinstance(record_id, str) and bool(record_id),
                     f"organizer dev line {number} has no valid ID")
            ids.append(record_id)
    _require(len(ids) == 200 and len(set(ids)) == 200,
             "organizer dev is not 200 unique records")
    return ids


def _frozen_tuple(manifest: Mapping[str, Any]) -> dict[str, Any]:
    _require(all(field in manifest for field in FROZEN_FIELDS), "batch frozen tuple incomplete")
    cli = manifest.get("cli")
    _require(isinstance(cli, Mapping) and
             isinstance(cli.get("executable_sha256"), str) and
             isinstance(cli.get("version_output"), str), "batch CLI identity incomplete")
    return {**{field: manifest[field] for field in FROZEN_FIELDS},
            "cli_executable_sha256": cli["executable_sha256"],
            "cli_version_output": cli["version_output"]}


def _window_rows(run: pathlib.Path, manifest: Mapping[str, Any]) -> dict[str, dict[str, int | str]]:
    selected = manifest["selected_ids"]
    batch_size = manifest["batch_size"]
    rows: dict[str, dict[str, int | str]] = {}
    for local_index, record_id in enumerate(selected):
        batch_dir = run / f"batch-{local_index // batch_size:03d}"
        row_path = batch_dir / f"{record_id}.row.json"
        _require(row_path.parent.resolve() == batch_dir.resolve(), "unsafe batch row ID path")
        row = verifier._json_file(row_path)
        _require(row.get("id") == record_id and row.get("status") == "provisional_unqualified"
                 and row.get("batch_index") == local_index // batch_size,
                 f"batch row identity or index differs: {record_id}")
        ledger = row.get("ledger")
        cells = ledger.get("cells") if isinstance(ledger, Mapping) else None
        _require(isinstance(cells, list) and len(cells) == 24,
                 f"batch row has incomplete 24-cell ledger: {record_id}")
        values: dict[str, int | str] = {}
        for cell in cells:
            item = cell.get("item") if isinstance(cell, Mapping) else None
            label = cell.get("label") if isinstance(cell, Mapping) else None
            _require(item in ITEMS and item not in values and
                     ((type(label) is int and label in (0, 1)) or label == "U"),
                     f"batch row has duplicate or invalid decision: {record_id}")
            values[item] = label
        _require(set(values) == set(ITEMS), f"batch row items differ: {record_id}")
        _require(record_id not in rows, f"duplicate batch row: {record_id}")
        rows[record_id] = values
    return rows


def _load_official_labels(expected_ids: Sequence[str]) -> dict[str, dict[str, int]]:
    """This function is intentionally called only after all raw verification."""
    labels: dict[str, dict[str, int]] = {}
    with DEV_LABELS.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        _require(reader.fieldnames is not None and {"id", *ITEMS} <= set(reader.fieldnames),
                 "official dev label columns missing")
        for row in reader:
            record_id = row.get("id")
            _require(isinstance(record_id, str) and record_id not in labels,
                     "official dev label ID missing or duplicated")
            values: dict[str, int] = {}
            for item in ITEMS:
                value = row.get(item)
                _require(value in ("0", "1"), f"official dev label is not binary: {record_id}/{item}")
                values[item] = int(value)
            labels[record_id] = values
    _require(len(labels) == 200 and set(labels) == set(expected_ids),
             "official dev labels and verified organizer IDs differ")
    return labels


def score_windows(windows: Sequence[tuple[pathlib.Path, pathlib.Path]]) -> dict[str, Any]:
    """Return metrics only; any structural failure occurs before labels open."""
    _require(len(windows) == 10, "exactly ten 20-record windows are required")
    label_path = DEV_LABELS.resolve()
    prepared_paths = [
        (_checked_scanned_path(run, labels=label_path),
         _checked_scanned_path(archive, labels=label_path))
        for run, archive in windows
    ]
    all_scanned_paths = [path for pair in prepared_paths for path in pair]
    _require(all(
        left != right and not left.is_relative_to(right) and not right.is_relative_to(left)
        for index, left in enumerate(all_scanned_paths)
        for right in all_scanned_paths[index + 1:]
    ), "run/archive scan trees overlap")
    scorer_source = pathlib.Path(__file__).resolve()
    verifier_source = pathlib.Path(verifier.__file__).resolve()
    source_code_sha = {
        "scorer_sha256": _sha_file(scorer_source),
        "verifier_sha256": _sha_file(verifier_source),
    }
    organizer_ids = _organizer_ids()
    input_sha = _sha_file(DEV_INPUT.resolve())
    all_rows: dict[str, dict[str, int | str]] = {}
    window_proofs: list[dict[str, Any]] = []
    frozen: dict[str, Any] | None = None
    snapshots: list[tuple[pathlib.Path, pathlib.Path, dict[str, str], dict[str, str]]] = []
    seen_runs: set[pathlib.Path] = set()
    seen_archives: set[pathlib.Path] = set()
    for expected_start, (run, archive) in zip(WINDOW_STARTS, prepared_paths):
        _require(run not in seen_runs and archive not in seen_archives,
                 "duplicate run or source archive path")
        seen_runs.add(run)
        seen_archives.add(archive)
        before_run, before_archive = _tree_snapshot(run), _tree_snapshot(archive)
        proof = verifier.verify(run, archive)
        _require(proof.get("status") == "executed_rows_structurally_verified_not_gold"
                 and proof.get("gold_qualification") is False and
                 proof.get("model_calls_by_verifier") == 0 and
                 proof.get("selected_records") == 20 and
                 proof.get("records_ok") == 20 and
                 proof.get("records_content_error") == 0 and
                 proof.get("safety_errors") == 0,
                 f"window {expected_start} did not pass executed raw replay")
        manifest = verifier._json_file(run / "run_manifest.json")
        _require(manifest.get("manifest_sha256") == pilot.base.sha256_object({
            key: value for key, value in manifest.items() if key != "manifest_sha256"
        }), f"window {expected_start} manifest self-hash differs")
        _require(manifest.get("start_index") == expected_start and
                 manifest.get("selected_ids") == organizer_ids[expected_start:expected_start + 20] and
                 manifest.get("input_sha256") == input_sha and
                 manifest.get("execute") is True,
                 f"window {expected_start} start/source/selection differs")
        _require(proof.get("run_manifest_sha256") == manifest["manifest_sha256"],
                 f"window {expected_start} verifier manifest proof differs")
        _require(proof.get("start_index") == expected_start,
                 f"window {expected_start} verifier start proof differs")
        current_tuple = _frozen_tuple(manifest)
        if frozen is None:
            frozen = current_tuple
        else:
            _require(current_tuple == frozen,
                     f"window {expected_start} frozen mode/rubric/model/source tuple differs")
        rows = _window_rows(run, manifest)
        _require(len(rows) == 20 and not (set(rows) & set(all_rows)),
                 f"window {expected_start} missing or duplicate row")
        all_rows.update(rows)
        _require(_tree_snapshot(run) == before_run and _tree_snapshot(archive) == before_archive,
                 f"window {expected_start} artifacts changed during raw verification")
        snapshots.append((run, archive, before_run, before_archive))
        window_proofs.append({
            "start_index": expected_start, "run_dir": str(run),
            "source_archive": str(archive),
            "run_manifest_sha256": manifest["manifest_sha256"],
            "archive": proof.get("archive"), "records_verified": 20,
        })
    _require(set(all_rows) == set(organizer_ids) and len(all_rows) == 200,
             "verified windows do not cover exactly the 200 organizer records")
    # No official label file has been opened above this line.
    label_sha = _sha_file(DEV_LABELS)
    labels = _load_official_labels(organizer_ids)
    metrics: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for item in ITEMS:
        counts = {key: 0 for key in (
            "gold_positive", "gold_negative", "tp", "tn", "fp", "fn", "u",
            "u_gold_positive", "u_gold_negative",
        )}
        for record_id in organizer_ids:
            truth, pred = labels[record_id][item], all_rows[record_id][item]
            counts["gold_positive" if truth else "gold_negative"] += 1
            if pred == "U":
                counts["u"] += 1
                counts["u_gold_positive" if truth else "u_gold_negative"] += 1
                unresolved.append({"id": record_id, "item": item, "official": truth})
                continue
            kind = "tp" if truth and pred == 1 else (
                "tn" if not truth and pred == 0 else "fp" if pred == 1 else "fn"
            )
            counts[kind] += 1
            if kind in {"fp", "fn"}:
                errors.append({"id": record_id, "item": item, "official": truth,
                               "teacher": pred, "kind": kind})
        denominator = 2 * counts["tp"] + counts["fp"] + counts["fn"]
        counts["positive_f1"] = (
            None if counts["u"] else 0.0 if denominator == 0
            else 2 * counts["tp"] / denominator
        )
        metrics[item] = counts
    macro = (statistics.mean(metrics[item]["positive_f1"] for item in ITEMS)
             if not unresolved else None)
    for run, archive, before_run, before_archive in snapshots:
        _require(_tree_snapshot(run) == before_run and _tree_snapshot(archive) == before_archive,
                 "verified raw run or source archive changed before metric publication")
    _require(_sha_file(DEV_INPUT.resolve()) == input_sha,
             "organizer dev input changed before metric publication")
    _require(_sha_file(DEV_LABELS) == label_sha,
             "official dev labels changed during scoring")
    _require(_sha_file(scorer_source) == source_code_sha["scorer_sha256"] and
             _sha_file(verifier_source) == source_code_sha["verifier_sha256"],
             "scorer or verifier source changed during scoring")
    return {
        "schema_version": SCHEMA,
        "status": "failed_unresolved_cells" if unresolved else "metrics_only_pending_quality_policy_not_gold",
        "qualified_for_gold_generation": False,
        "quality_threshold_applied": False,
        "records": 200, "cells_expected": 4800, "cells_verified": 4800,
        "macro_positive_f1": macro,
        "per_item": metrics, "error_cells": errors, "unresolved_cells": unresolved,
        "frozen_teacher_tuple_sha256": pilot.base.sha256_object(frozen),
        "organizer_input_sha256": input_sha,
        "official_dev_labels_sha256": label_sha,
        "verification_code_sha256": source_code_sha,
        "windows": window_proofs,
        "model_calls_by_scorer": 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", action="append", nargs=2, metavar=("RUN_DIR", "SOURCE_ARCHIVE"),
                        required=True, help="Repeat ten times in organizer order: starts 0,20,...,180.")
    args = parser.parse_args(argv)
    try:
        report = score_windows([(pathlib.Path(run), pathlib.Path(archive))
                                for run, archive in args.window])
    except (BatchDevScoreError, verifier.BatchVerificationError, OSError, KeyError,
            TypeError, ValueError) as exc:
        print(json.dumps({
            "schema_version": SCHEMA, "status": "failed_integrity_before_quality_gate",
            "qualified_for_gold_generation": False,
            "reason": f"{type(exc).__name__}: {exc}", "model_calls_by_scorer": 0,
        }, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if not report["unresolved_cells"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
