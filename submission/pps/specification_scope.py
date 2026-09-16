"""Optional, source-addressed V9 reasoning contract.

Separate the product being bought from the target and attribute of permission.
Source coordinates are reconstructed by code. A model's semantic classification
remains fallible; validation and diagnostic flags do not certify legal truth.
The default four-call producer does not use this experimental contract.
"""
from __future__ import annotations

import dataclasses
import re

import jsonschema

from .data import clean_evidence
from .response_contract import loads
from .source_units import render, unitize, validate as validate_source_units

NAME = 'specification_scope_v1'
ROLES = ('new_supply', 'license_renewal', 'maintenance_target', 'existing_reference', 'unknown')
TARGETS = ('whole_product', 'component', 'service', 'unknown')
ATTRIBUTES = ('brand_or_model', 'performance', 'quantity', 'warranty', 'unknown')

SYSTEM = '''v9를 판단하기 전에 특정 명칭과 대체 허용의 관계를 구조화한다.
products는 실제로 원문에 나온 대상별로 작성한다. 고유 제조사·제품·모델의 명칭과 일반
성능 수치·장비 종류명을 구별한다. sources는 명칭을 보여 주는 S번호 목록이며,
role_sources는 그 대상의 구매·납품·기존 장비 역할을 보여 주는 S번호 목록이다.
role은 new_supply(이번 계약의 신규 납품), license_renewal(기존 제품 사용권의 갱신 구매),
maintenance_target(기존 제품의 유지관리 대상), existing_reference(기존 인프라 설명만),
unknown 중 하나다. 갱신 구매를 기존 장비의 단순 설명으로 바꾸지 않는다.
specificity는 named/generic/unknown, requirement는 mandatory/example/unknown이다.
명칭이 존재한다는 사실만으로 납품 의무나 위반을 확정하지 않는다.

permissions는 허용·금지·조건부 대체 문구별로 작성한다. product는 관련 products의
1부터 시작하는 번호이며 연결을 모르면0이다. target은 whole_product/component/service/unknown,
attribute는 brand_or_model/performance/quantity/warranty/unknown,
effect는 allowed/prohibited/conditional/unknown이다. sources에 허용·금지 문장,
target_sources에 그 대상과 연결하는 상위 제목·앞 문장·다른 문서의 S번호를 함께 고른다.
동등 이상 수량은 수량, 동등 성능은 성능에 대한 진술이다. 부속품의 수량 허용이 본체
모델 대체 허용이 되지 않는다. 전체 납품제품을 허용해도 필수 칩셋 등 특정 명칭이
유지되는지를 따로 검토한다. 보증 단락의 문장도 목적어·납품 행위·다른 문서와 함께
해석한다. 위치만으로 보증 전용 또는 전체 제품이라고 단정하지 않는다.
특정 명칭을 변경할 수 있다고 원문이 명시했을 때만 brand_or_model로 기록한다.
실제 시장에 대체 제품이 존재하는지는 이 문구만으로 확정하지 않는다.

exemption_sources에는 해당 공고가 주장하는 적용 예외의 원문 S번호를 쓴다.
예외 주장이 있다는 사실과 제공 법령상 적용이 확인됐다는 판단은 다르다.
원문을 다시 쓰거나 좌표를 생성하지 않고 S번호만 고른다. 구간 경계는 문장·조건의
끝을 뜻하지 않으므로 인접 구간을 함께 읽는다. 전체 문서 부재를 검색 실패로 만들지 않는다.
products와 permissions는 각각 최대4개다. 관련 사실이 없으면 빈 목록이다.
unresolved에는 판정에 필요한 미확정 정보가 있으면120자 이내로 쓴다. 없으면 빈 문자열이다.
마지막 judgment는 {"reason":"적용조건과 관계를 연결한110자 이내 판단","v":0또는1,"e":S번호또는0}이다.
v는 위반이면1, 비위반 또는 적용대상이 아니면0이며, 근거 없는 위반을 만들지 않는다.
e는 위반을 직접 뒷받침하는 원문 번호이며 비위반이면0이다.
출력은 {"specification_scope_v1":{"products":[...],"permissions":[...],
"exemption_sources":[...],"unresolved":"...","judgment":{...}}} JSON만 쓴다.
'''


