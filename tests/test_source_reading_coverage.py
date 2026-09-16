from tools.source_coverage import covered
from tools.compare_notice_retrieval import hit


def test_split_excerpts_do_not_lose_a_fully_returned_sentence():
    rec={'docs':[{'text':'동등품을\n허용한다.'}]}
    spans=[{'doc_index':0,'start':0,'end':4},{'doc_index':0,'start':5,'end':10}]
    witness={'doc_index':0,'start':0,'end':10}
    assert not hit(witness,{'spans':spans})  # Single S-number citation differs.
    assert covered(rec,spans,witness)


def test_missing_negation_cannot_be_joined_into_reading_coverage():
    text='동등품은 허용하지 않는다.'
    rec={'docs':[{'text':text}]}
    spans=[{'doc_index':0,'start':0,'end':4},{'doc_index':0,'start':9,'end':len(text)}]
    assert not covered(rec,spans,{'doc_index':0,'start':0,'end':len(text)})


def test_equal_words_in_another_document_cannot_fill_a_source_gap():
    rec={'docs':[{'text':'제출 시점은 계약 이후다.'},{'text':'제출 시점은 계약 이후다.'}]}
    spans=[{'doc_index':0,'start':0,'end':6},{'doc_index':1,'start':6,'end':14}]
    assert not covered(rec,spans,{'doc_index':0,'start':0,'end':14})
