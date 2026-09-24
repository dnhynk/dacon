"""Compare source qualification extraction with a preserved consumption trace; no labels."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.qualification import inventory, qualification_facts
from submission.runtime import source_manifest

FIELDS = ('allowed', 'no_direct', 'no_size', 'closed_eligibility', 'size_conflict')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return list(map(json.loads, stream))


def audit(inputs, reference, output):
    source = source_manifest()
    hashes = {str(p.resolve()): sha(p) for p in (inputs, reference)}
    records = rows(inputs)
    prior = {r['id']:r['source_qualification'] for r in rows(reference) if r['item']==10}
    if set(prior) != {r['id'] for r in records}:
        raise ValueError('Reference and input record identities differ')
    results = []
    for rec in records:
        current = qualification_facts(rec, inventory(rec))
        # Verify all nested source references, including headings and wrapped
        # candidates, without substituting normalized or reconstructed text.
        def check(value):
            if isinstance(value, dict):
                if {'doc_index','start','end','text'} <= value.keys():
                    assert rec['docs'][value['doc_index']]['text'][value['start']:value['end']] == value['text']
                for v in value.values():
                    check(v)
            elif isinstance(value, list):
                for v in value:
                    check(v)
        check(current)
        old = prior[rec['id']]
        differences = {k:[old[k],current[k]] for k in FIELDS if old[k]!=current[k]}
        def heads(q):
            return {(s['evidence']['doc_index'],s['evidence']['start']):
                    s['evidence']['text'].splitlines()[0] for s in q['eligibility_sections']}
        old_heads, new_heads = heads(old), heads(current)
        results.append(dict(id=rec['id'], changed_facts=differences,
            removed_headings=[dict(doc_index=k[0],start=k[1],text=v) for k,v in old_heads.items() if k not in new_heads],
            added_headings=[dict(doc_index=k[0],start=k[1],text=v) for k,v in new_heads.items() if k not in old_heads],
            qualification=current))
    if source_manifest()!=source or any(sha(Path(p))!=h for p,h in hashes.items()):
        raise ValueError('Source or evidence changed')
    output.mkdir(parents=True,exist_ok=False)
    with gzip.open(output/'observations.jsonl.gz','wt',encoding='utf-8') as stream:
        for r in results:
            stream.write(json.dumps(r,ensure_ascii=False)+'\n')
    report = dict(kind='source_qualification_structure_comparison', source_sha256=source,input_sha256=hashes,
        records=len(records),labels_read=False,new_model_calls=0,original_evidence_offsets_verified=True,
        fact_changes=[{k:v for k,v in r.items() if k!='qualification'} for r in results if r['changed_facts']],
        removed_heading_count=sum(len(r['removed_headings']) for r in results),
        added_heading_count=sum(len(r['added_headings']) for r in results),
        limitation='A changed source extraction is not a correctness or F1 certificate. Inspect source relations before scoring.')
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if not k.endswith('sha256')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args()
    audit(a.inputs,a.reference,a.output)
