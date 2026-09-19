"""Run the streaming executor against frozen model responses; no GPU, no new calls.

The real preparation pipeline (worker processes, canonical packets, source-only
rows) and the real consumers run; the engine is replaced by a replay runner that
answers each request key with its preserved response. Compare the result with
the frozen cohort run using tools/verify_stream_run.py.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_responses(source):
    source = Path(source)
    files = [source] if source.is_file() else sorted(source.glob('call_*_responses.jsonl.gz'))
    if not files and (source / 'resolved_responses.jsonl.gz').is_file():
        files = [source / 'resolved_responses.jsonl.gz']
    responses = {}
    for path in files:
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line)
                if row.get('attempt', 0) != 0 or row['request_key'] in responses:
                    continue
                responses[row['request_key']] = row['response']
    if not responses:
        raise ValueError('No frozen responses found under ' + str(source))
    return responses


class ReplayRunner:
    def __init__(self, config, responses):
        self.config, self.responses = config, responses
        self.queue = collections.OrderedDict()
        self.missing, self.load_seconds, self.tokenizer = [], 0., None

    def submit(self, request_id, packet):
        self.queue[request_id] = packet

    def step(self):
        finished = []
        for request_id, packet in list(self.queue.items()):
            del self.queue[request_id]
            response = self.responses.get(packet['request_key'])
            if response is None:
                self.missing.append(request_id)
            text = response['text'] if response else ''
            finished.append(SimpleNamespace(
                request_id=request_id, finished=True, prompt_token_ids=None,
                num_cached_tokens=response.get('cached_input_tokens') if response else None,
                outputs=[SimpleNamespace(text=text, token_ids=[0] * (response['output_tokens'] if response else 0),
                                         finish_reason=response['finish_reason'] if response else 'missing',
                                         stop_reason=None)]))
        return finished

    def unfinished(self):
        return bool(self.queue)

    def abort(self, request_ids):
        for request_id in request_ids:
            self.queue.pop(request_id, None)

    def response_from_native(self, output, packet):
        response = self.responses.get(packet['request_key'])
        if response is None:
            return {'text': '', 'finish_reason': 'missing_frozen_response', 'output_tokens': 0,
                    'cached_input_tokens': None, 'raw_output_sha256': None, 'generation_stall': None}
        return dict(response)

    def close(self):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--tokenizer-dir', type=Path, required=True)
    parser.add_argument('--responses', type=Path, required=True, help='run directory or responses file')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--embed-device', choices=('auto', 'cuda', 'cpu', 'none'), default='none')
    parser.add_argument('--tier-ceiling', type=int, default=0)
    parser.add_argument('--tier-floor', type=int, default=0)
    parser.add_argument('--projection-records', type=int)
    parser.add_argument('--runtime-seconds', type=int, default=100000)
    args = parser.parse_args()
    from submission.prep import PreparationPool
    from submission.stream import StreamOptions, execute_stream
    responses = load_responses(args.responses)
    runners = []

    def factory(config, journal):
        runner = ReplayRunner(config, responses)
        runners.append(runner)
        journal.save('replay_runner.json', {'frozen_responses': len(responses), 'source': str(args.responses)})
        return runner

    options = StreamOptions(total_runtime_seconds=args.runtime_seconds, tier_ceiling=args.tier_ceiling,
                            tier_floor=args.tier_floor, projection_records=args.projection_records)
    pool = PreparationPool(args.data_dir, args.tokenizer_dir, {}, workers=args.workers, encoder=args.embed_device,
                           encoder_dir=os.environ.get('PPS_EMBED_DIR'))
    began = time.monotonic()
    report = execute_stream(args.input, args.data_dir, args.output, tokenizer_dir=args.tokenizer_dir,
                            runner_factory=factory, options=options, limit=args.limit, pool=pool)
    report['replay'] = {'missing_frozen_responses': runners[0].missing if runners else None,
                        'wall_seconds': time.monotonic() - began, 'new_model_calls': 0}
    (args.output / 'replay_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('records', 'requests_submitted', 'responses', 'unresolved_requests',
                                              'tier_counts', 'seconds')}, ensure_ascii=False))
    print('missing frozen responses:', len(runners[0].missing) if runners else None)


if __name__ == '__main__':
    main()
