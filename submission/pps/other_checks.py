"""Pure notice-local v19/v20/v22 facts and conservative tri-state decisions."""
from __future__ import annotations
from .legal_context import applicable_law
import re
from decimal import Decimal, InvalidOperation
from .assertions import unresolved_assertion, assertion_scope
from .amounts import WON, won_value
from .software_roles import work_review
from .software_disclosure import passages as disclosure_passages


def evidence(di,doc,left,right):
    return {'doc_index':di,'doc_type':doc['type'],'start':left,'end':right,'quote':doc['text'][left:right]}


def result(value,reason,facts,quote=''):
    return {'value':value,'reason':reason,'evidence':quote if value==1 else '', 'facts':facts}


def complete(rec):
    from .input_contract import provided_complete
    return provided_complete(rec)


def block(doc,start,end,pad=0):
    text=doc['text'];left=text.rfind('\n',0,start)+1;right=text.find('\n',end)
    if right<0:right=len(text)
    return max(0,left-pad),min(len(text),right+pad)


def legal_scope(rec):
    meta=rec.get('meta',{});law=applicable_law(rec)
    known=law in {'국가계약법','지방계약법'}
    return {'law':law if known else None,'known':known,'authority':meta.get('소관구분')}


def _pledge_check_basic(rec):
    pledges=[];irrelevant=[];certificates=[]
    # Scope to the document function. "확약서" by itself also covers security,
    # labor and bid-bond undertakings, which are different documents.
    target=re.compile(r'(?:물품\s*공급|정품\s*공급|공급(?!업체|자|사|물품)|기술\s*지원(?!사)|A\s*/\s*S|사후\s*관리|유지\s*보수)[^\n]{0,35}?(?:확\s*약\s*서|협약서)|(?:지원\s*\(A/S\)|무상지원\s*\(A/S\))\s*확약서')
    issuer=re.compile(r'제조사|제조회사|제조회|제조업체|원제조|공급사|기술지원사|대리점으로부터')
    early=re.compile(r'(?:전자\s*)?입찰(?:서)?\s*(?:제출)?\s*마감일?\s*전|입찰\s*전(?:일|까지)?|낙찰통보\s*(?:이전|전)|입찰\s*시(?:에)?\s*(?:제출|발급|보유)')
    late=re.compile(r'낙찰(?:자\s*결정)?\s*(?:후|이후)|계약\s*(?:체결\s*)?(?:시|전|후)|착수\s*전|납품\s*전')
    for di,doc in enumerate(rec.get('docs',[])):
        text=doc['text']
        for m in target.finditer(text):
            left,right=block(doc,m.start(),m.end());q=text[left:right]
            # Never borrow an issuer or deadline from an adjacent numbered
            # clause. Unresolved OCR wrapping is an abstention.
            preceding=text[max(0,left-900):left]
            who='manufacturer_or_support_provider' if issuer.search(q) else 'unresolved'
            self_written=bool(re.search(r'(?:입찰자|제안사|참가업체|입찰업체)(?:가|는|에서)?\s*(?:직접|자체)\s*작성|당사\s*명의로\s*작성',q))
            mixed_issuers=bool(self_written and issuer.search(q))
            if mixed_issuers:self_written=False;who='unresolved'
            if self_written:who='bidder'
            pre=bool(early.search(q));post=bool(late.search(q))
            capability=bool(re.search(r'제출(?:이)?\s*가능|제출할\s*수\s*있',q))
            possession=bool(re.search(r'보유|발급\s*(?:받|후)|발급받',q))
            negated=bool(re.search(r'(?:입찰\s*전|입찰\s*시)[^\n]{0,80}(?:요구하지\s*않|제출하지\s*않|제출할\s*필요\s*없|보유할\s*필요\s*없)|확약서[^\n]{0,20}제출\s*(?:면제|불요)',q))
            uncertain=mixed_issuers or bool(re.search(r'가정|예시|규정은\s*삭제|요구사항은\s*삭제|아닌\s*것은\s*아니',q))
            matches=list(target.finditer(q))
            bundle=re.sub(r'\s','',q[matches[0].start():matches[-1].end()]) if matches else ''
            # A line-item alone is not a proven bid-time requirement. Preserve
            # the nearest explicit proposal/qualification frame for review.
            frames=list(re.finditer(r'(?:제안서|입찰관련|입찰참가)\s*(?:제출|서류)|제출서류|착수\s*전\s*제출서류|선정된\s*업체',preceding))
            frame=frames[-1][0] if frames else None
            timing='explicit_pre_bid' if pre else 'explicit_later_stage' if post else 'capability_only' if capability else 'unresolved'
            pledges.append({'issuer':who,'timing':timing,'possession_required':possession,'submission_capability_only':capability,
                            'explicit_no_bid_time_requirement':negated,'bidder_written':self_written,'preceding_frame':frame,'uncertain_context':uncertain,'pledge_bundle':bundle,
                            'evidence':evidence(di,doc,left,right)})
        for m in re.finditer(r'[^\n]{0,130}(?:복사본\s*미보유|비밀유지|보안)[^\n]{0,100}확약서[^\n]{0,100}',text):
            irrelevant.append(evidence(di,doc,m.start(),m.end()))
        for m in re.finditer(r'[^\n]{0,60}(?:파트너십\s*인증|제조자증명서|판매대리점\s*계약서)[^\n]{0,110}',text):
            certificates.append(evidence(di,doc,m.start(),m.end()))
    # Deduplicate overlapping matches of supply and support in the same clause.
    dedup=[]
    for p in pledges:
        e=p['evidence']
        if not any(x['evidence']==e for x in dedup):dedup.append(p)
    pledges=dedup
    from .pledge_modality import apply as apply_action_modality
    for pledge in pledges:
        apply_action_modality(pledge)
        if (pledge['issuer']=='manufacturer_or_support_provider'
                and not issuer.search(pledge['source_subject_text'])):
            pledge['issuer']='unresolved'
    facts={'pledges':pledges,'other_document_functions':irrelevant,'certificate_facts':certificates,'complete':complete(rec),'scope':legal_scope(rec)}
    positive=[p for p in pledges if p['issuer']=='manufacturer_or_support_provider' and p['timing']=='explicit_pre_bid' and not p['explicit_no_bid_time_requirement'] and not p['submission_capability_only'] and not p['uncertain_context']]
    usable=[p for p in positive if 0<len(p['evidence']['quote'])<=500]
    if usable and facts['scope']['known']:return result(1,'explicit_third_party_pre_bid_pledge',facts,usable[0]['evidence']['quote'])
    if positive:return result(None,'positive_proof_scope_or_evidence_unresolved',facts)
    if not complete(rec):return result(None,'incomplete_documents_no_proven_positive',facts)
    # Negative overrides require every actual pledge candidate to be resolved.
    safe=[p for p in pledges if not p['uncertain_context'] and (p['bidder_written'] or p['explicit_no_bid_time_requirement'] or p['timing']=='explicit_later_stage')]
    def bound_later(p):
        return p['timing']=='capability_only' and len(p['pledge_bundle'])>=15 and any(
            x['timing']=='explicit_later_stage' and x['pledge_bundle']==p['pledge_bundle'] and x['issuer']==p['issuer'] for x in safe)
    if pledges and all(p in safe or bound_later(p) for p in pledges):
        return result(0,'all_pledges_explicitly_later_or_bidder_written',facts)
    if not pledges and irrelevant:return result(0,'only_unrelated_security_undertaking_recognized',facts)
    return result(None,'issuer_or_required_timing_unresolved' if pledges else 'no_proven_pledge_facts',facts)


