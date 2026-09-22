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
MANDATORY_END = re.compile(r'(?:실적|경험).{0,200}(?:업체(?!명)|자격(?!등록)|있어야|보유한자|있는자|(?:있는|보유한|갖춘)(?:법인|단체|기관|사업자)(?!명))|(?:수행|완료|납품)\)?한업체(?!명)')


def experience_text(text):
    # Tokenize before removing spaces: 사실적/현실적 are adjectives, whereas
    # 공사실적 and 행사실적 are genuine compound experience nouns.
    return compact(re.sub(r'(?<![가-힣])(?:사실|현실)적(?=$|[^가-힣]|인|으로)', '', text))


def item_depth(text):
    """Structural markers only; a monetary literal is not an item number."""
    text = text.lstrip()
    m = re.match(r'(\d+(?:[.-]\d+)*)[.](?!\d)', text)
    if m:
        return len(re.findall(r'[.-]', m[1]))
    if re.match(r'[가-하][.)]', text):
        return 1
    if re.match(r'\(?\d+\)|\[\d+\]|[①-⑳]|[A-Za-zⅰ-ⅹⅠ-Ⅹ]+[.)]', text):
        return 2
    if re.match(r'[○●•·□■◆◇❍◦▫‣∙․*\-]|[ㅇo](?=\s)', text):
        return 3
    if text.startswith('※'):
        return 4
    return None


def condition_units(doc, di, *, consumer=False):
    """Join wrapped clauses, retaining exact source spans and hard boundaries."""
    source = list(lines(doc, di))
    index = 0
    while index < len(source):
        first = source[index]
        end = index + 1
        if not heading_role(compact(first['text'])) and not re.search(r'[|\t]', first['text']):
            while end < min(index + 8, len(source)):
                prev, nxt = source[end-1], source[end]
                gap = doc['text'][prev['end']:nxt['start']]
                previous = experience_text(prev['text'])
                if (gap.count('\n') > 1 or gap.count('\r') > 1
                        or item_depth(nxt['text']) is not None
                        or heading_role(compact(nxt['text']))
                        or re.search(r'[|\t]', nxt['text'])
                        or re.search(r'[.!?。;]\s*$', prev['text'])
                        or (PAST.search(previous) and MANDATORY_END.search(previous))
                        or nxt['end'] - first['start'] > 1200):
                    break
                end += 1
        joined = span(doc, di, first['start'], source[end-1]['end'])
        n = experience_text(joined['text'])
        from .performance_predicates import mandatory, office_clause
        if end > index + 1 and (PAST.search(n) and MANDATORY_END.search(n)
                or consumer and (mandatory(n) or office_clause(n))):
            yield joined
        else:
            yield from source[index:end]
        index = end


def unresolved_requirement(n):
    """Reject a nonasserted substantive condition, not a certificate waiver."""
    if re.search(r'[<〈\[]삭제[>〉\]]', n):
        return True
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
    if certificate_scope_reference(n):
        return 'forms'
    prefix = bool(re.match(r'^(?:\d+(?:[-.]\d+)*[.)]?|[가-하][.)]|[IVXⅠⅡⅢⅣⅤⅥ]+[.)]?|[□■◆◇○])', n))
    short = len(n) <= 95
    title = re.sub(r'\([^()]*\)$', '', n)
    if short and ELIG.search(title) and not re.search(r'등록규정|시행령|등록한|갖춘|문의|법률',title) and (prefix or title.endswith(('자격','조건'))):
        return 'eligibility'
    if short and ((SCORE.search(n) and (prefix or '배점' in n or '평가' in n)) or (PAST.search(n) and re.search(r'\d+점',n))):
        return 'scoring'
    if short and FORM.search(n):
        return 'forms'
    if len(n) <= 65 and re.match(r'^\d+[.](?!\d)', n):
        return 'other'
    return None


