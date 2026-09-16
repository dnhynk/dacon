"""CPU-only, label/ID-free, conservative performance facts prototype.

All offsets are half-open Python character offsets into unmodified doc text.
No absence-based negative decisions. Policy constants refer to supplied law,
not an asserted current-law service. See legal_sources.json and report.
"""
from __future__ import annotations

from .anonymized_tokens import anonymous_tokens, province_projection

from .legal_context import applicable_law
import re
import unicodedata

from .comparison import _WON, won_value

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
MONEY = re.compile(_WON.pattern + r'|(?<![\d.,])' + NUM + r'억(?![\d원조억만천백십])')


def won(raw):
    text = str(raw).strip()
    value = won_value(text if '원' in text else text + '원')
    if value is None or value != value.to_integral_value():
        raise ValueError(raw)
    return int(value)


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
        source = subspan(doc, ev['doc_index'], ev['start'], pos, m.start(), m.end())
        try:
            # Normalization finds candidates; the original spacing still owns
            # the literal. Joining damaged numeric columns must not invent 23.
            value = won(unicodedata.normalize('NFKC', source['text']))
        except ValueError:
            value = None  # Preserve the failed observation without aborting the notice.
        out.append({'won': value, 'comparator': comparator,
                    'parse_status': 'exact' if value is not None else 'unresolved_unit_expression',
                    'vat': vat(n[max(0,m.start()-12):m.end()+27]),
                    'binding': 'current_project' if project else 'experience_candidate',
                    'evidence': source})
    return out


ELIG = re.compile(r'(?:입찰|견적(?:서)?제출|제안(?:\(입찰\))?)(?:참가|참여)?자격|참가자격|입찰참가조건')
SCORE = re.compile(r'배점|정량(?:적)?평가|평가기준|평가항목|평가방법|적격심사|수행능력평가|기술능력평가')
FORM = re.compile(r'서식\s*\d|붙임\d|서식[〉>\]]|제출서류|제출목록|작성요령|작성지침|증명서양식')
PAST = re.compile(r'(?<!현)실적|수행경험|납품경험|최근\d+년.{0,240}(?:수행|완료|납품)')
# A flattened certificate's "업체명" field names its submitter; it is not the
# bidder subject of an experience requirement. Keep genuine "실적 보유 업체"
# clauses, including those restated in a form, eligible for substantive review.
MANDATORY_END = re.compile(r'(?:실적|경험).{0,200}(?:업체(?!명)|자격|있어야|보유한자|있는자)|(?:수행|완료|납품)\)?한업체(?!명)')


