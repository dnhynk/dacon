"""Optional V9 product relations; no legal judgment is inferred from an edge.

The original scope contract remains available as the matched control. This
version can represent a named component inside an otherwise generic assembly,
or a newly supplied replacement for an existing component.
"""
from __future__ import annotations

import copy

import jsonschema

from .data import clean_evidence
from .response_contract import loads
from . import specification_scope as base

NAME = 'specification_relations_v1'
FORMAT = 'specification_relations'
LIMIT = 6
RELATIONS = ('part_of', 'replacement_for', 'compatibility_with', 'none', 'unknown')

SYSTEM = base.SYSTEM.replace(
    'maintenance_target(기존 제품의 유지관리 대상), existing_reference(기존 인프라 설명만),',
    'maintenance_target(기존 제품의 유지관리 대상), existing_reference(기존 인프라 설명만),\n'
    'replacement_component(기존 설비 안의 부품을 대신해 이번에 납품하는 교체품),'
).replace(
    'attribute는 brand_or_model/performance/quantity/warranty/unknown,',
    'attribute는 brand_or_model/performance/quantity/warranty/manufacturer_consistency/unknown,'
).replace(
    'effect는 allowed/prohibited/conditional/unknown이다.',
    'effect는 allowed/prohibited/conditional/required/unknown이다.'
).replace('products와 permissions는 각각 최대4개다.', 'products와 permissions는 각각 최대6개다.'
).replace(base.NAME, NAME) + '''
[대상 사이의 관계]
완제품과 그 안의 고유 명칭 구성품은 구별되는 products로 기록한다. 기존 설비가
이번 납품 대상과 다른 경우에도 별도의 existing_reference 또는 maintenance_target으로
기록한다. 동일 대상을 여러 개 복제하거나 수량만 다른 같은 물품을 별개로 만들지 않는다.
각 product의 relation은 part_of(구성품→전체), replacement_for(신규 교체품→기존 부품),
compatibility_with(납품품→연결할 기존 설비), none(관계가 필요 없는 독립 대상), unknown이다.
related_product는 관계의 상대 product 번호다. none/unknown이면 related_product=0,
relation_sources=[]로 쓴다. 구성품·교체 관계가 순환하도록 연결하지 않는다.
relation_sources에는 해당 관계를 실제로 보여 주는 S번호를 쓴다. part_of 등 관계를
명시했으면 상대 번호와 관계 근거가 모두 있어야 한다. 자기 자신과 관계를 만들지 않는다.
기존 설비 설명만으로 그 설비 전체를 신규 구매한다고 바꾸지 않는다. 교체품도 이번에
납품되는 물품이지만 호환 대상과 교체 사유를 보존한다. 교체품·호환 요구·위원회 승인이라는
표현만으로 적용 예외가 확인됐다고 단정하지 않는다.

같은 회사 제품끼리 세트를 구성하라는 조건이나 부품 간 제조사 일치 조건은
manufacturer_consistency/required다. 그 자체는 한 특정 회사의 모델만 허용한다는 뜻이
아니다. 별도로 고유 명칭을 지정하는 원문이 있으면 해당 products에서 따로 판단한다.
완제품의 성능 대체 허용을 특정 구성품의 제조사 대체 허용으로 자동 상속하지 않는다.
다른 물품의 규격서에 있는 허용 조건을 적용하려면 그 물품과 연결하는 실제 범위 근거가
필요하다. products의 순서나 원문 거리가 그 연결을 증명하지 않는다.

product_inventory는 이번 발췌에서 판정에 필요한 대상 관계를 모두 담았으면 complete,
6개 제한 등으로 필요한 대상이 남으면 partial, 범위를 확인하지 못하면 unknown이다.
complete도 제공 문서 전체의 부재를 증명하지 않는다. 미확정은 unresolved에 남긴다.
판정은 관계를 확인한 뒤 마지막 judgment에 한 번 기록한다.
'''


