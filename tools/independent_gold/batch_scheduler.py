"""CPU-only, fail-closed plan for bounded provisional 20k annotation batches.

Version 1 deliberately does not execute a model.  It freezes organizer-order
100-record shards and source/model/prompt identities, with fresh future output
paths.  A later execution adapter must verify this immutable plan and every
saved receipt before it may reuse a vote or retry only missing IDs.  The two
already-running first-batch directories are never staging targets here.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import pathlib
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

try:
    from tools.independent_gold import review_ledger
except ModuleNotFoundError:  # Direct execution from this directory.
    import review_ledger  # type: ignore[no-redef]


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "dacon.independent.batch_schedule_plan.v1"
DEFAULT_SOURCE = ROOT / "data_open" / "train_unlabeled.jsonl.gz"
EXPECTED_RECORDS = 20_000
SHARD_SIZE = 100
MAX_INFLIGHT_CAP = 4
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ACTIVE_FIRST_BATCHES = (
    ROOT / "runs" / "self_label_20000_20260918" / "provisional_sol_shard000_of200_v1",
    ROOT / "runs" / "self_label_20000_20260918" / "provisional_claude_shard000_first10_v1",
)


class BatchScheduleError(ValueError):
    """A schedule cannot be frozen or replayed safely."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BatchScheduleError(message)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_sha(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hash(value: Any, context: str) -> str:
    _require(isinstance(value, str) and HEX64.fullmatch(value) is not None, f"{context}: invalid SHA-256")
    return value


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise BatchScheduleError(f"non-standard JSON constant {value!r}")


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw, object_pairs_hook=_pairs_without_duplicates, parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BatchScheduleError(f"invalid JSON: {path}") from exc
    _require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def _source_rows(path: pathlib.Path) -> list[dict[str, str | int]]:
    opener = gzip.open if path.suffix.lower() == ".gz" else pathlib.Path.open
    handle = gzip.open(path, "rt", encoding="utf-8") if opener is gzip.open else path.open("r", encoding="utf-8")
    rows: list[dict[str, str | int]] = []
    seen: set[str] = set()
    with handle:
        for position, line in enumerate(handle):
            _require(bool(line.strip()), f"blank organizer row at position {position}")
            try:
                record = json.loads(
                    line,
                    object_pairs_hook=_pairs_without_duplicates,
                    parse_constant=_reject_constant,
                )
            except json.JSONDecodeError as exc:
                raise BatchScheduleError(f"invalid organizer JSON at position {position}") from exc
            _require(isinstance(record, dict), f"organizer row {position} is not an object")
            try:
                review_ledger._validate_organizer_record(record)
            except review_ledger.LedgerValidationError as exc:
                raise BatchScheduleError(f"organizer row {position}: {exc}") from exc
            record_id = record.get("id")
            _require(isinstance(record_id, str) and bool(record_id), f"invalid organizer ID at {position}")
            _require(record_id not in seen, f"duplicate organizer ID {record_id}")
            seen.add(record_id)
            rows.append(
                {
                    "organizer_position": position,
                    "record_id": record_id,
                    "source_sha256": _sha(record),
                }
            )
    return rows


def _role_identity(path: pathlib.Path, *, role: str, organizer_sha256: str) -> dict[str, Any]:
    manifest = _read_json(path)
    schema = manifest.get("schema_version")
    _require(manifest.get("phase") == "unlabeled_20000", f"{role}: wrong phase")
    _require(manifest.get("pass_kind") == "blind_first_pass", f"{role}: not a blind first pass")
    if role == "candidate":
        _require(schema == "dacon.independent.codex_cli_runner.v2", "candidate: expected Codex runner v2")
        _require(manifest.get("annotator_role") == "candidate", "candidate: wrong role")
        model = manifest.get("model_identity")
        prompt = manifest.get("prompt_lineage")
        semantic = manifest.get("semantic_config")
        source = manifest.get("record_input")
        _require(all(isinstance(x, Mapping) for x in (model, prompt, semantic, source)), "candidate: missing identity fields")
        _require(model.get("provider") == "openai", "candidate: unexpected provider")
        _hash(prompt.get("lineage_sha256"), "candidate prompt lineage")
        _hash(manifest.get("tuple_sha256"), "candidate tuple")
        _require(source.get("sha256") == organizer_sha256, "candidate source hash differs")
        identity = {
            "backend": "codex_cli",
            "provider": model.get("provider"),
            "model_identity": model,
            "prompt_lineage": prompt,
            "semantic_config": semantic,
            "source_bundle": manifest.get("imported_source_bundle"),
            "runner_source": manifest.get("runner_source"),
            "tuple_sha256": manifest.get("tuple_sha256"),
        }
    else:
        _require(schema == "dacon.independent.claude_full_record_run.v2", "verifier: expected Claude runner v2")
        _require(manifest.get("requested_model") and manifest.get("expected_observed_model"), "verifier: model identity missing")
        _require(manifest.get("no_tools") is True, "verifier: tools are not disabled")
        _require(manifest.get("input_sha256") == organizer_sha256, "verifier source hash differs")
        for key in (
            "prompt_argument_sha256", "static_system_prefix_sha256",
            "rubric_source_sha256", "schema_sha256",
        ):
            _hash(manifest.get(key), f"verifier {key}")
        identity = {
            "backend": "claude_cli",
            "provider": "anthropic",
            "requested_model": manifest.get("requested_model"),
            "expected_observed_model": manifest.get("expected_observed_model"),
            "model_identity_mode": manifest.get("model_identity_mode"),
            "resolved_revision": manifest.get("resolved_revision"),
            "effort": manifest.get("effort"),
            "no_tools": manifest.get("no_tools"),
            "session_persistence": manifest.get("session_persistence"),
            "prompt_protocol": manifest.get("prompt_protocol"),
            "prompt_argument_sha256": manifest.get("prompt_argument_sha256"),
            "static_system_prefix_sha256": manifest.get("static_system_prefix_sha256"),
            "rubric_source_sha256": manifest.get("rubric_source_sha256"),
            "rubric_projection_sha256": manifest.get("rubric_projection_sha256"),
            "context_mode": manifest.get("context_mode"),
            "compact_projection_schema": manifest.get("compact_projection_schema"),
            "source_context": manifest.get("source_context"),
            "source_bundle": manifest.get("source_bundle"),
            "schema_sha256": manifest.get("schema_sha256"),
            "cli": manifest.get("cli"),
        }
    return {
        "reference_manifest_path": str(path),
        "reference_manifest_sha256": _file_sha(path),
        "source_sha256": organizer_sha256,
        "identity": identity,
        "identity_sha256": _sha(identity),
        "identity_cannot_attest_opaque_alias_revision": True,
    }


