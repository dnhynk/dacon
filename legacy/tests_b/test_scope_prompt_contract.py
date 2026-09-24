"""The model-facing schema must preserve addresses, output types and unknowns."""
import copy
import json

import pytest

from submission.pps.catalog_scope import output_contract, schema
from tools.prepare_scope_contract import explain_packet


@pytest.mark.parametrize('count',[1,12,150])
def test_model_visible_contract_contains_the_actual_schema(count):
    text=output_contract(count)
    visible=json.loads(text.split('[JSON Schema]\n',1)[1])
    assert visible==schema(count)
    definitions=text.split('[JSON Schema]\n',1)[0]
    assert all(key in definitions for key in visible['properties'])
    for key in ('purchase_kind','catalog_relation'):
        assert all(choice in definitions for choice in visible['properties'][key]['enum'])
    assert '물리적 산출물을 납품하거나 재료를 사용하는 사실만으로 mixed로 바꾸지 않는다' in definitions
    assert '산출물이나 세부 업무가 여러 개라는 이유만으로 true로 하지 않는다' in definitions
    assert '자격증명서 품목이 다르다는 사실만으로 uncertain이나 unresolved_scope=true로 하지 말고' in definitions
    link=visible['properties']['relationships']['items']['properties']
    assert all(key in definitions for key in link)
    assert all(choice in definitions for choice in link['role']['enum'])


def test_exposition_does_not_change_original_source_or_sampler():
    class Tokenizer:
        def apply_chat_template(self,messages,**kwargs):
            return [ord(c) for c in json.dumps(messages,ensure_ascii=False)]
    packet={'messages':[{'role':'system','content':'관계 검토'},{'role':'user','content':'원문 S1'}],
        'spans':[{'text':'원문','start':0,'end':2}],
        'generation':{'response_format':'catalog_scope','thinking_budget':0,'max_output_tokens':1536},
        'source_search':{'source_tokens':2},'catalog_scope':{'catalog':['고정']},
        'token_ids':[1], 'schema_sha256':'unchanged', 'prompt_sha256':'old','token_ids_sha256':'old'}
    saved=copy.deepcopy(packet)
    candidate=explain_packet(packet,Tokenizer())
    assert packet==saved
    assert candidate['messages'][1:]==packet['messages'][1:]
    for key in ('spans','generation','source_search','catalog_scope','schema_sha256'):
        assert candidate[key]==packet[key]
    assert candidate['token_ids']!=packet['token_ids']


@pytest.mark.parametrize('count',[0,-1,True,1.5])
def test_unknown_or_invalid_address_bounds_are_not_invented(count):
    with pytest.raises(ValueError):
        output_contract(count)
