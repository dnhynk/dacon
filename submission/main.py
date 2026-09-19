"""Public CLI for the canonical current-input B4 pipeline."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .engine import CanonicalRunner, configure_environment
from .runtime import execute
from .stream import TIERS, StreamOptions, execute_stream

HERE = Path(__file__).resolve().parent


def main(argv=None):
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--data-dir', type=Path, default=os.environ.get('PPS_DATA_DIR', './data'))
    parser.add_argument('--model-dir', type=Path,
                        default=os.environ.get('PPS_MODEL_DIR', '/opt/models/gemma-4-26B-A4B-it'))
    parser.add_argument('--output-dir', type=Path, default=os.environ.get('PPS_OUTPUT_DIR', './output'))
    parser.add_argument('--limit', type=int)
    parser.add_argument('--source-policy', choices=('current','evidence_cover','factual_lexical','purchase_context','purchase_context_hybrid'),
                        help='Integrated source producer; the same current-notice token cap is used')
    parser.add_argument('--specification-review', choices=('current','candidates','gated_candidates','gated_source_candidates'),
                        help='Optional candidate-by-candidate v9 review of the same selected source')
    parser.add_argument('--legal-policy', choices=('current','direct_production'),
                        help='Optional A10 legal dependency reading at the current law token cap')
    parser.add_argument('--catalog-review',choices=('current','control','explicit'),
                        help='Review unresolved service scope against the complete provided service catalog')
    parser.add_argument('--catalog-source-policy',choices=('shared','task_lexical','task_hybrid'),
                        help='Q-only task source search within the unchanged shared-Q source token cap')
    parser.add_argument('--catalog-task-groups',action=argparse.BooleanOptionalAction,default=None,
                        help='Add original task-field source addresses to Q without duplicating source text')
    parser.add_argument('--software-review',choices=('current','relations'),
                        help='Optional software relations; unresolved reviews preserve the independent judgment')
    parser.add_argument('--a10-thinking-budget',type=int,choices=(0,384,768),
                        help='Explicit experimental native thinking allocation for items10..18')
    parser.add_argument('--a-cohort-size',type=int,choices=range(1,33),
                        help='A-only input cohort size; L and optional reviews retain32')
    parser.add_argument('--a10-question-policy',choices=('current','source_questions'),
                        help='Optional A10 source-fixed judgments and unresolved condition questions')
    parser.add_argument('--executor', choices=('stream', 'cohort'), default=os.environ.get('PPS_EXECUTOR', 'stream'),
                        help='stream: record-major deadline-aware execution (default); cohort: the fixed 32-record batches')
    parser.add_argument('--tier-ceiling', type=int, default=2, choices=range(len(TIERS)),
                        help='Richest judgment tier the stream executor may run (default 2: A1+A19+Q10; 0 = canonical)')
    parser.add_argument('--tier-floor', type=int, default=len(TIERS) - 1, choices=range(len(TIERS)),
                        help='Cheapest judgment tier the stream executor may fall to')
    parser.add_argument('--projection-records', type=int,
                        help='Project the deadline as if this many records had to be processed (development timing runs)')
    parser.add_argument('--runtime-seconds', type=int,
                        help='Override the configured total runtime budget for the stream executor')
    parser.add_argument('--prep-workers', type=int,
                        default=int(os.environ['PPS_PREP_WORKERS']) if os.environ.get('PPS_PREP_WORKERS') else None,
                        help='Preparation worker processes; 0 prepares inline in the engine process')
    parser.add_argument('--embed-device', choices=('auto', 'cuda', 'cpu', 'none'),
                        default=os.environ.get('PPS_EMBED_DEVICE', 'auto'),
                        help='Where the shared BGE-M3 encoder service runs; none keeps per-worker CPU encoders')
    parser.add_argument('--prior-prefill-us', type=float,
                        help='Stream cost model prior: microseconds per new prefill token before measurements arrive')
    parser.add_argument('--prior-decode-ms', type=float,
                        help='Stream cost model prior: milliseconds per decode token before measurements arrive')
    parser.add_argument('--inflight-requests', type=int,
                        help='Stream executor engine queue depth (default 8; shallow keeps each record\'s prefix resident)')
    parser.add_argument('--max-num-batched-tokens', type=int,
                        help='Engine prefill chunk size override (config default 8192); a scheduler-only experiment knob')
    parser.add_argument('--moe-tuning', choices=('auto', 'off'), default=os.environ.get('PPS_MOE_TUNING', 'off'),
                        help='Opt-in: benchmark fused-MoE kernel tilings before the engine loads and apply a clear win. '
                             'Off by default: on A100 the kernel gained 12%% but end-to-end prefill under 1%%, less than the 160s it costs')
    parser.add_argument('--moe-tuning-seconds', type=int, default=180,
                        help='Benchmark budget for the startup MoE kernel tuning')
    args = parser.parse_args(argv)
    if not args.data_dir or not args.model_dir or not args.output_dir:
        parser.error('Provide data, local fixed-model and output directories')
    if args.limit is not None and args.limit <= 0:
        parser.error('--limit must be positive')
    if args.tier_ceiling > args.tier_floor:
        parser.error('--tier-ceiling must not exceed --tier-floor')
    input_path = args.input or args.data_dir / 'test.jsonl.gz'
    if not input_path.is_file() or not args.data_dir.is_dir() or not args.model_dir.is_dir():
        parser.error('Input file, data directory and local model directory must exist')
    if args.output_dir.exists() and (not args.output_dir.is_dir() or any(args.output_dir.iterdir())):
        parser.error('Output directory must be empty; prior attempts are preserved')
    configure_environment()
    source_options = {'source_policy':args.source_policy} if args.source_policy is not None else {}
    if args.specification_review is not None:
        source_options['specification_review'] = args.specification_review
    if args.legal_policy is not None:
        source_options['legal_policy'] = args.legal_policy
    for name in ('catalog_review','catalog_source_policy','catalog_task_groups','software_review',
                 'a10_thinking_budget','a_cohort_size','a10_question_policy'):
        if getattr(args,name) is not None:
            source_options[name]=getattr(args,name)
    runner_factory = lambda config, journal: CanonicalRunner(args.model_dir, config, journal)
    if args.executor == 'cohort':
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(args.model_dir, local_files_only=True,
                                                  trust_remote_code=False)
        report = execute(input_path, args.data_dir, args.output_dir, tokenizer=tokenizer,
                         runner_factory=runner_factory, limit=args.limit, started_at=started, **source_options)
    else:
        from .pps.prompts import Config
        budget = Config.load(HERE / 'model/config.json').total_runtime_seconds
        stream = {'total_runtime_seconds': args.runtime_seconds or budget, 'tier_ceiling': args.tier_ceiling,
                  'tier_floor': args.tier_floor, 'projection_records': args.projection_records}
        if args.prior_prefill_us is not None:
            stream['prior_prefill_seconds_per_token'] = args.prior_prefill_us * 1e-6
        if args.prior_decode_ms is not None:
            stream['prior_decode_seconds_per_token'] = args.prior_decode_ms * 1e-3
        if args.inflight_requests is not None:
            stream['inflight_requests'] = args.inflight_requests
        options = StreamOptions(**stream)
        engine_overrides = {}
        if args.max_num_batched_tokens is not None:
            if args.max_num_batched_tokens < 1024:
                parser.error('--max-num-batched-tokens must be at least 1024')
            engine_overrides['max_num_batched_tokens'] = args.max_num_batched_tokens
        from .prep import PreparationPool
        from .moe_tune import tune_moe_kernels
        pool = PreparationPool(args.data_dir, args.model_dir, source_options, workers=args.prep_workers,
                               encoder=args.embed_device, encoder_dir=os.environ.get('PPS_EMBED_DIR'))
        pre_engine = lambda journal: tune_moe_kernels(args.model_dir, journal, seconds=args.moe_tuning_seconds,
                                                      enabled=args.moe_tuning == 'auto')
        report = execute_stream(input_path, args.data_dir, args.output_dir, tokenizer_dir=args.model_dir,
                                runner_factory=runner_factory, options=options, source_options=source_options,
                                limit=args.limit, started_at=started, pool=pool, engine_overrides=engine_overrides,
                                pre_engine=pre_engine)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


if __name__ == '__main__':
    main()
