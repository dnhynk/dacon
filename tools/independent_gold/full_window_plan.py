"""Plan (never execute) organizer-order windows for a future 20k gold run.

The plan can be frozen before qualification.  It does not authorize creating
active full-run staging, calling a model, or skipping an opaque-alias canary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
from collections import Counter
from typing import Any, Mapping, Sequence

try:
    from tools.independent_gold import opaque_alias_canary, template_clusters
except ModuleNotFoundError:  # Direct execution from this directory.
    import opaque_alias_canary  # type: ignore[no-redef]
    import template_clusters  # type: ignore[no-redef]


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "dacon.independent.full_window_plan.v1"
ASSIGNMENT_METHOD = "organizer_order_500_contiguous_windows_stride_shards_v1"
FULL_COUNT = 20_000
WINDOW_SIZE = 500
WINDOW_COUNT = 40
DEFAULT_SHARDS = 16
ROLES = ("candidate", "verifier")
DEFAULT_CANARY_PLAN = (
    ROOT / "runs" / "self_label_20000_20260918" / "continuity_canary_v3" / "plan.json"
)
DEFAULT_INPUT = ROOT / "data_open" / "train_unlabeled.jsonl.gz"
DEFAULT_OUTPUT = (
    ROOT / "runs" / "self_label_20000_20260918" / "audit_v3" / "future_full_window_plan.json"
)
DEFAULT_STAGING_ROOT = (
    ROOT / "runs" / "self_label_20000_20260918" / "future_full_record_v1"
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_sha(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_string(path: pathlib.Path) -> str:
    path = path.resolve()
    root = ROOT.resolve()
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()


def _resolve_plan_path(value: str) -> pathlib.Path:
    path = pathlib.Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _build_layout(
    records: Sequence[Mapping[str, Any]],
    checkpoints: Sequence[Mapping[str, Any]],
    staging_root: pathlib.Path,
    *,
    window_size: int,
    shard_count: int,
) -> list[dict[str, Any]]:
    """Map exact source rows and boundary timing; also used by small tests."""

    _require(type(window_size) is int and window_size > 0, "invalid window size")
    _require(type(shard_count) is int and 1 <= shard_count <= window_size, "invalid shard count")
    _require(len(records) % window_size == 0, "records do not fill whole windows")
    count = len(records) // window_size
    _require(len(checkpoints) == count + 2, "canary boundary count does not fit windows")
    _require(checkpoints[1]["full_run_tasks_completed"] == 0, "missing pre-full boundary")
    _require(
        [row["full_run_tasks_completed"] for row in checkpoints[2:]]
        == [window_size * index for index in range(1, count + 1)],
        "canary interval boundaries do not align with windows",
    )
    windows: list[dict[str, Any]] = []
    for window_index in range(count):
        start = window_index * window_size
        end = start + window_size
        window_id = f"w{window_index + 1:03d}"
        window_records = [dict(record) for record in records[start:end]]
        shards: list[dict[str, Any]] = []
        for shard_index in range(shard_count):
            shard_id = f"s{shard_index:02d}"
            shard_records = window_records[shard_index::shard_count]
            stage_paths = {
                role: _path_string(staging_root / role / window_id / shard_id)
                for role in ROLES
            }
            shards.append(
                {
                    "shard_id": shard_id,
                    "shard_index": shard_index,
                    "stride": shard_count,
                    "organizer_positions": [row["organizer_position"] for row in shard_records],
                    "record_ids": [row["record_id"] for row in shard_records],
                    "record_sources_sha256": _sha(
                        [(row["record_id"], row["source_sha256"]) for row in shard_records]
                    ),
                    "role_staging_paths": stage_paths,
                }
            )
        windows.append(
            {
                "window_id": window_id,
                "window_index": window_index,
                "start_position": start,
                "end_position_exclusive": end,
                "record_count": len(window_records),
                "records": window_records,
                "window_record_sources_sha256": _sha(
                    [(row["record_id"], row["source_sha256"]) for row in window_records]
                ),
                "checkpoint_before": checkpoints[window_index + 1]["checkpoint_id"],
                "checkpoint_after": checkpoints[window_index + 2]["checkpoint_id"],
                "shards": shards,
            }
        )
    return windows


def _read_organizer_rows(
    path: pathlib.Path, *, expected_ids: Sequence[str]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for position, record in enumerate(template_clusters.read_jsonl_gz(path)):
        if position >= len(expected_ids) or record["id"] != expected_ids[position]:
            raise ValueError(f"organizer order/ID mismatch at position {position}")
        records.append(
            {
                "organizer_position": position,
                "record_id": record["id"],
                "source_sha256": template_clusters.sha256_object(record),
            }
        )
    _require(len(records) == len(expected_ids), "organizer record count differs from frozen canary")
    _require(len({row["record_id"] for row in records}) == len(records), "duplicate organizer IDs")
    return records


def _canary_epoch_observations(
    canary_plan_path: pathlib.Path, canary: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Bind the latest preserved epoch for each role without claiming a gate pass."""

    observations: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        paths = sorted((canary_plan_path.parent / role).glob("epoch-*.json"))
        if not paths:
            observations[role] = {
                "status": "not_observed",
                "continuity_qualified": False,
                "epoch_path": None,
                "epoch_file_sha256": None,
                "epoch_manifest_sha256": None,
                "new_epoch_and_requalification_required": None,
            }
            continue
        latest = paths[-1]
        epoch = opaque_alias_canary.load_epoch(latest, plan=canary)
        _require(epoch["annotator_role"] == role, f"{role}: epoch role differs")
        observations[role] = {
            "status": epoch["status"],
            "continuity_qualified": epoch["continuity_qualified"],
            "epoch_path": _path_string(latest),
            "epoch_file_sha256": _file_sha(latest),
            "epoch_manifest_sha256": epoch["manifest_sha256"],
            "new_epoch_and_requalification_required": epoch[
                "new_epoch_and_requalification_required"
            ],
        }
    return observations


