import json
from types import SimpleNamespace

import pytest

from submission.pps.model_citation import FIELD, cited_indices, named_anchors, repair_v9
from submission.pps.retrieval import Span


def fixture(source='신규 납품 규격\n\nChipset: Atlas GX900\n\n동등 이상 제품도 납품 가능하다.', summary=None):
    first = '입찰 공고\n\n기초금액은 별도 공고합니다.'
    rec = {'id': 'synthetic', 'docs': [{'type': '공고문', 'text': first}, {'type': '규격서', 'text': source}]}
    spans = [Span(i, d['type'], 0, len(d['text']), d['text']) for i, d in enumerate(rec['docs'])]
    summary = summary or '규격서(S2)에 Chipset(Atlas GX900)을 명시함.'
    response = {'text': json.dumps({'facts': {FIELD: summary}, 'judgments': {'v': [1], 'e': [1]}}, ensure_ascii=False)}
    row = {'id': 'synthetic', 'v9': 1, 'e9': first, 'v1': 0, 'e1': ''}
    return rec, row, response, spans


def test_explicit_named_source_repairs_unrelated_locator_and_preserves_full_context():
    rec, row, response, spans = fixture()
    original_response = dict(response)
    result, detail = repair_v9(rec, row, response, spans, items=(9,))
    assert result['v9'] == row['v9'] == 1
    assert 'Atlas GX900' in result['e9']
    assert result['e9'] in rec['docs'][1]['text']
    assert '동등 이상' in detail['selected']['cited_span']['text']
    assert response == original_response and row['e9'] == rec['docs'][0]['text']
    assert not detail['product_identity_certified'] and not detail['equivalence_scope_certified']


@pytest.mark.parametrize('summary', [
    'Atlas GX900이 명시되어 있음.',
    '규격서(S99)에 Atlas GX900이 명시됨.',
    '규격서(S2)에 Phantom ZZ900이 명시됨.',
    '규격서(S1)에 Atlas GX900이 명시됨.',
    '규격서(S2)에 Processor, Chipset, GPU, CPU, Memory가 있음.',
    '규격서(S2)에 128GB, 3.2GHz, USB, SSD 등 일반 성능 수치가 있음.',
    '규격서(S2)에 Processor: 20-core Arm 사양이 명시됨.',
])
def test_reference_or_named_source_must_be_verified(summary):
    rec, row, response, spans = fixture(summary=summary)
    result, detail = repair_v9(rec, row, response, spans, items=(9,))
    assert result == row and detail is None


def test_missing_evidence_can_be_repaired_without_creating_positive():
    rec, row, response, spans = fixture()
    row['e9'] = ''
    result, detail = repair_v9(rec, row, response, spans, items=(9,))
    assert 'Atlas GX900' in result['e9'] and detail['judgment_preserved']
    row['v9'] = 0
    result, detail = repair_v9(rec, row, response, spans, items=(9,))
    assert result == row and detail is None


@pytest.mark.parametrize('judgments', [{'v': [0], 'e': [0]}, {'v9': {'v': 0, 'e': 0}}])
def test_negative_model_citation_cannot_replace_a_cpu_positive_witness(judgments):
    rec, row, response, spans = fixture(
        source='구매 규격서\n모델명: Zenith Z400\n\n기존 장비 Atlas GX900은 점검 대상이다.',
        summary='규격서(S2)에 기존 장비 Atlas GX900을 표시하여 위반이 아니다.')
    row['e9'] = '모델명: Zenith Z400'
    obj = json.loads(response['text'])
    obj['judgments'] = judgments
    response['text'] = json.dumps(obj, ensure_ascii=False)
    assert repair_v9(rec, row, response, spans, items=(9,)) == (row, None)


def test_existing_named_quote_and_unrequested_item_are_preserved():
    rec, row, response, spans = fixture()
    row['e9'] = 'Chipset: Atlas GX900'
    assert repair_v9(rec, row, response, spans, items=(9,)) == (row, None)
    row['e9'] = rec['docs'][0]['text']
    assert repair_v9(rec, row, response, spans, items=(1,)) == (row, None)


def test_invalid_source_coordinates_do_not_redirect_to_matching_other_document():
    rec, row, response, spans = fixture()
    spans[1] = Span(0, '공고문', 0, len(spans[1].text), spans[1].text)
    assert repair_v9(rec, row, response, spans, items=(9,)) == (row, None)


