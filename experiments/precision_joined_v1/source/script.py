"""Competition-compatible entry point for the isolated four-call candidate."""
import argparse
import json
import os
from pathlib import Path

from pps.pipeline import MockRunner, VLLMRunner
from pps.prompts import Config
from pps.v20_route import run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parent / 'model/config.json')
    parser.add_argument('--data-dir', default=os.environ.get('PPS_DATA_DIR'))
    parser.add_argument('--output-dir', default=os.environ.get('PPS_OUTPUT_DIR'))
    parser.add_argument('--model-dir', default=os.environ.get('PPS_MODEL_DIR'))
    parser.add_argument('--input')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--mock', action='store_true')
    parser.add_argument('--tokenizer-dir')
    parser.add_argument('--trace', action='store_true')
    args = parser.parse_args()
    if not args.data_dir or not args.output_dir:
        parser.error('Set PPS_DATA_DIR/PPS_OUTPUT_DIR or provide the matching arguments')
    config = Config.load(args.config)
    if args.mock:
        tokenizer = None
        if args.tokenizer_dir:
            from transformers import AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(args.tokenizer_dir, local_files_only=True)
        runner = MockRunner(tokenizer)
    else:
        if not args.model_dir:
            parser.error('Set PPS_MODEL_DIR to the local fixed competition model snapshot')
        runner = VLLMRunner(args.model_dir, config)
    output = Path(args.output_dir) / ('mock_submission.csv' if args.mock else 'submission.csv')
    report = run(args.input or Path(args.data_dir) / 'test.jsonl.gz', output, args.data_dir,
                 config, runner, limit=args.limit, trace=args.trace)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
