"""Per-notice purchase and qualification facts, using the supplied catalog only.

The caller supplies this notice and its model-based row in memory. No history,
identifier rules, labels, external documents, mutable parser hooks, or file I/O.
This module does not reproduce an entire historical research pipeline by itself.
"""
from __future__ import annotations

import copy
from .legal_context import applicable_law
import re

from . import sme
from .data import clean_evidence
from .products import CODE, ProductFacts, normalized_map, scope_spans
from .prices import project_prices, in_band
from .purchase_cardinality import purchase_cardinality

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
        n = sme.norm(entry['evidence']['text'])
        # Quotes around a certificate's entire class list are typography, not
        # a boundary that discards the first side of its OR. Repair consumers
        # only: the model-input inventory remains unchanged.
        quoted = re.sub(r'[“‘"\'](' + sme.CLASS + r'(?:자)?(?:' + sme.SEP +
                        sme.CLASS + r'(?:자)?)*)(?:[”’"\'])(?=확인서|확인증)',
                        r'\1', n)
        if quoted != n:
            size = sme.size_facts(quoted)
            if size:
                entry['size'] = size
                entry['postprocessing_repair'] = 'quoted_certificate_class_scope'
        if (entry.get('size') and re.search(r'(?:신인도|평가|가점).{0,40}(?:자료|목적)(?:로|으로|에)만', n)
                and re.search(r'(?:아닌|비해당|해당하지않는).{0,50}(?:입찰|견적)(?:에)?참가'
                              r'(?:할수있|가능(?:하다|합니다|함|한))', n)):
            entry['status'], entry['section_role'] = 'scoring', 'scoring'
            entry['postprocessing_repair'] = 'evaluation_only_certificate_with_nonholder_admission'
        if entry['section_role'] != 'eligibility':
            continue
        if entry['status'] not in ('mandatory_eligibility', 'incidental_or_unresolved'):
            continue
        raw = entry['evidence']['text']
        n = sme.mask_laws(sme.norm(raw))
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
    from .qualification_predicates import (size_permission, direct_requirement,
        nonprofit_exception, unrelated_exception, size_requirement, complete_direct_code,
        deemed_special_inclusion)
    for entry in result[0]:
        complete_direct_code(record, entry)
        if size_permission(entry):
            entry['status'] = 'explicit_permission'
            entry['postprocessing_repair'] = 'explicit_unrestricted_bidder_size'
        if direct_requirement(entry):
            entry['status'] = 'mandatory_eligibility'
            entry['direct_requirement'] = True
            entry['direct_requirement_basis'] = 'source_bidder_holding_or_prebid_exclusion'
        if size_requirement(entry):
            entry['status'] = 'mandatory_eligibility'
            entry['postprocessing_repair'] = 'operative_entity_or_statutorily_deemed_entity'
        if deemed_special_inclusion(entry):
            entry['status'] = 'mandatory_eligibility'
            entry['postprocessing_repair'] = 'deemed_special_corporation_inside_size_clause'
    for exception in result[3]:
        if unrelated_exception(exception):
            exception['kind'] = 'unrelated_statutory_reference'
            exception['interpretation_basis'] = 'explicit_other_statute_namespace'
        if nonprofit_exception(exception):
            exception['kind'] = 'nonprofit_alternative'
            exception['interpretation_basis'] = 'statutory_exception_explicitly_scoped_to_nonprofit'
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
    # An explicitly named non-profit alternative is narrower than a bare
    # priority-procurement exception reference. Preserve that branch without
    # treating it as permission for ordinary medium commercial enterprises.
    for exception in result[3]:
        if exception['kind'] != 'priority_exception_reference' or exception['role'] != 'eligibility':
            continue
        ev = exception['evidence']
        n = norm(ev['text'])
        alternative = re.search(
            r'(?:또는|혹은)[「『｢]?(?:중소기업제품구매촉진및판로지원에관한법률|판로지원법)'
            r'[」』｣]?시행령[」』｣]?제2조의3제2호(?:에따른|에해당하는)비영리법인[.]?$', n)
        if not alternative:
            continue
        holders = [entry for entry in result[0]
            if entry['status'] == 'mandatory_eligibility' and entry.get('size')
            and not entry['alternative_size_branch_unresolved']
            and entry['evidence']['doc_index'] == ev['doc_index']
            and entry['evidence']['start'] == ev['start']]
        if holders and sme.CERT.search(n[:alternative.start()]) and re.search(
                r'(?:소지|보유)한(?:자|업체)', n[:alternative.start()]):
            exception['kind'] = 'nonprofit_alternative'
            exception['interpretation_basis'] = 'explicit_certificate_holder_OR_nonprofit_entity'
    return result


FLOOR = 100_000_000
NOTICE = 230_000_000
ABSENCE = {10, 11, 16, 18, 20}
EVENT = re.compile(r'(?:행사|축제|포럼|박람회|전시회|회의).{0,65}(?:기획|대행|운영|위탁)')
SOFTWARE = re.compile(r'(?:정보시스템|경영정보시스템|정보인프라|소프트웨어|전산시스템|출입통제체계).{0,60}(?:구축|개발|유지보수|유지관리|운영|갱신)')
PURCHASE_TITLE = re.compile(r'(?:용역명|사업명|과업명|공고건명|입찰건명|공고명|건명)[:：|]')
# A judgment base date states when the listed qualifications are assessed, and
# an enumerated-clause governor points at this heading's own sub-items. Neither
# adds nor removes a qualification, so the exhaustive governor still closes the
# section. Kept here as a consumer repair; the model-input extractor is frozen.
_DATED_OR_ENUMERATED_GOVERNOR = re.compile(
    r'(?:(?:입찰|견적|제안|공고|등록|접수)*(?:공고일|게시일|공고게시일|등록마감일|접수마감일|제출마감일|'
    r'마감일시|마감일|개찰일|개찰예정일|입찰일|제출일|기준일)(?:현재|기준(?:으로)?|까지)[,，]?)?'
    r'(?:(?:다음|아래)(?:의)?(?:각(?:호|항)(?:의)?)?|각(?:호|항)(?:의)?)'
    r'(?:입찰참가)?(?:자격요건|조건|요건|자격|사항|기준|호)?(?:을|를)?(?:동시에)?모두'
    + sme._QUALIFICATION_OBLIGATION)


def whole_task_support(scopes, pattern):
    """A component task cannot establish the identity of the whole purchase.

    Prefer explicit purchase titles, then introductory title candidates. Keep
    conflicting titles unresolved; an event inside a wider program is evidence
    of an event component only. No classifier is based on the notice ID.
    """
    titles = [s for s in scopes if PURCHASE_TITLE.search(norm(s['text']))]
    if not titles:
        titles = [s for s in scopes if s['role'] == 'intro_title_candidate']
    return bool(titles) and all(pattern.search(norm(s['text'])) for s in titles)


def norm(text):
    return normalized_map(str(text))[0]


def exact_catalog_name_codes(text, products):
    """Return undominated literal catalog names in one source field.

    Catalog names are not tokenized words.  A short supplied name such as
    ``디자인서비스`` can occur wholly inside the different, longer catalog
    name ``전시홍보관설치및디자인서비스``.  Count the short name only when it
    has an occurrence outside every longer matched catalog name.
    """
    value = norm(text)
    occurrences = []
    for code, product in products.items():
        name = norm(product['세부품명'])
        if not code or len(name) < 5:
            continue
        occurrences.extend((match.start(), match.end(), code, name)
                           for match in re.finditer(re.escape(name), value))
    result = set()
    for start, end, code, name in occurrences:
        dominated = any(other_start <= start and end <= other_end
                        and other_end-other_start > end-start
                        for other_start, other_end, _, _ in occurrences)
        if not dominated:
            result.add(code)
    return result


