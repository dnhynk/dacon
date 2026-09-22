"""Evidence must follow the decided fact without changing its judgment."""
import pytest

from submission.pps.data import make_row
from submission.pps.evidence_selection import select_verdict_evidence


def case(text, item, quote):
    rec={'id':'case','docs':[{'text':text}]}
    values=[0]*24;values[item-1]=1
    evidence=['']*24;evidence[item-1]=quote
    return rec,make_row(rec,values,evidence)


@pytest.mark.parametrize('text,quote',[
    ('설명회는 2026. 2. 5.(목) 14:00에 개최합니다.', '설명회는 2026. 2. 5.'),
    ('설명회는 2026년 2월 5일 14시에 개최합니다.', '설명회는 2026년 2월 5일'),
    ('실적을 보유한\n업체만 참여할 수 있습니다.', '실적을 보유한'),
    ('가. 직접생산확인증명서를 소지한\n업체이어야 합니다.', '가. 직접생산확인증명서를 소지한'),
])
def test_complete_selected_sentence_without_date_or_wrapped_line_truncation(text,quote):
    rec,row=case(text,23,quote);before={k:v for k,v in row.items() if k.startswith('v')}
    select_verdict_evidence(rec,row,[])
    assert row['e23']==text
    assert before=={k:v for k,v in row.items() if k.startswith('v')}


def test_cpu_requirement_wins_over_model_number_without_guessing_removable_context():
    fact='제조사가 발급한 확약서를 입찰마감일까지 제출하여야 합니다.'
    cpu='공고 내용을 안내합니다. '+fact+' 서류는 반환하지 않습니다.'
    rec,row=case('일정 안내\n\n'+cpu,19,'일정 안내')
    select_verdict_evidence(rec,row,[{'item':19,'value':1,'evidence':cpu}])
    assert row['e19']==cpu and row['v19']==1


def test_ambiguous_repeated_anchor_does_not_borrow_context():
    q='설명회는 2026년 2월 5일'
    rec,row=case(q+' 오전 개최합니다.\n\n'+q+' 오후 개최합니다.',23,q)
    select_verdict_evidence(rec,row,[])
    assert row['e23']==q


def test_multiple_cpu_requirement_sentences_and_exception_remain():
    q='공동수급체 최소지분율은 5% 이상이어야 합니다. 다만 대표자는 30% 이상이어야 합니다.'
    rec,row=case(q,21,q)
    select_verdict_evidence(rec,row,[{'item':21,'value':1,'evidence':q}])
    assert row['e21']==q


def test_parenthesized_share_keeps_the_preceding_sentence_identifying_members():
    q=('공동수급체 구성원별 참여비율을 협정서에 명시하여야 합니다. '
       '(최소 지분율 4% 이상)\n- 협정서는 전자 제출하여야 합니다.')
    rec,row=case(q,21,q)
    select_verdict_evidence(rec,row,[{'item':21,'value':1,'evidence':q}])
    assert row['e21']==q and row['v21']==1


def test_cannot_use_unconfirmed_nested_facts_or_older_overruled_witness():
    q='선택한 출처입니다.';other='다른 실적을 요구하는 업체입니다.'
    rec,row=case(q+'\n\n'+other,2,q)
    select_verdict_evidence(rec,row,[{'item':2,'value':1,'evidence':other},
        {'item':2,'value':0,'evidence':''},{'facts':{'item':2,'value':1,'evidence':other}}])
    assert row['e2']==q


@pytest.mark.parametrize('item',[10,11,16,18,20])
def test_absence_stays_blank_even_with_cpu_quote(item):
    rec,row=case('존재하는 문장입니다.',item,'존재하는 문장입니다.')
    select_verdict_evidence(rec,row,[{'item':item,'value':1,'evidence':'존재하는 문장입니다.'}])
    assert row[f'e{item}']=='' and row[f'v{item}']==1


def test_oversize_sentence_keeps_valid_existing_quote():
    q='설명회 일정 '+ '가'*500+'입니다.'
    rec,row=case(q,23,q[:30]);before=row['e23']
    select_verdict_evidence(rec,row,[])
    assert row['e23']==before and len(row['e23'])<=500


def test_no_negative_promotion_or_quote_creation():
    rec,row=case('필수 실적입니다.',2,'')
    row['v2']=0
    select_verdict_evidence(rec,row,[{'item':2,'value':1,'evidence':'필수 실적입니다.'}])
    assert row['v2']==0 and row['e2']==''
