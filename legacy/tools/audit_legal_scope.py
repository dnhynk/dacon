"""Freeze notice-law observations and consumers without labels or model calls."""
import argparse
import copy
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.legal_context import resolve_scope, applicable_law
from submission.pps.other_checks import legal_scope
from submission.pps.rules import joint_share_check
from submission.pps.temporal import v23
from submission.runtime import source_manifest

LAW = re.compile(r'국가\s*계약법|지방\s*계약법|'
                 r'국가를\s*당사자로\s*하는\s*계약에\s*관한\s*법률|'
                 r'지방자치단체를\s*당사자로\s*하는\s*계약에\s*관한\s*법률')


def read_rows(path):
    with gzip.open(path, 'rt', encoding='utf8') as stream:
        return list(map(json.loads, stream))


def observe(record):
    original = copy.deepcopy(record)
    scope = resolve_scope(record)
    mentions = []
    for di, doc in enumerate(record['docs']):
        text = doc['text']
        for match in LAW.finditer(text):
            left = max(text.rfind('\n', 0, match.start()) + 1, match.start() - 160)
            right = text.find('\n', match.end())
            if right < 0:
                right = len(text)
            right = min(right, match.end() + 230)
            mentions.append(dict(doc_index=di, doc_type=doc['type'], doc_id=doc.get('doc_id'),
                start=match.start(), end=match.end(), text=match.group(),
                context_start=left, context_end=right, context=text[left:right]))
    row = dict(id=record['id'], scope=scope, mentions=mentions,
        consumers=dict(applicable_law=applicable_law(record), legal_scope=legal_scope(record),
                       joint_share_check=joint_share_check(record), v23=v23(record)))
    for signal in scope['signals']+scope.get('ignored_declarations',[]):
        if signal['source'] == 'document':
            doc = record['docs'][signal['doc_index']]
            assert doc['text'][signal['start']:signal['end']].strip() == signal['text']
            for note in signal.get('scope_notes',[]):
                assert doc['text'][note['start']:note['end']]==note['text']
    assert record == original
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    code = source_manifest()
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    input_sha = digest(args.input)
    records = read_rows(args.input)
    rows = [observe(record) for record in records]
    assert len({r['id'] for r in rows}) == len(rows)
    report = dict(kind='source_law_observations', notices=len(rows), new_model_calls=0, labels_read=False,
        input=str(args.input), input_sha256=input_sha, source_sha256=code,
        statuses=dict(Counter(r['scope']['status'] for r in rows)),
        effective_sources=dict(Counter(r['scope']['effective_source'] for r in rows)),
        document_signals=sum(s['source']=='document' for r in rows for s in r['scope']['signals']),
        ignored_declarations=sum(len(r['scope'].get('ignored_declarations',[])) for r in rows),
        source_conflicts=[r['id'] for r in rows if r['scope']['source_conflict']])
    if args.baseline:
        old_report = json.loads((args.baseline/'report.json').read_text(encoding='utf8'))
        assert old_report['input_sha256'] == input_sha
        before = {r['id']: r for r in read_rows(args.baseline/'observations.jsonl.gz')}
        assert before.keys() == {r['id'] for r in rows}
        report.update(baseline=str(args.baseline),
            changed_scope_ids=[r['id'] for r in rows if r['scope'] != before[r['id']]['scope']],
            changed_statuses=[dict(id=r['id'], before=before[r['id']]['scope']['status'], after=r['scope']['status'])
                for r in rows if r['scope']['status'] != before[r['id']]['scope']['status']],
            changed_consumer_ids=[r['id'] for r in rows if r['consumers'] != before[r['id']]['consumers']])
    with gzip.open(args.output/'observations.jsonl.gz', 'wt', encoding='utf8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False)+'\n')
    assert source_manifest() == code and digest(args.input) == input_sha
    (args.output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    summary = {k:v for k,v in report.items() if k not in {'source_sha256','changed_scope_ids'}}
    if 'changed_scope_ids' in report:
        summary['scope_structure_changed_count'] = len(report['changed_scope_ids'])
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
