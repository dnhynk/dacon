"""Source roles survive typography; item counts and tax notes cannot set scope."""
import copy
import json
from types import SimpleNamespace

import pytest

from submission.pps.model_fact_overlay import PRODUCT_FIELD, overlay
from submission.pps.fact_consistency import apply as apply_consistency, uncertain_statement
from submission.pps.products import lexical_grams, scope_spans
from submission.pps.qualification import infer, inventory, purchase_scope, qualification_facts
from submission.pps.reference_coverage import assess
from submission.pps.sme import extract_inventory, requires_exception_review
from tests.test_comparison import record


CATALOG = SimpleNamespace(products={'9912340001': {
    '제품명': '측정장치', '세부품명': '정밀측정장치', '특이사항': '', '대분류': '물품'}})
ELIGIBILITY = '1. 입찰참가자격\n가. 해당 업종으로 등록한 업체이어야 한다.\n2. 입찰일정\n'
SETTLEMENT = ('이윤이 계상되어 있으므로 비영리법인이 투찰할 경우에는 이윤을 포함하여 입찰에 참여하고, '
              '계약상대자가 될 경우에는 이윤을 제외한 금액으로 계약을 체결한다.')


def source_matches(rec, ev):
    assert rec['docs'][ev['doc_index']]['text'][ev['start']:ev['end']] == ev['text']


@pytest.mark.parametrize('label', ('구매내역', '구입내역', '구매품목', '구입품목'))
@pytest.mark.parametrize('layout', ('{label}: {value}', '{label}\n{value}', '{spaced} | {value}'))
def test_purchase_field_alias_and_layout_preserve_mixed_scope(label, layout):
    content = layout.format(label=label, spaced=' '.join(label), value='정밀측정장치 등 4종')
    rec = record('문서 안내\n' * 240 + content, 업무구분='물품(내자)',
                 세부품명번호목록='정밀측정장치[9912340001]')
    before = copy.deepcopy(rec)
    spans = scope_spans(rec)
    assert [s['text'] for s in spans] == [content]
    assert lexical_grams(content, query=True) == lexical_grams('정밀측정장치 등 4종')
    p = purchase_scope(rec, CATALOG, [], [])
    assert p['status'] == 'unknown'
    assert 'explicit_multiple_items_not_all_identified' in p['uncertainty']
    assert p['purchase_item_counts'][0]['minimum_total'] == 4
    source_matches(rec, spans[0])
    source_matches(rec, p['purchase_item_counts'][0]['evidence'])
    assert rec == before


@pytest.mark.parametrize('count,status,total', (
    ('외 1종', 'unknown', 2), ('등 1종', 'competition', 1),
    ('외 2품목', 'unknown', 3), ('등 2품목', 'unknown', 2),
))
def test_additional_items_are_distinct_from_total_items(count, status, total):
    rec = record('구입내역: 정밀측정장치 '+count, 업무구분='물품(내자)',
                 세부품명번호목록='정밀측정장치[9912340001]')
    p = purchase_scope(rec, CATALOG, [], [])
    assert p['status'] == status
    assert p['purchase_item_counts'][0]['minimum_total'] == total


def test_an_exact_catalog_name_cannot_cover_unnamed_additional_items():
    p = purchase_scope(record('구입내역: 정밀측정장치 외 1종'), CATALOG, [], [])
    assert p['products'][0]['name'] == '정밀측정장치'
    assert p['status'] == 'unknown' and p['uncertainty']


def test_catalog_name_embedded_in_a_longer_supplied_name_is_not_a_second_purchase():
    catalog = SimpleNamespace(products={
        '7215409902': {'제품명': '전시시설', '세부품명': '전시홍보관설치및디자인서비스',
                       '특이사항': '', '대분류': '용역'},
        '8214150201': {'제품명': '디자인', '세부품명': '디자인서비스',
                       '특이사항': '', '대분류': '용역'},
    })
    rec = record('용역명: 전시홍보관설치및디자인서비스', 업무구분='일반용역',
                 세부품명번호목록='전시홍보관설치및디자인서비스[7215409902]')
    product = purchase_scope(rec, catalog, [], [])
    assert product['status'] == 'competition'
    assert [(item['code'], item['name']) for item in product['products']] == [
        ('7215409902', '전시홍보관설치및디자인서비스')]
    assert 'additional_named_catalog_purchase' not in product['uncertainty']


