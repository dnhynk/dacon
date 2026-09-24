"""Attribute fixed purchase-retrieval outcomes without selecting better answers."""
import argparse
import csv
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.retrieval import _source_units
from tools.score_catalog_scope import rows, sha
from tools.audit_purchase_feedback import save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('run', 'score-v3', 'score-v5', 'labels', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    folder = args.run/'contrast_output'
    freeze = json.loads((folder/'input_freeze.json').read_text(encoding='utf-8'))
    packets = rows(folder/'current_packets.jsonl.gz')
    observations = rows(folder/'contrast_results.jsonl.gz')
    records = {r['id']: r for r in rows(folder/'current_inputs.jsonl.gz')}
    audit = json.loads((args.score_v3/'generation_audit.json').read_text(encoding='utf-8'))
    assert audit == json.loads((args.score_v5/'generation_audit.json').read_text(encoding='utf-8'))
    assert audit['cpu_consumer_reproduced'] and len(records) == 12
    with args.labels.open(encoding='utf-8-sig', newline='') as stream:
        truth = {r['id']: r for r in csv.DictReader(stream)}
    index = {(r['record_id'], r['arm']): r for r in observations if r['arm'] in freeze['arms']}
    items = packets[0]['items']
    assert len(index) == len(records)*len(freeze['arms'])
    cells, totals, changed = [], [], []
    for arm in freeze['arms']:
        for kind, key in [('raw', 'raw_values'), ('consumed', 'row')]:
            counts = dict(tp=0, fp=0, fn=0, tn=0)
            for rid in records:
                observation = index[rid, arm]
                assert observation['parse_error'] is None
                for item in items:
                    field = f'v{item}'
                    gold, value = int(truth[rid][field]), int(observation[key][field])
                    counts['tp' if value and gold else 'fp' if value else 'fn' if gold else 'tn'] += 1
                    cells.append(dict(id=rid, arm=arm, stage=kind, item=item, value=value, gold=gold))
            totals.append(dict(arm=arm, stage=kind, **counts))
        for rid in records:
            before, after = index[rid, freeze['arms'][0]], index[rid, arm]
            differing = [k for k in items if before['row'][f'v{k}'] != after['row'][f'v{k}']]
            if differing:
                changed.append(dict(id=rid, arm=arm, items=differing,
                    raw_before=before['raw_values'], raw_after=after['raw_values'],
                    final_before={f'v{k}': before['row'][f'v{k}'] for k in items},
                    final_after={f'v{k}': after['row'][f'v{k}'] for k in items},
                    before_response=json.loads(before['response']['text']),
                    after_response=json.loads(after['response']['text'])))
    # Independent source observations, not replacement gold for the frozen
    # purchase-bundle metric. A selected line does not certify semantic scope.
    coverage = []
    for rid, record in records.items():
        chosen = {p['arm']: p for p in packets if p['record_id'] == rid and p['arm'] in freeze['arms']}
        for di, doc in enumerate(record['docs']):
            for lo, hi in _source_units(doc['text']):
                text = doc['text'][lo:hi]
                roles = [name for name, pattern in (
                    ('equivalence', r'동등|동급'),
                    ('direct_production', r'직접\s*생산'),
                    ('size_qualification', r'중\s*소\s*기업|소\s*기\s*업|소상공인'),
                ) if re.search(pattern, text)]
                if not roles:
                    continue
                covered = {arm: any(s['doc_index'] == di and s['start'] <= lo and hi <= s['end']
                                   for s in p['spans']) for arm, p in chosen.items()}
                coverage.append(dict(id=rid, doc_index=di, start=lo, end=hi, text=text,
                                     roles=roles, covered=covered, semantic_scope_certified=False))
    report = dict(kind='purchase_retrieval_fresh_attribution', notices=len(records),
        items=items, primary_comparisons=len(index), repeats=len(freeze['repeated_pairs']),
        cohort_totals=totals, changed=changed, repeats_diagnostic=audit['repeated_observations'],
        mixed_scores={pop: [{k: m[k] for k in ('arm', 'mixed_macro_f1', 'fp', 'fn')}
                           for m in json.loads((path/'report.json').read_text(encoding='utf-8'))['measurements']]
                      for pop, path in [('v3', args.score_v3), ('v5', args.score_v5)]},
        labels_sha256=sha(args.labels), source_sha256=freeze['source_sha256'],
        official_score=None, full_fresh_inference=False, default_adopted=False,
        interpretation='Compare source acquisition, raw model judgments and consumed outcomes separately. No default promotion or per-item arm/repeat selection is derived by this audit.')
    save(args.output/'cells.json', cells)
    save(args.output/'source_observations.json', coverage)
    save(args.output/'report.json', report)
    print(json.dumps(totals, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