# A named class of public bodies can be the counterparty of the required prior
# work.  Public education bodies (교육청·교육지원청·국공립 학교/유치원) are such a
# class; the current-purchaser vocabulary above did not name them.  Keep this
# separate from ``public`` so that widening it cannot turn a mandatory
# condition into the "public or private accepted" negative.
PUBLIC_EDUCATION = (r'교육청|교육지원청|각급학교|(?:국|공|국공)립[가-힣]{0,6}(?:학교|유치원)')
OTHER_PURCHASER_EXCLUDED = (r'(?:민간|민자|일반기업|사립|그밖의기관|그외의기관|'
                            r'다른(?:기관|교육시설|시설|학교))'
                            r'[^。\n]{0,40}(?:불인정|인정하지|인정되지|제외|포함하지|받지않)')


def purchaser(n):
    private = re.search(r'민간|민자|일반기업', n)
    excludes = bool(re.search(r'(?:민간|민자|일반기업).{0,25}(?:불인정|인정하지|제외)', n)
                    or re.search(OTHER_PURCHASER_EXCLUDED, n))
    public = re.search(r'국가기관|국가[,·ㆍ및]|지방자치단체|지자체|정부투자기관|공공기관|대학병원', n)
    if private and re.search(r'각각|모두보유',n):
        return 'public_private_conjunction_unresolved'
    if private and not excludes:
        return 'public_or_private_accepted' if public else 'private_accepted'
    # An institution reference must modify prior commissioning/delivery, not
    # merely certify documents or identify the current purchaser/address.
    relation = re.search(r'(?:국가기관|국가|지방자치단체|정부투자기관|공공기관|대학병원|\[수요기관\([^]]+\])[^。\n]{0,75}(?:발주|시행한|납품한|통근버스운행실적)', n)
    education = re.search(rf'(?:{PUBLIC_EDUCATION})'
                          r'[^。\n]{0,75}(?:발주|시행한|납품한|체결한|수행한|완료한|계약하여)', n)
    # A mandatory past-performance class can also be narrowed to one specific
    # institutional beneficiary even when the clause says "students of..."
    # rather than "ordered by...".  This is a restriction on acceptable prior
    # work, not a current delivery-location mention.
    institutional_beneficiary = re.search(
        r'(?:유치원|초등학교|중학교|고등학교|중고등학교|초·중·고등학교)'
        r'[^\n]{0,35}(?:학생|원생)\s*대상', n)
    if (relation or education or institutional_beneficiary
            or (excludes and (public or re.search(PUBLIC_EDUCATION, n)))):
        return 'specific_purchaser_required'
    return 'unspecified'


def project_prices(record):
    from .prices import project_prices as shared_prices
    prices = shared_prices(record)
    for price in prices.values():
        # A metadata-only one-won sentinel is not a project total. Preserve the
        # observations for audit; a comparable source amount still takes priority.
        if (price['meta']['won'] == 1 and price['candidate_values_won'] == [1]
                and not any(b['price_role'] == 'project_total_candidate' for b in price['body'])):
            price.update(status='unknown', value_won=None, candidate_values_won=[],
                         unresolved_placeholder=True)
    return prices


def experience_permission(n):
    """Bind denial to experience eligibility, not to certificate submission."""
    return bool(re.search(
        r'(?:실적|경험).{0,25}(?:제한없|제한하지|관계없이|무관하게|없어도|없는업체도)|'
        r'(?:실적|경험).{0,40}(?:참가자격|자격요건)(?:으로|은|는|이)?'
        r'(?:요구하지(?:않|아니)|제한을두지(?:않|아니)|아(?:니|닙))|'
        r'(?:실적|경험)(?!증명서|확인서|표).{0,35}(?:참가자격|자격요건)(?:에|으로)'
        r'(?:반영|심사|평가)하지(?:않|아니)', n))


def document_substitution_only(n):
    return bool(re.search(r'(?:제출서류|증명서|확인서).{0,300}(?:대체|갈음)', n)
                and not re.search(r'(?:실적|경험).{0,120}(?:있어야|보유한|갖춘|있는업체|이상인업체)', n))


