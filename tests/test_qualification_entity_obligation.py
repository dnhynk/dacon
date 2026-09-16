"""A bidder's size, certificate possession and paper submission are distinct facts."""
from types import SimpleNamespace
import pytest

from submission.pps.qualification import infer, inventory, qualification_facts
from tests.test_comparison import record


def facts(clause, tail='', heading='입찰참가자격'):
    rec=record('1. '+heading+'\n가. '+clause+'\n'+tail+'\n2. 납품조건\n계약기간: 1년', 업무구분='일반용역')
    return rec,qualification_facts(rec,inventory(rec))


@pytest.mark.parametrize('predicate', ['구비해야 합니다.', '구비하여야 한다.', '갖추어야 합니다.'])
def test_entity_obligation_proves_a_size_bound_without_proving_issued_certificates(predicate):
    rec,q=facts('중소기업 자격을 '+predicate)
    bound=q['common_size_bound']
    assert bound and bound['larger_commercial_enterprises_excluded']
    assert not q['no_size'] and not q['active_direct']
    for ev in bound['evidence']:
        assert rec['docs'][ev['doc_index']]['text'][ev['start']:ev['end']]==ev['text']


def test_wrapped_statutes_and_a_later_paper_waiver_do_not_erase_entity_qualification():
    clause=('「중소기업기본법」 제2조 중소기업 및 「소기업 및 소상공인 지원을 위한 법률」\n'
            '제2조 소상공인 자격을 구비해야 합니다.')
    rec,q=facts(clause, '▪ 전산망으로 중소기업 확인서가 확인되면 서류 제출은 면제할 수 있음.')
    bound=q['common_size_bound']
    assert bound and bound['commercial_upper_bound']==['medium','micro','small']
    assert q['allowed'] is None  # Ambiguous AND wording is not an exact allowed set.
    assert len(bound['evidence'])==1 and '서류 제출' not in bound['evidence'][0]['text']
    assert not q['active_direct']


@pytest.mark.parametrize('clause', [
    '중소기업 자격을 구비할 수 있습니다.',
    '중소기업 자격을 구비해야 하는 것은 아닙니다.',
    '예시: 중소기업 자격을 구비해야 합니다.',
    '중소기업이 참가하는 경우 중소기업 자격을 구비해야 합니다.',
    '낙찰자는 계약체결 이후 중소기업 자격을 구비해야 합니다.',
    '분담 구성원은 중소기업 자격을 구비해야 합니다.',
    '협력업체는 중소기업 자격을 구비해야 합니다.',
    '대기업 또는 중소기업 자격을 구비해야 합니다.',
    '중소기업 또는 벤처기업 자격을 구비해야 합니다.',
    '중소기업 자격을 구비해야 한다는 조건은 삭제합니다.',
    '중소기업 자격을 구비해야 합니다. 이 자격조건은 삭제합니다.',
    '“중소기업 자격을 구비해야 합니다.”라는 문장은 예시입니다.',
    '제조사는 중소기업 자격을 구비해야 합니다.',
    '발주기관은 중소기업 자격을 구비해야 합니다.',
])
def test_conditional_other_actor_or_unresolved_alternative_cannot_prove_the_bidder_bound(clause):
    assert facts(clause)[1]['common_size_bound'] is None


@pytest.mark.parametrize('heading', ['제출서류', '평가기준'])
def test_forms_and_scoring_do_not_become_bidder_eligibility(heading):
    assert facts('중소기업 자격을 구비해야 합니다.',heading=heading)[1]['common_size_bound'] is None


def test_another_commercial_branch_still_blocks_a_whole_eligibility_bound():
    assert facts('중소기업 자격을 구비해야 합니다.', '나. 대기업도 참가할 수 있습니다.')[1]['common_size_bound'] is None


def test_immediately_following_submission_waiver_has_a_different_subject():
    _,q=facts('중소기업 자격을 구비해야 함. 서류 제출은 면제함.')
    assert q['common_size_bound']


def test_known_size_requirement_clears_only_missing_requirement_bits_without_catalog_promotion():
    rec,_=facts('중소기업 자격을 구비해야 합니다.')
    row={'v10':'1','e10':'','v11':'1','e11':'','v16':'1','e16':'','v18':'1','e18':''}
    result,trace=infer(rec,row,SimpleNamespace(products={}))
    assert trace['product']['status']=='unknown'
    assert result['v10']=='1'
    assert [result[f'v{i}'] for i in (11,16,18)]==['0']*3
    assert all(trace['decisions'][f'v{i}']['value']==0 for i in (11,16,18))
