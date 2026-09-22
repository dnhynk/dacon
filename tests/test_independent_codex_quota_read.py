"""Synthetic App Server protocol tests; never invoke the Codex executable."""

from __future__ import annotations

import json
import queue

import pytest

from tools.independent_gold import codex_quota_read


def window(duration: int = 10080, used: int | float = 21) -> dict:
    return {
        "usedPercent": used,
        "windowDurationMins": duration,
        "resetsAt": 2_000_000_000,
    }


def result(*, second_weekly: bool = False) -> dict:
    return {
        "rateLimits": {
            "limitId": "codex",
            "primary": window(300, 10),
            "secondary": window(),
        },
        "rateLimitsByLimitId": {
            "codex": {
                "limitId": "codex",
                "primary": window(300, 10),
                "secondary": window(),
            },
            "codex_other": {
                "limitId": "codex_other",
                "primary": window(10080 if second_weekly else 60, 30),
                "secondary": None,
            },
        },
        "account": {"email": "private@example.com", "token": "secret-token"},
        "rateLimitResetCredits": {"credits": [{"id": "secret-credit"}]},
    }


class FakeStdout:
    def __init__(self) -> None:
        self.lines: queue.Queue[str] = queue.Queue()

    def readline(self, _limit: int) -> str:
        return self.lines.get(timeout=2)

    def close(self) -> None:
        self.lines.put("")


class FakeStdin:
    def __init__(self, proc: "FakeProcess") -> None:
        self.proc = proc
        self.closed = False

    def write(self, data: str) -> int:
        message = json.loads(data)
        self.proc.messages.append(message)
        method = message["method"]
        if method == "initialize" and self.proc.reply_initialize:
            self.proc.emit({"method": "account/updated", "params": {"email": "hidden@example.com"}})
            self.proc.emit({"id": message["id"], "result": {"userAgent": "secret-token"}})
        if method == "account/rateLimits/read" and self.proc.reply_quota:
            self.proc.emit(self.proc.quota_reply)
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(
        self,
        *,
        quota_reply: dict | None = None,
        reply_initialize: bool = True,
        reply_quota: bool = True,
    ) -> None:
        self.stdout = FakeStdout()
        self.stdin = FakeStdin(self)
        self.messages: list[dict] = []
        self.quota_reply = quota_reply or {"id": 2, "result": result()}
        self.reply_initialize = reply_initialize
        self.reply_quota = reply_quota
        self.terminated = False
        self.killed = False

    def emit(self, message: dict) -> None:
        self.stdout.lines.put(json.dumps(message) + "\n")

    def poll(self) -> int | None:
        return 0 if self.terminated or self.killed else None

    def terminate(self) -> None:
        self.terminated = True
        self.stdout.close()

    def kill(self) -> None:
        self.killed = True
        self.stdout.close()

    def wait(self, *, timeout: float) -> int:
        return 0


def factory_for(proc: FakeProcess, calls: list[tuple[list[str], dict]]):
    def factory(argv, **kwargs):
        calls.append((argv, kwargs))
        return proc

    return factory


def test_documented_protocol_only_and_sanitized_output(monkeypatch, capsys):
    # The second weekly window mimics a separate reserve shown in /status.
    proc = FakeProcess(quota_reply={"id": 2, "result": result(second_weekly=True)})
    calls: list[tuple[list[str], dict]] = []
    factory = factory_for(proc, calls)
    original = codex_quota_read.read_weekly_quota
    monkeypatch.setattr(
        codex_quota_read,
        "read_weekly_quota",
        lambda **kwargs: original(popen_factory=factory, **kwargs),
    )
    assert codex_quota_read.main([]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "bucketId": "codex",
        "leftPercent": 79,
        "resetsAt": 2_000_000_000,
        "usedPercent": 21,
        "windowDurationMins": 10080,
    }
    assert "private@example.com" not in output.out
    assert "secret-token" not in output.out
    assert "secret-credit" not in output.out
    assert calls[0][0] == ["codex", "app-server"]
    assert [message["method"] for message in proc.messages] == [
        "initialize",
        "initialized",
        "account/rateLimits/read",
    ]
    assert proc.terminated
    assert proc.stdin.closed


def test_main_bucket_is_required_even_when_reserve_has_weekly_window():
    payload = {
        "rateLimitsByLimitId": {
            "codex_other": {
                "limitId": "codex_other",
                "primary": window(10080, 0),
            }
        }
    }
    with pytest.raises(codex_quota_read.QuotaReadError) as error:
        codex_quota_read._weekly_projection(payload)
    assert error.value.code == "CODEX_BUCKET_MISSING"


def test_two_weekly_windows_inside_main_bucket_are_ambiguous():
    payload = result(second_weekly=True)
    payload["rateLimitsByLimitId"]["codex"]["primary"] = window(10080, 5)
    with pytest.raises(codex_quota_read.QuotaReadError) as error:
        codex_quota_read._weekly_projection(payload)
    assert error.value.code == "CODEX_WEEKLY_AMBIGUOUS"


def test_single_bucket_fallback_and_fractional_percent():
    projection = codex_quota_read._weekly_projection(
        {"rateLimits": {"limitId": "codex", "primary": window(10080, 21.1)}}
    )
    assert projection["usedPercent"] == 21.1
    assert projection["leftPercent"] == 78.9


@pytest.mark.parametrize(
    "payload",
    [
        {"rateLimits": {"limitId": "codex", "primary": window(300)}},
        {"rateLimits": {"limitId": "codex", "primary": window(10080, 101)}},
        {"rateLimits": {"limitId": "codex", "primary": window(10080, float("nan"))}},
        {"rateLimits": {"limitId": "user@example.com", "primary": window()}},
        {"rateLimitsByLimitId": {}, "rateLimits": {"limitId": "codex", "primary": window()}},
    ],
)
def test_ambiguous_missing_or_invalid_weekly_window_fails_closed(payload):
    with pytest.raises(codex_quota_read.QuotaReadError):
        codex_quota_read._weekly_projection(payload)


def test_rpc_error_and_timeout_clean_up_process():
    error_proc = FakeProcess(quota_reply={"id": 2, "error": {"message": "secret-token"}})
    with pytest.raises(codex_quota_read.QuotaReadError):
        codex_quota_read.read_weekly_quota(popen_factory=factory_for(error_proc, []))
    assert error_proc.terminated

    timeout_proc = FakeProcess(reply_initialize=False)
    with pytest.raises(codex_quota_read.QuotaReadError):
        codex_quota_read.read_weekly_quota(
            timeout_seconds=0.05, popen_factory=factory_for(timeout_proc, [])
        )
    assert timeout_proc.terminated
    assert timeout_proc.stdin.closed


def test_cli_failure_redacts_exception(monkeypatch, capsys):
    def leak(**_kwargs):
        raise RuntimeError("private@example.com secret-token secret-credit")

    monkeypatch.setattr(codex_quota_read, "read_weekly_quota", leak)
    assert codex_quota_read.main([]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "private@example.com" not in output.err
    assert "secret-token" not in output.err
    assert "secret-credit" not in output.err
    assert "[UNEXPECTED]" in output.err


def test_cli_emits_only_safe_internal_error_code(monkeypatch, capsys):
    def bad_quota(**_kwargs):
        raise codex_quota_read.QuotaReadError("CODEX_WEEKLY_MISSING")

    monkeypatch.setattr(codex_quota_read, "read_weekly_quota", bad_quota)
    assert codex_quota_read.main([]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.strip() == "Codex weekly quota read failed [CODEX_WEEKLY_MISSING]."
