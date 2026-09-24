from tools.audit_specification_candidates import citation_audit, covered
from submission.pps.retrieval import Span
from submission.pps.source_units import unitize
from submission.pps.specification_candidates import inventory


def test_containing_product_quote_does_not_certify_component_relation():
    text = '수신기는 Maxwell 7칩이 내장된 제품이다.\n'
    record = {'id': 'synthetic', 'docs': [{'type': '규격서', 'text': text}]}
    spans = unitize([Span(0, '규격서', 0, len(text), text)])
    plan = inventory(record, spans)
    facts = {'product_inventory': 'complete', 'products': [{'specificity': 'generic',
        'role': 'new_supply', 'requirement': 'mandatory', 'relation': 'none',
        'source_locations': [{'doc_index': 0, 'start': 0, 'end': len(text), 'text': text}]}]}
    result = citation_audit(plan, facts, record)
    assert result['value_cited'] == 1 and result['inventory_claim'] == 'complete'
    assert not result['candidates'][0]['semantic_bundle_verified']
    assert not result['candidates'][0]['separate_candidate_review_verified']
    assert result['candidates'][0]['value_fully_cited_by_products'][0]['specificity'] == 'generic'


def test_same_words_in_another_document_do_not_cover_original_value():
    record = {'docs': [{'text': 'Q7'}, {'text': 'Q7'}]}
    assert not covered({'doc_index': 0, 'start': 0, 'end': 2},
        [{'doc_index': 1, 'start': 0, 'end': 2}], record)


def test_omitted_part_of_value_cannot_be_filled_from_other_citations():
    record = {'docs': [{'text': 'Intel Q7'}]}
    assert not covered({'doc_index': 0, 'start': 0, 'end': 8},
        [{'doc_index': 0, 'start': 0, 'end': 4}, {'doc_index': 0, 'start': 7, 'end': 8}], record)
    assert covered({'doc_index': 0, 'start': 0, 'end': 8},
        [{'doc_index': 0, 'start': 0, 'end': 5}, {'doc_index': 0, 'start': 6, 'end': 8}], record)
