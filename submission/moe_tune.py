"""Startup self-tuning of the fused MoE Triton kernel for the GPU at hand.

vLLM 0.26 falls back to generic block sizes when no tuned JSON exists for the
expert shape (the A100 logs warn "Using default MoE config" for
E=128,N=704,dtype=int8_w8a16, and prefill ran at about 16% of peak). This module
benchmarks a small candidate set with vLLM's own kernel on the real expert
shapes and writes a JSON into VLLM_TUNED_CONFIG_FOLDER only when the best
candidate beats vLLM's default by a margin and reproduces its output. It runs
in a subprocess before the engine exists so its GPU memory is released. Any
failure or timeout leaves no file, and the engine then behaves exactly as
before. Judgment inputs never change; only kernel tiling does.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

PROBE_TOKENS = (8192, 4096)            # the prefill chunk sizes the engine actually runs
DEFAULT_KEYS = (1, 2, 4, 8, 16, 24, 32, 48, 64, 96, 128, 256, 512, 1024, 2048)
FIELDS = ('BLOCK_SIZE_M', 'BLOCK_SIZE_N', 'BLOCK_SIZE_K', 'GROUP_SIZE_M', 'num_warps', 'num_stages')
DTYPE_NAME = 'int8_w8a16'
MINIMUM_GAIN = .05                     # relative latency gain the best candidate must show
RELATIVE_TOLERANCE = .02               # output agreement between the best and the default tiling


def moe_shape(model_dir):
    """Expert shape from the fixed checkpoint's config.json; no weights are read."""
    config = json.loads((Path(model_dir) / 'config.json').read_text(encoding='utf-8'))
    text = config.get('text_config', config)
    experts = text.get('num_experts') or text.get('num_local_experts')
    topk = text.get('top_k_experts') or text.get('num_experts_per_tok')
    hidden = text.get('hidden_size')
    intermediate = text.get('moe_intermediate_size') or text.get('intermediate_size')
    if not all(isinstance(v, int) and v > 0 for v in (experts, topk, hidden, intermediate)):
        raise ValueError('MoE shape is not fully specified by the model config')
    return {'experts': experts, 'topk': topk, 'hidden': hidden, 'intermediate': intermediate}


def coarse_candidates():
    return [{'BLOCK_SIZE_M': m, 'BLOCK_SIZE_N': n, 'BLOCK_SIZE_K': k, 'GROUP_SIZE_M': 1,
             'num_warps': w, 'num_stages': 3}
            for m in (64, 128) for n in (128, 256) for k in (64, 128) for w in (4, 8)]


