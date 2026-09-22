"""CPU-only invariants for the resumable Claude task runner; the CLI is replaced by a fake."""

import json

import pytest

from tools.independent_gold import cell_runner as runner


def _envelope(structured):
    usage = {"inputTokens": 10, "outputTokens": 5, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0}
    return json.dumps({
        "type": "result", "subtype": "success", "is_error": False, "permission_denials": [],
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0, "server_tool_use": {"web_search_requests": 0}},
        "subagent_stats": {"spawned": 0},
        "modelUsage": {runner.base.OBSERVED_MODEL: {
            **usage, "canonicalModel": runner.base.OBSERVED_MODEL, "provider": "firstParty"}},
        "total_cost_usd": 0.25, "structured_output": structured,
    }).encode("utf-8")


def _task(name, prompt="PROMPT"):
    return {"task_id": name, "system": "SYSTEM", "prompt": prompt, "schema_json": "{}",
            "prompt_argument": "ARG", "binding": {"name": name}}


@pytest.fixture()
def output(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    return tmp_path / "runs" / "vet"


def test_dry_stage_writes_inputs_once_and_makes_no_attempt(output):
    summary = runner.run_tasks([_task("a"), _task("b")], output, executable=None)
    assert summary["pending"] == 2 and summary["attempted"] == 0 and not summary["executed"]
    assert (output / "tasks" / "a" / "prompt.txt").read_text(encoding="utf-8") == "PROMPT"
    assert not (output / "tasks" / "a" / "attempts").exists()
    runner.run_tasks([_task("a")], output, executable=None)
    with pytest.raises(ValueError, match="different inputs"):
        runner.run_tasks([_task("a", prompt="CHANGED")], output, executable=None)
    with pytest.raises(ValueError, match="duplicate"):
        runner.run_tasks([_task("a"), _task("a")], output, executable=None)
    with pytest.raises(ValueError, match="unsafe task id"):
        runner.run_tasks([_task("../escape")], output, executable=None)


def test_finished_tasks_are_never_called_again_and_failures_are_not_silently_retried(output):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs["prompt"])
        if kwargs["prompt"] == "BAD":
            return 1, b"", b"rate limited", None
        return 0, _envelope({"records": []}), b"", None

    tasks = [_task("ok-1", "GOOD"), _task("bad", "BAD"), _task("late", "GOOD")]
    first = runner.run_tasks(tasks, output, executable="claude", workers=1, invoke_fn=fake)
    # The failure stops new submissions: the third task is left untouched.
    assert (first["ok"], first["failed"], first["not_started_after_failure"]) == (1, 1, 1)
    assert calls == ["GOOD", "BAD"] and first["reported_cost_usd"] == 0.25
    assert runner.task_status(output / "tasks" / "ok-1") == ("ok", 1)
    assert runner.task_status(output / "tasks" / "bad") == ("failed", 1)
    assert runner.task_status(output / "tasks" / "late") == ("new", 0)
    stored = json.loads((output / "tasks" / "ok-1" / "attempts" / "attempt-001" / "structured_output.json").read_text(encoding="utf-8"))
    assert stored == {"records": []}
    second = runner.run_tasks(tasks, output, executable="claude", workers=1, invoke_fn=fake)
    assert (second["already_ok"], second["previously_failed_skipped"], second["ok"]) == (1, 1, 1)
    assert calls == ["GOOD", "BAD", "GOOD"]
    third = runner.run_tasks(tasks, output, executable="claude", workers=1, retry_failed=True, invoke_fn=fake)
    assert third["attempted"] == 1 and runner.task_status(output / "tasks" / "bad") == ("failed", 2)


def test_tool_use_or_budget_overrun_is_a_safety_error_not_an_answer(output):
    def over_budget(**kwargs):
        return 0, _envelope({"records": []}), b"", None

    summary = runner.run_tasks([_task("a")], output, executable="claude", max_budget_usd=0.1, invoke_fn=over_budget)
    assert summary["failed"] == 1
    receipt = json.loads((output / "tasks" / "a" / "attempts" / "attempt-001" / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "safety_error" and "cost exceeds cap" in receipt["error"]
    assert not (output / "tasks" / "a" / "attempts" / "attempt-001" / "structured_output.json").exists()


def test_output_outside_runs_and_unreviewed_cli_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="under repository runs"):
        runner.run_tasks([_task("a")], tmp_path / "elsewhere", executable=None)

    class Result:
        returncode, stdout, stderr = 0, b"9.9.9 (Claude Code)\n", b""

    monkeypatch.setattr(runner.shutil, "which", lambda name: __file__)
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: Result())
    with pytest.raises(ValueError, match="unreviewed Claude CLI version"):
        runner.resolve_cli("claude")
    admitted = runner.resolve_cli("claude", ["9.9.9 (Claude Code)"])
    assert admitted["version_admitted_by_caller_flag"] is True
