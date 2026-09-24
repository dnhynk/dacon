import json
from types import SimpleNamespace

import pytest

from submission.pps.pipeline import _response_row
from submission.pps.prompts import Config
from submission.pps.retrieval import Span
from submission.pps.specification_table_fields import apply_v9, observations


HEADER = '''규 격 서

COMMODITY DESCRIPTION

품목번호

Item No.

품 명

Description

모델명

세부품명번호

단위

Unit

수량

Q'ty

'''


def record(body, *, meta=None):
    text = HEADER + body
    return {'id': 'synthetic', 'meta': meta or {},
            'docs': [{'doc_id': 'D1', 'type': '규격서', 'text': text}]}


def test_flattened_header_and_typed_values_recover_exact_model_relation():
    rec = record('유도결합플라즈마(ICP)분광계\n\nAgilent ICP-OES 5900\n\n4111541101\n\nSystem\n\nⅠ. 용도\n')
    found = observations(rec)
    assert len(found) == 1
    item = found[0]
    assert item['model_source']['text'] == 'Agilent ICP-OES 5900'
    assert item['catalog_source']['text'] == '4111541101'
    for name in ('form_source', 'model_label_source', 'product_source', 'model_source',
                 'catalog_source', 'unit_source', 'evidence_source'):
        source = item[name]
        assert rec['docs'][source['doc_index']]['text'][source['start']:source['end']] == source['text']
    row, detail = apply_v9(rec, {'v9': 0, 'e9': ''}, items=(9,))
    assert row['v9'] == 1 and 'Agilent ICP-OES 5900' in row['e9']
    assert detail['typed_relation_certified'] and not detail['document_wide_absence_inferred']


def test_repeated_forms_are_independent_and_source_ordered():
    blocks = [
        ('원자방출분광기', 'Agilent ICP-OES 5900', '4111541101', 'System'),
        ('원소분석기', 'Elementer Vario Macro', '4111304101', 'Set'),
        ('실험실용밀', 'Alpine 400AFG', '4110170101', 'Set'),
    ]
    text = '\n'.join(HEADER + '\n\n'.join(values) + '\n\nⅠ. 용도\n' for values in blocks)
    rec = {'id': 'synthetic', 'docs': [{'doc_id': 'D1', 'type': '규격서', 'text': text}]}
    found = observations(rec)
    assert [item['model_source']['text'] for item in found] == [value[1] for value in blocks]
    assert len({item['model_source']['start'] for item in found}) == 3


@pytest.mark.parametrize('body', [
    '시험장비\n\n\n\n4111541101\n\nSystem\n',
    '시험장비\n\nQuad Core CPU\n\n4111541101\n\nSystem\n',
    '시험장비\n\nAtlas X9\n\n잘못된코드\n\nSystem\n',
    '시험장비\n\nAtlas X9\n\n4111541101\n\n미확인단위\n',
    '불필요값1\n\n불필요값2\n\n시험장비\n\nAtlas X9\n\n4111541101\n\nSystem\n',
])
def test_blank_generic_or_misaligned_values_remain_unresolved(body):
    assert observations(record(body)) == []


def test_explicit_model_alternative_blocks_deterministic_positive():
    rec = record('시험장비\n\nAtlas X9\n\n4111541101\n\nSystem\n\n동등 모델 납품을 허용한다.\n')
    found = observations(rec)
    assert found and found[0]['model_alternative_sources']
    row = {'v9': 0, 'e9': ''}
    assert apply_v9(rec, row, items=(9,)) == (row, None)


def test_general_performance_or_quantity_allowance_does_not_erase_model_field():
    rec = record('시험장비\n\nAtlas X9\n\n4111541101\n\nSystem\n\n'
                 '특정업체 필요 액세서리 외 동등 이상 수량 공급\n'
                 '이상 제시된 규격 및 사양 이상의 물품 공급이 가능해야 함.\n')
    row, detail = apply_v9(rec, {'v9': 0, 'e9': ''}, items=(9,))
    assert row['v9'] == 1 and detail is not None


def test_pipeline_applies_table_relation_after_a_negative_model_answer():
    rec = record('시험장비\n\nAtlas X9\n\n4111541101\n\nSystem\n\nⅠ. 용도\n')
    text = rec['docs'][0]['text']
    spans = [Span(0, '규격서', 0, len(text), text)]
    response = {'text': json.dumps({'v': [0], 'e': [0]})}
    row, details = _response_row(rec, response, {'spans': spans}, (9,),
        Config(rule_checks=True, qualification_checks=False), SimpleNamespace(), (9,))
    assert row['v9'] == 1 and 'Atlas X9' in row['e9']
    assert any(detail.get('source') == 'flattened_typed_model_column_v1' for detail in details)


def test_unrequested_or_existing_positive_is_unchanged():
    rec = record('시험장비\n\nAtlas X9\n\n4111541101\n\nSystem\n')
    positive = {'v9': 1, 'e9': '기존 근거'}
    assert apply_v9(rec, positive, items=(9,)) == (positive, None)
    negative = {'v9': 0, 'e9': ''}
    assert apply_v9(rec, negative, items=(1,)) == (negative, None)