def pledge_check(rec):
    from .pledge_reference import pledge_check as structured_check
    return structured_check(rec)


def decimal(value):
    if value is None or isinstance(value,bool):return None
    try:
        n=Decimal(str(value).replace(',','').strip())
        return n if n.is_finite() and n>0 else None
    except (InvalidOperation,ValueError):return None


def won(text):
    s=str(text).strip().removeprefix('금').strip()
    return won_value(s if '원' in s else s+'원')


def budget_facts(rec):
    amounts=[];durations=[];separated=[];maintenance=[];bundled=[]
    from .prices import project_prices
    shared = project_prices(rec)['budget']
    # The SW band needs an affirmative whole notice budget on an inclusive
    # tax basis. A second regex used to resurrect excluded/negated amounts,
    # miss explicit field aliases, and borrow VAT from an unrelated next duty.
    # All rejected observations remain in the receipt for review.
    for reading in shared['body']:
        if (reading['price_role']=='project_total_candidate' and reading['vat']=='included'
                and reading['evidence']['document_role']=='공고문'):
            ev=reading['evidence']
            amounts.append({'won':str(reading['won']),
                'evidence':evidence(ev['doc_index'],rec['docs'][ev['doc_index']],ev['start'],ev['end'])})
    for di,d in enumerate(rec.get('docs',[])):
        if d['type']!='공고문':continue
        t=d['text']
        for m in re.finditer(r'(?:사업기간|계약기간|용역기간)\s*[:：|][^\n]{0,80}?(\d+)\s*개월',t):durations.append({'months':int(m[1]),'evidence':evidence(di,d,m.start(),m.end())})
        for m in re.finditer(r'[^\n]{0,80}(?:장기계속계약|소프트웨어\s*(?:유지|보수))[^\n]{0,100}',t):maintenance.append(evidence(di,d,m.start(),m.end()))
        for m in re.finditer(r'[^\n]{0,100}(?:소프트웨어사업|SW사업)[^\n]{0,100}(?:분리|분담이행)[^\n]{0,100}',t):separated.append(evidence(di,d,m.start(),m.end()))
        for m in re.finditer(r'[^\n]*(?:소프트웨어사업|SW사업)[^\n]*(?:일괄\s*발주|통합\s*발주)[^\n]*',t):
            if re.search(r'둘\s*이상|2\s*개|복수|각\s*사업|여러',m[0]):bundled.append(evidence(di,d,m.start(),m.end()))
    vals={Decimal(a['won']) for a in amounts};meta=decimal(rec.get('meta',{}).get('배정예산금액'))
    metadata_conflict=bool(vals and meta is not None and any(v!=meta for v in vals))
    conflict=(len(vals)>1 or shared['status']=='conflict' or shared['unresolved_tax_basis'])
    value=(next(iter(vals)) if len(vals)==1 and not conflict and shared['status']=='known'
           and shared['effective_source']=='notice' and shared['value_won']==next(iter(vals)) else None)
    # Metadata-only budget retains an explicitly unverified tax basis.
    basis='explicit_VAT_inclusive_project_amount' if value is not None else 'unresolved_VAT_or_project_basis'
    effective=value;annualized=False
    joined=' '.join(e['quote'] for e in maintenance)
    long_maintenance=bool(re.search(r'장기계속계약',joined) and re.search(r'소프트웨어\s*(?:유지|보수)',joined))
    if bundled:effective=None;basis='lowest_bundled_SW_component_amount_unresolved'
    elif separated:effective=None;basis='separate_SW_component_amount_unresolved'
    elif long_maintenance:
        months={d['months'] for d in durations}
        if value is not None and len(months)==1 and next(iter(months))>=12:
            effective=value*12/next(iter(months));annualized=True
        else:effective=None;basis='long_maintenance_duration_unresolved'
    band=None if effective is None else 'below_20eok' if effective<2000000000 else '20_to_below_40eok' if effective<4000000000 else '40_to_below_80eok' if effective<8000000000 else 'at_least_80eok'
    return {'project_won':str(value) if value is not None else None,'effective_won':str(effective) if effective is not None else None,'metadata_budget_won':str(meta) if meta is not None else None,'basis':basis,'conflict':conflict,'metadata_conflict':metadata_conflict,'amount_evidence':amounts,'typed_budget_observations':shared['body'],'duration_evidence':durations,'maintenance_evidence':maintenance,'separated_evidence':separated,'bundled_evidence':bundled,'annualized':annualized,'band':band,'legal_floors_won':{'SME_to_midsize_within_five_years':2000000000,'large_revenue_below_800b':4000000000,'large_revenue_at_least_800b':8000000000}}


