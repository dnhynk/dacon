"""Conservative per-record SME facts; no IDs, labels, learned rules or I/O.

Pass a preloaded ProductFacts catalog helper. Original text offsets are kept.
Catalog candidate retrieval is reused, but weak candidates never set scope.
"""
from __future__ import annotations
import re
from .products import normalized_map, ProductFacts

ITEMS=tuple(range(10,19))
FLOOR=100_000_000
NOTICE=230_000_000
CODE=re.compile(r'(?<!\d)\d{10}(?!\d)')
CLASS=r'(?:중소기업|중[·ㆍᆞ․‧・∙･.,-]소기업|중기업|소기업|소상공인)'
SEP=r'(?:[·ㆍᆞ․‧・∙･.,\/()\-]|또는|및|혹은|와|과)*'
CERT=re.compile(CLASS+r'(?:자)?(?:'+SEP+CLASS+r'(?:자)?)*'+r'[)]?(?:확인서|확인증)')
SIZE_SIGNAL=re.compile(CLASS)
DIRECT=re.compile(r'직접생산(?:확인)?(?:증명|확인)?서|직접생산확인기준|직접생산하는')
ELIG=re.compile(r'참가자(?:의)?자격|참가자격|참여자격|입찰자격|응모자격|참가조건|제안자격')
END=re.compile(r'소지한|보유한|갖춘|소지하여|보유하여|소지해야|보유해야|소지한자|업체이어야|업체여야|자이어야|참가할수|참가가능')


def norm(s):return normalized_map(s)[0]


def evidence(record,di,a,b):
    d=record['docs'][di]
    return {'doc_index':di,'doc_id':d.get('doc_id'),'document_role':d.get('type'),
            'start':a,'end':b,'text':d['text'][a:b]}


def mask_laws(n):
    # Same-length masking preserves positions in normalized strings.
    def mask(m):
        return ' '*len(m.group()) if re.search(r'법|규정|규칙|기준|지침|요령',m.group()) else m.group()
    n=re.sub(r'[「｢『][^」｣』]{1,180}[」｣』]',mask,n)
    for title in ['중소기업범위및확인에관한규정','중소기업공공구매종합정보망','중소기업제품공공구매종합정보망','중소기업기본법','소상공인기본법']:
        n=n.replace(title,' '*len(title))
    return n


def heading(n):
    if len(n)<100 and ELIG.search(n) and not re.search(r'규정|법률|시행령|제\d+조|갖춘|등록한|문의',n):return 'eligibility'
    if len(n)<100 and re.search(r'제출서류|구비서류|제출목록|제안서작성|서식\d|붙임\d',n):return 'forms'
    if len(n)<90 and re.search(r'배점|평가기준|평가항목|평가방법|정량평가',n) and not re.search(r'각\d+부|자료.{0,15}\d+부',n):return 'scoring'
    if len(n)<85 and re.match(r'^(?:\d+(?:[-.]\d+)*[.)]|[ⅠⅡⅢⅣⅤⅥ]+[.)]?)',n) and not END.search(n):return 'other'
    return None


def class_set(s):
    s=norm(s)
    if re.search(r'중소기업|중[·ㆍᆞ․‧・∙･.,-]소기업',s):return {'medium','small','micro'}
    allowed=set()
    if '중기업' in s:allowed.add('medium')
    if '소기업' in s:allowed.update(('small','micro'))
    if '소상공인' in s:allowed.add('micro')
    return allowed


