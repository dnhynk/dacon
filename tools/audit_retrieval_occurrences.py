"""Measure coordinate-preserving legacy selection against frozen input ranges."""
import argparse
import copy
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.pps.notice_search import NoticeSearch
from submission.runtime import source_manifest
from tools.audit_purchase_feedback import annotate,save,sha
from tools.audit_purchase_fact_groups import covered
from tools.build_submission import build
from tools.score_catalog_scope import rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('run','review','observations','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    folder=args.run/'contrast_output'
    records=rows(folder/'current_inputs.jsonl.gz')
    packets=rows(folder/'current_packets.jsonl.gz')
    cases={r['id']:r for r in json.loads(args.review.read_text(encoding='utf8'))['cases']}
    observations=json.loads(args.observations.read_text(encoding='utf8'))
    source=source_manifest();snapshot=build(args.output/'source_snapshot.zip')
    save(args.output/'preregistered.json',dict(source_manifest=source,source_fingerprint=snapshot['source_fingerprint'],
        input_sha256=sha(folder/'current_inputs.jsonl.gz'),packets_sha256=sha(folder/'current_packets.jsonl.gz'),
        review_sha256=sha(args.review),observations_sha256=sha(args.observations),tool_sha256=sha(__file__),
        source_token_budget=4096,labels_read=False,gpu_used=False,new_gemma_calls=0,
        hypothesis='Equal text at distinct addresses must remain independently selectable, including product permissions and table headers.'))
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer',local_files_only=True,trust_remote_code=False)
    started=time.monotonic();results=[]
    for rec in records:
        old=copy.deepcopy(next(p['source_search'] for p in packets if p['record_id']==rec['id'] and p['arm']=='A_current'))
        old.update(arm='A_current',record_id=rec['id']);annotate(old,cases[rec['id']]);results.append(old)
        new=NoticeSearch(rec,tokenizer).search([9,10,11,18],method='current',token_budget=4096)
        new.update(arm='F_original_occurrences');annotate(new,cases[rec['id']]);results.append(new)
    by_key={(r['record_id'],r['arm']):r for r in results}
    observed=[{**r,'covered':{a:covered(by_key[r['id'],a],r) for a in ('A_current','F_original_occurrences')}}
              for r in observations]
    summary=[]
    for arm in ('A_current','F_original_occurrences'):
        group=[r for r in results if r['arm']==arm]
        summary.append(dict(arm=arm,related=sum(r['review']['related_sentence_found'] for r in group),
            known_bundle=sum(r['review']['known_required_bundle_found'] for r in group),
            full_bundle=sum(r['review']['full_decision_bundle_found'] for r in group),
            source_tokens=sum(r['source_tokens'] for r in group),
            automatic_source_lines={role:dict(covered=sum(r['covered'][arm] for r in observed if role in r['roles']),
                total=sum(role in r['roles'] for r in observed)) for role in ('equivalence','direct_production','size_qualification')}))
    assert source_manifest()==source
    save(args.output/'results.json',results);save(args.output/'source_observations.json',observed)
    save(args.output/'report.json',dict(summary=summary,source_manifest=source,seconds=time.monotonic()-started,
        labels_read=False,gpu_used=False,new_gemma_calls=0,final_F1_measured=False,official_score=None))
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
