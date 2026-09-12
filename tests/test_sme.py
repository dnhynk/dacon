"""Semantic SME conditions, with no development identities or labels."""
from pathlib import Path

import pytest

from submission.pps.products import ProductFacts
from submission.pps.sme import extract_sme_facts, norm, size_facts

CATALOG = Path(__file__).resolve().parents[1] / 'data_open/data/법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv'


@pytest.fixture(scope='module')
def catalog():
    if not CATALOG.exists():
        pytest.skip('provided catalog unavailable')
    return ProductFacts(CATALOG)


def notice(body, price=150_000_000, code=None):
    return {'meta': {'적용계약법':'지방계약법', '업무구분':'일반용역',
                     '계약방법':'제한경쟁', '입찰추정가격':price, '세부품명번호목록':code},
            'docs':[{'type':'공고문','doc_id':'notice','text':body}],
            'input_completeness':{'완전관측':True}, 'dropped_doc_counts':{}}


def test_certificate_scope_intersection_union_and_unicode():
    for mark in ['·','ㆍ','ᆞ','․','‧','・','∙','･',',','.','-']:
        assert set(size_facts(norm(f'나. 중{mark}소기업{mark}소상공인 확인서를 소지한 업체'))['allowed']) == {'medium','small','micro'}
    a=size_facts(norm('나. 「중소기업기본법」에 따른 중소기업으로서 소기업·소상공인확인서를 소지한 업체'))
    b=size_facts(norm('나. 중기업 확인서, 중소기업확인서, 소기업확인서 중 하나를 소지한 업체'))
    assert set(a['allowed']) == {'small','micro'}
    assert 'medium' in b['allowed']


def test_actual_purchase_and_numeric_catalog_condition(catalog):
    text='1. 사업개요\n용역명: 축제 행사대행 용역\n2. 입찰참가자격\n가. 직접생산확인증명서(9015189001)를 소지한 업체\n나. 중소기업확인서를 소지한 업체\n3. 제출서류'
    below=extract_sme_facts(notice(text,299_999_999),catalog)
    equal=extract_sme_facts(notice(text,300_000_000),catalog)
    incidental=extract_sme_facts(notice(text.replace('축제 행사대행','축제 무대설치'),300_000_000),catalog)
    assert below['product']['status']=='competition'
    assert equal['decisions']['v12']['value']==equal['decisions']['v14']['value']==1
    assert incidental['product']['status']=='unknown'


def test_absence_requires_full_input_and_actual_identity(catalog):
    text='1. 사업개요\n2. 입찰참가자격\n가. 세부품명번호 6010989901 실물모형및전시물을 제조물품으로 등록한 업체\n3. 제출서류\n사업자등록증'
    rec=notice(text,code='6010989901')
    full=extract_sme_facts(rec,catalog)
    assert full['decisions']['v10']['value']==full['decisions']['v11']['value']==1
    rec['dropped_doc_counts']={'제안요청서':1}  # Contradictory completeness flag cannot prove absence.
    partial=extract_sme_facts(rec,catalog)
    assert partial['decisions']['v10']['value'] is None
    assert partial['decisions']['v11']['value'] is None
    rec=notice(text.replace('6010989901','9999999999'),code='9999999999')
    assert extract_sme_facts(rec,catalog)['product']['status']=='unknown'


def test_forms_are_not_eligibility_and_price_conflict_is_unknown(catalog):
    text='1. 사업개요\n용역명: 축제 행사대행 용역\n2. 입찰참가자격\n사업자등록을 마친 업체\n3. 제출서류\n직접생산확인증명서(9015189001) 1부'
    result=extract_sme_facts(notice(text,300_000_000),catalog)
    assert result['direct_production']['active_clauses']==0
    assert result['decisions']['v12']['value'] is None
    result=extract_sme_facts(notice('추정가격: 200,000,000원\n'+text,300_000_000),catalog)
    assert result['price']['effective_won'] is None


def test_negated_withdrawn_and_alternative_requirements_do_not_clear_absence(catalog):
    for wording in ['중소기업확인서를 소지한 업체로 제한하지 않는다.',
                    '중소기업확인서를 소지한 업체라는 조건은 삭제되었다.']:
        rec=notice('2. 입찰참가자격\n가. '+wording+'\n3. 제출서류')
        result=extract_sme_facts(rec,catalog)
        assert result['enterprise_size']['active_clauses']==0
        assert result['decisions']['v11']['value'] is None
    rec=notice('2. 입찰참가자격\n다음 중 어느 하나를 갖춘 자\n가. 중소기업확인서를 소지한 업체\n나. 특별법에 따른 공공 연구기관\n3. 제출서류')
    result=extract_sme_facts(rec,catalog)
    assert result['enterprise_size']['allowed_commercial'] is None
    assert result['decisions']['v11']['value'] is None
