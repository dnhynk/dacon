"""An optional A10 plan over source-fixed and unresolved judgments.

The plan proves independence from the current consumer's response-fact paths.
It is not an oracle of legal correctness. Unknown initial values are never
source decisions. No labels, record histories or model generations are used.
"""
from __future__ import annotations

import copy
import json

from .data import make_row
from .model_fact_overlay import overlay, PRODUCT_FIELD
from .prompts import EVIDENCE_CONTRACT, fact_fields, output_schema, token_ids
from .rubrics import RUBRIC_V6
from .rules import apply_rules

ITEMS = tuple(range(10, 19))
VERSION = 'source_questions_v1'


def plan(record, knowledge):
    from submission.b4_entry import digest
    context = knowledge.for_source(record)
    source, checks = apply_rules(record, make_row(record, [0] * 24, [''] * 24), context, items=ITEMS)
    source, facts = context.qualification_decisions(record, source)
    decisions = {check['item']: {**check, 'stage': 'source_rules'} for check in checks}
    for key, decision in facts['decisions'].items():
        decisions[int(key[1:])] = {**decision, 'stage': 'source_qualification'}
    # The only model-dependent positive join is conditional on one Boolean:
    # categorical_general(model summary). Evaluate its true branch as an
    # abstract dependency check; this is NEVER recorded as a model response.
    # Its false branch leaves the source decisions untouched. All source gates
    # and item predicates are executed by the actual consumer, not duplicated.
    branch = {'finish_reason': 'stop', 'text': json.dumps({'facts': {
        PRODUCT_FIELD: '경쟁제품에 해당하지 않음'}})}
    possible, joined = overlay(record, source, branch, facts, ITEMS)
    fixed, dependent = {}, {}
    for item, decision in sorted(decisions.items()):
        value = int(source[f'v{item}'])
        why = None
        if int(possible[f'v{item}']) != value:
            why = 'categorical_general_fact_can_change_source_decision'
        elif facts['product']['status'] == 'unknown' and item in (10, 11, 13) and value == 1:
            # fact_consistency may reject an explicitly uncertain or unlisted
            # model claim in this state. Never fix this model-dependent bit.
            why = 'model_applicability_consistency_can_reject_positive'
        if why:
            dependent[str(item)] = why
        else:
            fixed[str(item)] = {'value': value, 'evidence': source[f'e{item}'],
                'reason': decision['reason'], 'stage': decision['stage']}
    from .catalog_condition_review import condition_plan
    questions = condition_plan(facts['product'])
    return {'version': VERSION, 'record_sha256': digest(record), 'fixed': fixed,
        'model_items': [i for i in ITEMS if str(i) not in fixed],
        'response_dependent_source_items': dependent,
        'source_purchase_status': facts['product']['status'],
        'source_purchase_uncertainty': facts['product']['uncertainty'],
        'condition_questions': questions,
        'general_fact_branch_gate': joined['gate'],
        'source_service_provider': context.provider_log,
        'unknown_is_not_fixed_zero': True, 'absence_from_retrieval_is_not_proof': True}


def _guidance(question_plan, knowledge, items):
    obligations = []
    for row in question_plan['condition_questions']:
        if row['source_status'] in {'met', 'no_stated_condition', 'not_met'}:
            continue
        obligations.append({k: row[k] for k in ('code', 'name', 'note', 'fields', 'source_status')})
    context = {'원문판정기의_고정항목': {f'v{k}': v['value'] for k, v in question_plan['fixed'].items()},
        '구매범위_상태': question_plan['source_purchase_status'],
        '구매범위_미확정사유': question_plan['source_purchase_uncertainty'],
        '고시_조건별_확인질문': obligations}
    text = '\n\n[이번 호출의 항목별 판단 안내]\n[원문 판정과 남은 질문]\n'
    text += json.dumps(context, ensure_ascii=False, separators=(',', ':'))
    text += '''
고정항목은 제공 원문을 읽은 코드가 처리하므로 다시 출력하지 않는다. 나머지 항목은 독립적으로 판단한다.
구매범위 unknown은 일반제품이나 경쟁제품이라는 뜻이 아니다. 미확정사유는 추가 확인할 조건이다.
고시 목록의 code·품명 등재와 특이사항 충족은 별개다. 각 해당 후보에서 판정을 가르는 조건을 원문으로 확인한다.
실제구매대상_경쟁제품_고시조건 사실 요약에는 결론을 가르는 속성·대상·허용/예외와 S번호를 남긴다.
조건을 확인하지 못했으면 무엇이 불명확한지 쓴다. 목록에 있다는 이유만으로 조건을 충족했다고 쓰지 않는다.
이륙무게/최대고도와 자체중량/운용상승고도, 업체 소재지와 납품지, 예외 가능과 실제 적용은 서로 다르다.
조건이 다른 여러 품목이나 전체·구성품의 범위를 하나의 meta 품목으로 대신하지 않는다.
'''
    text += '\n[미해결 항목의 판단 안내]\n'
    text += '\n'.join(f"v{k} {knowledge.table[f'v{k}']['항목명']}: {RUBRIC_V6[k]}" for k in items)
    text += '\n이번 호출에서 검토할 항목: ' + ','.join(f'v{k}' for k in items) + '. 이 항목들만 출력한다.\n'
    text += ('최종 JSON은 {"facts":{사실항목:짧은설명},"judgments":{"v":[0또는1,...],"e":[원문구간번호,...]}}이다. '
             'facts를 먼저 작성하고 그 사실에 법령조건을 적용해 judgments를 쓴다. '
             '각 사실은220자 이내이며 확인할 수 없으면 불명확하다고 쓴다. '
             'facts 필수키: ' + ', '.join(fact_fields(items)) + '.\n'
             'v와 e 배열은 요청한 항목 순서대로 각각 ' + str(len(items)) + '개이다. '
             '위반이면 v=1, 정상이거나 적용대상이 아니면 v=0이다. 판단별 reason을 반복 출력하지 않는다.\n')
    return text + EVIDENCE_CONTRACT + '위 공고에 대한 지정된 JSON만 출력한다.'


