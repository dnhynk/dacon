"""Governing-law ownership, not a law-name occurrence count."""
import copy
from pathlib import Path

import pytest

from submission.pps.legal_context import applicable_law, resolve_scope
from submission.pps.knowledge import Knowledge
from submission.pps.other_checks import legal_scope
from submission.pps.performance import performance_facts
from submission.pps.rules import joint_share_check, narrow_region_check
from submission.pps.temporal import v23
from submission.pps.v2_quote_check import check as quote_check


def notice(text, law='국가계약법', attachment=None, **meta):
    r = dict(id='synthetic-law-declaration', meta={
        '적용계약법':law, '업무구분':'일반용역', '소관구분':'국가기관',
        '공동도급구성방식':'공동이행', '계약방법':'제한경쟁', '낙찰방법':'협상에의한계약',
        '입찰추정가격':80000000, '배정예산금액':88000000, '공고게시일자':'20260101', **meta},
        docs=[dict(doc_id='D0',type='공고문',text=text)],
        input_completeness={'완전관측':True}, dropped_doc_counts={})
    if attachment is not None:
        r['docs'].append(dict(doc_id='D1',type='제안요청서',text=attachment))
    return r


@pytest.mark.parametrize('text', [
    '적용계약법: 지방계약법',
    '적 용 계 약 법 : 지 방 계 약 법',
    '○ 적용계약법: 지방계약법',
    '가. 적용계약법: 지방계약법',
    '② 적용계약법: 지방계약법',
    '| 적용계약법 | 지방계약법 |',
    '○ | 적용계약법 : | 지방계약법 |',
    '공고번호: 1 | 적용계약법: 지방계약법 | 분할납품: 불가',
    '계약적용법령: ｢지방자치단체를 당사자로 하는 계약에 관한 법률｣',
    '적용계약법:\n지방계약법',
    '적용계약법: 지방자치단체를 당사자로 하는\n계약에 관한 법률',
    '본 계약은 지방계약법을 적용한다.',
    '가. 본 계약은 지방계약법을 적용합니다.',
    '○ | 본 계약은 ｢지방계약법｣을 적용한다. |',
    '본 계약에는 지방계약법이 적용됩니다.',
    '본 계약의 적용법령은 지방계약법입니다.',
    '이 입찰은 지방계약법에 따라 진행한다. 다음 조건을 확인한다.',
])
def test_clear_declaration_survives_layout_and_owns_the_effective_law(text):
    r = notice(text)
    original = copy.deepcopy(r)
    s = resolve_scope(r)
    assert s['status']=='local' and s['effective_source']=='notice_declaration'
    assert s['source_conflict'] and s['metadata_value']=='국가계약법'
    assert applicable_law(r)=='지방계약법' and legal_scope(r)['law']=='지방계약법'
    for signal in s['signals']:
        if signal['source']=='document':
            t = r['docs'][signal['doc_index']]['text']
            assert t[signal['start']:signal['end']].strip()==signal['text']
    assert r==original


@pytest.mark.parametrize('value', [
    '국가계약법 또는 지방계약법', '국가계약법(해당 시)', '국가계약법(예정)',
    '국가계약법인지 미확정', '[국가계약법」', '지방계약법(현행 여부 미확인)',
    '미정', '미입력', '', '별도 계약규정',
])
def test_unsupported_explicit_notice_field_cannot_silently_fall_back_to_meta(value):
    r = notice('적용계약법: '+value+'\n공동이행 구성원별 최소 지분율은 7% 이상이어야 한다.')
    s = resolve_scope(r)
    assert s['status']=='unknown' and applicable_law(r) is None
    assert s['metadata_value']=='국가계약법'
    assert any(x['kind']=='unresolved' for x in s['signals'])
    assert joint_share_check(r) is None and v23(r)['value'] is None


