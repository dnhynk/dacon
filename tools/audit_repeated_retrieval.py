"""Compare all preregistered repeated inputs, preserving execution differences."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path


def read(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def index(rows):
    result = {r['request_key']: r for r in rows}
    if len(result) != len(rows):
        raise ValueError('Duplicate request identity')
    return result


def load(folder):
    inputs = index(read(folder/'current_packets.jsonl.gz'))
    primary = index([r for path in sorted(folder.glob('primary_*.jsonl.gz')) for r in read(path)])
    native = index([r for path in sorted(folder.glob('call_*_native.jsonl.gz')) for r in read(path) if r['attempt'] == 0])
    sampling = {}
    for path in sorted(folder.glob('call_*_sampling.json')):
        call = json.loads(path.read_text(encoding='utf-8'))
        assert len(call['request_keys']) == len(call['before_llm_generate'])
        for key, sampler in zip(call['request_keys'], call['before_llm_generate']):
            sampling.setdefault(key, sampler)
    return inputs, primary, native, sampling


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prior', type=Path, required=True)
    p.add_argument('--current', type=Path, required=True)
    p.add_argument('--preregistered', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    manifest = json.loads(args.preregistered.read_text(encoding='utf-8'))
    before, after = load(args.prior), load(args.current)
    cases = []
    for key in manifest['common_requests']:
        a, b = before[0][key], after[0][key]
        assert all(a[f] == b[f] for f in manifest['equal_fields']), 'Preregistered repeated input changed'
        assert before[3][key] == after[3][key], 'Actual sampling parameters changed'
        x, y = before[1][key], after[1][key]
        nx, ny = before[2][key]['native'], after[2][key]['native']
        assert nx['prompt_token_ids'] == ny['prompt_token_ids'] == a['token_ids']
        for n, r in ((nx, x), (ny, y)):
            assert hashlib.sha256(n['raw_text'].encode()).hexdigest() == r['response']['raw_output_sha256']
        cases.append({'request_key': key, 'case': a['case'], 'items': a['items'],
            'actual_input_and_sampling_equal': True, 'prior_parse_error': x['parse_error'],
            'current_parse_error': y['parse_error'],
            'raw_text_equal': nx['raw_text'] == ny['raw_text'],
            'output_tokens_equal': nx['output_token_ids'] == ny['output_token_ids'],
            'prior_raw_values': x['raw_values'], 'current_raw_values': y['raw_values'],
            'prior_consumed_values': {k: v for k, v in x['row'].items() if k.startswith('v')},
            'current_consumed_values': {k: v for k, v in y['row'].items() if k.startswith('v')},
            'raw_output_sha256': [x['response']['raw_output_sha256'], y['response']['raw_output_sha256']]})
    report = {'kind': 'preregistered_repeated_input_across_execution_instances',
        'preregistered_sha256': hashlib.sha256(args.preregistered.read_bytes()).hexdigest(),
        'requests': len(cases), 'raw_text_changed': sum(not c['raw_text_equal'] for c in cases),
        'raw_judgment_requests_changed': sum(c['prior_raw_values'] != c['current_raw_values'] for c in cases),
        'consumed_judgment_requests_changed': sum(c['prior_consumed_values'] != c['current_consumed_values'] for c in cases),
        'cases': cases, 'labels_read': False, 'extra_model_calls': 0,
        'batch_composition_identical': False,
        'limitation': 'All declared common requests, no answer selection. Same actual input and sampler across two executions; batch composition and runtime instance differ. This does not isolate intrinsic model randomness or guarantee repeatability.'}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
