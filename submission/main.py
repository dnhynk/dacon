"""Public CLI for the canonical current-input B4 pipeline."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .engine import CanonicalRunner, configure_environment
from .runtime import execute


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--data-dir', type=Path, default=os.environ.get('PPS_DATA_DIR', './data'))
    parser.add_argument('--model-dir', type=Path,
                        default=os.environ.get('PPS_MODEL_DIR', '/opt/models/gemma-4-26B-A4B-it'))
    parser.add_argument('--output-dir', type=Path, default=os.environ.get('PPS_OUTPUT_DIR', './output'))
    parser.add_argument('--limit', type=int)
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
    report = execute(input_path, args.data_dir, args.output_dir, tokenizer=tokenizer,
                     runner_factory=lambda config, journal: CanonicalRunner(args.model_dir, config, journal),
                     limit=args.limit)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


if __name__ == '__main__':
    main()