def price(value):
    return value if type(value) in (int, float) and 0 <= value < float('inf') else None


def inventory(record):
    # Per-call parser injection keeps extraction independent across threads.
    original_heading = sme.heading
    def recognize(n):
        role = original_heading(n)
        # A standalone all-qualifications governor can follow a repeated
        # documents caption in flattened notices. The governor, not the
        # presence of a certificate in a list, establishes eligibility.
        if re.fullmatch(r'[○●□■❍•·ㆍ※*\-]*(?:아래|다음)의자격을모두갖춘(?:자|업체)[.:：]?', n):
            return 'eligibility'
        from .performance_predicates import eligibility_heading
        if eligibility_heading(n):
            return 'eligibility'
        from .qualification_predicates import section_close
        if section_close(n):
            return 'other'
        # Equivalent closed "all these qualifications" governors. Keep this
        # consumer repair separate from the frozen model-input SME extractor.
        # "입찰서 제출자격" titles the bid qualifications as "견적서 제출자격" does.
        titled = re.sub(r'^(' + sme._HEADING_PREFIX + r')입찰서제출자격', r'\1입찰참가자격', n)
        title = sme._QUALIFICATION_TITLE.match(titled) if len(titled) < 150 else None
        if role != 'eligibility' and title:
            tail = titled[title.end():].lstrip(':：').rstrip('.。')
            if tail.startswith('(') and tail.endswith(')'):
                tail = tail[1:-1].rstrip('.。')
            if (re.fullmatch(r'(?:및(?:관련사항|제출서류|구비조건|계약방식))?'
                             r'(?:[\[(]?모두(?:요함|충족|해당)[)\]]?)?', tail)
                    or re.fullmatch(r'(?:다음|아래)(?:사항|조건|요건)의자격을모두' +
                             sme._QUALIFICATION_OBLIGATION, tail)
                    or re.fullmatch(r'(?:다음|아래)의?모든(?:사항|조건|요건|자격)을' +
                                    sme._QUALIFICATION_OBLIGATION, tail)
                    or re.fullmatch(r'해당(?:자격|조건|요건)을모두갖춘(?:자|업체|사업자)'
                                    r'만(?:이)?(?:입찰참가|견적제출)할수있습니다', tail)
                    or re.fullmatch(r'(?:다음|아래)의?(?:조건|요건|자격)을동시에'
                                    r'(?:충족|만족)(?:하여야|해야)하며[,，]'
                                    r'공동(?:계약|수급)(?:\(수급\))?(?:및하도급)?불허', tail)
                    or _DATED_OR_ENUMERATED_GOVERNOR.fullmatch(tail)):
                return 'eligibility'
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
            and re.search(r'중소기업기본법|소상공인기본법|중소기업제품구매촉진|중소기업범위및확인|'
                          r'법(?:률)?[」』｣]?(?:시행령|시행규칙)?[」』｣]?제\d+조', n)
            and not re.search(r'목차|예외사항|참고사항', n))
        # Numbered bidder predicates are members of the active qualification
        # list, not peer document sections. PDF text often drops the enclosing
        # list indentation, so a regional/registration member otherwise closes
        # the qualification role and strands every following size clause.
        bidder_clause = (
            re.match(r'^\d+[.)]', n)
            and re.search(
                r'(?:본사|주된영업소).{0,80}(?:소재|둔|위치).{0,80}'
                r'(?:입찰|견적)(?:참가|제출)?(?:가)?능|'
                r'(?:입찰|견적)(?:참가|제출)?(?:가)?능.{0,80}'
                r'(?:업체|사업자|법인)|'
                r'(?:소지|등록|갖춘)(?:한|한자|한업체|업체|자).{0,40}$', n)
            and not re.search(
                r'목차|예외사항|참고사항|계약체결|낙찰자|예시|작성예|가정|삭제|철회|적용하지|효력없', n))
        if role == 'other' and (statutory_clause or bidder_clause):
            return None
        if (role == 'other' and re.match(r'^\d+[.)]', n)
                and re.search(r'입찰공고일(?:전일|이전)|주된영업소|본점소재지', n)
                and not re.search(r'예시|작성예|목차', n)):
            return None  # A wrapped bidder-location clause is not a heading.
        return role
    result = repair_inventory(record, sme.extract_inventory(record, heading_fn=recognize))
    return result


SPATIAL_DEFINITION = ('토지, 도시계획, 지하 시설물 등 지리정보를 전자매체로 제공하기 위한 '
                      '측량, 탐사, 수치지도, 정사 영상 지도 제작 등의 기초 활동 포함')


def catalog_condition(note, estimate, budget, *, estimate_prices=None, budget_prices=None,
                      record=None, product_name=None):
    compact_note = norm(note)
    spatial = compact_note == norm('1. 소프트웨어 진흥법 제48조 적용 2. '+SPATIAL_DEFINITION)
    if spatial or re.fullmatch(r'소프트웨어\s*진흥법\s*제\s*48\s*조\s*적용', note.strip()):
        prices = budget_prices if budget_prices is not None else {
            'candidate_values_won': [budget] if budget is not None else []}
        band = in_band(prices, upper=2_000_000_000)
        if prices.get('derived_band') is not None:
            band = prices['derived_band']['below_20eok']
        return {'kind': 'software_article48_SME_only_band', 'basis': 'same_scope_project_amount_predicate',
                'value_won': budget, 'operator': '<', 'ceiling_won': 2_000_000_000,
                'candidate_values_won': prices['candidate_values_won'],
                'scope_definition': SPATIAL_DEFINITION if spatial else None, 'purchase_identity_certified': False,
                'status': 'unknown' if band is None else 'met' if band else 'not_met',
                'effective_scope': prices.get('effective_scope', 'project_budget'),
                'derived_band': prices.get('derived_band')}
    result = ProductFacts.condition(note, estimate, record=record, product_name=product_name)
    if result.get('kind') == 'estimated_price_ceiling':
        prices = estimate_prices if estimate_prices is not None else {
            'candidate_values_won': [estimate] if estimate is not None else []}
        band = in_band(prices, upper=result['ceiling_krw'])
        result.update(basis='same_scope_estimated_price_predicate', value_won=estimate,
            candidate_values_won=prices['candidate_values_won'],
            status='unknown' if band is None else 'met' if band else 'not_met')
    return result


def software_catalog_prices(record, prices):
    """Do not use a multi-year or separated total as the software floor amount."""
    from decimal import Decimal
    from .other_checks import budget_facts
    effective = budget_facts(record)
    if effective['separated_evidence'] or effective['bundled_evidence']:
        return {**prices, 'candidate_values_won': [], 'effective_scope': effective['basis']}
    maintenance = ' '.join(e['quote'] for e in effective['maintenance_evidence'])
    if '장기계속계약' in maintenance and re.search(r'소프트웨어\s*(?:유지|보수)', maintenance):
        value = effective['effective_won'] if effective['annualized'] else None
        return {**prices, 'candidate_values_won': [], 'effective_scope': 'annual_average_SW_maintenance',
                'derived_band': {'below_20eok': Decimal(value) < 2_000_000_000 if value is not None else None,
                                 'effective_won': value, 'annualized': effective['annualized']}}
    return prices


