"""Public CLI for the canonical current-input B4 pipeline."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .engine import CanonicalRunner, configure_environment
from .runtime import execute


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
    args = parser.parse_args(argv)
    if not args.data_dir or not args.model_dir or not args.output_dir:
        parser.error('Provide data, local fixed-model and output directories')
    if args.limit is not None and args.limit <= 0:
        parser.error('--limit must be positive')
    input_path = args.input or args.data_dir / 'test.jsonl.gz'
    if not input_path.is_file() or not args.data_dir.is_dir() or not args.model_dir.is_dir():
        parser.error('Input file, data directory and local model directory must exist')
    if args.output_dir.exists() and (not args.output_dir.is_dir() or any(args.output_dir.iterdir())):
        parser.error('Output directory must be empty; prior attempts are preserved')
    configure_environment()
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.model_dir, local_files_only=True,
                                              trust_remote_code=False)
    source_options = {'source_policy':args.source_policy} if args.source_policy is not None else {}
    if args.specification_review is not None:
        source_options['specification_review'] = args.specification_review
    if args.legal_policy is not None:
        source_options['legal_policy'] = args.legal_policy
    for name in ('catalog_review','catalog_source_policy','catalog_task_groups','software_review',
                 'a10_thinking_budget','a_cohort_size','a10_question_policy'):
        if getattr(args,name) is not None:
            source_options[name]=getattr(args,name)
    report = execute(input_path, args.data_dir, args.output_dir, tokenizer=tokenizer,
                     runner_factory=lambda config, journal: CanonicalRunner(args.model_dir, config, journal),
                     limit=args.limit, started_at=started, **source_options)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


if __name__ == '__main__':
    main()
