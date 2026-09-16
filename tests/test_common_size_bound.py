"""Only predicates shared by every surviving size reading can be consumed."""
import json
from types import SimpleNamespace

import pytest

from submission.pps.model_fact_overlay import overlay, PRODUCT_FIELD
from submission.pps.qualification import inventory, qualification_facts, purchase_scope, infer
from tests.test_comparison import record


def setup(amount=300_000_000, tail=''):
    rec = record('1. 입찰참가자격\n가. 중소기업확인서를 소지한 자\n'
                 '나. 소기업확인서를 소지한 자\n'+tail+'\n2. 계약조건\n내일 계약한다.',
                 업무구분='일반용역', 입찰추정가격=amount, 배정예산금액=amount*1.1)
    pf = SimpleNamespace(products={})
    parts = inventory(rec)
    q = qualification_facts(rec, parts)
    product = purchase_scope(rec,pf,parts[4],[])
    response = {'finish_reason':'stop','text':json.dumps({'facts':{
        PRODUCT_FIELD:'실제 전체 구매대상은 경쟁제품에 해당하지 않는 일반용역이다.'}},ensure_ascii=False)}
    return rec, {'v14':'0','e14':'','v16':'0','e16':''}, response, {'product':product,'qualification':q}


def test_upper_band_common_restriction_survives_an_unresolved_medium_enterprise_conflict():
    rec, row, response, facts = setup()
    q = facts['qualification']
    assert q['allowed'] is None and q['size_conflict']
    bound = q['common_size_bound']
    assert bound['commercial_upper_bound'] == ['medium','micro','small']
    assert bound['exact_size_conflict_preserved'] and len(bound['evidence']) == 2
    result, log = overlay(rec,row,response,facts,[14,16])
    assert result['v14'] == '1' and result['v16'] == '0'
    assert q['allowed'] is None and q['size_conflict'] and log['exact_size_conflict_preserved']
    assert not log['source_scope_promoted']


@pytest.mark.parametrize('tail', [
    '다. 중견기업도 입찰참가가 가능합니다.',
    '다. 대기업은 중소기업 확인서 없이 참가할 수 있습니다.',
    '다. 소기업확인서가 있는 업체 또는 벤처기업도 참가할 수 있습니다.',
])
def test_an_unresolved_or_larger_commercial_alternative_blocks_the_common_bound(tail):
    rec,row,response,facts = setup(tail=tail)
    assert facts['qualification']['common_size_bound'] is None
    assert overlay(rec,row,response,facts,[14])[0]['v14'] == '0'


def test_the_bound_cannot_certify_absence_or_resolve_a_middle_band_size_question():
    rec,row,response,facts = setup(amount=150_000_000)
    result, log = overlay(rec,row,response,facts,[14,16])
    assert result == row and log['gate'] == 'source_size_conflict'


def test_an_exception_still_requires_review_even_when_size_predicates_agree():
    rec,row,response,facts = setup(tail='단, 판로지원법 시행령 제2조의3에 해당하는 비영리법인은 참가 가능합니다.')
    result, log = overlay(rec,row,response,facts,[14])
    assert result == row and log['gate'] == 'exception_requires_resolution'


def test_resolved_general_purchase_uses_the_same_bound_without_resolving_exact_size():
    rec,row,_,facts = setup()
    product = {**facts['product'], 'status':'general'}
    result, source = infer(rec,row,SimpleNamespace(products={}),product_override=product)
    assert result['v14'] == '1'
    assert source['qualification']['size_conflict'] and source['qualification']['allowed'] is None


def test_explicit_medium_eligibility_survives_a_conflicting_small_procedure_label():
    text = ('입찰방법: 제한경쟁(소기업)\n'
            '1. 입찰참가자격\n'
            '가. 중소기업 또는 소상공인으로서 중소기업 또는 소상공인확인서를 소지한 업체\n'
            '2. 계약조건')
    rec = record(text, 업무구분='일반용역', 입찰추정가격=60_000_000,
                 배정예산금액=66_000_000, 세부품명번호목록='서적[5510151001]')
    row, facts = infer(rec, {'v17': '0', 'e17': ''}, SimpleNamespace(products={}))
    assert facts['qualification']['size_conflict']
    assert len(facts['qualification']['explicit_medium_permissions']) == 1
    assert row['v17'] == '1'
    assert facts['decisions']['v17']['reason'].endswith('conflict_preserved')


def test_a_broad_certificate_name_does_not_override_an_explicit_small_entity_preamble():
    text = ('입찰방법: 제한경쟁(소기업)\n'
            '1. 입찰참가자격\n'
            '가. 소기업자 또는 소상공인으로서 중소기업·소상공인 확인서를 소지한 업체\n'
            '2. 계약조건')
    rec = record(text, 업무구분='일반용역', 입찰추정가격=60_000_000,
                 배정예산금액=66_000_000, 세부품명번호목록='서적[5510151001]')
    row, facts = infer(rec, {'v17': '0', 'e17': ''}, SimpleNamespace(products={}))
    assert facts['qualification']['explicit_medium_permissions'] == []
    assert row['v17'] == '0'


