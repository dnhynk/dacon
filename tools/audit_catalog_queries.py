"""Freeze source-only catalog queries and measure additions versus cap losses."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.notice_search import NoticeSearch
from submission.pps.purchase_reading import seed_reading, catalog_source_queries
from submission.runtime import source_manifest


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def key(query):
    return re.sub(r'\s+', ' ', query['query']).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('input', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT / 'models/gemma-tokenizer', local_files_only=True,
                                              trust_remote_code=False)
    code = source_manifest()
    input_hash = sha(args.input)
    with gzip.open(args.input, 'rt', encoding='utf-8') as stream:
        records = list(map(json.loads, stream))
    observations = []
    for record in records:
        tool = NoticeSearch(record, tokenizer)
        seed = seed_reading(tool, token_budget=4096)
        for policy in ('units', 'context_blocks', 'table_candidates'):
            queries = catalog_source_queries(record, seed, query_policy=policy, max_queries=32)
            available = catalog_source_queries(record, seed, query_policy=policy, max_queries=8192)
            assert len(available) < 8192
            for query in available:
                ref = query['evidence']
                assert record['docs'][ref['doc_index']]['text'][ref['start']:ref['end']] == ref['text']
                assert any(s['doc_index'] == ref['doc_index'] and s['start'] <= ref['start']
                           and s['end'] >= ref['end'] for s in seed['spans'])
            observations.append(dict(id=record['id'], policy=policy, queries=queries,
                available=available, source_tokens=seed['source_tokens'],
                seed_ranges=[[s['doc_index'], s['start'], s['end']] for s in seed['spans']]))
    changes = []
    if args.baseline:
        old_report = json.loads((args.baseline / 'report.json').read_text(encoding='utf-8'))
        assert old_report['input_sha256'] == input_hash
        with gzip.open(args.baseline / 'queries.jsonl.gz', 'rt', encoding='utf-8') as stream:
            old = {(r['id'], r['policy']): r for r in map(json.loads, stream)}
        assert len(old) == len(observations)
        for now in observations:
            prior = old[(now['id'], now['policy'])]
            assert prior['seed_ranges'] == now['seed_ranges']
            assert prior['source_tokens'] == now['source_tokens']
            before = {key(q) for q in prior['queries']}
            after = {key(q) for q in now['queries']}
            before_all = {key(q) for q in prior['available']}
            after_all = {key(q) for q in now['available']}
            changes.append(dict(id=now['id'], policy=now['policy'],
                added=sorted(after-before), removed=sorted(before-after),
                newly_available=sorted(after_all-before_all),
                no_longer_available=sorted(before_all-after_all),
                lost_to_cap=sorted((before-after)&after_all),
                source_tokens=now['source_tokens']))
    with gzip.open(args.output / 'queries.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for row in observations:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    if changes:
        with (args.output / 'changes.json').open('x', encoding='utf-8') as stream:
            json.dump(changes, stream, ensure_ascii=False, indent=2)
    summary = []
    for policy in ('units', 'context_blocks', 'table_candidates'):
        selected = [r for r in observations if r['policy'] == policy]
        changed = [r for r in changes if r['policy'] == policy]
        summary.append(dict(policy=policy, records=len(selected),
            final_queries=sum(len(r['queries']) for r in selected),
            available_queries=sum(len(r['available']) for r in selected),
            notices_at_cap=sum(len(r['available']) > 32 for r in selected),
            changed_notices=sum(bool(r['added'] or r['removed']) for r in changed),
            change_totals=dict(Counter({k:sum(len(r[k]) for r in changed) for k in (
                'added', 'removed', 'newly_available', 'no_longer_available', 'lost_to_cap')}))))
    assert source_manifest() == code and sha(args.input) == input_hash
    report = dict(kind='source_only_catalog_query_audit', input_sha256=input_hash, source_sha256=code,
        labels_read=False, new_model_calls=0, gpu_used=False, source_budget=4096,
        source_seed_fraction='one_third', query_cap=32, summary=summary,
        limitation='Query availability and cap losses are not source coverage or final decision accuracy.')
    with (args.output / 'report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
