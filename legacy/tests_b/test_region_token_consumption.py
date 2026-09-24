"""Anonymous type and attribute boundaries survive temporal/comparison use."""
import copy

import pytest

from submission.pps.comparison import compare,positive_decision
from submission.pps.temporal import region_set,region_observation,region_clauses,v24
from tests.test_comparison import record


def restriction(place):
    return '법인등기부상 본점 소재지를 '+place+'에 둔 업체이어야 한다.'


@pytest.mark.parametrize('token,names', [
    ('[지역:r1|단위=기초|광역=경기도|비고=제주도]',{'경기도'}),
    ('[지역:r1|단위=기초|광역=경기도|광역=부산광역시]',set()),
    ('[지역:r1|단위=기초|광역=경기도 또는 제주도]',set()),
    ('[지역:r1|광역=경기도',set()),
    ('[수요기관(기초자치단체)|비고=경기도]',set()),
    ('[수요기관(경기도 공기업)|지역=r1]',set()),
    ('서[수요기관(기초자치단체)|지역=r1]울특별시',set()),
])
def test_token_prose_and_malformed_attributes_cannot_become_province_values(token,names):
    assert region_set(token)[0]==names


@pytest.mark.parametrize('token,basic,unresolved', [
    ('[지역:r1|단위=기초|광역=경기도]',True,False),
    ('[지역:r1|단위=광역|광역=경기도]',False,False),
    ('[지역:r1|단위=기초동|광역=경기도]',False,True),
    ('[지역:r1|광역=경기도]',False,True),
    ('[지역:r1|단위=기초|단위=광역|광역=경기도]',False,True),
    ('[수요기관(기초자치단체)|지역=r1]',False,True),
])
def test_known_basic_unit_and_unknown_token_scope_are_separate_observations(token,basic,unresolved):
    obs=region_observation(token)
    assert obs['basic_level'] is basic
    assert obs['anonymous_scope_unresolved'] is unresolved
    for ev in obs['unresolved_tokens']:
        assert token[ev['start']:ev['end']]==ev['text']


def test_unknown_attributes_do_not_hide_an_independently_named_outside_branch():
    place='경기도 [지역:r1|단위=기초|광역=경기도|비고=부산광역시] 또는 제주도'
    rec=record(restriction(place),제한지역코드목록='경기도')
    original=copy.deepcopy(rec)
    result=positive_decision(rec,compare(rec))
    assert result and result['value']==1
    assert result['comparison']['outside_registered_provinces']==['제주특별자치도']
    assert result['evidence'] in rec['docs'][0]['text']
    assert rec==original


def test_no_new_province_can_be_fabricated_from_unknown_attributes():
    rec=record(restriction('[지역:r1|단위=기초|광역=경기도|비고=제주도]'),제한지역코드목록='경기도')
    packet=compare(rec)
    assert [f['value'] for f in packet['facts'] if f['field']=='region']==[['경기도']]
    assert not positive_decision(rec,packet)
    assert not any(m['field']=='region_provinces' for m in v24(rec)['value_mismatches'])


def test_unknown_token_is_retained_without_certifying_its_institution_as_a_basic_region():
    token='[수요기관(기초자치단체)|지역=r1|비고=경기도]'
    rec=record(restriction(token),제한지역코드목록='경기도')
    source=region_clauses(rec)
    assert source and not source[0]['basic_level'] and source[0]['value']==[]
    assert source[0]['anonymous_region_scope_unresolved']
    for ev in source[0]['unresolved_region_tokens']:
        assert rec['docs'][ev['doc_index']]['text'][ev['start']:ev['end']]==ev['text']
    packet=compare(rec)
    comparison=next(c for c in packet['comparisons'] if c['field']=='region')
    assert comparison['status']=='hierarchy_unresolved'
    assert positive_decision(rec,packet) is None


def test_unknown_unit_does_not_certify_same_full_region_as_the_metadata():
    rec=record(restriction('[지역:r1|광역=경기도]'),제한지역코드목록='경기도')
    comparison=next(c for c in compare(rec)['comparisons'] if c['field']=='region')
    assert comparison['status']=='hierarchy_unresolved'


def test_local_symbols_do_not_join_across_metadata_or_documents():
    rec=record(restriction('[지역:r1|단위=기초|광역=경기도]'),
        restriction('[지역:r1|단위=기초|광역=경기도]'),
        제한지역코드목록='[지역:r1|단위=기초|광역=경기도]')
    packet=compare(rec)
    assert next(c for c in packet['comparisons'] if c['field']=='region')['status']=='hierarchy_unresolved'
    assert positive_decision(rec,packet) is None


def test_an_unknown_first_token_does_not_erase_an_independent_literal_outside_branch():
    # This original comparison was already defensible: uncertainty in the first
    # branch does not remove the explicitly permitted Jeju branch from an OR.
    rec=record(restriction('경기도 [지역:r1|단위=기초|단위=광역|광역=경기도] 또는 제주도'),
        제한지역코드목록='경기도')
    result=positive_decision(rec,compare(rec))
    assert result and result['comparison']['outside_registered_provinces']==['제주특별자치도']


@pytest.mark.parametrize('prefix', ['지역','등록지역'])
def test_source_and_registered_region_tokens_preserve_basic_scope_without_sharing_identity(prefix):
    from submission.pps.anonymized_tokens import anonymous_tokens
    token=f'[{prefix}:r1|단위=기초|광역=경기도|비고=제주도]'
    assert region_set(token)==({'경기도'},True)
    parsed=list(anonymous_tokens(token,document_key=('one',0)))[0]
    assert parsed.kind==('region' if prefix=='지역' else 'registered_region')
    assert parsed.region_key==(('one',0,'r1') if prefix=='지역' else None)
    rec=record(restriction('경기도'),제한지역코드목록=token)
    assert next(c for c in compare(rec)['comparisons'] if c['field']=='region')['status']=='hierarchy_unresolved'


def test_metadata_registration_tokens_cannot_leak_unrelated_attribute_names():
    token='[등록지역:r1|단위=기초|광역=경기도|비고=제주도]'
    rec=record(restriction('경기도 [지역:r1|단위=기초|광역=경기도] 또는 제주도'),제한지역코드목록=token)
    assert positive_decision(rec,compare(rec)) is None  # Metadata's districts remain unresolved.
    assert region_observation(token)['provinces']==['경기도']


def test_an_institution_issuing_specifications_is_not_an_unresolved_place():
    rec=record('법인등기부상 본점 소재지를 전라남도에 두고, [수요기관(중학교)]에서 제시한 '
        '사양서대로 납품할 수 있는 업체이어야 합니다.',제한지역코드목록='전라남도')
    clauses=region_clauses(rec)
    assert clauses and not any(c.get('anonymous_region_scope_unresolved') for c in clauses)
    assert next(c for c in compare(rec)['comparisons'] if c['field']=='region')['status']=='same'


def test_an_application_form_institution_does_not_become_a_region_clause():
    rec=record('본인(본사)은 [수요기관(공공기관)]에서 시행하는 행사 대행 용역 제안 공모에 대한 '
        '입찰참가신청서를 붙임과 같이 제출합니다.\n업체명:')
    assert not region_clauses(rec)