def unresolved_requirement(n):
    """Reject a nonasserted substantive condition, not a certificate waiver."""
    if re.search(r'예시|가정|참고용|주장|단정할수없|확인불가', n):
        return True
    # The predicate is about the bidder's experience. A preceding exemption
    # from submitting a certificate does not remove a later actual condition.
    for m in MANDATORY_END.finditer(n):
        tail = n[m.end():]
        if re.search(r'(?:요건|조건|제한|의무)(?:은|는|을|를)?(?:삭제|철회|폐지|면제)|'
                     r'(?:일|이어야할)?필요(?:가|는|도)?없|'
                     r'(?:삭제|철회|폐지)(?:한다|합니다|함|된|되었)', tail):
            return True
    return False


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
    from .prices import project_prices as shared_prices
    return shared_prices(record)


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
            explicit_province_bidder = re.search(
                r'(?:특별시|광역시|특별자치도|경기|경북|경남|경상|강원|충청|전라|제주)'
                r'(?:에|내에)?소재(?:한|하고있는|해있는)?[^\n]{0,80}(?:업체|사업자|갖춘자)', n)
            if role == 'eligibility' and (
                    re.search(r'본점|본사|주된영업소|주된사무소', n)
                    or explicit_province_bidder):
                # Keep anonymous attributes out of the free-text place matcher.
                # A malformed token is not a geographic witness just because
                # one of its attributes contains a recognizable province.
                literal = province_projection(n, allowed_provinces=())
                place = re.search(r'(?:특별|광역)시|특별자치도|경기|경북|경남|경상|강원|충청|전라|제주', literal)
                place = place or any(not token.errors and (
                    token.kind == 'region' or token.kind == 'institution' and token.value == '기초자치단체'
                    and re.match(r'(?:관할(?:구역)?|행정구역|지역)?내', n[token.end:]))
                    for token in anonymous_tokens(n))
                operative = re.search(r'업체|사업자|제한|두고|둔|갖춘자|있는자', n)
                neg = re.search(r'지역제한없|소재지.{0,15}(?:무관|관계없)|소재지.{0,10}제한하지', n)
                if place and operative and not neg:
                    regions.append({'evidence': ev, 'governing_heading': heading, 'status': 'operative'})
            if not PAST.search(n):
                continue
            local_score = bool(re.search(r'배점|\d+(?:\.\d+)?점|평가한다|평가하며|실적으로평가', n))
            local_form = bool(re.search(r'실적증명서.{0,20}(?:[1-9]부|서식)|실적만기재|실적은.{0,20}기재|기재한|잔존구성원|집행실적|배출실적', n))
            positive_gate = bool(MANDATORY_END.search(n))
            nonasserted = unresolved_requirement(n)
            actual_gate = role == 'eligibility' and positive_gate and not local_score and not local_form and not nonasserted
            qualitative_gate = bool(re.search(r'실적이우수|풍부한실적|실적이풍부', n))
            vague = qualitative_gate or bool(re.search(r'업체또는|보유하거나', n))
            qualifier_note = n.startswith('※') and bool(re.search(r'공동수급체중|대표사를제외|조건만충족|실적증명서는.{0,25}제출',n))
            if permission:
                status = 'explicit_permission'
            elif nonasserted:
                status = 'unresolved_modality'
            elif qualifier_note:
                status = 'qualification_note'
            elif actual_gate and qualitative_gate:
                # The threshold cannot support amount or purchaser arithmetic,
                # but it is still an affirmative experience qualification. It
                # can therefore establish the experience side of item 8.
                status = 'qualitative_mandatory'
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
            if any(a['won'] is None for a in money):
                req = []
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
    law=applicable_law(record)
    work=meta.get('업무구분')
    prices=project_prices(record)
    estimate_price=prices['estimated_price']
    estimate=estimate_price['value_won']
    budget=prices['budget']['value_won']
    mandatory=[c for c in candidates if c['status']=='mandatory']
    qualitative_mandatory=[c for c in candidates if c['status']=='qualitative_mandatory']
    ambiguous=[c for c in candidates if c['status']=='ambiguous_eligibility']
    quote=bool(procedures)
    blockers=[]
    if ambiguous: blockers.append('vague_experience_eligibility')
    if qualitative_mandatory: blockers.append('qualitative_experience_threshold_not_numeric')
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
    # The necessary price predicate is independent of whether the experience
    # clause parser recognized an operative requirement. At/above the supplied
    # ceiling item 2 cannot apply, including goods. Unknown authority thresholds
    # and conflicting prices still abstain.
    from .prices import in_band
    below_notice = in_band(estimate_price, upper=NOTICE_WON)
    if (law in ('국가계약법', '지방계약법') and work in ('일반용역', '물품(내자)')
            and below_notice is False):
        decide(2,0,'known_estimate_not_below_supplied_notice',[])
    if valid and mandatory:
        es=[c['evidence'] for c in mandatory]
        if work=='일반용역' and estimate is not None and not quote:
            if estimate < NOTICE_WON:
                decide(2,1,'mandatory_service_experience_below_supplied_notice',es)
            elif below_notice is False:
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
    if (valid and qualitative_mandatory and regions and not quote
            and work == '일반용역'):
        decide(8, 1, 'qualitative_mandatory_service_experience_and_operative_region',
               [c['evidence'] for c in qualitative_mandatory]
               + [r['evidence'] for r in regions])
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


def validate_model_witness(record, row, item, facts):
    """A scoring/form quote cannot support an eligibility violation.

    This rejects only the model's supplied proof, not other source conditions.
    The caller applies independent positive source rules afterwards. Unknown is
    emitted as0 and never recorded as a full-document absence certificate.
    """
    quote = row.get(f'e{item}', '')
    if row.get(f'v{item}') not in (1, '1') or not isinstance(quote, str) or not quote.strip():
        return None
    # A form can restate a substantive eligibility condition. Do not reject it
    # merely because a section heading or another line describes a form.
    if MANDATORY_END.search(compact(quote)):
        return None
    occurrences = []
    for di, doc in enumerate(record['docs']):
        for match in re.finditer(re.escape(quote), doc['text']):
            candidates = [c for c in facts['candidates'] if c['evidence']['doc_index'] == di
                and c['evidence']['start'] < match.end() and c['evidence']['end'] > match.start()]
            if not candidates or any(c['status'] not in ('scoring', 'forms_or_submission') for c in candidates):
                return None
            occurrences.append({'evidence': span(doc, di, match.start(), match.end()),
                'purposes': [{'status': c['status'], 'evidence': c['evidence'],
                              'governing_heading': c['governing_heading']} for c in candidates]})
    if not occurrences:
        return None
    return {'item': item, 'value': 0, 'evidence': '', 'semantic_value': None,
            'reason': 'model_witness_has_only_scoring_or_form_purpose',
            'source': 'performance_witness_validation', 'absence_verified': False,
            'rejected_witness': quote, 'occurrences': occurrences}


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
