"""v19 source-only structural extraction; unchanged pledge decision predicates."""
import copy
import re
from .other_checks import _pledge_check_basic as original_check, complete, result

def norm(text):return re.sub(r'\s+','',text)
def ev(rec,di,start,end):
    doc=rec['docs'][di]
    return {'doc_index':di,'doc_type':doc['type'],'start':start,'end':end,'quote':doc['text'][start:end]}

REF=re.compile(r'[\[【<〈(]?(?:첨부|붙임|별첨|서식)\s*(\d{1,3})\s*[\]】>〉)]?')
FORM_HEADER=re.compile(r'^\s*[\[【<〈(]?(?:첨부|붙임|별첨|서식)\s*\d{1,3}\s*[\]】>〉)]?\s*$')
ISSUER=re.compile(r'제조\s*(?:\(\s*수입\s*\))?\s*사|제조\s*업체|원\s*제조사|기술\s*지원사|공급사')
EARLY_LIST=re.compile(r'입찰\s*(?:참가\s*)?(?:제출\s*서류|참가\s*제안\s*서류|시\s*제출)|입찰\s*참가\s*제안\s*서류')
LATE_STAGE=re.compile(r'계약\s*(?:체결\s*)?(?:시|이후|후)|낙찰\s*(?:후|이후)|착수\s*전|납품\s*(?:전|후)')
LIST_REQUEST=re.compile(r'(?:아래|다음)(?:의)?\s*서류.{0,45}제출|제출\s*서류')
NEGATIVE_FRAME=re.compile(r'예시|가정|작성\s*예|해당하지|적용하지|삭제|철회|아닌\s*것은\s*아니')
DELIVERY_TITLE=re.compile(r'납품\s*(?:\(\s*설치\s*\)|및\s*설치|[·ㆍ‧/]\s*설치)?\s*확인서')
DELIVERY_FOOTER=re.compile(r'납품\s*(?:및\s*설치|[·ㆍ‧/]\s*설치)?\s*(?:후|완료\s*후).{0,80}(?:본\s*)?확인서.{0,60}(?:첨부|제출).{0,60}(?:대금|청구)')


def level(raw):
    s=raw.strip()
    if FORM_HEADER.fullmatch(s):return 0
    if re.match(r'^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.．]?\s*',s):return 5
    if re.match(r'^\d{1,3}[.．]\s*',s):return 10
    if re.match(r'^[가-하][.．]\s*',s):return 20
    if re.match(r'^\d{1,3}[)]\s*',s):return 30
    if re.match(r'^[①-⑳➀-➉]',s):return 40
    return None


def structure(rec):
    """Explicit list governors, form bounds, and reference anchors; no distance join."""
    governors=[];forms=[];delivery_forms=[]
    for di,doc in enumerate(rec['docs']):
        text=doc['text'];lines=list(re.finditer(r'[^\r\n]+',text))
        active=None;section_level=10
        form_marks=[]
        for m in lines:
            raw=m.group();rank=level(raw)
            if active is not None and rank is not None and rank<=active['rank']:
                active['end']=m.start();governors.append(active);active=None
            if rank is not None:section_level=rank
            stage=None;kind=None
            if not NEGATIVE_FRAME.search(raw):
                if EARLY_LIST.search(raw):stage='explicit_pre_bid';kind='bid_submission_section'
                elif LATE_STAGE.search(raw) and LIST_REQUEST.search(raw):
                    stage='explicit_later_stage';kind='explicit_later_submission_list'
            if stage:
                if active is not None:
                    active['end']=m.start();governors.append(active)
                active={'doc_index':di,'start':m.start(),'end':len(text),'rank':rank if rank is not None else section_level,
                        'stage':stage,'kind':kind,'evidence':ev(rec,di,m.start(),m.end()),
                        'required_party':'contract_performer' if re.search(r'사업수행자|계약상대자|납품업체',raw) else 'bid_participant' if stage=='explicit_pre_bid' else 'unresolved'}
            if FORM_HEADER.fullmatch(raw.strip()):form_marks.append(m)
        if active is not None:governors.append(active)
        for i,m in enumerate(form_marks):
            end=form_marks[i+1].start() if i+1<len(form_marks) else len(text)
            forms.append({'number':REF.search(m.group())[1],'doc_index':di,'start':m.start(),'end':end,
                          'evidence':ev(rec,di,m.start(),m.end())})
        titles=[]
        for m in lines:
            q=m.group()
            if (len(q)<=90 and DELIVERY_TITLE.search(q) and re.search(r'확인서\s*$',q)
                    and not re.search(r'첨부|참조|제출|예시',q)):
                titles.append(m)
        for i,m in enumerate(titles):
            end=titles[i+1].start() if i+1<len(titles) else len(text)
            # The same bounded form must explicitly say it accompanies a
            # post-delivery payment claim. A form title alone does not date a pledge.
            footer=next((x for x in lines if m.end()<=x.start()<end and DELIVERY_FOOTER.search(x.group())
                         and not NEGATIVE_FRAME.search(x.group())),None)
            if footer:
                delivery_forms.append({'doc_index':di,'start':m.start(),'end':footer.end(),
                    'title':ev(rec,di,m.start(),m.end()),'footer':ev(rec,di,footer.start(),footer.end()),
                    'function':'post_delivery_installation_confirmation_with_payment_claim'})
    return governors,forms,delivery_forms