def purchase_scope(record, pf, entries, declarations):
    meta = record.get('meta', {})
    prices = project_prices(record)
    estimate, budget = prices['estimated_price']['value_won'], prices['budget']['value_won']
    scopes = scope_spans(record, max_spans=1000, char_limit=1_000_000)
    # Certificate, registration and purchase identities remain separate.
    meta_text = str(meta.get('세부품명번호목록') or '')
    meta_codes = set(CODE.findall(meta_text))
    # These are supplied purchase identities, unlike a certificate/industry
    # registration code harvested elsewhere in the text. An exact name/code
    # pair supports lookup in the supplied closed catalog, subject to the
    # explicit conflicting/mixed-purchase guards below. It is not certification
    # against external catalogs or evidence that a bidder holds a certificate.
    named_meta_codes = {m[1] for m in re.finditer(r'[^,\[\]\n]{2,}\[(\d{10})\]', meta_text)}
    declared = {code for declaration in declarations for code in declaration['codes']}
    codes = meta_codes | declared
    identity = [declaration['evidence'] for declaration in declarations]
    exact = set()
    for span in scopes:
        matched = exact_catalog_name_codes(span['text'], pf.products)
        if matched:
            exact.update(matched)
            identity.append(span)
    uncertainty = []
    if meta_codes and declared and not meta_codes <= declared:
        uncertainty.append('metadata_and_body_purchase_codes_conflict')
    if meta_codes and declared - meta_codes:
        uncertainty.append('additional_declared_purchase_components')
    if codes and exact - codes:
        uncertainty.append('additional_named_catalog_purchase')
    from .products import software_delivery_conflicts
    software_conflicts = software_delivery_conflicts(record, codes, pf.products)
    if software_conflicts:
        uncertainty.append('metadata_purchase_does_not_resolve_explicit_software_delivery')
        identity.extend(software_conflicts)
    cardinality = purchase_cardinality(record)
    item_counts = cardinality['counts']
    accounted_lists = []
    for count in item_counts:
        if count['minimum_total'] <= 1 or count['reference_only']:
            continue
        index = count['item_list_index']
        witness = cardinality['item_lists'][index] if index is not None else None
        if (witness and witness.get('catalog_identity_complete')
                and set(witness['distinct_codes']) <= codes | exact):
            accounted_lists.append(witness)
        elif witness:
            # Every purchased item is source-addressed, but names without
            # codes still need an identity comparison against the supplied
            # catalog.  Preserve that narrower unresolved state for the
            # optional goods-scope reviewer instead of pretending retrieval
            # failure proves that the products are unlisted.
            if 'explicit_multiple_items_catalog_identity_unresolved' not in uncertainty:
                uncertainty.append('explicit_multiple_items_catalog_identity_unresolved')
        elif 'explicit_multiple_items_not_all_identified' not in uncertainty:
            # Even N different registration/catalog codes do not prove that
            # all N purchased items have been individually identified.
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
            if not whole_task_support(scopes, EVENT):
                uncertainty.append('event_component_does_not_establish_whole_purchase')
        elif meta.get('업무구분') == '일반용역' and SOFTWARE.search(task) and re.search(r'소프트웨어사업자|컴퓨터관련서비스', norm(notices)):
            codes = {code for code, row in pf.products.items() if re.search(r'소프트웨어\s*진흥법\s*제\s*48\s*조', row['특이사항'])}
            identity = [s for s in scopes if SOFTWARE.search(norm(s['text']))]
            mechanism = 'software_service_family_with_registration_and_actual_task'
            family_candidates = True
            if not whole_task_support(scopes, SOFTWARE):
                uncertainty.append('software_component_does_not_establish_whole_purchase')

    software_prices = software_catalog_prices(record, prices['budget']) if any(
        code in pf.products and '소프트웨어' in pf.products[code]['특이사항'] for code in codes) else prices['budget']
    rows = [{'code': code, 'listed': code in pf.products,
             'name': pf.products[code]['세부품명'] if code in pf.products else None,
             'note': pf.products[code]['특이사항'] if code in pf.products else None,
              'condition': catalog_condition(pf.products[code]['특이사항'], estimate, budget,
                                             estimate_prices=prices['estimated_price'], budget_prices=software_prices,
                                             record=record, product_name=pf.products[code]['세부품명'])
                          if code in pf.products else {'status': 'unlisted'}} for code in sorted(codes)]
    statuses = {row['condition']['status'] for row in rows}
    if accounted_lists and any(row['note'] for row in rows):
        # A property observed on one variant cannot discharge every variant's
        # designation condition. Keep this separate from counting named rows.
        uncertainty.append('multiple_item_catalog_condition_scope_unresolved')
    state = 'unknown'
    if rows and not uncertainty:
        if statuses <= {'met', 'no_stated_condition'}:
            state = 'competition'
        elif statuses <= {'unlisted', 'not_met'} and (
                'unlisted' not in statuses or codes <= named_meta_codes):
            state = 'general'
        elif 'unlisted' in statuses:
            # The lookup may miss an alias, parent category, mixed lot or
            # erroneous registration code. This is not a legal exclusion.
            uncertainty.append('unlisted_code_is_not_proof_of_general_purchase')
        elif len(statuses) > 1:
            uncertainty.append('mixed_or_differently_conditioned_purchase_candidates')
    # Explicit named research purchase is distinct from an event certificate.
    # This name is the supplied official example, used as a purchase category,
    # never a notice-ID exception or a title that overrides conflicting scope.
    research = [s for s in scopes if re.search(r'품명[:：|]*농림수산연구조사서비스', norm(s['text']))]
    if not codes and research and not uncertainty:
        state, mechanism, identity = 'general', 'explicit_nonlisted_research_purchase_name', research
    return {'status': state, 'mechanism': mechanism, 'products': rows, 'uncertainty': uncertainty,
            'identity_evidence': identity, 'meta_purchase': meta_text, 'scope_evidence': scopes,
            'purchase_item_counts': item_counts,
            'purchase_item_lists': cardinality['item_lists'],
            'catalog_scope': 'supplied_catalog_only', 'paired_meta_purchase_codes': sorted(named_meta_codes),
            'detail_candidates_not_unique_identity': family_candidates,
            'estimate_won': estimate, 'budget_won': budget, 'project_prices': prices}


def _required_certificate_lists(record, entries):
    """Retain unresolved possession requirements under explicit required lists.

    A certificate in a flattened table cannot establish the bidder scope by
    itself, but an explicit required-list governor prevents proving absence.
    This preserves both source spans without guessing the missing table cells.
    """
    observations = []
    for entry in entries:
        if entry.get('certificate_table', {}).get('header', {}).get('kind') == 'checklist':
            from .qualification_tables import submission_observation
            observation = submission_observation(record, entry)
            if observation:
                text = sme.mask_laws(norm(observation.pop('certificate_text')))
                for kind, present in (('size', sme.CERT.search(text)),
                        ('direct', re.search(r'직접생산(?:확인)?(?:증명|확인)?서', text))):
                    if present:
                        observations.append({**observation, 'certificate_type':kind})
            continue  # A parent title cannot override explicit row columns.
        head = entry.get('heading')
        if entry['status'] != 'submission_or_form' or not head:
            continue
        ancestors = entry.get('heading_ancestors') or [head]
        if any(re.search(r'예시|작성예|참고|가정|해당시|경우|필수아님|필수가아님', norm(h['text']))
               for h in ancestors):
            continue
        governors = [h for h in ancestors if re.search(r'필수(?:제출|구비)?(?:서류|목록)', norm(h['text']))]
        if not governors:
            continue
        head = governors[-1]
        text = sme.mask_laws(norm(entry['evidence']['text']))
        for kind, present in (
                ('size', sme.CERT.search(text)),
                ('direct', re.search(r'직접생산(?:확인)?(?:증명|확인)?서', text))):
            if present:
                observations.append({'reason':'required_certificate_submission_scope_unresolved',
                    'certificate_type':kind, 'governor':head, 'evidence':entry['evidence']})
    return observations


