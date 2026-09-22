"""Build a source-checked *provisional* 20k annotation release.

This is deliberately not ``build_gold``.  It consumes an independent primary
pass and an optional, blind cross-family subset.  Exact organizer source
and all 24 ledger cells are replayed; model competence, blind execution, and
gold accuracy are NOT certified by this adapter.  No production output or
official answer is accepted as an input.  V2 pass rows keep the actual raw
runner role separate from their post-hoc release vote order.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import pathlib
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Iterator, Mapping
from typing import Any

try:
    from tools.independent_gold import full_record_output, review_ledger
except ModuleNotFoundError:  # Direct execution from this directory.
    import full_record_output  # type: ignore[no-redef]
    import review_ledger  # type: ignore[no-redef]


PASS_ROW_SCHEMA = "dacon.independent.provisional_pass_row.v1"
PASS_ROW_ROLE_SCHEMA = "dacon.independent.provisional_pass_row.v2"
CELL_SCHEMA = "dacon.independent.provisional_cell.v1"
SUMMARY_SCHEMA = "dacon.independent.provisional_summary.v1"
MANIFEST_SCHEMA = "dacon.independent.provisional_manifest.v1"
ITEMS = tuple(f"v{i}" for i in range(1, 25))
HEX64 = re.compile(r"^[0-9a-f]{64}$")
PASS_KEYS = frozenset(
    {"schema_version", "record_id", "source_sha256", "annotator_role", "lineage", "ledger"}
)
PASS_ROLE_KEYS = PASS_KEYS | {"release_pass_role", "role_provenance"}
ROLE_PROVENANCE_KEYS = frozenset({
    "raw_runner_kind", "raw_runner_role", "raw_pass_kind", "raw_run_manifest_sha256",
    "raw_prompt_lineage", "release_pass_role", "role_assignment_kind",
})
LINEAGE_KEYS = frozenset(
    {
        "provider",
        "model_family",
        "model_name",
        "prompt_sha256",
        "receipt_sha256",
        "input_boundary",
        "blind_first_pass",
        "peer_answer_visible",
        "production_output_visible",
    }
)
DECISION_KEYS = (
    "label",
    "confidence",
    "rationale",
    "premise_span_ids",
    "exception_analysis",
    "completeness",
    "material_missing_information",
    "positive_evidence_span_id",
)


class ProvisionalReleaseError(ValueError):
    """An input cannot safely enter a provisional, source-checked release."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProvisionalReleaseError(message)


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


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ProvisionalReleaseError(f"non-standard JSON constant {value!r}")


def _parse_row(raw: bytes, *, location: str) -> dict[str, Any]:
    try:
        row = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvisionalReleaseError(f"{location}: invalid UTF-8 JSON") from exc
    _require(isinstance(row, dict), f"{location}: expected one object")
    return row


def _organizer_rows(path: pathlib.Path) -> Iterator[dict[str, Any]]:
    opener = gzip.open if path.suffix.lower() == ".gz" else pathlib.Path.open
    handle = (
        gzip.open(path, "rb") if opener is gzip.open else path.open("rb")
    )
    with handle:
        for number, raw in enumerate(handle, 1):
            _require(bool(raw.strip()), f"{path}:{number}: blank organizer row")
            record = _parse_row(raw, location=f"{path}:{number}")
            try:
                review_ledger._validate_organizer_record(record)
            except review_ledger.LedgerValidationError as exc:
                raise ProvisionalReleaseError(f"{path}:{number}: {exc}") from exc
            _require(
                isinstance(record.get("id"), str) and bool(record["id"]),
                f"{path}:{number}: source ID must be a nonempty string",
            )
            yield record


def _source_index(path: pathlib.Path, *, expected_count: int) -> dict[str, str]:
    _require(expected_count > 0, "expected_count must be positive")
    result: dict[str, str] = {}
    for record in _organizer_rows(path):
        record_id = record["id"]
        _require(record_id not in result, f"duplicate organizer ID {record_id}")
        result[record_id] = _sha(record)
    _require(
        len(result) == expected_count,
        f"organizer count {len(result)} differs from expected {expected_count}",
    )
    return result


