"""B2 review fix: preserve explicitly referenced cross-line early pledge events."""
import copy
import re
from . import pledge_structure as b1

original_check=b1.original_check
decide=b1.decide
REFERENCE=re.compile(r'(?:위|상기|해당|당해)(?:의)?\s*(확약서|협약서|서류)')
EARLY=re.compile(r'입찰\s*(?:서\s*)?(?:제출\s*)?(?:마감(?:일)?\s*)?전(?:일|까지|에)?')
EVENT=re.compile(r'발급\s*(?:받|하여|해야)|보유|제출')
NEGATED=re.compile(r'예시|가정|않아도|필요\s*없|요구하지\s*않|보유하지\s*않|면제|불요|철회|삭제')


def pledge_check(rec):
    output=b1.pledge_check(rec)
    facts=copy.deepcopy(output['facts'])
    pledges=facts['pledges']
    links=[];unresolved=[]
    for di,doc in enumerate(rec['docs']):
        text=doc['text']
        for match in re.finditer(r'[^\r\n]+',text):
            q=match.group();ref=REFERENCE.search(q)
            if not ref or not EARLY.search(q) or not EVENT.search(q) or NEGATED.search(q):continue
            event_ev=b1.ev(rec,di,match.start(),match.end())
            if any(p['clause_evidence']['doc_index']==di and
                   p['clause_evidence']['start']<=match.start()<p['clause_evidence']['end'] for p in pledges):continue
            # A demonstrative pledge reference must have an unambiguous pledge
            # antecedent inside the SAME explicit governing list. Crossing a new
            # numbered clause or guessing which of several documents is meant
            # does not establish a new positive obligation.
            frames=[g for g in facts['structure']['governors'] if g['doc_index']==di and g['start']<match.start()<g['end']]
            frame=max(frames,key=lambda g:g['start']) if frames else None
            candidates=[p for p in pledges if frame and p['clause_evidence']['doc_index']==di
                        and frame['start']<p['clause_evidence']['start']<match.start()]
            if ref[1] in ('확약서','협약서') and len(candidates)==1:
                p=candidates[0];clause=p['clause_evidence']
                p['timing']='explicit_pre_bid'
                if re.search(r'보유|발급\s*받',q):p['possession_required']=True
                actions=[]
                if re.search(r'발급',q):actions.append('issue_or_receive')
                if re.search(r'보유',q):actions.append('hold')
                if re.search(r'제출',q):actions.append('submit')
                for action in actions:
                    p['events'].append({'action':action,'stage':'explicit_pre_bid','evidence':event_ev,
                                        'antecedent':copy.deepcopy(clause),'binding':'explicit_same_pledge_reference_in_same_list'})
                binding={'kind':'explicit_same_pledge_reference_in_same_list','antecedent':copy.deepcopy(clause),
                         'reference_event':event_ev,'list_governor':frame['evidence']}
                p['structural_links'].append(binding);links.append(binding)
                p['evidence']=b1.ev(rec,di,min(clause['start'],match.start()),max(clause['end'],match.end()))
            elif candidates:
                # Ambiguous early references cannot license an all-later
                # negative. Keep the facts and abstain instead of borrowing the
                # requirement for a particular issuer/document.
                unresolved.append({'event':event_ev,'reference_type':ref[1],
                                   'possible_antecedents':[copy.deepcopy(p['clause_evidence']) for p in candidates]})
                for p in candidates:
                    p['uncertain_context']=True
                    p['unresolved_early_reference']=copy.deepcopy(event_ev)
    facts['cross_line_reference_events']={'bound':links,'unresolved':unresolved}
    facts['extraction']='document_list_form_event_binding_B2'
    return decide(rec,facts)
