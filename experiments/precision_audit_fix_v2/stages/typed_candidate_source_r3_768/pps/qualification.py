"""Per-notice purchase and qualification facts, using the supplied catalog only.

The caller supplies this notice and its model-based row in memory. No history,
identifier rules, labels, external documents, mutable parser hooks, or file I/O.
This module does not reproduce an entire historical research pipeline by itself.
"""
from __future__ import annotations

import copy
import re

from . import sme
from .data import clean_evidence
from .products import CODE, ProductFacts, normalized_map, scope_spans, purchase_coverage
from .prices import project_prices

TAIL = re.compile(
    r'간제한경쟁입찰에따라(?:조달)?계약을체결하여야(?:한다|합니다|함)[.。]?$')
ARTICLE = r'제\d+조(?:의\d+)?(?:제\d+항)?(?:제\d+호)?(?:에따른|에의한)'
OR_BRIDGE = re.compile(r'(?:또는|혹은)(?:' + ARTICLE + r')?')
# These signal a separate entity branch, hypothetical/quoted rule, withdrawal,
# or optional condition. They are not transformed into a proved requirement.
UNRESOLVED = re.compile(
    r'비영리|벤처|창업|특별법인|협동조합|중견기업|대기업|비중소|'
    r'경우|예외|다만|참고|예시|인용|삭제|철회|면제|선택|제외|'
    r'않|아니|아닌|없어도|할수|할수도|가능|조건부')



def _base_repair(record, original):
    inventory, sections, quotes, exceptions, declarations = copy.deepcopy(original)
    for entry in inventory:
        if entry['section_role'] != 'eligibility':
            continue
        if entry['status'] not in ('mandatory_eligibility', 'incidental_or_unresolved'):
            continue
        raw = entry['evidence']['text']
        n = sme.mask_laws(sme.norm(raw))
        if (entry['direct_production'] and entry['codes']
                and re.search(r'직접생산.{0,260}(?:소지|보유)(?:한)?(?:업체|자)[.。]?$', n)
                and not UNRESOLVED.search(n)):
            entry['direct_requirement'] = True
            entry['status'] = 'mandatory_eligibility'
            entry['postprocessing_repair'] = 'certificate_possession_noun_in_eligibility'
        # This identifies the qualified entity, independently of certificates.
        entity = re.search(r'(?:요건|자격)을?갖춘(중소기업자)(?:$|[.,。])', n)
        if entry['size'] is None and entity and entry['status'] == 'mandatory_eligibility':
            entry['size'] = {'allowed': sorted(sme.class_set(entity[1])),
                'basis': 'eligible_entity', 'connective': 'single',
                'certificate_phrases': [], 'commercial_only': True}
            entry['postprocessing_repair'] = 'qualified_entity_after_operative_predicate'
        # A submission date alone is insufficient. Require the certificate,
        # pre-opening holding deadline, and explicit disqualification together
        # in one original line, without waivers or optional alternatives.
        if entry['size'] is None or entry['alternative_size_branch_unresolved']:
            continue
        for line in re.finditer(r'[^\r\n]+', raw):
            ln = sme.norm(line.group())
            if not sme.CERT.search(sme.mask_laws(ln)):
                continue
            requirement = re.search(r'(?:개찰|입찰마감)(?:일)?전까지확인서미소지시(?:未|미|무)자격자로처리(?:합니다|한다|함)', ln)
            if not requirement or re.search(r'없어도|면제|불필요|처리하지|경우에한|(?:또는|혹은)(?:벤처|창업)', ln):
                continue
            entry['status'] = 'mandatory_eligibility'
            entry['postprocessing_repair'] = 'pre_opening_nonholder_disqualification'
            entry['holding_requirement_evidence'] = sme.evidence(record,
                entry['evidence']['doc_index'], entry['evidence']['start'] + line.start(),
                entry['evidence']['start'] + line.end())
            entry['timing_roles'] = {
                'holding': 'required_before_opening_or_bid_deadline',
                'submission': 'separate_not_used_to_prove_holding',
                'actual_bidder_certificate': 'not_supplied_not_verified'}
            break
    return inventory, sections, quotes, exceptions, declarations