def schema(max_evidence, items=(9,)):
    if tuple(items) != (9,) or type(max_evidence) is not int or max_evidence < 0:
        raise ValueError('Specification scope requires only v9 and a finite source inventory')
    reference = {'type': 'integer', 'enum': list(range(1, max_evidence + 1)) or [1]}
    references = {'type': 'array', 'maxItems': min(6, max_evidence), 'items': reference}
    def enum(values):
        return {'type': 'string', 'enum': list(values)}
    def obj(properties):
        return {'type': 'object', 'additionalProperties': False,
                'required': list(properties), 'properties': properties}
    product = obj({'sources': {**references, 'minItems': 1},
        'role_sources': {**references, 'minItems': 1}, 'role': enum(ROLES),
        'specificity': enum(('named', 'generic', 'unknown')),
        'requirement': enum(('mandatory', 'example', 'unknown'))})
    permission = obj({'sources': {**references, 'minItems': 1}, 'target_sources': references,
        'product': {'type': 'integer', 'enum': list(range(5))}, 'target': enum(TARGETS),
        'attribute': enum(ATTRIBUTES), 'effect': enum(('allowed', 'prohibited', 'conditional', 'unknown'))})
    judgment = obj({'reason': {'type': 'string', 'minLength': 1, 'maxLength': 110},
        'v': {'type': 'integer', 'enum': [0, 1]},
        'e': {'type': 'integer', 'enum': list(range(max_evidence + 1))}})
    payload = obj({'products': {'type': 'array', 'maxItems': 4 if max_evidence else 0, 'items': product},
        'permissions': {'type': 'array', 'maxItems': 4 if max_evidence else 0, 'items': permission},
        'exemption_sources': references, 'unresolved': {'type': 'string', 'maxLength': 120},
        'judgment': judgment})
    return obj({NAME: payload})


def decode(text, spans, rec=None):
    obj = loads(text)
    try:
        jsonschema.validate(obj, schema(len(spans)))
    except jsonschema.ValidationError as exc:
        raise ValueError('Invalid specification relation schema: ' + exc.message) from exc
    return decode_payload(obj[NAME], spans, rec)


def source_locations(numbers, spans):
    """Validate the raw IDs before equal-valued floats can collapse into ints."""
    if any(type(n) is not int or not 1 <= n <= len(spans) or not spans[n-1].text.strip()
           for n in numbers):
        raise ValueError('Invalid specification source address')
    # Only repeated valid addresses are normalized. The response stays intact.
    return [{'s': n, **dataclasses.asdict(spans[n-1])} for n in dict.fromkeys(numbers)]


def decode_payload(payload, spans, rec=None):
    """Resolve addresses after the caller validates its versioned wire schema."""
    validate_source_units(spans, rec)
    judgment = payload['judgment']
    if (type(judgment['v']) is not int or judgment['v'] not in (0, 1)
            or type(judgment['e']) is not int or not 0 <= judgment['e'] <= len(spans)):
        raise ValueError('Specification judgment requires an integer bit and source address')
    products = [{**p, 'source_locations': source_locations(p['sources'], spans),
                 'role_locations': source_locations(p['role_sources'], spans)} for p in payload['products']]
    permissions = []
    for p in payload['permissions']:
        if type(p['product']) is not int or not 0 <= p['product'] <= len(products):
            raise ValueError('Permission requires an integer reference to an existing product or zero')
        permissions.append({**p, 'source_locations': source_locations(p['sources'], spans),
                            'target_locations': source_locations(p['target_sources'], spans)})
    return {**payload, 'products': products, 'permissions': permissions,
            'exemption_locations': source_locations(payload['exemption_sources'], spans)}


def _source_groups(locations, rec=None):
    """Read connected original ranges, never synthesize adjacency with a join.

    A model may return addresses in any order or from different documents.
    Without the original record, an unobserved gap cannot be called whitespace.
    """
    if rec is not None:
        from .notice_search import merge_ranges
        return [{'doc_index':di,'start':lo,'end':hi,'text':rec['docs'][di]['text'][lo:hi]}
                for di,lo,hi in merge_ranges(
                    [(loc['doc_index'],loc['start'],loc['end']) for loc in locations],rec['docs'])]
    groups=[]
    for loc in sorted(locations,key=lambda x:(x['doc_index'],x['start'],x['end'])):
        if groups and groups[-1]['doc_index']==loc['doc_index'] and groups[-1]['end']==loc['start']:
            groups[-1]['end']=loc['end'];groups[-1]['text']+=loc['text']
        else:
            groups.append({key:loc[key] for key in ('doc_index','start','end','text')})
    return groups


