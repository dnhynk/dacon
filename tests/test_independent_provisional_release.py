"""Synthetic-only contract tests for the non-gold provisional release."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import pathlib

import pytest

from tools.independent_gold import full_record_output, provisional_release


def record(record_id: str, *, complete: bool = True) -> dict:
    return {
        "id": record_id,
        "meta": {"title": "synthetic notice"},
        "input_completeness": {"notice": True, "attachments": complete},
        "dropped_doc_counts": {} if complete else {"attachment": 1},
        "docs": [{"doc_id": "D0", "type": "공고문", "text": "기관 제한 원문과 검토 근거\n"}],
    }


def ledger(source: dict, *, labels: dict[str, int | str] | None = None, confidence: str = "H") -> dict:
    labels = labels or {}
    decisions = {}
    for item in provisional_release.ITEMS:
        label = labels.get(item, 0)
        decisions[item] = {
            "label": label,
            "confidence": confidence,
            "rationale": "제공 원문에서 범위와 예외를 검토함",
            "premise_span_ids": ["SRC-D0000-S000000"],
            "exception_analysis": None,
            "completeness": "unknown" if label == "U" else "sufficient",
            "material_missing_information": "적용 조항의 본문이 누락됨" if label == "U" else None,
            "positive_evidence_span_id": (
                "SRC-D0000-S000000"
                if label == 1 and item not in full_record_output.ABSENCE_ITEMS
                else None
            ),
        }
    return full_record_output.canonical_ledger_projection(
        {"source_span_ids": ["SRC-D0000-S000000"], "decisions": decisions}, source
    )


def pass_row(source: dict, *, role: str, labels: dict | None = None, family: str | None = None) -> dict:
    family = family or ("openai:gpt5" if role == "candidate" else "anthropic:claude")
    return {
        "schema_version": provisional_release.PASS_ROW_SCHEMA,
        "record_id": source["id"],
        "source_sha256": provisional_release._sha(source),
        "annotator_role": role,
        "lineage": {
            "provider": "openai" if role == "candidate" else "anthropic",
            "model_family": family,
            "model_name": "independent-test-model",
            "prompt_sha256": hashlib.sha256(f"prompt:{role}".encode()).hexdigest(),
            "receipt_sha256": hashlib.sha256(f"receipt:{source['id']}:{role}".encode()).hexdigest(),
            "input_boundary": "competition_source_only",
            "blind_first_pass": True,
            "peer_answer_visible": False,
            "production_output_visible": False,
        },
        "ledger": ledger(source, labels=labels),
    }


def role_bound_row(source: dict, *, runner: str, release_role: str,
                   labels: dict | None = None) -> dict:
    row = pass_row(source, role=release_role, labels=labels)
    row["schema_version"] = provisional_release.PASS_ROW_ROLE_SCHEMA
    row["lineage"]["provider"] = "anthropic" if runner == "claude" else "openai"
    row["lineage"]["model_family"] = "anthropic:opus" if runner == "claude" else "openai:sol"
    raw_role = None if runner == "claude" else "candidate"
    row["annotator_role"] = raw_role
    row["release_pass_role"] = release_role
    if runner == "claude":
        prompt_lineage = {
            "prompt_protocol": "synthetic-blind-source-only",
            "prompt_argument_sha256": "1" * 64,
            "rubric_projection_sha256": "2" * 64,
            "static_system_prefix_sha256": "3" * 64,
            "context_mode": "full",
        }
    else:
        prompt_lineage = {"annotator_role": raw_role, "profile": "synthetic-source-only"}
        prompt_lineage["lineage_sha256"] = provisional_release._sha(prompt_lineage)
    row["role_provenance"] = {
        "raw_runner_kind": runner,
        "raw_runner_role": raw_role,
        "raw_pass_kind": "blind_first_pass",
        "raw_run_manifest_sha256": "4" * 64 if runner == "claude" else "5" * 64,
        "raw_prompt_lineage": prompt_lineage,
        "release_pass_role": release_role,
        "role_assignment_kind": "posthoc_independent_vote_order",
    }
    return row


def write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def fixture_paths(tmp_path, sources, primary, secondary=None):
    source_path = tmp_path / "organizer.jsonl"
    primary_path = tmp_path / "primary.jsonl"
    secondary_path = tmp_path / "secondary.jsonl" if secondary is not None else None
    write_jsonl(source_path, sources)
    write_jsonl(primary_path, primary)
    if secondary_path is not None:
        write_jsonl(secondary_path, secondary)
    return source_path, primary_path, secondary_path


def build(tmp_path, sources, primary, secondary=None, *, emit_csv=False):
    paths = fixture_paths(tmp_path, sources, primary, secondary)
    output = tmp_path / "provisional"
    summary = provisional_release.build_release(
        records_path=paths[0],
        primary_path=paths[1],
        secondary_path=paths[2],
        output_dir=output,
        expected_count=len(sources),
        emit_provisional_csv=emit_csv,
    )
    cells = [json.loads(line) for line in (output / "provisional_cells.jsonl").read_text(encoding="utf-8").splitlines()]
    return summary, cells, output


def test_primary_only_is_24_cell_draft_not_gold(tmp_path):
    sources = [record("R-1"), record("R-2")]
    primary = [pass_row(source, role="candidate") for source in sources]
    summary, cells, output = build(tmp_path, sources, primary)
    assert len(cells) == 48
    assert summary["tier_counts"] == {"draft": 48, "corroborated": 0, "contested": 0, "abstain": 0, "missing": 0}
    assert summary["gold_cells"] == 0
    assert all(cell["provisional_label"] == 0 and cell["gold_label"] is None for cell in cells)
    assert all("single_pass_unverified" in cell["risk_flags"] for cell in cells)
    assert all(cell["unresolved"] and cell["eligible_as_gold"] is False for cell in cells)
    assert not (output / "provisional_opt_in.csv").exists()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["assurance"]["eligible_as_production_tuning_target"] is False
    assert manifest["assurance"]["gold_answer_key"] is False
    assert manifest["outputs"]["provisional_cells.jsonl"] == provisional_release._file_sha(output / "provisional_cells.jsonl")


def test_agreement_disagreement_and_abstention_have_distinct_tiers(tmp_path):
    source = record("R-1")
    primary = pass_row(source, role="candidate", labels={"v1": 1, "v2": 1, "v3": "U"})
    secondary = pass_row(source, role="verifier", labels={"v1": 1, "v2": 0, "v3": 0})
    summary, cells, _ = build(tmp_path, [source], [primary], [secondary])
    by_item = {cell["item"]: cell for cell in cells}
    assert summary["tier_counts"] == {"draft": 0, "corroborated": 22, "contested": 1, "abstain": 1, "missing": 0}
    assert by_item["v1"]["tier"] == "corroborated" and by_item["v1"]["provisional_label"] == 1
    assert by_item["v2"]["tier"] == "contested" and by_item["v2"]["provisional_label"] is None
    assert by_item["v3"]["tier"] == "abstain" and by_item["v3"]["provisional_label"] is None
    assert by_item["v2"]["unresolved"] and by_item["v3"]["unresolved"]
    assert all(cell["gold_label"] is None for cell in cells)


def test_claude_first_drafts_all_records_and_sol_overlap_only_is_corroborated(tmp_path):
    sources = [record("R-1"), record("R-2")]
    claude_rows = [
        role_bound_row(sources[0], runner="claude", release_role="candidate",
                       labels={"v1": 1, "v2": 1, "v3": "U"}),
        role_bound_row(sources[1], runner="claude", release_role="candidate"),
    ]
    sol_rows = [
        role_bound_row(sources[0], runner="codex", release_role="verifier",
                       labels={"v1": 1, "v2": 0, "v3": 0}),
    ]
    summary, cells, output = build(tmp_path, sources, claude_rows, sol_rows)
    assert summary["tier_counts"] == {
        "draft": 24, "corroborated": 22, "contested": 1, "abstain": 1, "missing": 0,
    }
    by_key = {(cell["record_id"], cell["item"]): cell for cell in cells}
    assert by_key["R-1", "v1"]["tier"] == "corroborated"
    assert by_key["R-1", "v2"]["tier"] == "contested"
    assert by_key["R-1", "v3"]["tier"] == "abstain"
    assert all(by_key["R-2", item]["tier"] == "draft" for item in provisional_release.ITEMS)
    assert all(cell["gold_label"] is None and not cell["eligible_as_gold"] for cell in cells)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["assurance"]["gold_answer_key"] is False


@pytest.mark.parametrize("fault", ["raw_role", "release_role", "prompt_lineage", "legacy_schema"])
def test_claude_first_requires_truthful_explicit_role_provenance(tmp_path, fault):
    source = record("R-1")
    row = role_bound_row(source, runner="claude", release_role="candidate")
    if fault == "raw_role":
        row["role_provenance"]["raw_runner_role"] = "candidate"
    elif fault == "release_role":
        row["release_pass_role"] = "verifier"
    elif fault == "prompt_lineage":
        del row["role_provenance"]["raw_prompt_lineage"]["prompt_protocol"]
    else:
        row["schema_version"] = provisional_release.PASS_ROW_SCHEMA
        del row["role_provenance"]
        del row["release_pass_role"]
    paths = fixture_paths(tmp_path, [source], [row])
    with pytest.raises(provisional_release.ProvisionalReleaseError):
        provisional_release.build_release(
            records_path=paths[0], primary_path=paths[1], secondary_path=None,
            output_dir=tmp_path / "must_not_publish", expected_count=1,
        )
    assert not (tmp_path / "must_not_publish").exists()


@pytest.mark.parametrize("fault", ["provider", "family", "prompt", "receipt"])
def test_claude_first_sol_second_still_requires_independent_lineage(tmp_path, fault):
    source = record("R-1")
    first = role_bound_row(source, runner="claude", release_role="candidate")
    second = role_bound_row(source, runner="codex", release_role="verifier")
    if fault == "provider":
        second["lineage"]["provider"] = first["lineage"]["provider"]
    elif fault == "family":
        second["lineage"]["model_family"] = first["lineage"]["model_family"]
    elif fault == "prompt":
        second["lineage"]["prompt_sha256"] = first["lineage"]["prompt_sha256"]
    else:
        second["lineage"]["receipt_sha256"] = first["lineage"]["receipt_sha256"]
    paths = fixture_paths(tmp_path, [source], [first], [second])
    with pytest.raises(provisional_release.ProvisionalReleaseError):
        provisional_release.build_release(
            records_path=paths[0], primary_path=paths[1], secondary_path=paths[2],
            output_dir=tmp_path / "must_not_publish", expected_count=1,
        )


def test_declared_missing_source_never_becomes_binary_label_even_with_agreement(tmp_path):
    source = record("R-1", complete=False)
    first = pass_row(source, role="candidate")
    second = pass_row(source, role="verifier")
    summary, cells, _ = build(tmp_path, [source], [first], [second])
    assert summary["organizer_declared_incomplete_records"] == 1
    assert summary["tier_counts"]["abstain"] == 24
    assert all(cell["provisional_label"] is None and cell["unresolved"] for cell in cells)
    assert all("source_incomplete" in cell["risk_flags"] for cell in cells)


@pytest.mark.parametrize("fault", ["source_hash", "span_quote", "missing_cell", "extra_cell", "unknown_span"])
def test_source_and_24_cell_tampering_fail_closed(tmp_path, fault):
    source = record("R-1")
    row = pass_row(source, role="candidate")
    if fault == "source_hash":
        row["source_sha256"] = "0" * 64
    elif fault == "span_quote":
        row["ledger"]["source_spans"][0]["quote"] = "조작"
    elif fault == "missing_cell":
        row["ledger"]["cells"].pop()
    elif fault == "extra_cell":
        row["ledger"]["cells"].append(copy.deepcopy(row["ledger"]["cells"][-1]))
    else:
        row["ledger"]["cells"][0]["premise_span_ids"] = ["SRC-D9999-S999999"]
    paths = fixture_paths(tmp_path, [source], [row])
    output = tmp_path / "provisional"
    with pytest.raises(provisional_release.ProvisionalReleaseError):
        provisional_release.build_release(
            records_path=paths[0], primary_path=paths[1], secondary_path=None,
            output_dir=output, expected_count=1,
        )
    assert not output.exists()


def test_duplicate_or_unknown_primary_id_rejected(tmp_path):
    sources = [record("R-1"), record("R-2")]
    for rows in ([pass_row(sources[0], role="candidate")] * 2,
                 [pass_row(record("R-X"), role="candidate")]):
        case = tmp_path / f"case-{len(list(tmp_path.iterdir()))}"
        case.mkdir()
        paths = fixture_paths(case, sources, rows)
        with pytest.raises(provisional_release.ProvisionalReleaseError):
            provisional_release.build_release(
                records_path=paths[0], primary_path=paths[1], secondary_path=None,
                output_dir=case / "provisional", expected_count=2,
            )


def test_missing_first_pass_keeps_24_blank_cells_and_optional_csv_blank(tmp_path):
    sources = [record("R-1"), record("R-2")]
    summary, cells, output = build(
        tmp_path, sources, [pass_row(sources[0], role="candidate")], emit_csv=True
    )
    assert summary["tier_counts"]["missing"] == 24
    missing = [cell for cell in cells if cell["record_id"] == "R-2"]
    assert all(cell["tier"] == "missing" and cell["provisional_label"] is None for cell in missing)
    assert all("missing_primary_vote" in cell["risk_flags"] for cell in missing)
    with (output / "provisional_opt_in.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2 and all(rows[1][item] == "" for item in provisional_release.ITEMS)
    assert all(rows[0][item] == "" for item in provisional_release.ITEMS)  # draft is not in the opt-in subset


def test_same_family_is_not_corroboration(tmp_path):
    source = record("R-1")
    first = pass_row(source, role="candidate")
    second = pass_row(source, role="verifier", family=first["lineage"]["model_family"])
    paths = fixture_paths(tmp_path, [source], [first], [second])
    with pytest.raises(provisional_release.ProvisionalReleaseError, match="cross-family"):
        provisional_release.build_release(
            records_path=paths[0], primary_path=paths[1], secondary_path=paths[2],
            output_dir=tmp_path / "provisional", expected_count=1,
        )


def test_same_provider_is_not_corroboration_even_with_different_family(tmp_path):
    source = record("R-1")
    first = pass_row(source, role="candidate")
    second = pass_row(source, role="verifier")
    second["lineage"]["provider"] = first["lineage"]["provider"]
    paths = fixture_paths(tmp_path, [source], [first], [second])
    with pytest.raises(provisional_release.ProvisionalReleaseError, match="cross-provider"):
        provisional_release.build_release(
            records_path=paths[0], primary_path=paths[1], secondary_path=paths[2],
            output_dir=tmp_path / "provisional", expected_count=1,
        )


def test_opt_in_csv_writes_only_high_confidence_corroborated_binary(tmp_path):
    source = record("R-1")
    first = pass_row(source, role="candidate", labels={"v1": 1, "v2": "U"})
    second = pass_row(source, role="verifier", labels={"v1": 1, "v2": 0})
    _, _, output = build(tmp_path, [source], [first], [second], emit_csv=True)
    with (output / "provisional_opt_in.csv").open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["v1"] == "1" and row["v2"] == "" and row["v4"] == "0"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["assurance"]["gold_answer_key"] is False
    assert "provisional_opt_in.csv" in manifest["outputs"]


def test_existing_output_is_never_overwritten(tmp_path):
    source = record("R-1")
    paths = fixture_paths(tmp_path, [source], [pass_row(source, role="candidate")])
    output = tmp_path / "provisional"
    output.mkdir()
    marker = output / "preserve.txt"
    marker.write_text("mine", encoding="utf-8")
    with pytest.raises(provisional_release.ProvisionalReleaseError, match="fresh-only"):
        provisional_release.build_release(
            records_path=paths[0], primary_path=paths[1], secondary_path=None,
            output_dir=output, expected_count=1,
        )
    assert marker.read_text(encoding="utf-8") == "mine"
