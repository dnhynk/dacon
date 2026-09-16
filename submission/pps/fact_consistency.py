"""Preserve uncertainty when a positive contradicts its facts or catalog lookup.

The model summary is fallible. This never resolves catalog/SW scope or declares
legal normality. Independent source proof takes precedence; source unknown alone
does not reject a model judgment. No notice identifiers, labels or I/O are used.
"""
import re
import unicodedata

from .response_contract import loads

PRODUCT_FIELD = '실제구매대상_경쟁제품_고시조건'
COMPETITION_ITEMS = (10, 11, 13)
GENERAL_ITEMS = (12, 14, 15, 16, 17, 18)
SW_FIELDS = ('계약상_SW산출물_주체_의무_원문구간', '도구_교육내용_기존장비_조건부과업과의구별',
             '하한제도_적용근거_안내의실제존재_미확정정보', '실제SW사업_하한제도기재')
UNKNOWN = r'(?:불명확|불분명|미확정|확인불가|확인되지않|판단할수없)'
CATALOG_UNKNOWN = re.compile(
    r'(?:경쟁제품(?:해당)?여부|구매대상(?:과의)?(?:동일성|일치여부))'
    r'(?:가|이|는|은)?' + UNKNOWN + r'(?:함|하다|합니다|음|다)?[.。]?$|'
    r'경쟁제품에해당할수있(?:음|다|습니다)?[.。]?$', re.I)
SW_UNKNOWN = re.compile(
    r'(?:SW|소프트웨어)(?:사업|과업)(?:해당)?여부(?:가|이|는|은)?'
    + UNKNOWN + r'(?:함|하다|합니다|음|다|하여|하며|하고|하므로)?(?=$|[,，.。]|하한|대기업|SW|소프트웨어)', re.I)
MIXED = re.compile(r'일부|나머지|구성품|부분품|복수|한편|다만|하지만|그러나|반면|예시')
DISCOURSE = re.compile(r'(?<![가-힣])(?:가정|인용|주장)(?=$|[^가-힣]|(?:하|한|된|했|임|인|일|은|을|에|의))')
DISCOURSE_VERB = re.compile(r'(?:라고|을|를)(?:가정|인용|주장)(?:하|한|했|함)')
DENIED_UNKNOWN = re.compile(r'(?:불명확|불분명|미확정)(?:하지않|하지아니|한것은아니)')
NEGATED_COMPETITION = re.compile(
    r'(?:경쟁제품|후보군)(?:에|이|가|은|는)?(?:해당하지않|해당하지아니|아니라|아님)|'
    r'일반(?:제품|용역)(?:임|이다|에해당)')
POSITIVE_COMPETITION = re.compile(
    r'(?:경쟁제품|후보군)(?:에|이|가|은|는)?(?:해당|조건충족)|'
    r'(?:경쟁제품|후보군)(?:임|이다)')
MULTI_PURCHASE_UNCERTAINTY = {
    'explicit_multiple_items_not_all_identified',
    'mixed_or_differently_conditioned_purchase_candidates',
}


def compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def qualified_statement(text):
    # Word roles need the original lexical boundary. "성인용" and "가정용"
    # are product modifiers, not quoted claims or hypothetical assumptions.
    normalized = unicodedata.normalize('NFKC', text)
    return bool(MIXED.search(compact(text)) or DISCOURSE.search(normalized)
                or DISCOURSE_VERB.search(compact(text)))


def uncertain_statement(text, kind):
    """A missing title/code or hypothetical exception is not this signal."""
    if not isinstance(text, str):
        return None
    normalized = compact(text)
    if qualified_statement(text) or DENIED_UNKNOWN.search(normalized):
        return None
    match = (CATALOG_UNKNOWN if kind == 'catalog' else SW_UNKNOWN).search(normalized)
    return {'model_summary': text, 'matched_declaration': match[0]} if match else None


def unlisted_purchase_claim(text, product):
    """Check an explicit model code against its supplied purchase lookup.

    A source-unknown mixed purchase may contain other, unidentified items. Do
    not promote it to general. Equally, a model's cited unlisted code cannot
    certify competition membership. A separate listed/coded component or an
    explicit distinction between parts keeps the claim for semantic review.
    """
    if not isinstance(text, str) or product.get('catalog_scope') != 'supplied_catalog_only':
        return None
    if product['status'] != 'unknown' or qualified_statement(text):
        return None
    # An independently named catalog component or conflicting purchase identity
    # must be resolved before judging the code claim's effect on the whole item.
    if set(product.get('uncertainty', [])) - {'explicit_multiple_items_not_all_identified'}:
        return None
    codes = set(re.findall(r'(?<!\d)\d{10}(?!\d)', text))
    rows = product.get('products', [])
    if not codes or not rows or any(row.get('listed') is not False for row in rows):
        return None
    unlisted = {row['code'] for row in rows if row.get('condition', {}).get('status') == 'unlisted'}
    named = set(product.get('paired_meta_purchase_codes', []))
    if not codes <= unlisted & named:
        return None
    return {'model_summary': text, 'matched_declaration': sorted(codes),
            'lookup_status': 'not_listed_in_supplied_catalog',
            'whole_purchase_status': 'unknown', 'other_purchase_items_inferred': False}


