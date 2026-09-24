"""Complete candidate inventories must fit the actual constrained decoder."""
import copy
import json
from pathlib import Path

import jsonschema
import pytest

from submission.pps.generation_contract import generation_schema,validate_grammar
from submission.pps.specification_candidate_review import schema,NAME
from tools.audit_specification_candidates import unknown_response


def inventory(count,units):
    return {'unit_count':units,'candidates':[{'key':f'C{i+1}','value_source':{'start':i,'end':i+1}} for i in range(count)]}


def answer(plan):
    obj=unknown_response(plan)
    for value in obj[NAME].values():value.update(permission_attribute='unknown',permission_effect='unknown')
    obj.update(unresolved='미확인',judgment={'reason':'미확인','v':0,'e':0})
    return obj


@pytest.mark.parametrize('count,units',[(23,166),(64,2048)])
def test_large_inventories_compile_without_dropping_candidates_or_relations(count,units):
    plan=inventory(count,units)
    generated=generation_schema('specification_candidates',units,(9,),specification_inventory=plan)
    validate_grammar(generated)
    required=generated['properties'][NAME]['required']
    assert required==[f'C{i+1}' for i in range(count)]
    obj=answer(plan)
    jsonschema.validate(obj,generated)
    del obj[NAME][required[-1]]
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(obj,generated)


@pytest.mark.parametrize('mutation',['valid','missing_value_named','missing_scope','no_permission_source','false_unobserved','outside_source'])
def test_shared_definitions_preserve_missing_values_and_cross_field_constraints(mutation):
    plan=inventory(2,5);plan['candidates'][0]['value_source']=None
    strict=schema(5,plan=plan);before=copy.deepcopy(strict)
    generated=generation_schema('specification_candidates',5,(9,),specification_inventory=plan)
    obj=answer(plan)
    if mutation=='missing_value_named':obj[NAME]['C1']['specificity']='named'
    if mutation=='missing_scope':obj[NAME]['C2']['role']='new_whole_product'
    if mutation=='no_permission_source':obj[NAME]['C2']['permission_scope']='this_candidate'
    if mutation=='false_unobserved':obj[NAME]['C2']['permission_attribute']='brand_or_model'
    if mutation=='outside_source':obj[NAME]['C2']['scope_sources']=[6]
    original_ok=jsonschema.Draft202012Validator(strict).is_valid(obj)
    assert original_ok is (mutation=='valid')
    assert jsonschema.Draft202012Validator(generated).is_valid(obj)==original_ok
    assert strict==before and len(generated['$defs'])==2


def test_large_inventory_token_mask_accepts_every_required_original_candidate():
    root=Path(__file__).resolve().parents[1]
    if not (root/'models/gemma-tokenizer').is_dir():pytest.skip('Fixed tokenizer required')
    from transformers import AutoTokenizer
    import llguidance
    import llguidance.hf
    tokenizer=AutoTokenizer.from_pretrained(root/'models/gemma-tokenizer',local_files_only=True,trust_remote_code=False)
    plan=inventory(23,166)
    generated=generation_schema('specification_candidates',166,(9,),specification_inventory=plan)
    grammar=llguidance.LLMatcher.grammar_from_json_schema(generated,defaults={'whitespace_flexible':False})
    matcher=llguidance.LLMatcher(llguidance.hf.from_tokenizer(tokenizer),grammar)
    tokens=tokenizer.encode(json.dumps(answer(plan),ensure_ascii=False,separators=(',',':')),add_special_tokens=False)
    assert matcher.consume_tokens(tokens) and matcher.is_accepting()
    assert len(tokens)<2048


def detailed_answer(plan):
    obj=answer(plan)
    sources=list(range(max(1,plan['unit_count']-5),plan['unit_count']+1))
    for value in obj[NAME].values():
        value.update(specificity='named',role='replacement_component',requirement='mandatory',
            scope_sources=sources,permission_sources=sources,exception_sources=sources,
            permission_scope='whole_product_only',permission_attribute='manufacturer_consistency',
            permission_effect='conditional')
    obj['unresolved']='조건의 적용 범위와 상위 납품 대상은 원문 관계를 확인해야 한다. '*4
    obj['unresolved']=obj['unresolved'][:120]
    obj['judgment'].update(reason=('각 후보의 납품 역할과 허용 대상 및 예외 요건을 함께 검토한다. '*4)[:110])
    return obj


def test_detailed_large_answer_fits_adaptive_budget_and_actual_token_mask():
    root=Path(__file__).resolve().parents[1]
    if not (root/'models/gemma-tokenizer').is_dir():pytest.skip('Fixed tokenizer required')
    from transformers import AutoTokenizer
    import llguidance
    import llguidance.hf
    from submission.pps.specification_candidate_review import output_budget
    tokenizer=AutoTokenizer.from_pretrained(root/'models/gemma-tokenizer',local_files_only=True,trust_remote_code=False)
    plan=inventory(23,166);obj=detailed_answer(plan)
    generated=generation_schema('specification_candidates',166,(9,),specification_inventory=plan)
    jsonschema.validate(obj,schema(166,plan=plan))
    tokens=tokenizer.encode(json.dumps(obj,ensure_ascii=False,separators=(',',':')),add_special_tokens=False)
    assert 2048<len(tokens)<=output_budget(plan,2048)
    grammar=llguidance.LLMatcher.grammar_from_json_schema(generated,defaults={'whitespace_flexible':False})
    matcher=llguidance.LLMatcher(llguidance.hf.from_tokenizer(tokenizer),grammar)
    assert matcher.consume_tokens(tokens) and matcher.is_accepting()
    # The real large-inventory input has 11226 tokens. Preserve that source.
    assert 11226+output_budget(plan,2048)+32<=16384


def test_output_reserve_preserves_small_packets_and_format_recovery():
    from submission.pps.specification_candidate_review import output_budget
    from submission.runtime import format_recovery_packet
    assert output_budget(inventory(8,166),2048)==2048
    plan=inventory(23,166)
    packet={'generation':{'max_output_tokens':output_budget(plan,2048),'thinking_budget':0},
        'prompt_sha256':'same_source','specification_inventory':plan}
    recovered=format_recovery_packet(packet,1)
    assert recovered['generation']['max_output_tokens']==packet['generation']['max_output_tokens']
    assert recovered['specification_inventory']==plan
