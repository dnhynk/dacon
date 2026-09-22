"""Per-notice CPU prototype. No IDs, labels, filesystem or model access.

Rules use supplied item definitions and law snapshot only. None = abstain.
v24 exposes flag contradictions for audit; its conservative overlay uses only
explicit value-to-value mismatches. A matched field never proves all of v24=0.
"""
from __future__ import annotations
import datetime as dt
from .legal_context import applicable_law
import re
from decimal import Decimal, InvalidOperation
from .amounts import WON as MONEY, won_value


def compact(s):
    return re.sub(r'\s+', '', str(s))


def known(v):
    return v is not None and str(v).strip() not in {'', '미입력', 'null', 'None'}


def positive_decimal(v):
    if not known(v) or isinstance(v,bool):return None
    try:
        n=Decimal(str(v).replace(',',''))
        return n if n.is_finite() and n>0 else None
    except InvalidOperation:return None


def sp(word):
    return r'\s*'.join(map(re.escape, word))


def ev(text, start, end):
    """A contiguous source quote, preserving exact whitespace and characters."""
    s = text[max(0, start):min(len(text), end)].strip()
    return s[:500] if s and s[0] not in '=+@' else ''


def fact(di, text, start, end, kind, value, **extra):
    return dict(kind=kind, value=value, doc_index=di, start=start, end=end,
                evidence=ev(text, start, end), **extra)


def result(item, value, reason, facts=(), evidence=''):
    return dict(item=item, value=value, reason=reason, evidence=evidence if value == 1 else '', facts=list(facts))


DATE = re.compile(r'(?<!\d)(?P<y>20\d{2})\s*[.년/-]\s*(?P<m>1[0-2]|0?[1-9])\s*[.월/-]\s*(?P<d>3[01]|[12]\d|0?[1-9])\s*[.일]?(?!\d)')
PART_DATE = re.compile(r'(?<![\d.])(?P<m>1[0-2]|0?[1-9])\s*[.월/-]\s*(?P<d>3[01]|[12]\d|0?[1-9])\s*[.일]?(?!\d)')
BRIEF = re.compile(r'제안\s*요청\s*서?\s*설명(?:회)?|사업\s*설명(?:회)?|과업\s*설명(?:회)?|현장\s*설명(?:회)?')
NO_BRIEF = re.compile(r'생략|없음|미개최|개최\s*하지|실시\s*하지|진행\s*하지|(?:요청서|과업지시서|문서|서면)[^\n]{0,30}갈음')
DEADLINE = re.compile(r'(?:기술\s*)?제안서(?:\s*및\s*(?:가격\s*입찰서|가격\s*제안서))?\s*(?:등\s*)?(?:제출|접수)|입찰참가\s*등록[^\n]{0,30}제안서\s*접수|접수\s*마감')
SCHEDULE = re.compile(r'입찰|제안|등록|접수|마감|공고|설명|평가|발표|제출|개찰')


def money_value(m):
    return won_value(m.group())


def dates(text):
    out=[]
    for m in DATE.finditer(text):
        try:v=dt.date(int(m['y']),int(m['m']),int(m['d']))
        except ValueError:continue
        out.append((m.start(),m.end(),v))
    # An omitted year is accepted only as the second endpoint of a local range.
    for a,b,v in list(out):
        tail=text[b:b+55]
        m=re.search(r'(?:~|∼|～|부터|–|—)\s*'+PART_DATE.pattern,tail)
        if m:
            try:w=dt.date(v.year,int(m['m']),int(m['d']))
            except ValueError:continue
            if w>=v:out.append((b+m.start(),b+m.end(),w))
    return sorted(set(out))


def field_window(text, start, anchor_end, width=200):
    """Stop on a following lettered/numbered heading, not arbitrary paragraphs."""
    end=min(len(text),anchor_end+width)
    tail=text[anchor_end:end]
    for m in re.finditer(r'\n\s*(?:[가-하]|\d{1,2})[.)]\s*([^\n]+)',tail):
        if re.match(r'일\s*시|접수\s*기간|제출\s*기간|기\s*간',m[1]):continue
        end=anchor_end+m.start();break
    return text[start:end],end


