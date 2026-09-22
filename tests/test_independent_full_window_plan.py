"""Plan-only full-run windows preserve source order and every canary boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.independent_gold import full_window_plan as plan_tool
from tools.independent_gold import opaque_alias_canary


def _mini_records(count: int) -> list[dict]:
    return [
        {
            "organizer_position": index,
            "record_id": f"D-{index:03d}",
            "source_sha256": f"{index:064x}",
        }
        for index in range(count)
    ]


def test_contiguous_windows_stride_shards_and_checkpoint_timing(tmp_path: Path) -> None:
    schedule = opaque_alias_canary.build_schedule(
        qualification_task_count=2,
        full_run_task_count=10,
        interval_every=5,
    )
    records = _mini_records(10)
    windows = plan_tool._build_layout(
        records,
        schedule["checkpoints"],
        tmp_path / "future_staging",
        window_size=5,
        shard_count=3,
    )
    assert len(windows) == 2
    assert windows[0]["start_position"] == 0
    assert windows[0]["end_position_exclusive"] == 5
    assert windows[0]["checkpoint_before"] == "c001-pre-full-run"
    assert windows[0]["checkpoint_after"] == "c002-full-00005"
    assert windows[1]["checkpoint_before"] == "c002-full-00005"
    assert windows[1]["checkpoint_after"] == "c003-post-full-run"
    assert [shard["organizer_positions"] for shard in windows[0]["shards"]] == [
        [0, 3], [1, 4], [2]
    ]
    positions = [
        position for window in windows for shard in window["shards"]
        for position in shard["organizer_positions"]
    ]
    assert sorted(positions) == list(range(10))
    paths = [
        value for window in windows for shard in window["shards"]
        for value in shard["role_staging_paths"].values()
    ]
    assert len(paths) == len(set(paths)) == 12
    assert all("candidate" in path or "verifier" in path for path in paths)
    assert not (tmp_path / "future_staging").exists()


def test_layout_rejects_missing_or_misaligned_canary_boundaries(tmp_path: Path) -> None:
    schedule = opaque_alias_canary.build_schedule(
        qualification_task_count=2,
        full_run_task_count=10,
        interval_every=5,
    )
    with pytest.raises(ValueError, match="boundary count"):
        plan_tool._build_layout(
            _mini_records(10), schedule["checkpoints"][:-1], tmp_path,
            window_size=5, shard_count=3,
        )
    changed = [dict(row) for row in schedule["checkpoints"]]
    changed[2]["full_run_tasks_completed"] = 4
    with pytest.raises(ValueError, match="align"):
        plan_tool._build_layout(
            _mini_records(10), changed, tmp_path,
            window_size=5, shard_count=3,
        )
    with pytest.raises(ValueError, match="invalid shard"):
        plan_tool._build_layout(
            _mini_records(10), schedule["checkpoints"], tmp_path,
            window_size=5, shard_count=6,
        )


def test_frozen_20k_plan_has_exact_once_only_source_and_timing() -> None:
    # This test reads organizer records but never prepares tasks or calls a model.
    plan = plan_tool.build_plan()
    assert plan_tool.verify_plan(plan) == {
        "windows": 40,
        "records_per_role": 20_000,
        "shards_per_window": 16,
        "canary_checkpoints": 42,
    }
    assert plan["execution_authorized"] is False
    assert plan["qualification_gate"]["required_before_any_active_full_run_artifact"] is True
    assert plan["qualification_gate"]["role_tuples_qualified_for_full_run"] is False
    assert plan["qualification_gate"]["epoch_observations"]["candidate"]["status"] == "closed_failure"
    assert "candidate" in plan["qualification_gate"]["blocked_roles"]
    assert "v4-or-later" in plan["qualification_gate"]["next_required_action"]
    assert len(plan["windows"]) == 40
    assert all(window["record_count"] == 500 for window in plan["windows"])
    assert all(len(window["shards"]) == 16 for window in plan["windows"])
    assert plan["windows"][0]["checkpoint_before"] == "c001-pre-full-run"
    assert plan["windows"][-1]["checkpoint_after"] == "c041-post-full-run"
    assert not plan_tool.DEFAULT_STAGING_ROOT.exists()