def test_public_notice_small_only_condition_is_not_relaxed_by_a_broader_attachment():
    notice = ('1. 입찰참가자격\n'
              '가. 소기업자 또는 소상공인으로서 소기업·소상공인 확인서를 소지한 업체\n'
              '2. 계약조건')
    attachment = ('1. 참가자격\n'
                  '가. 중·소기업자 또는 소상공인으로서 '
                  '중·소기업·소상공인 확인서를 소지한 업체\n'
                  '2. 과업내용')
    rec = record(notice, attachment, 업무구분='일반용역', 입찰추정가격=70_000_000,
                 세부품명번호목록='전시회기획및대행서비스[8014198801]')
    catalog = SimpleNamespace(products={'8014198801': {
        '제품명': '행사서비스', '세부품명': '전시회기획및대행서비스', '특이사항': ''}})
    row, facts = infer(rec, {'v13': '0', 'e13': ''}, catalog)
    assert facts['qualification']['size_conflict']
    bound = facts['qualification']['notice_commercial_size_bound']
    assert bound['attachment_conflict_preserved']
    assert row['v13'] == '1'
    assert facts['decisions']['v13']['reason'] == (
        'competition_notice_excludes_ordinary_medium_enterprises')


def test_competition_product_size_absence_ignores_nonoperative_network_guidance():
    notice = ('1. 입찰참가자격\n'
              '가. 소프트웨어사업자로 등록한 업체\n'
              '※ 중소기업 확인은 공공구매정보망을 활용하여 확인합니다.\n'
              '2. 계약조건')
    rec = record(notice, 업무구분='일반용역', 입찰추정가격=300_000_000,
                 세부품명번호목록='정보시스템개발서비스[8111159901]')
    catalog = SimpleNamespace(products={'8111159901': {
        '제품명': '소프트웨어엔지니어링업', '세부품명': '정보시스템개발서비스',
        '특이사항': '소프트웨어 진흥법 제48조 적용'}})
    row, facts = infer(rec, {'v11': '0', 'e11': ''}, catalog)
    assert facts['qualification']['no_operative_size_prerequisite']
    assert facts['qualification']['no_size']
    assert row['v11'] == '1'


def test_explicit_bidder_classes_and_invalidity_are_not_lookup_only_guidance():
    notice = ('1. 입찰참가자격\n'
              '가. 중소기업 또는 소상공인\n'
              '※ 중기업, 소기업 또는 소상공인 확인은 공공구매종합정보망에서 하며, '
              '자격조건이 되지 않는 업체의 입찰은 무효입니다.\n'
              '2. 계약조건')
    rec = record(notice, 업무구분='일반용역', 입찰추정가격=300_000_000,
                 세부품명번호목록='정보시스템개발서비스[8111159901]')
    catalog = SimpleNamespace(products={'8111159901': {
        '제품명': '소프트웨어엔지니어링업', '세부품명': '정보시스템개발서비스',
        '특이사항': '소프트웨어 진흥법 제48조 적용'}})
    row, facts = infer(rec, {'v11': '0', 'e11': ''}, catalog)
    assert not facts['qualification']['no_size']
    assert row['v11'] == '0'


def special_alternative_case(header, entities):
    rec = record(header+'\n'+entities+'\n2. 계약조건', 업무구분='일반용역',
                 입찰추정가격=70_000_000,
                 세부품명번호목록='전시회기획및대행서비스[8014198801]')
    catalog = SimpleNamespace(products={'8014198801': {
        '제품명': '행사서비스', '세부품명': '전시회기획및대행서비스', '특이사항': ''}})
    return infer(rec, {'v13': '0', 'e13': ''}, catalog)


def test_special_entity_alternatives_preserve_the_ordinary_small_company_bound():
    row, facts = special_alternative_case(
        '1. 아래의 입찰참가자격을 모두 갖춘 자이어야 합니다.',
        ('가. 「중소기업기본법」에 따른 소기업자, 「소상공인기본법」에 따른 소상공인, '
         '「벤처기업육성에 관한 특별조치법」에 따른 벤처기업 또는 '
         '「중소기업창업 지원법」에 따른 창업자'))
    bound = facts['qualification']['ordinary_commercial_size_bound']
    assert bound['commercial_upper_bound'] == ['micro', 'small']
    assert bound['special_entity_alternatives_preserved'] == ['벤처기업', '창업자']
    assert row['v13'] == '1'


@pytest.mark.parametrize('header,entities', [
    ('1. 아래의 입찰참가자격을 모두 갖춘 자이어야 합니다.',
     '가. 중기업, 소기업, 소상공인, 벤처기업 또는 창업자'),
    ('1. 입찰참가자격',
     '가. 소기업, 소상공인, 벤처기업 또는 창업자'),
])
def test_ordinary_bound_is_not_inferred_with_medium_or_without_exhaustive_governor(header, entities):
    row, facts = special_alternative_case(header, entities)
    assert facts['qualification']['ordinary_commercial_size_bound'] is None
    assert row['v13'] == '0'