def _valid_sha(value: Any, *, context: str) -> None:
    _require(isinstance(value, str) and HEX64.fullmatch(value) is not None, f"{context}: invalid sha256")


def _validate_pass_row(
    row: Mapping[str, Any], *, role: str, source_hashes: Mapping[str, str], context: str
) -> None:
    schema = row.get("schema_version")
    _require(schema in {PASS_ROW_SCHEMA, PASS_ROW_ROLE_SCHEMA}, f"{context}: wrong pass row schema")
    expected_keys = PASS_ROLE_KEYS if schema == PASS_ROW_ROLE_SCHEMA else PASS_KEYS
    _require(set(row) == expected_keys, f"{context}: pass row keys differ")
    record_id = row.get("record_id")
    _require(isinstance(record_id, str) and record_id in source_hashes, f"{context}: unknown source ID")
    if schema == PASS_ROW_SCHEMA:
        _require(row.get("annotator_role") == role, f"{context}: role differs from input file")
    else:
        _require(row.get("release_pass_role") == role,
                 f"{context}: release role differs from input file")
    if schema == PASS_ROW_ROLE_SCHEMA:
        provenance = row["role_provenance"]
        _require(isinstance(provenance, Mapping) and set(provenance) == ROLE_PROVENANCE_KEYS,
                 f"{context}: role provenance keys differ")
        runner = provenance["raw_runner_kind"]
        _require(runner in {"codex", "claude"} and
                 provenance["raw_pass_kind"] == "blind_first_pass" and
                 provenance["release_pass_role"] == role and
                 row["annotator_role"] == provenance["raw_runner_role"] and
                 provenance["role_assignment_kind"] == "posthoc_independent_vote_order",
                 f"{context}: raw/release role declaration invalid")
        _valid_sha(provenance["raw_run_manifest_sha256"], context=f"{context}:raw_run_manifest_sha256")
        prompt_lineage = provenance["raw_prompt_lineage"]
        _require(isinstance(prompt_lineage, Mapping) and bool(prompt_lineage),
                 f"{context}: raw prompt lineage missing")
        if runner == "codex":
            raw_role = provenance["raw_runner_role"]
            _require(raw_role in {"candidate", "verifier"} and
                     prompt_lineage.get("annotator_role") == raw_role and
                     prompt_lineage.get("lineage_sha256") == _sha({
                         key: value for key, value in prompt_lineage.items()
                         if key != "lineage_sha256"
                     }), f"{context}: Codex raw role/prompt lineage differs")
        else:
            _require(provenance["raw_runner_role"] is None and
                     set(prompt_lineage) == {
                         "prompt_protocol", "prompt_argument_sha256", "rubric_projection_sha256",
                         "static_system_prefix_sha256", "context_mode",
                     } and prompt_lineage["context_mode"] in {"full", "compact"} and
                     isinstance(prompt_lineage["prompt_protocol"], str) and
                     bool(prompt_lineage["prompt_protocol"]),
                     f"{context}: Claude raw role/prompt lineage differs")
            for key in ("prompt_argument_sha256", "rubric_projection_sha256", "static_system_prefix_sha256"):
                _valid_sha(prompt_lineage[key], context=f"{context}:raw_prompt_lineage:{key}")
    _valid_sha(row.get("source_sha256"), context=f"{context}:source_sha256")
    _require(
        row["source_sha256"] == source_hashes[record_id],
        f"{context}: organizer source hash differs",
    )
    lineage = row.get("lineage")
    _require(isinstance(lineage, Mapping) and set(lineage) == LINEAGE_KEYS, f"{context}: invalid lineage")
    for key in ("provider", "model_family", "model_name"):
        value = lineage.get(key)
        _require(isinstance(value, str) and bool(value.strip()), f"{context}: empty {key}")
        forbidden = (
            "gemma",
            "production",
            "sub" + "mission" + "/",
            "sub" + "mission" + "\\",
        )
        _require(
            not any(marker in value.casefold() for marker in forbidden),
            f"{context}: production-related {key} is forbidden",
        )
    for key in ("prompt_sha256", "receipt_sha256"):
        _valid_sha(lineage.get(key), context=f"{context}:{key}")
    if schema == PASS_ROW_ROLE_SCHEMA:
        runner = row["role_provenance"]["raw_runner_kind"]
        expected_provider = "openai" if runner == "codex" else "anthropic"
        _require(lineage.get("provider") == expected_provider and
                 lineage["model_family"].startswith(expected_provider + ":"),
                 f"{context}: raw runner/provider lineage differs")
    _require(
        lineage.get("input_boundary") == "competition_source_only"
        and lineage.get("blind_first_pass") is True
        and lineage.get("peer_answer_visible") is False
        and lineage.get("production_output_visible") is False,
        f"{context}: source-only blind-first-pass declaration required",
    )
    ledger = row.get("ledger")
    _require(isinstance(ledger, Mapping), f"{context}: ledger must be an object")
    _require(
        ledger.get("schema_version") == full_record_output.LEDGER_SCHEMA_VERSION
        and ledger.get("record_id") == record_id,
        f"{context}: ledger identity differs",
    )