@pytest.mark.parametrize('field', ('구입내역:\n계약기간: 6개월', '구입내역 | 수량 | 단가',
                                 '구입내역: 과업지시서 참조', '구입내역:\n사업예산: 1억원',
                                 '구입내역\n제품명 | 제조사명 | 규격 | 비고'))
def test_unpopulated_purchase_fields_do_not_certify_a_product(field):
    p = purchase_scope(record(field), CATALOG, [], [])
    assert p['status'] == 'unknown' and not p['products'] and not p['scope_evidence']


def test_a_table_row_with_actual_product_content_is_kept():
    rec = record('구입내역: 정밀측정장치 | 수량: 2개')
    assert scope_spans(rec)[0]['text'] == rec['docs'][0]['text']


@pytest.mark.parametrize('label', ('입찰방법', '계약방법', '입찰방식'))
@pytest.mark.parametrize('spaced', (False, True))
def test_procedure_typography_does_not_erase_an_operative_size_limit(label, spaced):
    field = (' '.join(label) if spaced else label)+': 제한경쟁(소기업·소상공인), 총액입찰'
    rec = record(field+'\n'+ELIGIBILITY)
    q = qualification_facts(rec, inventory(rec))
    assert q['allowed'] == ['micro', 'small'] and not q['no_size']
    assert len(q['operative_procedure_size']) == 1
    source_matches(rec, q['operative_procedure_size'][0]['evidence'])


@pytest.mark.parametrize('prefix', ('작성 예시: ', '가정: ', '삭제: '))
def test_spaced_quoted_or_withdrawn_procedure_stays_nonoperative(prefix):
    rec = record(prefix+'입 찰 방 법: 제한경쟁(소기업·소상공인)\n'+ELIGIBILITY)
    assert not qualification_facts(rec, inventory(rec))['operative_procedure_size']


@pytest.mark.parametrize('text', ('입찰참가 등록서류: 제안요청서 참조',
                                '입찰 참가 서류는 별첨 과업지시서를 따른다.',
                                '○ 입찰참가 등록서류:\n제안요청서 참조'))
def test_missing_bid_registration_document_blocks_absence_even_when_input_is_complete(text):
    rec = record(ELIGIBILITY+text)
    q = qualification_facts(rec, inventory(rec))
    assert q['complete'] and q['closed_eligibility']
    assert not q['no_size'] and not q['no_direct']
    missing = q['reference_coverage']['referenced_unavailable_docs']
    assert any(r['qualification_deferral'] for r in missing)
    for r in missing:
        source_matches(rec, r['evidence'])


@pytest.mark.parametrize('text', ('납품장비 등록서류는 별도 규격서를 참조한다.',
                                '사업자 등록서류 예시는 제안요청서를 참고한다.'))
def test_other_registration_documents_are_not_bidder_qualification_deferrals(text):
    assert assess(record(ELIGIBILITY+text))['qualification_deferrals_resolved']


def test_reference_availability_and_actual_contents_remain_separate():
    rec = record(ELIGIBILITY+'입찰참가 등록서류: 제안요청서 참조',
                 '1. 입찰참가자격\n가. 중소기업 확인서를 보유한 업체이어야 한다.\n2. 일정')
    q = qualification_facts(rec, inventory(rec))
    assert q['reference_coverage']['qualification_deferrals_resolved']
    assert q['allowed'] == ['medium', 'micro', 'small'] and not q['no_size']


def test_price_settlement_is_observed_but_does_not_waive_size_qualification():
    rec = record(ELIGIBILITY+SETTLEMENT)
    flags = extract_inventory(rec)[3]
    assert len(flags) == 1 and flags[0]['kind'] == 'conditional_price_settlement'
    assert not requires_exception_review(flags[0])
    source_matches(rec, flags[0]['evidence'])