def _sw_work_candidates(q, doc_type, registered, service):
    patterns = []
    if doc_type == '공고문':
        if registered:
            patterns.append(('actual_service_qualification_and_SW_registration', r'(?P<work>정보시스템유지관리서비스)'))
        if not re.search(r'등록|확인서|담당|부서|처\s', q):
            patterns.append(('software_system_work_statement', r'(?:정보시스템|경영정보시스템)[^\n.。;；]{0,45}?(?P<work>구축|운영|유지보수)'))
        if registered:
            patterns.append(('software_license_procurement_with_SW_registration', r'라이선스\s*(?P<work>갱신|구매)'))
    if registered and service:
        patterns.append(('mandatory_software_installation_work', r'(?:소프트웨어|S/W|\bSW\b)[^\n.。;；]{0,80}?(?P<work>설치)[^\n.。;；]{0,50}(?:하여야|해야)'))
    for kind, pattern in patterns:
        for match in re.finditer(pattern, q, re.I):
            yield kind, match.start('work'), match.end('work')


def _hardware_refurbishment_ancillary_only(docs, actual):
    """Recognize installation furnished by the buyer as hardware servicing.

    A complete PC collection/refurbishment/distribution contract does not
    become a procured software task merely because the contractor installs the
    buyer's existing OS and applications.  Any separately detected software
    declaration, licence purchase, system work, or other software action keeps
    this guard off.
    """
    if not actual or any(a['kind'] != 'mandatory_software_installation_work' for a in actual):
        return False
    furnished = re.compile(
        r'(?:발주처|발주기관|수요기관)[\s\"\'“”‘’「」『』]*(?:가|에서)?\s*제공(?:하(?:는|여)|한)?'
        r'[^\n.。;；]{0,80}(?:O/S|S/W|\bSW\b|소프트웨어)'
        r'[^\n.。;；]{0,80}설치', re.I)
    if not all(furnished.search(a['evidence']['quote']) for a in actual):
        return False
    text = '\n'.join(d.get('text', '') for d in docs)
    hardware = bool(re.search(r'(?:불용|중고)\s*(?:PC|컴퓨터)', text, re.I))
    collected = bool(re.search(r'수집|회수', text))
    refurbished = bool(re.search(r'정비|재생|양품화', text))
    distributed = bool(re.search(r'보급|기증|배부', text))
    return hardware and collected and refurbished and distributed