def prepare(pipe, record, control):
    from submission.b4_entry import digest
    if (control['batch'] != 'A10' or tuple(control['items']) != ITEMS
            or pipe.config.rubric_version != 'v6' or pipe.config.response_format != 'fact_compact'):
        raise ValueError('Source questions require the canonical v6 A10 contract')
    question_plan = plan(record, pipe.knowledge)
    result = copy.deepcopy(control)
    result['source_questions'] = question_plan
    result['source_questions_sha256'] = digest(question_plan)
    if not question_plan['model_items']:
        # Retain a valid base packet for input inventory/schema tooling. The
        # runtime consumes the separate source plan without generating it.
        return result
    messages = copy.deepcopy(control['messages'])
    marker = '\n\n[이번 호출의 항목별 판단 안내]\n'
    prefix, separator, _ = messages[-1]['content'].rpartition(marker)
    if not separator:
        raise ValueError('Canonical A10 judgment suffix is missing')
    items = question_plan['model_items']
    messages[-1]['content'] = prefix + _guidance(question_plan, pipe.knowledge, items)
    ids = token_ids(pipe.tokenizer, messages, pipe.config.enable_thinking)
    if len(ids) + pipe.config.max_output_tokens + 32 > pipe.config.max_model_len:
        # Never gain capacity by dropping original source or unresolved rows.
        return {**control, 'source_questions_fallback': 'complete_question_plan_exceeds_context'}
    result.update(items=items, messages=messages, token_ids=ids,
        prompt_sha256=digest(messages), token_ids_sha256=digest(ids),
        schema_sha256=digest(output_schema(pipe.config.response_format, len(control['spans']), items)))
    if 'legal_control' in control:
        from .legal_query_contract import rebind_questions
        rebind_questions(control, result)
    return result


def validate(record, packet, knowledge):
    from submission.b4_entry import digest
    validate_structure(packet)
    prepared = packet.get('source_questions')
    current = plan(record, knowledge)
    if (packet['batch'] != 'A10' or prepared != current
            or packet.get('source_questions_sha256') != digest(current)):
        raise ValueError('Source question plan changed or belongs to a different record')
    expected = current['model_items'] or list(ITEMS)
    if packet['items'] != expected:
        raise ValueError('Source question items do not match the unresolved plan')
    return current


def validate_structure(packet):
    from submission.b4_entry import digest
    prepared = packet.get('source_questions')
    if (not isinstance(prepared, dict) or prepared.get('version') != VERSION
            or packet.get('batch') != 'A10' or packet.get('family') != 'A'
            or packet.get('source_questions_sha256') != digest(prepared)):
        raise ValueError('Invalid source question contract')
    fixed, active = prepared.get('fixed'), prepared.get('model_items')
    if not isinstance(fixed, dict) or not isinstance(active, list):
        raise ValueError('Invalid source question partition')
    if (any(type(i) is not int or i not in ITEMS for i in active)
            or active != sorted(set(active)) or not set(fixed) <= {str(i) for i in ITEMS}
            or {str(i) for i in active} & set(fixed)
            or {str(i) for i in active} | set(fixed) != {str(i) for i in ITEMS}):
        raise ValueError('Source question partition must cover A10 exactly once')
    for item, decision in fixed.items():
        if (not isinstance(decision, dict) or type(decision.get('value')) is not int
                or decision['value'] not in (0, 1) or not isinstance(decision.get('evidence'), str)):
            raise ValueError('Invalid fixed source decision')
        if (decision['value'] == 0 or int(item) in {10, 11, 16, 18}) and decision['evidence']:
            raise ValueError('Fixed decision carries forbidden evidence')
    if packet['items'] != (active or list(ITEMS)):
        raise ValueError('Source question items do not match the unresolved plan')
    return prepared


def fixed_row(question_plan):
    return {f'{prefix}{item}': decision['value'] if prefix == 'v' else decision['evidence']
            for item, decision in question_plan['fixed'].items() for prefix in ('v', 'e')}


def code_only(record, packet, knowledge):
    question_plan = validate(record, packet, knowledge)
    if question_plan['model_items']:
        raise ValueError('Unresolved questions require a model call')
    return fixed_row(question_plan), {'rule': 'all_A10_items_source_fixed',
        'model_called': False, 'prediction_inferred': True,
        'source_questions_sha256': packet['source_questions_sha256'],
        'normality_from_missing_response': False}