def repair_inventory(record, original):
    result = _base_repair(record, original)
    for entry in result[0]:
        if (entry['section_role'] != 'eligibility'
                or entry['status'] != 'incidental_or_unresolved'
                or entry['other_entity_options']
                or entry['alternative_size_branch_unresolved']
                or entry['direct_production']):
            continue
        raw = entry['evidence']['text']
        n = re.sub(r'\s+', '', sme.mask_laws(sme.norm(raw)))
        if UNRESOLVED.search(n) or re.match(r'^[※"“『「\-]', n):
            continue
        tail = TAIL.search(n)
        if not tail or sme.CERT.search(n):
            continue
        prefix = n[:tail.start()]
        entities = list(re.finditer(sme.CLASS + r'(?:자)?', prefix))
        if not entities or entities[-1].end() != len(prefix):
            continue
        # The entire explicit entity list must be a single noun or a pure OR
        # chain. Do not drop an unfamiliar conjunct and keep its final noun.
        bridges = [prefix[a.end():b.start()] for a, b in zip(entities, entities[1:])]
        if any(not OR_BRIDGE.fullmatch(b) for b in bridges):
            continue
        allowed = set().union(*(sme.class_set(e.group()) for e in entities))
        entry['status'] = 'mandatory_eligibility'
        entry['size'] = {
            'allowed': sorted(allowed), 'basis': 'eligible_entity',
            'connective': 'OR' if bridges else 'single',
            'certificate_phrases': [], 'commercial_only': True,
            'entity_phrases': [e.group() for e in entities],
            'modality': 'mandatory_restricted_competition_contract',
        }
        entry['postprocessing_repair'] = 'operative_contract_and_entire_entity_OR'
    return result


FLOOR = 100_000_000
NOTICE = 230_000_000
ABSENCE = {10, 11, 16, 18, 20}
EVENT = re.compile(r'(?:행사|축제|포럼|박람회|전시회|회의).{0,65}(?:기획|대행|운영|위탁)')
SOFTWARE = re.compile(r'(?:정보시스템|경영정보시스템|정보인프라|소프트웨어|전산시스템|출입통제체계).{0,60}(?:구축|개발|유지보수|유지관리|운영|갱신)')


def norm(text):
    return normalized_map(str(text))[0]


def price(value):
    return value if type(value) in (int, float) and 0 <= value < float('inf') else None


def inventory(record):
    # Per-call parser injection keeps extraction independent across threads.
    original_heading = sme.heading
    def recognize(n):
        if (re.match(r'^\d+(?:\.\d+)+[.]?[「『｢]?', n)
                and re.search(r'업종코드|직접생산|중소기업|소상공인|주된영업소|본점', n)):
            return None  # A decimal-numbered requirement continues its section.
        if re.match(r'^(?:[|○□■\d.)-])*입찰참가자격[:：]?(?:다음|아래|각호)', n) and len(n) < 130:
            return 'eligibility'
        role = original_heading(n)
        # A numbered industry registration requirement is a list entry,
        # not a new heading that ends the surrounding eligibility section.
        if (role == 'other' and re.match(r'^\d+[.)]', n)
                and (re.search(r'(?:업종코드|업종번호).{0,12}\d{4}.{0,80}등록(?:한|된|을필한)업체', n)
                     or re.search(r'우선조달계약대상으로.{0,90}(?:소기업|소상공인)', n))):
            return None
        # A wrapped numbered qualification clause is not a new section.
        # Keep the surrounding role until a genuine section heading appears.
        statutory_clause = (
            re.match(r'^\d+[.)][「『｢]?', n)
            and re.search(r'중소기업기본법|소상공인기본법|중소기업제품구매촉진|중소기업범위및확인', n)
            and not re.search(r'목차|예외사항|참고사항', n))
        if role == 'other' and statutory_clause:
            return None
        return role
    result = repair_inventory(record, sme.extract_inventory(record, heading_fn=recognize))
    return result


def catalog_condition(note, estimate, budget):
    if re.fullmatch(r'소프트웨어\s*진흥법\s*제\s*48\s*조\s*적용', note.strip()):
        return {'kind': 'software_article48_SME_only_band', 'basis': 'meta_project_budget',
                'value_won': budget, 'operator': '<', 'ceiling_won': 2_000_000_000,
                'status': 'unknown' if budget is None else 'met' if budget < 2_000_000_000 else 'not_met'}
    result = ProductFacts.condition(note, estimate)
    if result.get('kind') == 'estimated_price_ceiling':
        result.update(basis='meta_estimated_price', value_won=estimate)
    return result


