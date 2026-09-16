"""Compare identical prepared controls across executions without answer selection."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.score_catalog_scope import rows, sha


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def read_run(root, arm):
    folder = root / 'contrast_output'
    packets = {p['request_key']: p for p in rows(folder/'current_packets.jsonl.gz')
               if p['arm'] in (arm, arm+'_repeat')}
    # Exact matching packet keys alone determine this comparison.
    observations = {r['request_key']: r for r in rows(folder/'contrast_results.jsonl.gz')}
    native = {(r['request_key'], r['attempt']): r['native']
              for p in sorted(folder.glob('call_*_native.jsonl.gz')) for r in rows(p)}
    sampling = {}
    for path in sorted(folder.glob('call_*_sampling.json')):
        trace = load(path)
        assert len(trace['request_keys']) == len(trace['before_llm_generate'])
        for key, params in zip(trace['request_keys'], trace['before_llm_generate']):
            sampling.setdefault(key, []).append(params)
    return packets, observations, native, sampling


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--arm', default='A_current')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    left, right = read_run(args.before, args.arm), read_run(args.after, args.arm)
    assert left[0] and left[0].keys() == right[0].keys()
    fixed_fields = ('record_id', 'items', 'messages', 'token_ids', 'spans',
                    'coverage', 'generation', 'generation_schema_sha256', 'schema_sha256')
    cells = []
    for key in left[0]:
        a, b = left[0][key], right[0][key]
        assert all(a[field] == b[field] for field in fixed_fields), key
        old, new = left[1][key], right[1][key]
        assert old['attempt'] == new['attempt'] == 0, 'Do not substitute retries in drift checks'
        assert old['parse_error'] is new['parse_error'] is None
        assert left[3][key] == right[3][key], 'Actual pre-generation sampling changed'
        for run, packet in ((left, a), (right, b)):
            assert run[2][key, 0]['prompt_token_ids'] == packet['token_ids']
        raw_changed = [f'v{i}' for i in a['items']
                       if old['raw_values'][f'v{i}'] != new['raw_values'][f'v{i}']]
        final_changed = [f'v{i}' for i in a['items']
                         if old['row'][f'v{i}'] != new['row'][f'v{i}']]
        cells.append(dict(request_key=key, record_id=a['record_id'], arm=a['arm'],
            input_tokens_equal=True, actual_sampling_equal=True,
            raw_text_equal=left[2][key, 0]['raw_text'] == right[2][key, 0]['raw_text'],
            raw_values_changed=raw_changed, consumed_values_changed=final_changed,
            before_raw=old['raw_values'], after_raw=new['raw_values'],
            before_consumed={f'v{i}': old['row'][f'v{i}'] for i in a['items']},
            after_consumed={f'v{i}': new['row'][f'v{i}'] for i in a['items']}))
    report = dict(kind='identical_control_cross_execution_observations', arm=args.arm,
        compared_requests=len(cells), raw_text_changes=sum(not c['raw_text_equal'] for c in cells),
        raw_value_changed_requests=sum(bool(c['raw_values_changed']) for c in cells),
        consumed_value_changed_requests=sum(bool(c['consumed_values_changed']) for c in cells),
        raw_changed_fields=sum(len(c['raw_values_changed']) for c in cells),
        consumed_changed_fields=sum(len(c['consumed_values_changed']) for c in cells),
        before_allocation=load(args.before/'allocation.json'),
        after_allocation=load(args.after/'allocation.json'),
        before_engine=load(args.before/'engine_bootstrap/engine.json'),
        after_engine=load(args.after/'engine_bootstrap/engine.json'),
        labels_read=False, better_response_selection=False,
        limitations='Different engines, hardware allocations and companion requests may affect output. '
                    'This is observed cross-execution drift, not an isolated causal test of randomness or batching.',
        inputs=[dict(path=str(root/'contrast_output/current_packets.jsonl.gz'),
                     sha256=sha(root/'contrast_output/current_packets.jsonl.gz'))
                for root in (args.before, args.after)])
    for name, obj in (('report.json', report), ('cells.json', cells)):
        with (args.output/name).open('x', encoding='utf-8') as stream:
            json.dump(obj, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: report[k] for k in ('compared_requests', 'raw_text_changes',
        'raw_value_changed_requests', 'consumed_value_changed_requests',
        'raw_changed_fields', 'consumed_changed_fields')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
