"""Anonymous attributes cannot change a type-based regional rule."""
import copy

import pytest

from submission.pps.rules import narrow_region_check, apply_rules
from submission.pps.regions import multiple_region_check, provinces


def record(text, *, price=300_000_000, law='지방계약법'):
    return dict(id='synthetic-anonymous-region',meta={'적용계약법':law,'업무구분':'일반용역',
        '계약방법':'제한경쟁','입찰추정가격':price},docs=[dict(doc_id='D0',type='공고문',text=text)],
        input_completeness={'완전관측':True},dropped_doc_counts={})


@pytest.mark.parametrize('attributes',['','|지역=r1','|지역=r1|표기=기관'])
@pytest.mark.parametrize('price',[100_000_000,300_000_000])
def test_basic_authority_attributes_preserve_direct_jurisdiction_restriction(attributes,price):
    rec=record('입찰참가자의 본점은 [수요기관(기초자치단체)'+attributes+'] 관할구역 내에 소재한 업체이어야 한다.',price=price)
    before=copy.deepcopy(rec)
    check=narrow_region_check(rec)
    assert check is not None and check['value']==1
    assert check['evidence'] in rec['docs'][0]['text']
    for initial in (0,1):
        result,_=apply_rules(rec,{'v6':initial,'e6':''},items=(6,))
        assert result['v6']==1
    assert rec==before


@pytest.mark.parametrize('attributes',['','|지역=r1'])
def test_authority_type_in_notice_header_is_separate_from_qualification_target(attributes):
    rec=record('[수요기관(기초자치단체)'+attributes+']\n\n1. 입찰참가자격\n'
        '본점 소재지가 [지역:r2|단위=기초|광역=경기도] 내에 소재한 업체이어야 한다.')
    assert narrow_region_check(rec)['ceiling']==500_000_000


@pytest.mark.parametrize('attributes',['|단위=기초동|광역=경기도','|단위=기초|단위=광역|광역=경기도'])
def test_partial_or_conflicting_unit_attribute_does_not_certify_basic_municipality(attributes):
    rec=record('본점 소재지가 [지역:r1'+attributes+'] 내에 소재한 업체이어야 한다.',price=100_000_000)
    assert narrow_region_check(rec) is None


def test_basic_authority_only_in_attachment_does_not_raise_notice_ceiling():
    rec=record('본점 소재지가 [지역:r1|단위=기초|광역=경기도] 내에 소재한 업체이어야 한다.')
    rec['docs'].append(dict(doc_id='D1',type='규격서',text='[수요기관(기초자치단체)|지역=r1]'))
    assert narrow_region_check(rec) is None


def test_conflicting_authority_types_do_not_choose_the_higher_ceiling():
    rec=record('[수요기관(기초자치단체)|지역=r1]\n[수요기관(광역자치단체)|지역=r2]\n'
        '본점 소재지가 [지역:r3|단위=기초|광역=경기도] 내에 소재한 업체이어야 한다.')
    assert narrow_region_check(rec) is None
    rec['docs'][0]['text']+='\n본점 소재지를 경기도와 서울특별시 내에 둔 업체이어야 한다.'
    assert multiple_region_check(rec) is None


def test_conflicting_province_attributes_do_not_choose_the_first_one():
    text='[지역:r1|단위=기초|광역=경기도|광역=부산광역시] 및 서울특별시'
    assert provinces(text)=={'서울특별시'}


def test_region_identity_is_document_and_record_local_with_exact_source_offsets():
    from submission.pps.anonymized_tokens import anonymous_tokens
    text='공고 [수요기관(기초자치단체)|지역=r1] [지역:r1|광역=경기도|단위=기초]'
    tokens=list(anonymous_tokens(text,document_key=('one-notice',0)))
    assert tokens[0].region_key==tokens[1].region_key==('one-notice',0,'r1')
    for token in tokens:
        assert text[token.start:token.end]==token.text
    other_doc=list(anonymous_tokens(text,document_key=('one-notice',1)))[1]
    other_record=list(anonymous_tokens(text,document_key=('another-notice',0)))[1]
    unbound=list(anonymous_tokens(text))[1]
    assert tokens[1].region_key!=other_doc.region_key
    assert tokens[1].region_key!=other_record.region_key
    assert unbound.region_key is None


@pytest.mark.parametrize('suffix',['|지역=r1|지역=r2','|지역=','|지역'])
def test_malformed_attributes_preserve_observations_but_cannot_identify_region(suffix):
    from submission.pps.anonymized_tokens import anonymous_tokens
    raw='[수요기관(기초자치단체)'+suffix+']'
    token=list(anonymous_tokens(raw,document_key=('notice',0)))[0]
    assert token.text==raw and token.errors and token.region_key is None
    assert narrow_region_check(record('본점이 '+raw+' 관할구역 내에 소재한 업체이어야 한다.')) is None


def test_projection_cannot_join_characters_or_read_an_institution_attribute_as_a_province():
    assert provinces('서[수요기관(기초자치단체)|지역=r1]울특별시')==set()
    assert provinces('[수요기관(기초자치단체)|비고=경기도] 및 서울특별시')=={'서울특별시'}