def extract_amounts(rec):
    found=[]
    labels=re.compile('|'.join(sp(x) for x in ['배정예산금액','사업예산','사업금액','소요예산','예산금액','예산액','기초금액','추정가격']))
    for di,d in enumerate(rec['docs']):
        if d['type']!='공고문':continue
        t=d['text']
        for a in labels.finditer(t):
            lead=t[max(0,a.start()-32):a.start()]
            if re.search(r'연차|연도|차년도|[1-9]\s*차|단가|평가|보증|한도|이하인',lead):continue
            tail=t[a.end():a.end()+135]
            m=MONEY.search(tail)
            if not m or m.start()>70:continue
            pre=tail[:m.start()]
            if re.search(r'이하|이상|미만|초과|[0-9]%|계산|기준으로|산정|낙찰|투찰|예정가격|제\d+조',pre):continue
            # A field label must be followed by its literal value, not narrative.
            if not re.fullmatch(r'[\s:：|=금￦₩\\()]*[가-힣]{0,28}[\s(￦₩\\]*',pre):continue
            value=money_value(m)
            if value is None:continue
            around=t[a.start():a.end()+m.end()+90]
            after=tail[m.end():m.end()+80]
            label=compact(a.group())
            basis='estimated_ex_vat' if label=='추정가격' else 'unresolved_budget_basis'
            c=compact(m.group()+' '+after).lower()
            if label!='추정가격' and re.search(r'(?:부가가치세|부가세|vat)[^\n]{0,14}포함',c) and not re.search(r'(?:부가가치세|부가세|vat)[^\n]{0,14}(?:미포함|불포함|별도|제외)',c):basis='budget_including_vat'
            found.append(fact(di,t,a.start(),a.end()+m.end()+min(50,len(after)),label,str(value),basis=basis))
    return found