def test_long_span_does_not_clip_away_the_decisive_named_anchor():
    text = '일반 안내 문단입니다.\n\n' * 50 + '신규 제품 Atlas GX900을 납품한다.\n\n동등 제품은 허용하지 않는다.'
    rec, row, response, spans = fixture(source=text)
    result, detail = repair_v9(rec, row, response, spans, items=(9,))
    assert 'Atlas GX900' in result['e9'] and '허용하지 않는다' in result['e9']
    assert len(result['e9']) <= 500
    loc = detail['selected']
    assert rec['docs'][loc['doc_index']]['text'][loc['start']:loc['end']] == result['e9']


def test_exact_name_not_a_prefix_of_a_different_product():
    rec, row, response, spans = fixture(source='Chipset: Atlas GX9000')
    assert repair_v9(rec, row, response, spans, items=(9,)) == (row, None)


def test_generic_numeric_hyphenated_specification_is_not_a_named_anchor():
    rec, row, response, spans = fixture(source='Processor: 20-core Arm\n\nMemory: 128GB',
        summary='규격서(S2)에 Processor: 20-core Arm, Memory: 128GB를 지정함.')
    assert named_anchors(json.loads(response['text'])['facts'][FIELD]) == []
    assert repair_v9(rec, row, response, spans, items=(9,)) == (row, None)


def test_old_equipment_context_is_retained_without_a_new_procurement_claim():
    rec, row, response, spans = fixture(source='기존 장비 Atlas GX900은 유지보수 대상이다. 대체 구매는 하지 않는다.')
    result, detail = repair_v9(rec, row, response, spans, items=(9,))
    assert '기존 장비' in result['e9']
    assert result['v9'] == 1 and detail['judgment_preserved']
    assert not detail['product_identity_certified']


def test_quoted_korean_name_can_ground_a_citation():
    rec, row, response, spans = fixture(source='브랜드: 가온하늘\n\n가온하늘 제품 또는 동등품을 납품한다.',
        summary="규격서(S2)의 '가온하늘' 브랜드를 지정함.")
    result, detail = repair_v9(rec, row, response, spans, items=(9,))
    assert '가온하늘' in result['e9'] and detail is not None


def test_reference_range_is_bounded_and_not_an_identifier_substring():
    assert cited_indices('S2-S4, S6, S8-9, XS3, S90-S999999999', 10) == [1, 2, 3, 5, 7, 8]
    assert cited_indices('S99-S2', 10) == []
    assert cited_indices('S2Gamma', 10) == []


def test_formula_safe_extension_has_exact_coordinates():
    source = '참고 사항\n\n+ Atlas GX900 제품이다.'
    rec, row, response, spans = fixture(source=source)
    result, detail = repair_v9(rec, row, response, spans, items=(9,))
    assert detail is not None and '+ Atlas GX900' in result['e9']
    assert result['e9'][0] not in '=+@'
    loc = detail['selected']
    assert source[loc['start']:loc['end']] == result['e9']


def test_overlong_continuing_clause_is_not_clipped_before_its_negation():
    rec, row, response, spans = fixture(source='Atlas GX900 ' + '필요 조건 ' * 100 + '제품만 납품해야 한다는 뜻은 아니다.')
    assert repair_v9(rec, row, response, spans, items=(9,)) == (row, None)


def test_separately_cited_permission_context_is_retained():
    rec, row, response, spans = fixture(summary='규격서(S2)에 Atlas GX900을 명시하나 동등품 허용(S3)이 있음.')
    text = '동등 이상 제품 납품을 허용한다.'
    rec['docs'].append({'type': '공고문', 'text': text})
    spans.append(Span(2, '공고문', 0, len(text), text))
    result, detail = repair_v9(rec, row, response, spans, items=(9,))
    assert result['v9'] == 1
    assert [c['span_number'] for c in detail['verified_cited_contexts']] == [2, 3]


def test_no_facts_compact_response_keeps_original_quote():
    rec, row, response, spans = fixture()
    response['text'] = json.dumps({'v': [1], 'e': [1]})
    assert repair_v9(rec, row, response, spans, items=(9,)) == (row, None)


def test_pipeline_applies_source_citation_without_changing_bits():
    from submission.pps.pipeline import _response_row
    from submission.pps.prompts import Config, fact_fields
    rec, row, response, spans = fixture()
    obj = json.loads(response['text'])
    obj['facts'] = {k: obj['facts'].get(k, '확인하지 않음') for k in fact_fields((9,))}
    response['text'] = json.dumps(obj, ensure_ascii=False)
    actual, details = _response_row(rec, response, {'spans': spans}, (9,),
        Config(rule_checks=True, qualification_checks=False), SimpleNamespace(), (9,))
    assert actual['v9'] == 1 and 'Atlas GX900' in actual['e9']
    assert any(d.get('source') == 'source_named_citation_repair' for d in details)
