"""Verify independent full-record runs and emit provisional-pass rows.

This is a zero-model-call, fresh-output adapter.  It does not qualify a model,
adjudicate a disagreement, or turn provisional votes into gold.  The provider
and family in each output row come only from a verified runner manifest (Codex)
or a validated Claude CLI envelope plus the runner's pinned model contract.
The candidate/verifier *release order* is post-hoc and separately recorded;
it never rewrites a raw runner role or the prompt originally sent to a model.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import math
import os
import pathlib
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

try:
    from tools.independent_gold import (
        claude_full_record_annotator as claude,
        codex_full_record_annotator as codex,
        full_record_context,
        full_record_output,
        full_run_receipt_verify as codex_verify,
        provisional_release,
        review_ledger,
    )
except ModuleNotFoundError:  # Direct script invocation.
    import claude_full_record_annotator as claude  # type: ignore[no-redef]
    import codex_full_record_annotator as codex  # type: ignore[no-redef]
    import full_record_context  # type: ignore[no-redef]
    import full_record_output  # type: ignore[no-redef]
    import full_run_receipt_verify as codex_verify  # type: ignore[no-redef]
    import provisional_release  # type: ignore[no-redef]
    import review_ledger  # type: ignore[no-redef]


class PassRowBridgeError(ValueError):
    """A runner artifact cannot safely enter a provisional pass."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PassRowBridgeError(message)


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_object(value: Any) -> str:
    return _sha_bytes(_canonical(value).encode("utf-8"))


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _bad_constant(value: str) -> None:
    raise PassRowBridgeError(f"non-JSON numeric constant {value}")