def qualification_facts(record, parts):
    entries, sections, quotes, exceptions, declarations = parts
    active = [entry for entry in entries if entry['status'] == 'mandatory_eligibility']
    sizes = [entry for entry in active if entry['size']]
    from .qualification_obligation import entity_obligations, ordinary_commercial_bounds
    obligations = entity_obligations(record, entries)
    ordinary_bounds = ordinary_commercial_bounds(record, entries)
    from .qualification_predicates import commercial_exclusion
    exclusions = [bound for entry in entries if (bound := commercial_exclusion(entry))]
    ordinary_bounds += exclusions
    procedures = []
    unresolved_procedures = []
    for di, doc in enumerate(record['docs']):
        if doc['type'] != '공고문':
            continue
        # Table typography may separate every syllable. Match original lines,
        # then normalize only the interpretation; evidence offsets stay exact.
        for m in re.finditer(r'[^\n]*(?:입[ \t]*찰[ \t]*방[ \t]*법|계[ \t]*약[ \t]*방[ \t]*법|입[ \t]*찰[ \t]*방[ \t]*식)[^\n]*', doc['text']):
            n = norm(m[0])
            # Only an actual labelled competition field, not an award method,
            # quoted rule, conditional settlement or a certificate form title.
            field = re.search(r'(?:입찰방법|계약방법|입찰방식)[:：|]제한경쟁(?:입찰)?\(([^)]+)\)', n)
            if (re.search(r'(?:입찰방법|계약방법|입찰방식)[:：|]제한(?:경쟁|\(경쟁\))', n)
                    and sme.SIZE_SIGNAL.search(sme.mask_laws(n))
                    and not re.search(r'예시|가정|삭제|철회', n)):
                # A size class outside the recognized parenthetical field is
                # not an exact allowed set, but prevents claiming no limit.
                unresolved_procedures.append({'reason':'size_in_restricted_competition_field',
                    'evidence':sme.evidence(record, di, m.start(), m.end())})
            if not field or re.search(r'예시|가정|삭제|철회|경우|협상', n[:field.start()]):
                continue
            phrase = field[1]
            if not re.fullmatch(sme.CLASS + r'(?:[·ㆍ,]' + sme.CLASS + r')*', phrase):
                continue
            procedures.append({'status': 'mandatory_eligibility', 'section_role': 'competition_procedure',
                               'size': {'allowed': sorted(sme.class_set(phrase)),
                                        'basis': 'explicit_restricted_competition_field'},
                               'evidence': sme.evidence(record, di, m.start(), m.end())})
    sizes += procedures
    # An SME competition preamble describes the enclosing class. When it
    # expressly requires all following qualifications, a narrower certificate
    # in that same section is a restriction within the class, not a competing
    # permission. Keep both source observations; never collapse peer ORs.
    broad_preambles = [entry for entry in sizes
        if entry['size'].get('basis') == 'eligible_entity'
        and re.search(r'중소기업자간제한경쟁입찰', norm(entry['evidence']['text']))
        and re.search(r'다음의?자격을모두갖추어야', norm(entry['evidence']['text']))
        and any(other is not entry and other.get('heading') == entry.get('heading')
                and other['size'].get('basis') == 'certificate'
                and set(other['size']['allowed']) < set(entry['size']['allowed'])
                for other in sizes)]
    size_sets = {tuple(entry['size']['allowed']) for entry in sizes if entry not in broad_preambles}
    conflict = len(size_sets) > 1
    # A detailed operative clause that expressly names the SME class preserves
    # its permission even when a summary field elsewhere says ``소기업``.
    # Certificate wording alone is insufficient: malformed extraction can pair
    # a broad certificate name with an explicitly small-only entity preamble.
    explicit_medium_permissions = [entry for entry in sizes
        if entry.get('section_role') == 'eligibility'
        and 'medium' in (entry.get('size') or {}).get('allowed', [])
        and 'medium' in ((entry.get('size') or {}).get('eligible_entity_preamble') or [])]
    # The public notice is the operative invitation to bid.  A broader size
    # description in an attachment cannot silently relax a narrower mandatory
    # notice condition.  Preserve both readings for diagnostics, while also
    # retaining the notice-level fact needed by the competition-product check.
    notice_sizes = [entry for entry in sizes
        if (entry.get('evidence') or {}).get('document_role') == '공고문']
    notice_small_bound = None
    notice_bound_sets = [set(entry['size']['allowed']) for entry in notice_sizes]
    notice_sections = [section for section in sections
        if section['evidence']['document_role'] == '공고문']
    if (notice_bound_sets
            and all(values and 'medium' not in values
                    and values <= {'small', 'micro'} for values in notice_bound_sets)
            and not any(entry.get('alternative_size_branch_unresolved')
                        for entry in notice_sizes)
            and not any(re.search(r'대기업|중견기업', norm(section['evidence']['text']))
                        for section in notice_sections)):
        notice_small_bound = {
            'commercial_upper_bound': sorted(set().union(*notice_bound_sets)),
            'ordinary_medium_enterprises_excluded': True,
            'attachment_conflict_preserved': any(
                'medium' in set(entry['size']['allowed']) for entry in sizes
                if entry not in notice_sizes),
            'evidence': [entry['evidence'] for entry in notice_sizes],
        }
    # A stated narrow competition scope cannot erase a broader eligibility
    # clause. Preserve that internal conflict, as requested in review Q7.
    notices = '\n'.join(d['text'] for d in record['docs'] if d['type'] == '공고문')
    narrow_procedure = bool(re.search(r'제한\s*경쟁\s*\(\s*소기업\s*\)', notices))
    if narrow_procedure and any('medium' in entry['size']['allowed'] for entry in sizes):
        conflict = True
    allowed = set(next(iter(size_sets))) if len(size_sets) == 1 and not conflict else None
    # Broad and narrow SME clauses can disagree about medium enterprises while
    # both exclude larger commercial enterprises. Preserve that common upper
    # bound without resolving their AND/OR relation or erasing the conflict.
    common_bound = None
    bound_sets = [set(e['size']['allowed']) for e in sizes]
    bound_sets += [set(e['commercial_upper_bound']) for e in obligations]
    if (bound_sets and all(s and s <= {'medium', 'small', 'micro'} for s in bound_sets)
            and not any(e.get('alternative_size_branch_unresolved') for e in entries)
            and not any(re.search(r'대기업|중견기업', norm(s['evidence']['text'])) for s in sections)):
        common_bound = {'commercial_upper_bound': sorted(set().union(*bound_sets)),
                        'larger_commercial_enterprises_excluded': True,
                        'exact_size_conflict_preserved': conflict,
                        'evidence': [e['evidence'] for e in sizes]+[e['evidence'] for e in obligations]}
    ordinary_bound = None
    if ordinary_bounds and all('medium' not in set(item['commercial_upper_bound'])
                               for item in ordinary_bounds):
        ordinary_bound = {
            'commercial_upper_bound': sorted(set().union(*(
                set(item['commercial_upper_bound']) for item in ordinary_bounds))),
            'ordinary_medium_enterprises_excluded': True,
            'special_entity_alternatives_preserved': sorted(set().union(*(
                set(item['special_entity_alternatives']) for item in ordinary_bounds))),
            'evidence': [item['evidence'] for item in ordinary_bounds],
        }
    if exclusions:
        # Explicit exclusion is an operative upper bound, not a complete OR
        # reconstruction or permission inferred from a certificate's name.
        allowed, conflict = {'micro', 'small'}, False
        notice_small_bound = ordinary_bound
        explicit_medium_permissions = []
    direct = [entry for entry in active if entry['direct_requirement']]
    from .production_certificate import (coverage as certificate_coverage,
        incorporated_registration_requirements, unresolved_validity,
        governed_certificate_requirements, source_supplier_requirements,
        statutory_absence_review)
    direct_coverage = certificate_coverage(record, direct, completed_registration=True)
    incorporated_direct = incorporated_registration_requirements(record, entries, declarations)
    incorporated_direct += governed_certificate_requirements(record, entries)
    required_lists = _required_certificate_lists(record, entries)
    direct_unresolved = [e for e in required_lists if e['certificate_type']=='direct']
    direct_unresolved += source_supplier_requirements(record)
    direct_unresolved += statutory_absence_review(record)
    size_unresolved = [e for e in required_lists if e['certificate_type']=='size'] + unresolved_procedures
    # A submission subsection can contain an explicit eligibility exclusion.
    # Its role still cannot prove an allowed set, but neither can the explicit
    # certificate check be discarded when asserting absence of a size duty.
    for entry in entries:
        if entry['status'] != 'submission_or_form':
            continue
        ancestors = entry.get('heading_ancestors') or []
        if any(re.search(r'예시|작성예|참고용|가정|삭제|철회', norm(h['text'])) for h in ancestors):
            continue
        text = norm(entry['evidence']['text'])
        # A standalone bidder-class restriction remains a restriction when
        # an earlier submission heading was not closed by PDF extraction.
        # Retain it as unresolved rather than inventing its complete OR set.
        entity_limit = re.search(
            r'(?:참가(?:기업|업체|자)|공급자)(?:은|는)?.{0,25}'
            r'(?:소기업|소상공인|중소기업)(?:또는|및|[·ㆍ,])?'
            r'(?:소기업|소상공인)?(?:에한하|으로확인되어야)', text)
        if entity_limit and not re.search(r'예시|가정|가점|배점|평가자료|한하지않|확인되지않아도', text):
            size_unresolved.append({'reason': 'standalone_bidder_size_predicate_in_submission_scope',
                                    'evidence': entry['evidence']})
        if (sme.CERT.search(sme.mask_laws(text))
                and re.search(r'확인(?:이)?(?:되지않(?:을|는)|안(?:되|될))경우(?:에는|에)?'
                              r'(?:입찰|견적)?(?:참가|제출)?자격(?:이|은)?없', text)):
            size_unresolved.append({'reason': 'certificate_verification_exclusion_in_submission_scope',
                                    'evidence': entry['evidence']})
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
        else:
            validity = unresolved_validity(record, entry)
            if validity:
                direct_unresolved.append(validity)
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
    from .input_contract import provided_complete
    complete = provided_complete(record)
    recovered = any(s['closed'] and s['evidence']['document_role'] == '공고문' for s in sections)
    unclosed = [s['evidence'] for s in sections if not s['closed']]
    from .reference_coverage import assess, eligibility_absence_coverage
    coverage = assess(record)
    absence_coverage = eligibility_absence_coverage(record, sections, coverage)
    absence_scope = (absence_coverage['eligibility_source_complete'] and recovered and not unclosed
                     and absence_coverage['size_and_direct_predicates_resolved'])
    meta_reason = str(record.get('meta', {}).get('조항호내용') or '')
    meta_size = None
    if re.search(r'중기업[,·ㆍ]소기업[,·ㆍ]소상공인제한', norm(meta_reason)):
        meta_size = {'medium', 'small', 'micro'}
    # This registered priority-procurement route establishes only the missing
    # SME-condition check, never the purchase's catalog identity.
    meta_priority_route = bool(re.fullmatch(
        r'\[판로지원법시행령\](?:중기업[,·ㆍ])?소기업[,·ㆍ]소상공인제한|'
        r'추정가격1억원미만물품[·ㆍ]용역\(소기업[,·ㆍ]소상공인'
        r'(?:[,·ㆍ]벤처기업[,·ㆍ]창업자)?\)|'
        r'추정가격1억원이상고시금액미만물품[·ㆍ]용역\(중소기업자\)',
        norm(meta_reason)))
    optional_size_documents = [e for e in entries
        if e['status'] != 'mandatory_eligibility'
        and re.search(r'해당시|해당하는경우', norm(e['evidence']['text']))
        and re.search(r'(?:중소기업|소기업|소상공인).{0,35}(?:확인서|확인서류)',
                      norm(e['evidence']['text']))]

    def network_lookup_guidance(entry):
        """Return true only for a lookup instruction without a bidder class.

        ``중소기업 확인은 ... 정보망을 활용`` explains the verification
        mechanism but does not itself say that the bidder must be an SME.  An
        explicit class list, exclusion, invalid-bid consequence, or parsed
        size predicate remains operative and must continue to block absence.
        """
        text = norm(entry['evidence']['text'])
        explicit_classes = re.sub(
            r'중소기업(?:제품)?공공구매(?:종합)?정보망|중소기업', '', text)
        return bool(
            entry.get('size') is None
            and entry.get('status') == 'incidental_or_unresolved'
            and re.match(r'^※?중소기업확인은', text)
            and re.search(r'공공구매(?:종합)?정보망', text)
            and re.search(r'확인(?:이)?가능하여야|활용하여확인', text)
            and not re.search(r'중기업|소기업|소상공인', explicit_classes)
            and not re.search(r'무효|자격.{0,12}(?:없|되지않)', text)
        )

    raw_size = [e for e in entries
                if e['status'] not in {'submission_or_form', 'scoring', 'explicit_permission'}
                and e not in optional_size_documents
                and not network_lookup_guidance(e)
                and sme.SIZE_SIGNAL.search(sme.mask_laws(norm(e['evidence']['text'])))
                and (e['section_role'] == 'eligibility' or e['size'])]
    size_mentions = [e for e in entries if e.get('size')]
    nonoperative_size_statuses = {
        'submission_or_form', 'verification_or_exception_note', 'scoring',
        'explicit_permission',
    }
    # Items 13/14/15/17 all require an operative bidder-size restriction.
    # A certificate requested only from the awardee at contract formation is
    # useful evidence for document handling, but cannot satisfy that common
    # prerequisite.  Keep this fact separate from ``no_size``: the latter is
    # an absence claim used to create v16/v18 positives and therefore retains
    # stricter global-source guards.
    no_operative_size_prerequisite = bool(
        absence_coverage['eligibility_source_complete'] and recovered and not unclosed
        and not sizes and not obligations and not size_unresolved
        and all(e.get('section_role') != 'eligibility'
                and e.get('status') in nonoperative_size_statuses
                for e in size_mentions))
    return {'inventory': entries, 'eligibility_sections': sections, 'allowed': sorted(allowed) if allowed else None,
            'common_size_bound': common_bound,
            'explicit_medium_permissions': explicit_medium_permissions,
            'notice_commercial_size_bound': notice_small_bound,
            'ordinary_commercial_size_bound': ordinary_bound,
            'entity_qualification_obligations': obligations,
            'size_conflict': conflict, 'active_size': sizes, 'active_direct': direct,
            'direct_certificate_coverage': direct_coverage,
            'incorporated_direct_requirements': incorporated_direct,
            'meta_size_restriction': sorted(meta_size) if meta_size else None,
            'meta_sme_priority_route': meta_priority_route,
            'operative_procedure_size': procedures,
            'no_direct': absence_scope and not direct and not direct_unresolved and not incorporated_direct,
            # Registration classification is retained, but is not a source
            # clause requiring bidder size. Only an observed operative condition
            # or unresolved source reference can block this absence observation.
            'no_size': absence_scope and not raw_size and not procedures and not obligations and not size_unresolved,
            'reference_coverage': {**coverage, 'eligibility_absence': absence_coverage},
            'unresolved_direct': direct_unresolved, 'unresolved_size': size_unresolved,
            'no_operative_size_prerequisite': no_operative_size_prerequisite,
            'complete': complete, 'closed_eligibility': recovered,
            'unclosed_eligibility': unclosed,
            'exceptions': exceptions, 'quote_evidence': quotes,
            'raw_size': raw_size,
            'optional_size_documents': optional_size_documents}