def v23(rec):
    meta=rec.get('meta',{})
    law=applicable_law(rec)
    if law not in {'국가계약법','지방계약법'}:
        return result(23,None,'unknown_applicable_law')
    if law=='국가계약법':return result(23,0,'national_contract_outside_item_scope')
    award=meta.get('낙찰방법')
    if not known(award):return result(23,None,'unknown_award_procedure')
    negotiated='협상' in compact(award)
    explicit_procedures=[]
    for d in rec['docs']:
        if d['type']=='공고문':
            for m in re.finditer(r'(?:계약\s*방법|낙찰자?\s*선정\s*방법)\s*[:：|]?\s*([^\n]{1,70})',d['text']):explicit_procedures.append(m[1])
    body_neg=any(re.search(r'협상\s*에\s*의한',x) for x in explicit_procedures)
    if body_neg and not negotiated:return result(23,None,'conflicting_award_procedure')
    if not negotiated:return result(23,0,'not_negotiated_contract')
    briefs=[]; negatives=[]; unresolved=[]; deadlines=[]; publications=[]
    for di,d in enumerate(rec['docs']):
        if d['type'] not in {'공고문','제안요청서'}:continue
        t=d['text']
        for a in BRIEF.finditer(t):
            block,end=field_window(t,a.start(),a.end(),170)
            before=t[max(0,a.start()-90):a.start()]
            if re.search(r'담합|손해|착수|주민|홍보|워크숍|프로그램|과업\s*수행',before):continue
            if re.search(r'배상',t[max(t.rfind('\n',0,a.start())+1,a.start()-90):a.start()]):continue
            if d['type']!='공고문' and not SCHEDULE.search(before):continue
            # Attendability/handbook mentions are not scheduling anchors.
            immediate=t[a.end():a.end()+35]
            if re.match(r'\s*(?:참석|불참|미참석|참가|사항에|문구|자료)',immediate):continue
            if NO_BRIEF.search(block):
                negatives.append(fact(di,t,a.start(),end,'briefing_not_held',False));continue
            ds=dates(block)
            if re.search(r'평가위원|제안서\s*평가|제안\s*발표',block[:ds[0][0]] if ds else block):continue
            if not ds or ds[0][0]>140:
                if d['type']=='공고문':unresolved.append(fact(di,t,a.start(),end,'briefing_unresolved',None))
                continue
            b,e,date=ds[0]
            between=block[a.end()-a.start():b]
            if re.search(r'제안서\s*(?:제출|접수)|접수\s*마감|개찰',between):continue
            briefs.append(fact(di,t,a.start(),a.start()+e,'briefing',date.isoformat()))
        for a in DEADLINE.finditer(t):
            block,end=field_window(t,a.start(),a.end(),220)
            # Bare 접수마감 is only accepted in an explicit tender schedule.
            if compact(a.group())=='접수마감' and not re.search(r'제안|입찰',t[max(0,a.start()-550):a.start()]):continue
            ds=dates(block)
            if not ds:continue
            between=block[a.end()-a.start():ds[0][0]]
            if re.search(r'개찰|평가|발표|설명회|설명\s*:',between):continue
            # Announcement signatures, contacts and handouts are not deadlines.
            if re.search(r'공고합니다|관련\s*사항|제안서\s*교부',between):continue
            # A cited statute's edition date belongs to the citation, even
            # when it follows a proposal submission-method heading.
            if re.search(r'(?:법률|시행령|시행규칙|예규|고시)\s*(?:제?\s*[\d-]+\s*호)?\s*[（(]\s*$',between):continue
            # Explicit date ranges yield their final endpoint. No bid-opening fallback.
            chosen=ds[0]
            if len(ds)>1:
                separator=block[ds[0][1]:ds[1][0]]
                # A 10:00~17:00 time range cannot reach the next event's date.
                if re.fullmatch(r'\s*(?:\([월화수목금토일]\)\s*)?(?:\d{1,2}:\d{2}\s*)?(?:~|∼|～|부터)\s*',separator):chosen=ds[1]
                elif ds[1][0]-ds[0][1]<45 and re.fullmatch(r'\s*(?:\([월화수목금토일]\)\s*)?(?:\d{1,2}:\d{2}\s*)?',separator) and re.match(r'(?:~|∼|～|부터)',block[ds[1][0]:ds[1][1]]):chosen=ds[1]
            deadlines.append(fact(di,t,a.start(),a.start()+chosen[1],'proposal_deadline',chosen[2].isoformat()))
        if d['type']=='공고문':
            for a in re.finditer(r'공고\s*(?:게시\s*일|일자|일|기간)\s*[:：|]',t):
                block,end=field_window(t,a.start(),a.end(),80);ds=dates(block)
                if ds and not re.search(r'사전',t[max(0,a.start()-10):a.start()]) and not re.search(r'공고일\s*로부터',block):publications.append(fact(di,t,a.start(),a.start()+ds[0][1],'publication',ds[0][2].isoformat()))
    vals={f['value'] for f in briefs}
    if not vals:
        if negatives and not unresolved:return result(23,0,'explicit_briefing_not_held',negatives)
        return result(23,None,'no_resolved_briefing_date',unresolved+negatives)
    if len(vals)!=1 or negatives:return result(23,None,'conflicting_briefing_dates_or_cancellation',briefs+negatives)
    deadline_vals={f['value'] for f in deadlines}
    if len(deadline_vals)>1:return result(23,None,'conflicting_proposal_deadlines',briefs+deadlines)
    amount_facts=extract_amounts(rec)
    # Import locally: the shared price parser also uses temporal source facts.
    # A registration disagreement is preserved, but does not replace the
    # official notice amount used for applicability.
    from .prices import project_prices
    price = project_prices(rec)['estimated_price']
    estimate = price['value_won']
    ordinary_threshold=None if estimate is None else 10 if estimate<100000000 else 20 if estimate<1000000000 else 40
    # The supplied local-contract snapshot has a separate urgent branch.  For
    # a real pre-proposal briefing, seven full calendar days between briefing
    # and proposal deadline are sufficient on that branch.  Use only the
    # official structured Y flag; a title containing "긴급" is not enough.
    urgent = meta.get('긴급공고여부') == 'Y'
    threshold = 7 if urgent else ordinary_threshold
    briefing=dt.date.fromisoformat(next(iter(vals)))
    gap=None if not deadline_vals else (dt.date.fromisoformat(next(iter(deadline_vals)))-briefing).days
    pubs={f['value'] for f in publications}
    meta_pub=meta.get('공고게시일자')
    if len(pubs)>1:return result(23,None,'conflicting_publication_dates',briefs+publications)
    if known(meta_pub) and re.fullmatch(r'20\d{6}',str(meta_pub)):
        try:mp=dt.datetime.strptime(str(meta_pub),'%Y%m%d').date().isoformat()
        except ValueError:mp=None
        if mp and pubs and mp not in pubs:return result(23,None,'body_meta_publication_date_conflict',briefs+publications)
        if mp and not pubs:pubs={mp}
    pubgap=None if not pubs else (briefing-dt.date.fromisoformat(next(iter(pubs)))).days
    calc=dict(kind='calculation',estimated_price=str(estimate) if estimate is not None else None,price_resolution=price,required_days=threshold,ordinary_required_days=ordinary_threshold,urgent_notice=urgent,briefing_to_proposal_calendar_days=gap,publication_to_briefing_calendar_days=pubgap,boundary_policy='urgent: equality sufficient; ordinary: equality abstains')
    facts=briefs+deadlines+publications+amount_facts+[calc]
    if gap is not None and gap<=0:return result(23,None,'briefing_not_before_proposal_or_wrong_event',facts)
    if pubgap is not None and pubgap<0:return result(23,None,'briefing_before_publication_or_wrong_event',facts)
    # A strict shortfall is invariant to the unresolved exact-day counting boundary.
    if (gap is not None and threshold is not None and gap<threshold) or (pubgap is not None and pubgap<7):
        return result(23,1,'definite_shortfall',facts,briefs[0]['evidence'])
    if urgent and gap is not None and gap>=threshold and pubgap is not None and pubgap>=7:
        return result(23,0,'urgent_intervals_sufficient',facts)
    if not urgent and gap is not None and threshold is not None and gap>threshold and pubgap is not None and pubgap>7:
        return result(23,0,'both_intervals_clearly_sufficient',facts)
    return result(23,None,'missing_interval_or_exact_boundary',facts)