def certificate_scope_reference(n):
    """A certificate refers back to eligibility; it does not create a gate."""
    return bool(re.search(r'(?:실적|경험)(?:은|는).{0,30}공고(?:문)?.{0,30}'
                          r'(?:제시|명시|정한).{0,60}(?:조건|기준|범위).{0,20}'
                          r'(?:부합|충족|해당)', n)
                and not re.search(r'(?:실적|경험).{0,100}(?:보유한|있는|갖춘)(?:업체|자)', n))


def performance_facts(record, *, consumer=False):
    """Return facts + nullable per-item overlays; never inspect a record ID."""
    candidates, regions, procedures, exclusions = [], [], [], []
    scanned = 0
    for di, doc in enumerate(record['docs']):
        scanned += len(doc['text'])
        role, heading = 'unknown', None
        eligibility_heading, forms_depth = None, None
        current_depth = 0
        doclines = list(condition_units(doc, di, consumer=consumer))
        for li, ev in enumerate(doclines):
            n = experience_text(ev['text'])
            depth = item_depth(ev['text'])
            new_role = heading_role(n)
            from .performance_predicates import eligibility_heading as consumer_eligibility_heading
            if consumer and consumer_eligibility_heading(n):
                new_role = 'eligibility'
            elif consumer:
                from .performance_predicates import evaluative_role
                new_role = evaluative_role(n) or new_role
            # A documents note nested in eligibility ends at the next peer or
            # parent item. A top-level forms/scoring section never inherits it.
            if (role == 'forms' and eligibility_heading is not None
                    and depth is not None and forms_depth is not None
                    and depth <= forms_depth and new_role != 'forms'):
                role, heading = 'eligibility', eligibility_heading
                forms_depth = None
            if new_role:
                if new_role == 'eligibility':
                    eligibility_heading = ev
                elif (new_role == 'forms' and role == 'eligibility'
                      and (depth is None or depth > (item_depth(heading['text']) or 0))):
                    # Bullets within the same documents list retain its role;
                    # a parent item (or a peer numbered subsection) ends it.
                    forms_depth = (min(current_depth, depth - 1) if depth >= 3 else depth) if depth is not None else current_depth
                elif new_role != 'forms' or role == 'eligibility' or depth == 0:
                    eligibility_heading, forms_depth = None, None
                role, heading = new_role, ev
            if depth is not None:
                current_depth = depth
            if re.search(r'수의(?:계약)?(?:견적|계약)|소액수의|견적(?:서)?제출(?:안내공고|및계약방법|대상용역)', n) and not re.search(r'참고|준용|경우|법률|시행령', n):
                procedures.append(ev)
            permission = experience_permission(n)
            if consumer:
                from .performance_predicates import eligibility_permission
                permission = permission or eligibility_permission(n)
            if permission and role == 'eligibility':
                exclusions.append(ev)
            # A past purchaser/facility's location is not a restriction on the
            # bidder's current office. Preserve that distinction for v8.
            explicit_province_bidder = re.search(
                r'(?:특별시|광역시|특별자치도|경기|경북|경남|경상|강원|충청|전라|제주)'
                r'(?:에|내에)?소재(?:한|하고있는|해있는)?[^\n]{0,80}(?:업체|사업자|갖춘자)', n)
            from .performance_predicates import office_anchor
            if role == 'eligibility' and (
                    re.search(r'본점|본사|주된영업소|주된사무소', n)
                    or explicit_province_bidder
                    or consumer and office_anchor(n)):
                # Keep anonymous attributes out of the free-text place matcher.
                # A malformed token is not a geographic witness just because
                # one of its attributes contains a recognizable province.
                literal = province_projection(n, allowed_provinces=())
                place = re.search(r'(?:특별|광역)시|특별자치도|경기|경북|경남|경상|강원|충청|전라|제주', literal)
                if consumer:
                    from .performance_predicates import additional_region
                    place = place or additional_region(literal)
                place = place or any(not token.errors and (
                    token.kind == 'region' or token.kind == 'institution' and token.value == '기초자치단체'
                    and re.match(r'(?:관할(?:구역)?|행정구역|지역)?내', n[token.end:]))
                    for token in anonymous_tokens(n))
                operative = re.search(r'업체|사업자|제한|두고|둔|갖춘자|있는자', n)
                if consumer:
                    from .performance_predicates import region_obligation, office_location_duty, location_predicate
                    operative = operative or region_obligation(n) or office_location_duty(n)
                    # A clause wrapped over a blank line keeps its locative
                    # predicate on the next unnumbered line of the same item.
                    if place and not operative and not re.search(r'[.。]\s*$|니다\s*$', ev['text']):
                        for nx in doclines[li+1:li+3]:
                            if item_depth(nx['text']) is not None or heading_role(compact(nx['text'])):
                                break
                            if location_predicate(experience_text(nx['text'])):
                                operative = True
                                break
                neg = re.search(r'지역제한없|소재지.{0,15}(?:무관|관계없)|소재지.{0,10}제한하지', n)
                if place and operative and not neg:
                    regions.append({'evidence': ev, 'governing_heading': heading, 'status': 'operative'})
            from .performance_predicates import completed_experience
            if not (PAST.search(n) or consumer and completed_experience(n)):
                continue
            local_score = bool(re.search(r'배점|\d+(?:\.\d+)?점|평가한다|평가하며|평가합니다|실적으로평가', n))
            local_form = bool(re.search(r'실적증명서.{0,20}(?:[1-9]부|서식)|실적만기재|실적은.{0,20}기재|기재한|잔존구성원|집행실적|배출실적', n))
            # A documents substitution list may include both experience and
            # a bidder-registration certificate. Neither establishes a minimum
            # prior-performance gate; a separate substantive gate stays eligible.
            if document_substitution_only(n) or certificate_scope_reference(n):
                local_form = True
            if consumer:
                from .performance_predicates import chronological_experience_form
                local_form = local_form or chronological_experience_form(n)
            from .performance_predicates import mandatory as consumer_mandatory, noun_alternative
            positive_gate = bool(MANDATORY_END.search(n) or consumer and consumer_mandatory(n))
            nonasserted = unresolved_requirement(n)
            actual_gate = role == 'eligibility' and positive_gate and not local_score and not local_form and not nonasserted
            qualitative_gate = bool(re.search(r'실적이우수|풍부한실적|실적이풍부', n))
            vague = qualitative_gate or bool(re.search(r'업체또는|보유하거나', n)
                and not (consumer and noun_alternative(n)))
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
        elif consumer and work=='물품(내자)' and estimate is not None and not quote and estimate < NOTICE_WON:
            # Goods may be restricted only by same-kind manufacturing records at or
            # above the notice amount (국가 영 제21조①3호·규칙 제25조②1호, 지방 영
            # 제20조①3호); a purchase has no record basis at all. Below it, a record
            # the bidder must hold (…실적이 있는/보유한 업체) is the item-2
            # restriction; a mere mention of 실적 or an alternative route is not.
            from .competitive_performance import _BIDDER
            held=[c['evidence'] for c in mandatory if _BIDDER.search(compact(c['evidence']['text']))]
            if held:
                decide(2,1,'mandatory_goods_experience_below_supplied_notice',held)
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