def specific_supplier_quote_review(record, eligibility, estimate):
    """An observed national special-supplier quote needs its own exception review.

    Supplied priority-procurement decree2-3(1)3 and national decree26(1)5(a)5
    distinguish this from the ordinary small/micro quote branch. This only
    prevents a blind absence override; it does not certify an exception waiver.
    """
    meta = record.get('meta', {})
    if (applicable_law(record) != '국가계약법' or meta.get('계약방법') != '수의계약'
            or not eligibility['quote_evidence'] or estimate is None or not 20_000_000 < estimate <= 100_000_000):
        return []
    claim = re.compile(r'(?:[「｢『]?여성기업지원에관한법률[」｣』]?제2조제?1호에따른여성기업|'
                       r'[「｢『]?장애인기업활동촉진법[」｣』]?제2조제?2호에따른장애인기업)')
    result = []
    for section in eligibility['eligibility_sections']:
        ev = section['evidence']
        if not section['closed'] or ev['document_role'] != '공고문':
            continue
        lines = list(re.finditer(r'[^\r\n]+', ev['text']))
        for i, line in enumerate(lines):
            n = norm(line[0])
            if not claim.search(n) or UNRESOLVED.search(n) or re.search(r'참고|가점|우대|권장', n):
                continue
            own_predicate = bool(re.search(r'(?:여성기업|장애인기업)(?:인자|인업체|으로등록한자|이어야|여야)', n))
            before = norm(ev['text'][max(0, line.start()-250):line.start()])
            listed = bool(re.search(r'(?:아래|다음)의?사항을입찰참가자격으로등록한자[○●·ㆍ\-]*$', before))
            if own_predicate or listed:
                source = sme.evidence(record, ev['doc_index'], ev['start']+line.start(), ev['start']+line.end())
                result.append({'kind': 'specific_supplier_quote_exception_requires_review', 'evidence': source,
                    'source': 'supplied_national_decree26_1_5_a_5_and_priority_decree2_3_1_3',
                    'waiver_certified': False})
    return result