def unresolved_multi_item_claim(text, product, qualification):
    """Reject a whole-purchase positive that skips unidentified purchase rows.

    The source parser deliberately keeps an explicit ``N items`` purchase
    unknown until every operative row is linked to the supplied catalog.  A
    model summary that simply promotes one metadata/registration candidate to
    the whole purchase does not close that gap.  This gate is limited to fully
    supplied inputs and leaves an explicitly component-qualified model summary
    for semantic review.
    """
    if (not isinstance(text, str) or product.get('catalog_scope') != 'supplied_catalog_only'
            or product.get('status') != 'unknown'
            or not qualification or qualification.get('complete') is not True):
        return None
    uncertainty = set(product.get('uncertainty', []))
    if uncertainty != {'explicit_multiple_items_not_all_identified'} or qualified_statement(text):
        return None
    rows = product.get('products', [])
    certified = [row for row in rows if row.get('listed') and
                 row.get('condition', {}).get('status') in {'met', 'no_stated_condition'}]
    if certified:
        return None
    return {'model_summary': text,
            'matched_declaration': 'explicit_multiple_items_not_all_identified',
            'lookup_status': 'no_source_certified_competition_target',
            'candidate_condition_statuses': sorted({
                row.get('condition', {}).get('status', 'missing') for row in rows}),
            'whole_purchase_status': 'unknown', 'other_purchase_items_inferred': False}


def unresolved_conditional_catalog_claim(text, product, qualification):
    """Reject an unconditional positive that skips a decisive condition conflict.

    Unbound specification properties do not establish the identity or scope of
    a purchase and therefore never promote a source result to ``general``.  They
    can still disprove the model's stronger assertion that the exactly paired,
    single catalog row satisfies its condition when the closed predicate is
    already false under every parsed observation needed for that branch.  The
    public result remains unresolved (the competition bits emit 0); the source
    facts and their original unbound scope are left unchanged.
    """
    if (not isinstance(text, str) or product.get('catalog_scope') != 'supplied_catalog_only'
            or product.get('status') != 'unknown' or product.get('uncertainty')
            or not qualification or qualification.get('complete') is not True
            or qualified_statement(text)):
        return None
    rows = product.get('products', [])
    if len(rows) != 1:
        return None
    row = rows[0]
    condition = row.get('condition', {})
    code = str(row.get('code') or '')
    paired = {str(value) for value in product.get('paired_meta_purchase_codes', [])}
    if (row.get('listed') is not True or condition.get('status') != 'unknown'
            or not condition.get('expression') or not code or paired != {code}):
        return None
    normalized = compact(text)
    name = compact(str(row.get('name') or ''))
    if not (code in normalized or name and name in normalized):
        return None
    if not re.search(r'경쟁제품|고시조건', normalized):
        return None
    source_facts = condition.get('source_facts', {})
    observations = source_facts.get('observations', [])
    if (not observations or source_facts.get('whole_purchase_certified') is not False
            or any(x.get('scope') != 'unbound_property_mention' for x in observations)):
        return None
    usable = [x for x in observations if not x.get('issue') and x.get('value') is not None]
    if not usable:
        return None
    # This copy is a counterfactual consistency check only.  Never mutate or
    # promote the original observation scope used by purchase qualification.
    diagnostic = [{**x, 'scope': 'entire_named_purchase'} for x in usable]
    from .catalog_predicates import evaluate
    if evaluate(condition['expression'], diagnostic) is not False:
        return None
    return {'model_summary': text, 'matched_declaration': code,
            'lookup_status': 'listed_condition_applicability_unresolved',
            'condition_result_if_observed_properties_apply': False,
            'observed_property_scopes': sorted({x['scope'] for x in observations}),
            'observations': usable, 'whole_purchase_status': 'unknown',
            'scope_promoted': False, 'normality_certified': False}


def contradictory_general_branch_claim(text, values, product):
    """Find a model-internal competition/general applicability contradiction.

    Items 10/11/13 require a competition product, while 12/14--18 require a
    general product.  Both branches may legitimately occur in a mixed
    purchase, so this check is deliberately narrower: the source must expose
    at least one condition-satisfied catalog candidate, the model's own fact
    must make an unqualified positive competition-family assertion, and the
    same raw response must turn on both branches.  It only rejects the
    contradictory general positives; it never promotes a competition bit.
    """
    if (not isinstance(text, str) or product.get('catalog_scope') != 'supplied_catalog_only'
            or product.get('status') != 'unknown'
            or set(product.get('uncertainty', [])) & MULTI_PURCHASE_UNCERTAINTY
            or qualified_statement(text)):
        return None
    normalized = compact(text)
    if NEGATED_COMPETITION.search(normalized) or not POSITIVE_COMPETITION.search(normalized):
        return None
    eligible = [row for row in product.get('products', [])
                if row.get('listed') is True and
                row.get('condition', {}).get('status') in {'met', 'no_stated_condition'}]
    if not eligible:
        return None
    competition = [item for item in COMPETITION_ITEMS if values.get(item) == 1]
    general = [item for item in GENERAL_ITEMS if values.get(item) == 1]
    if not competition or not general:
        return None
    return {
        'model_summary': text,
        'matched_declaration': POSITIVE_COMPETITION.search(normalized)[0],
        'competition_positive_items': competition,
        'general_positive_items': general,
        'eligible_catalog_candidates': [
            {'code': row.get('code'), 'name': row.get('name'),
             'condition_status': row.get('condition', {}).get('status')}
            for row in eligible
        ],
        'whole_purchase_status': 'unknown',
    }