def missing_evidence(record, item, facts):
    """Quote the sole confirmed requirement, optionally with nearby region."""
    if item not in (2, 8):
        return None
    mandatory = [c for c in facts['candidates']
                 if c['status'] in ('mandatory', 'qualitative_mandatory')]
    if len(mandatory) != 1 or facts['explicit_no_experience_restriction']:
        return None
    ev = mandatory[0]['evidence']
    di, lo, hi = ev['doc_index'], ev['start'], ev['end']
    regions = facts['operative_regions'] if item == 8 else []
    combined = []
    for region in regions:
        source = region['evidence']
        if source['doc_index'] == di:
            start, end = min(lo, source['start']), max(hi, source['end'])
            if end - start <= 500:
                combined.append((end - start, start, end))
    if combined:
        _, lo, hi = min(combined)
    quote = record['docs'][di]['text'][lo:hi]
    if not quote or len(quote) > 500 or quote.startswith(('=', '+', '@')):
        return None
    return {'evidence': quote, 'source_range': (di, lo, hi),
            'region_included': bool(combined),
            'region_sources': [r['evidence'] for r in regions]}


def validate_model_witness(record, row, item, facts):
    """A scoring/form quote cannot support an eligibility violation.

    This rejects only the model's supplied proof, not other source conditions.
    The caller applies independent positive source rules afterwards. Unknown is
    emitted as0 and never recorded as a full-document absence certificate.
    """
    quote = row.get(f'e{item}', '')
    if row.get(f'v{item}') not in (1, '1') or not isinstance(quote, str):
        return None
    price_kinds = ('estimated_price', 'budget') if item == 3 else ('estimated_price',) if item == 2 else ()
    if any(facts['prices'][kind].get('unresolved_placeholder') for kind in price_kinds):
        return {'item': item, 'value': 0, 'evidence': '', 'semantic_value': None,
                'reason': 'metadata_placeholder_cannot_establish_performance_price_predicate',
                'source': 'performance_witness_validation', 'absence_verified': False,
                'rejected_witness': quote}
    if not quote.strip():
        # Missing public evidence cannot normally disprove a model judgment.
        # One bounded exception is source-complete: the extractor found a
        # scored/form-only monetary condition, no operative qualification
        # candidate at all, and the model supplied no witness.
        operative={'mandatory','qualitative_mandatory','ambiguous_eligibility',
                   'qualification_note','unresolved_modality'}
        evaluative=[c for c in facts['candidates'] if c['status'] == 'explicit_permission'
                    or c['status'] in ('scoring','forms_or_submission')
                    and (c['required_money'] is not None or c['purchaser']!='unspecified')]
        if (evaluative and not any(c['status'] in operative for c in facts['candidates'])
                and facts['overlays'][f'v{item}']['value'] is None):
            return {'item': item, 'value': 0, 'evidence': '', 'semantic_value': None,
                    'reason': 'unsupported_model_positive_with_only_scoring_or_form_performance',
                    'source': 'performance_witness_validation', 'absence_verified': False,
                    'rejected_witness': '',
                    'occurrences': [{'purposes':[{'status':c['status'],
                        'evidence':c['evidence'],'governing_heading':c['governing_heading']}]
                        for c in evaluative}]}
        return None
    # A form can restate a substantive eligibility condition. Do not reject it
    # merely because a section heading or another line describes a form.
    if any(MANDATORY_END.search(experience_text(line)) and not experience_permission(experience_text(line))
           and not document_substitution_only(experience_text(line))
           and not certificate_scope_reference(experience_text(line))
           for line in quote.splitlines() if line.strip()):
        return None
    occurrences = []
    for di, doc in enumerate(record['docs']):
        for match in re.finditer(re.escape(quote), doc['text']):
            candidates = [c for c in facts['candidates'] if c['evidence']['doc_index'] == di
                and c['evidence']['start'] < match.end() and c['evidence']['end'] > match.start()]
            purposes = [c for c in candidates if c['status'] in ('scoring','forms_or_submission','explicit_permission')]
            unsafe = [c for c in candidates if c['status'] not in ('scoring','forms_or_submission','explicit_permission')
                      and (c['required_money'] is not None or c['purchaser']!='unspecified'
                           or MANDATORY_END.search(experience_text(c['evidence']['text'])))]
            # A long model excerpt may also overlap a bare form field such as
            # "주요 사업실적 1부" that the extractor leaves unresolved.
            # Such a title cannot turn an otherwise scored/form-only excerpt
            # into an eligibility condition.  A substantive unresolved clause
            # (money, purchaser or bidder predicate) still blocks rejection.
            if not purposes or unsafe:
                return None
            occurrences.append({'evidence': span(doc, di, match.start(), match.end()),
                'purposes': [{'status': c['status'], 'evidence': c['evidence'],
                              'governing_heading': c['governing_heading']} for c in purposes]})
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
