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
    if UNCERTAIN.search(text) or not NEGATIVE.search(text) or POSITIVE.search(text):
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
    amount=product['estimate_won']
    if amount is None: return stop('unresolved_estimate')
    targets=[]
    if amount>=230_000_000 and eligibility['allowed']:
        evidence=next((clean_evidence(e['evidence']['text'],record) for e in eligibility['active_size'] if clean_evidence(e['evidence']['text'],record)),'')
        if evidence: targets.append((14,evidence,'source_amount_and_operative_SME_restriction'))
    if eligibility['no_size'] and eligibility['closed_eligibility'] and not eligibility['quote_evidence']:
        if 100_000_000<=amount<230_000_000: targets.append((16,'','complete_middle_band_without_size_requirement'))
        elif 20_000_000<amount<100_000_000: targets.append((18,'','complete_low_band_without_size_requirement'))
    for item,evidence,reason in targets:
        if item in items and not int(result[f'v{item}']):
            result[f'v{item}']='1'; result[f'e{item}']=evidence
            log['applied'].append({'item':item,'reason':reason,'model_fact':value,'source_evidence':evidence})
    return stop('source_predicates_joined_to_model_assertion' if log['applied'] else 'no_new_supported_positive')