def purchase_scope(record, pf, entries, declarations):
    meta = record.get('meta', {})
    prices = project_prices(record)
    estimate, budget = prices['estimated_price']['value_won'], prices['budget']['value_won']
    scopes = scope_spans(record, max_spans=1000, char_limit=1_000_000)
    # Certificate, registration and purchase identities remain separate.
    meta_text = str(meta.get('세부품명번호목록') or '')
    meta_codes = set(CODE.findall(meta_text))
    declared = {code for declaration in declarations for code in declaration['codes']}
    codes = meta_codes | declared
    identity = [declaration['evidence'] for declaration in declarations]
    exact = set()
    for span in scopes:
        text = norm(span['text'])
        for code, product in pf.products.items():
            name = norm(product['세부품명'])
            if code and len(name) >= 5 and name in text:
                exact.add(code)
                identity.append(span)
    uncertainty = []
    if meta_codes and declared and not meta_codes <= declared:
        uncertainty.append('metadata_and_body_purchase_codes_conflict')
    if meta_codes and declared - meta_codes:
        uncertainty.append('additional_declared_purchase_components')
    if codes and exact - codes:
        uncertainty.append('additional_named_catalog_purchase')
    coverage = purchase_coverage(record, codes | exact)
    additional_counts = [int(m.group(1)) for s in scopes
                         for m in re.finditer(r'(?:외|등)\s*(\d+)\s*(?:종|품목)', s['text'])]
    if coverage['whole_purchase_coverage_unresolved'] or (additional_counts and max(additional_counts) > len(codes | exact)):
        uncertainty.append('explicit_multiple_items_not_all_identified')
    notices = '\n'.join(d['text'] for d in record['docs'] if d['type'] == '공고문')
    if re.search(r'국방규격|기동.{0,15}총포|군용규격', notices):
        uncertainty.append('unnumbered_defense_catalog_category')

    mechanism = None
    family_candidates = False
    if codes:
        mechanism = 'provided_metadata_and_declared_purchase_codes'
        # The supplied field pairs purchase names and codes. A bare arbitrary
        # code without any named purchase remains unresolved.
        named_meta = bool(re.search(r'[가-힣a-zA-Z]{2}', CODE.sub('', meta_text)))
        if not named_meta and not declarations:
            uncertainty.append('purchase_name_unresolved')
    elif exact:
        codes = exact
        mechanism = 'exact_catalog_purchase_name'
    else:
        task = '\n'.join(norm(span['text']) for span in scopes)
        if meta.get('업무구분') == '일반용역' and EVENT.search(task):
            codes = {code for code, row in pf.products.items()
                     if (re.search(r'전시회.*회의.*행사대행', norm(row['제품명']))
                         or norm(row['세부품명']) == '축제기획및대행서비스')}
            # The catalog's festival service has a different parent category.
            # Include it among possible event services; narrow to it only when
            # the actual named task explicitly identifies festival planning.
            titles = [norm(s['text']) for s in scopes
                      if re.search(r'(?:용역명|사업명|과업명|공고건명|입찰건명|건명)[:：|]', norm(s['text']))
                      and EVENT.search(norm(s['text']))]
            festival_task = r'축제[』」〉>”"‘’]*(?:행사)?(?:기획|대행)(?:용역|서비스)?'
            if titles and all(re.search(festival_task, t) for t in titles):
                codes = {code for code in codes
                         if norm(pf.products[code]['세부품명']) == '축제기획및대행서비스'}
            identity = [s for s in scopes if EVENT.search(norm(s['text']))]
            mechanism = 'event_service_family_with_unresolved_detail'
            family_candidates = True
        elif meta.get('업무구분') == '일반용역' and SOFTWARE.search(task) and re.search(r'소프트웨어사업자|컴퓨터관련서비스', norm(notices)):
            codes = {code for code, row in pf.products.items() if re.search(r'소프트웨어\s*진흥법\s*제\s*48\s*조', row['특이사항'])}
            identity = [s for s in scopes if SOFTWARE.search(norm(s['text']))]
            mechanism = 'software_service_family_with_registration_and_actual_task'
            family_candidates = True

    rows = [{'code': code, 'listed': code in pf.products,
             'name': pf.products[code]['세부품명'] if code in pf.products else None,
             'note': pf.products[code]['특이사항'] if code in pf.products else None,
             'condition': catalog_condition(pf.products[code]['특이사항'], estimate, budget)
                          if code in pf.products else {'status': 'unlisted'}} for code in sorted(codes)]
    statuses = {row['condition']['status'] for row in rows}
    state = 'unknown'
    if rows and not uncertainty:
        if statuses <= {'met', 'no_stated_condition'}:
            state = 'competition'
        elif statuses <= {'unlisted', 'not_met'}:
            state = 'general'
        elif len(statuses) > 1:
            uncertainty.append('mixed_or_differently_conditioned_purchase_candidates')
    # Explicit named research purchase is distinct from an event certificate.
    # This name is the supplied official example, used as a purchase category,
    # never a notice-ID exception or a title that overrides conflicting scope.
    research = [s for s in scopes if re.search(r'품명[:：|]*농림수산연구조사서비스', norm(s['text']))]
    if not codes and research and not uncertainty:
        state, mechanism, identity = 'general', 'explicit_nonlisted_research_purchase_name', research
    return {'status': state, 'mechanism': mechanism, 'products': rows, 'uncertainty': uncertainty, 'purchase_coverage': coverage,
            'identity_evidence': identity, 'meta_purchase': meta_text, 'scope_evidence': scopes,
            'detail_candidates_not_unique_identity': family_candidates,
            'estimate_won': estimate, 'budget_won': budget, 'project_prices': prices}


