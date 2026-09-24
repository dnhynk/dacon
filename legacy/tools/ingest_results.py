"""Read Colab results as data and independently recompute development metrics."""
from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.evaluate import evaluate
from submission.pps.data import records, validate_csv


def extract_results(archive, destination):
    archive, destination = Path(archive), Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(f"Preserve prior results; use a new destination: {destination}")
    with zipfile.ZipFile(archive) as z:
        entries = z.infolist()
        if len(entries) > 5000 or sum(e.file_size for e in entries) > 1024**3:
            raise ValueError("Unexpectedly large results archive")
        seen = set()
        for member in entries:
            name = member.filename.replace("\\", "/")
            relative = PurePosixPath(name)
            target = (destination / name).resolve()
            if (relative.is_absolute() or ".." in relative.parts or ":" in name
                    or not target.is_relative_to(destination) or target in seen):
                raise ValueError("Unsafe or duplicate results archive path")
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("Symlinks are not supported in result archives")
            seen.add(target)
        # Reserve a new directory only after every archive path has been checked.
        destination.mkdir(parents=True, exist_ok=False)
        # Do not import or execute any Python/notebook code from the result bundle.
        for member in entries:
            target = destination / member.filename.replace("\\", "/")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(member) as source, target.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
    return destination


def inspect(destination, split_dir=ROOT / "artifacts/audit", *, include_holdout=False):
    destination, split_dir = Path(destination), Path(split_dir)
    report = {"gpu_checks": {}, "experiments": [], "verified_predictions": [], "errors": [],
              "skipped_predictions": []}
    for name in ("colab_preflight.json", "gpu_runtime_check.json"):
        for path in destination.rglob(name):
            report["gpu_checks"][path.relative_to(destination).as_posix()] = json.loads(path.read_text(encoding="utf-8"))
    for path in destination.rglob("completed.json"):
        result = json.loads(path.read_text(encoding="utf-8"))
        report["experiments"].append({"directory": path.parent.relative_to(destination).as_posix(), **result})
    for prediction in destination.rglob("submission.csv"):
        stage = next((s for s in ("development", "holdout") if s in prediction.relative_to(destination).parts), None)
        if stage is None:
            continue
        if stage == "holdout" and not include_holdout:
            report["skipped_predictions"].append({
                "path": prediction.relative_to(destination).as_posix(),
                "reason": "holdout_not_explicitly_enabled",
            })
            continue
        try:
            recs = list(records(split_dir / f"{stage}.jsonl.gz"))
            validate_csv(prediction, recs)
            metric = evaluate(split_dir / f"{stage}_labels.csv", prediction)
            report["verified_predictions"].append({"path": prediction.relative_to(destination).as_posix(),
                                                    "records": metric["records"], "macro_f1": metric["macro_f1"],
                                                    "per_item": metric["per_item"], "errors": metric["errors"]})
        except (ValueError, OSError) as exc:
            report["errors"].append({"path": prediction.relative_to(destination).as_posix(), "error": str(exc)})
    report["actual_prediction_files_verified"] = len(report["verified_predictions"])
    report["prediction_validation_passed"] = bool(report["verified_predictions"]) and not report["errors"]
    # A CSV alone does not prove use of the fixed model. Keep provenance separate.
    report["model_provenance"] = []
    for path in destination.rglob("environment.json"):
        obj = json.loads(path.read_text(encoding="utf-8"))
        report["model_provenance"].append({"path": path.relative_to(destination).as_posix(),
                                            "model": obj.get("model_provenance"), "packages": obj.get("packages")})
    report["fixed_model_environment_reported"] = any(
        r["model"] and r["model"].get("model") == "google/gemma-4-26B-A4B-it"
        and r["model"].get("revision") == "4d7ae4984b7db7de8f8457170b3f1a419ee76d52"
        and not r["model"].get("tokenizer_only", True)
        for r in report["model_provenance"])
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("archive", type=Path)
    p.add_argument("--output-root", type=Path, default=ROOT / "runs/imported")
    p.add_argument("--include-holdout", action="store_true",
                   help="Explicitly enable holdout-label scoring; leave off unless that data scope is authorized")
    a = p.parse_args()
    with a.archive.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    destination = a.output_root / digest[:16]
    if destination.exists():
        raise ValueError(f"Results already imported at {destination}; inspect the saved report")
    extract_results(a.archive, destination)
    report = inspect(destination, include_holdout=a.include_holdout)
    report["archive_sha256"] = digest
    (destination / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"directory": str(destination), "gpu_checks": report["gpu_checks"],
                      "experiments": report["experiments"], "errors": report["errors"],
                      "verified_predictions": [{k: v for k, v in r.items() if k not in {"errors", "per_item"}}
                                               for r in report["verified_predictions"]]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