def test_province_field_must_be_a_recognized_value_not_arbitrary_prose():
    assert provinces('[지역:r1|단위=기초|광역=경기도 또는 부산광역시] 및 서울특별시')=={'서울특별시'}


@pytest.mark.parametrize('key',[('notice',True),('notice',-1),('',0),('notice',),0])
def test_invalid_document_key_is_rejected(key):
    from submission.pps.anonymized_tokens import anonymous_tokens
    with pytest.raises(ValueError):
        list(anonymous_tokens('[지역:r1|단위=기초]',document_key=key))


@pytest.mark.parametrize('attributes',['','|지역=r1','|지역=r1|표기=기관'])
def test_combined_experience_region_check_preserves_authority_attributes(attributes):
    from submission.pps.performance import performance_facts
    rec=record('2. 입찰 참가자격\n가. 단일 용역 수행 실적 2억원 이상이 있는 업체\n'
        '나. 법인등기부상 본점 소재지를 [수요기관(기초자치단체)'+attributes+'] 내에 둔 업체',price=100_000_000)
    facts=performance_facts(rec)
    assert facts['overlays']['v8']['value']==1
    assert facts['operative_regions'][0]['evidence']['text'] in rec['docs'][0]['text']
    for initial in (0,1):
        row,_=apply_rules(rec,{'v8':initial,'e8':''},items=(8,))
        assert row['v8']==1


@pytest.mark.parametrize('token',['[지역:|광역=경기도]','[지역:r1|광역=경기도'])
def test_invalid_region_placeholder_is_not_an_experience_region_witness(token):
    from submission.pps.performance import performance_facts
    rec=record('2. 입찰 참가자격\n가. 단일 용역 수행 실적 2억원 이상이 있는 업체\n'
        '나. 본점 소재지를 '+token+' 내에 둔 업체',price=100_000_000)
    assert performance_facts(rec)['overlays']['v8']['value'] is None


def test_unclosed_token_is_diagnostic_and_does_not_leak_its_province_attribute():
    from submission.pps.anonymized_tokens import anonymous_tokens
    text='서울특별시 [지역:r1|광역=경기도\n다음 문장'
    token=list(anonymous_tokens(text,document_key=('notice',0)))[0]
    assert token.errors==('unclosed_token',) and token.region_key is None
    assert text[token.start:token.end]==token.text
    assert provinces(text)=={'서울특별시'}


def test_complete_registered_region_metadata_can_supply_units_without_joining_names():
    from submission.pps.anonymized_tokens import registered_region_tokens
    rec = record('2. 입찰참가자격\n본점 소재지가 경기도의 두 시군 안에 있는 업체이어야 한다.',
                 price=111_000_000)
    rec['meta'].update(지역제한여부='Y', 제한지역코드목록=(
        '[등록지역:r1|단위=기초|광역=경기도], '
        '[등록지역:r2|단위=기초|광역=경기도]'))
    tokens = registered_region_tokens(rec)
    assert [token.value for token in tokens] == ['r1', 'r2']
    assert all(token.region_key is None for token in tokens)
    check = narrow_region_check(rec)
    assert check['value'] == 1
    assert check['evidence'] in rec['docs'][0]['text']
    assert check['region_symbols_are_record_local'] is True


@pytest.mark.parametrize('raw', [
    '[등록지역:r1|단위=기초|광역=경기도], 경기도',
    '[등록지역:r1|단위=기초|단위=광역|광역=경기도]',
    '[지역:r1|단위=기초|광역=경기도]',
])
def test_partial_malformed_or_nonregistration_metadata_cannot_certify_basic_scope(raw):
    from submission.pps.anonymized_tokens import registered_region_tokens
    rec = record('2. 입찰참가자격\n본점 소재지가 어느 지역 안에 있는 업체이어야 한다.',
                 price=100_000_000)
    rec['meta'].update(지역제한여부='Y', 제한지역코드목록=raw)
    assert registered_region_tokens(rec) == ()
    assert narrow_region_check(rec) is None


def test_structured_basic_metadata_needs_an_observed_bidder_office_clause():
    rec = record('2. 납품장소\n두 기초지역의 학교에 납품한다.', price=100_000_000)
    rec['meta'].update(지역제한여부='Y', 제한지역코드목록=(
        '[등록지역:r1|단위=기초|광역=경기도], '
        '[등록지역:r2|단위=기초|광역=경기도]'))
    assert narrow_region_check(rec) is None


def test_typed_basic_region_followed_by_region_bidder_is_an_operative_restriction():
    rec = record('2. 입찰참가자격\n부정당업체로 제재를 받지 않은 경기도 '
                 '[지역:r1|단위=기초|광역=경기도] 지역 업체', price=80_000_000)
    rec['meta'].update(지역제한여부='N')
    check = narrow_region_check(rec)
    assert check['value'] == 1
    assert '[지역:r1|단위=기초|광역=경기도]' in check['evidence']


def test_typed_basic_delivery_region_is_not_a_bidder_restriction():
    rec = record('2. 납품장소\n[지역:r1|단위=기초|광역=경기도] 지역 학교에 납품한다.',
                 price=80_000_000)
    assert narrow_region_check(rec) is None