def general_absence_review(record, product):
    """Keep unmatched purchase subjects open before a general-product absence.

    This is a consumer guard, not a new catalog identity or a prompt input.
    A metadata name/code pair cannot account for another explicit product
    subject, multiple independently named specification products, or actual
    system development. None of these witnesses certifies catalog membership.
    """
    review, specification_names = [], []
    meta_names = [norm(m[1]) for m in re.finditer(r'([^,\[\]\n]{2,})\[\d{10}\]',
                                               product['meta_purchase'])]
    for span in product['scope_evidence']:
        doc = record['docs'][span['doc_index']]
        if doc['type'] not in {'규격서', '과업지시서', '제안요청서', '시방서'}:
            continue
        text = norm(span['text'])
        field = re.match(r'(?:[-·ㆍ]|\d+[.)])*(품명|세부품명|물품명|제품명)[:：](.+)', text)
        if not field:
            continue
        name = field[2]
        # An aggregate purchase heading names the package, not an individual
        # product whose spelling can be compared to one metadata identity.
        if re.search(r'(?:등|외)\d+(?:건|종|품목)', name):
            continue
        specification_names.append((name, span))
        if field[1] != '제품명' and meta_names and not any(n in name or name in n for n in meta_names):
            review.append({'kind': 'body_purchase_name_not_resolved_by_metadata', 'evidence': span})
    if (len({name for name, _ in specification_names}) > max(1, len(meta_names))
            and not product['purchase_item_lists']):
        review.extend({'kind': 'multiple_specification_products_not_accounted_for', 'evidence': span}
                      for _, span in specification_names)
    for di, doc in enumerate(record['docs']):
        if doc['type'] not in {'규격서', '과업지시서', '제안요청서', '시방서'}:
            continue
        software = re.search(r'소프트웨어|\bs/?w\b', doc['text'], re.I)
        if not software:
            continue  # A physical capture/heating system is not a SW task.
        for line in re.finditer(r'[^\r\n]+', doc['text']):
            text = norm(line[0])
            if re.search(r'(?:신고|등록)(?:한|된|을필한)(?:자|업체)|참가자격|사업자등록', text):
                continue  # A supplier's registered industry is not a procured task.
            if (re.search(r'(?:시스템|플랫폼|소프트웨어|s/?w).{0,25}(?:개발|설계)', text)
                    and not re.search(r'실적|경험|기존|평가|교육|경우|예시|아니|않|제외|설계서|설계도|'
                                      r'사용자|가능|활용|실험|연구목적', text)):
                review.append({'kind': 'actual_system_development_candidate',
                               'evidence': sme.evidence(record, di, line.start(), line.end())})
    return review


def band_estimate(product):
    """One applicability band for an estimate left unresolved by its VAT basis.

    A whole-contract estimate stated without its VAT basis reads as the stated
    amount or 1/1.1 of it; one stated with VAT reads as 1/1.1 of it; the
    registered 입찰추정가격 excludes VAT. When every reading of every observed
    estimate agrees on each threshold used below (2천만, 1억, 고시금액), the band
    is established although the exact price is not. Literal errors stay open.
    """
    prices = (product.get('project_prices') or {}).get('estimated_price')
    if not prices or prices.get('unresolved_literal') or prices.get('value_won') is not None:
        return None, []
    body = [b for b in prices['body'] if b['scope'] == 'whole' and b['won'] is not None]
    if prices.get('effective_source') == 'notice':
        body = [b for b in body if b['evidence']['document_role'] == '공고문']
    readings = set()
    for b in body:
        if b['vat'] in ('excluded', 'unspecified'):
            readings.add(b['won'])
        if b['vat'] in ('included', 'unspecified'):
            readings.add(b['won'] / 1.1)
    if prices['meta']['won'] is not None:
        readings.add(prices['meta']['won'])
    sides = {(v > 20_000_000, v >= FLOOR, v > FLOOR, v >= NOTICE) for v in readings}
    if not readings or len(sides) != 1 or min(readings) <= 0:
        return None, sorted(readings)
    meta = prices['meta']['won']
    return (meta if meta is not None else min(readings)), sorted(readings)