def sw_check(rec):
    actual=[];incidental=[];disclosures=[];exceptions=[];registration=[];unresolved_disclosures=[];explicit_non_sw=[];rejected_work=[]
    docs=rec.get('docs',[])
    for di,d in enumerate(docs):
        t=d['text']
        for m in re.finditer(r'소프트웨어\s*사업자\s*\([^\n]{0,60}컴퓨터[^\n]{0,60}\)',t):registration.append(evidence(di,d,m.start(),m.end()))
    registered=bool(registration)
    for di,d in enumerate(docs):
        t=d['text']
        for m in re.finditer(r'[^\n]*(?:소프트웨어|S/W|\bSW\b|라이선스|정보시스템|정보보안|경영정보시스템)[^\n]*',t,re.I):
            q=m[0];proof=None
            generic=bool(re.search(r'경우|용역수행을\s*위한|계약상대자의\s*비용|하도급|심의위원회|평가점수|계좌|지식재산|비밀유지|교육(?:용|과정|교재)|연구\s*수행|실습|구축\s*방안|구축.{0,20}타당성|구축.{0,20}연구\s*용역',q))
            if (d['type']=='공고문' and re.search(r'본\s*(?:사업|과업)(?:은|는)\s*(?:SW|소프트웨어)\s*사업(?:이|에)?\s*(?:아닙니다|아니다|아님|해당하지\s*않)',q,re.I)
                    and not re.search(r'예시|가정|주장|단정|다만|하지만|것은\s*아니',q)):
                explicit_non_sw.append(evidence(di,d,m.start(),m.end()))
            declaration=re.search(r'본\s*사업은\s*(?:SW|소프트웨어)\s*사업',q,re.I)
            declaration_scope=assertion_scope(q,declaration.start(),declaration.end(),'software') if declaration else q
            explicit=bool(declaration) and not re.search(r'사업(?:이|에)?\s*(?:아니|아님|아닙|아닌|해당하지|해당되지)|가정|예시',declaration_scope)
            if explicit and not unresolved_assertion(declaration_scope):proof='explicit_SW_project_declaration'
            elif not generic:
                for kind, start, end in _sw_work_candidates(q, d['type'], registered, rec.get('meta',{}).get('업무구분')=='일반용역'):
                    review=work_review(t,m.start()+start,m.start()+end)
                    if review['issues']:
                        rejected_work.append({'kind':kind,'issues':review['issues'],
                            'action_evidence':evidence(di,d,m.start()+start,m.start()+end),
                            'evidence':evidence(di,d,review['start'],review['end'])})
                    else:
                        proof=kind
                        break
            if proof:actual.append({'kind':proof,'evidence':evidence(di,d,m.start(),m.end())})
            else:incidental.append({'reason':'scope_unresolved_or_incidental_reference','evidence':evidence(di,d,m.start(),m.end())})
        # A disclosure or exception can be in any supplied attachment. Its
        # document type alone must not turn observed wording into absence.
        if t:
            for start,end in disclosure_passages(t):
                q=t[start:end]
                floor_anchor=re.search(r'소프트웨어\s*진흥법|하한제도|사업금액의\s*하한',q)
                disclosure_scope=assertion_scope(q,floor_anchor.start(),floor_anchor.end(),'floor') if floor_anchor else q
                # A preceding disclaimer governs the quoted disclosure too.
                # Keep its source and abstain; it cannot certify normality.
                previous_end=max(0,start-1)
                previous_start=t.rfind('\n',0,previous_end)+1
                previous=t[previous_start:previous_end]
                if re.search(r'예시|작성\s*예|가정|경우(?:에)?만|경우에\s*한|적용하지|적용\s*제외',previous):
                    unresolved_disclosures.append(evidence(di,d,previous_start,end))
                    continue
                basis=bool(re.search(r'제\s*48\s*조|중소\s*소프트웨어사업자의\s*사업\s*참여\s*지원',q))
                applied=bool(re.search(r'사업금액별\s*참여\s*제한|중소\s*소프트웨어사업자.{0,120}만\s*입찰참가|대기업.{0,60}참여.{0,20}(?:제한|불가)|하한제도.{0,30}적용',q,re.S))
                exception=bool(re.search(r'(?:제\s*48\s*조.{0,30}제?\s*3\s*항|하한제도).{0,100}(?:예외|적용하지|적용\s*제외)',q,re.S))
                if exception:exceptions.append(evidence(di,d,start,end))
                elif unresolved_assertion(disclosure_scope) or re.search(r'제한하지|적용하지|제한\s*없|참여\s*가능|적용\s*여부[^\n]{0,20}미정|가정|예시|생략|불명|미기재',disclosure_scope):unresolved_disclosures.append(evidence(di,d,start,end))
                elif re.search(r'제\s*48\s*조\s*제?\s*4\s*항|상호출자제한',q) and not re.search(r'사업금액별|중소\s*소프트웨어사업자.{0,120}만\s*입찰참가|하한제도',q,re.S):unresolved_disclosures.append(evidence(di,d,start,end))
                elif basis and applied:disclosures.append(evidence(di,d,start,end))
    amount=budget_facts(rec)
    authority=rec.get('meta',{}).get('소관구분')
    public_scope=authority in {'국가기관','지방정부','공기업','준정부기관','기타공공기관','지방공기업'}
    ancillary_only=_hardware_refurbishment_ancillary_only(docs,actual)
    facts={'actual_work':actual,'rejected_work':rejected_work,'explicit_non_SW':explicit_non_sw,'hardware_refurbishment_ancillary_only':ancillary_only,'other_mentions':incidental,'registration':registration,'floor_disclosure':disclosures,'exception_disclosure':exceptions,'unresolved_disclosures':unresolved_disclosures,'budget':amount,'public_authority_supported':public_scope,'authority_meta':authority,'complete':complete(rec),'dropped_docs':rec.get('dropped_doc_counts',{})}
    if explicit_non_sw:
        if actual:return result(None,'conflicting_SW_scope_declarations',facts)
        if complete(rec):return result(0,'explicit_non_SW_scope_in_complete_source',facts)
    if ancillary_only and complete(rec):
        return result(0,'buyer_furnished_software_is_ancillary_to_complete_hardware_refurbishment',facts)
    if exceptions:return result(None,'floor_exception_claim_requires_applicability_review',facts)
    if unresolved_disclosures and not disclosures:return result(None,'participation_text_requires_scope_or_negation_review',facts)
    # Presence is narrow: this is a disclosure decision, not certification that
    # every possible bidder classification or other procurement rule is valid.
    if disclosures:
        if any(re.search(r'제한하지|적용하지|적용\s*여부[^\n]{0,20}미정', e['quote']) for e in unresolved_disclosures):
            return result(None,'contradictory_floor_application_clauses',facts)
        conflict=amount['conflict']
        value=decimal(amount['effective_won'])
        for e in disclosures:
            if re.search(r'20\s*억\s*(?:원\s*)?미만',e['quote']) and value is not None and value>=2000000000:conflict=True
        return result(None,'disclosure_amount_conflict',facts) if conflict else result(0,'floor_application_and_basis_explicitly_disclosed',facts)
    if not actual:return result(None,'actual_SW_procurement_not_proven',facts)
    if not public_scope:return result(None,'SW_authority_scope_unresolved',facts)
    if not complete(rec):return result(None,'missing_documents_prevent_absence_conclusion',facts)
    return result(1,'actual_public_SW_work_with_no_floor_disclosure_in_complete_inputs',facts)