@pytest.mark.parametrize('text', [
    '예시:\n설명용 문구입니다.\n\n본 계약은 지방계약법을 적용한다.',
    '1. 작성 예시\n다음 내용을 참고한다.\n적용계약법: 지방계약법',
    '예시 | 적용계약법: 지방계약법 |',
    '(예시)\n본 계약은 지방계약법을 적용한다.',
    '참고자료: 1. 적용계약법: 지방계약법',
    '“인용 문구\n본 계약은 지방계약법을 적용한다.\n”',
    '본 계약은 지방계약법을 적용한다고 가정한다.',
    '청렴계약제는 지방계약법 제6조의2에 따라 적용한다.',
    '본 계약은 지방계약법 제6조의2에 따른 청렴계약제가 적용됩니다.',
    '본 계약은 지방계약법 시행령 제8조에 따라 예정가격을 작성하지 않는다.',
    '계약보증금은 지방계약법 시행령 제51조부터 제54조까지의 적용을 받습니다.',
    '기타 명시되지 않은 사항은 지방계약법을 준용한다.',
])
def test_examples_quotations_and_partial_legal_citations_are_not_global_overrides(text):
    assert resolve_scope(notice(text))['status']=='national'


def test_explicit_end_of_example_restores_real_notice_scope():
    r = notice('1. 작성예시\n본 계약은 국가계약법을 적용한다.\n\n'
               '2. 실제 공고내용\n본 계약은 지방계약법을 적용한다.')
    s = resolve_scope(r)
    assert s['status']=='local'
    assert any(x['ignored_reason']=='explicit_reference_block' for x in s['ignored_declarations'])


def test_numbered_peer_heading_ends_reference_scope_but_nested_sample_lines_do_not():
    r = notice('1. 작성예시\n가. 입찰참가자격\n본 계약은 지방계약법을 적용한다.\n'
               '2. 입찰참가자격\n본 계약은 국가계약법을 적용한다.',law='지방계약법')
    s = resolve_scope(r)
    assert s['status']=='national' and len(s['ignored_declarations'])==1


@pytest.mark.parametrize('text', [
    '본 계약은 국가계약법을 적용하지 않는다.',
    '본 계약에는 국가계약법이 적용되지 않습니다.',
    '적용계약법: 국가계약법(미적용)',
    '○ | 적용계약법 : | 국가계약법 적용 제외 |',
])
def test_explicit_exclusion_does_not_choose_the_other_law(text):
    s = resolve_scope(notice(text))
    assert s['status']=='conflict' and applicable_law(notice(text)) is None
    assert s['excluded_scopes']==['national']
    assert s['source_conflict']


@pytest.mark.parametrize('tail', [
    '적용한다는 문구는 철회한다.',
    '적용하는 경우에 한한다.',
    '적용하지 않으며, 지방계약법을 적용한다.',
    '적용한다, 단 해당 부분의 적용 여부는 미정이다.',
])
def test_unresolved_predicate_or_compound_clause_is_not_a_positive_declaration(tail):
    s = resolve_scope(notice('본 계약은 국가계약법을 '+tail))
    assert s['status']=='unknown'


def test_notice_priority_preserves_attachment_disagreement_and_two_notice_conflicts():
    r = notice('적용계약법: 지방계약법', attachment='적용계약법: 국가계약법')
    s = resolve_scope(r)
    assert s['status']=='local' and s['source_conflict']
    assert len([x for x in s['signals'] if x['source']=='document'])==2
    r['docs'][0]['text']+='\n적용계약법: 국가계약법'
    assert resolve_scope(r)['status']=='conflict'


def test_clear_declaration_cannot_erase_another_unreadable_notice_declaration():
    s = resolve_scope(notice('적용계약법: 지방계약법\n적용계약법: 추후 확정'))
    assert s['status']=='unknown'


@pytest.mark.parametrize('text', [
    '적용계약법: 국가계약법\n또는 지방계약법',
    '적용계약법: 지방계약법\n비고: 위 법령은 작성 예시이다.',
    '적용계약법: 지방계약법\n적용조건: 협의 후 확정한다.',
    '본 계약은 지방계약법을 적용한다.\n위 법령의 적용 기재는 철회한다.',
    '적용계약법: 지방자치단체를 당사자로 하는\n계약에 관한 법률(적용 여부 미정)',
])
def test_related_continuations_cannot_be_detached_from_the_law_declaration(text):
    assert resolve_scope(notice(text))['status']=='unknown'


def test_wrapped_conjunctive_law_list_retains_the_conflict():
    assert resolve_scope(notice('적용계약법: 국가계약법\n및 지방계약법'))['status']=='conflict'


@pytest.mark.parametrize('other', [
    '분할납품: 불가', '장비대여: 적용하지 않는다.',
    '검사조건: 승인 여부 미확정',
])
def test_unrelated_field_conditions_do_not_change_the_law(other):
    assert resolve_scope(notice('적용계약법: 지방계약법\n'+other))['status']=='local'