PROVINCES={
 '서울':'서울특별시','부산':'부산광역시','대구':'대구광역시','인천':'인천광역시','광주':'광주광역시','대전':'대전광역시','울산':'울산광역시','세종':'세종특별자치시',
 '경기':'경기도','강원':'강원특별자치도','충북':'충청북도','충남':'충청남도','전북':'전북특별자치도','전남':'전라남도','경북':'경상북도','경남':'경상남도','제주':'제주특별자치도',
}
ALIASES={**PROVINCES,**{v:v for v in PROVINCES.values()},'강원도':'강원특별자치도','전라북도':'전북특별자치도','제주도':'제주특별자치도'}
REGION_RE=re.compile('|'.join(sorted(map(re.escape,ALIASES),key=len,reverse=True)))
OFFICE=re.compile(r'법인등기부\s*상\s*본점\s*소재지|본점\s*소재지|주된\s*(?:영업소|사무소)(?:\s*소재지)?|본사|사업장\s*소재지')
# A physical list item is an ownership boundary even when PDF extraction left
# no blank line. Do not attach the next duty's place, OR or negation to this one.
_REGISTRATION_ITEM = re.compile(
    r'\n[ \t]*(?:(?:\d+(?:-\d+)*|[가-하])[.)][ \t]*|[①-⑳•○●◦◾▪□■※✓]\s*|[-–—][ \t]+)')


def registration_bounds(text, start, end):
    """Keep a registration sentence's ordinary context within its list item."""
    from .assertions import clause
    lo, hi = clause(text, start, end)
    for boundary in _REGISTRATION_ITEM.finditer(text, lo, hi):
        if boundary.end() <= start:
            lo = boundary.end()
        elif boundary.start() >= end:
            hi = boundary.start()
            break
    return lo, hi


def region_observation(text, *, require_place_role=False):
    """Project explicit province attributes without reading other token prose.

    A known basic unit and an unreadable/unknown unit are different facts.
    Neither province projection nor a local rN symbol proves district equality.
    """
    from .anonymized_tokens import anonymous_tokens, province_projection
    projected=province_projection(text,allowed_provinces=ALIASES)
    names=sorted({ALIASES[m.group()] for m in REGION_RE.finditer(projected)})
    basic=False
    unresolved=[]
    for token in anonymous_tokens(text):
        reasons=list(token.errors)
        if token.kind=='institution':
            if require_place_role and not re.match(
                    r'\s*(?:관할(?:\s*(?:구역|지역))?\s*)?(?:내(?:에)?|에|안에)\s*'
                    r'(?:소재|두고|둔|있는)',text[token.end:]):
                continue  # The agency issuing specifications is a different subject.
            reasons.append('institution_token_does_not_identify_a_region')
        else:
            unit=token.attribute('단위')
            basic |= not token.errors and unit=='기초'
            if not token.errors and unit not in ('기초','광역'):
                reasons.append('region_unit_missing_or_unrecognized')
            if not token.errors and token.attribute('광역') not in ALIASES:
                reasons.append('province_missing_or_unrecognized')
        if reasons:
            unresolved.append({'start':token.start,'end':token.end,'text':token.text,'reasons':reasons})
    return {'provinces':names,'basic_level':basic,
        'anonymous_scope_unresolved':bool(unresolved),'unresolved_tokens':unresolved}