def size_facts(n):
    original=n
    n=mask_laws(n)
    certificates=list(CERT.finditer(n))
    # The final actual certificate specification can narrow a broad preamble.
    if certificates:
        sets=[class_set(m.group()) for m in certificates]
        union=bool(re.search(r'중하나|어느하나|확인서[,·ㆍ]*(?:또는|혹은)',n))
        # Parenthetical broad certificate aliases are not a second condition.
        primary=[(m,s) for m,s in zip(certificates,sets) if not (m.start()>0 and n[m.start()-1]=='(')]
        sets=[s for m,s in primary] or sets
        allowed=set().union(*sets) if union else set.intersection(*sets)
        # Both statutory entity wording and certificate scope constrain the
        # same applicant. A broad form name cannot relax an explicit small-
        # entity gate, nor can a broad preamble relax a narrow certificate.
        preamble_allowed=None
        if re.match(r'^(?:[가-하][.)]|[①-⑳]|\d+[-.)]|[「｢『])',original):
            prefix=n[:certificates[0].start()]
            if re.search(r'로서|으로서|에따른|에해당',prefix):
                preamble_sets=[class_set(m.group()) for m in SIZE_SIGNAL.finditer(prefix)]
                if preamble_sets:
                    preamble_allowed=set().union(*preamble_sets)
                    allowed &= preamble_allowed
        return {'allowed':sorted(allowed),'basis':'certificate','connective':'OR' if union else 'AND_or_single',
                'certificate_phrases':[m.group() for m in certificates],
                'eligible_entity_preamble':sorted(preamble_allowed) if preamble_allowed is not None else None,
                'commercial_only':True}
    # Bare legal/statutory wording is not a size restriction without a noun
    # phrase identifying the eligible business and an operative predicate.
    m=re.search('('+CLASS+r'(?:자)?)(?:로서|으로서|에해당|인업체|인자|간제한경쟁|만참가)',n)
    if m:return {'allowed':sorted(class_set(m[1])),'basis':'eligible_entity','connective':'single','certificate_phrases':[],'commercial_only':True}
    return None