@pytest.mark.parametrize('law,expected', [('국가계약법',1),('지방계약법',0)])
@pytest.mark.parametrize('meta_law', ['국가계약법','지방계약법',None])
def test_joint_member_share_uses_notice_law_with_unchanged_seven_percent_condition(law,expected,meta_law):
    r = notice('○ | 적용계약법 : | '+law+'\n공동이행 구성원별 최소 지분율은 7% 이상이어야 한다.', meta_law)
    assert joint_share_check(r)['value']==expected


def test_briefing_consumer_and_legal_packet_use_the_same_notice_law():
    r = notice('가. 본 계약은 지방계약법을 적용한다.\n'
        '사업설명회: 2026. 1. 5.\n\n제안서 제출 마감: 2026. 1. 10.')
    assert v23(r)['value']==1
    k = Knowledge(Path(__file__).resolve().parents[1]/'data_open/data')
    packet = k.legal_context_v2(r,[21,23],3600,return_metadata=True)
    assert packet['scope']['status']=='local' and packet['used_chars']<=3600


def test_unknown_law_blocks_automatic_performance_and_regional_thresholds():
    r = notice('적용계약법: 추후 확정\n2. 입찰 참가자격\n'
        '가. 단일 용역 수행 실적 2억원 이상이 있는 업체\n'
        '본점 소재지가 [지역:r1|단위=기초|광역=경기도]에 있는 업체이어야 한다.')
    assert performance_facts(r)['overlays']['v2']['value'] is None
    assert narrow_region_check(r) is None


def test_local_small_quote_exception_uses_effective_law():
    r = notice('소액수의 견적서 제출 안내 공고\n적용계약법: 지방계약법\n'
               '입찰참가자격\n가. 용역 실적 2억원 이상인 업체이어야 한다.', 계약방법='수의계약')
    assert quote_check(r,performance_facts(r))['value']==0


@pytest.mark.parametrize('law', ['국가계약법','지방계약법'])
def test_region_consumers_obey_the_same_effective_family(law):
    from submission.pps.regions import above_ceiling_region_check, multiple_region_check
    r = notice('적용계약법: '+law+'\n[수요기관(기초자치단체)|지역=r1]\n'
        '1. 입찰참가자격\n본점 소재지가 [지역:r2|단위=기초|광역=경기도] 내에 소재한 업체이어야 한다.\n'
        '본점 소재지를 경기도 또는 서울특별시 내에 둔 업체이어야 한다.',
        law=None,입찰추정가격=300000000)
    if law=='지방계약법':
        assert narrow_region_check(r)['ceiling']==500000000
        assert multiple_region_check(r)['sufficient_price_upper_bound']==500000000
        assert above_ceiling_region_check(r) is None
    else:
        assert narrow_region_check(r) is None and multiple_region_check(r) is None
        assert above_ceiling_region_check(r)['value']==1


def test_qualification_sme_and_model_overlay_do_not_bypass_unresolved_notice_law():
    import json
    from submission.pps.qualification import infer
    from submission.pps.sme import extract_sme_facts
    from submission.pps.model_fact_overlay import overlay, PRODUCT_FIELD
    text = ('용역명: 농림수산연구조사서비스\n적용계약법: 지방계약법\n'
        '2. 입찰참가자격\n가. 중소기업확인서를 소지한 업체이어야 합니다.\n3. 기타사항')
    r = notice(text,law=None,세부품명번호목록='농림수산연구조사서비스[7010150001]')
    k = Knowledge(Path(__file__).resolve().parents[1]/'data_open/data');k.detailed_product_facts(r)
    row, facts = infer(r,{'v17':'0','e17':''},k._product_facts)
    assert row['v17']=='1'
    assert extract_sme_facts(r,k._product_facts)['decisions']['v14']['value']==0
    r['docs'][0]['text']=text.replace('적용계약법: 지방계약법','적용계약법: 추후 확정')
    row, facts = infer(r,{'v17':'0','e17':''},k._product_facts)
    assert row['v17']=='0'
    assert extract_sme_facts(r,k._product_facts)['decisions']['v14']['value'] is None
    response=dict(finish_reason='stop',text=json.dumps({'facts':{PRODUCT_FIELD:'경쟁제품에 해당하지 않습니다.'}}))
    result, trace = overlay(r,{'v17':'0','e17':''},response,facts,(17,))
    assert trace['gate']=='unknown_contract_law' and result['v17']=='0'