def region_set(text):
    # Compatibility projection: the second value means hierarchy review, not
    # certified membership in a basic municipality. Exact token facts stay above.
    observation=region_observation(text)
    return set(observation['provinces']),bool(observation['basic_level'] or observation['anonymous_scope_unresolved'])


def region_clauses(rec, *, doc_types=('공고문',)):
    facts=[]
    for di,d in enumerate(rec['docs']):
        if doc_types is not None and d['type'] not in doc_types:continue
        t=d['text']
        for a in OFFICE.finditer(t):
            # The operative regional phrase can follow a long definition in parentheses.
            tail=t[a.start():a.start()+480]
            nxt=_REGISTRATION_ITEM.search(tail,a.end()-a.start())
            if nxt:tail=tail[:nxt.start()]
            tail=tail.rstrip()
            if re.search(r'다른\s*경우|변경등록|불일치|확인\s*서류',tail):continue
            compact_tail=compact(tail)
            if not re.search(r'(?:소재|두고|둔|있는|기재).{0,60}(?:업체|사업자|자로|자이어야|자에|갖춘자)|(?:둔|소재한|두고있는)자(?:[.。]|$)|업체.{0,15}(?:소재|두고|둔)',compact_tail):continue
            names,basic=region_set(tail)
            if not names and not basic:continue
            # Isolate through the operative bidder restriction, not contact addresses.
            m=re.search(r'(?:있는|둔|두고|소재한|소재하고|기재되어\s*있는)[^\n]{0,40}?(?:업체|사업자|자이어야|자로)|갖춘\s*자|업체',tail)
            end=a.start()+(m.end() if m else len(tail))
            quote=t[a.start():end]
            observation=region_observation(quote,require_place_role=True)
            names,basic=observation['provinces'],observation['basic_level']
            unresolved=observation['anonymous_scope_unresolved']
            if not names and not basic and not unresolved:continue
            if re.search(r'제출\s*장소|접수\s*장소|납품\s*장소',quote):continue
            extra={}
            if unresolved:
                extra={'anonymous_region_scope_unresolved':True,
                    'unresolved_region_tokens':[{**token,'doc_index':di,
                        'start':a.start()+token['start'],'end':a.start()+token['end']}
                        for token in observation['unresolved_tokens']]}
            facts.append(fact(di,t,a.start(),end,'bidder_region',sorted(names),basic_level=basic,**extra))
    return facts


def contract_fields(rec, *, doc_types=('공고문',), include_unresolved=False, observation_only=False):
    """Keep measured literal observations separate from method policy inference."""
    out=[]
    for di,d in enumerate(rec['docs']):
        if doc_types is not None and d['type'] not in doc_types:continue
        t=d['text']
        title_methods=set(re.findall(r'[(（](일반경쟁|제한경쟁|지명경쟁|수의계약)[·ㆍ・･.]', compact(t)))
        quotation=bool(re.search(
            r'(?:소액\s*수의|수의\s*견적|견적\s*제출)[^\n.]{0,45}(?:공고|안내)|'
            r'(?:제출\s*(?:된|한)?|대비)\s*견적\s*가격|소액\s*수의계약\s*결격', t))
        pat=re.compile('(?:'+sp('계약방법')+'|'+sp('입찰방법')+'|'+sp('입찰방식')+r')\s*[:：|]?\s*([^\n]{0,85})')
        for a in pat.finditer(t):
            value=a.group(1)
            normalized=value if observation_only else re.sub(r'[(（]\s*(?:단가|총액)\s*[)）]', '', value)
            m=re.search(r'일반\s*경쟁|제한\s*경쟁|지명\s*경쟁|수의\s*계약',normalized)
            if m:
                # Restrictions within a small-quotation procedure do not change
                # the semantic contract method into competitive tendering.
                value_compact=compact(normalized)
                quote=bool(re.search(r'(?:소액(?:\(총액\))?)?수의(?:계약|견적|입찰)|소액(?:\(총액\))?수의'
                                    if observation_only else r'(?:소액)?수의(?:계약|견적|입찰)|소액수의',value_compact))
                method='수의계약' if quote else compact(m.group())
                unresolved=not observation_only and bool(re.search(r'(?:제한|일반|지명)\s*[/／]\s*(?:제한|일반|지명)', value)
                    or quotation and method!='수의계약'
                    or title_methods and title_methods!={method})
                if unresolved and not include_unresolved:
                    continue
                out.append(fact(di,t,a.start(),a.end(),'competition_method',method,
                    **({'assertion_scope_unresolved':True} if unresolved else {})))
    return out


