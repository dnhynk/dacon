"""Separate source-union reading recall from one-span citation availability.

Reuses immutable historical retrieval output. No search, embedding, generation,
or classification-label access takes place here.
"""
from collections import defaultdict
from functools import lru_cache
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.source_coverage import reading_ranges
from tools.compare_notice_retrieval import hit


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def rows(path):
    with gzip.open(path, 'rt', encoding='utf8') as stream:
        yield from map(json.loads, stream)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('audit-root', 'inputs', 'review-manifest', 'tokenizer', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True,
                                             trust_remote_code=False)
    recs = {r['id']: r for r in rows(args.inputs)}
    review = read(args.review_manifest)
    cases = {c['task_id']: c for c in review['cases']}
    for case in cases.values():
        for witness in case['required_bundle']:
            doc = recs[case['record_id']]['docs'][witness['doc_index']]
            if hashlib.sha256(doc['text'].encode()).hexdigest() != witness['doc_sha256']:
                raise ValueError('Review source changed')
    @lru_cache(maxsize=50000)
    def cost(rid, di, lo, hi):
        return len(tokenizer.encode(recs[rid]['docs'][di]['text'][lo:hi],
                                    add_special_tokens=False))
    artifacts, observations = [], []
    paths = sorted(args.audit_root.glob('retrieval_*/retrieval_results.jsonl.gz'))
    for path in paths:
        prior = read(path.parent / 'preregistered.json')
        old_count = other_count = 0
        for row in rows(path):
            old_count += 1
            measure, result = row['measurement'], row['retrieval']
            # The optimizer's separate source-only cohort has record/item
            # observations rather than human-review task identifiers.
            task = measure.get('task_id')
            if task not in cases:
                other_count += 1
                continue
            case = cases[task]
            rec = recs[case['record_id']]
            if result['record_id'] != rec['id']:
                raise ValueError('Retrieval crosses notice identity')
            for span in result['spans']:
                di, lo, hi = span['doc_index'], span['start'], span['end']
                if not 0 <= lo < hi <= len(rec['docs'][di]['text']):
                    raise ValueError('Invalid original coordinates')
                if rec['docs'][di]['text'][lo:hi] != span['text']:
                    raise ValueError('Retrieval source text changed')
            merged = {'spans': reading_ranges(rec, result['spans'])}
            union_hits = {e['evidence_id']: hit(e, merged) for e in case['required_bundle']}
            single_hits = {e['evidence_id']: hit(e, result) for e in case['required_bundle']}
            visible_tokens = sum(cost(rec['id'], s['doc_index'], s['start'], s['end'])
                                 for s in result['spans'])
            union_tokens = sum(cost(rec['id'], s['doc_index'], s['start'], s['end'])
                               for s in merged['spans'])
            setting = (str(measure['budget']) if len(prior.get('budgets', [])) > 1 else
                       prior.get('source_budget_policy', str(prior.get('primary_budget',
                                                      prior.get('source_budget', 4096)))))
            observations.append(dict(round=path.parent.name, setting=setting,
                task=task, arm=measure['arm'], resolution=case['resolution'],
                related=any(union_hits[e['evidence_id']] for e in case['required_bundle']
                            if e['role'] == 'support'),
                bundle=bool(union_hits) and all(union_hits.values()),
                single_bundle=bool(single_hits) and all(single_hits.values()),
                anchors=sum(union_hits.values()), single_anchors=sum(single_hits.values()),
                anchor_total=len(union_hits), hits=union_hits, single_hits=single_hits,
                original_recorded_tokens=result['source_tokens'], visible_tokens=visible_tokens,
                union_tokens=union_tokens, cap=result['source_token_budget'],
                visible_within_cap=visible_tokens <= result['source_token_budget'],
                union_within_cap=union_tokens <= result['source_token_budget'],
                original_coverage=result['coverage']))
        artifacts.append(dict(path=str(path), sha256=sha(path), original_rows=old_count,
            non_human_review_rows=other_count, preregistered_sha256=sha(path.parent/'preregistered.json')))
    groups = defaultdict(list)
    for row in observations:
        groups[row['round'], row['setting'], row['arm']].append(row)
    aggregates = []
    for (folder, setting, arm), group in sorted(groups.items()):
        if len({r['task'] for r in group}) != len(group):
            raise ValueError('Duplicate human case in a comparison arm')
        aggregates.append(dict(round=folder, setting=setting, arm=arm, cases=len(group),
            complete_review_cohort={r['task'] for r in group} == set(cases),
            related=sum(r['related'] for r in group), bundle=sum(r['bundle'] for r in group),
            single_bundle=sum(r['single_bundle'] for r in group),
            resolved_bundle=sum(r['bundle'] and r['resolution']=='resolved' for r in group),
            anchors=sum(r['anchors'] for r in group), single_anchors=sum(r['single_anchors'] for r in group),
            visible_tokens=sum(r['visible_tokens'] for r in group),
            caps={r['task']:r['cap'] for r in group},
            visible_cap_excesses=sum(not r['visible_within_cap'] for r in group),
            union_cap_excesses=sum(not r['union_within_cap'] for r in group)))
    report = dict(artifacts=artifacts, aggregates=aggregates,
        input_sha256=sha(args.inputs), review_manifest_sha256=sha(args.review_manifest),
        labels_read=False, new_searches=0, new_embedding_calls=0, new_gemma_calls=0,
        limitations='Exposed review anchors, not official gold. Source-union reading coverage '
            'does not certify relations, citation validity or legal absence. Compare only matching '
            'case populations and per-case caps. Historical outputs and reports remain unchanged.')
    for name, value in [('report.json', report), ('observations.json', observations)]:
        with (args.output / name).open('x', encoding='utf8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
    print(json.dumps(dict(artifacts=len(artifacts), observations=len(observations),
        groups=len(aggregates), changed_reading_anchors=sum(r['anchors']-r['single_anchors']
                                                         for r in observations))))


if __name__ == '__main__':
    main()