def infer(record, baseline, pf, *, product_override=None):
    result = dict(baseline)
    parts = inventory(record)
    product = purchase_scope(record, pf, parts[0], parts[4])
    if product_override is not None:
        product = product_override
    eligibility = qualification_facts(record, parts)
    decisions, deferred = {}, {}
    meta = record.get('meta', {})
    estimate = product['estimate_won']
    if estimate is None:
        estimate, readings = band_estimate(product)
        if estimate is not None:
            eligibility['estimate_band_readings'] = readings
    allowed = set(eligibility['allowed'] or [])
    from .contracting_principal import review as contracting_principal_review
    contracting_principal = contracting_principal_review(record)
    ordinary = (applicable_law(record) in {'국가계약법', '지방계약법'}
                and meta.get('업무구분') in {'일반용역', '물품(내자)'}
                and not contracting_principal['outside_public_purchase_checks'])
    actual_small_quote = bool(eligibility['quote_evidence']) and meta.get('계약방법') == '수의계약'
    disclosed_small_route = actual_small_quote and estimate is not None and estimate <= 20_000_000 and bool(re.search(r'2천만원이하|2천만\s*원\s*이하', str(meta.get('조항호내용'))))
    # A private contract registered under 국가계약법 시행령 제26조①5가 above 2천만원
    # either contracts only with small/micro enterprises or is one another
    # provision permits (판로지원법 시행령 제2조의3①3, basis registered per ②).
    # Neither leaves a missing small-enterprise restriction to report, but only
    # within the registered amount range; an estimate above it is not that route.
    route = re.match(r'추정가격2천만원초과(1억원|5천만원)이하물품[·ㆍ.]?용역\(', norm(str(meta.get('조항호내용') or '')))
    registered_private_route = bool(meta.get('계약방법') == '수의계약' and route and estimate is not None
        and estimate <= {'1억원': 100_000_000, '5천만원': 50_000_000}[route[1]])
    exception_review = [e for e in eligibility['exceptions'] if sme.requires_exception_review(e)
                        and e['kind'] != 'unrelated_statutory_reference']
    supplier_review = specific_supplier_quote_review(record, eligibility, estimate)
    exception_review += supplier_review
    # A separately eligible non-profit branch can only broaden the bidder set.
    # It cannot undo the already observed fact that ordinary commercial medium
    # enterprises are allowed.  Other statutory or supplier exceptions remain
    # unresolved and continue to block an automatic v17 conclusion.
    commercial_size_exception_review = [e for e in exception_review
                                        if e.get('kind') != 'nonprofit_alternative']
    v17_exception_review = commercial_size_exception_review
    eligibility['commercial_size_exception_review'] = commercial_size_exception_review
    eligibility['specific_supplier_quote_review'] = supplier_review
    eligibility['v17_exception_review'] = v17_exception_review
    quote = lambda spans: next((clean_evidence(s['text'], record) for s in spans if clean_evidence(s['text'], record)), '')
    size_evidence = [e['evidence'] for e in eligibility['active_size']]
    size_evidence += [e['evidence'] for e in eligibility['entity_qualification_obligations']]
    if eligibility['ordinary_commercial_size_bound']:
        size_evidence += eligibility['ordinary_commercial_size_bound']['evidence']
    direct_evidence = [e['evidence'] for e in eligibility['direct_certificate_coverage']['observations']
        if e['production_required_in_every_branch']]
    def put(item, value, why, evidence=()):
        if item == 18 and value:
            review = general_absence_review(record, product)
            if review:
                deferred['v18'] = {'reason': 'general_purchase_premise_not_closed',
                                  'review': review, 'catalog_membership_certified': False}
                return
        text = quote(evidence) if value and item not in ABSENCE else ''
        if value and item not in ABSENCE and not text:
            return
        decisions[f'v{item}'] = {'value': value, 'reason': why, 'evidence': text}
        result[f'v{item}'], result[f'e{item}'] = str(value), text

    if eligibility['no_operative_size_prerequisite']:
        for item in (13, 14, 15, 17):
            put(item, 0, 'operative_bidder_size_restriction_prerequisite_absent')

    if contracting_principal['outside_public_purchase_checks']:
        # Items 10--18 ask whether the public purchaser omitted or imposed a
        # procurement condition.  Here the notice expressly says that the
        # public body only conducts the bid and is not the contracting buyer.
        for item in range(10, 19):
            put(item, 0, 'verified_external_principal_outside_public_purchase_checks')
    elif ordinary:
        state = product['status']
        if state == 'general':
            for item in (10, 11, 13):
                put(item, 0, 'identified_purchase_outside_conditional_catalog')
            if direct_evidence:
                put(12, 1, 'general_purchase_with_operative_direct_certificate', direct_evidence)
            common_restriction = bool(eligibility['common_size_bound'])
            if estimate is not None:
                if estimate >= NOTICE and (common_restriction or allowed and not eligibility['size_conflict']):
                    if commercial_size_exception_review:
                        # The size set covers commercial bidders. A stated
                        # alternative/exception may change the complete scope;
                        # it was already extracted and must not be discarded
                        # only in this price band. Neither certify a waiver nor
                        # overwrite the preserved model's value with zero.
                        deferred['v14'] = {'reason': 'priority_exception_requires_review',
                            'evidence': [e['evidence'] for e in commercial_size_exception_review],
                            'waiver_certified': False}
                    else:
                        put(14, 1, 'general_purchase_above_notice_with_SME_restriction', size_evidence)
                elif (allowed and not eligibility['size_conflict'] and FLOOR <= estimate < NOTICE
                      and 'medium' not in allowed and not commercial_size_exception_review
                      and not actual_small_quote):
                    put(15, 1, 'general_middle_band_excludes_medium', size_evidence)
                elif (estimate < FLOOR and not v17_exception_review and not disclosed_small_route
                      and ((allowed and not eligibility['size_conflict'] and 'medium' in allowed)
                           or eligibility['explicit_medium_permissions'])):
                    medium_evidence = ([e['evidence'] for e in eligibility['explicit_medium_permissions']]
                        if eligibility['explicit_medium_permissions'] else size_evidence)
                    reason = ('general_low_band_explicit_medium_permission_with_conflict_preserved'
                              if eligibility['size_conflict'] else 'general_low_band_includes_medium')
                    put(17, 1, reason, medium_evidence)
            if disclosed_small_route:
                for item in (16, 18):
                    put(item, 0, 'documented_actual_small_quote_priority_exception_route')
            elif registered_private_route:
                for item in (16, 18):
                    put(item, 0, 'registered_private_contract_route_not_a_missing_restriction')
            elif eligibility['no_size'] and not exception_review and estimate is not None and estimate > 20_000_000:
                if FLOOR <= estimate < NOTICE:
                    put(16, 1, 'complete_general_middle_band_no_size_requirement')
                elif estimate < FLOOR:
                    put(18, 1, 'complete_general_low_band_no_size_requirement')
        elif (state == 'unknown' and eligibility['meta_sme_priority_route']
              and not actual_small_quote and eligibility['no_size']
              and not exception_review and estimate is not None
              and 20_000_000 < estimate < NOTICE):
            # Scoring/submission headings can strand a later bidder condition.
            # Do not certify absence while a size-certificate holder predicate
            # still needs its source role resolved. Bare document lists and
            # optional documents are not such predicates. This local guard
            # neither promotes purchase identity nor changes the other items.
            mentions = [e for e in eligibility['inventory'] if e.get('size')
                and re.search(r'(?:소지|보유)(?:한|하고있는|하고있어야하는)?(?:업체|자)',
                              norm(e['evidence']['text']))]
            item = 16 if estimate >= FLOOR else 18
            if mentions:
                deferred[f'v{item}'] = {
                    'reason': 'meta_priority_route_size_mentions_require_review',
                    'evidence': [e['evidence'] for e in mentions],
                    'waiver_certified': False}
            else:
                put(item, 1, 'registered_SME_priority_route_complete_no_size_requirement')
        elif state == 'competition':
            for item in (12, 14, 15, 16, 17, 18):
                put(item, 0, 'identified_purchase_in_conditional_catalog')
            if actual_small_quote and eligibility['no_direct']:
                from .production_scope import quote_requirement
                review = quote_requirement(record, product, actual_quote=True)
                eligibility['direct_production_quote_review'] = review
                if review['status'] == 'required':
                    put(10, 1, 'specified_private_contract_without_direct_production_requirement')
                elif review['status'] == 'below_trigger_amount':
                    put(10, 0, 'specified_private_contract_below_direct_production_trigger')
                else:
                    deferred['v10'] = {'reason': 'private_contract_direct_production_applicability_unresolved',
                        'review': review, 'waiver_certified': False}
            if not actual_small_quote:
                if eligibility['no_direct']:
                    put(10, 1, 'complete_eligibility_without_possession_requirement')
                if eligibility['no_size']:
                    put(11, 1, 'complete_eligibility_without_SME_restriction')
                if (eligibility['notice_commercial_size_bound']
                        and not commercial_size_exception_review):
                    put(13, 1, 'competition_notice_excludes_ordinary_medium_enterprises',
                        eligibility['notice_commercial_size_bound']['evidence'])
                elif (allowed and 'medium' not in allowed and not eligibility['size_conflict']
                        and not commercial_size_exception_review):
                    put(13, 1, 'competition_excludes_ordinary_medium_enterprises', size_evidence)
                elif (eligibility['ordinary_commercial_size_bound']
                      and not eligibility['size_conflict']):
                    put(13, 1, 'competition_excludes_ordinary_medium_with_special_entity_alternatives',
                        eligibility['ordinary_commercial_size_bound']['evidence'])
        if state == 'competition':
            # A certificate for a different code cannot clear the obligation.
            targets = {p['code'] for p in product['products']}
            direct_codes = set(eligibility['direct_certificate_coverage']['guaranteed_codes'])
            direct_codes.update(code for requirement in eligibility['incorporated_direct_requirements']
                                for code in requirement['guaranteed_codes'])
            if targets and targets <= direct_codes:
                put(10, 0, 'all_identified_targets_have_possession_requirement')
        if allowed or eligibility['common_size_bound'] or eligibility['ordinary_commercial_size_bound']:
            for item in (11, 16, 18):
                put(item, 0, 'operative_size_restriction_present_dates_separate')
        from .small_quote import review as small_quote_review
        quote_review = small_quote_review(record, estimate,
            allowed if not eligibility['size_conflict'] and eligibility['common_size_bound'] else None)
        eligibility['small_quote_v13_review'] = quote_review
        if quote_review['status'] == 'permitted_small_size_route':
            put(13, 0, quote_review['reason'])
        from .production_scope import unqualified_verification_requirements
        verification = unqualified_verification_requirements(record, product, eligibility)
        if verification:
            eligibility['unqualified_production_verification'] = verification
            put(10, 0, 'operative_unqualified_production_verification_present',
                [v['evidence'] for v in verification])

    # Block guard: on any failure keep the values from before the block (the run must always write its CSV).
    b_before = {f'{p}{i}': result.get(f'{p}{i}') for i in range(10, 19) for p in ('v', 'e')}
    try:
        # Size and competition-family positives added on top of the decisions above
        # (runs/rebuild_20260924/track_b/DESIGN_B.md).
        # v15/v16/v18 for general purchases (an unresolved identity without competition evidence counts as
        # general) that the competition-service classifier does not identify as a catalog service. 'No size
        # restriction' means no observed size bound, obligation, procedure or explicit medium permission.
        # Competition family (on): v11 (no size restriction) and v13 (small-only restriction) for
        # services the classifier identifies as competition services with their catalog condition met, and v10
        # when no direct-production requirement is observed (the engine's absence reading) or no document of the
        # notice mentions 직접생산 at all.
        # General purchases outside those services also get v12 (a direct-production requirement in every branch)
        # and v14 (estimate at or above 2.3억 with an SME restriction).
        # Private contracts and records with a commercial exception review are left unchanged; a non-profit
        # alternative only broadens the bidder set and does not block these commercial-bidder rules.
        if ordinary and meta.get('계약방법') != '수의계약' and not commercial_size_exception_review:
            from .csc_probe import classify as classify_competition_service
            s1_price = estimate if estimate is not None else meta.get('입찰추정가격')
            s1_service = classify_competition_service(record, getattr(pf, 'products', None))
            s1_competition_service = s1_service.get('competition') is True
            # Goods: a direct-production requirement shows the purchaser treats the item as a competition product, so an
            # unresolved goods identity with that requirement is not general. Services are settled by the classifier.
            s1_general = product['status'] == 'general' or (product['status'] == 'unknown' and not any(
                p['condition']['status'] in ('met', 'no_stated_condition') for p in product.get('products', []))
                and not (meta.get('업무구분') == '물품(내자)' and direct_evidence))
            # A raw size mention inside the eligibility section is treated as an unparsed restriction: 'no size
            # restriction' (v11, v16, v18) needs none there. Mentions elsewhere (document lists) do not block.
            s1_sized = bool(allowed or eligibility['common_size_bound'] or eligibility['ordinary_commercial_size_bound']
                            or eligibility['entity_qualification_obligations'] or eligibility['operative_procedure_size']
                            or eligibility['explicit_medium_permissions']
                            or any(e.get('section_role') == 'eligibility' for e in eligibility.get('raw_size') or []))
            s1_small_only = bool(allowed) and 'medium' not in allowed and not eligibility['size_conflict']
            if isinstance(s1_price, (int, float)) and s1_price > 0:
                if s1_general and not s1_competition_service:
                    if FLOOR <= s1_price < NOTICE:
                        if s1_small_only:
                            result['v15'], result['e15'] = '1', result.get('e15') or quote(size_evidence)
                        if not s1_sized:
                            result['v16'], result['e16'] = '1', ''
                    elif 20_000_000 < s1_price < FLOOR and not s1_sized:
                        result['v18'], result['e18'] = '1', ''
                    s1_medium = ('medium' in allowed and not eligibility['size_conflict']) or bool(eligibility['explicit_medium_permissions'])
                    if s1_price >= NOTICE and (s1_small_only or s1_medium or eligibility['common_size_bound']
                                               or eligibility['ordinary_commercial_size_bound']):
                        result['v14'], result['e14'] = '1', result.get('e14') or quote(size_evidence)
                    if 20_000_000 < s1_price < FLOOR and s1_medium:
                        result['v17'], result['e17'] = '1', result.get('e17') or quote(size_evidence)
                    if direct_evidence:
                        result['v12'], result['e12'] = '1', result.get('e12') or quote(direct_evidence)
                if s1_competition_service:
                    if not s1_sized:
                        result['v11'], result['e11'] = '1', ''
                    if s1_small_only and not eligibility['quote_evidence']:
                        result['v13'], result['e13'] = '1', result.get('e13') or quote(size_evidence)
                    if eligibility['no_direct'] or not re.search(r'직접\s*생산', ' '.join(d.get('text', '') for d in record.get('docs', []))):
                        result['v10'], result['e10'] = '1', ''
    except Exception:
        result.update(b_before)
    return result, {'product': product, 'qualification': eligibility,
                    'contracting_principal': contracting_principal, 'decisions': decisions,
                    'deferred_decisions': deferred,
                    'exception_review_flags_are_not_waivers': True,
                    'saved_model_response_unchanged': True}