def industry_fields(rec, *, doc_types=('공고문',), observation_only=False):
    """Policy may extend a predicate; observation coordinates remain unchanged."""
    from .assertions import assertion_scope, unresolved_assertion, has_withdrawal
    out=[]
    pat=re.compile(r'(?:업종|면허)\s*(?:코드|번호)?\s*[:：]?\s*(\d{4})(?!\d)')
    # An original name/code pair is also a code observation. No external alias
    # table, metadata-derived code or inferred code is inserted into the source.
    # Requiring a registration-class suffix and a closing bracket excludes
    # years, ten-digit purchase identities, amounts and model numbers.
    named=re.compile(
        r'(?P<name>[가-힣][가-힣·ㆍ. \t]{1,60}?(?:업|업자|용역|면허|서비스))'
        r'[ \t]*(?:[(（\[]|[:：])[ \t]*(?P<code>\d{4})[ \t]*(?=[)）\]])')
    withdrawn = has_withdrawal(rec, 'industry')
    for di,d in enumerate(rec['docs']):
        if doc_types is not None and d['type'] not in doc_types:continue
        t=d['text']
        candidates = [(a.start(), a.end(), a[1], 'explicit_code_label') for a in pat.finditer(t)]
        candidates += [(a.start(), a.end(), a['code'], 'original_name_code_pair') for a in named.finditer(t)]
        seen = set()
        for start, end, code, mechanism in sorted(candidates):
            lo,hi=registration_bounds(t,start,end)
            # A wrapped parenthesis can contain the code while the registration
            # predicate follows on the next paragraph. Keep its numbered item.
            item_lo=max((m.end() for m in _REGISTRATION_ITEM.finditer(t,0,start)),default=0)
            item_end=_REGISTRATION_ITEM.search(t,end)
            item_hi=item_end.start() if item_end else len(t)
            if not observation_only and t[item_lo:start].count('(')>t[item_lo:start].count(')'):
                closing=t.find(')',end,item_hi)
                if closing>=0 and closing-end<=800:
                    lo=max(item_lo,start-420)
                    stop=re.search(r'[.。;；](?=\s|$)',t[closing:item_hi])
                    hi=min(item_hi,closing+stop.start()+1 if stop else closing+240)
            context=assertion_scope(t,start,end,'industry',bounds=(lo,hi))
            if not re.search(r'등록|신고|허가',context):continue
            if not re.search(r'업체|자이어야|한\s*자|된\s*자|갖춘\s*자|등록한',context):continue
            if re.search(r'경우에\s*한|해당\s*시|변경\s*등록|입찰\s*대리인',context):continue
            if (lo,hi,code) in seen:continue
            seen.add((lo,hi,code))
            # A bare parenthesized number next to a service name can be a year
            # or reference. It needs its own registration link before it can
            # support comparison; an unrelated later registration is insufficient.
            named_link = mechanism != 'original_name_code_pair' or bool(re.match(
                r'[)）\]」』’\'" \t]*(?:으로|로|에|을|를)[^\r\n]{0,45}?(?:등록|신고|허가)', t[end:hi]))
            out.append(fact(di,t,lo,hi,'mandatory_industry_code',code,
                extraction=mechanism,
                alternative=bool(re.search(r'또는|중\s*하나|이거나',context)),
                predicate_scope=context,
                assertion_scope_unresolved=not named_link or withdrawn or unresolved_assertion(context) or bool(re.search(
                    r'(?:등록|신고|허가).{0,8}하지\s*(?:않|아니)|등록\s*(?:면제|불필요|불요)|미등록', context))))
    return out