@pytest.mark.parametrize('grant', (
    '비영리법인은 입찰에 참가할 수 있다.',
    '비영리법인도 입찰참여가 가능하다.',
    '비영리법인의 입찰참가자격을 인정한다.',
    '판로지원법 시행령 제2조의3의 적용 여부를 검토한다.',
))
def test_price_wording_cannot_erase_a_separate_eligibility_or_exception_claim(grant):
    flags = extract_inventory(record(ELIGIBILITY+grant+' '+SETTLEMENT))[3]
    assert flags and all(requires_exception_review(f) for f in flags)


@pytest.mark.parametrize('baseline', ('0', '1'))
def test_model_overlay_and_numeric_consumer_share_the_price_note_role(baseline):
    rec = record('1. 입찰참가자격\n가. 중소기업 확인서를 소지한 업체이어야 한다.\n'
                 '2. 가격제출\n'+SETTLEMENT, 업무구분='일반용역',
                 입찰추정가격=320_000_000, 배정예산금액=352_000_000)
    row = {'v14': baseline, 'e14': ''}
    _, facts = infer(rec, row, CATALOG)
    assert facts['product']['status'] == 'unknown'
    response = {'finish_reason': 'stop', 'text': json.dumps({'facts': {
        PRODUCT_FIELD: '자문용역이며 경쟁제품에 해당하지 않음.'}}, ensure_ascii=False)}
    result, log = overlay(rec, row, response, facts, [14])
    assert result['v14'] == '1'
    assert log.get('gate') != 'exception_requires_resolution'
    product = dict(facts['product'], status='general')
    result, facts = infer(rec, row, CATALOG, product_override=product)
    assert result['v14'] == '1' and 'v14' not in facts['deferred_decisions']


def unlisted_source():
    rec = record('구입내역: 기록장치 등 3종', 세부품명번호목록='기록장치[9901234501]')
    return purchase_scope(rec, CATALOG, [], [])


@pytest.mark.parametrize('claim', (
    '기록장치(9901234501) 등 3종으로 경쟁제품에 해당함.',
    '기록장치[9901234501]. 메타정보에는 소기업·소상공인 제한으로 기재됨.',
    '성인용기저귀(9901234501) 등 3종으로 경쟁제품에 해당함.',
    '개인용기록장치(9901234501)는 경쟁제품에 해당함.',
    '가정용기록장치(9901234501)는 경쟁제품에 해당함.',
))
def test_unlisted_cited_code_cannot_restore_competition_bits_after_mixed_scope_is_found(claim):
    product = unlisted_source()
    before = copy.deepcopy(product)
    response = {'text': json.dumps({'facts': {PRODUCT_FIELD: claim}}, ensure_ascii=False)}
    row = {'v10': '1', 'v11': '1', 'v13': '1', 'v18': '0'}
    out, flags = apply_consistency(row, response, {10: 1, 11: 1, 13: 1, 18: 0}, product=product)
    assert [out[f'v{i}'] for i in (10, 11, 13, 18)] == ['0'] * 4
    assert len(flags) == 3 and all(not f['normality_certified'] for f in flags)
    assert product == before and product['status'] == 'unknown'
    assert all(not f['other_purchase_items_inferred'] for f in flags)


def unresolved_conditional_multi_item_source():
    product = unlisted_source()
    product['products'] = [{
        'code': '9901234501', 'listed': True, 'name': '기록장치',
        'condition': {'status': 'unknown'}}]
    return product


def test_complete_unidentified_multi_item_purchase_rejects_an_uncertified_whole_purchase_positive():
    product = unresolved_conditional_multi_item_source()
    before = copy.deepcopy(product)
    response = {'text': json.dumps({'facts': {PRODUCT_FIELD:
        '기록장치(9901234501)는 중소기업자간 경쟁제품이며 고시 조건을 충족한다.'}}, ensure_ascii=False)}
    row = {f'v{i}': '1' for i in (10, 11, 13)} | {f'e{i}': '근거' for i in (10, 11, 13)}
    out, flags = apply_consistency(row, response, {10: 1, 11: 1, 13: 1},
        product=product, qualification={'complete': True})
    assert [out[f'v{i}'] for i in (10, 11, 13)] == ['0', '0', '0']
    assert [out[f'e{i}'] for i in (10, 11, 13)] == ['', '', '']
    assert {f['item'] for f in flags} == {10, 11, 13}
    assert all(f['reason'] == 'model_whole_purchase_claim_skips_unidentified_items'
               and not f['normality_certified'] for f in flags)
    assert product == before


