"""Synthetic CPU-only tests; no model or external CLI invocation."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from tools.independent_gold import batch_scheduler


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def record(record_id: str) -> dict:
    return {
        "id": record_id,
        "meta": {"title": "synthetic"},
        "input_completeness": {"notice": True},
        "dropped_doc_counts": {},
        "docs": [{"doc_id": "D0", "type": "공고문", "text": f"{record_id}의 제공 원문"}],
    }


def fixture_files(tmp_path, *, count=4):
    source = tmp_path / "organizer.jsonl"
    rows = [record(f"R-{i}") for i in range(count)]
    source.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    source_sha = batch_scheduler._file_sha(source)
    candidate = tmp_path / "candidate_manifest.json"
    verifier = tmp_path / "verifier_manifest.json"
    candidate.write_text(
        json.dumps(
            {
                "schema_version": "dacon.independent.codex_cli_runner.v2",
                "phase": "unlabeled_20000",
                "pass_kind": "blind_first_pass",
                "annotator_role": "candidate",
                "model_identity": {"provider": "openai", "family": "openai:gpt5", "requested_model": "model-a"},
                "prompt_lineage": {"lineage_sha256": digest("candidate prompt")},
                "semantic_config": {"reasoning_effort": "high", "rubric_sha256": digest("rubric")},
                "record_input": {"sha256": source_sha},
                "tuple_sha256": digest("candidate tuple"),
                "imported_source_bundle": {"bundle_sha256": digest("bundle")},
                "runner_source": {"sha256": digest("runner")},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    verifier.write_text(
        json.dumps(
            {
                "schema_version": "dacon.independent.claude_full_record_run.v2",
                "phase": "unlabeled_20000",
                "pass_kind": "blind_first_pass",
                "requested_model": "opus",
                "expected_observed_model": "claude-opus-5",
                "model_identity_mode": "opaque_hosted_alias",
                "resolved_revision": None,
                "no_tools": True,
                "session_persistence": "disabled",
                "effort": "high",
                "input_sha256": source_sha,
                "prompt_protocol": "independent-v1",
                "prompt_argument_sha256": digest("prompt argument"),
                "static_system_prefix_sha256": digest("system"),
                "rubric_source_sha256": digest("rubric"),
                "rubric_projection_sha256": digest("projection"),
                "context_mode": "full",
                "compact_projection_schema": None,
                "source_context": {"law_sha256": digest("law")},
                "source_bundle": {"sha256": digest("bundle")},
                "schema_sha256": digest("schema"),
                "cli": {"sha256": digest("cli")},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return source, candidate, verifier


def build(tmp_path, *, count=4, shard_size=2):
    source, candidate, verifier = fixture_files(tmp_path, count=count)
    plan = batch_scheduler.build_plan(
        records_path=source,
        candidate_manifest_path=candidate,
        verifier_manifest_path=verifier,
        future_output_root=tmp_path / "future_attempts",
        expected_records=count,
        shard_size=shard_size,
        max_inflight=2,
    )
    return source, candidate, verifier, plan


def test_disjoint_ordered_shards_and_dry_run_only(tmp_path):
    source, candidate, verifier, plan = build(tmp_path)
    assert plan["execution_authorized"] is False
    assert plan["source"]["record_count"] == 4
    assert [s["shard_id"] for s in plan["shards"]] == ["s000", "s001"]
    assert [r["record_id"] for s in plan["shards"] for r in s["records"]] == ["R-0", "R-1", "R-2", "R-3"]
    assert len({r["record_id"] for s in plan["shards"] for r in s["records"]}) == 4
    assert batch_scheduler.verify_plan(plan) == {"records": 4, "shards": 2}
    assert not (tmp_path / "future_attempts").exists()
    assert plan["operational_boundaries"]["subscription_limit_is_not_a_currency_budget"] is True
    assert plan["roles"]["candidate"]["reference_manifest_sha256"] == batch_scheduler._file_sha(candidate)
    assert plan["roles"]["verifier"]["reference_manifest_sha256"] == batch_scheduler._file_sha(verifier)
    assert plan["source"]["sha256"] == batch_scheduler._file_sha(source)
    # Rechecking a frozen plan remains possible once later execution has
    # created the planned root; this dry-run does not create it itself.
    (tmp_path / "future_attempts").mkdir()
    assert batch_scheduler.verify_plan(plan) == {"records": 4, "shards": 2}


def test_source_and_role_prompt_identity_changes_fail_replay(tmp_path):
    source, candidate, verifier, plan = build(tmp_path)
    original_candidate = candidate.read_text(encoding="utf-8")
    changed = json.loads(candidate.read_text(encoding="utf-8"))
    changed["prompt_lineage"]["lineage_sha256"] = digest("different prompt")
    candidate.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(batch_scheduler.BatchScheduleError):
        batch_scheduler.verify_plan(plan)
    candidate.write_text(original_candidate, encoding="utf-8")
    # A change to the source is independently rejected even if the organizer ID remains.
    records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    records[0]["docs"][0]["text"] += " 바뀜"
    source.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    with pytest.raises(batch_scheduler.BatchScheduleError):
        batch_scheduler.verify_plan(plan)


def test_model_identity_change_fails_replay(tmp_path):
    _, candidate, _, plan = build(tmp_path)
    changed = json.loads(candidate.read_text(encoding="utf-8"))
    changed["model_identity"]["requested_model"] = "model-b"
    candidate.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(batch_scheduler.BatchScheduleError):
        batch_scheduler.verify_plan(plan)


def test_tampered_plan_or_bad_shard_parameters_rejected(tmp_path):
    _, _, _, plan = build(tmp_path)
    forged = copy.deepcopy(plan)
    forged["shards"][0]["records"][0]["record_id"] = "R-X"
    with pytest.raises(batch_scheduler.BatchScheduleError, match="self-hash"):
        batch_scheduler.verify_plan(forged)
    assert plan["assignment"]["max_inflight_planned"] == 2


def test_fresh_only_plan_file_and_execution_is_disabled(tmp_path):
    source, candidate, verifier, plan = build(tmp_path)
    path = tmp_path / "plan.json"
    batch_scheduler.write_plan_new(path, plan)
    assert json.loads(path.read_text(encoding="utf-8")) == plan
    with pytest.raises(batch_scheduler.BatchScheduleError, match="fresh-only"):
        batch_scheduler.write_plan_new(path, plan)
    assert batch_scheduler.main(
        [
            "--records", str(source),
            "--candidate-manifest", str(candidate),
            "--verifier-manifest", str(verifier),
            "--future-output-root", str(tmp_path / "future_attempts"),
            "--plan-output", str(tmp_path / "never_created.json"),
            "--expected-records", "4", "--shard-size", "2", "--execute",
        ]
    ) == 2
    assert not (tmp_path / "never_created.json").exists()


def test_count_duplicate_id_and_unsafe_future_root_fail(tmp_path):
    source, candidate, verifier, _ = build(tmp_path)
    with pytest.raises(batch_scheduler.BatchScheduleError, match="count"):
        batch_scheduler.build_plan(
            records_path=source, candidate_manifest_path=candidate,
            verifier_manifest_path=verifier, future_output_root=tmp_path / "future2",
            expected_records=20_000, shard_size=100,
        )
    with pytest.raises(batch_scheduler.BatchScheduleError, match="max_inflight"):
        batch_scheduler.build_plan(
            records_path=source, candidate_manifest_path=candidate,
            verifier_manifest_path=verifier, future_output_root=tmp_path / "future2",
            expected_records=4, shard_size=2, max_inflight=5,
        )
    with pytest.raises(batch_scheduler.BatchScheduleError, match="overlaps active"):
        batch_scheduler.build_plan(
            records_path=source, candidate_manifest_path=candidate,
            verifier_manifest_path=verifier,
            future_output_root=batch_scheduler.ACTIVE_FIRST_BATCHES[0] / "new",
            expected_records=4, shard_size=2,
        )
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    rows[1]["id"] = rows[0]["id"]
    source.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(batch_scheduler.BatchScheduleError, match="duplicate organizer ID"):
        batch_scheduler.build_plan(
            records_path=source, candidate_manifest_path=candidate,
            verifier_manifest_path=verifier, future_output_root=tmp_path / "future3",
            expected_records=4, shard_size=2,
        )
