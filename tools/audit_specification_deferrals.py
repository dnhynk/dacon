"""Inventory decision-irrelevant unknowns without changing model judgments."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.runtime import Journal,sha256


def audit(paths,output):
    journal=Journal(output);observations=[];counts=Counter()
    for path in paths:
        original=sha256(path)
        with gzip.open(path,'rt',encoding='utf8') as stream:
            for entry in map(json.loads,stream):
                details=entry.get('details',[])
                if not isinstance(details,list):continue
                found=next((d.get('facts') for d in details if isinstance(d,dict)
                    and d.get('source')=='complete_observed_specification_candidate_review'),None)
                if found is None:continue
                counts['review_responses']+=1
                if found['judgment']['v']!=0:continue
                counts['negative_judgments']+=1
                unresolved=set(found['unresolved_candidates']);irrelevant={};remaining=[]
                for key,answer in found['reviews'].items():
                    # These are fallible model predicates, not certified source
                    # truth. An observed generic value, an explicitly excluded
                    # purchase role or an example can make other unknowns
                    # irrelevant to this candidate's necessary conditions.
                    why=[]
                    if answer['specificity']=='generic':why.append('model_generic_specification')
                    if answer['role']=='not_procurement' and answer['scope_sources']:
                        why.append('model_excludes_procurement_with_scope_source')
                    if answer['requirement']=='example' and answer['scope_sources']:
                        why.append('model_example_with_scope_source')
                    if key in unresolved and why:irrelevant[key]=why
                    elif key in unresolved:remaining.append({'candidate':key,'reason':'unresolved_applicable_relationship'})
                    if (answer['specificity']=='named' and answer['requirement']=='mandatory'
                            and answer['role'] in {'new_whole_product','new_component','replacement_component'}):
                        permitted=(bool(answer['permission_sources']) and answer['permission_scope']=='this_candidate'
                            and answer['permission_attribute']=='brand_or_model' and answer['permission_effect']=='allowed')
                        if not permitted and not answer['exception_sources']:
                            remaining.append({'candidate':key,'reason':'named_mandatory_purchase_without_resolved_permission_or_exception'})
                if irrelevant:
                    counts['negative_with_decision_irrelevant_unknowns']+=1
                    if not remaining:counts['all_current_blockers_potentially_irrelevant']+=1
                    observations.append({'consumed':str(path),'consumed_sha256':original,
                        'request_key':entry['request_key'],'record_id':entry['record_id'],
                        'model_judgment':found['judgment'],'observed_row':entry['row'],
                        'irrelevant_unknown_candidates':irrelevant,'remaining_blockers':remaining,
                        'reviews':found['reviews'],'predictions_changed':False,'semantic_truth_certified':False})
        assert sha256(path)==original
    journal.rows('observations.jsonl.gz',observations)
    report={'counts':dict(counts),'inputs':{str(p):sha256(p) for p in paths},
        'labels_read':False,'new_model_calls':0,'predictions_changed':False,
        'interpretation':'Counterfactual deferral audit only. Verify current source context and compare whole saved/fresh cohorts before changing the canonical overlay; model predicates remain fallible.'}
    journal.save('report.json',report);return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--consumed',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(audit(a.consumed,a.output),ensure_ascii=False,indent=2))
