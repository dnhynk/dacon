"""Known bidder verification is consumed without inventing catalog identity."""
import copy

import pytest

from submission.pps.knowledge import Knowledge
from tests.test_independent_audit import synthetic_notice, DATA
from tests.test_production_verification import CLAUSE


def notice(prefix='', *, extra='', heading='2. 입찰참가자격', clause=CLAUSE):
    return synthetic_notice('1. 사업개요\n용역명: 건강장비 임차 및 유지관리\n'+extra+
        heading+'\n※ '+prefix+clause+'\n3. 계약조건')


def consume(rec, value='1'):
    before = copy.deepcopy(rec)
    row, facts = Knowledge(DATA).qualification_decisions(rec, {'v10': value, 'e10': ''})
    assert rec == before
    return row, facts


@pytest.mark.parametrize('prefix', ['', '중소기업확인서 및 ', '「소기업, 소상공인확인서」 및 ',
                                     '중·소기업·소상공인확인서 및 '])
@pytest.mark.parametrize('value', ['0', '1'])
def test_unqualified_verification_refutes_absence_with_unknown_purchase_identity(prefix, value):
    rec = notice(prefix)
    row, facts = consume(rec, value)
    assert row['v10'] == '0' and row['e10'] == ''
    assert facts['product']['status'] == 'unknown'
    witness = facts['qualification']['unqualified_production_verification'][0]
    assert not witness['catalog_identity_certified'] and not witness['all_item_certificates_certified']
    e = witness['evidence']
    assert rec['docs'][e['doc_index']]['text'][e['start']:e['end']] == e['text']


@pytest.mark.parametrize('prefix', ['책상 공급업체의 ', '대표사에 한하여 ', '분담업체의 ',
    '일부 품목의 ', '중소기업확인서 또는 ', '제조사의 ', '해당되는 경우 ',
    '책상(5610170301)의 ', '계약상대자는 '])
def test_qualified_or_alternative_subject_does_not_refute_missing_other_target(prefix):
    row, facts = consume(notice(prefix))
    assert row['v10'] == '1' and not facts['qualification'].get('unqualified_production_verification')


@pytest.mark.parametrize('heading', ['2. 제출서류', '2. 평가기준', '2. 입찰참가자격(대표사에 한함)',
                                    '2. 입찰참가자격(해당되는 경우)'])
def test_other_section_or_qualified_parent_is_not_an_unqualified_bidder_check(heading):
    row, facts = consume(notice(heading=heading))
    assert row['v10'] == '1' and not facts['qualification'].get('unqualified_production_verification')


def test_mixed_unidentified_purchase_keeps_item_specific_coverage_open():
    row, facts = consume(notice(extra='구매품목: 건강장비 외 2종\n'))
    assert row['v10'] == '1'
    assert facts['product']['uncertainty'] and not facts['qualification'].get('unqualified_production_verification')


def test_different_document_waiver_remains_unresolved():
    rec = notice()
    rec['docs'].append({'type': '예외공표서', 'text': '직접생산확인증명서 보유는 면제한다.'})
    row, facts = consume(rec)
    assert row['v10'] == '1' and not facts['qualification'].get('unqualified_production_verification')


def test_paper_submission_waiver_does_not_remove_database_verification():
    rec = notice()
    rec['docs'].append({'type': '규격서', 'text': '직접생산확인증명서 사본 제출은 생략한다.'})
    row, facts = consume(rec)
    assert row['v10'] == '0' and facts['qualification']['unqualified_production_verification']


@pytest.mark.parametrize('clause', [CLAUSE.replace('확인 가능하여야 하며', '확인할 수 있으며'),
    CLAUSE.replace('자격이 없습니다', '자격에 영향이 없습니다'),
    CLAUSE.replace('직접생산여부 확인은', '소기업확인서는')])
def test_optional_check_or_other_certificate_cannot_refute_production_absence(clause):
    assert consume(notice(clause=clause))[0]['v10'] == '1'


def test_positive_observation_does_not_require_unavailable_attachments():
    rec = notice()
    rec['input_completeness'] = {'완전관측': False}
    rec['dropped_doc_counts'] = {'규격서': 1}
    row, facts = consume(rec)
    assert row['v10'] == '0' and not facts['qualification']['complete']