def _safe_output_root(path: pathlib.Path, *, require_fresh: bool) -> None:
    resolved = path.resolve()
    if require_fresh:
        _require(not resolved.exists(), f"future output root already exists: {resolved}")
    protected = (
        ROOT,
        ROOT / "data_open",
        ROOT / "submission",
        *ACTIVE_FIRST_BATCHES,
    )
    for item in protected:
        exact = item.resolve()
        _require(resolved != exact, f"future output root is protected: {exact}")
    for live in ACTIVE_FIRST_BATCHES:
        _require(
            not resolved.is_relative_to(live.resolve())
            and not live.resolve().is_relative_to(resolved),
            f"future output root overlaps active first batch: {live}",
        )
    _require(not resolved.is_relative_to((ROOT / "data_open").resolve()), "future output root is inside organizer data")
    _require(not resolved.is_relative_to((ROOT / "submission").resolve()), "future output root is inside production runtime")


def build_plan(
    *,
    records_path: pathlib.Path,
    candidate_manifest_path: pathlib.Path,
    verifier_manifest_path: pathlib.Path,
    future_output_root: pathlib.Path,
    expected_records: int = EXPECTED_RECORDS,
    shard_size: int = SHARD_SIZE,
    max_inflight: int = 2,
    _allow_existing_future_root: bool = False,
) -> dict[str, Any]:
    """Freeze a CPU-only 200×100 schedule; never stage tasks or call a model."""

    records_path = pathlib.Path(records_path).resolve()
    candidate_manifest_path = pathlib.Path(candidate_manifest_path).resolve()
    verifier_manifest_path = pathlib.Path(verifier_manifest_path).resolve()
    future_output_root = pathlib.Path(future_output_root).resolve()
    _require(all(path.is_file() for path in (records_path, candidate_manifest_path, verifier_manifest_path)), "an input file is missing")
    _require(len({records_path, candidate_manifest_path, verifier_manifest_path}) == 3, "input paths overlap")
    _require(type(expected_records) is int and expected_records > 0, "expected_records must be positive")
    _require(type(shard_size) is int and shard_size > 0, "shard_size must be positive")
    _require(type(max_inflight) is int and 1 <= max_inflight <= MAX_INFLIGHT_CAP, "max_inflight exceeds safe plan cap")
    _safe_output_root(future_output_root, require_fresh=not _allow_existing_future_root)
    rows = _source_rows(records_path)
    _require(len(rows) == expected_records, f"organizer count {len(rows)} differs from expected {expected_records}")
    _require(len(rows) % shard_size == 0, "organizer count must fill complete shards")
    organizer_sha = _file_sha(records_path)
    candidate = _role_identity(candidate_manifest_path, role="candidate", organizer_sha256=organizer_sha)
    verifier = _role_identity(verifier_manifest_path, role="verifier", organizer_sha256=organizer_sha)
    shards: list[dict[str, Any]] = []
    for start in range(0, len(rows), shard_size):
        index = start // shard_size
        portion = rows[start : start + shard_size]
        shard_id = f"s{index:03d}"
        shards.append(
            {
                "shard_id": shard_id,
                "shard_index": index,
                "start_position": start,
                "end_position_exclusive": start + shard_size,
                "record_count": len(portion),
                "records": portion,
                "source_rows_sha256": _sha([(row["record_id"], row["source_sha256"]) for row in portion]),
                "future_fresh_attempt_paths": {
                    role: str(future_output_root / role / shard_id / "attempt_001")
                    for role in ("candidate", "verifier")
                },
            }
        )
    plan = {
        "schema_version": SCHEMA_VERSION,
        "plan_kind": "cpu_only_dry_run_no_model_calls",
        "execution_authorized": False,
        "execute_flag_status": "disabled_until_separate_scheduler_validation",
        "source": {
            "path": str(records_path),
            "sha256": organizer_sha,
            "record_count": len(rows),
            "ordered_id_source_sha256": _sha([(row["record_id"], row["source_sha256"]) for row in rows]),
        },
        "roles": {"candidate": candidate, "verifier": verifier},
        "assignment": {
            "method": "organizer_order_contiguous_100_v1",
            "shard_size": shard_size,
            "shard_count": len(shards),
            "max_inflight_planned": max_inflight,
            "fresh_future_output_root": str(future_output_root),
            "preserve_original_runs_and_receipts": True,
            "retry_missing_in_new_attempt_path_only": True,
        },
        "operational_boundaries": {
            "official_labels_read": False,
            "production_predictions_read": False,
            "subscription_limit_is_not_a_currency_budget": True,
            "prior_success_requires_receipt_and_source_replay_before_reuse": True,
            "a_usage_limit_or_identity_change_must_stop_execution": True,
            "opaque_alias_continuity_not_attested_by_this_plan": True,
        },
        "shards": shards,
    }
    plan["manifest_sha256"] = _sha(plan)
    return plan