def refinements(best, seen):
    """Nearby tilings of the coarse winner; duplicates and seen configs excluded."""
    result = []
    for stages in (2, 3, 4):
        for group in (1, 16, 32):
            for n in sorted({max(32, best['BLOCK_SIZE_N'] // 2), best['BLOCK_SIZE_N'], min(256, best['BLOCK_SIZE_N'] * 2)}):
                candidate = {**best, 'num_stages': stages, 'GROUP_SIZE_M': group, 'BLOCK_SIZE_N': n}
                key = tuple(candidate[f] for f in FIELDS)
                if key not in seen:
                    seen.add(key)
                    result.append(candidate)
    return result


def only_fields(config):
    return {field: int(config[field]) for field in FIELDS}


def decide(default_ms, best_ms, agrees):
    """Write a tuned file only for a clear, numerically consistent gain."""
    if default_ms is None or best_ms is None or not agrees:
        return False
    return best_ms <= default_ms * (1. - MINIMUM_GAIN)


def assemble(defaults, tuned):
    """JSON body: default tilings for small token counts, tuned ones for prefill chunks."""
    body = {}
    for m, config in sorted(defaults.items()):
        body[str(m)] = only_fields(config)
    for m, config in sorted(tuned.items()):
        body[str(m)] = only_fields(config)
    return body


def tune(model_dir, output_dir, seconds, report_path):
    """Benchmark inside the subprocess; returns the report dict."""
    began = time.monotonic()
    report = {'status': 'not_written', 'model_dir': str(model_dir), 'budget_seconds': seconds,
              'measurements': [], 'errors': []}
    import torch
    from vllm.model_executor.layers.fused_moe import fused_topk, override_config
    from vllm.model_executor.layers.fused_moe.config import int8_w8a16_moe_quant_config
    from vllm.model_executor.layers.fused_moe.fused_moe import (fused_experts, get_config_file_name,
                                                                get_default_config)
    shape = moe_shape(model_dir)
    experts, topk, hidden, intermediate = shape['experts'], shape['topk'], shape['hidden'], shape['intermediate']
    shard = 2 * intermediate
    report.update(shape=shape, device=torch.cuda.get_device_name())
    file_name = get_config_file_name(experts, intermediate, DTYPE_NAME, None)
    report['file_name'] = file_name
    torch.manual_seed(0)
    device = 'cuda'
    dtype = torch.bfloat16
    largest = max(PROBE_TOKENS)
    x = torch.randn(largest, hidden, dtype=dtype, device=device)
    w1 = torch.randint(-127, 127, (experts, shard, hidden), dtype=torch.int8, device=device)
    w2 = torch.randint(-127, 127, (experts, hidden, shard // 2), dtype=torch.int8, device=device)
    # Weight-only int8 with one scale per output channel, as the engine's own
    # int8 oracle builds it. FusedMoEQuantConfig.make(quant_dtype=int8) is the
    # W8A8 path: it quantizes activations and fails on bfloat16 inputs.
    w1_scale = torch.rand((experts, shard), dtype=torch.float32, device=device) * .01
    w2_scale = torch.rand((experts, hidden), dtype=torch.float32, device=device) * .01
    gating = torch.randn(largest, experts, dtype=torch.float32, device=device)
    quant_config = int8_w8a16_moe_quant_config(w1_scale=w1_scale, w2_scale=w2_scale)

    def run(config, tokens):
        with override_config(config):
            weights, ids, _ = fused_topk(x[:tokens], gating[:tokens], topk, renormalize=True)
            return fused_experts(x[:tokens], w1, w2, weights, ids, quant_config=quant_config)

    def bench(config, tokens, iters=5):
        try:
            run(config, tokens)
            torch.cuda.synchronize()
            graph = torch.cuda.CUDAGraph()
            with torch.cuda.graph(graph):
                for _ in range(10):
                    run(config, tokens)
            torch.cuda.synchronize()
            for _ in range(3):
                graph.replay()
            torch.cuda.synchronize()
            start, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
            latencies = []
            for _ in range(iters):
                start.record()
                graph.replay()
                end.record()
                end.synchronize()
                latencies.append(start.elapsed_time(end))
            graph.reset()
            return sum(latencies) / (iters * 10)
        except Exception as exc:  # invalid tiling for this GPU, resource limits, compile errors
            report['errors'].append({'config': only_fields(config), 'tokens': tokens,
                                     'error': type(exc).__name__ + ': ' + str(exc)[:300]})
            torch.cuda.synchronize()
            return None

    def out_of_time():
        return time.monotonic() - began > seconds

    defaults = {m: get_default_config(m, experts, intermediate, hidden, topk, DTYPE_NAME, None)
                for m in (*DEFAULT_KEYS, *PROBE_TOKENS)}
    tuned = {}
    measured = {}
    for tokens in PROBE_TOKENS:
        default_ms = bench(defaults[tokens], tokens)
        measured[tokens] = {'default': default_ms}
        report['measurements'].append({'tokens': tokens, 'config': only_fields(defaults[tokens]),
                                       'ms': default_ms, 'role': 'default'})
        if default_ms is None:
            continue
        seen = {tuple(only_fields(defaults[tokens])[f] for f in FIELDS)}
        best_config, best_ms = defaults[tokens], default_ms
        candidates = [c for c in coarse_candidates() if tuple(c[f] for f in FIELDS) not in seen]
        if tokens != PROBE_TOKENS[0] and PROBE_TOKENS[0] in tuned:
            # Smaller chunks reuse the compiled winners of the largest chunk first.
            candidates = [tuned[PROBE_TOKENS[0]], *candidates]
        for candidate in candidates:
            if out_of_time():
                report['errors'].append({'error': 'budget exhausted during the coarse search', 'tokens': tokens})
                break
            seen.add(tuple(candidate[f] for f in FIELDS))
            ms = bench(candidate, tokens)
            report['measurements'].append({'tokens': tokens, 'config': only_fields(candidate), 'ms': ms, 'role': 'coarse'})
            if ms is not None and ms < best_ms:
                best_config, best_ms = candidate, ms
        if tokens == PROBE_TOKENS[0]:
            for candidate in refinements(best_config, seen):
                if out_of_time():
                    report['errors'].append({'error': 'budget exhausted during refinement', 'tokens': tokens})
                    break
                ms = bench(candidate, tokens)
                report['measurements'].append({'tokens': tokens, 'config': only_fields(candidate), 'ms': ms, 'role': 'refine'})
                if ms is not None and ms < best_ms:
                    best_config, best_ms = candidate, ms
        measured[tokens].update(best=best_ms, gain=1. - best_ms / default_ms)
        if best_ms < default_ms:
            tuned[tokens] = best_config
    # The largest chunk decides; it must agree numerically with the default tiling.
    primary = PROBE_TOKENS[0]
    agrees = None
    if primary in tuned:
        with torch.no_grad():
            reference = run(defaults[primary], primary).float()
            candidate_out = run(tuned[primary], primary).float()
        difference = float((reference - candidate_out).abs().max())
        magnitude = float(reference.abs().max())
        agrees = bool(torch.isfinite(candidate_out).all()) and difference <= RELATIVE_TOLERANCE * max(1., magnitude)
        report['agreement'] = {'max_abs_difference': difference, 'reference_max_abs': magnitude, 'agrees': agrees}
    report['per_tokens'] = {str(k): v for k, v in measured.items()}
    write = decide(measured.get(primary, {}).get('default'), measured.get(primary, {}).get('best'), agrees)
    report['seconds'] = time.monotonic() - began
    if write:
        body = assemble({m: defaults[m] for m in DEFAULT_KEYS}, {m: c for m, c in tuned.items()})
        for m in PROBE_TOKENS:
            body.setdefault(str(m), only_fields(tuned.get(m, defaults[m])))
        import triton
        target = Path(output_dir) / file_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({'triton_version': triton.__version__, **body}, indent=2) + '\n', encoding='utf-8')
        report.update(status='written', file=str(target), body=body)
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def tune_moe_kernels(model_dir, journal, *, seconds=180, enabled=True, python=None):
    """Parent side: run the tuner in a subprocess and point vLLM at the result. Never raises."""
    outcome = {'enabled': enabled, 'status': 'skipped'}
    if not enabled:
        journal.save('moe_tuning.json', outcome)
        return outcome
    folder = journal.root / 'moe_tuned_configs'
    report_path = journal.root / 'moe_tuning_report.json'
    log_path = journal.root / 'moe_tuning.log'
    root = Path(__file__).resolve().parents[1]
    command = [python or sys.executable, '-B', '-m', 'submission.moe_tune', '--model-dir', str(model_dir),
               '--output', str(folder), '--seconds', str(int(seconds)), '--report', str(report_path)]
    env = {**os.environ, 'PYTHONPATH': str(root) + os.pathsep + os.environ.get('PYTHONPATH', '')}
    began = time.monotonic()
    try:
        with log_path.open('w', encoding='utf-8') as log:
            completed = subprocess.run(command, cwd=str(root), env=env, stdout=log, stderr=subprocess.STDOUT,
                                       timeout=seconds + 120)
        outcome.update(returncode=completed.returncode)
    except subprocess.TimeoutExpired:
        outcome.update(status='timeout')
    except Exception as exc:
        outcome.update(status='launch_failed', error=type(exc).__name__ + ': ' + str(exc))
    outcome['seconds'] = time.monotonic() - began
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding='utf-8'))
            outcome['report'] = {k: report.get(k) for k in ('status', 'file_name', 'device', 'per_tokens',
                                                            'agreement', 'seconds', 'errors')}
            if report.get('status') == 'written' and Path(report['file']).is_file():
                os.environ['VLLM_TUNED_CONFIG_FOLDER'] = str(folder)
                outcome.update(status='applied', folder=str(folder))
            elif outcome.get('status') == 'skipped':
                outcome['status'] = 'measured_not_applied'
        except Exception as exc:
            outcome.update(status='report_unreadable', error=type(exc).__name__ + ': ' + str(exc))
    elif outcome.get('status') == 'skipped':
        outcome['status'] = 'no_report'
    journal.save('moe_tuning.json', outcome)
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=180)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = tune(args.model_dir, args.output, args.seconds, args.report)
    except Exception:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({'status': 'failed', 'traceback': traceback.format_exc()},
                                          ensure_ascii=False, indent=2), encoding='utf-8')
        return 1
    return 0 if report.get('status') in {'written', 'not_written'} else 1


if __name__ == '__main__':
    sys.exit(main())
