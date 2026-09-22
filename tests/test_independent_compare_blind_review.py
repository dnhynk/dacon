"""CPU-only guards for post-hoc blind/peer comparison."""

import json

import pytest

from tools.independent_gold import compare_blind_review as compare


def _peer(path, record_id, value):
    path.mkdir()
    row = {
        "id": record_id, "source_sha256": "a" * 64,
        "ledger": {"cells": [
            {"item": f"v{index}", "label": value if index == 10 else 0}
            for index in range(1, 25)
        ]},
    }
    (path / "full_records.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")


def test_blind_comparison_is_diagnostic_only(tmp_path):
    report = tmp_path / "blind.md"
    report.write_text("| PPS-D-000002 v10 | U | unresolved |\n", encoding="utf-8")
    sol, claude = tmp_path / "sol", tmp_path / "claude"
    _peer(sol, "PPS-D-000002", 1)
    _peer(claude, "PPS-D-000002", "U")
    result = compare.compare(report, [sol], [claude])
    assert result["status"] == "diagnostic_only_not_gold"
    assert result["counts"] == {"blind_U": 1, "matches_claude": 1}


def test_duplicate_blind_cell_and_missing_peer_fail_closed(tmp_path):
    report = tmp_path / "blind.md"
    report.write_text("| PPS-D-000002 v10 | 0 | x |\n" * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate blind cell"):
        compare.review_cells(report)
    report.write_text("| PPS-D-000002 v10 | 0 | x |\n", encoding="utf-8")
    sol, claude = tmp_path / "sol", tmp_path / "claude"
    _peer(sol, "PPS-D-000002", 1)
    _peer(claude, "PPS-D-000003", 0)
    with pytest.raises(ValueError, match="missing peer vote"):
        compare.compare(report, [sol], [claude])
