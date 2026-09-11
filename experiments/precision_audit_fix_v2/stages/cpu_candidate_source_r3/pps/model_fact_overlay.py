"""Positive-only source predicates joined to a fallible categorical model fact.

No identifiers, labels, history, filesystem reads or new model calls. A model
assertion never overrides resolved catalog scope, mixed purchases or exceptions.
"""
import json
from pps.data import clean_evidence
from .fact_contract import PRODUCT as PRODUCT_FIELD, general_purchase


def overlay(record,row,response,source_facts,items,*,spans=(),allow_legacy=False):
    result=dict(row)
    log={'applied':[],'model_fact_is_fallible':True,'source_scope_promoted':False}
    def stop(reason):
        log['gate']=reason
        return result,log
    if response.get('finish_reason') not in ('stop','eos_token'):
        return stop('incomplete_response')
    value=json.loads(response['text']).get('facts',{}).get(PRODUCT_FIELD)
    claim=general_purchase(value,record,spans)
    if claim is None:
        from .legacy_model_fact import categorical_general
        if not allow_legacy or not categorical_general(value):
            return stop('no_typed_source_bound_whole_general_purchase_fact')
        log['legacy_v1_compatibility_path']=True
    else:
        log['typed_purchase_claim']=claim
    product=source_facts['product']; eligibility=source_facts['qualification']
    if claim is not None:
        import re
        location=claim['source']
        whole_titles=[s for s in product.get('scope_evidence', [])
                      if s['doc_index']==location['doc_index']
                      and record['docs'][s['doc_index']]['type']=='공고문'
                      and re.search(r'(?:사업명|용역명|과업명|입찰건명|구매품목|품명)[:：|]', re.sub(r'\s+', '', s['text']))
                      and s['start'] <= location['start'] < location['end'] <= s['end']]
        if not whole_titles:
            return stop('whole_purchase_claim_not_bound_to_notice_purchase_field')
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