def extract_inventory(record, *, heading_fn=None):
    recognize = heading if heading_fn is None else heading_fn
    inventory=[];sections=[];quotes=[];exceptions=[];declarations=[]
    for di,d in enumerate(record['docs']):
        t=d['text'];ls=list(re.finditer(r'[^\r\n]+',t));role='unknown';head=None;section_start=None
        for li,m in enumerate(ls):
            raw=m.group();n=norm(raw);new=recognize(n)
            if new:
                if section_start is not None:
                    sections.append({'evidence':evidence(record,di,section_start,m.start()),'closed':True});section_start=None
                role=new;head=evidence(record,di,m.start(),m.end())
                if new=='eligibility':section_start=m.start()
            ev=evidence(record,di,m.start(),m.end())
            if len(n)<250 and re.search(r'소액수의|수의계약.{0,15}(?:견적|안내)|견적제출안내공고|견적서제출안내공고',n) and not re.search(r'경우|법률|시행령|준용',n):quotes.append(ev)
            if re.search(r'제2조의3|우선조달.{0,15}(?:예외|제외|적용하지)|비영리.{0,40}(?:참가|참여)',n):
                denied=bool(re.search(r'제2조의3.{0,25}해당되지않|비영리.{0,40}참가불가',n))
                kind='priority_exception_denied' if denied else 'nonprofit_alternative' if '비영리' in n and re.search(r'참가|참여',n) else 'priority_exception_reference'
                exceptions.append({'kind':kind,'evidence':ev,'role':role})
            if re.search(r'제7조의2|공동사업|자격.{0,35}3인이하|소기업.{0,45}유찰',n):
                exceptions.append({'kind':'small_enterprise_special_case_reference','evidence':ev,'role':role})
            if CODE.search(n) and re.search(r'세부품명|품명번호|품목번호',n):
                purchase=bool(re.search(r'본입찰대상물품|본사업대상물품|구매대상물품',n))
                registration=bool(re.search(r'등록한|등록된|등록하여|등록을필|등록되어',n))
                direct=bool(DIRECT.search(n))
                if purchase or (registration and not direct) or (re.search(r'품명[:：|]',n) and role not in ('eligibility','forms') and not direct):
                    declarations.append({'codes':CODE.findall(n),'role':'explicit_purchase' if purchase else 'purchase_registration' if registration else 'purchase_field',
                                         'evidence':ev})
            signal=bool('직접생산' in n or SIZE_SIGNAL.search(n))
            if not signal:continue
            end=m.end()
            # Join immediately following wrapped wording only; headings stop it.
            if (DIRECT.search(n) or SIZE_SIGNAL.search(n)) and not END.search(n) and len(n)<400:
                for nx in ls[li+1:li+5]:
                    nn=norm(nx.group())
                    if recognize(nn) or re.match(r'^[가-하][.)]|^[①-⑳]',nn):break
                    if nx.end()-m.start()>900:break
                    end=nx.end();n=norm(t[m.start():end])
                    if END.search(n):break
            ev=evidence(record,di,m.start(),end);masked=mask_laws(n)
            direct='직접생산' in n;sz=size_facts(n)
            direct_required=bool(re.search(r'직접생산.{0,240}(?:소지한|보유한|소지하여|보유하여|업체이어야)',masked) or
                                 re.search(r'직접생산확인기준.{0,150}세부품명.{0,100}소지한',n))
            is_certificate=bool(re.search(r'확인서|확인증|직접생산',n))
            operative=role=='eligibility' and bool(END.search(n))
            note=bool(re.match(r'^(?:※|다만|단[,.:]|[-✓])',n))
            conditional=bool(re.search(r'특별법인|중소기업으로간주|중소기업자로간주|협동조합|초기중견|중견기업',n))
            permission=bool(re.search(r'(?:확인서|직접생산).{0,60}(?:없어도|불필요|요구하지|제한하지|면제|무관)',n))
            withdrawn=bool(re.search(r'(?:규정|조건|요건|요구사항).{0,20}(?:삭제|철회)',n))
            conditional |= bool(re.search(r'분담.{0,50}(?:구성원|업체)|(?:구성원|업체).{0,50}분담',n))
            if withdrawn:status='incidental_or_unresolved'
            elif permission:status='explicit_permission'
            elif conditional:status='special_entity_branch'
            elif role=='forms':status='submission_or_form'
            elif role=='scoring':status='scoring'
            elif operative and not note:status='mandatory_eligibility'
            elif operative and note and not re.search(r'경우|신청|유효|발급된',n):status='mandatory_eligibility'
            elif note and is_certificate:status='verification_or_exception_note'
            else:status='incidental_or_unresolved'
            inventory.append({'status':status,'section_role':role,'heading':head,'evidence':ev,
                'direct_production':direct,'direct_requirement':direct_required,'size':sz,'codes':CODE.findall(n),
                'other_entity_options':re.findall(r'비영리법인|벤처기업|창업기업|특별법인|협동조합|중견기업',n),
                'alternative_size_branch_unresolved':bool(re.search(r'(?:또는|혹은)(?:벤처기업|창업기업)|(?:벤처기업|창업기업).{0,30}(?:중하나|어느하나|또는|혹은)',n)),
                'validity':{'required_valid_period':bool(re.search(r'유효기간(?:내|이내)|유효한',n)),
                            'pre_bid_issue_wording':bool(re.search(r'마감.{0,12}전일까지.{0,12}(?:발급|신청)',n)),
                            'application_grace_wording':bool(re.search(r'신청한.{0,12}(?:업체|사항)|5일이내',n)),
                            'actual_bidder_certificate':'not_supplied_not_verified'},
                'nonprofit_alternative':bool(re.search(r'비영리.{0,35}(?:법인|참가|참여)',n))})
        if section_start is not None:sections.append({'evidence':evidence(record,di,section_start,len(t)),'closed':False})
    # Wrapped candidates can overlap; retain the earliest complete span.
    result=[]
    for x in inventory:
        e=x['evidence']
        if any(y['evidence']['doc_index']==e['doc_index'] and y['evidence']['start']<=e['start'] and e['end']<=y['evidence']['end'] and y['status']==x['status'] for y in result):continue
        result.append(x)
    return result,sections,quotes,exceptions,declarations


