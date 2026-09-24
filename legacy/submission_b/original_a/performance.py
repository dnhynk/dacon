"""CPU-only, label/ID-free, conservative performance facts prototype.

All offsets are half-open Python character offsets into unmodified doc text.
No absence-based negative decisions. Policy constants refer to supplied law,
not an asserted current-law service. See legal_sources.json and report.
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

NOTICE_WON = 230_000_000  # supplied national notice; local decree 20(1)(5)
ITEMS = (2, 3, 4, 8)


def compact(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text) if not c.isspace())


def mapped(text):
    chars, positions = [], []
    for pos, ch in enumerate(text):
        for c in unicodedata.normalize('NFKC', ch):
            if not c.isspace():
                chars.append(c)
                positions.append(pos)
    return ''.join(chars), positions


def span(doc, di, start, end):
    return {'doc_index': di, 'doc_id': doc.get('doc_id'),
            'document_role': doc.get('type'), 'start': start, 'end': end,
            'text': doc['text'][start:end]}


def subspan(doc, di, base, positions, start, end):
    return span(doc, di, base + positions[start], base + positions[end-1] + 1)


def lines(doc, di):
    for m in re.finditer(r'[^\r\n]+', doc['text']):
        if m.group().strip():
            yield span(doc, di, m.start(), m.end())


NUM = r'\d[\d,]*(?:\.\d+)?'
UNIT = r'(?:천만|백만|십만|억|만|천|백|십)'
MONEY = re.compile(r'(?<![\d.,])(?:' + NUM + UNIT + r'?(?:' + NUM + UNIT + r')?원|' + NUM + r'억(?![\d원]))')
MULT = {'억': 100000000, '천만': 10000000, '백만': 1000000,
        '십만': 100000, '만': 10000, '천': 1000, '백': 100, '십': 10, '': 1}


def won(raw):
    raw = compact(raw).removesuffix('원').replace(',', '')
    total, end = Decimal(0), 0
    for m in re.finditer(r'(\d+(?:\.\d+)?)(천만|백만|십만|억|만|천|백|십)?', raw):
        if m.start() != end:
            raise ValueError(raw)
        total += Decimal(m[1]) * MULT[m[2] or '']
        end = m.end()
    if end != len(raw) or total != total.to_integral_value():
        raise ValueError(raw)
    return int(total)


def vat(text):
    n = compact(text).upper()
    inc = bool(re.search(r'(?:VAT|부가가치세|부가세)[^가-힣A-Z]{0,3}포함', n))
    exc = bool(re.search(r'(?:VAT|부가가치세|부가세)[^가-힣A-Z]{0,3}(?:별도|제외)', n))
    return 'conflict' if inc and exc else 'included' if inc else 'excluded' if exc else 'unspecified'


def amounts(ev, doc):
    n, pos = mapped(ev['text'])
    out = []
    for m in MONEY.finditer(n):
        tail = n[m.end():m.end()+32]
        cm = re.match(r'(?:\([^)]{0,24}\))?(의)?(이상|초과|이하|미만)', tail)
        comparator = cm[2] if cm else None
        # Current-project amounts are facts, never silently experience cutoffs.
        project = bool(re.search(r'(?:본사업|금회|금번|현재사업)(?:의)?(?:예산|금액|기초금액)[^\d]{0,8}$', n[max(0,m.start()-22):m.start()]))
        out.append({'won': won(m.group()), 'comparator': comparator,
                    'vat': vat(n[max(0,m.start()-12):m.end()+27]),
                    'binding': 'current_project' if project else 'experience_candidate',
                    'evidence': subspan(doc, ev['doc_index'], ev['start'], pos, m.start(), m.end())})
    return out


ELIG = re.compile(r'(?:입찰|견적(?:서)?제출|제안(?:\(입찰\))?)(?:참가|참여)?자격|참가자격|입찰참가조건')
SCORE = re.compile(r'배점|정량(?:적)?평가|평가기준|평가항목|평가방법|적격심사|수행능력평가|기술능력평가')
FORM = re.compile(r'서식\s*\d|붙임\d|서식[〉>\]]|제출서류|제출목록|작성요령|작성지침|증명서양식')
PAST = re.compile(r'실적|수행경험|납품경험|최근\d+년.{0,240}(?:수행|완료|납품)')
MANDATORY_END = re.compile(r'(?:실적|경험).{0,200}(?:업체|자격|있어야|보유한자|있는자)|(?:수행|완료|납품)\)?한업체')


def heading_role(n):
    """Only explicit, short headings establish governing section context."""
    prefix = bool(re.match(r'^(?:\d+(?:[-.]\d+)*[.)]?|[가-하][.)]|[IVXⅠⅡⅢⅣⅤⅥ]+[.)]?|[□■◆◇○])', n))
    short = len(n) <= 95
    if short and ELIG.search(n) and not re.search(r'등록규정|시행령|등록한|갖춘|문의|법률',n) and (prefix or n.endswith(('자격','조건'))):
        return 'eligibility'
    if short and ((SCORE.search(n) and (prefix or '배점' in n or '평가' in n)) or (PAST.search(n) and re.search(r'\d+점',n))):
        return 'scoring'
    if short and FORM.search(n):
        return 'forms'
    if len(n) <= 65 and re.match(r'^\d+[.](?!\d)', n):
        return 'other'
    return None


def purchaser(n):
    private = re.search(r'민간|민자|일반기업', n)
    excludes = bool(re.search(r'(?:민간|민자|일반기업).{0,25}(?:불인정|인정하지|제외)', n))
    public = re.search(r'국가기관|국가[,·ㆍ및]|지방자치단체|지자체|정부투자기관|공공기관|대학병원', n)
    if private and re.search(r'각각|모두보유',n):
        return 'public_private_conjunction_unresolved'
    if private and not excludes:
        return 'public_or_private_accepted' if public else 'private_accepted'
    # An institution reference must modify prior commissioning/delivery, not
    # merely certify documents or identify the current purchaser/address.
    relation = re.search(r'(?:국가기관|국가|지방자치단체|정부투자기관|공공기관|대학병원|\[수요기관\([^]]+\])[^。\n]{0,75}(?:발주|시행한|납품한|통근버스운행실적)', n)
    if relation or (excludes and public):
        return 'specific_purchaser_required'
    return 'unspecified'


def project_prices(record):
    obs = {'estimated_price': [], 'budget': []}
    for di, doc in enumerate(record['docs']):
        doclines = list(lines(doc, di))
        for li, ev in enumerate(doclines):
            n, pos = mapped(ev['text'])
            # Field/value binding excludes legal price bands in prose.
            for m in re.finditer(r'(추정가격|사업예산|사업금액|배정예산|기초금액|추정금액)(?:\([^)]{0,25}\))?[:：|=]+(?:금|￦|₩|\\)?(' + NUM + r'(?:천만|백만|십만|억|만|천)?원?)', n):
                raw = m[2]
                if not raw.endswith('원') and not re.search(r'[천만억]', raw):
                    if len(re.sub(r'\D', '', raw)) < 5:
                        continue
                try:
                    value = won(raw)
                except ValueError:
                    continue
                kind = 'estimated_price' if m[1] == '추정가격' else 'budget'
                if m[1] == '추정금액' and vat(n) != 'included':
                    continue  # estimated total is not estimated price
                next_note=doclines[li+1] if li+1<len(doclines) else None
                unit_context=n+(compact(next_note['text']) if next_note and compact(next_note['text']).startswith('※') else '')
                unit_price=m[1]=='기초금액' and bool(re.search(r'단가|원/(?:톤|l|L|ℓ|kg)',unit_context))
                obs[kind].append({'won': value, 'field': m[1], 'vat': vat(n),
                                 'price_role':'unit_price_excluded' if unit_price else 'project_total_candidate',
                                 'source_context':ev,
                                 'unit_note':next_note if unit_price and next_note and compact(next_note['text']).startswith('※') else None,
                                 'evidence': subspan(doc, di, ev['start'], pos, m.start(), m.end())})
    result = {}
    for kind, key in [('estimated_price', '입찰추정가격'), ('budget', '배정예산금액')]:
        meta = record.get('meta', {}).get(key)
        meta = meta if isinstance(meta, int) and not isinstance(meta, bool) and meta > 0 else None
        bodyvals = {x['won'] for x in obs[kind] if x['price_role']!='unit_price_excluded'}
        vals = bodyvals | ({meta} if meta is not None else set())
        result[kind] = {'meta': {'field': key, 'won': meta}, 'body': obs[kind],
                        'status': 'conflict' if len(vals)>1 else 'known' if vals else 'unknown',
                        'value_won': next(iter(vals)) if len(vals)==1 else None,
                        'basis': 'body_and_meta' if bodyvals and meta is not None else 'body' if bodyvals else 'meta_only'}
    return result


def performance_facts(record):
    """Return facts + nullable per-item overlays; never inspect a record ID."""
    candidates, regions, procedures, exclusions = [], [], [], []
    scanned = 0
    for di, doc in enumerate(record['docs']):
        scanned += len(doc['text'])
        role, heading = 'unknown', None
        doclines = list(lines(doc, di))
        for li, ev in enumerate(doclines):
            n = compact(ev['text'])
            new_role = heading_role(n)
            if new_role:
                role, heading = new_role, ev
            if re.search(r'수의(?:계약)?(?:견적|계약)|소액수의|견적(?:서)?제출(?:안내공고|및계약방법|대상용역)', n) and not re.search(r'참고|준용|경우|법률|시행령', n):
                procedures.append(ev)
            permission = bool(re.search(r'실적.{0,25}(?:제한없|제한하지|관계없이|무관하게|없어도|없는업체도)', n))
            if permission and role == 'eligibility':
                exclusions.append(ev)
            # A past purchaser/facility's location is not a restriction on the
            # bidder's current office. Preserve that distinction for v8.
            if role == 'eligibility' and re.search(r'본점|본사|주된영업소|주된사무소', n):
                place = re.search(r'\[지역:|\[수요기관\(기초자치단체\)\].{0,3}내|(?:특별|광역)시|특별자치도|경기|경북|경남|경상|강원|충청|전라|제주', n)
                operative = re.search(r'업체|사업자|제한|두고|둔|갖춘자|있는자', n)
                neg = re.search(r'지역제한없|소재지.{0,15}(?:무관|관계없)|소재지.{0,10}제한하지', n)
                if place and operative and not neg:
                    regions.append({'evidence': ev, 'governing_heading': heading, 'status': 'operative'})
            if not PAST.search(n):
                continue
            local_score = bool(re.search(r'배점|\d+(?:\.\d+)?점|평가한다|평가하며|실적으로평가', n))
            local_form = bool(re.search(r'실적증명서.{0,20}(?:[1-9]부|서식)|실적만기재|실적은.{0,20}기재|기재한|잔존구성원|집행실적|배출실적', n))
            positive_gate = bool(MANDATORY_END.search(n))
            actual_gate = role == 'eligibility' and positive_gate and not local_score and not local_form
            vague = bool(re.search(r'실적이우수|풍부한실적|실적이풍부|업체또는|보유하거나', n))
            qualifier_note = n.startswith('※') and bool(re.search(r'공동수급체중|대표사를제외|조건만충족|실적증명서는.{0,25}제출',n))
            if permission:
                status = 'explicit_permission'
            elif qualifier_note:
                status = 'qualification_note'
            elif actual_gate and not vague:
                status = 'mandatory'
            elif actual_gate and vague:
                status = 'ambiguous_eligibility'
            elif local_score or role == 'scoring':
                status = 'scoring'
            elif local_form or role == 'forms':
                status = 'forms_or_submission'
            else:
                status = 'unresolved'
            money = amounts(ev, doc)
            req = [a for a in money if a['comparator'] in ('이상', '초과') and a['binding']=='experience_candidate']
            if '합산' in n or '합계' in n or '누계' in n:
                aggregation = 'sum' if not re.search(r'단일|단독계약', n) else 'mixed'
            elif re.search(r'단일|단독계약', n):
                aggregation = 'single_contract'
            else:
                aggregation = 'unspecified'
            quantities=[]
            nn, pm = mapped(ev['text'])
            for qm in re.finditer(r'(\d[\d,.]*)(㎡|m2|m²|톤|대|건|명|인)(?:의)?(이상|초과)',nn):
                quantities.append({'value': qm[1], 'unit': qm[2], 'comparator': qm[3],
                                   'evidence': subspan(doc,di,ev['start'],pm,qm.start(),qm.end()),
                                   'comparison': 'abstain_no_universal_quantity_limit'})
            notes=[]
            for nx in doclines[li+1:li+4]:
                nxn=compact(nx['text'])
                if nxn.startswith(('※','○위실적')) and re.search(r'실적|준공금액|공동수급', nxn):
                    notes.append(nx)
                else:
                    break
            combined=n+''.join(compact(x['text']) for x in notes)
            candidates.append({'status': status, 'section_role': role, 'evidence': ev,
                               'governing_heading': heading, 'notes': notes, 'money': money,
                               'required_money': req[0] if len(req)==1 else None,
                               'amount_status': 'known' if len(req)==1 else 'multiple' if req else 'unknown',
                               'quantities': quantities, 'aggregation': aggregation,
                               'purchaser': purchaser(combined)})
    meta=record.get('meta',{})
    law=meta.get('적용계약법')
    work=meta.get('업무구분')
    prices=project_prices(record)
    estimate=prices['estimated_price']['value_won']
    budget=prices['budget']['value_won']
    mandatory=[c for c in candidates if c['status']=='mandatory']
    ambiguous=[c for c in candidates if c['status']=='ambiguous_eligibility']
    quote=bool(procedures)
    blockers=[]
    if ambiguous: blockers.append('vague_experience_eligibility')
    if exclusions and mandatory: blockers.append('conflicting_experience_permission')
    if quote: blockers.append('actual_quote_procedure_exception_review')
    if any(c['amount_status']!='known' for c in mandatory): blockers.append('mandatory_amount_unknown_or_multiple')
    if any(c['quantities'] for c in mandatory): blockers.append('quantity_requires_contract_specific_rule')
    if work=='물품(내자)' and mandatory: blockers.append('v2_v8_goods_manufacturing_product_exception_scope_unimplemented')
    if any(p['status']=='conflict' for p in prices.values()): blockers.append('price_source_conflict')
    decisions={f'v{i}': {'value': None, 'reason': 'no_sufficient_operative_evidence', 'evidence': []} for i in ITEMS}
    def decide(i,value,reason,evidence):
        decisions[f'v{i}']={'value':value,'reason':reason,'evidence':evidence}
    valid=law in ('국가계약법','지방계약법') and work in ('일반용역','물품(내자)') and not (exclusions and mandatory)
    if valid and mandatory:
        es=[c['evidence'] for c in mandatory]
        if work=='일반용역' and estimate is not None and not quote:
            if estimate < NOTICE_WON:
                decide(2,1,'mandatory_service_experience_below_supplied_notice',es)
            elif law=='지방계약법' or meta.get('소관구분')=='국가기관':
                decide(2,0,'known_estimate_not_below_supplied_notice',es)
        numeric=[c for c in mandatory if c['required_money']]
        if budget and estimate:
            def compare(c):
                a=c['required_money']
                # Both explicitly stored comparisons; equality is unresolved.
                amount=a['won']
                c['comparison']={'required_won':amount, 'estimated_price_won':estimate,
                                  'budget_won':budget, 'vs_estimate':(amount>estimate)-(amount<estimate),
                                  'vs_budget':(amount>budget)-(amount<budget),
                                  'vat_caveat':a['vat']=='unspecified', 'basis':'nominal_documented_won'}
                return amount
            excessive=[c for c in numeric if compare(c)>max(estimate,budget)]
            if excessive:
                decide(3,1,'required_money_strictly_exceeds_both_price_bases',[c['evidence'] for c in excessive])
            elif len(numeric)==len(mandatory) and not ambiguous and all(c['required_money']['won']<min(estimate,budget) for c in numeric):
                decide(3,0,'all_extracted_mandatory_amounts_strictly_below_both_bases',es)
        specific=[c for c in mandatory if c['purchaser']=='specific_purchaser_required']
        private_accepted=[c for c in mandatory if c['purchaser'] in ('public_or_private_accepted','private_accepted')]
        if specific and private_accepted:
            blockers.append('purchaser_conflict_or_multiple_scopes_requires_review')
        elif specific:
            decide(4,1,'specific_prior_purchaser_in_mandatory_experience',[c['evidence'] for c in specific])
        elif not ambiguous and all(c['purchaser'] in ('public_or_private_accepted','private_accepted') for c in mandatory):
            decide(4,0,'mandatory_experience_explicitly_accepts_private_purchasers',es)
        if regions and not quote and work=='일반용역':
            decide(8,1,'mandatory_service_experience_and_operative_region',es+[r['evidence'] for r in regions])
    if quote:
        for i in (2,8):
            decisions[f'v{i}']['reason']='actual_quote_procedure_requires_exception_review'
    if mandatory and decisions['v3']['value'] is None:
        decisions['v3']['reason']='unknown_multiple_quantity_boundary_or_price_basis_conflict'
    return {'schema':'performance_facts_v1', 'law':law, 'work':work,
            'prices':prices, 'procedure':{'actual_quote_evidence':procedures, 'meta_contract_method':meta.get('계약방법')},
            'candidates':candidates, 'operative_regions':regions, 'explicit_no_experience_restriction':exclusions,
            'uncertainty':blockers, 'overlays':decisions,
            'scan':{'documents':len(record['docs']), 'characters':scanned,
                    'input_completeness':record.get('input_completeness'),
                    'dropped_doc_counts':record.get('dropped_doc_counts')}}


def compact_prompt(facts, *, max_examples=3):
    """Small reviewable model-input adapter; full facts remain the audit record.

    Retains all operative candidates/regions and up to max_examples scored
    or unresolved contrast examples. No raw string truncation of evidence.
    """
    def reference(ev):
        return f"[D{ev['doc_index']}|{ev['document_role']}|{ev['start']}:{ev['end']}] {ev['text']}"
    out=['PERFORMANCE FACTS (null = abstain; absence of extraction is not permission)']
    for kind, p in facts['prices'].items():
        out.append(f"{kind}={p['value_won']} KRW; {p['status']}; {p['basis']}; meta {p['meta']}")
        for b in p['body'][:2]: out.append(b['price_role']+' '+reference(b['evidence']))
    keep=[c for c in facts['candidates'] if c['status'] in ('mandatory','ambiguous_eligibility','qualification_note','explicit_permission')]
    contrasts=[c for c in facts['candidates'] if c['status'] in ('scoring','forms_or_submission','unresolved') and (c['money'] or c['purchaser']=='specific_purchaser_required')]
    seen=set()
    for c in keep+contrasts[:max_examples]:
        out.append(f"{c['status']}; aggregation={c['aggregation']}; purchaser={c['purchaser']}; required_money={c['required_money']['won'] if c['required_money'] else None}")
        for ev in [c['governing_heading'],c['evidence'],*c['notes']]:
            if ev is not None:
                key=(ev['doc_index'],ev['start'],ev['end'])
                if key not in seen:
                    out.append(reference(ev));seen.add(key)
    for r in facts['operative_regions']:out.append('OPERATIVE REGION '+reference(r['evidence']))
    for ev in facts['procedure']['actual_quote_evidence'][:2]:out.append('QUOTE PROCEDURE '+reference(ev))
    out.append('OVERLAYS '+str({k:(v['value'],v['reason']) for k,v in facts['overlays'].items()}))
    out.append('UNCERTAINTY '+str(facts['uncertainty'])+'; '+str(facts['scan']['input_completeness']))
    return '\n'.join(out)
