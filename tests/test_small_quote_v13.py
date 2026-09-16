"""Source-bound statutory exceptions through both deterministic consumers."""
import copy
import json

import pytest

from submission.b4_entry import B4Pipeline
from submission.pps.knowledge import Knowledge
from submission.pps.prompts import fact_fields
from submission.pps.small_quote import procedure, review
from tests.test_independent_audit import DATA, synthetic_notice


TITLE = '물품 제조·구매 소액수의계약 견적제출 공고'
LOW = '추정가격 2천만원 이하 물품 또는 용역'
MIDDLE = '추정가격 2천만원 초과 1억원 이하 물품·용역(소기업 소상공인 계약)'
SIZE = '가. 소기업·소상공인 확인서를 소지한 업체'


def notice(title=TITLE, price=15_000_000, *, tail='', disclosure=None):
    body=(title+'\n1. 사업개요\n구매품명: 데스크톱컴퓨터\n'
          '2. 입찰참가자격\n'+SIZE+'\n3. 계약조건\n'+tail)
    r=synthetic_notice(body, 업무구분='물품(내자)', 계약방법='수의계약',
        입찰추정가격=price, 배정예산금액=None, 세부품명번호목록='데스크톱컴퓨터[4321150701]',
        조항호내용=disclosure if disclosure is not None else LOW if price is not None and price<=20_000_000 else MIDDLE)
    r['id']='synthetic-quote';r['docs'][0]['doc_id']='notice'
    return r


@pytest.fixture(scope='module')
def knowledge():
    return Knowledge(DATA)


@pytest.mark.parametrize('law',['국가계약법','지방계약법'])
@pytest.mark.parametrize('price',[1,19_999_999,20_000_000,20_000_001,100_000_000])
def test_two_amount_routes_include_their_boundaries(law,price):
    r=notice(price=price);r['meta']['적용계약법']=law
    result=review(r,price,{'small','micro'})
    assert result['status']=='permitted_small_size_route'
    assert result['amount_route']==('at_most20m' if price<=20_000_000 else 'over20m_at_most100m')
    assert not result['direct_production_waiver_certified']
    for e in result['evidence']:
        assert r['docs'][e['doc_index']]['text'][e['start']:e['end']]==e['text']


@pytest.mark.parametrize('price',[None,0,-1,True,100_000_001,float('nan'),float('inf')])
def test_invalid_or_outside_amount_does_not_clear(price):
    assert review(notice(),price,{'small','micro'})['status']=='unresolved'


@pytest.mark.parametrize('allowed',[None,[],['micro'],['small'],['medium','small','micro']])
def test_only_standard_joint_small_micro_eligibility_is_in_scope(allowed):
    assert review(notice(),15_000_000,allowed)['status']=='unresolved'


@pytest.mark.parametrize('title',[
    '참고: '+TITLE,
    TITLE+'는 예시입니다.',
    '참고용 공고 서식\n'+TITLE,
    '소액수의계약 견적제출 공고는 취소합니다.',
    '본 계약은 소액수의계약으로 체결하지 않습니다.',
    '계약방법: 소액수의계약 또는 일반경쟁',
    '계약방법: 일반경쟁 / 소액수의견적',
    '계약방법: 소액수의계약은 아니며 제한경쟁입니다.',
    '계약방법: 유찰되는 경우 수의계약으로 전환할 수 있습니다.',
    '계약상대자는 부품을 소액수의계약으로 구매할 수 있습니다.',
    '지난 공고의 계약방법: 소액수의계약',
    '입찰공고\n1. 제출서류\n첨부: '+TITLE,
])
def test_keywords_negation_examples_and_other_contracts_do_not_prove_method(title):
    assert review(notice(title),15_000_000,{'small','micro'})['status']=='unresolved'


@pytest.mark.parametrize('tail',[
    '계약방법: 일반경쟁',
    '본 계약은 일반경쟁으로 체결합니다.',
    '본 계약은 소액수의계약이 아닙니다.',
    '본 계약은 소액수의계약이 아닌 일반경쟁입니다.',
    '본 공고를 취소합니다.',
    '수의계약 공고를 철회합니다.',
])
def test_later_current_contradiction_blocks_earlier_title(tail):
    r=notice(tail=tail)
    assert procedure(r)['affirmative']
    assert review(r,15_000_000,{'small','micro'})['status']=='unresolved'


def test_limited_scope_within_actual_quotation_is_not_competitive_method():
    r=notice('입찰방법: 전자입찰(소액수의견적제출, 총액입찰, 제한경쟁)')
    assert review(r,15_000_000,{'small','micro'})['status']=='permitted_small_size_route'