def product_scope(record,pf,product,inventory,declarations,price):
    meta={x['code'] for x in product['meta_purchase_codes']}
    declared={code for d in declarations for code in d['codes']}
    supported=[]
    for d in declarations:
        for c in d['codes']:
            if d['role']=='explicit_purchase' or (meta and c in meta) or d['role']=='purchase_field':
                supported.append({'code':c,'evidence':d['evidence'],'identity_support':d['role']})
    # Exact catalog parent identity corroborates an actual certificate target;
    # never use arbitrary bigram rank as identity. Short parents need an exact
    # field/title occurrence, and all supplied notes remain binding.
    scopes=[product['sources'][s] for s in product['purchase_scope_sources']]
    mandatory=[x for x in inventory if x['status']=='mandatory_eligibility' and x['direct_requirement']]
    for x in mandatory:
        for c in x['codes']:
            row=pf.products.get(c)
            if not row:continue
            parent=norm(row['제품명']);detail=norm(row['세부품명'])
            for s in scopes:
                n=norm(s['text'])
                exact_parent_task=bool(len(parent)>=2 and re.search(re.escape(parent)+r'[』」〉>”"‘’:]*(?:행사)?(?:기획|대행)(?:용역|서비스)?',n))
                kind_agrees=(record.get('meta',{}).get('업무구분')=='일반용역' and row['대분류'].endswith('서비스'))
                if (len(detail)>=5 and detail in n) or (kind_agrees and exact_parent_task):
                    supported.append({'code':c,'evidence':s,'certificate_evidence':x['evidence'],
                                      'identity_support':'exact_catalog_name_or_parent_in_purchase_scope'})
                    break
    codes=sorted({s['code'] for s in supported})
    rows=[{'code':c,'listed':c in pf.products,
           'name':pf.products[c]['세부품명'] if c in pf.products else None,
           'note':pf.products[c]['특이사항'] if c in pf.products else None,
           'condition':ProductFacts.condition(pf.products[c]['특이사항'],price) if c in pf.products else {'status':'unlisted'}} for c in codes]
    status='unknown'
    if rows:
        states=[r['condition']['status'] for r in rows]
        if all(s in ('met','no_stated_condition') for s in states):status='competition'
        elif all(s=='not_met' for s in states):status='general_in_supplied_catalog'
        # Unlisted codes are retained as lookup facts, never closed-world
        # proof that the real purchased product is general. Names, aliases,
        # mixed lots, or a code-registration error can remain unresolved.
    conflicts=[]
    if meta and declared and not meta.issubset(declared):conflicts.append('metadata_purchase_codes_not_all_confirmed_by_body')
    if any(c not in meta for c in declared) and meta:conflicts.append('additional_body_purchase_codes')
    # Do not conclude a whole mixed contract is general or competition from a
    # subset of explicit metadata targets.
    if meta and not meta.issubset(set(codes)):status='unknown'
    if conflicts:status='unknown'
    return {'status':status,'supported_products':rows,'identity_evidence':supported,
            'declared_body_products':declarations,'meta_codes':sorted(meta),'uncertainty':conflicts,
            'weak_lexical_candidates_are_not_identity':True}