def qualification_facts(record, parts):
    entries, sections, quotes, exceptions, declarations = parts
    active = [entry for entry in entries if entry['status'] == 'mandatory_eligibility']
    sizes = [entry for entry in active if entry['size']]
    size_sets = {tuple(entry['size']['allowed']) for entry in sizes}
    conflict = len(size_sets) > 1
    # A stated narrow competition scope cannot erase a broader eligibility
    # clause. Preserve that internal conflict, as requested in review Q7.
    notices = '\n'.join(d['text'] for d in record['docs'] if d['type'] == '공고문')
    narrow_procedure = bool(re.search(r'제한\s*경쟁\s*\(\s*소기업\s*\)', notices))
    if narrow_procedure and any('medium' in entry['size']['allowed'] for entry in sizes):
        conflict = True
    allowed = set(next(iter(size_sets))) if len(size_sets) == 1 and not conflict else None
    direct = [entry for entry in active if entry['direct_requirement']]
    direct_unresolved = []
    for entry in entries:
        if not entry['direct_production'] or entry in direct:
            continue
        text = norm(entry['evidence']['text'])
        if entry['status'] in {'submission_or_form', 'scoring'}:
            continue
        if re.search(r'위반.{0,90}(?:계약해지|계약을해지|제재|입찰참가자격제한)|계약상대자.{0,60}직접생산', text):
            continue
        if re.search(r'직접생산.{0,150}(?:소지|보유|참가자격|참가가능|갖춘)', text):
            direct_unresolved.append(entry)
    # The cited statutory registration basis can imply a production check.
    # It does not prove possession, but blocks a confident missing-condition
    # inference until that incorporated requirement is resolved.
    for declaration in declarations:
        text = norm(declaration['evidence']['text'])
        if (declaration['role'] == 'purchase_registration'
                and '중소기업제품구매촉진' in text and '제9조' in text
                and re.search(r'등록한|등록된|등록을필|등록되어', text)):
            direct_unresolved.append({'reason': 'incorporated_production_law_registration',
                                      'evidence': declaration['evidence']})
    complete = record.get('input_completeness', {}).get('완전관측') is True and not any(record.get('dropped_doc_counts', {}).values())
    recovered = any(s['closed'] and s['evidence']['document_role'] == '공고문' for s in sections)
    meta_reason = str(record.get('meta', {}).get('조항호내용') or '')
    meta_size = None
    if re.search(r'중기업[,·ㆍ]소기업[,·ㆍ]소상공인제한', norm(meta_reason)):
        meta_size = {'medium', 'small', 'micro'}
    raw_size = [e for e in entries
                if e['status'] not in {'submission_or_form', 'scoring', 'explicit_permission'}
                and sme.SIZE_SIGNAL.search(sme.mask_laws(norm(e['evidence']['text'])))
                and (e['section_role'] == 'eligibility' or e['size'])]
    return {'inventory': entries, 'eligibility_sections': sections, 'allowed': sorted(allowed) if allowed else None,
            'size_conflict': conflict, 'active_size': sizes, 'active_direct': direct,
            'meta_size_restriction': sorted(meta_size) if meta_size else None,
            'no_direct': complete and recovered and not direct and not direct_unresolved,
            'no_size': complete and recovered and not raw_size and not meta_size,
            'unresolved_direct': direct_unresolved, 'complete': complete, 'closed_eligibility': recovered,
            'exceptions': exceptions, 'quote_evidence': quotes}