def v24(rec):
    meta=rec.get('meta',{});facts=[];flags=[];explicit=[];unresolved=[]
    amounts=extract_amounts(rec);facts.extend(amounts)
    # 기초금액 is a base price, not automatically the allocated project budget.
    budgets=[f for f in amounts if f['basis']=='budget_including_vat' and f['kind']!='기초금액']
    budget_values={Decimal(f['value']) for f in budgets}
    mb=meta.get('배정예산금액')
    if len(budget_values)==1 and isinstance(mb,(int,float)) and not isinstance(mb,bool) and mb>0:
        bv=next(iter(budget_values));delta=abs(bv-Decimal(str(mb)))
        if delta>1:
            explicit.append(dict(field='budget_including_vat',body=str(bv),metadata=mb,evidence=budgets[0]['evidence']))
        elif delta:unresolved.append('one_won_budget_difference_not_material')
    else:unresolved.append('budget_missing_ambiguous_or_basis_unresolved')
    contracts=contract_fields(rec);facts.extend(contracts);cv={f['value'] for f in contracts}
    cm=compact(meta.get('계약방법',''))
    if len(cv)==1 and cm in {'일반경쟁','제한경쟁','지명경쟁','수의계약'}:
        bv=next(iter(cv))
        if bv!=cm:explicit.append(dict(field='competition_method',body=bv,metadata=cm,evidence=contracts[0]['evidence']))
    else:unresolved.append('competition_method_missing_or_conflicting')
    regions=region_clauses(rec);facts.extend(regions)
    if regions and meta.get('지역제한여부')=='N':flags.append(dict(field='region_flag',body='explicit_bidder_region',metadata='N',evidence=regions[0]['evidence']))
    mr=meta.get('제한지역코드목록')
    if regions and known(mr):
        meta_names,meta_basic=region_set(str(mr));sets={tuple(f['value']) for f in regions if f['value']}
        if len(sets)==1 and meta_names:
            bv=set(next(iter(sets)))
            # Extra body province proves a mismatch even when a district is anonymized.
            if bv-meta_names:explicit.append(dict(field='region_provinces',body=sorted(bv),metadata=sorted(meta_names),evidence=next(f['evidence'] for f in regions if set(f['value'])==bv)))
            elif meta_names-bv and not any(f['basic_level'] or f.get('anonymous_region_scope_unresolved') for f in regions) and not meta_basic:
                explicit.append(dict(field='region_provinces',body=sorted(bv),metadata=sorted(meta_names),evidence=regions[0]['evidence']))
            elif any(f['basic_level'] or f.get('anonymous_region_scope_unresolved') for f in regions) or meta_basic:unresolved.append('district_equivalence_unresolved')
        else:unresolved.append('region_sets_unresolved_or_conflicting')
    else:unresolved.append('region_value_missing')
    industries=industry_fields(rec);facts.extend(industries)
    industries=[f for f in industries if not f['assertion_scope_unresolved']]
    if industries and meta.get('업종제한여부')=='N':flags.append(dict(field='industry_flag',body='explicit_mandatory_code',metadata='N',evidence=industries[0]['evidence']))
    ml=meta.get('면허업종제한목록');codes=set(re.findall(r'(?<!\d)\d{4}(?!\d)',str(ml))) if known(ml) else set()
    body_codes={f['value'] for f in industries}
    if len(body_codes)==len(codes)==1 and not any(f['alternative'] for f in industries) and body_codes!=codes:
        explicit.append(dict(field='industry_code',body=sorted(body_codes),metadata=sorted(codes),evidence=industries[0]['evidence']))
    else:unresolved.append('industry_value_missing_partial_or_alternative')
    # Partial extraction cannot certify all four semantic fields as matching.
    # Region-set extraction is retained for audit but not promoted to the default
    # overlay: province projection can lose hierarchy and registration semantics.
    structured=[x for x in explicit if x['field']!='region_provinces']
    res=result(24,1 if structured else None,'structured_field_mismatch' if structured else 'no_proven_structured_field_mismatch',facts,structured[0]['evidence'] if structured else '')
    res.update(flag_contradictions=flags,value_mismatches=explicit,unresolved=unresolved,
               value_comparison_value=1 if explicit else None,
               value_comparison_evidence=explicit[0]['evidence'] if explicit else '',
               diagnostic_value=1 if explicit or flags else None,
               diagnostic_evidence=(explicit+flags)[0]['evidence'] if explicit or flags else '')
    return res


def predict(rec):
    return {'v23':v23(rec),'v24':v24(rec)}