def build_plan(
    *,
    canary_plan_path: pathlib.Path = DEFAULT_CANARY_PLAN,
    organizer_input_path: pathlib.Path = DEFAULT_INPUT,
    staging_root: pathlib.Path = DEFAULT_STAGING_ROOT,
    shard_count: int = DEFAULT_SHARDS,
    require_fresh_staging: bool = True,
) -> dict[str, Any]:
    """Build only a self-hashed schedule; no staging directory is created."""

    canary_plan_path = canary_plan_path.resolve()
    organizer_input_path = organizer_input_path.resolve()
    staging_root = staging_root.resolve()
    _require(staging_root != ROOT.resolve(), "staging root cannot be repository root")
    _require(
        staging_root.is_relative_to((ROOT / "runs").resolve()),
        "staging root must be inside repository runs/",
    )
    if require_fresh_staging:
        _require(not staging_root.exists(), "planned staging root already exists")
    canary = opaque_alias_canary.load_plan(canary_plan_path)
    frozen = canary["organizer_inputs"]["full_unlabeled"]
    _require(frozen["record_count"] == FULL_COUNT, "frozen canary is not for 20,000 records")
    _require(
        canary["schedule"]["full_run_task_count"] == FULL_COUNT
        and canary["schedule"]["interval_every"] == WINDOW_SIZE,
        "frozen canary does not have 500-record full-run intervals",
    )
    _require(
        _file_sha(organizer_input_path) == frozen["sha256"],
        "organizer input bytes differ from frozen canary",
    )
    expected_ids = canary["workload_order"]["unlabeled_full_run"]
    _require(len(expected_ids) == FULL_COUNT, "frozen canary ID count differs")
    records = _read_organizer_rows(organizer_input_path, expected_ids=expected_ids)
    _require(_sha(expected_ids) == frozen["ordered_ids_sha256"], "frozen ordered ID hash differs")
    checkpoints = canary["schedule"]["checkpoints"]
    _require(
        checkpoints[1]["checkpoint_id"] == "c001-pre-full-run"
        and checkpoints[-1]["checkpoint_id"] == "c041-post-full-run",
        "frozen checkpoint IDs differ from 40-window schedule",
    )
    windows = _build_layout(
        records,
        checkpoints,
        staging_root,
        window_size=WINDOW_SIZE,
        shard_count=shard_count,
    )
    _require(len(windows) == WINDOW_COUNT, "full-run window count differs")
    all_window_ids = [row["record_id"] for window in windows for row in window["records"]]
    _require(all_window_ids == expected_ids, "window coverage is not exact organizer order")
    all_shard_positions = [
        position
        for window in windows
        for shard in window["shards"]
        for position in shard["organizer_positions"]
    ]
    _require(
        Counter(all_shard_positions) == Counter(range(FULL_COUNT)),
        "shard coverage is not exact once-only",
    )
    all_stage_paths = [
        path
        for window in windows
        for shard in window["shards"]
        for path in shard["role_staging_paths"].values()
    ]
    _require(len(all_stage_paths) == len(set(all_stage_paths)), "role staging paths overlap")
    epoch_observations = _canary_epoch_observations(canary_plan_path, canary)
    blocked_roles = [
        role for role, observation in epoch_observations.items()
        if observation["status"] in {"closed_failure", "closed_drift"}
    ]

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "plan_kind": "future_full_run_plan_only_no_model_calls",
        "execution_authorized": False,
        "qualification_gate": {
            "required_before_any_active_full_run_artifact": True,
            "status_at_plan_creation": "not_asserted_qualified",
            "role_tuples_qualified_for_full_run": False,
            "referenced_canary_plan_usable_for_execution": False,
            "blocked_roles": blocked_roles,
            "epoch_observations": epoch_observations,
            "schedule_only_validity": "organizer source and timing layout only",
            "next_required_action": (
                "fail-closed canary: freeze a new v4-or-later plan, pass c000 and "
                "fresh official-dev qualification, then regenerate this window plan"
                if blocked_roles else
                "pass c000 and fresh official-dev qualification under this exact "
                "canary plan before any active full-run artifact"
            ),
            "required_pre_full_checkpoint_id": checkpoints[1]["checkpoint_id"],
        },
        "source_module_sha256": _file_sha(pathlib.Path(__file__)),
        "canary_plan": {
            "path": _path_string(canary_plan_path),
            "file_sha256": _file_sha(canary_plan_path),
            "manifest_sha256": canary["manifest_sha256"],
            "policy_version": canary["policy_version"],
            "checkpoint_ids_sha256": _sha([row["checkpoint_id"] for row in checkpoints]),
        },
        "organizer_input": {
            "path": _path_string(organizer_input_path),
            "sha256": frozen["sha256"],
            "record_count": FULL_COUNT,
            "ordered_ids_sha256": frozen["ordered_ids_sha256"],
            "ordered_record_sources_sha256": _sha(
                [(row["record_id"], row["source_sha256"]) for row in records]
            ),
        },
        "assignment": {
            "method": ASSIGNMENT_METHOD,
            "window_size": WINDOW_SIZE,
            "window_count": WINDOW_COUNT,
            "shard_count_per_window": shard_count,
            "role_count": len(ROLES),
            "roles": list(ROLES),
            "stage_path_kind": "planned_only_not_created",
            "staging_root": _path_string(staging_root),
        },
        "windows": windows,
    }
    payload["manifest_sha256"] = _sha(payload)
    return payload


