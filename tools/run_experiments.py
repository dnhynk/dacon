"""RETIRED legacy experiment runner; use the canonical root script.py."""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    from tools.legacy_entry import retired_main
    retired_main(Path(__file__).name)

from pps.data import make_row, records, validate_csv, write_csv
from pps.pipeline import VLLMRunner, log, run
from pps.prompts import Config
from tools.evaluate import evaluate


def save(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def baseline_run(input_path, out, data_dir, baseline_path, runner, config):
    """Official prompt/JSON parser on the same loaded engine as the candidates.

    Malformed responses stop the experiment instead of silently becoming zeros.
    Engine batching differs from the standalone official baseline.
    """
    from vllm import SamplingParams
    from vllm.sampling_params import StructuredOutputsParams

    spec = importlib.util.spec_from_file_location("official_baseline", baseline_path)
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = baseline
    spec.loader.exec_module(baseline)
    recs = list(records(input_path))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sampling = SamplingParams(temperature=0., seed=baseline.SEED, max_tokens=baseline.MAX_TOKENS,
                              structured_outputs=StructuredOutputsParams(
                                  json=baseline.decode_schema(str(data_dir)), disable_any_whitespace=True))

    class TokenCounter:
        tok = runner.tokenizer
        count_tokens = baseline.VLLMRunner.count_tokens

    system = baseline.build_system_prompt(baseline.item_table(str(data_dir)))
    rows, ntokens, output_tokens = [], [], 0
    start = time.monotonic()
    with (out.parent / "trace.jsonl").open("w", encoding="utf-8") as trace:
        for offset in range(0, len(recs), config.batch_size):
            if time.monotonic() >= runner.deadline:
                raise TimeoutError("Experiment time budget reached")
            batch = recs[offset:offset+config.batch_size]
            prompts = [baseline.fit_to_budget(r, system, TokenCounter(), 4000) for r in batch]
            outputs = runner.llm.chat([p[0] for p in prompts], sampling_params=sampling, use_tqdm=False)
            if len(outputs) != len(batch):
                raise RuntimeError("Official baseline returned missing responses")
            for rec, prompt, response in zip(batch, prompts, outputs):
                if not response.outputs or response.outputs[0].finish_reason == "length":
                    raise RuntimeError(f"Incomplete baseline response for {rec['id']}")
                text = response.outputs[0].text
                parsed, missing = baseline.parse_judgment(text)
                if missing:
                    raise RuntimeError(f"Invalid baseline response for {rec['id']}: {missing}")
                values = [parsed[f"v{k}"]["위반여부"] for k in range(1, 25)]
                evidence = [parsed[f"v{k}"]["근거문구"] for k in range(1, 25)]
                rows.append(make_row(rec, values, evidence))
                ntokens.append(prompt[1])
                output_tokens += len(response.outputs[0].token_ids)
                trace.write(json.dumps({"id": rec["id"], "messages": prompt[0], "response": text}, ensure_ascii=False) + "\n")
            trace.flush()
            log(f"official prompt: {len(rows)}/{len(recs)}; {time.monotonic()-start:.1f}s")
    write_csv(out, rows)
    validate_csv(out, recs)
    elapsed = time.monotonic() - start
    report = {"name": "official_prompt", "mock": False, "records": len(recs),
              "normal_model_calls": len(recs), "csv_validation": "PASS", "pipeline_seconds": elapsed,
              "load_seconds": runner.load_seconds, "input_tokens_total": sum(ntokens),
              "output_tokens_total": output_tokens, "runtime_version": runner.version,
              "estimated_1853_seconds_in_this_environment": runner.load_seconds + elapsed / len(recs) * 1853}
    save(out.parent / "run_report.json", report)
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model-dir", type=Path, required=True)
    p.add_argument("--data-root", type=Path, default=Path("data_open"))
    p.add_argument("--split-dir", type=Path, default=Path("artifacts/audit"))
    p.add_argument("--out", type=Path, default=Path("runs/colab"))
    p.add_argument("--max-minutes", type=float, default=90)
    p.add_argument("--smoke-only", action="store_true")
    p.add_argument("--config", type=Path, default=ROOT / "model/config.json")
    p.add_argument("--skip-official", action="store_true")
    p.add_argument("--modes", nargs="+", choices=("head", "retrieval"), default=("head", "retrieval"))
    p.add_argument("--development-only", action="store_true", help="Do not open or evaluate holdout data")
    a = p.parse_args()
    if a.out.exists() and any(a.out.iterdir()):
        p.error("Output directory already contains results; choose a new --out to preserve past runs")
    a.out.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + a.max_minutes * 60
    cfg = Config.load(a.config)
    environment = {"python": platform.python_version(), "system": platform.platform(),
                   "packages": {name: importlib.metadata.version(name) for name in
                                ("vllm", "torch", "transformers", "tokenizers", "xgrammar", "numpy")},
                   "model_provenance": json.loads((a.model_dir / "competition_provenance.json").read_text()),
                   "split_sha256": hashlib.sha256((a.split_dir / "split.json").read_bytes()).hexdigest()}
    environment["source_sha256"] = {str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
                                    for path in [ROOT / "script.py", *sorted((ROOT / "pps").glob("*.py"))]}
    environment["nvidia_smi"] = subprocess.check_output(["nvidia-smi"], text=True)
    save(a.out / "environment.json", environment)
    snapshot = {name: (ROOT / name).read_text(encoding="utf-8") for name in environment["source_sha256"]}
    snapshot["tools/run_experiments.py"] = Path(__file__).read_text(encoding="utf-8")
    save(a.out / "source_snapshot.json", snapshot)
    runner = VLLMRunner(a.model_dir, cfg)
    runner.deadline = deadline
    data_dir = a.data_root / "data"
    development = a.split_dir / "development.jsonl.gz"

    # These eight examples are part of development, never the holdout or hidden test.
    smoke = run(development, a.out / "smoke/submission.csv", data_dir,
                cfg, runner, limit=8, trace=True)
    if cfg.enable_thinking and (smoke["retries"] or smoke["thinking_outputs"] != smoke["thinking_outputs_expected"]):
        raise RuntimeError("Native thought smoke did not meet the no-retry/observed-thought gate")
    if a.smoke_only:
        print(f"SMOKE PASS: {smoke['normal_model_calls']} normal fixed-model calls on eight records and valid CSV. Accuracy not yet evaluated.")
        return

    suffix = "v1" if cfg.response_format == "compact" else cfg.name
    configs = {f"{mode}_{suffix}": dataclasses.replace(cfg, name=f"{mode}_{suffix}", mode=mode)
               for mode in dict.fromkeys(a.modes)}
    summaries = {}
    variant_names = ([] if a.skip_official else ["official_prompt"]) + list(configs)
    for name in variant_names:
        dest = a.out / "development" / name / "submission.csv"
        if name == "official_prompt":
            report = baseline_run(development, dest, data_dir,
                                  a.data_root / "baseline/script.py", runner, cfg)
        else:
            runner.config = configs[name]
            report = run(development, dest, data_dir, configs[name], runner, trace=True)
        score = evaluate(a.split_dir / "development_labels.csv", dest)
        save(dest.parent / "metrics.json", score)
        summaries[name] = {"macro_f1": score["macro_f1"], "seconds": report["pipeline_seconds"],
                           "estimated_1853_seconds_in_this_environment": report["estimated_1853_seconds_in_this_environment"]}
        save(a.out / "development_comparison.json", summaries)
        print(json.dumps({"variant": name, **summaries[name]}, ensure_ascii=False), flush=True)

    winner = max(summaries, key=lambda n: (summaries[n]["macro_f1"], -summaries[n]["seconds"]))
    # The choice is frozen before opening holdout labels. No per-item cherry picking.
    selection = {"selected": winner, "selected_on": "development_160_macro_f1_then_speed",
                 "development": summaries, "config": dataclasses.asdict(configs[winner]) if winner in configs else None,
                 "l40s_runtime_verified": False}
    save(a.out / "selection.json", selection)
    if a.development_only:
        save(a.out / "development_completed.json", {"selected": winner, "holdout_evaluated": False})
        print(json.dumps({"selected": winner, "development_only": True}, ensure_ascii=False), flush=True)
        return
    holdout_dest = a.out / "holdout" / winner / "submission.csv"
    if winner == "official_prompt":
        baseline_run(a.split_dir / "holdout.jsonl.gz", holdout_dest, data_dir,
                     a.data_root / "baseline/script.py", runner, cfg)
    else:
        runner.config = configs[winner]
        run(a.split_dir / "holdout.jsonl.gz", holdout_dest, data_dir, configs[winner], runner, trace=True)
    score = evaluate(a.split_dir / "holdout_labels.csv", holdout_dest)
    save(holdout_dest.parent / "metrics.json", score)
    save(a.out / "completed.json", {"selected": winner, "holdout_macro_f1": score["macro_f1"],
                                   "submission_uploaded": False, "l40s_runtime_verified": False})
    print(json.dumps({"selected": winner, "holdout_macro_f1": score["macro_f1"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