def relationship_diagnostics(facts, rec=None):
    """Expose narrow source contradictions without inventing a legal decision."""
    issues = []
    for i, p in enumerate(facts['permissions']):
        for group in _source_groups(p['source_locations'],rec):
            text=group['text']
            quantity = re.search(r'동(?:등|급)(?:\s*또는)?(?:\s*(?:그\s*)?이상)?\s*수량', text)
            explicit_identity = re.search(r'(?:다른|타|대체)\s*(?:제조사|상표|모델|브랜드)|(?:제조사|상표|모델|브랜드).{0,15}(?:변경|대체)', text)
            if quantity and p['attribute'] == 'brand_or_model' and not explicit_identity:
                issues.append({'permission': i + 1, 'kind': 'quantity_permission_used_as_identity_substitution',
                    'matched_source': quantity[0], 'source_group':group, 'semantic_truth_certified': False,
                    'note': 'Inspect target and neighboring clauses; this connected source phrase alone does not support model substitution.'})
            # Matching the manufacturer of components to each other is not
            # naming the one manufacturer of the whole supplied product.
            warranty = re.search(r'동일\s*제조사\s*보증', text)
            set_coherence = re.search(r'(?:동일|같은)\s*(?:회사|제조사)[\s\S]{0,35}(?:세트화|세트로|세트형태)',text)
            direct_restriction = re.search(r'모델|상표|특정|대체|변경|불가|금지|순정', text)
            if ((warranty or set_coherence) and p['attribute']=='brand_or_model'
                    and p['effect']=='prohibited' and not direct_restriction):
                matched=warranty or set_coherence
                issues.append({'permission':i+1,
                    'kind':('warranty_consistency_used_as_model_prohibition' if warranty
                            else 'set_manufacturer_consistency_used_as_model_prohibition'),
                    'matched_source':matched[0], 'source_group':group,'semantic_truth_certified':False,
                    'note':'Internal manufacturer consistency alone does not name a required brand/model; inspect the identity-bearing source and its scope.'})
        if p['target'] != 'unknown' and not p['target_locations']:
            issues.append({'permission': i + 1, 'kind': 'permission_target_has_no_source_link',
                           'semantic_truth_certified': False})
    if rec is not None:
        from .specification_blocks import permission_link_issues
        issues.extend(permission_link_issues(facts, rec))
    if len(facts['judgment']['reason']) == 110:
        issues.append({'kind': 'reason_at_schema_length_limit', 'characters': 110,
            'semantic_truth_certified': False,
            'note': 'The schema limit was reached. Preserve the native text and inspect whether the explanation completed; do not repair or flip the model judgment.'})
    return issues


def review(rec, response, prompt):
    if tuple(prompt['items']) != (9,):
        raise ValueError('Specification scope may only consume v9')
    facts = decode(response['text'], prompt['spans'], rec)
    judgment = facts['judgment']
    evidence = ''
    if judgment['v'] and judgment['e']:
        span = prompt['spans'][judgment['e'] - 1]
        evidence = clean_evidence(span.text, rec, source=(span.doc_index, span.start, span.end))
    return {'v9': judgment['v'], 'e9': evidence}, [{'source': 'source_addressed_specification_scope',
        'facts': facts, 'relationship_issues': relationship_diagnostics(facts, rec),
        'judgment_is_model_output': True, 'semantic_validation_complete': False,
        'new_source_evidence_inferred': False}]


def prompts(rec, knowledge, config, tokenizer, source_selection):
    """Return a matched control/candidate with byte-identical original source."""
    from .prompts import build_prompt, token_ids, EVIDENCE_CONTRACT
    from .rubrics import SYSTEM_V6, RUBRIC_V6
    cfg = dataclasses.replace(config, shared_prefix=False, response_format='factored',
        product_facts=False, sme_facts=False, cross_source_facts=False, thinking_items=(), thinking_token_budget=0)
    base = build_prompt(rec, knowledge, cfg, tokenizer, (9,), source_selection=source_selection)
    units = unitize(base['spans'])
    marker = '\n\n[분석할 공고 및 첨부 원문 구간]\n'
    prefix, _ = base['messages'][1]['content'].split(marker, 1)
    user = prefix + marker + render(units) + '\n이번 호출에서 검토할 항목: v9. 지정된 JSON만 출력한다.'
    systems = {'factored': base['messages'][0]['content'], 'specification_scope':
        SYSTEM_V6 + '\n[항목별 판단 안내]\nv9 ' + RUBRIC_V6[9] + EVIDENCE_CONTRACT + '\n' + SYSTEM}
    result = {}
    for form, system in systems.items():
        messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
        ids = token_ids(tokenizer, messages, cfg.enable_thinking)
        if len(ids) + cfg.max_output_tokens + 32 > cfg.max_model_len:
            raise ValueError('Matched specification inputs exceed context; select a new common source budget')
        result[form] = {**base, 'messages': messages, 'token_ids': ids, 'spans': units,
                       'source_layout': 'finite_units', 'generation': {'response_format': form,
                           'thinking_budget': 0, 'max_output_tokens': cfg.max_output_tokens}}
    return result