def infer(record, baseline, pf, *, product_override=None):
    result = dict(baseline)
    parts = inventory(record)
    product = purchase_scope(record, pf, parts[0], parts[4])
    if product_override is not None:
        product = product_override
    eligibility = qualification_facts(record, parts)
    decisions = {}
    meta = record.get('meta', {})
    estimate = product['estimate_won']
    allowed = set(eligibility['allowed'] or [])
    ordinary = meta.get('적용계약법') in {'국가계약법', '지방계약법'} and meta.get('업무구분') in {'일반용역', '물품(내자)'}
    actual_small_quote = bool(eligibility['quote_evidence']) and meta.get('계약방법') == '수의계약'
    disclosed_small_route = actual_small_quote and estimate is not None and estimate <= 20_000_000 and bool(re.search(r'2천만원이하|2천만\s*원\s*이하', str(meta.get('조항호내용'))))
    exception_review = [e for e in eligibility['exceptions'] if e['kind'] != 'priority_exception_denied']
    quote = lambda spans: next((clean_evidence(s['text'], record) for s in spans if clean_evidence(s['text'], record)), '')
    size_evidence = [e['evidence'] for e in eligibility['active_size']]
    direct_evidence = [e['evidence'] for e in eligibility['active_direct']]
    def put(item, value, why, evidence=()):
        text = quote(evidence) if value and item not in ABSENCE else ''
        if value and item not in ABSENCE and not text:
            return
        decisions[f'v{item}'] = {'value': value, 'reason': why, 'evidence': text}
        result[f'v{item}'], result[f'e{item}'] = str(value), text

    if ordinary:
        state = product['status']
        if state == 'general':
            for item in (10, 11, 13):
                put(item, 0, 'identified_purchase_outside_conditional_catalog')
            if direct_evidence:
                put(12, 1, 'general_purchase_with_operative_direct_certificate', direct_evidence)
            if estimate is not None and allowed and not eligibility['size_conflict']:
                if estimate >= NOTICE:
                    put(14, 1, 'general_purchase_above_notice_with_SME_restriction', size_evidence)
                elif FLOOR <= estimate < NOTICE and 'medium' not in allowed and not exception_review and not actual_small_quote:
                    put(15, 1, 'general_middle_band_excludes_medium', size_evidence)
                elif estimate < FLOOR and 'medium' in allowed and not exception_review and not disclosed_small_route:
                    put(17, 1, 'general_low_band_includes_medium', size_evidence)
            if disclosed_small_route:
                for item in (16, 18):
                    put(item, 0, 'documented_actual_small_quote_priority_exception_route')
            elif eligibility['no_size'] and not exception_review and estimate is not None and estimate > 20_000_000:
                if FLOOR <= estimate < NOTICE:
                    put(16, 1, 'complete_general_middle_band_no_size_requirement')
                elif estimate < FLOOR:
                    put(18, 1, 'complete_general_low_band_no_size_requirement')
        elif state == 'competition':
            for item in (12, 14, 15, 16, 17, 18):
                put(item, 0, 'identified_purchase_in_conditional_catalog')
            if not actual_small_quote:
                if eligibility['no_direct']:
                    put(10, 1, 'complete_eligibility_without_possession_requirement')
                if eligibility['no_size']:
                    put(11, 1, 'complete_eligibility_without_SME_restriction')
                if allowed and 'medium' not in allowed and not eligibility['size_conflict'] and not exception_review:
                    put(13, 1, 'competition_excludes_ordinary_medium_enterprises', size_evidence)
        if eligibility['active_direct'] and state == 'competition':
            # A certificate for a different code cannot clear the obligation.
            targets = {p['code'] for p in product['products']}
            direct_codes = {code for e in eligibility['active_direct'] for code in e['codes']}
            if targets and targets <= direct_codes:
                put(10, 0, 'all_identified_targets_have_possession_requirement')
        if allowed:
            for item in (11, 16, 18):
                put(item, 0, 'operative_size_restriction_present_dates_separate')

    return result, {'product': product, 'qualification': eligibility, 'decisions': decisions,
                    'exception_review_flags_are_not_waivers': True,
                    'saved_model_response_unchanged': True}
