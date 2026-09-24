"""Source-only region checks with format-preserving anonymous-token probes."""
import argparse
from collections import Counter
import copy
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.pps.rules import narrow_region_check
from submission.pps.regions import multiple_region_check,above_ceiling_region_check
from submission.pps.performance import performance_facts
from submission.runtime import source_manifest

AUTHORITY=re.compile(r'\[수요기관\(([^\]\n()]+)\)((?:\|[^\]\n]*)?)\]')
ANON=re.compile(r'\[(?:지역:[^\]\n]+|수요기관\([^\]\n()]+\)(?:\|[^\]\n]*)?)\]')


def checks(record):
    result={str(k):f(record) for k,f in ((5,above_ceiling_region_check),(6,narrow_region_check),(7,multiple_region_check))}
    result['8']=performance_facts(record)['overlays']['v8']
    return result


def bits(found):
    return {k:r['value'] if r else None for k,r in found.items()}


def variants(record):
    for policy in ('remove_authority_attributes','reverse_attributes','rename_local_symbols'):
        changed=copy.deepcopy(record)
        for i,doc in enumerate(changed['docs']):
            text=doc['text']
            if policy=='remove_authority_attributes':
                text=AUTHORITY.sub(lambda m:'[수요기관('+m[1]+')]',text)
            elif policy=='reverse_attributes':
                def reverse(match):
                    parts=match[0][1:-1].split('|')
                    return '['+'|'.join([parts[0],*reversed(parts[1:])])+']'
                text=ANON.sub(reverse,text)
            else:
                symbols=sorted(set(re.findall(r'(?:지역:|지역=)(r\d+)\b',text)))
                mapping={s:'r'+str(900000+i*10000+n) for n,s in enumerate(symbols)}
                def rename(match):
                    return re.sub(r'(지역[:=])(r\d+)\b',lambda m:m[1]+mapping[m[2]],match[0])
                text=ANON.sub(rename,text)
            doc['text']=text
        if changed!=record:
            yield policy,changed


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--baseline',type=Path)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    code=source_manifest();input_hash=hashlib.sha256(args.input.read_bytes()).hexdigest()
    with gzip.open(args.input,'rt',encoding='utf-8') as stream:records=list(map(json.loads,stream))
    observations=[];probes=[]
    for record in records:
        actual=checks(record)
        observations.append(dict(id=record['id'],checks=actual,
            authorities=[dict(doc_index=i,start=m.start(),end=m.end(),text=m[0],authority_type=m[1])
                for i,d in enumerate(record['docs']) for m in AUTHORITY.finditer(d['text'])]))
        for policy,changed in variants(record):
            result=checks(changed)
            probes.append(dict(id=record['id'],policy=policy,before=bits(actual),after=bits(result),
                applicability_changed=bits(actual)!=bits(result)))
    differences=[]
    if args.baseline:
        old_report=json.loads((args.baseline/'report.json').read_text(encoding='utf-8'))
        assert old_report['input_sha256']==input_hash
        with gzip.open(args.baseline/'observations.jsonl.gz','rt',encoding='utf-8') as stream:
            old={r['id']:r for r in map(json.loads,stream)}
        for row in observations:
            if row['checks']!=old[row['id']]['checks']:
                differences.append(dict(id=row['id'],before=old[row['id']]['checks'],after=row['checks'],
                    applicability_changed=bits(old[row['id']]['checks'])!=bits(row['checks'])))
    for name,rows in (('observations',observations),('probes',probes)):
        with gzip.open(args.output/(name+'.jsonl.gz'),'wt',encoding='utf-8') as stream:
            for row in rows:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    if args.baseline:
        with (args.output/'differences.json').open('x',encoding='utf-8') as stream:json.dump(differences,stream,ensure_ascii=False,indent=2)
    report=dict(kind='source_only_region_token_audit',source_sha256=code,input_sha256=input_hash,
        labels_read=False,new_model_calls=0,records=len(records),source_decision_changes=sum(r['applicability_changed'] for r in differences),
        actual_positive_checks=dict(Counter(k for r in observations for k,c in r['checks'].items() if c and c['value']==1)),
        synthetic_transformations=len(probes),format_sensitive_checks=sum(r['applicability_changed'] for r in probes),
        format_sensitive_examples=[r for r in probes if r['applicability_changed']],
        limitation='Transformed inputs are probes only; no source identity or final F1 is certified. Removing authority attributes tests existing type-only rules, not every possible future spatial relation.')
    assert code==source_manifest() and input_hash==hashlib.sha256(args.input.read_bytes()).hexdigest()
    with (args.output/'report.json').open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in report.items() if k!='source_sha256'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
