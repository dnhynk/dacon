"""Review actual v19 witnesses against document functions, without relabeling."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.pps.other_checks import pledge_check
from submission.runtime import Journal,sha256,source_manifest
from tools.run_runtime_revival import rows

CERTIFICATE=re.compile(r'제조자\s*증명서|판매대리점\s*계약서|공급자\s*증명서|파트너십\s*인증')
UNDERTAKING=re.compile(r'확\s*약|협약|보장|보증|책임|지원할|공급할|공급하여야|지원하여야')

def audit(prepared,consumed,output):
    journal=Journal(output); source=source_manifest()
    records={r['id']:r for r in rows(prepared/'current_inputs.jsonl.gz')}
    packets={p['request_key']:p for p in rows(prepared/'primary_packets.jsonl.gz')}
    observations=[]
    for entry in rows(consumed/'consumed.jsonl.gz'):
        packet=packets.get(entry['request_key'])
        if packet is None or packet['batch']!='A19': continue
        record=records[entry['record_id']];row=entry['row'];quote=row.get('e19','')
        found=[]
        for di,doc in enumerate(record['docs']):
            for match in re.finditer(re.escape(quote),doc['text']) if quote else ():
                found.append({'doc_index':di,'start':match.start(),'end':match.end(),
                    'text':match.group(),'doc_sha256':sha256_text(doc['text'])})
        if quote and not found: raise ValueError('A consumed witness is not original source')
        decision=pledge_check(record)
        certs=CERTIFICATE.findall(quote)
        flags=[]
        if row['v19'] and not quote: flags.append('positive_without_original_citation')
        if row['v19'] and certs and not UNDERTAKING.search(quote):
            flags.append('positive_witness_only_names_authenticity_or_dealership_documents')
        if row['v19'] and decision['value'] is None: flags.append('model_positive_after_source_abstention')
        observations.append({'record_id':record['id'],'request_key':entry['request_key'],
            'model_consumed_v19':row['v19'],'witness_occurrences':found,'flags':flags,
            'source_decision':decision,'candidate_document_names':certs,
            'model_positive_rewritten':False,'document_function_certified':False})
    assert len(observations)==len(records)
    assert source_manifest()==source
    journal.rows('observations.jsonl.gz',observations)
    report={'records':len(records),'source_sha256':source,'new_model_calls':0,'labels_read':False,
        'source_prepared_sha256':sha256(prepared/'input_freeze.json'),
        'consumed_sha256':sha256(consumed/'consumed.jsonl.gz'),
        'flags':dict(Counter(flag for r in observations for flag in r['flags'])),
        'candidate_document_function_review':[r['record_id'] for r in observations
            if 'positive_witness_only_names_authenticity_or_dealership_documents' in r['flags']],
        'interpretation':'Flags select original document-function relationships for review; no certificate name is a full-document negative proof and no predictions are changed.'}
    journal.save('report.json',report)
    return {k:v for k,v in report.items() if k!='source_sha256'}

def sha256_text(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('prepared','consumed','output'): p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args(); print(json.dumps(audit(args.prepared,args.consumed,args.output),ensure_ascii=False,indent=2))