def verify_plan(plan: Mapping[str, Any]) -> dict[str, int]:
    """Rebuild from frozen files and fail on source, model, prompt, or plan drift."""

    _require(isinstance(plan, Mapping), "plan is not an object")
    _require(plan.get("schema_version") == SCHEMA_VERSION, "wrong plan schema")
    manifest_hash = plan.get("manifest_sha256")
    _hash(manifest_hash, "plan manifest")
    _require(manifest_hash == _sha({key: value for key, value in plan.items() if key != "manifest_sha256"}), "plan self-hash differs")
    roles = plan.get("roles")
    source = plan.get("source")
    assignment = plan.get("assignment")
    _require(all(isinstance(x, Mapping) for x in (roles, source, assignment)), "plan components missing")
    expected = build_plan(
        records_path=pathlib.Path(source["path"]),
        candidate_manifest_path=pathlib.Path(roles["candidate"]["reference_manifest_path"]),
        verifier_manifest_path=pathlib.Path(roles["verifier"]["reference_manifest_path"]),
        future_output_root=pathlib.Path(assignment["fresh_future_output_root"]),
        expected_records=source["record_count"],
        shard_size=assignment["shard_size"],
        max_inflight=assignment["max_inflight_planned"],
        _allow_existing_future_root=True,
    )
    _require(dict(plan) == expected, "plan differs from frozen organizer/role identity replay")
    return {"records": source["record_count"], "shards": assignment["shard_count"]}


def write_plan_new(path: pathlib.Path, plan: Mapping[str, Any]) -> None:
    path = pathlib.Path(path).resolve()
    _require(not path.exists(), f"fresh-only plan path already exists: {path}")
    _require(
        not any(path.is_relative_to(live.resolve()) for live in ACTIVE_FIRST_BATCHES),
        "plan path overlaps an active first batch",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical(plan) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=pathlib.Path, default=DEFAULT_SOURCE)
    parser.add_argument("--candidate-manifest", type=pathlib.Path, required=True)
    parser.add_argument("--verifier-manifest", type=pathlib.Path, required=True)
    parser.add_argument("--future-output-root", type=pathlib.Path, required=True)
    parser.add_argument("--plan-output", type=pathlib.Path, required=True)
    parser.add_argument("--expected-records", type=int, default=EXPECTED_RECORDS)
    parser.add_argument("--shard-size", type=int, default=SHARD_SIZE)
    parser.add_argument("--max-inflight", type=int, default=2)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.execute:
        print("Execution disabled in v1: validate the dry-run plan and receipt-resume adapter first; no model call made.", file=sys.stderr)
        return 2
    try:
        _require(
            not args.plan_output.resolve().is_relative_to(args.future_output_root.resolve()),
            "plan output must be separate from future execution output root",
        )
        plan = build_plan(
            records_path=args.records,
            candidate_manifest_path=args.candidate_manifest,
            verifier_manifest_path=args.verifier_manifest,
            future_output_root=args.future_output_root,
            expected_records=args.expected_records,
            shard_size=args.shard_size,
            max_inflight=args.max_inflight,
        )
        write_plan_new(args.plan_output, plan)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Batch schedule failed: {exc}", file=sys.stderr)
        return 2
    print(_canonical({"plan_path": str(args.plan_output.resolve()), "records": plan["source"]["record_count"], "shards": len(plan["shards"]), "execution_authorized": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