def test_attachment_only_or_metadata_only_does_not_prove_current_method():
    r=notice('물품 구매 입찰공고');r['docs'].append({'doc_id':'attachment','type':'규격서','text':TITLE})
    assert review(r,15_000_000,{'small','micro'})['status']=='unresolved'
    r['docs'].pop()
    assert review(r,15_000_000,{'small','micro'})['status']=='unresolved'


def test_long_identifiers_cannot_trigger_exponential_numbering_backtracking():
    import subprocess
    import sys
    result=subprocess.run([sys.executable,'-B','-c',
        "from submission.pps.small_quote import procedure; "
        "r={'docs':[{'type':'공고문','text':'9'*20000+'\\n'+('999.'*5000)}]}; "
        "assert not procedure(r)['affirmative']"],capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('disclosure',['',MIDDLE,'참고: '+LOW,LOW+'인 경우 가능',LOW+'이지만 미적용'])
def test_missing_wrong_band_or_hypothetical_disclosure_is_not_exception(disclosure):
    r=notice(disclosure=disclosure)
    assert review(r,15_000_000,{'small','micro'})['status']=='unresolved'


@pytest.mark.parametrize('key,value',[('적용계약법',None),('업무구분','공사'),('계약방법','제한경쟁')])
def test_unresolved_or_incompatible_contract_fields_do_not_clear(key,value):
    r=notice();r['meta'][key]=value
    assert review(r,15_000_000,{'small','micro'})['status']=='unresolved'


@pytest.mark.parametrize('price',[15_000_000,50_000_000])
def test_both_cpu_consumers_clear_same_proven_exception_and_preserve_record(knowledge,price):
    r=notice(price=price);before=copy.deepcopy(r)
    s=knowledge.sme_record_facts(r)
    q,f=knowledge.qualification_decisions(r,{'v10':'1','e10':'','v13':'1','e13':''})
    assert s['decisions']['v13']['value']==0
    assert q['v13']=='0' and q['v10']=='1'
    assert f['qualification']['small_quote_v13_review']['status']=='permitted_small_size_route'
    assert r==before


@pytest.mark.parametrize('raw',[0,1])
def test_final_boundary_is_invariant_to_raw_v13_and_model_prose(raw):
    r=notice();items=tuple(range(10,19))
    response={'finish_reason':'stop','text':json.dumps({
        'facts':dict.fromkeys(fact_fields(items),'원문 관계 확인'),
        'judgments':{f'v{k}':{'reason':'경쟁제품 소기업 제한이라고 추정함','v':raw if k==13 else 0,'e':0}
                     for k in items}},ensure_ascii=False)}
    before=copy.deepcopy(response)
    packet={'family':'A','items':list(items),'spans':[], 'generation':{'response_format':'factored'}}
    result,_=B4Pipeline(DATA,None).consume(r,packet,response)
    assert result['v13']==0 and result['e13']==''
    assert response==before


def test_effective_notice_amount_and_law_are_consumed_without_rewriting_meta(knowledge):
    r=notice(price=50_000_000,disclosure=LOW)
    r['meta']['적용계약법']='지방계약법'
    r['docs'][0]['text']=TITLE+'\n본 계약에는 국가계약법을 적용한다.\n추정가격: 15,000,000원\n'+r['docs'][0]['text']
    before=copy.deepcopy(r)
    result,f=knowledge.qualification_decisions(r,{'v13':'1','e13':''})
    proof=f['qualification']['small_quote_v13_review']
    assert proof['law']=='국가계약법' and proof['estimated_price_won']==15_000_000
    assert result['v13']=='0' and r==before


def test_conflicting_or_broken_notice_price_does_not_use_lower_meta(knowledge):
    for amount in ('추정가격: 15,000,000원\n추정가격: 150,000,000원', '추정가격: 15,00,000원'):
        r=notice();r['docs'][0]['text']=amount+'\n'+r['docs'][0]['text']
        result,f=knowledge.qualification_decisions(r,{'v13':'1','e13':''})
        assert f['product']['estimate_won'] is None
        assert result['v13']=='1'


def test_conflicting_size_clauses_do_not_prove_standard_small_quote(knowledge):
    r=notice()
    r['docs'][0]['text']=r['docs'][0]['text'].replace(SIZE,SIZE+'\n나. 중소기업 확인서를 소지한 업체')
    s=knowledge.sme_record_facts(r)
    q,f=knowledge.qualification_decisions(r,{'v13':'1','e13':''})
    assert s['exceptions']['small_quote_v13_review']['status']=='unresolved'
    assert f['qualification']['size_conflict'] and q['v13']=='1'
