"""Source links must preserve document ownership, captions and unmatched rows."""
from types import SimpleNamespace

from tests.test_purchase_detail_links import card
from tools.compare_purchase_detail_context import LinkedDetailSearch, summary_ranges


class Tokenizer:
    def encode(self,text,**kwargs):
        return list(text)


def test_context_preserves_ambiguous_descriptions_without_cross_document_linking():
    text='품명 | 단위 | 수량\n센서(A형) | 개 | 3\n\n'+card()+'\n<참고용 예시>\n'+card(unit='세트')
    rec={'id':'one','meta':{},'docs':[{'doc_id':'a','type':'공고문','text':text},
                                   {'doc_id':'b','type':'규격서','text':card(extra='다른 문서의 같은 이름')} ]}
    tool=LinkedDetailSearch(rec,Tokenizer())
    links=tool.detail_inventory[0]['links']
    assert len(links)==2 and all(not link['unique_literal_target'] for link in links)
    row=links[0]['summary_row']
    ranges=tool._context(SimpleNamespace(doc_index=0,start=row['start'],end=row['end']))
    assert all(di==0 for di,_,_ in ranges)
    for found in tool.detail_inventory[0]['cards']:
        assert any(lo<=found['source']['start'] and hi>=found['source']['end'] for _,lo,hi in ranges)
        if found['preceding_caption']:
            assert any(lo<=found['preceding_caption']['start'] and hi>=found['preceding_caption']['end'] for _,lo,hi in ranges)
    assert all(not found['whole_purchase_certified'] for found in tool.detail_inventory[0]['cards'])


def test_preserved_summary_includes_rows_that_have_no_linked_description():
    text='품명 | 단위 | 수량\n센서(A형) | 개 | 3\n알 수 없는 물품 | 개 | 2\n\n'+card()
    rec={'id':'two','meta':{},'docs':[{'doc_id':'a','type':'공고문','text':text}]}
    tool=LinkedDetailSearch(rec,Tokenizer())
    assert tool.detail_inventory[0]['unmatched_summary_rows']
    ranges=summary_ranges(rec,tool.detail_inventory)
    for row in tool.detail_inventory[0]['unmatched_summary_rows']:
        ref=row['summary_row']
        assert any(lo<=ref['start'] and hi>=ref['end'] for _,lo,hi in ranges)
    result=tool.search([9,10,11,18],token_budget=len(text),method='lexical',required_ranges=ranges,
                       queries=('센서 용도 규격 구성품',),selection_policy='evidence_cover')
    assert result['source_tokens']<=len(text)
    for span in result['spans']:
        assert text[span['start']:span['end']]==span['text']
