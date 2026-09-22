"""Freeze the bytes of the current independent Claude source bundle.

This is a mechanical evidence copy, not a model call or an active runtime fork.
The destination must be a new directory under the independent-gold run root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil

from tools.independent_gold import claude_full_record_annotator as claude


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN_ROOT = ROOT / "runs" / "self_label_20000_20260918"


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def archive(output_dir: pathlib.Path, run_manifest: pathlib.Path | None = None) -> dict[str, object]:
    destination = output_dir.resolve()
    if RUN_ROOT.resolve() not in destination.parents or destination.exists():
        raise ValueError("destination must be a new child of the independent run root")
    extra_sources: list[dict[str, str]] = []
    if run_manifest is None:
        bundle = claude.source_bundle()
        files = bundle["files"]
        rubric_sha256_expected = file_sha256(claude.RUBRIC_PATH)
        run_manifest_sha256 = None
    else:
        run_path = run_manifest.resolve()
        if RUN_ROOT.resolve() not in run_path.parents or not run_path.is_file():
            raise ValueError("run manifest must be an existing independent run artifact")
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run_manifest_sha256 = run.get("manifest_sha256")
        if not isinstance(run_manifest_sha256, str) or (
            claude.sha256_object({key: value for key, value in run.items() if key != "manifest_sha256"})
            != run_manifest_sha256
        ):
            raise ValueError("run manifest self-hash mismatch")
        if "source_bundle" in run:
            bundle = run["source_bundle"]
            expected_bundle_sha256 = claude.sha256_object(bundle["files"])
            files = bundle["files"]
            rubric_sha256_expected = run["rubric_source_sha256"]
            if "pilot_source_sha256" in run:
                extra_sources.append({
                    "path": "tools/independent_gold/claude_batch_pilot.py",
                    "sha256": run["pilot_source_sha256"],
                })
            has_batch_pilot = "batch_pilot_source_sha256" in run
            has_preparer = "preparer_source_sha256" in run
            if has_batch_pilot or has_preparer:
                if (run.get("schema_version") != "dacon.independent.claude_unlabeled_batch_prepare.v1"
                        or run.get("phase") != "unlabeled_batch_input_dry_only"
                        or not (has_batch_pilot and has_preparer)
                        or "pilot_source_sha256" in run):
                    raise ValueError("unlabeled dry source references are incomplete or ambiguous")
                extra_sources.extend([
                    {
                        "path": "tools/independent_gold/claude_batch_pilot.py",
                        "sha256": run["batch_pilot_source_sha256"],
                    },
                    {
                        "path": "tools/independent_gold/claude_unlabeled_batch_prepare.py",
                        "sha256": run["preparer_source_sha256"],
                    },
                ])
        else:
            bundle = run["imported_source_bundle"]
            expected_bundle_sha256 = claude.sha256_object(
                {key: value for key, value in bundle.items() if key != "bundle_sha256"}
            )
            indexed = {entry["path"]: entry for entry in bundle["files"]}
            for source_ref in (
                run["runner_source"],
                run["prompt_lineage"]["builder_source"],
                run["prompt_lineage"]["registry_source"],
            ):
                path = pathlib.Path(source_ref["path"])
                relative = path.resolve().relative_to(ROOT.resolve()).as_posix() if path.is_absolute() else path.as_posix()
                expected = {"path": relative, "sha256": source_ref["sha256"]}
                if relative in indexed and indexed[relative] != expected:
                    raise ValueError("run source hash conflicts with imported bundle")
                indexed[relative] = expected
            files = sorted(indexed.values(), key=lambda entry: entry["path"])
            rubric_sha256_expected = run["semantic_config"]["rubric_sha256"]
        if bundle.get("bundle_sha256") != expected_bundle_sha256:
            raise ValueError("run source bundle self-hash mismatch")
    if not isinstance(files, list) or not files:
        raise ValueError("empty source bundle")
    destination.mkdir(parents=True, exist_ok=False)
    for entry in [*files, *extra_sources]:
        relative = pathlib.PurePosixPath(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe source bundle path")
        source = ROOT.joinpath(*relative.parts).resolve()
        if ROOT.resolve() not in source.parents:
            raise ValueError("source bundle leaves repository")
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if file_sha256(source) != entry["sha256"]:
            raise ValueError(f"source changed before archival: {relative}")
        with target.open("xb") as handle, source.open("rb") as original:
            shutil.copyfileobj(original, handle)
        if file_sha256(target) != entry["sha256"] or file_sha256(source) != entry["sha256"]:
            raise ValueError(f"source changed during archival: {relative}")
    rubric_source = claude.RUBRIC_PATH.resolve()
    rubric_sha256 = file_sha256(rubric_source)
    if rubric_sha256 != rubric_sha256_expected:
        raise ValueError("rubric differs from the run manifest")
    rubric_target = destination / "tools" / "independent_gold" / "rubric_v1.md"
    rubric_target.parent.mkdir(parents=True, exist_ok=True)
    with rubric_target.open("xb") as handle, rubric_source.open("rb") as original:
        shutil.copyfileobj(original, handle)
    if file_sha256(rubric_target) != rubric_sha256 or file_sha256(rubric_source) != rubric_sha256:
        raise ValueError("rubric changed during archival")
    manifest: dict[str, object] = {
        "schema_version": "dacon.independent.source_archive.v1",
        "bundle_sha256": bundle["bundle_sha256"],
        "files": files,
        "rubric_sha256": rubric_sha256,
        "run_manifest_sha256": run_manifest_sha256,
        "archive_kind": "mechanical_source_bytes_only_no_model_call",
    }
    if extra_sources:
        manifest["extra_sources"] = extra_sources
    with (destination / "manifest.json").open("xb") as handle:
        handle.write((json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--run-manifest", type=pathlib.Path)
    args = parser.parse_args()
    manifest = archive(args.output_dir, args.run_manifest)
    print(json.dumps({"output_dir": str(args.output_dir.resolve()), "bundle_sha256": manifest["bundle_sha256"], "file_count": len(manifest["files"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