def extract_sme_facts(record,pf):
    product=pf.extract(record,top_k=3)
    inventory,sections,quotes,exceptions,declarations=extract_inventory(record)
    p=product['price'];price=None if p['meta_body_conflict'] else p['value_krw']
    scope=product_scope(record,pf,product,inventory,declarations,price)
    active=[x for x in inventory if x['status']=='mandatory_eligibility']
    sizes=[x for x in active if x['size']]
    direct=[x for x in active if x['direct_requirement']]
    # Distinct mandatory commercial clauses combine by AND, while OR inside
    # one certificate clause is retained by size_facts.
    allowed=set.intersection(*(set(x['size']['allowed']) for x in sizes)) if sizes else None
    unresolved_size_branch=any(x['alternative_size_branch_unresolved'] for x in active)
    for x in sizes:
        e=x['evidence']
        context=norm(record['docs'][e['doc_index']]['text'][max(0,e['start']-700):e['start']])
        if re.search(r'(?:다음|아래|각호).{0,30}(?:어느하나|중하나)',context):
            unresolved_size_branch=True  # Cross-clause alternatives need a scoped parse.
    if unresolved_size_branch:allowed=None
    complete=(record.get('input_completeness',{}).get('완전관측') is True
              and not any(record.get('dropped_doc_counts',{}).values()))
    recovered=any(s['closed'] and s['evidence']['document_role']=='공고문' for s in sections)
    # Absence needs full-record scan, completed input, a closed eligibility
    # section and no unresolved lexical candidate for the relevant obligation.
    direct_ambiguous=[x for x in inventory if x['direct_production'] and x['status'] not in ('scoring','incidental_or_unresolved')]
    size_ambiguous=[x for x in inventory if x['size'] and x['status'] not in ('scoring',)]
    no_direct=complete and recovered and not any(x['direct_production'] for x in inventory)
    raw_size_uncertain=[x for x in inventory if SIZE_SIGNAL.search(mask_laws(norm(x['evidence']['text']))) and x['status'] not in ('scoring',)]
    no_size=complete and recovered and not raw_size_uncertain and not sizes
    commercial_exceptions=[x for x in exceptions if x['role']=='eligibility' and x['kind']!='priority_exception_denied']
    meta_exception=record.get('meta',{}).get('조항호내용')
    meta_exception_relevant=bool(re.search(r'제2조의3|비영리|우선조달.{0,10}예외',str(meta_exception)))
    exception_uncertain=bool(commercial_exceptions or meta_exception_relevant)
    meta_small_special=bool(re.search(r'제7조의2|공동사업|3인이하|유찰',str(meta_exception)))
    quote_uncertain=bool(quotes) or record.get('meta',{}).get('계약방법')=='수의계약'
    law=record.get('meta',{}).get('적용계약법')
    ordinary=law in ('국가계약법','지방계약법') and record.get('meta',{}).get('업무구분') in ('일반용역','물품(내자)')
    decisions={f'v{i}':{'value':None,'reason':'insufficient_semantic_proof','evidence':[]} for i in ITEMS}
    def put(i,value,reason,evs=()):decisions[f'v{i}']={'value':value,'reason':reason,'evidence':list(evs)}
    if ordinary:
        # Necessary price predicates yield negatives independently of product
        # identity. These are not inferred from empty snippets.
        if price is not None:
            if price<NOTICE:put(14,0,'outside_v14_price_band')
            if not FLOOR<=price<NOTICE:
                put(15,0,'outside_v15_price_band');put(16,0,'outside_v16_price_band')
            if price>=FLOOR:
                put(17,0,'outside_v17_price_band');put(18,0,'outside_v18_price_band')
        es=[x['evidence'] for x in sizes]
        if allowed:
            put(11,0,'operative_size_qualification_present',es)
            put(16,0,'operative_size_qualification_present',es)
            put(18,0,'operative_size_qualification_present_not_absence',es)
            if 'medium' in allowed:put(13,0,'medium_enterprise_explicitly_permitted',es);put(15,0,'medium_enterprise_explicitly_permitted',es)
            else:put(17,0,'small_or_micro_only_not_broad_sme_restriction',es)
        known=scope['status'];identity=[s['evidence'] for s in scope['identity_evidence']]
        targets={r['code'] for r in scope['supported_products']}
        direct_codes={c for x in direct for c in x['codes']}
        all_declared_supported=(not scope['uncertainty'] and set(scope['meta_codes']).issubset(targets))
        if targets and all_declared_supported and targets.issubset(direct_codes):put(10,0,'all_supported_purchase_targets_have_operative_direct_requirement',[x['evidence'] for x in direct])
        if known=='general_in_supplied_catalog':
            for i in (10,11,13):put(i,0,'supported_purchase_outside_supplied_competition_catalog',identity)
            if direct:put(12,1,'general_purchase_with_mandatory_direct_production',identity+[x['evidence'] for x in direct])
            if price is not None:
                if price>=NOTICE and allowed:put(14,1,'general_above_notice_has_commercial_sme_restriction',es+identity)
                if FLOOR<=price<NOTICE and allowed and 'medium' not in allowed and not quote_uncertain and not exception_uncertain and not meta_small_special:put(15,1,'general_middle_band_excludes_medium_enterprise',es+identity)
                if price<FLOOR and allowed and 'medium' in allowed and not quote_uncertain and not exception_uncertain and not meta_small_special:put(17,1,'general_low_band_permits_medium_enterprise',es+identity)
                if not quote_uncertain and not exception_uncertain and not meta_small_special and no_size:
                    if FLOOR<=price<NOTICE:put(16,1,'full_observed_record_no_size_requirement',identity)
                    if price<FLOOR:put(18,1,'full_observed_record_no_size_requirement',identity)
        elif known=='competition':
            for i in (12,14,15,16,17,18):put(i,0,'supported_purchase_in_competition_catalog',identity)
            if not quote_uncertain and not exception_uncertain:
                if no_direct:put(10,1,'full_observed_record_no_direct_requirement',identity)
                if no_size:put(11,1,'full_observed_record_no_size_requirement',identity)
                if allowed and 'medium' not in allowed:
                    # The provided competition table does not establish the
                    # separate Article 7-2 small-enterprise designation list.
                    put(13,None,'small_only_competition_requires_article7_2_designation_check',es+identity)
        quote_small=bool(quotes) and record.get('meta',{}).get('계약방법')=='수의계약' and price is not None and 20_000_000<price<=100_000_000 and allowed and 'medium' not in allowed
        if quote_small:put(13,0,'actual_small_quote_with_statutory_small_enterprise_band',[*quotes,*es])
    return {'version':'sme_logic_v1','product':scope,'product_candidates':product,
            'price':{'effective_won':price,**p},'inventory':inventory,'eligibility_sections':sections,
            'enterprise_size':{'allowed_commercial':sorted(allowed) if allowed else None,'active_clauses':len(sizes),
                               'unresolved_alternative_branch':unresolved_size_branch,
                               'special_entities_are_separate':True},
            'direct_production':{'active_clauses':len(direct),'supported_target_codes':sorted(direct_codes) if ordinary else []},
            'absence_proof':{'full_input_scanned':True,'complete':complete,'closed_notice_eligibility_found':recovered,
                             'no_direct_requirement':no_direct,'no_size_requirement':no_size,
                             'unresolved_direct_candidates':len(direct_ambiguous),'size_candidates':len(size_ambiguous),
                             'dropped_doc_counts':record.get('dropped_doc_counts'), 'input_completeness':record.get('input_completeness')},
            'exceptions':{'body':exceptions,'actual_quote_evidence':quotes,'meta_reason':meta_exception,
                          'meta_reason_relevant':meta_exception_relevant,'priority_exception_requires_review':exception_uncertain,
                          'meta_small_enterprise_special_case':meta_small_special,'quote_or_quote_metadata':quote_uncertain,
                          'article7_2_designation_status':'not_established_from_competition_catalog'},
            'decisions':decisions}


