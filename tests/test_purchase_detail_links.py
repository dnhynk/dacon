"""Item-detail discovery preserves ambiguity and literal source addresses."""
from submission.pps.purchase_details import detail_links


def card(name='센서(A형)', unit='개', extra=''):
    return (f'품명 | 단위\n{name} | {unit}\n1. 용도\n온도 측정\n'
            f'2. 규격\n측정범위 -20도 이상\n{extra}')


def test_summary_links_to_literal_variant_without_certifying_the_purchase():
    text = '품명 | 단위 | 수량\n센서(A형) | 개 | 3\n\n' + card()
    value = detail_links(text)
    assert len(value['links']) == 1
    link = value['links'][0]
    assert link['unique_literal_target']
    assert not link['same_purchase_item_certified']
    assert not link['property_agreement_checked']
    assert not value['complete_purchase_identity_certified']
    for ref in (link['summary_row'], link['summary_name'], link['detail_name']):
        assert text[ref['start']:ref['end']] == ref['text']


def test_duplicate_detail_names_are_retained_as_multiple_candidates():
    text = '품명 | 단위 | 수량\n센서(A형) | 개 | 3\n\n' + card() + '\n' + card()
    value = detail_links(text)
    assert len(value['links']) == 2
    assert all(not link['unique_literal_target'] for link in value['links'])


def test_generic_summary_name_cannot_select_an_unprinted_variant():
    text = '품명 | 단위 | 수량\n센서 | 개 | 3\n\n' + card()
    value = detail_links(text)
    assert len(value['cards']) == 1
    assert value['links'] == []
    assert value['unmatched_summary_rows']


def test_unit_disagreement_is_not_silently_reconciled():
    text = '품명 | 단위 | 수량\n센서(A형) | 개 | 3\n\n' + card(unit='세트')
    value = detail_links(text)
    assert value['links'][0]['summary_unit']['text'] == '개'
    assert value['cards'][0]['unit']['text'] == '세트'
    assert not value['links'][0]['property_agreement_checked']


def test_common_section_and_example_captions_keep_their_source_ownership_open():
    text = ('품명 | 단위 | 수량\n센서(A형) | 개 | 3\n\n'
            '<참고용 예시>\n' + card(extra='<참고용 예시>\n공통 적용사항\n납품 후 점검'))
    value = detail_links(text)
    found = value['cards'][0]
    assert found['preceding_caption']['text'] == '<참고용 예시>'
    assert len(found['trailing_captions']) == 1
    assert all('참고용' not in f['body']['text'] for f in found['fields'])
    assert '납품 후 점검' not in found['source']['text']
    assert not found['operative_scope_certified']
