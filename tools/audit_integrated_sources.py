"""Human-reviewed source anchors and absence reading scope at fixed raw-token caps."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.run_integrated_comparison import load,read,rows,sha
from tools.compare_notice_retrieval import hit
from submission.pps.qualification import inventory,qualification_facts
from submission.pps.prompts import verified_search_spans
from tools.source_coverage import reading_ranges
from submission.runtime import Journal


def audit(prepared,review_manifest,tokenizer_dir,output):
    from transformers import AutoTokenizer
    if (prepared/'primary_packets.jsonl.gz').is_file():
        from tools.run_runtime_revival import verify_prepared
        freeze=verify_prepared(prepared)
        recs=list(rows(prepared/'current_inputs.jsonl.gz'))
        index={p['request_key']:p for p in [*rows(prepared/'primary_packets.jsonl.gz'),*rows(prepared/'probe_packets.jsonl.gz')]}
        policies=read(prepared/'preregistered.json')['arms']
        recipes=read(prepared/'recipes.json')
        packets={(index[key]['record_id'],policy,index[key]['batch']):index[key]
            for policy in policies for key in recipes[f'{policy}_catalog_explicit_v9_gated']
            if index[key]['batch'] in {'A1','L19','S9'}}
    else:
        freeze,recs,native,plan=load(prepared)
        packets={(p['record_id'],p['experiment_policy'],p['experiment_role']):p
            for p in rows(prepared/'canonical_packets.jsonl.gz') if p['experiment_role'] in {'A1','L19','S9'}}
        policies=read(prepared/'preregistered.json')['source_policies']
    journal=Journal(output)
    tokenizer=AutoTokenizer.from_pretrained(tokenizer_dir,local_files_only=True,trust_remote_code=False)
    by_id={r['id']:r for r in recs}; review=read(review_manifest)
    cases=[];scope=[];capacity=[]
    for rec in recs:
        facts=qualification_facts(rec,inventory(rec)); sections=facts['eligibility_sections']
        for policy in policies:
            a=packets[rec['id'],policy,'A1'];l=packets[rec['id'],policy,'L19']
            # An original sentence/section may cross adjacent returned spans.
            # Reading coverage is their source-local union, not the ability to
            # emit the entire quotation by selecting one S number. Only gaps
            # that are actually whitespace in the original may be joined.
            a_union={'spans':reading_ranges(rec,a['spans'])}
            covered=[hit(section['evidence'],a_union) for section in sections]
            ranges=[]; nonwhite_gaps=0
            for di,doc in enumerate(rec['docs']):
                selected=[s for s in a_union['spans'] if s['doc_index']==di]
                cursor=0;gaps=[]
                for s in selected:
                    if doc['text'][cursor:s['start']].strip():gaps.append([cursor,s['start']])
                    cursor=s['end']
                if doc['text'][cursor:].strip():gaps.append([cursor,len(doc['text'])])
                nonwhite_gaps+=len(gaps)
                ranges.append({'doc_index':di,'doc_id':doc['doc_id'],'provided_chars':len(doc['text']),
                    'selected_ranges':[[s['start'],s['end']] for s in selected],'unseen_nonwhitespace_ranges':gaps})
            scope.append({'record_id':rec['id'],'policy':policy,
                'provided_completeness':rec.get('input_completeness',{}),'dropped_doc_counts':rec.get('dropped_doc_counts',{}),
                'all_provided_text_selected':nonwhite_gaps==0,'documents':ranges,
                'detected_eligibility_sections':len(sections),'complete_eligibility_sections_selected':sum(covered),
                'all_detected_eligibility_sections_selected':bool(sections) and all(covered),
                'source_has_closed_eligibility':facts['closed_eligibility'],
                'source_qualification_deferrals_resolved':facts['reference_coverage']['qualification_deferrals_resolved'],
                'absence_verified':False,'section_detection_is_not_complete_semantic_scope_proof':True})
            for p in (a,l):
                current=packets[rec['id'],'current',p.get('experiment_role',p['batch'])]
                cap=sum(len(tokenizer.encode(s['text'],add_special_tokens=False)) for s in current['spans'])
                used=sum(len(tokenizer.encode(s['text'],add_special_tokens=False)) for s in p['spans'])
                if used>cap:raise ValueError('Candidate exceeds the original source cap')
                if p.get('source_search') is not None:verified_search_spans(rec,p['source_search'],tokenizer)
            specialist=packets.get((rec['id'],policy,'S9'))
            if specialist is not None:
                from tools.audit_specification_candidates import unknown_response
                from submission.pps.specification_candidate_review import NAME,decode
                from submission.b4_entry import restored
                obj=unknown_response(specialist['specification_inventory'])
                for v in obj[NAME].values():v.update(permission_attribute='unknown',permission_effect='unknown')
                obj.update(unresolved='미확인',judgment={'reason':'미확인','v':0,'e':0})
                raw=json.dumps(obj,ensure_ascii=False,separators=(',',':'))
                decode(raw,restored(specialist)['spans'],specialist['specification_inventory'],rec)
                tokens=len(tokenizer.encode(raw,add_special_tokens=False))
                # A complete unknown answer is only the smallest useful shape.
                # Also exercise the schema's full three source lists and long
                # explanations, without claiming these synthetic relations are
                # source truth or an exhaustive Unicode token upper bound.
                from copy import deepcopy
                detailed=deepcopy(obj)
                sources=list(range(max(1,len(specialist['spans'])-5),len(specialist['spans'])+1))
                for candidate in specialist['specification_inventory']['candidates']:
                    detailed[NAME][candidate['key']].update(
                        specificity='named' if candidate['value_source'] is not None else 'unknown',
                        role='replacement_component',requirement='mandatory',scope_sources=sources,
                        permission_sources=sources,exception_sources=sources,permission_scope='whole_product_only',
                        permission_attribute='manufacturer_consistency',permission_effect='conditional')
                detailed['unresolved']=('조건의 적용 범위와 상위 납품 대상은 원문 관계를 확인해야 한다. '*4)[:120]
                detailed['judgment']['reason']=('각 후보의 납품 역할과 허용 대상 및 예외 요건을 함께 검토한다. '*4)[:110]
                detailed_raw=json.dumps(detailed,ensure_ascii=False,separators=(',',':'))
                decode(detailed_raw,restored(specialist)['spans'],specialist['specification_inventory'],rec)
                detailed_tokens=len(tokenizer.encode(detailed_raw,add_special_tokens=False))
                capacity.append({'record_id':rec['id'],'policy':policy,
                    'candidates':len(specialist['specification_inventory']['candidates']),
                    'valid_unknown_answer_tokens':tokens,'output_token_limit':specialist['generation']['max_output_tokens'],
                    'fits_example':tokens<=specialist['generation']['max_output_tokens'],
                    'valid_detailed_example_tokens':detailed_tokens,
                    'fits_detailed_example':detailed_tokens<=specialist['generation']['max_output_tokens'],
                    'synthetic_capacity_example_is_not_a_semantic_answer':True,
                    'all_possible_answers_fit_certified':False})
    for case in review['cases']:
        rec=by_id[case['record_id']]
        for e in case['required_bundle']:
            if hashlib.sha256(rec['docs'][e['doc_index']]['text'].encode()).hexdigest()!=e['doc_sha256']:
                raise ValueError('Human review source changed')
        role='L19' if case['items']==[20] else 'A1'
        for policy in policies:
            packet=packets[rec['id'],policy,role]
            merged={'spans':reading_ranges(rec,packet['spans'])}
            hits={e['evidence_id']:hit(e,merged) for e in case['required_bundle']}
            supports=[hits[e['evidence_id']] for e in case['required_bundle'] if e['role']=='support']
            cases.append({'case':case['task_id'],'record_id':rec['id'],'policy':policy,'profile':role,
                'resolution':case['resolution'],'any_review_anchor_hit':any(hits.values()),
                'related_support_sentence_hit':any(supports),'known_review_bundle_hit':bool(hits) and all(hits.values()),
                'review_resolved_and_bundle_hit':case['resolution']=='resolved' and bool(hits) and all(hits.values()),
                'evidence_hits':hits,'unresolved_information':case['unresolved_information'],
                'single_span_anchor_hits':{e['evidence_id']:hit(e,packet) for e in case['required_bundle']},
                'source_tokens':sum(len(tokenizer.encode(s['text'],add_special_tokens=False)) for s in packet['spans'])})
    aggregated={}
    for policy in policies:
        group=[r for r in cases if r['policy']==policy];sg=[r for r in scope if r['policy']==policy]
        aggregated[policy]={'review_cases':len(group),
            **{key:sum(r[key] for r in group) for key in ('any_review_anchor_hit','related_support_sentence_hit',
                'known_review_bundle_hit','review_resolved_and_bundle_hit')},
            'anchors':sum(sum(r['evidence_hits'].values()) for r in group),
            'anchor_denominator':sum(len(r['evidence_hits']) for r in group),
            'review_source_tokens':sum(r['source_tokens'] for r in group),
            'source_notices':len(sg),'entire_provided_text_selected':sum(r['all_provided_text_selected'] for r in sg),
            'all_detected_eligibility_sections_selected':sum(r['all_detected_eligibility_sections_selected'] for r in sg),
            'detected_eligibility_section_denominator':sum(r['detected_eligibility_sections'] for r in sg),
            'complete_eligibility_sections_selected':sum(r['complete_eligibility_sections_selected'] for r in sg)}
    journal.save('human_cases.json',cases);journal.save('absence_reading_scope.json',scope)
    journal.save('specialist_answer_capacity.json',capacity)
    report={'aggregates':aggregated,'prepared_sha256':sha(prepared/'input_freeze.json'),
        'review_manifest_sha256':sha(review_manifest),'all_source_token_caps_verified':True,
        'all_valid_unknown_specialist_answers_fit':all(r['fits_example'] for r in capacity),
        'all_detailed_specialist_examples_fit':all(r['fits_detailed_example'] for r in capacity),
        'largest_specialist_inventory':max((r['candidates'] for r in capacity),default=0),
        'largest_valid_unknown_answer_tokens':max((r['valid_unknown_answer_tokens'] for r in capacity),default=0),
        'labels_read':False,'new_model_calls':0,'review_is_exposed_development_not_official_gold':True,
        'absence_measurement':'Provided input completeness, selected original ranges and detected eligibility sections; no absence prediction inferred',
        'bundle_measurement':'Source-local union covers every reviewed support/context/limit range; only original whitespace gaps joined; relation correctness is not certified',
        'single_span_citation_availability_reported_separately':True}
    journal.save('report.json',report);return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepared',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--review-manifest',type=Path,default=ROOT/'runs/independent_audit_20260913/retrieval_v1/preregistered.json')
    p.add_argument('--tokenizer-dir',type=Path,default=ROOT/'models/gemma-tokenizer')
    args=p.parse_args();print(json.dumps(audit(args.prepared,args.review_manifest,args.tokenizer_dir,args.output),ensure_ascii=False,indent=2))