def _index_pass(
    path: pathlib.Path, *, role: str, source_hashes: Mapping[str, str], full: bool
) -> dict[str, int]:
    offsets: dict[str, int] = {}
    with path.open("rb") as handle:
        number = 0
        while True:
            offset = handle.tell()
            raw = handle.readline()
            if not raw:
                break
            number += 1
            _require(bool(raw.strip()), f"{path}:{number}: blank pass row")
            row = _parse_row(raw, location=f"{path}:{number}")
            _validate_pass_row(row, role=role, source_hashes=source_hashes, context=f"{path}:{number}")
            record_id = row["record_id"]
            _require(record_id not in offsets, f"{path}:{number}: duplicate pass ID {record_id}")
            offsets[record_id] = offset
    if full:
        missing = sorted(set(source_hashes) - set(offsets))
        _require(not missing, f"primary pass lacks {len(missing)} organizer IDs: {missing[:3]}")
    return offsets


def _read_at(handle: Any, offset: int, *, context: str) -> dict[str, Any]:
    handle.seek(offset)
    return _parse_row(handle.readline(), location=context)


def validate_ledger(record: Mapping[str, Any], ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Replay all source spans and 24 decisions against organizer bytes."""

    record_id = str(record["id"])
    _require(isinstance(ledger, Mapping), f"{record_id}: ledger is not an object")
    _require(
        set(ledger) == {"schema_version", "record_id", "source_spans", "cells"}
        and ledger.get("schema_version") == full_record_output.LEDGER_SCHEMA_VERSION
        and ledger.get("record_id") == record_id,
        f"{record_id}: ledger schema/identity differs",
    )
    cells = ledger.get("cells")
    spans = ledger.get("source_spans")
    _require(
        isinstance(cells, list)
        and len(cells) == len(ITEMS)
        and all(isinstance(cell, Mapping) for cell in cells)
        and tuple(cell.get("item") for cell in cells) == ITEMS,
        f"{record_id}: exactly ordered v1-v24 cells required",
    )
    _require(
        isinstance(spans, list) and all(isinstance(span, Mapping) for span in spans),
        f"{record_id}: invalid source spans",
    )
    for cell in cells:
        _require(cell.get("record_id") == record_id, f"{record_id}:{cell['item']}: cell ID differs")
    wire = {
        "source_span_ids": [span.get("span_id") for span in spans],
        "decisions": {
            cell["item"]: {key: cell.get(key) for key in DECISION_KEYS}
            for cell in cells
        },
    }
    try:
        rebuilt = full_record_output.canonical_ledger_projection(wire, record)
    except (TypeError, ValueError, full_record_output.FullRecordOutputError) as exc:
        raise ProvisionalReleaseError(f"{record_id}: invalid source-bound ledger: {exc}") from exc
    _require(dict(ledger) == rebuilt, f"{record_id}: ledger does not replay exactly from organizer source")
    return rebuilt


def _source_incomplete(record: Mapping[str, Any]) -> bool:
    completeness = record["input_completeness"]
    return bool(record["dropped_doc_counts"]) or any(value is False for value in completeness.values())


def _cell_release(
    record: Mapping[str, Any],
    primary: Mapping[str, Any] | None,
    secondary: Mapping[str, Any] | None,
    *,
    item_index: int,
) -> dict[str, Any]:
    first = primary["ledger"]["cells"][item_index] if primary is not None else None
    second = secondary["ledger"]["cells"][item_index] if secondary is not None else None
    source_missing = _source_incomplete(record)
    first_label = first["label"] if first is not None else None
    second_label = second["label"] if second is not None else None
    flags: list[str] = []
    if source_missing:
        flags.append("source_incomplete")
    if first is None:
        flags.append("missing_primary_vote")
    if first is None and second is not None:
        flags.append("secondary_without_primary")
    if first_label == "U":
        flags.append("primary_abstention")
    if second_label == "U":
        flags.append("secondary_abstention")
    if first is not None and second is None:
        flags.append("single_pass_unverified")
    elif first is not None and second is not None and first_label != second_label:
        flags.append("role_label_disagreement")
    if any(vote["confidence"] == "L" for vote in (first, second) if vote is not None):
        flags.append("low_confidence")
    elif any(vote["confidence"] == "M" for vote in (first, second) if vote is not None):
        flags.append("medium_confidence")
    if any(
        vote["completeness"] != "sufficient"
        for vote in (first, second)
        if vote is not None
    ):
        flags.append("item_completeness_boundary")
    if any(
        vote["material_missing_information"] is not None
        for vote in (first, second)
        if vote is not None
    ):
        flags.append("material_missing_information")
    if first_label == 1 or second_label == 1:
        flags.append("positive_label_review")
    if (
        second is not None
        and first_label == second_label == 1
        and first is not None
        and first["positive_evidence_span_id"] != second["positive_evidence_span_id"]
    ):
        flags.append("positive_evidence_divergence")

    if first is None:
        tier = "missing"
    elif source_missing or first_label == "U" or second_label == "U":
        tier = "abstain"
    elif second is not None and first_label != second_label:
        tier = "contested"
    elif second is not None:
        tier = "corroborated"
    else:
        tier = "draft"
    provisional_label = first_label if tier in ("draft", "corroborated") else None
    # A single vote is still unresolved for independent verification.  Even a
    # corroborated vote is only agreement, not an expert-audited gold decision.
    unresolved = tier != "corroborated"
    priority = "high" if unresolved else ("medium" if flags else "low")
    return {
        "schema_version": CELL_SCHEMA,
        "record_id": record["id"],
        "item": ITEMS[item_index],
        "source_sha256": _sha(record),
        "tier": tier,
        "provisional_label": provisional_label,
        "gold_label": None,
        "eligible_as_gold": False,
        "unresolved": unresolved,
        "risk_flags": flags,
        "review_priority": priority,
        "votes": {
            "primary": dict(first) if first is not None else None,
            "secondary": dict(second) if second is not None else None,
        },
        "lineage_receipt_sha256": {
            "primary": primary["lineage"]["receipt_sha256"] if primary is not None else None,
            "secondary": secondary["lineage"]["receipt_sha256"] if secondary is not None else None,
        },
    }


def build_release(
    *,
    records_path: pathlib.Path,
    primary_path: pathlib.Path,
    secondary_path: pathlib.Path | None,
    output_dir: pathlib.Path,
    expected_count: int = 20_000,
    emit_provisional_csv: bool = False,
) -> dict[str, Any]:
    """Build a fresh, atomic-directory provisional release; make no model calls."""

    records_path = pathlib.Path(records_path).resolve()
    primary_path = pathlib.Path(primary_path).resolve()
    secondary_path = pathlib.Path(secondary_path).resolve() if secondary_path is not None else None
    output_dir = pathlib.Path(output_dir).resolve()
    inputs = [records_path, primary_path] + ([secondary_path] if secondary_path is not None else [])
    _require(len(set(inputs)) == len(inputs), "input paths must be distinct")
    _require(not output_dir.exists(), f"fresh-only output already exists: {output_dir}")
    for path in inputs:
        _require(path.is_file(), f"missing input file: {path}")

    source_hashes = _source_index(records_path, expected_count=expected_count)
    primary_offsets = _index_pass(primary_path, role="candidate", source_hashes=source_hashes, full=False)
    secondary_offsets = (
        _index_pass(secondary_path, role="verifier", source_hashes=source_hashes, full=False)
        if secondary_path is not None
        else {}
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = pathlib.Path(tempfile.mkdtemp(prefix=output_dir.name + ".incomplete-", dir=output_dir.parent))
    counts: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()
    source_incomplete_records = 0
    cells_path = stage / "provisional_cells.jsonl"
    csv_path = stage / "provisional_opt_in.csv"
    try:
        with primary_path.open("rb") as primary_handle, cells_path.open("x", encoding="utf-8", newline="\n") as cells_handle:
            secondary_handle = secondary_path.open("rb") if secondary_path is not None else None
            csv_handle = csv_path.open("x", encoding="utf-8", newline="") if emit_provisional_csv else None
            try:
                csv_writer = csv.writer(csv_handle) if csv_handle is not None else None
                if csv_writer is not None:
                    csv_writer.writerow(["id", *ITEMS])
                for record in _organizer_rows(records_path):
                    record_id = record["id"]
                    _require(_sha(record) == source_hashes[record_id], f"{record_id}: source changed during release")
                    first = None
                    if record_id in primary_offsets:
                        first = _read_at(primary_handle, primary_offsets[record_id], context=f"candidate:{record_id}")
                        _validate_pass_row(first, role="candidate", source_hashes=source_hashes, context=f"candidate:{record_id}")
                        if first["lineage"]["provider"] == "anthropic":
                            _require(first["schema_version"] == PASS_ROW_ROLE_SCHEMA and
                                     first["role_provenance"]["raw_runner_kind"] == "claude",
                                     f"{record_id}: Claude primary requires explicit raw/release role provenance")
                        validate_ledger(record, first["ledger"])
                    second = None
                    if record_id in secondary_offsets:
                        assert secondary_handle is not None
                        second = _read_at(secondary_handle, secondary_offsets[record_id], context=f"verifier:{record_id}")
                        _validate_pass_row(second, role="verifier", source_hashes=source_hashes, context=f"verifier:{record_id}")
                        validate_ledger(record, second["ledger"])
                        if first is not None:
                            a, b = first["lineage"], second["lineage"]
                            _require(a["provider"] != b["provider"], f"{record_id}: verifier is not cross-provider")
                            _require(a["model_family"] != b["model_family"], f"{record_id}: verifier is not cross-family")
                            _require(a["prompt_sha256"] != b["prompt_sha256"], f"{record_id}: verifier prompt lineage is shared")
                            _require(a["receipt_sha256"] != b["receipt_sha256"], f"{record_id}: duplicate role receipt")
                        if second["lineage"]["provider"] == "openai":
                            _require(second["schema_version"] == PASS_ROW_ROLE_SCHEMA and
                                     second["role_provenance"]["raw_runner_kind"] == "codex",
                                     f"{record_id}: Sol secondary requires explicit raw/release role provenance")
                    if _source_incomplete(record):
                        source_incomplete_records += 1
                    csv_row: list[str | int] = [record_id]
                    for index in range(len(ITEMS)):
                        cell = _cell_release(record, first, second, item_index=index)
                        counts[cell["tier"]] += 1
                        risk_counts.update(cell["risk_flags"])
                        cells_handle.write(_canonical(cell) + "\n")
                        csv_value = (
                            cell["provisional_label"]
                            if cell["tier"] == "corroborated"
                            and not any(
                                risk in cell["risk_flags"]
                                for risk in (
                                    "low_confidence",
                                    "medium_confidence",
                                    "item_completeness_boundary",
                                    "material_missing_information",
                                    "positive_evidence_divergence",
                                )
                            )
                            else ""
                        )
                        csv_row.append(csv_value)
                    if csv_writer is not None:
                        csv_writer.writerow(csv_row)
            finally:
                if secondary_handle is not None:
                    secondary_handle.close()
                if csv_handle is not None:
                    csv_handle.close()

        summary = {
            "schema_version": SUMMARY_SCHEMA,
            "release_kind": "provisional_not_gold",
            "records": len(source_hashes),
            "cells": len(source_hashes) * len(ITEMS),
            "primary_records": len(primary_offsets),
            "secondary_records": len(secondary_offsets),
            "organizer_declared_incomplete_records": source_incomplete_records,
            "tier_counts": {tier: counts[tier] for tier in ("draft", "corroborated", "contested", "abstain", "missing")},
            "risk_flag_counts": dict(sorted(risk_counts.items())),
            "gold_cells": 0,
            "official_score": None,
        }
        (stage / "summary.json").write_text(_canonical(summary) + "\n", encoding="utf-8")
        manifest = {
            "schema_version": MANIFEST_SCHEMA,
            "release_kind": "provisional_not_gold",
            "assurance": {
                "organizer_source_and_ledger_replay_verified": True,
                "cross_family_is_declared_not_independently_attested": True,
                "model_competence_verified": False,
                "expert_audit_passed": False,
                "production_superiority_established": False,
                "eligible_as_production_tuning_target": False,
                "gold_answer_key": False,
            },
            "inputs": {
                "organizer": {"path": str(records_path), "sha256": _file_sha(records_path)},
                "primary": {"path": str(primary_path), "sha256": _file_sha(primary_path)},
                "secondary": {"path": str(secondary_path), "sha256": _file_sha(secondary_path)} if secondary_path is not None else None,
            },
            "outputs": {
                "provisional_cells.jsonl": _file_sha(cells_path),
                "summary.json": _file_sha(stage / "summary.json"),
                **({"provisional_opt_in.csv": _file_sha(csv_path)} if emit_provisional_csv else {}),
            },
            "source_module_sha256": _file_sha(pathlib.Path(__file__)),
        }
        manifest["manifest_sha256"] = _sha(manifest)
        (stage / "manifest.json").write_text(_canonical(manifest) + "\n", encoding="utf-8")
        _require(not output_dir.exists(), f"fresh-only output appeared during build: {output_dir}")
        stage.rename(output_dir)
        return summary
    except Exception:
        # Preserve the uniquely named incomplete staging directory as evidence;
        # never publish a partial release at the requested output path.
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=pathlib.Path, required=True)
    parser.add_argument("--primary", type=pathlib.Path, required=True)
    parser.add_argument("--secondary", type=pathlib.Path)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--expected-count", type=int, default=20_000)
    parser.add_argument("--emit-provisional-csv", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = build_release(
            records_path=args.records,
            primary_path=args.primary,
            secondary_path=args.secondary,
            output_dir=args.output_dir,
            expected_count=args.expected_count,
            emit_provisional_csv=args.emit_provisional_csv,
        )
    except (OSError, ValueError) as exc:
        print(f"Provisional release failed: {exc}", file=sys.stderr)
        return 2
    print(_canonical(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