def schema(max_evidence, items=(9,)):
    result = copy.deepcopy(base.schema(max_evidence, items))
    payload = result['properties'].pop(base.NAME)
    result['properties'][NAME] = payload
    result['required'] = [NAME]
    fields = payload['properties']
    product = fields['products']['items']
    props = product['properties']
    props['role']['enum'].append('replacement_component')
    refs = copy.deepcopy(fields['exemption_sources'])
    props.update(relation={'type': 'string', 'enum': list(RELATIONS)},
                 related_product={'type': 'integer', 'enum': list(range(LIMIT + 1))},
                 relation_sources=refs)
    product['required'] = list(props)
    fields['products']['maxItems'] = LIMIT if max_evidence else 0
    fields['permissions']['maxItems'] = LIMIT if max_evidence else 0
    permission = fields['permissions']['items']['properties']
    permission['product']['enum'] = list(range(LIMIT + 1))
    permission['attribute']['enum'].append('manufacturer_consistency')
    permission['effect']['enum'].append('required')
    # Keep all extraction fields before the final judgment in the grammar.
    judgment = fields.pop('judgment')
    fields['product_inventory'] = {'type': 'string', 'enum': ['complete', 'partial', 'unknown']}
    fields['judgment'] = judgment
    payload['required'] = list(fields)
    return result


def decode(text, spans, rec=None):
    obj = loads(text)
    try:
        jsonschema.validate(obj, schema(len(spans)))
    except jsonschema.ValidationError as exc:
        raise ValueError('Invalid product relation schema: ' + exc.message) from exc
    facts = base.decode_payload(obj[NAME], spans, rec)
    products = facts['products']
    for index, product in enumerate(products, 1):
        target = product['related_product']
        relation = product['relation']
        if type(target) is not int or not 0 <= target <= len(products) or target == index:
            raise ValueError('Product relation requires an integer target, not a nonexistent product or itself')
        if relation in {'none', 'unknown'}:
            if target or product['relation_sources']:
                raise ValueError('Unspecified product relation must not assert a link')
        elif not target or not product['relation_sources']:
            raise ValueError('Product relation requires an observed target and source link')
        product['relation_locations'] = base.source_locations(product['relation_sources'], spans)
    # Containment and replacement are directed. Compatibility can be reciprocal.
    parents = {i: p['related_product'] for i, p in enumerate(products, 1)
               if p['relation'] in {'part_of', 'replacement_for'}}
    for start in parents:
        visited = set()
        node = start
        while node in parents:
            if node in visited:
                raise ValueError('Product containment/replacement relations form a cycle')
            visited.add(node)
            node = parents[node]
    return facts


def review(rec, response, prompt):
    if tuple(prompt['items']) != (9,):
        raise ValueError('Product relation review may only consume v9')
    facts = decode(response['text'], prompt['spans'], rec)
    judgment = facts['judgment']
    evidence = ''
    if judgment['v'] and judgment['e']:
        span = prompt['spans'][judgment['e'] - 1]
        evidence = clean_evidence(span.text, rec, source=(span.doc_index, span.start, span.end))
    issues = base.relationship_diagnostics(facts, rec)
    for i, product in enumerate(facts['products'], 1):
        if product['role'] == 'replacement_component' and product['relation'] not in {
                'replacement_for', 'compatibility_with'}:
            issues.append({'kind': 'replacement_target_unresolved', 'product': i,
                           'semantic_truth_certified': False})
    return {'v9': judgment['v'], 'e9': evidence}, [{
        'source': 'source_addressed_specification_relations', 'facts': facts,
        'relationship_issues': issues, 'judgment_is_model_output': True,
        'semantic_validation_complete': False, 'new_source_evidence_inferred': False,
        'relations_imply_legal_exemption': False}]


def matched_prompts(rec, knowledge, config, tokenizer, source_selection):
    from .prompts import EVIDENCE_CONTRACT, token_ids
    from .rubrics import RUBRIC_V6, SYSTEM_V6

    control = base.prompts(rec, knowledge, config, tokenizer, source_selection)['specification_scope']
    messages = copy.deepcopy(control['messages'])
    messages[0]['content'] = (SYSTEM_V6 + '\n[항목별 판단 안내]\nv9 ' + RUBRIC_V6[9]
                              + EVIDENCE_CONTRACT + '\n' + SYSTEM)
    ids = token_ids(tokenizer, messages, config.enable_thinking)
    if len(ids) + config.max_output_tokens + 32 > config.max_model_len:
        raise ValueError('Matched product relation input exceeds the fixed context budget')
    candidate = {**control, 'messages': messages, 'token_ids': ids,
                 'generation': {**control['generation'], 'response_format': FORMAT}}
    return {'specification_scope': control, FORMAT: candidate}
