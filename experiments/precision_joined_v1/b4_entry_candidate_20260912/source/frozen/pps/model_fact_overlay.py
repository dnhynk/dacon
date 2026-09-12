"""Positive-only source predicates joined to a fallible categorical model fact.

No identifiers, labels, history, filesystem reads or new model calls. A model
assertion never overrides resolved catalog scope, mixed purchases or exceptions.
"""
import json
import re
from pps.data import clean_evidence
from pps.sme import norm

PRODUCT_FIELD='실제구매대상_경쟁제품_고시조건'
UNCERTAIN=re.compile(r'불명|불확실|확인불가|확인되지|판단불가|가능성|여부|아닐수|아닐가능|해당하지않을|추정됨|추정된다|추정함|보임|일부|주된')
NEGATIVE=re.compile(r'경쟁제품(?:\([^)]{1,30}\))?(?:에해당하지않(?:는|음|습니다)|해당없음|에해당없음|이아닌|이아님|이아니다)')
POSITIVE=re.compile(r'경쟁제품(?:에해당(?:함|하는|한다)|임|이다|으로지정)')

def categorical_general(value):
    """Reject meta-statements, re-negation and unresolved qualifications.

    A matched substring inside a claim about somebody else's assertion is not
    an assertion by this response. The remaining source gates are still required.
    """
    if not isinstance(value, str):
        return False
    text = norm(value)
    discourse = re.compile(r'단정|주장|인용|틀렸|오류|부정|검토|다만|하지만|그러나|반면|별도|판단할수없|확정할수없|아니라고|않는다고|않음으로|않는다는|해당할수|지정대상|지정된대상')
    negative = NEGATIVE.search(text)
    if negative is None:
        return False
    remainder = text[:negative.start()] + text[negative.end():]
    return bool(not UNCERTAIN.search(text) and not POSITIVE.search(text)
                and not discourse.search(text) and not re.search(r'아니|아닌|아닙|않', remainder))

def overlay(record,row,response,source_facts,items):
    result=dict(row)
    log={'applied':[],'model_fact_is_fallible':True,'source_scope_promoted':False}
    def stop(reason):
        log['gate']=reason
        return result,log
    if response.get('finish_reason') not in ('stop','eos_token'):
        return stop('incomplete_response')
    value=json.loads(response['text']).get('facts',{}).get(PRODUCT_FIELD)
    if not isinstance(value,str): return stop('missing_model_fact')
    text=norm(value)
    if not categorical_general(value):
        return stop('noncategorical_or_conflicting_model_fact')
    product=source_facts['product']; eligibility=source_facts['qualification']
    if record.get('meta',{}).get('업무구분')!='일반용역': return stop('outside_service_scope')
    if record.get('meta',{}).get('적용계약법') not in ('국가계약법','지방계약법'): return stop('unknown_contract_law')
    if product['status']!='unknown': return stop('resolved_source_scope_preserved')
    if product['uncertainty']: return stop('source_purchase_conflict')
    if any(p.get('listed') and p.get('condition',{}).get('status') in ('met','no_stated_condition') for p in product['products']):
        return stop('supported_competition_component')
    if not eligibility['complete']: return stop('incomplete_input')
    if eligibility['size_conflict']: return stop('source_size_conflict')
    if any(e['kind']!='priority_exception_denied' for e in eligibility['exceptions']): return stop('exception_requires_resolution')
    from .prices import in_band
    amount=product['estimate_won']
    prices=product.get('project_prices',{}).get('estimated_price',
        {'candidate_values_won': [amount] if amount is not None else []})
    high=in_band(prices,lower=230_000_000)
    middle=in_band(prices,lower=100_000_000,upper=230_000_000)
    low=in_band(prices,lower=20_000_001,upper=100_000_000)
    if high is not True and middle is not True and low is not True:
        return stop('unresolved_estimate_band')
    targets=[]
    if high is True and eligibility['allowed']:
        evidence=next((clean_evidence(e['evidence']['text'],record) for e in eligibility['active_size'] if clean_evidence(e['evidence']['text'],record)),'')
        if evidence: targets.append((14,evidence,'source_amount_and_operative_SME_restriction'))
    if eligibility['no_size'] and eligibility['closed_eligibility'] and not eligibility['quote_evidence']:
        if middle is True: targets.append((16,'','complete_middle_band_without_size_requirement'))
        elif low is True: targets.append((18,'','complete_low_band_without_size_requirement'))
    for item,evidence,reason in targets:
        if item in items and not int(result[f'v{item}']):
            result[f'v{item}']='1'; result[f'e{item}']=evidence
            log['applied'].append({'item':item,'reason':reason,'model_fact':value,'source_evidence':evidence})
    return stop('source_predicates_joined_to_model_assertion' if log['applied'] else 'no_new_supported_positive')
