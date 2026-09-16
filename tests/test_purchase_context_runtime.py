"""Source discoveries become normal packets without a token subsidy or v1 drift."""
import copy
import dataclasses
import json

import pytest

from tests.test_integrated_candidates import tokenizer, pipeline, rec
from submission.b4_entry import restored
from submission.pps.source_units import unitize
from submission.pps.retrieval import Span
from submission.pps.specification_candidates import inventory, decode_review, NAME
from submission.pps.specification_candidate_review import validate_prepared


def unknown(plan):
    return json.dumps({NAME: {c['key']: {'specificity':'unknown','role':'unknown','requirement':'unknown',
        'scope_sources':[],'permission_sources':[],'permission_scope':'not_observed','exception_sources':[]}
        for c in plan['candidates']}})


def test_inventory_v2_preserves_counted_occurrences_without_reinterpreting_v1():
    text='1. 공급 목록\nACME Raven 1개, USB-C 케이블 2개\nACME Raven 1개\n'
    record={'id':'syntax','docs':[{'doc_id':'n','type':'공고문','text':text}]}
    units=unitize([Span(0,'공고문',0,len(text),text)])
    old=inventory(record,units)
    new=inventory(record,units,include_supply=True)
    assert not old['candidates'] and old['version']==1
    assert len(new['candidates'])==3 and new['version']==2
    assert decode_review(unknown(old),old,record,units)['declared_candidates_answered']==0
    assert decode_review(unknown(new),new,record,units)['declared_candidates_answered']==3
    for candidate in new['candidates']:
        for key in ('label_source','value_source'):
            ref=candidate[key]
            assert text[ref['start']:ref['end']]==ref['text']
    # Neither a suffix inside an omitted name nor an omitted quantity is read.
    lo=text.index('Raven'); hi=text.index('1개')+1
    partial=unitize([Span(0,'공고문',lo,hi,text[lo:hi])])
    assert not inventory(record,partial,include_supply=True)['candidates']


@pytest.mark.parametrize('policy',['purchase_context','purchase_context_hybrid'])
def test_new_context_policy_is_normal_A_source_with_same_original_budget(tokenizer,rec,policy):
    import numpy as np
    class Encoder:
        def encode(self,texts):
            return np.tile(np.array([[1.,0.]],dtype=np.float32),(len(texts),1))
    rec=copy.deepcopy(rec)
    rec['docs'][1]['text']='1. 구성품\nACME Raven 1개, USB-C 케이블 2개\n2. 대체\n동등 이상 성능의 제품 허용'
    control={p['batch']:p for p in pipeline(tokenizer).packets([rec])}
    pipe=pipeline(tokenizer,source_policy=policy,specification_review='gated_source_candidates',encoder=Encoder())
    packets={p['batch']:p for p in pipe.packets([rec])}
    assert packets['L19']==control['L19']
    for profile in ('A1','A10','A19'):
        old,new=control[profile],packets[profile]
        assert old['generation']==new['generation']
        cap=sum(len(tokenizer.encode(s['text'],add_special_tokens=False)) for s in old['spans'])
        assert new['source_search']['source_tokens']<=cap
        assert new['source_search']['diagnostics']['purchase_context']['full_source_syntax_candidates']==2
        for span in new['spans']:
            assert rec['docs'][span['doc_index']]['text'][span['start']:span['end']]==span['text']
    plan=validate_prepared(rec,restored(packets['S9']))
    assert plan['version']==4 and len(plan['candidates'])==2


def test_context_without_discovered_links_keeps_the_current_packets(tokenizer,rec):
    # Explicit model fields alone are handled by the existing specialist;
    # this source candidate tests counted lists and literal detail links.
    base=pipeline(tokenizer).packets([rec])
    actual=pipeline(tokenizer,source_policy='purchase_context').packets([rec])
    assert actual==base
