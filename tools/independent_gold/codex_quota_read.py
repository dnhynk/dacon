"""Read the Codex weekly quota through the documented App Server method.

This tool never starts a model thread or turn. It emits only a small, sanitized
weekly-window projection; raw App Server messages are neither logged nor saved.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from decimal import Decimal
from typing import Any, Callable


WEEKLY_WINDOW_MINS = 7 * 24 * 60
WEEKLY_TOLERANCE_MINS = 60
MAX_LINE_CHARS = 1_000_000
MAX_MESSAGES = 256
MAIN_BUCKET_ID = "codex"
SAFE_ERROR_CODES = frozenset(
    {
        "START_FAILED",
        "INVALID_TIMEOUT",
        "TRANSPORT_ERROR",
        "TIMEOUT",
        "STREAM_CLOSED",
        "PROTOCOL_ERROR",
        "REQUEST_REJECTED",
        "RATE_LIMITS_MISSING",
        "CODEX_BUCKET_MISSING",
        "CODEX_BUCKET_INVALID",
        "CODEX_WEEKLY_MISSING",
        "CODEX_WEEKLY_AMBIGUOUS",
        "INVALID_WINDOW",
        "UNEXPECTED",
    }
)


class QuotaReadError(Exception):
    """A fail-closed read error with a fixed, non-sensitive diagnostic code."""

    def __init__(self, code: str) -> None:
        self.code = code if code in SAFE_ERROR_CODES else "UNEXPECTED"
        super().__init__(self.code)


def _send(proc: Any, message: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise QuotaReadError("TRANSPORT_ERROR")
    try:
        proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        proc.stdin.flush()
    except OSError as exc:
        raise QuotaReadError("TRANSPORT_ERROR") from exc


def _pump_stdout(stdout: Any, out: queue.Queue[tuple[str, str | None]], overflow: threading.Event) -> None:
    try:
        while True:
            line = stdout.readline(MAX_LINE_CHARS + 1)
            if not line:
                item = ("eof", None)
            elif len(line) > MAX_LINE_CHARS:
                item = ("oversize", None)
            else:
                item = ("line", line)
            try:
                out.put_nowait(item)
            except queue.Full:
                overflow.set()
                return
            if item[0] != "line":
                return
    except Exception:
        try:
            out.put_nowait(("read_error", None))
        except queue.Full:
            overflow.set()


def _await_response(
    expected_id: int,
    inbox: queue.Queue[tuple[str, str | None]],
    overflow: threading.Event,
    deadline: float,
) -> dict[str, Any]:
    for _ in range(MAX_MESSAGES):
        if overflow.is_set():
            raise QuotaReadError("PROTOCOL_ERROR")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise QuotaReadError("TIMEOUT")
        try:
            kind, line = inbox.get(timeout=remaining)
        except queue.Empty as exc:
            raise QuotaReadError("TIMEOUT") from exc
        if kind != "line" or line is None:
            raise QuotaReadError("STREAM_CLOSED" if kind == "eof" else "PROTOCOL_ERROR")
        try:
            message = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise QuotaReadError("PROTOCOL_ERROR") from exc
        if not isinstance(message, dict):
            raise QuotaReadError("PROTOCOL_ERROR")
        message_id = message.get("id")
        if message_id is None:
            # Notifications may contain account data; never log or return them.
            continue
        if type(message_id) is not int or message_id != expected_id:
            raise QuotaReadError("PROTOCOL_ERROR")
        if "error" in message or not isinstance(message.get("result"), dict):
            raise QuotaReadError("REQUEST_REJECTED")
        return message["result"]
    raise QuotaReadError("PROTOCOL_ERROR")


def _stop_process(proc: Any) -> None:
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except Exception:
        pass
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=1)
        except Exception:
            pass
    try:
        if proc.stdout is not None:
            proc.stdout.close()
    except Exception:
        pass


def _valid_int(value: Any, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _weekly_projection(result: dict[str, Any]) -> dict[str, int | float | str]:
    buckets = result.get("rateLimitsByLimitId")
    if buckets is None:
        single = result.get("rateLimits")
        if not isinstance(single, dict):
            raise QuotaReadError("RATE_LIMITS_MISSING")
        if single.get("limitId") != MAIN_BUCKET_ID:
            raise QuotaReadError("CODEX_BUCKET_MISSING")
        buckets = {MAIN_BUCKET_ID: single}
    if not isinstance(buckets, dict) or not buckets:
        raise QuotaReadError("RATE_LIMITS_MISSING")

    # The backward-compatible single view can coexist with the multi-bucket
    # view. Only the documented main Codex bucket represents the TUI's main
    # weekly limit; reserve/other buckets must never be substituted for it.
    bucket = buckets.get(MAIN_BUCKET_ID)
    if bucket is None:
        raise QuotaReadError("CODEX_BUCKET_MISSING")
    if not isinstance(bucket, dict) or bucket.get("limitId") != MAIN_BUCKET_ID:
        raise QuotaReadError("CODEX_BUCKET_INVALID")

    weekly: list[dict[str, Any]] = []
    for field in ("primary", "secondary"):
        window = bucket.get(field)
        if window is None:
            continue
        if not isinstance(window, dict):
            raise QuotaReadError("INVALID_WINDOW")
        duration = window.get("windowDurationMins")
        used = window.get("usedPercent")
        reset = window.get("resetsAt")
        if not _valid_int(duration, minimum=1):
            raise QuotaReadError("INVALID_WINDOW")
        if type(used) not in (int, float) or not math.isfinite(used) or not 0 <= used <= 100:
            raise QuotaReadError("INVALID_WINDOW")
        if not _valid_int(reset, minimum=1):
            raise QuotaReadError("INVALID_WINDOW")
        if abs(duration - WEEKLY_WINDOW_MINS) <= WEEKLY_TOLERANCE_MINS:
            weekly.append(window)

    if not weekly:
        raise QuotaReadError("CODEX_WEEKLY_MISSING")
    if len(weekly) != 1:
        raise QuotaReadError("CODEX_WEEKLY_AMBIGUOUS")
    window = weekly[0]
    used_percent = window["usedPercent"]
    left_decimal = Decimal(100) - Decimal(str(used_percent))
    left_percent: int | float = int(left_decimal) if left_decimal == int(left_decimal) else float(left_decimal)
    return {
        "usedPercent": used_percent,
        "leftPercent": left_percent,
        "windowDurationMins": window["windowDurationMins"],
        "resetsAt": window["resetsAt"],
        "bucketId": MAIN_BUCKET_ID,
    }


def read_weekly_quota(
    *,
    timeout_seconds: float = 15.0,
    popen_factory: Callable[..., Any] = subprocess.Popen,
) -> dict[str, int | float | str]:
    """Query one weekly quota window, without creating a Codex model turn."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise QuotaReadError("INVALID_TIMEOUT")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        proc = popen_factory(
            ["codex", "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=creationflags,
        )
    except OSError as exc:
        raise QuotaReadError("START_FAILED") from exc
    try:
        if proc.stdout is None:
            raise QuotaReadError("TRANSPORT_ERROR")
        inbox: queue.Queue[tuple[str, str | None]] = queue.Queue(maxsize=64)
        overflow = threading.Event()
        reader = threading.Thread(
            target=_pump_stdout, args=(proc.stdout, inbox, overflow), daemon=True
        )
        reader.start()
        deadline = time.monotonic() + timeout_seconds
        _send(
            proc,
            {
                "method": "initialize",
                "id": 1,
                "params": {
                    "clientInfo": {
                        "name": "dacon_quota_reader",
                        "title": "DACON Codex quota reader",
                        "version": "1.0.0",
                    }
                },
            },
        )
        _await_response(1, inbox, overflow, deadline)
        _send(proc, {"method": "initialized", "params": {}})
        _send(proc, {"method": "account/rateLimits/read", "id": 2})
        result = _await_response(2, inbox, overflow, deadline)
        return _weekly_projection(result)
    finally:
        _stop_process(proc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    args = parser.parse_args(argv)
    try:
        projection = read_weekly_quota(timeout_seconds=args.timeout_seconds)
    except QuotaReadError as exc:
        print(f"Codex weekly quota read failed [{exc.code}].", file=sys.stderr)
        return 2
    except Exception:
        # Never print upstream errors, account details, paths, or response payloads.
        print("Codex weekly quota read failed [UNEXPECTED].", file=sys.stderr)
        return 2
    print(json.dumps(projection, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