@pytest.mark.parametrize('edit,fact,complete', (
    ({}, '기록장치(9901234501)는 경쟁제품이다.', False),
    ({'uncertainty': ['explicit_multiple_items_not_all_identified',
                      'additional_declared_purchase_components']},
     '기록장치(9901234501)는 경쟁제품이다.', True),
    ({'products': [{'code': '9901234501', 'listed': True,
                    'condition': {'status': 'met'}}]},
     '기록장치(9901234501)는 경쟁제품이다.', True),
    ({}, '복수 구매품목 중 기록장치(9901234501)만 경쟁제품이고 나머지는 미확정이다.', True),
))
def test_multi_item_guard_preserves_incomplete_conflicting_certified_or_component_qualified_cases(
        edit, fact, complete):
    product = dict(unresolved_conditional_multi_item_source(), **edit)
    response = {'text': json.dumps({'facts': {PRODUCT_FIELD: fact}}, ensure_ascii=False)}
    out, flags = apply_consistency({'v10': '1', 'e10': ''}, response, {10: 1},
        product=product, qualification={'complete': complete})
    assert out['v10'] == '1' and not flags


@pytest.mark.parametrize('edit,claim', (
    ({}, '기록장치 등 3종으로 경쟁제품에 해당함.'),
    ({}, '기록장치(9901234501)와 별도 장치(9912340001)를 구매한다.'),
    ({}, '기록장치(9901234501)는 일부이며, 나머지는 별도 경쟁제품이다.'),
    ({'paired_meta_purchase_codes': []}, '기록장치(9901234501)는 경쟁제품이다.'),
    ({'catalog_scope': 'unverified_external_list'}, '기록장치(9901234501)는 경쟁제품이다.'),
    ({'uncertainty': ['additional_named_catalog_purchase']},
      '기록장치(9901234501) 및 별도 정밀측정장치를 구매한다.'),
    ({'uncertainty': ['metadata_and_body_purchase_codes_conflict']},
      '기록장치(9901234501)는 경쟁제품이다.'),
    ({'products': [{'code': '9901234501', 'listed': True, 'condition': {'status': 'unknown'}}]},
      '기록장치(9901234501)는 경쟁제품이다.'),
))
def test_incomplete_or_distinct_catalog_relationships_are_not_rejected_by_a_code_substring(edit, claim):
    product = dict(unlisted_source(), **edit)
    response = {'text': json.dumps({'facts': {PRODUCT_FIELD: claim}}, ensure_ascii=False)}
    out, flags = apply_consistency({'v10': '1'}, response, {10: 1}, product=product)
    assert out['v10'] == '1' and not flags


@pytest.mark.parametrize('modifier', ('성인용', '개인용', '가정용'))
def test_product_modifiers_do_not_hide_explicit_uncertainty(modifier):
    assert uncertain_statement(modifier+' 물품의 경쟁제품 해당 여부가 불명확함.', 'catalog')


@pytest.mark.parametrize('prefix', ('인용: ', '가정: ', '가정한 사례: ', '문구를 인용한 것: '))
def test_actual_discourse_role_still_blocks_a_claim_from_becoming_a_fact(prefix):
    assert uncertain_statement(prefix+'경쟁제품 해당 여부가 불명확함.', 'catalog') is None
    response = {'text': json.dumps({'facts': {PRODUCT_FIELD:
        prefix+'기록장치(9901234501)는 경쟁제품이다.'}}, ensure_ascii=False)}
    _, flags = apply_consistency({'v10': '1'}, response, {10: 1}, product=unlisted_source())
    assert not flags
