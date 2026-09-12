"""Generic current-input B4 entry. No research data or stored answers are loaded."""
import argparse,json,os,sys
from pathlib import Path
from b4_entry import HERE
from b4_runtime import execute
from pps.pipeline import VLLMRunner
from pps.prompts import Config


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path)
    parser.add_argument('--data-dir',type=Path,default=os.environ.get('PPS_DATA_DIR'))
    parser.add_argument('--model-dir',type=Path,default=os.environ.get('PPS_MODEL_DIR'))
    parser.add_argument('--output-dir',type=Path,default=os.environ.get('PPS_OUTPUT_DIR'))
    parser.add_argument('--limit',type=int)
    args=parser.parse_args()
    if not args.data_dir or not args.model_dir or not args.output_dir:
        parser.error('Provide data, local fixed-model and output directories')
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error('Output directory must be empty; prior attempts are preserved')
    cfg=Config.load(HERE/'frozen/model/config.json')
    runner=None
    try:
        runner=VLLMRunner(args.model_dir,cfg)
        report=execute(args.input or args.data_dir/'test.jsonl.gz',args.data_dir,args.output_dir,runner,limit=args.limit)
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        if runner is not None:runner.llm.llm_engine.engine_core.shutdown()


if __name__=='__main__':main()