def review(facts, values, product=None, sw=None, qualification=None):
    """Check explicit uncertainty and verifiable code claims, not topic similarity."""
    flags = []
    if product is not None:
        statement = uncertain_statement(facts.get(PRODUCT_FIELD), 'catalog')
        component = any(p.get('listed') and p.get('condition', {}).get('status')
                        in {'met', 'no_stated_condition'} for p in product.get('products', []))
        if (statement and product['status'] == 'unknown' and not product['uncertainty'] and not component):
            for item in (10, 11, 13):
                if values.get(item) == 1:
                    flags.append({'item': item, 'field': PRODUCT_FIELD, **statement,
                        'necessary_fact': 'competition_product_applicability',
                        'source_status': product['status'], 'semantic_value': None})
        rejected = unlisted_purchase_claim(facts.get(PRODUCT_FIELD), product)
        if rejected:
            for item in (10, 11, 13):
                if values.get(item) == 1 and not any(f['item'] == item for f in flags):
                    flags.append({'item': item, 'field': PRODUCT_FIELD, **rejected,
                        'necessary_fact': 'competition_product_applicability',
                        'source_status': product['status'], 'semantic_value': None,
                        'reason': 'model_purchase_code_cannot_certify_competition_membership'})
        unresolved_multi = unresolved_multi_item_claim(
            facts.get(PRODUCT_FIELD), product, qualification)
        if unresolved_multi:
            for item in (10, 11, 13):
                if values.get(item) == 1 and not any(f['item'] == item for f in flags):
                    flags.append({'item': item, 'field': PRODUCT_FIELD, **unresolved_multi,
                        'necessary_fact': 'competition_product_applicability',
                        'source_status': product['status'], 'semantic_value': None,
                        'reason': 'model_whole_purchase_claim_skips_unidentified_items'})
        conditional_conflict = unresolved_conditional_catalog_claim(
            facts.get(PRODUCT_FIELD), product, qualification)
        if conditional_conflict:
            for item in (10, 11, 13):
                if values.get(item) == 1 and not any(f['item'] == item for f in flags):
                    flags.append({'item': item, 'field': PRODUCT_FIELD, **conditional_conflict,
                        'necessary_fact': 'competition_product_condition_applicability',
                        'source_status': product['status'], 'semantic_value': None,
                        'reason': 'model_competition_claim_omits_decisive_conditional_conflict'})
        branch_conflict = contradictory_general_branch_claim(
            facts.get(PRODUCT_FIELD), values, product)
        if branch_conflict:
            for item in branch_conflict['general_positive_items']:
                if not any(f['item'] == item for f in flags):
                    flags.append({'item': item, 'field': PRODUCT_FIELD, **branch_conflict,
                        'necessary_fact': 'mutually_consistent_product_applicability',
                        'source_status': product['status'], 'semantic_value': None,
                        'reason': 'same_model_response_selects_incompatible_product_branches'})
    if sw is not None and values.get(20) == 1:
        statements = [(name, uncertain_statement(facts.get(name), 'software')) for name in SW_FIELDS]
        statements = [(name, value) for name, value in statements if value]
        if statements and sw['value'] is None and not sw['facts']['actual_work']:
            name, statement = statements[0]
            flags.append({'item': 20, 'field': name, **statement,
                'necessary_fact': 'actual_software_procurement', 'source_status': sw['reason'],
                'semantic_value': None})
    return flags


def apply(row, response, raw_values, *, product=None, sw=None, qualification=None):
    """Apply only the declared unresolved output policy; retain raw assertions."""
    result = dict(row)
    obj = loads(response['text'])
    facts = obj.get('facts', {})
    flags = review(facts, raw_values, product, sw, qualification)
    details = []
    for flag in flags:
        item = flag['item']
        before = result[f'v{item}']
        changed = int(before) == 1
        if changed:
            result[f'v{item}'] = '0' if isinstance(before, str) else 0
            result[f'e{item}'] = ''
        details.append({**flag, 'applied': changed, 'raw_model_value': raw_values[item],
            'previous_consumer_value': int(before), 'public_value': int(result[f'v{item}']),
            'normality_certified': False, 'model_summary_is_fallible': True,
            'reason': flag.get('reason', 'required_applicability_explicitly_unresolved_in_model_facts'),
            'output_policy': 'Unresolved emits0; the original facts and model response remain in the trace.'})
    return result, details