def _json_bytes(raw: bytes, context: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_bad_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PassRowBridgeError(f"{context}: invalid UTF-8 JSON") from exc
    _require(isinstance(value, dict), f"{context}: JSON object required")
    return value


def _json_file(path: pathlib.Path, context: str) -> dict[str, Any]:
    _require(path.is_file(), f"{context}: missing {path}")
    return _json_bytes(path.read_bytes(), context)


def _canonical_file(
    path: pathlib.Path, context: str, *, allow_legacy_crlf_final: bool = False
) -> dict[str, Any]:
    _require(path.is_file(), f"{context}: missing {path}")
    raw = path.read_bytes()
    value = _json_bytes(raw, context)
    expected = (_canonical(value) + "\n").encode("utf-8")
    _require(
        raw == expected or (allow_legacy_crlf_final and raw == expected[:-1] + b"\r\n"),
        f"{context}: noncanonical bytes",
    )
    return value


def _file_sha(path: pathlib.Path) -> str:
    _require(path.is_file(), f"missing artifact {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_hex(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _role_provenance(manifest: Mapping[str, Any], runner_kind: str, release_role: str) -> dict[str, Any]:
    """Keep the original runner role/prompt separate from release vote order."""

    if runner_kind == "codex":
        raw_role = manifest["annotator_role"]
        prompt_lineage = copy.deepcopy(manifest["prompt_lineage"])
    else:
        raw_role = None  # Claude's raw manifest has no candidate/verifier role.
        prompt_lineage = {
            "prompt_protocol": manifest["prompt_protocol"],
            "prompt_argument_sha256": manifest["prompt_argument_sha256"],
            "rubric_projection_sha256": manifest["rubric_projection_sha256"],
            "static_system_prefix_sha256": manifest["static_system_prefix_sha256"],
            "context_mode": manifest["context_mode"],
        }
    return {
        "raw_runner_kind": runner_kind,
        "raw_runner_role": raw_role,
        "raw_pass_kind": manifest["pass_kind"],
        "raw_run_manifest_sha256": manifest["manifest_sha256"],
        "raw_prompt_lineage": prompt_lineage,
        "release_pass_role": release_role,
        "role_assignment_kind": "posthoc_independent_vote_order",
    }


def _verified_recovery_plan(
    plan_path: pathlib.Path, run_dir: pathlib.Path,
    organizer_source: pathlib.Path, organizer_sha256: str,
) -> dict[str, Any]:
    """Bind a completed recovery subset to its verified original shard plan."""

    plan = _json_file(plan_path, "Claude recovery plan")
    _require(plan.get("schema_version") == "dacon.independent.claude_manual_recovery_plan.v1" and
             pathlib.Path(str(plan.get("recovery_output") or "")).resolve() == run_dir and
             pathlib.Path(str(plan.get("organizer_input_path") or "")).resolve() == organizer_source and
             plan.get("organizer_input_sha256_from_manifest") == organizer_sha256,
             "Claude recovery plan source/output lineage differs")
    recovery_ids = plan.get("recovery_ids")
    parent_archive_proof = plan.get("source_archive_proof")
    _require(isinstance(recovery_ids, list) and bool(recovery_ids) and
             all(isinstance(value, str) and bool(value) for value in recovery_ids) and
             len(recovery_ids) == len(set(recovery_ids)),
             "Claude recovery plan IDs invalid")
    _require(isinstance(parent_archive_proof, Mapping),
             "Claude recovery parent source archive proof missing")
    # Lazy import avoids a module cycle: the planner itself uses this bridge
    # for read-only validation of the original shard's successful receipts.
    try:
        from tools.independent_gold import claude_recovery_plan as recovery
    except ModuleNotFoundError:
        import claude_recovery_plan as recovery  # type: ignore[no-redef]
    proof = recovery.verify_for_completed_recovery(plan_path, run_dir)
    _require(isinstance(proof, Mapping) and proof.get("valid") is True and
             proof.get("recovery_ids") == recovery_ids and
             proof.get("parent_source_archive_proof") == parent_archive_proof and
             parent_archive_proof.get("archive_binding") == "exact_run" and
             proof.get("recovery_run_manifest_sha256") == _canonical_file(
                 run_dir / "run_manifest.json", "Claude recovery run manifest"
             ).get("manifest_sha256"),
             "Claude recovery plan parent/archive/run proof differs")
    return {
        "plan_path": str(plan_path),
        "plan_sha256": _file_sha(plan_path),
        "selection_mode": "explicit_parent_shard_missing_ids",
        "recovery_ids": recovery_ids,
        "recovery_ids_sha256": _sha_object(recovery_ids),
        "parent_source_run": plan["source_run"],
        "parent_source_archive": plan["source_archive"],
        "parent_source_archive_proof": proof["parent_source_archive_proof"],
        "recovery_run_manifest_sha256": proof["recovery_run_manifest_sha256"],
    }


def _verify_archived_source(
    archive_dir: pathlib.Path, run_dir: pathlib.Path, run_manifest: Mapping[str, Any],
    runner_kind: str,
) -> dict[str, Any]:
    """Verify frozen source bytes against a self-hashed original run manifest.

    An archive can be reused by a later shard only when the run's embedded
    source-bundle hash and every archived source byte still match. Its own
    anchor-run hash remains visible in provenance, never silently rewritten.
    """

    archive = archive_dir.resolve()
    _require(archive.is_dir() and not archive_dir.is_symlink(),
             f"source archive missing or symlinked: {archive}")
    run_path = run_dir / "run_manifest.json"
    _require(run_manifest.get("manifest_sha256") == _sha_object({
        key: value for key, value in run_manifest.items() if key != "manifest_sha256"
    }), "original run manifest self-hash differs")
    archive_manifest_path = archive / "manifest.json"
    _require(not archive_manifest_path.is_symlink(), "source archive manifest is symlinked")
    frozen = _canonical_file(
        archive_manifest_path, "source archive manifest", allow_legacy_crlf_final=True
    )
    _require(set(frozen) == {
        "schema_version", "bundle_sha256", "files", "rubric_sha256",
        "run_manifest_sha256", "archive_kind",
    }, "source archive manifest shape differs")
    _require(frozen["schema_version"] == "dacon.independent.source_archive.v1" and
             frozen["archive_kind"] == "mechanical_source_bytes_only_no_model_call",
             "source archive kind differs")
    anchor = frozen["run_manifest_sha256"]
    _require(anchor is None or _sha256_hex(anchor), "source archive anchor hash invalid")
    bundle = run_manifest.get("imported_source_bundle" if runner_kind == "codex" else "source_bundle")
    _require(isinstance(bundle, Mapping) and isinstance(bundle.get("files"), list),
             "original run source bundle missing")
    bundle_payload = (
        {"schema_version": bundle.get("schema_version"), "files": bundle["files"]}
        if runner_kind == "codex" else bundle["files"]
    )
    _require(_sha_object(bundle_payload) == bundle.get("bundle_sha256") == frozen["bundle_sha256"],
             "archived source bundle hash differs from original run")
    files = frozen["files"]
    _require(isinstance(files, list) and bool(files), "source archive file list missing")
    indexed: dict[str, str] = {}
    for entry in files:
        _require(isinstance(entry, Mapping) and set(entry) == {"path", "sha256"},
                 "source archive file entry invalid")
        relative = entry["path"]
        expected_sha = entry["sha256"]
        _require(isinstance(relative, str) and _sha256_hex(expected_sha),
                 "source archive file hash/path invalid")
        safe = pathlib.PurePosixPath(relative)
        _require(not safe.is_absolute() and safe.as_posix() == relative and
                 all(part not in {"", ".", ".."} for part in safe.parts) and
                 relative not in indexed, "source archive path unsafe or duplicated")
        target = archive.joinpath(*safe.parts)
        _require(not target.is_symlink() and target.resolve().is_relative_to(archive) and
                 _file_sha(target) == expected_sha, f"source archive byte hash differs: {relative}")
        indexed[relative] = expected_sha
    expected_paths: set[str] = set()
    for entry in bundle["files"]:
        _require(isinstance(entry, Mapping) and set(entry) == {"path", "sha256"} and
                 indexed.get(entry["path"]) == entry["sha256"],
                 "original run source file absent or changed in archive")
        expected_paths.add(entry["path"])
    if runner_kind == "codex":
        for reference in (
            run_manifest.get("runner_source"),
            (run_manifest.get("prompt_lineage") or {}).get("builder_source"),
            (run_manifest.get("prompt_lineage") or {}).get("registry_source"),
        ):
            _require(isinstance(reference, Mapping), "original run source reference missing")
            source_path = pathlib.Path(str(reference.get("path") or ""))
            relative = (
                source_path.resolve().relative_to(codex.ROOT.resolve()).as_posix()
                if source_path.is_absolute() else source_path.as_posix()
            )
            _require(indexed.get(relative) == reference.get("sha256"),
                     "original run source reference differs from archive")
            expected_paths.add(relative)
        expected_rubric = (run_manifest.get("semantic_config") or {}).get("rubric_sha256")
    else:
        expected_rubric = run_manifest.get("rubric_source_sha256")
    _require(set(indexed) == expected_paths,
             "source archive contains files outside original run source bundle")
    rubric_path = archive / "tools" / "independent_gold" / "rubric_v1.md"
    _require(_sha256_hex(expected_rubric) and expected_rubric == frozen["rubric_sha256"] and
             not rubric_path.is_symlink() and _file_sha(rubric_path) == expected_rubric,
             "original run rubric differs from archive")
    _require(not any(path.is_symlink() for path in archive.rglob("*")),
             "source archive contains a symlink")
    actual_files = {
        path.relative_to(archive).as_posix() for path in archive.rglob("*") if path.is_file()
    }
    _require(actual_files == set(indexed) | {
        "manifest.json", "tools/independent_gold/rubric_v1.md"
    }, "source archive has unexpected or missing files")
    # An archived run may outlive a source edit, but this bridge only permits
    # the explicitly opt-in qualification-context change. Every other live
    # source byte must still equal the archived byte; each successful task is
    # then rebuilt below and compared to its persisted context and prompt.
    allowed_drift = "tools/independent_gold/qualification_context.py"
    live_drift: list[str] = []
    live_source_sha256: dict[str, str] = {}
    for relative, expected_sha in indexed.items():
        live_path = codex.ROOT.joinpath(*pathlib.PurePosixPath(relative).parts)
        _require(not live_path.is_symlink() and live_path.resolve().is_relative_to(codex.ROOT.resolve()),
                 f"live source path unsafe: {relative}")
        live_sha = _file_sha(live_path)
        live_source_sha256[relative] = live_sha
        if live_sha != expected_sha:
            live_drift.append(relative)
    _require(set(live_drift) <= {allowed_drift},
             f"live source drift outside qualification-context opt-in: {sorted(set(live_drift) - {allowed_drift})}")
    _require(_file_sha(codex.RUBRIC_PATH) == expected_rubric,
             "live rubric differs from original run")
    return {
        "archive_path": str(archive),
        "archive_manifest_sha256": _file_sha(archive_manifest_path),
        "source_bundle_sha256": bundle["bundle_sha256"],
        "run_manifest_sha256": run_manifest["manifest_sha256"],
        "run_manifest_file_sha256": _file_sha(run_path),
        "archive_anchor_run_manifest_sha256": anchor,
        "archive_binding": "exact_run" if anchor == run_manifest["manifest_sha256"] else "shared_source_bytes",
        "live_source_drift_allowed": live_drift,
        "live_source_sha256": live_source_sha256,
    }


def _spool_records(source: pathlib.Path, spool: pathlib.Path) -> tuple[list[str], dict[str, tuple[int, str]]]:
    """Preserve a seekable organizer copy without retaining 20k large records in RAM."""

    order: list[str] = []
    index: dict[str, tuple[int, str]] = {}
    with (gzip.open(source, "rb") if source.suffix.lower() == ".gz" else source.open("rb")) as src, spool.open("xb") as dst:
        for line_number, raw in enumerate(src, 1):
            _require(bool(raw.strip()), f"organizer line {line_number}: blank row")
            record = _json_bytes(raw, f"organizer line {line_number}")
            try:
                review_ledger._validate_organizer_record(record)  # type: ignore[attr-defined]
            except ValueError as exc:
                raise PassRowBridgeError(f"organizer line {line_number}: {exc}") from exc
            record_id = record.get("id")
            _require(isinstance(record_id, str) and bool(record_id), f"organizer line {line_number}: invalid id")
            _require(record_id not in index, f"duplicate organizer ID {record_id}")
            offset = dst.tell()
            dst.write((_canonical(record) + "\n").encode("utf-8"))
            index[record_id] = (offset, _sha_object(record))
            order.append(record_id)
    _require(bool(order), "organizer input is empty")
    return order, index


def _record_at(handle: Any, location: tuple[int, str], record_id: str) -> dict[str, Any]:
    handle.seek(location[0])
    record = _json_bytes(handle.readline(), f"organizer record {record_id}")
    _require(record.get("id") == record_id, f"{record_id}: organizer offset changed")
    _require(_sha_object(record) == location[1], f"{record_id}: organizer source changed")
    return record


def _checkpoint_offsets(
    path: pathlib.Path, selected: Sequence[str], *, allow_partial: bool = False,
) -> tuple[dict[str, int], bool]:
    if allow_partial and not path.exists():
        return {}, False
    _require(path.is_file(), f"missing runner checkpoint {path}")
    offsets: dict[str, int] = {}
    ignored_truncated_tail = False
    expected = set(selected)
    with path.open("rb") as handle:
        while True:
            offset = handle.tell()
            raw = handle.readline()
            if not raw:
                break
            if not raw.endswith(b"\n") and allow_partial:
                # A crash may leave a final unfinished append. It is never a
                # successful row and is not parsed, trusted, or emitted.
                ignored_truncated_tail = True
                break
            _require(bool(raw.strip()) and raw.endswith(b"\n"), f"{path}: blank/partial checkpoint row")
            row = _json_bytes(raw, f"{path}:{len(offsets) + 1}")
            _require(raw == (_canonical(row) + "\n").encode("utf-8"), f"{path}: noncanonical checkpoint row")
            record_id = row.get("id")
            _require(isinstance(record_id, str) and record_id in expected, f"{path}: out-of-cohort row {record_id!r}")
            _require(record_id not in offsets, f"{path}: duplicate row {record_id}")
            offsets[record_id] = offset
    if not allow_partial:
        _require(set(offsets) == expected, f"{path}: selected IDs and successful checkpoint rows differ")
    return offsets, ignored_truncated_tail


def _checkpoint_row(handle: Any, offset: int, record_id: str) -> dict[str, Any]:
    handle.seek(offset)
    raw = handle.readline()
    row = _json_bytes(raw, f"checkpoint:{record_id}")
    _require(row.get("id") == record_id, f"{record_id}: checkpoint offset changed")
    _require(raw == (_canonical(row) + "\n").encode("utf-8"), f"{record_id}: checkpoint bytes changed")
    return row


def _codex_manifest(run_dir: pathlib.Path, source: pathlib.Path, source_sha256: str, role: str, order: Sequence[str], catalogs: tuple[Any, Any], *, archived: bool = False, allow_partial: bool = False) -> tuple[dict[str, Any], list[str]]:
    manifest = (_canonical_file if archived else _json_file)(run_dir / "run_manifest.json", "Codex run manifest")
    _require(manifest.get("manifest_sha256") == _sha_object({
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    }), "Codex run manifest self-hash differs")
    _require(manifest.get("annotator_role") == role, "Codex run role differs")
    phase = manifest.get("phase")
    _require(phase in {"unlabeled_20000", "continuity_canary", "official_dev", "development_diagnostic"}, "unsupported Codex phase")
    _require(manifest.get("pass_kind") == codex.PASS_KIND, "Codex run is not blind first pass")
    _require(manifest.get("peer_visibility") == codex.base.blind_peer_visibility(), "Codex peer vote was visible")
    _require(pathlib.Path(str(manifest.get("staging_dir") or "")).resolve() == run_dir, "Codex staging path differs")
    record_input = manifest.get("record_input")
    _require(isinstance(record_input, Mapping), "Codex organizer input missing")
    _require(pathlib.Path(str(record_input.get("path") or "")).resolve() == source, "Codex organizer path differs")
    _require(record_input.get("sha256") == source_sha256, "Codex organizer input hash differs")
    plan = manifest.get("cohort_plan")
    _require(isinstance(plan, Mapping), "Codex cohort plan missing")
    plan_file = _canonical_file if archived else _json_file
    _require(plan_file(run_dir / "cohort_plan.json", "Codex cohort plan") == plan, "Codex persisted cohort plan differs")
    if archived:
        _require(plan.get("manifest_sha256") == _sha_object({
            key: value for key, value in plan.items() if key != "manifest_sha256"
        }) and plan.get("record_input_sha256") == source_sha256 and
                 plan.get("phase") == phase and plan.get("annotation_topology") == "full_record_rows" and
                 plan.get("selected_groups") == ["v1-24"] and
                 plan.get("target_items_by_group") == {"v1-24": list(provisional_release.ITEMS)},
                 "Codex archived cohort plan lineage differs")
    else:
        codex.validate_cohort_plan(plan, input_path=source, phase=str(phase))
    partition = plan["partitioning"]
    # The source was already fully parsed into organizer order by the bridge.
    # Reproduce the runner's filter→stride→limit selection from that immutable
    # order instead of decompressing the 20k-record input once per shard.
    requested = set(plan["requested_ids"])
    _require(requested <= set(order), "Codex cohort requests an unknown organizer ID")
    matched = [record_id for record_id in order if not requested or record_id in requested]
    sharded = matched[partition["shard_index"]::partition["shard_count"]]
    limit = partition["limit_after_sharding"]
    selection = {
        "input_ids": tuple(order), "matched_ids": tuple(matched),
        "requested_ids": tuple(plan["requested_ids"]),
        "selected_ids": tuple(sharded if limit is None else sharded[:limit]),
        "partitioning": dict(partition),
    }
    if archived:
        _require(plan.get("selected_ids") == list(selection["selected_ids"]) and
                 plan.get("selected_id_count") == len(selection["selected_ids"]) and
                 plan.get("selected_group_row_count") == len(selection["selected_ids"]) and
                 plan.get("selected_ids_sha256") == _sha_object(list(selection["selected_ids"])) and
                 plan.get("selected_group_rows_sha256") == _sha_object([
                     [record_id, "v1-24"] for record_id in selection["selected_ids"]
                 ]) and plan.get("matched_record_count") == len(selection["matched_ids"]),
                 "Codex archived cohort selection differs")
    else:
        _require(plan == codex.build_cohort_plan(input_path=source, selection=selection, phase=str(phase)), "Codex cohort selection is stale")
    _require(plan["input_record_count"] == len(order), "Codex organizer count differs")
    lineage = manifest.get("prompt_lineage")
    _require(isinstance(lineage, Mapping) and lineage.get("annotator_role") == role and
             lineage.get("lineage_sha256") == _sha_object({
                 key: value for key, value in lineage.items() if key != "lineage_sha256"
             }), "Codex prompt lineage differs")
    if archived:
        model = (manifest.get("semantic_config") or {}).get("model")
        _require(isinstance(model, str) and bool(model) and lineage.get("required_model") == model,
                 "Codex archived role model differs")
        profile = None
    else:
        profile_name = codex.prompt_profiles.DEFAULT_PROFILE_BY_ROLE[role]
        _require(lineage.get("profile") == profile_name, "Codex role prompt differs")
        profile = codex.prompt_profiles.get_profile(profile_name, annotator_role=role)
        model = codex.ROLE_MODEL[role]
        _require(manifest.get("semantic_config", {}).get("model") == model, "Codex role model differs")
    cli = manifest.get("codex_cli")
    _require(isinstance(cli, Mapping), "Codex CLI provenance missing")
    executable = pathlib.Path(str(cli.get("resolved_executable") or ""))
    if not archived:
        _require(_file_sha(executable) == cli.get("executable_sha256"), "Codex CLI executable differs")
    else:
        _require(_sha256_hex(cli.get("executable_sha256")) and isinstance(cli.get("version_output"), str),
                 "Codex archived CLI provenance invalid")
    codex_verify._verify_model_identity(manifest.get("model_identity"), model=model, context="bridge Codex run")  # type: ignore[attr-defined]
    if archived:
        tuple_payload = {
            "schema_version": manifest["schema_version"], "annotator_role": role,
            "pass_kind": manifest["pass_kind"], "prompt_lineage": lineage,
            "model_identity": manifest["model_identity"],
            "semantic_config": manifest["semantic_config"],
            "imported_source_bundle": manifest["imported_source_bundle"],
            "codex_cli": {"executable_sha256": cli["executable_sha256"], "version_output": cli["version_output"]},
            "execution_isolation": manifest["execution_isolation"],
            "peer_visibility": manifest["peer_visibility"],
        }
        _require(manifest.get("tuple_sha256") == _sha_object(tuple_payload),
                 "Codex archived tuple hash differs")
        instance_sha = _sha_object({
            "tuple_sha256": manifest["tuple_sha256"],
            "record_input_sha256": source_sha256, "phase": phase,
            "cohort_plan_sha256": plan["manifest_sha256"],
        })
        _require(manifest.get("run_instance_sha256") == instance_sha and
                 manifest.get("run_key") == instance_sha,
                 "Codex archived run-instance hash differs")
    else:
        rebuilt = codex.build_run_manifest(
            input_path=source, staging_dir=run_dir, model=model,
            reasoning_effort=str(manifest["semantic_config"]["reasoning_effort"]),
            cli_provenance=cli, rubric=codex.RUBRIC_PATH.read_text(encoding="utf-8"),
            fact_catalog=catalogs[0], qualification_catalog=catalogs[1],
            annotator_role=role, prompt_profile=profile, phase=str(phase),
            cohort_plan=plan, model_identity=manifest["model_identity"],
        )
        _require(manifest == rebuilt, "Codex run manifest differs from current runner/source/rubric")
    selected = list(plan["selected_ids"])
    expected_slugs = {codex.base._task_slug(record_id) for record_id in selected}
    tasks = run_dir / "tasks"
    actual_slugs = {p.name for p in tasks.iterdir() if p.is_dir()} if tasks.is_dir() else set()
    _require(tasks.is_dir() and (actual_slugs <= expected_slugs if allow_partial else actual_slugs == expected_slugs),
             "Codex task directory cohort differs")
    return manifest, selected


def _verify_archived_context(record: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    """Recheck immutable organizer bytes and every supplied source span.

    The full context is independently rebuilt from the current compatible
    source checkout and compared byte-for-byte in each runner's task check.
    """

    record_id = str(record["id"])
    organizer = context.get("organizer_record")
    _require(isinstance(organizer, Mapping) and organizer.get("id") == record_id and
             organizer.get("record_sha256") == _sha_object(record),
             f"{record_id}: archived context organizer identity differs")
    fields = {key: copy.deepcopy(value) for key, value in record.items() if key not in {"id", "docs"}}
    _require(organizer.get("fields") == fields, f"{record_id}: archived organizer fields differ")
    _require(context.get("group") == "v1-24" and
             context.get("target_items") == list(provisional_release.ITEMS),
             f"{record_id}: archived context is not full-24")
    completeness = context.get("source_completeness")
    documents = record.get("docs")
    _require(isinstance(documents, list) and isinstance(completeness, Mapping) and
             completeness.get("full_supplied_document_text_visible") is True and
             completeness.get("source_chars") == sum(len(doc["text"]) for doc in documents) and
             completeness.get("input_completeness") == (record.get("input_completeness") or {}) and
             completeness.get("dropped_doc_counts") == (record.get("dropped_doc_counts") or {}),
             f"{record_id}: archived source completeness differs")
    document_manifest = organizer.get("documents")
    registry = context.get("allowed_span_registry")
    _require(isinstance(document_manifest, list) and len(document_manifest) == len(documents) and
             isinstance(registry, Mapping), f"{record_id}: archived document registry invalid")
    seen: set[str] = set()
    for index, (doc, saved) in enumerate(zip(documents, document_manifest)):
        _require(isinstance(doc, Mapping) and isinstance(saved, Mapping) and
                 isinstance(doc.get("text"), str), f"{record_id}: invalid organizer document")
        source_text = doc["text"]
        doc_id = str(doc.get("doc_id") or f"D{index}")
        doc_type = str(doc.get("type") or "unknown")
        doc_sha = _sha_bytes(source_text.encode("utf-8"))
        _require(all((saved.get(key) == expected) for key, expected in {
            "doc_index": index, "doc_id": doc_id, "doc_type": doc_type,
            "chars": len(source_text), "sha256": doc_sha,
        }.items()), f"{record_id}: archived document manifest differs")
        span_ids = saved.get("span_ids")
        _require(isinstance(span_ids, list) and all(isinstance(value, str) for value in span_ids),
                 f"{record_id}: archived source span IDs invalid")
        cursor = 0
        for span_id in span_ids:
            _require(span_id not in seen, f"{record_id}: duplicate archived source span")
            seen.add(span_id)
            span = registry.get(span_id)
            _require(isinstance(span, Mapping) and span.get("span_id") == span_id and
                     span.get("doc_index") == index and span.get("doc_id") == doc_id and
                     span.get("doc_type") == doc_type and span.get("source_doc_sha256") == doc_sha,
                     f"{record_id}: archived span provenance differs")
            start, end = span.get("start"), span.get("end")
            _require(type(start) is int and type(end) is int and
                     start == cursor and start < end <= len(source_text),
                     f"{record_id}: archived source spans gap or overlap")
            quote = source_text[start:end]
            _require(span.get("quote") == quote and
                     span.get("text_sha256") == _sha_bytes(quote.encode("utf-8")),
                     f"{record_id}: archived source span quote differs")
            cursor = end
        _require(cursor == len(source_text), f"{record_id}: archived source text not fully covered")
    _require(seen == set(registry), f"{record_id}: archived registry contains extra spans")
    bounds = context.get("bounds")
    _require(isinstance(bounds, Mapping) and bounds.get("truncated") is False and
             bounds.get("rendered_chars") == len(_canonical(context)),
             f"{record_id}: archived context truncation/bounds differ")
    hashable = copy.deepcopy(dict(context))
    hashable.pop("context_sha256", None)
    hashable["bounds"].pop("rendered_chars", None)
    _require(context.get("context_sha256") == _sha_object(hashable),
             f"{record_id}: archived context hash differs")


def _codex_archived_task(
    record: Mapping[str, Any], manifest: Mapping[str, Any], run_dir: pathlib.Path,
) -> tuple[pathlib.Path, dict[str, Any], dict[str, Any]]:
    record_id = str(record["id"])
    task_dir = run_dir / "tasks" / codex.base._task_slug(record_id) / "v1-24"
    _require(task_dir.is_dir(), f"{record_id}: archived Codex task directory missing")
    task = _canonical_file(task_dir / "task_manifest.json", f"{record_id} archived Codex task")
    _require(set(task) == codex.TASK_MANIFEST_KEYS and
             task.get("manifest_sha256") == _sha_object({
                 key: value for key, value in task.items() if key != "manifest_sha256"
             }), f"{record_id}: archived Codex task manifest differs")
    _require(task.get("record_id") == record_id and
             task.get("record_source_sha256") == _sha_object(record) and
             task.get("run_key") == manifest["run_key"] and
             task.get("run_manifest_sha256") == manifest["manifest_sha256"] and
             task.get("group") == "v1-24" and
             task.get("target_items") == list(provisional_release.ITEMS),
             f"{record_id}: archived Codex task source/run differs")
    for key, expected in codex_verify._lineage_projection(manifest).items():  # type: ignore[attr-defined]
        _require(task.get(key) == expected, f"{record_id}: archived task {key} differs")
    artifacts = task.get("artifact_file_sha256")
    _require(isinstance(artifacts, Mapping) and set(artifacts) == {
        "full_record_context.json", "output_schema.json", "prompt.txt"
    }, f"{record_id}: archived Codex artifact hash list differs")
    raw = {name: (task_dir / name).read_bytes() for name in artifacts}
    for name, expected in artifacts.items():
        _require(_sha_bytes(raw[name]) == expected, f"{record_id}: archived {name} byte hash differs")
    context = _json_bytes(raw["full_record_context.json"], f"{record_id} archived context")
    schema = _json_bytes(raw["output_schema.json"], f"{record_id} archived schema")
    _require(raw["full_record_context.json"] == _canonical(context).encode("utf-8") and
             raw["output_schema.json"] == _canonical(schema).encode("utf-8"),
             f"{record_id}: archived task JSON bytes noncanonical")
    _verify_archived_context(record, context)
    _require(task.get("full_record_context_sha256") == context["context_sha256"] and
             task.get("full_record_context_schema_version") == context.get("schema_version") and
             task.get("full_record_context_bounds") == context.get("bounds") and
             task.get("rubric_sha256") == manifest["semantic_config"]["rubric_sha256"] and
             task.get("system_prompt_sha256") == task["rubric_sha256"] and
             task.get("prompt_sha256") == _sha_bytes(raw["prompt.txt"]) and
             task.get("output_schema_sha256") == _sha_object(schema),
             f"{record_id}: archived context/prompt/schema lineage differs")
    _require({p.name for p in task_dir.iterdir()} == set(artifacts) | {
        "task_manifest.json", "attempts"
    }, f"{record_id}: archived Codex task artifact set differs")
    return task_dir, task, context


def _codex_pass(record: Mapping[str, Any], row: Mapping[str, Any], manifest: Mapping[str, Any], run_dir: pathlib.Path, role: str, catalogs: tuple[Any, Any], *, archived: bool = False) -> dict[str, Any]:
    record_id = str(record["id"])
    if archived:
        _codex_archived_task(record, manifest, run_dir)
    task_dir, task_manifest, context = codex_verify._verify_task(  # type: ignore[attr-defined]
        run_dir=run_dir, manifest=manifest, record=record,
        fact_catalog=catalogs[0], qualification_catalog=catalogs[1],
    )
    codex_verify._verify_attempts(  # type: ignore[attr-defined]
        task_dir=task_dir, checkpoint_row=row, task_manifest=task_manifest,
        manifest=manifest, record=record, source_context=context,
        policy_frozen=datetime.min.replace(tzinfo=timezone.utc), context=f"bridge Codex {record_id}",
    )
    identity = manifest["model_identity"]
    _require(identity["provider"] == "openai" and identity["family"].startswith("openai:"), "Codex provider/family is not the verified OpenAI role")
    ledger = row["ledger"]
    _require(len(ledger["cells"]) == 24, f"{record_id}: Codex ledger is not full-24")
    return {
        "schema_version": provisional_release.PASS_ROW_ROLE_SCHEMA,
        "record_id": record_id,
        "source_sha256": _sha_object(record),
        "annotator_role": manifest["annotator_role"],
        "release_pass_role": role,
        "role_provenance": _role_provenance(manifest, "codex", role),
        "lineage": {
            "provider": identity["provider"], "model_family": identity["family"],
            "model_name": identity["requested_model"],
            "prompt_sha256": row["prompt_sha256"],
            "receipt_sha256": _sha_object(row),
            "input_boundary": "competition_source_only", "blind_first_pass": True,
            "peer_answer_visible": False, "production_output_visible": False,
        },
        "ledger": ledger,
    }


def _claude_manifest(run_dir: pathlib.Path, source: pathlib.Path, source_sha256: str, order: Sequence[str], catalogs: tuple[Any, Any], *, archived: bool = False, allow_partial: bool = False, recovery_ids: Sequence[str] | None = None) -> tuple[dict[str, Any], list[str]]:
    manifest = _canonical_file(run_dir / "run_manifest.json", "Claude run manifest")
    manifest_keys = {
        "schema_version", "input_path", "input_sha256", "input_count", "selected_ids",
        "selected_ids_sha256", "shard_index", "shard_count", "phase",
        "rubric_source_sha256", "rubric_projection_sha256", "static_system_prefix_sha256",
        "static_system_prefix_bytes", "cache_reuse_assumption", "schema_sha256",
        "prompt_protocol", "context_mode", "compact_projection_schema",
        "prompt_argument_sha256", "requested_model", "expected_observed_model",
        "model_identity_mode", "resolved_revision", "effort", "max_budget_usd_per_call",
        "cli", "source_bundle", "source_context", "pass_kind", "no_tools",
        "session_persistence", "execute", "qualification_status", "manifest_sha256",
    }
    _require(set(manifest) in (manifest_keys, manifest_keys | {"continue_on_content_error"}),
             "Claude run manifest keys differ")
    if "continue_on_content_error" in manifest:
        _require(type(manifest["continue_on_content_error"]) is bool,
                 "Claude content-error continuation policy invalid")
    _require(manifest.get("schema_version") == claude.RUN_SCHEMA, "wrong Claude runner schema")
    _require(manifest.get("manifest_sha256") == _sha_object({k: v for k, v in manifest.items() if k != "manifest_sha256"}), "Claude manifest hash differs")
    _require(manifest.get("phase") in {"unlabeled_20000", "official_dev", "development_diagnostic"}, "unsupported Claude phase")
    _require(manifest.get("pass_kind") == "blind_first_pass" and manifest.get("no_tools") is True and manifest.get("session_persistence") is False, "Claude run is not blind/tool-free")
    _require(manifest.get("execute") is True, "Claude run did not execute")
    _require(manifest.get("qualification_status") == "unqualified_provisional", "Claude run claims qualification")
    _require(pathlib.Path(str(manifest.get("input_path") or "")).resolve() == source, "Claude organizer path differs")
    _require(manifest.get("input_sha256") == source_sha256 and type(manifest.get("input_count")) is int and manifest["input_count"] == len(order), "Claude organizer input differs")
    if manifest["phase"] == "unlabeled_20000":
        _require(source == (claude.ROOT / "data_open" / "train_unlabeled.jsonl.gz").resolve(), "Claude unlabeled phase uses another source")
    elif manifest["phase"] == "official_dev":
        _require(source == (claude.ROOT / "data_open" / "dev.jsonl.gz").resolve(), "Claude official-dev phase uses another source")
    shard_index, shard_count = manifest.get("shard_index"), manifest.get("shard_count")
    _require(type(shard_index) is int and type(shard_count) is int and shard_count > 0 and 0 <= shard_index < shard_count, "Claude shard metadata invalid")
    selected = manifest.get("selected_ids")
    _require(isinstance(selected, list) and bool(selected) and len(selected) == len(set(selected)), "Claude selected IDs invalid")
    _require(all(isinstance(x, str) and x in set(order) for x in selected), "Claude selected IDs are not organizer IDs")
    _require(selected == [x for x in order if x in set(selected)], "Claude selected IDs are not in organizer order")
    if recovery_ids is None:
        _require(selected == list(order[shard_index::shard_count])[:len(selected)],
                 "Claude selected IDs are not the declared shard prefix")
    else:
        _require(archived and manifest["phase"] == "unlabeled_20000" and
                 run_dir.name.startswith("recovery_claude_") and
                 shard_index == 0 and shard_count == 1 and
                 selected == list(recovery_ids),
                 "Claude recovery selection differs from the exact approved subset")
    _require(manifest.get("selected_ids_sha256") == _sha_object(selected), "Claude selected IDs hash differs")
    if not archived:
        _require(manifest.get("requested_model") == claude.REQUESTED_MODEL and manifest.get("expected_observed_model") == claude.OBSERVED_MODEL, "Claude model contract differs")
    else:
        _require(isinstance(manifest.get("requested_model"), str) and
                 isinstance(manifest.get("expected_observed_model"), str) and
                 bool(manifest["requested_model"]) and bool(manifest["expected_observed_model"]),
                 "Claude archived model contract missing")
    _require(manifest.get("model_identity_mode") == "opaque_hosted_alias" and manifest.get("resolved_revision") is None, "Claude alias falsely claims a revision")
    _require(manifest.get("effort") == claude.EFFORT or archived, "Claude reasoning effort differs")
    _require(manifest.get("context_mode") in {"full", "compact"}, "Claude context mode invalid")
    if not archived:
        _require(manifest.get("prompt_protocol") == (claude.COMPACT_PROMPT_PROTOCOL if manifest["context_mode"] == "compact" else claude.PROMPT_PROTOCOL), "Claude prompt protocol differs")
        _require(manifest.get("compact_projection_schema") == (claude.COMPACT_SCHEMA if manifest["context_mode"] == "compact" else None), "Claude compact schema differs")
        _require(manifest.get("prompt_argument_sha256") == claude.sha256_text(claude.PROMPT_ARGUMENT), "Claude prompt argument differs")
        rubric = claude.RUBRIC_PATH.read_text(encoding="utf-8")
        projected = claude.annotation_rubric(rubric)
        _require(manifest.get("rubric_source_sha256") == claude.sha256_text(rubric) and manifest.get("rubric_projection_sha256") == claude.sha256_text(projected), "Claude rubric differs")
        system = claude.system_prompt(projected, context_mode=manifest["context_mode"])
        _require(manifest.get("static_system_prefix_sha256") == claude.sha256_text(system) and manifest.get("static_system_prefix_bytes") == len(system.encode("utf-8")), "Claude static system prefix differs")
    else:
        for key in ("prompt_protocol", "prompt_argument_sha256", "rubric_source_sha256",
                    "rubric_projection_sha256", "static_system_prefix_sha256", "schema_sha256"):
            _require(bool(manifest.get(key)), f"Claude archived {key} missing")
    _require(manifest.get("cache_reuse_assumption") == "none; only actual CLI usage counters are evidence", "Claude cache assumption differs")
    budget = manifest.get("max_budget_usd_per_call")
    _require(type(budget) in (int, float) and math.isfinite(budget) and budget > 0, "Claude per-call budget invalid")
    if not archived:
        schema = claude.canonical_json(full_record_output.output_schema())
        _require(manifest.get("schema_sha256") == claude.sha256_text(schema), "Claude output schema differs")
        _require(manifest.get("source_bundle") == claude.source_bundle(), "Claude source bundle differs")
        source_context = {
            "schema_version": full_record_context.SCHEMA_VERSION,
            "fact_schema_version": full_record_context.fact_context.SCHEMA_VERSION,
            "qualification_schema_version": full_record_context.qualification_context.SCHEMA_VERSION,
            "law_schema_version": full_record_context.law_context.SCHEMA_VERSION,
            "fact_catalog_sha256": catalogs[0].sha256,
            "qualification_catalog_sha256": catalogs[1].sha256,
            "law_reference_manifest_sha256": full_record_context.law_context.reference_manifest_sha256(),
            "full_supplied_source": True, "truncation_allowed": False,
        }
        _require(manifest.get("source_context") == source_context, "Claude source context lineage differs")
    else:
        _require(isinstance(manifest.get("source_context"), Mapping) and
                 manifest["source_context"].get("full_supplied_source") is True and
                 manifest["source_context"].get("truncation_allowed") is False,
                 "Claude archived source-context policy differs")
    cli = manifest.get("cli")
    _require(isinstance(cli, Mapping), "Claude CLI provenance missing")
    _require(set(cli) == {"requested_executable", "resolved_executable", "executable_sha256", "version_output", "version_stderr_sha256"}, "Claude CLI provenance keys differ")
    exe = pathlib.Path(str(cli.get("resolved_executable") or ""))
    if not archived:
        _require(_file_sha(exe) == cli.get("executable_sha256"), "Claude CLI executable differs")
    else:
        _require(_sha256_hex(cli.get("executable_sha256")), "Claude archived CLI hash invalid")
    _require(isinstance(cli.get("version_output"), str) and cli["version_output"].startswith("2.1.276 (Claude Code)"), "Claude CLI version differs")
    _require(isinstance(cli.get("version_stderr_sha256"), str) and len(cli["version_stderr_sha256"]) == 64 and all(char in "0123456789abcdef" for char in cli["version_stderr_sha256"]), "Claude CLI stderr provenance invalid")
    summary_path = run_dir / "run_summary.json"
    summary = _canonical_file(summary_path, "Claude completed run summary") if summary_path.is_file() else None
    _require(allow_partial or summary is not None, "Claude completed run summary missing")
    if summary is not None:
        summary_keys = {
        "run_manifest_sha256", "records_selected", "tasks_prepared", "calls_attempted",
        "tasks_ok", "tasks_error", "abstention_cells", "full_context_bytes_total",
        "model_context_bytes_total", "context_bytes_removed_total", "execute", "checkpoint",
        }
        extra_counters = {"content_errors", "safety_errors", "continued_content_errors"}
        _require(set(summary) in (summary_keys, summary_keys | extra_counters),
                 "Claude run summary shape differs")
        if extra_counters <= set(summary):
            _require(all(type(summary[key]) is int and summary[key] >= 0 for key in extra_counters) and
                     summary["content_errors"] + summary["safety_errors"] == summary["tasks_error"] and
                     summary["continued_content_errors"] <= summary["content_errors"],
                     "Claude run summary error counters invalid")
        _require(all(type(summary[key]) is int and summary[key] >= 0 for key in (
        "records_selected", "tasks_prepared", "calls_attempted", "tasks_ok", "tasks_error",
        "abstention_cells", "full_context_bytes_total", "model_context_bytes_total",
        "context_bytes_removed_total",
        )), "Claude run summary counters invalid")
        _require(summary.get("run_manifest_sha256") == manifest["manifest_sha256"] and summary.get("records_selected") == len(selected), "Claude run summary lineage differs")
        _require(summary.get("execute") is True and summary.get("tasks_prepared") <= len(selected) and
                 summary.get("calls_attempted") <= len(selected) and
                 summary.get("tasks_ok") <= summary.get("calls_attempted"),
                 "Claude run summary counters inconsistent")
        if not allow_partial:
            _require(summary.get("tasks_prepared") == len(selected) and summary.get("calls_attempted") == len(selected) and summary.get("tasks_ok") == len(selected) and summary.get("tasks_error") == 0, "Claude run summary is not fully successful")
        _require(pathlib.Path(str(summary.get("checkpoint") or "")).resolve() == run_dir / "full_records.jsonl", "Claude run summary checkpoint differs")
    expected_slugs = {claude._slug(record_id) for record_id in selected}
    tasks = run_dir / "tasks"
    actual_slugs = {p.name for p in tasks.iterdir() if p.is_dir()} if tasks.is_dir() else set()
    _require(tasks.is_dir() and (actual_slugs <= expected_slugs if allow_partial else actual_slugs == expected_slugs),
             "Claude task directory cohort differs")
    return manifest, list(selected)


def _claude_pass(record: Mapping[str, Any], row: Mapping[str, Any], manifest: Mapping[str, Any], run_dir: pathlib.Path, catalogs: tuple[Any, Any], release_role: str, *, archived: bool = False) -> dict[str, Any]:
    record_id = str(record["id"])
    _require(set(row) == {"id", "status", "run_manifest_sha256", "task_manifest_sha256", "receipt_sha256", "source_sha256", "context_sha256", "raw_structured_output", "structured_output", "declared_span_normalization", "ledger", "abstention_cells"}, f"{record_id}: Claude checkpoint shape differs")
    _require(row["id"] == record_id and row["status"] == "provisional_unqualified", f"{record_id}: Claude checkpoint identity/status differs")
    _require(row["run_manifest_sha256"] == manifest["manifest_sha256"] and row["source_sha256"] == _sha_object(record), f"{record_id}: Claude run/source lineage differs")
    task_dir = run_dir / "tasks" / claude._slug(record_id) / "v1-24"
    task = _canonical_file(task_dir / "task_manifest.json", f"{record_id} Claude task manifest")
    _require(task.get("schema_version") == claude.TASK_SCHEMA and task.get("manifest_sha256") == _sha_object({k: v for k, v in task.items() if k != "manifest_sha256"}), f"{record_id}: Claude task manifest hash differs")
    _require(task.get("run_manifest_sha256") == manifest["manifest_sha256"] and task.get("record_id") == record_id and task.get("record_source_sha256") == _sha_object(record), f"{record_id}: Claude task source/run differs")
    mode = manifest["context_mode"]
    if archived:
        full_raw = (task_dir / "full_record_context.json").read_bytes()
        saved_context = _json_bytes(full_raw, f"{record_id} archived Claude context")
        _require(full_raw == _canonical(saved_context).encode("utf-8"),
                 f"{record_id}: archived Claude context bytes noncanonical")
        _verify_archived_context(record, saved_context)
    context = full_record_context.build_full_record_context(record, catalog_index=catalogs[0], qualification_catalog=catalogs[1])
    _require(not full_record_context.validate_full_record_context(record, context, catalog_index=catalogs[0]), f"{record_id}: Claude source context invalid")
    full_text = full_record_context.render_full_record_context(context)
    model_context = context if mode == "full" else claude.compact_context(context)
    model_text = claude.canonical_json(model_context)
    system = claude.system_prompt(claude.annotation_rubric(claude.RUBRIC_PATH.read_text(encoding="utf-8")), context_mode=mode)
    prompt = claude.user_prompt(context, context_mode=mode)
    schema = claude.canonical_json(full_record_output.output_schema())
    _require(task.get("context_sha256") == context["context_sha256"] and row["context_sha256"] == context["context_sha256"], f"{record_id}: Claude context hash differs")
    expected_task = {
        "schema_version": claude.TASK_SCHEMA, "run_manifest_sha256": manifest["manifest_sha256"],
        "record_id": record_id, "record_source_sha256": _sha_object(record),
        "context_sha256": context["context_sha256"], "context_mode": mode,
        "full_context_rendered_sha256": claude.sha256_text(full_text),
        "model_context_rendered_sha256": claude.sha256_text(model_text),
        "full_context_bytes": len(full_text.encode("utf-8")),
        "model_context_bytes": len(model_text.encode("utf-8")),
        "context_bytes_removed": len(full_text.encode("utf-8")) - len(model_text.encode("utf-8")),
        "compact_projection_sha256": model_context["compact_projection"]["projection_sha256"] if mode == "compact" else None,
        "system_prompt_sha256": claude.sha256_text(system),
        "static_system_prefix_bytes": len(system.encode("utf-8")),
        "prompt_sha256": claude.sha256_text(prompt),
        "output_schema_sha256": claude.sha256_text(schema),
        "prompt_protocol": claude.COMPACT_PROMPT_PROTOCOL if mode == "compact" else claude.PROMPT_PROTOCOL,
        "prompt_argument_sha256": claude.sha256_text(claude.PROMPT_ARGUMENT),
    }
    expected_task["manifest_sha256"] = _sha_object(expected_task)
    _require(task == expected_task and row["task_manifest_sha256"] == task["manifest_sha256"], f"{record_id}: Claude task differs from runner projection")
    _require(task["system_prompt_sha256"] == manifest["static_system_prefix_sha256"] and
             task["output_schema_sha256"] == manifest["schema_sha256"],
             f"{record_id}: Claude task static system/schema differs from original run")
    artifacts = {"system_prompt.txt": system, "prompt.txt": prompt, "full_record_context.json": full_text, "output_schema.json": schema}
    if mode == "compact":
        artifacts["model_input_context.json"] = model_text
    for name, value in artifacts.items():
        _require((task_dir / name).read_bytes() == value.encode("utf-8"), f"{record_id}: Claude {name} bytes differ")
    _require({p.name for p in task_dir.iterdir()} == set(artifacts) | {"task_manifest.json", "attempts"}, f"{record_id}: Claude task artifact set differs")
    attempt_dir = task_dir / "attempts" / "attempt-001"
    _require(attempt_dir.is_dir() and {p.name for p in (task_dir / "attempts").iterdir()} == {"attempt-001"}, f"{record_id}: Claude attempt history differs")
    _require({p.name for p in attempt_dir.iterdir()} == {"stdout.bin", "stderr.bin", "receipt.json"}, f"{record_id}: Claude attempt artifacts differ")
    stdout = (attempt_dir / "stdout.bin").read_bytes()
    stderr = (attempt_dir / "stderr.bin").read_bytes()
    receipt = _canonical_file(attempt_dir / "receipt.json", f"{record_id} Claude receipt")
    receipt_keys = {
        "schema_version", "run_manifest_sha256", "task_manifest_sha256", "record_id",
        "status", "errors", "returncode", "stdout_sha256", "stderr_sha256",
        "usage", "modelUsage", "static_system_prefix_sha256", "cache_observation",
        "subagent_stats", "total_cost_usd", "requested_max_budget_usd", "ledger_sha256",
        "declared_span_normalization", "opaque_alias_not_snapshot_attestation", "receipt_sha256",
    }
    _require(set(receipt) in (receipt_keys, receipt_keys | {
        "error_class", "continued_after_content_error"
    }), f"{record_id}: Claude receipt shape differs")
    _require(receipt.get("schema_version") == claude.RECEIPT_SCHEMA and receipt.get("receipt_sha256") == _sha_object({k: v for k, v in receipt.items() if k != "receipt_sha256"}), f"{record_id}: Claude receipt hash differs")
    _require(receipt.get("run_manifest_sha256") == manifest["manifest_sha256"] and receipt.get("task_manifest_sha256") == task["manifest_sha256"] and receipt.get("record_id") == record_id, f"{record_id}: Claude receipt lineage differs")
    _require(receipt.get("status") == "ok" and receipt.get("errors") == [] and receipt.get("returncode") == 0, f"{record_id}: Claude receipt is not successful")
    if "error_class" in receipt:
        _require(receipt["error_class"] is None and
                 receipt["continued_after_content_error"] is False,
                 f"{record_id}: Claude successful receipt carries an error class")
    _require(receipt.get("stdout_sha256") == _sha_bytes(stdout) and receipt.get("stderr_sha256") == _sha_bytes(stderr), f"{record_id}: Claude raw byte hashes differ")
    _require(receipt.get("requested_max_budget_usd") == manifest["max_budget_usd_per_call"] and receipt.get("opaque_alias_not_snapshot_attestation") is True, f"{record_id}: Claude budget/alias lineage differs")
    _require(receipt.get("static_system_prefix_sha256") == manifest["static_system_prefix_sha256"], f"{record_id}: Claude static prefix lineage differs")
    envelope, ledger, normalized, normalization = claude.validate_envelope(stdout, record, context)
    _require(envelope["total_cost_usd"] <= manifest["max_budget_usd_per_call"], f"{record_id}: Claude cost exceeds run budget")
    for key in ("usage", "modelUsage", "subagent_stats", "total_cost_usd"):
        _require(receipt.get(key) == envelope.get(key), f"{record_id}: Claude receipt {key} differs from stdout")
    body_model = envelope["modelUsage"][claude.OBSERVED_MODEL]
    aux_model = envelope["modelUsage"].get(claude.AUXILIARY_MODEL)
    expected_cache = {
        "body_cache_creation_input_tokens": body_model.get("cacheCreationInputTokens"),
        "body_cache_read_input_tokens": body_model.get("cacheReadInputTokens"),
        "auxiliary_cache_creation_input_tokens": aux_model.get("cacheCreationInputTokens") if aux_model else None,
        "auxiliary_cache_read_input_tokens": aux_model.get("cacheReadInputTokens") if aux_model else None,
    }
    _require(receipt.get("cache_observation") == expected_cache, f"{record_id}: Claude cache observation differs")
    _require(receipt.get("declared_span_normalization") == normalization, f"{record_id}: Claude receipt normalization differs")
    _require(receipt.get("ledger_sha256") == _sha_object(ledger), f"{record_id}: Claude receipt ledger hash differs")
    _require(row["receipt_sha256"] == receipt["receipt_sha256"] and row["raw_structured_output"] == envelope["structured_output"] and row["structured_output"] == normalized and row["declared_span_normalization"] == normalization and row["ledger"] == ledger, f"{record_id}: Claude checkpoint differs from raw output")
    _require(type(row["abstention_cells"]) is int and row["abstention_cells"] == sum(cell["label"] == "U" for cell in ledger["cells"]), f"{record_id}: Claude abstention count differs")
    return {
        "schema_version": provisional_release.PASS_ROW_ROLE_SCHEMA,
        "record_id": record_id, "source_sha256": _sha_object(record),
        "annotator_role": None,
        "release_pass_role": release_role,
        "role_provenance": _role_provenance(manifest, "claude", release_role),
        "lineage": {
            "provider": "anthropic", "model_family": f"anthropic:{body_model['canonicalModel']}",
            "model_name": body_model["canonicalModel"], "prompt_sha256": task["prompt_sha256"],
            "receipt_sha256": receipt["receipt_sha256"],
            "input_boundary": "competition_source_only", "blind_first_pass": True,
            "peer_answer_visible": False, "production_output_visible": False,
        },
        "ledger": ledger,
    }


def bridge_runs(
    *, records_path: pathlib.Path, run_dirs: Sequence[pathlib.Path],
    runner_kind: str, role: str, output_path: pathlib.Path,
    require_complete: bool = False, require_official_input: bool = True,
    source_archives: Sequence[pathlib.Path] | None = None,
    allow_partial: bool = False,
    provenance_output_path: pathlib.Path | None = None,
    recovery_plan_paths: Sequence[pathlib.Path] | None = None,
) -> dict[str, Any]:
    """Validate runner receipts, then publish fresh, never-gold pass rows."""

    source = pathlib.Path(records_path).resolve()
    output = pathlib.Path(output_path).resolve()
    dirs = [pathlib.Path(path).resolve() for path in run_dirs]
    archives = [pathlib.Path(path).resolve() for path in source_archives] if source_archives is not None else None
    plan_files = [pathlib.Path(path).resolve() for path in (recovery_plan_paths or [])]
    _require(not plan_files or runner_kind == "claude",
             "recovery plans apply only to Claude runs")
    _require(len(plan_files) == len(set(plan_files)), "duplicate recovery plan path")
    recovery_plan_by_run: dict[pathlib.Path, pathlib.Path] = {}
    for plan_path in plan_files:
        plan_header = _json_file(plan_path, "Claude recovery plan")
        recovery_run = pathlib.Path(str(plan_header.get("recovery_output") or "")).resolve()
        _require(recovery_run in dirs and recovery_run not in recovery_plan_by_run,
                 "recovery plan output does not uniquely match a requested run")
        recovery_plan_by_run[recovery_run] = plan_path
    _require(not allow_partial or archives is not None,
             "partial extraction requires original source archives")
    _require(archives is None or len(archives) == len(dirs),
             "one source archive is required per run directory")
    publish_provenance = allow_partial or archives is not None or provenance_output_path is not None
    provenance_output = (
        pathlib.Path(provenance_output_path).resolve() if provenance_output_path is not None
        else output.with_name(output.name + ".provenance.json")
    ) if publish_provenance else None
    _require(runner_kind in {"codex", "claude"}, "runner_kind must be codex or claude")
    _require(role in {"candidate", "verifier"}, "role must be candidate or verifier")
    _require(bool(dirs) and len(dirs) == len(set(dirs)), "run directories missing or duplicated")
    _require(source.is_file() and all(path.is_dir() for path in dirs), "organizer input or run directory missing")
    _require(not output.exists() and not output.is_symlink(), f"fresh output already exists: {output}")
    _require(output not in {source, *(path / "full_records.jsonl" for path in dirs)}, "output aliases a source artifact")
    _require(not any(output.is_relative_to(path) for path in dirs),
             "fresh output must be outside original run directories")
    if archives is not None:
        _require(not any(output.is_relative_to(path) for path in archives),
                 "fresh output must be outside frozen source archives")
    if provenance_output is not None:
        _require(provenance_output != output and
                 not provenance_output.exists() and not provenance_output.is_symlink(),
                 f"fresh provenance output already exists: {provenance_output}")
        _require(not any(provenance_output.is_relative_to(path) for path in dirs),
                 "fresh provenance must be outside original run directories")
        if archives is not None:
            _require(not any(provenance_output.is_relative_to(path) for path in archives),
                     "fresh provenance must be outside frozen source archives")
    if require_official_input:
        _require(source in {path.resolve() for path in claude.ALLOWED_INPUTS}, "only organizer-provided dev/unlabeled input is allowed")
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = pathlib.Path(tempfile.mkdtemp(prefix=f".{output.name}.bridge-", dir=output.parent))
    spool = scratch / "organizer.jsonl"
    staged_output = scratch / "pass_rows.jsonl"
    staged_provenance = scratch / "provenance.json"
    try:
        input_sha256 = _file_sha(source)
        order, source_index = _spool_records(source, spool)
        _require(_file_sha(source) == input_sha256, "organizer input changed while reading")
        catalogs = (
            full_record_context.fact_context.catalog_facts.CatalogIndex.load(),
            full_record_context.qualification_context.qualification_facts.CatalogReference.load(),
        )
        manifests: dict[pathlib.Path, dict[str, Any]] = {}
        checkpoints: dict[pathlib.Path, dict[str, int]] = {}
        checkpoint_shas: dict[pathlib.Path, str | None] = {}
        run_descriptors: list[dict[str, Any]] = []
        owner: dict[str, pathlib.Path] = {}
        selected_owner: dict[str, pathlib.Path] = {}
        for index, run_dir in enumerate(dirs):
            archive = archives[index] if archives is not None else None
            recovery_plan_path = recovery_plan_by_run.get(run_dir)
            if runner_kind == "claude" and run_dir.name.startswith("recovery_claude_"):
                _require(recovery_plan_path is not None,
                         "named Claude recovery run requires its sealed parent-shard recovery plan")
            _require(recovery_plan_path is None or archive is not None,
                     "recovery selection requires an exact source archive for its completed run")
            recovery_proof = (
                _verified_recovery_plan(recovery_plan_path, run_dir, source, input_sha256)
                if recovery_plan_path is not None else None
            )
            raw_role = None
            if runner_kind == "codex":
                raw_role = _json_file(run_dir / "run_manifest.json", "Codex raw role").get("annotator_role")
                _require(raw_role in {"candidate", "verifier"}, "Codex raw runner role invalid")
                _require(raw_role != "verifier" or role == "verifier",
                         "Codex raw verifier cannot become a release candidate")
            manifest, selected = (
                _codex_manifest(run_dir, source, input_sha256, raw_role, order, catalogs,
                                archived=archive is not None, allow_partial=allow_partial)
                if runner_kind == "codex"
                else _claude_manifest(run_dir, source, input_sha256, order, catalogs,
                                      archived=archive is not None, allow_partial=allow_partial,
                                      recovery_ids=recovery_proof["recovery_ids"] if recovery_proof else None)
            )
            archived_source = (
                _verify_archived_source(archive, run_dir, manifest, runner_kind)
                if archive is not None else None
            )
            if recovery_proof is not None:
                _require(archived_source is not None and
                         archived_source["archive_binding"] == "exact_run",
                         "completed recovery source archive is not anchored to its exact run manifest")
            _require(all(record_id in source_index for record_id in selected), "run selected an unknown organizer ID")
            if not allow_partial:
                for record_id in selected:
                    _require(record_id not in selected_owner, f"{record_id}: appears in multiple runs")
                    selected_owner[record_id] = run_dir
            manifests[run_dir] = manifest
            checkpoint_path = run_dir / "full_records.jsonl"
            checkpoint_sha = _file_sha(checkpoint_path) if checkpoint_path.is_file() else None
            offsets, ignored_tail = _checkpoint_offsets(
                checkpoint_path, selected, allow_partial=allow_partial
            )
            checkpoints[run_dir] = offsets
            checkpoint_shas[run_dir] = checkpoint_sha
            if runner_kind == "claude" and (run_dir / "run_summary.json").is_file():
                summary = _canonical_file(run_dir / "run_summary.json", "Claude run summary")
                _require(summary.get("tasks_ok") == len(offsets),
                         f"{run_dir}: Claude summary/checkpoint success count differs")
            for record_id in offsets:
                _require(record_id not in owner, f"{record_id}: successful row appears in multiple runs")
                owner[record_id] = run_dir
            run_descriptors.append({
                "run_dir": str(run_dir),
                "run_manifest_sha256": manifest["manifest_sha256"],
                "run_manifest_file_sha256": _file_sha(run_dir / "run_manifest.json"),
                "source_archive": archived_source,
                "source_mode": "archived_original_bytes" if archive is not None else "current_checkout_rebuilt",
                "raw_runner_role": raw_role,
                "release_pass_role": role,
                "raw_prompt_lineage_sha256": _sha_object(_role_provenance(manifest, runner_kind, role)["raw_prompt_lineage"]),
                "recovery_lineage": recovery_proof,
                "selected_records": len(selected),
                "verified_success_rows": len(offsets),
                "missing_selected_records": len(selected) - len(offsets),
                "missing_selected_ids": [record_id for record_id in selected if record_id not in offsets],
                "ignored_truncated_checkpoint_tail": ignored_tail,
                "checkpoint_sha256": checkpoint_sha,
            })
        _require(bool(owner), "no successful checkpoint rows to extract")
        if require_complete:
            _require(set(owner) == set(source_index), f"pass is incomplete: {len(source_index) - len(owner)} records missing")
        with spool.open("rb") as source_handle, staged_output.open("xb") as target:
            handles = {path: (path / "full_records.jsonl").open("rb") for path in dirs if checkpoints[path]}
            try:
                for record_id in order:
                    run_dir = owner.get(record_id)
                    if run_dir is None:
                        continue
                    record = _record_at(source_handle, source_index[record_id], record_id)
                    row = _checkpoint_row(handles[run_dir], checkpoints[run_dir][record_id], record_id)
                    pass_row = (
                        _codex_pass(record, row, manifests[run_dir], run_dir, role, catalogs,
                                    archived=archives is not None)
                        if runner_kind == "codex"
                        else _claude_pass(record, row, manifests[run_dir], run_dir, catalogs, role,
                                          archived=archives is not None)
                    )
                    provisional_release._validate_pass_row(  # type: ignore[attr-defined]
                        pass_row, role=role, source_hashes={record_id: source_index[record_id][1]},
                        context=f"bridged {record_id}",
                    )
                    provisional_release.validate_ledger(record, pass_row["ledger"])
                    target.write((_canonical(pass_row) + "\n").encode("utf-8"))
            finally:
                for handle in handles.values():
                    handle.close()
            target.flush()
            os.fsync(target.fileno())
        _require(_file_sha(source) == input_sha256, "organizer input changed during bridge")
        for descriptor, run_dir in zip(run_descriptors, dirs):
            checkpoint_path = run_dir / "full_records.jsonl"
            current_sha = _file_sha(checkpoint_path) if checkpoint_path.is_file() else None
            _require(current_sha == checkpoint_shas[run_dir],
                     f"{run_dir}: checkpoint changed while bridging")
            _require(_file_sha(run_dir / "run_manifest.json") == descriptor["run_manifest_file_sha256"],
                     f"{run_dir}: run manifest changed while bridging")
        if archives is not None:
            for descriptor, run_dir, archive in zip(run_descriptors, dirs, archives):
                _require(
                    _verify_archived_source(archive, run_dir, manifests[run_dir], runner_kind)
                    == descriptor["source_archive"],
                    f"{run_dir}: frozen or live source changed while bridging",
                )
        for descriptor, run_dir in zip(run_descriptors, dirs):
            plan_path = recovery_plan_by_run.get(run_dir)
            if plan_path is not None:
                _require(
                    _verified_recovery_plan(plan_path, run_dir, source, input_sha256)
                    == descriptor["recovery_lineage"],
                    f"{run_dir}: recovery plan or parent source changed while bridging",
                )
        if provenance_output is not None:
            incomplete_runs = any(entry["missing_selected_records"] or
                                  entry["ignored_truncated_checkpoint_tail"] for entry in run_descriptors)
            provenance = {
                "schema_version": "dacon.independent.provisional_pass_provenance.v1",
                "status": "partial_provisional_not_gold" if incomplete_runs else "provisional_pass_not_gold",
                "gold_qualification": False,
                "inference_origin": "historical_runner_receipts_zero_new_model_calls",
                "bridge_output_origin": "fresh_verified_export",
            "archival_validation_limits": [
                    "frozen_source_bytes_verified_not_executed",
                    "historical_cli_executable_hash_recorded_not_reverified",
                ] if archives is not None else [],
                "organizer_input_sha256": input_sha256,
                "output_sha256": _file_sha(staged_output),
                "pass_records": len(owner),
                "runner_kind": runner_kind,
                "pass_role": role,
                "release_pass_role": role,
                "runs": run_descriptors,
                "model_calls": 0,
            }
            with staged_provenance.open("xb") as handle:
                handle.write((_canonical(provenance) + "\n").encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
        _require(not output.exists() and not output.is_symlink(), f"output appeared during bridge: {output}")
        # Same-directory hard-link creation is atomic and fails if another
        # process claimed the fresh target; os.replace would silently clobber.
        if provenance_output is not None:
            _require(not provenance_output.exists() and not provenance_output.is_symlink(),
                     f"provenance output appeared during bridge: {provenance_output}")
            provenance_output.parent.mkdir(parents=True, exist_ok=True)
            os.link(staged_provenance, provenance_output)
        try:
            os.link(staged_output, output)
        except OSError:
            if (
                provenance_output is not None
                and provenance_output.exists()
                and os.path.samefile(staged_provenance, provenance_output)
            ):
                provenance_output.unlink()
            raise
        staged_output.unlink()
        return {
            "status": "partial_provisional_not_gold" if any(
                entry["missing_selected_records"] or entry["ignored_truncated_checkpoint_tail"]
                for entry in run_descriptors
            ) else "provisional_pass_not_gold", "runner_kind": runner_kind,
            "annotator_role": role, "organizer_records": len(order), "pass_records": len(owner),
            "release_pass_role": role,
            "raw_runner_roles": sorted({entry["raw_runner_role"] for entry in run_descriptors}, key=str),
            "run_directories": len(dirs), "output": str(output), "output_sha256": _file_sha(output),
            "partial_run_count": sum(bool(entry["missing_selected_records"] or
                                      entry["ignored_truncated_checkpoint_tail"]) for entry in run_descriptors),
            "provenance_output": str(provenance_output) if provenance_output is not None else None,
            "model_calls": 0,
        }
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, PassRowBridgeError):
            raise
        raise PassRowBridgeError(str(exc)) from exc
    finally:
        if scratch.exists():
            # Only this bridge's uniquely named staging directory is removed.
            for child in scratch.iterdir():
                if child.is_file():
                    child.unlink()
            scratch.rmdir()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=pathlib.Path, required=True)
    parser.add_argument("--run-dir", type=pathlib.Path, action="append", required=True)
    parser.add_argument("--runner", choices=("codex", "claude"), required=True)
    parser.add_argument("--release-role", "--role", dest="role",
                        choices=("candidate", "verifier"), required=True,
                        help="post-hoc release vote order; raw runner role remains in row provenance")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--source-archive", type=pathlib.Path, action="append",
                        help="frozen source archive matching each --run-dir, in the same order")
    parser.add_argument("--recovery-plan", type=pathlib.Path, action="append",
                        help="sealed parent-shard recovery plan; matches a --run-dir by its recovery_output")
    parser.add_argument("--allow-partial", action="store_true",
                        help="extract only verified successful checkpoint rows from interrupted runs")
    parser.add_argument("--provenance-output", type=pathlib.Path,
                        help="fresh provenance JSON path; mandatory sidecar is auto-named for partial/archive mode")
    args = parser.parse_args(argv)
    try:
        result = bridge_runs(
            records_path=args.records, run_dirs=args.run_dir, runner_kind=args.runner,
            role=args.role, output_path=args.output, require_complete=args.require_complete,
            source_archives=args.source_archive, allow_partial=args.allow_partial,
            provenance_output_path=args.provenance_output,
            recovery_plan_paths=args.recovery_plan,
        )
    except PassRowBridgeError as exc:
        print(f"pass row bridge refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
