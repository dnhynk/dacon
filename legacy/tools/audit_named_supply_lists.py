"""Find source-addressed alphanumeric supply-list candidates, not named products.

Generic interfaces, standards and product names deliberately remain candidates;
relevance, purchase role, alternatives and legal meaning require separate review.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.runtime import Journal,source_manifest,sha256
from tools.source_coverage import covered

_ENTRY=re.compile(r'(?:^|(?<=[,;]))[ \t]*(?P<name>[^,;\r\n]{2,110}?)'
                  r'[ \t*×]+(?P<quantity>\d+(?:,\d{3})*(?:\.\d+)?[ \t]*(?:개|대|점|세트|팩|쌍|식))'
                  r'(?=[ \t]*(?:[,;]|$))')
_PREFIX=re.compile(r'^[ \t]*(?:(?:[-○●□■•ㆍ]|\d{1,3}(?:-\d{1,3})*[.)]?)[ \t]+)?')
_ALPHA=re.compile(r'(?<![A-Za-z])[A-Za-z][A-Za-z0-9_-]{1,}(?![A-Za-z])')
_NONITEM=re.compile(r'https?://|www\.|@|사업자등록번호|계좌번호|전화번호|e-?mail',re.I)


def candidates(text):
    found=[]
    offset=0
    for physical in text.splitlines(keepends=True):
        line=physical.rstrip('\r\n')
        for match in _ENTRY.finditer(line):
            name=match['name'];prefix=_PREFIX.match(name).end();name=name[prefix:].strip()
            if not _ALPHA.search(name) or _NONITEM.search(name):
                continue
            lo=offset+match.start('name')+prefix
            while lo<offset+match.end('name') and text[lo].isspace():lo+=1
            hi=offset+match.end('name')
            while hi>lo and text[hi-1].isspace():hi-=1
            source={'start':lo,'end':offset+match.end(),'text':text[lo:offset+match.end()]}
            found.append({'source':source,'value_source':{'start':lo,'end':hi,'text':text[lo:hi]},
                'quantity_source':{'start':offset+match.start('quantity'),'end':offset+match.end('quantity'),
                                   'text':match['quantity']},
                'syntax':'alphanumeric_name_and_printed_item_count',
                'unique_name_certified':False,'purchase_role_certified':False})
        offset+=len(physical)
    return found


def audit(prepared,output):
    journal=Journal(output);code=source_manifest()
    def rows(name):
        with gzip.open(prepared/name,'rt',encoding='utf8') as stream:return list(map(json.loads,stream))
    records=rows('current_inputs.jsonl.gz')
    packets={p['record_id']:p for p in rows('primary_packets.jsonl.gz') if p['batch']=='A1'}
    observations=[]
    for record in records:
        for di,doc in enumerate(record['docs']):
            for value in candidates(doc['text']):
                for key in ('source','value_source','quantity_source'):
                    ref=value[key]
                    assert doc['text'][ref['start']:ref['end']]==ref['text']
                observations.append({'record_id':record['id'],'doc_index':di,'doc_id':doc['doc_id'],
                    **value,'selected_in_current_A':covered(record,packets[record['id']]['spans'],
                        {'doc_index':di,**value['source']})})
    if source_manifest()!=code:raise ValueError('Source changed during list discovery')
    journal.rows('observations.jsonl.gz',observations)
    report={'source_sha256':code,'tool_sha256':sha256(__file__),
        'input_sha256':sha256(prepared/'current_inputs.jsonl.gz'),'records':len(records),
        'notices_with_candidates':len({v['record_id'] for v in observations}),
        'candidate_occurrences':len(observations),'selected_in_current_A':sum(v['selected_in_current_A'] for v in observations),
        'per_notice':{rid:sum(v['record_id']==rid for v in observations) for rid in sorted({v['record_id'] for v in observations})},
        'labels_read':False,'new_model_calls':0,'runtime_source_changed':False,
        'scope':'Source syntax discovery. No manufacturer dictionary, name uniqueness, operative purchase, violation or full discovery recall certified.'}
    journal.save('report.json',report)
    return {k:v for k,v in report.items() if k!='source_sha256'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(audit(args.prepared,args.output),ensure_ascii=False,indent=2))