def compact_prompt(facts):
    """Prompt adapter. Audit JSON contains the complete full-record inventory."""
    lines=['SME FACTS: None means unresolved, not compliant.']
    lines.append('PRODUCT '+facts['product']['status']+'; unlisted codes and lexical candidates do not prove general status')
    for p in facts['product']['supported_products']:lines.append(str(p))
    lines.append('ESTIMATED_PRICE '+str(facts['price']['effective_won'])+'; meta/body conflict='+str(facts['price']['meta_body_conflict']))
    for x in facts['product']['identity_evidence']:
        e=x['evidence'];lines.append(f"PURCHASE [D{e['doc_index']} {e['start']}:{e['end']}] {e['text']}")
    lines.append('COMMERCIAL_SIZE '+str(facts['enterprise_size']))
    seen=set()
    candidates=[x for x in facts['inventory'] if x['status'] in ('mandatory_eligibility','explicit_permission') and (x['size'] or x['direct_production'])]
    for x in candidates:
        e=x['evidence'];key=(e['doc_index'],e['start'],e['end'])
        if key in seen:continue
        seen.add(key)
        if x['heading']:lines.append('HEADING '+x['heading']['text'])
        lines.append(f"[{e['document_role']} D{e['doc_index']} {e['start']}:{e['end']}] {e['text']}")
        lines.append(f"role={x['status']}; size={x['size']}; direct={x['direct_production']}; validity={x['validity']}")
    for x in facts['exceptions']['body']:
        e=x['evidence'];lines.append(f"EXCEPTION {x['kind']} [D{e['doc_index']} {e['start']}:{e['end']}] {e['text']}")
    lines.append('META_EXCEPTION '+str(facts['exceptions']['meta_reason']))
    for e in facts['exceptions']['actual_quote_evidence']:
        lines.append(f"QUOTE [D{e['doc_index']} {e['start']}:{e['end']}] {e['text']}")
    lines.append('ABSENCE '+str(facts['absence_proof']))
    lines.append('DECISIONS '+str({k:(d['value'],d['reason']) for k,d in facts['decisions'].items()}))
    return '\n'.join(lines)