def verify_plan(
    plan: Mapping[str, Any],
    *,
    canary_plan_path: pathlib.Path | None = None,
    organizer_input_path: pathlib.Path | None = None,
) -> dict[str, int]:
    """Recompute from frozen canary and source; fail on any altered assignment."""

    _require(plan.get("schema_version") == SCHEMA_VERSION, "wrong window plan schema")
    _require(plan.get("manifest_sha256") == _sha({k: v for k, v in plan.items() if k != "manifest_sha256"}), "window plan self-hash differs")
    canary_path = canary_plan_path or _resolve_plan_path(plan["canary_plan"]["path"])
    source_path = organizer_input_path or _resolve_plan_path(plan["organizer_input"]["path"])
    expected = build_plan(
        canary_plan_path=canary_path,
        organizer_input_path=source_path,
        staging_root=_resolve_plan_path(plan["assignment"]["staging_root"]),
        shard_count=plan["assignment"]["shard_count_per_window"],
        require_fresh_staging=False,
    )
    _require(dict(plan) == expected, "window plan differs from organizer/canary replay")
    return {
        "windows": WINDOW_COUNT,
        "records_per_role": FULL_COUNT,
        "shards_per_window": expected["assignment"]["shard_count_per_window"],
        "canary_checkpoints": WINDOW_COUNT + 2,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("build", "verify"))
    parser.add_argument("--canary-plan", type=pathlib.Path, default=DEFAULT_CANARY_PLAN)
    parser.add_argument("--input", type=pathlib.Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--staging-root", type=pathlib.Path, default=DEFAULT_STAGING_ROOT)
    parser.add_argument("--shards", type=int, default=DEFAULT_SHARDS)
    args = parser.parse_args(argv)
    if args.mode == "build":
        _require(not args.output.exists(), f"refusing to overwrite plan: {args.output}")
        plan = build_plan(
            canary_plan_path=args.canary_plan,
            organizer_input_path=args.input,
            staging_root=args.staging_root,
            shard_count=args.shards,
        )
        _require(
            not args.output.resolve().is_relative_to(args.staging_root.resolve()),
            "plan output cannot be inside planned active staging root",
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(_canonical({"output": str(args.output), **verify_plan(plan)}))
    else:
        plan = json.loads(args.output.read_text(encoding="utf-8"))
        print(
            _canonical(
                verify_plan(
                    plan,
                    canary_plan_path=args.canary_plan,
                    organizer_input_path=args.input,
                )
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