def local_events(p):
    q=p['evidence']['quote'];out=[]
    if p['possession_required']:
        out.append({'action':'hold_or_obtain','stage':p['timing'] if p['timing']=='explicit_pre_bid' else 'unresolved',
                    'evidence':p['evidence']})
    if re.search(r'제출',q):
        stage='explicit_later_stage' if LATE_STAGE.search(q) else p['timing']
        out.append({'action':'submit','stage':stage,'evidence':p['evidence']})
    if re.search(r'발급',q):
        out.append({'action':'issue_or_receive','stage':'unresolved','evidence':p['evidence']})
    return out


def decide(rec,facts):
    """The existing v19 decision body, with only the facts supplied separately."""
    pledges=facts['pledges']
    positive=[p for p in pledges if p['issuer']=='manufacturer_or_support_provider' and p['timing']=='explicit_pre_bid' and not p['explicit_no_bid_time_requirement'] and not p['submission_capability_only'] and not p['uncertain_context']]
    usable=[p for p in positive if 0<len(p['evidence']['quote'])<=500]
    if usable and facts['scope']['known']:return result(1,'explicit_third_party_pre_bid_pledge',facts,usable[0]['evidence']['quote'])
    if positive:return result(None,'positive_proof_scope_or_evidence_unresolved',facts)
    if not complete(rec):return result(None,'incomplete_documents_no_proven_positive',facts)
    safe=[p for p in pledges if not p['uncertain_context'] and (p['bidder_written'] or p['explicit_no_bid_time_requirement'] or p['timing']=='explicit_later_stage')]
    def bound_later(p):
        return p['timing']=='capability_only' and len(p['pledge_bundle'])>=15 and any(
            x['timing']=='explicit_later_stage' and x['pledge_bundle']==p['pledge_bundle'] and x['issuer']==p['issuer'] for x in safe)
    if pledges and all(p in safe or bound_later(p) for p in pledges):
        return result(0,'all_pledges_explicitly_later_or_bidder_written',facts)
    if not pledges and facts['other_document_functions']:return result(0,'only_unrelated_security_undertaking_recognized',facts)
    return result(None,'issuer_or_required_timing_unresolved' if pledges else 'no_proven_pledge_facts',facts)


def pledge_check(rec):
    original=original_check(rec)
    facts=copy.deepcopy(original['facts']);pledges=facts['pledges']
    governors,forms,delivery_forms=structure(rec)
    facts['structure']={'governors':governors,'forms':forms,'delivery_forms':delivery_forms}
    for p in pledges:
        p['events']=local_events(p)
        p['document_function']='supply_or_technical_support_pledge'
        p['required_party']='unresolved'
        p['structural_links']=[]
        e=p['evidence'];q=e['quote']
        p['clause_evidence']=copy.deepcopy(e)
        # This is an explicit issuer expression in the very same pledge clause,
        # not a signature, company-name blank, or an adjacent manufacturer field.
        if p['issuer']=='unresolved' and not p['bidder_written'] and not p['uncertain_context'] and ISSUER.search(q):
            p['issuer']='manufacturer_or_support_provider'
            p['issuer_evidence']=copy.deepcopy(e)
        containing=[g for g in governors if g['doc_index']==e['doc_index'] and g['start']<e['start']<g['end']]
        if p['timing']=='unresolved' and not p['uncertain_context'] and containing:
            g=max(containing,key=lambda x:x['start'])
            p['timing']=g['stage'];p['required_party']=g['required_party']
            p['events'].append({'action':'submit','stage':g['stage'],'evidence':g['evidence'],
                                'member_evidence':copy.deepcopy(e),'binding':'enclosing_submission_list'})
            p['structural_links'].append({'kind':g['kind'],'governor':g['evidence'],'member':copy.deepcopy(e)})
            if g['stage']=='explicit_pre_bid':
                # Positive output needs one exact, bounded quote containing the
                # governing requirement and this list item, not an invented join.
                merged=ev(rec,e['doc_index'],g['evidence']['start'],e['end'])
                p['evidence']=merged
        for form in delivery_forms:
            if (p['timing']=='unresolved' and not p['uncertain_context'] and form['doc_index']==e['doc_index']
                    and form['start']<e['start']<form['end']):
                p['document_function']='pledge_presence_checked_in_delivery_confirmation'
                p['timing']='explicit_later_stage'
                p['events'].append({'action':'check_attachment_at_delivery_confirmation',
                    'stage':'explicit_later_stage','evidence':form['footer'],'member_evidence':copy.deepcopy(e)})
                p['structural_links'].append({'kind':form['function'],'form_title':form['title'],
                                             'form_footer':form['footer'],'member':copy.deepcopy(e)})
    # Link a named attached form to the particular list item that requests it.
    # A form's author is never inferred from "당사" or a company-name blank.
    for requester in pledges:
        refs=REF.findall(requester['clause_evidence']['quote'])
        for number in set(refs):
            targets=[f for f in forms if f['number']==number]
            if len(targets)!=1:continue
            form=targets[0]
            for p in pledges:
                e=p['clause_evidence']
                if (p is requester or e['doc_index']!=form['doc_index'] or not form['start']<=e['start']<form['end']):continue
                p['structural_links'].append({'kind':'explicit_attached_form_reference','reference_number':number,
                    'requester':requester['evidence'],'form_header':form['evidence']})
                if p['timing']=='unresolved' and requester['timing'] in ('explicit_pre_bid','explicit_later_stage') and not requester['uncertain_context']:
                    p['timing']=requester['timing']
                    p['events'].append({'action':'submit_referenced_form','stage':requester['timing'],
                                        'evidence':requester['evidence'],'reference':form['evidence']})
    facts['extraction']='document_list_form_event_binding_v1'
    return decide(rec,facts)