def briefing_check(rec):
    events=[];meta=rec.get('meta',{});body_negotiated=[]
    anchor=re.compile(r'(?:현장|사업|과업|제안요청서?|입찰)\s*설명회|제안서\s*설명회')
    for di,d in enumerate(rec.get('docs',[])):
        t=d['text']
        if d['type']=='공고문':
            for m in re.finditer(r'협상에\s*의한\s*계약',t):body_negotiated.append(evidence(di,d,m.start(),m.end()))
        for m in anchor.finditer(t):
            left,right=block(d,m.start(),m.end());q=t[left:right]
            before=t[max(0,left-750):left]
            heading_matches=list(re.finditer(r'(?:\d+[.)]\s*)?(?:입찰참가자격|참가자격|제안서\s*평가|제안서\s*발표|제안서\s*설명회\s*및\s*평가)',before))
            heading=heading_matches[-1][0] if heading_matches else None
            evaluation=bool(re.search(r'제안서\s*설명회|평가위원|제안서\s*평가|프레젠테이션',q))
            # ``제안요청서 설명: 사업설명회로 갈음`` announces that the
            # briefing will be used; it does not cancel the briefing.  A
            # ``갈음`` negative needs an explicit replacement object after
            # the briefing subject.
            no_event=bool(re.search(
                r'설명회[^\n]{0,40}(?:생략|미개최|개최하지)|'
                r'설명회\s*(?:는|를|은|[:：])?[^\n]{0,24}'
                r'(?:제안요청서|과업지시서|공고서|첨부\s*자료)(?:로|으로)\s*갈음',q))
            independent=bool(re.search(r'참석\s*여부[^\n]{0,30}(?:상관없|상관없이|관계없)|불참[^\n]{0,25}불이익\s*없|참석하지\s*않아도[^\n]{0,30}(?:가능|참가)|(?:불참|미참석)[^\n]{0,45}(?:제외하지\s*않|참가를\s*제한하지\s*않)',q))
            restrict=bool(re.search(
                r'참석(?:한)?\s*(?:업체|자)[^\n]{0,35}(?:한하|한하여)[^\n]{0,45}(?:제안서|입찰|자격)|'
                r'(?:미참석|불참)[^\n]{0,45}(?:제안서[^\n]{0,25}접수하지\s*않|대상에서\s*제외|참가\s*불가)|'
                r'참석하지\s*(?:아니한|않은)[^\n]{0,35}업체[^\n]{0,35}'
                r'(?:입찰\s*)?참가[^\n]{0,20}허용되지\s*않',q))
            in_qualification=bool(heading and '참가자격' in heading)
            if in_qualification and re.search(r'설명회에\s*참석한\s*자',q):restrict=True
            unclear=bool(re.search(r'않는\s*것은\s*아니|예시|가정|(?:규정|조건|요건|요구사항)[^\n]{0,20}(?:삭제|철회)' ,q))
            later_event=bool(re.search(r'계약\s*(?:후|이후)|최종\s*보고|성과\s*보고|선정된\s*업체',q))
            if unclear:restrict=False
            events.append({'event_type':'evaluation_or_presentation' if evaluation else 'post_award_event' if later_event else 'prior_briefing','restricts_eligibility':restrict,'attendance_independent':independent and not unclear,'not_held':no_event and not unclear,'qualification_heading':heading,'date_unresolved':not bool(re.search(r'20\d{2}[.년/-]',q)),'evidence':evidence(di,d,left,right)})
    mm=meta.get('낙찰방법');negotiated=bool(body_negotiated) or mm=='협상에의한계약'
    conflict=bool(body_negotiated and mm not in {None,'미입력','협상에의한계약'})
    facts={'events':events,'body_negotiated':body_negotiated,'meta_award_method':mm,'procedure_conflict':conflict,'scope':legal_scope(rec),'complete':complete(rec)}
    if conflict or not facts['scope']['known']:return result(None,'law_or_procedure_conflict',facts)
    if not negotiated:return result(None,'negotiated_contract_not_proven',facts)
    prior=[e for e in events if e['event_type']=='prior_briefing']
    positive=[e for e in prior if e['restricts_eligibility'] and not e['attendance_independent'] and not e['not_held']]
    if positive and any(e['attendance_independent'] or e['not_held'] for e in prior):return result(None,'conflicting_briefing_conditions',facts)
    usable=[e for e in positive if 0<len(e['evidence']['quote'])<=500]
    if usable:return result(1,'prior_briefing_attendance_required_for_eligibility',facts,usable[0]['evidence']['quote'])
    if positive:return result(None,'attendance_evidence_span_unresolved',facts)
    if prior and complete(rec) and all(e['attendance_independent'] or e['not_held'] for e in prior):return result(0,'briefing_explicitly_optional_or_not_held',facts)
    return result(None,'no_proven_attendance_restriction',facts)


def predict(rec, items=(19, 20, 22)):
    return {f'v{k}': check(rec) for k, check in
            ((19, pledge_check), (20, sw_check), (22, briefing_check)) if k in items}


def overlay(rec,row,allow_negatives=True):
    result=dict(row)
    for k,d in predict(rec).items():
        if d['value'] is None or d['value']==0 and not allow_negatives:continue
        result[k]=str(d['value']);result['e'+k[1:]]=d['evidence'] if d['value']==1 and k!='v20' else ''
    return result
