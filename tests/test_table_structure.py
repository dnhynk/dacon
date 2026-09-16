"""Known source tables and damaged dumps exercise relationship uncertainty."""
import pytest
from submission.pps.purchase_tables import purchase_tables


@pytest.mark.parametrize('heading', ['2. 준수 사항', '붙임 4', '별첨 3', '별지 제2호'])
def test_table_stops_before_an_independent_section_or_form(heading):
    table='품명\n규격\n수량\n온도계\n실외용\n2'
    text=table+'\n'+heading+'\n보안서약서\n다음 사항을 준수한다.'
    found=purchase_tables(text)
    assert len(found)==1 and found[0]['text']==table


def test_unfilled_table_header_does_not_own_the_following_form():
    assert purchase_tables('품명\n수량\n금액\n붙임 4\n보안서약서\n본인은 서약한다.')==[]


def test_explicit_pipe_cells_keep_source_addresses_and_missing_values():
    from submission.pps.table_structure import table_structures
    text='품명 | 규격 | 단위 | 수량 | 단가(원) | 금액(원)\n온도계 | 실외용 | 개 | | 100 | 200'
    table=table_structures(text)[0]
    assert table['layout']=='pipe' and len(table['rows'])==1
    row=table['rows'][0]
    assert row['literal_column_alignment'] and row['candidates'][0]['quantity'] is None
    assert row['arithmetic']['status']=='not_testable'
    for cell in row['candidates'][0]['cells']:
        assert text[cell['start']:cell['end']]==cell['text']
    assert not table['whole_purchase_certified']


def test_missing_vertical_quantity_keeps_every_monotone_numeric_assignment():
    from submission.pps.table_structure import table_structures
    text='품명\n규격\n단위\n수량\n단가(원)\n금액(원)\n온도계\n실외용\n개\n100\n200'
    row=table_structures(text)[0]['rows'][0]
    assert len(row['numeric_alternatives'])==3
    assert {tuple(c[k]['text'] if c[k] else None for k in ('quantity','unit_price','amount'))
            for c in row['numeric_alternatives']}=={('100','200',None),('100',None,'200'),(None,'100','200')}
    assert not row['literal_column_alignment']
    assert row['arithmetic']['status']=='not_testable'
    assert all(c.get('inferred_quantity') is None for c in row['numeric_alternatives'])


def test_arithmetic_equality_without_tax_and_unit_basis_is_only_a_candidate_check():
    from submission.pps.table_structure import table_structures
    text='품명 | 단위 | 수량 | 단가(원) | 금액(원)\n온도계 | 개 | 2 | 100 | 200'
    row=table_structures(text)[0]['rows'][0]
    assert row['arithmetic']['status']=='equal_with_unverified_basis'
    assert not row['arithmetic']['constraint_certified']


def test_one_won_discrepancy_does_not_trigger_rounding_or_synthetic_repair():
    from submission.pps.table_structure import table_structures
    text='품명 | 단위 | 수량 | 단가(원) | 금액(원)\n온도계 | 개 | 3,600 | 2,440 | 8,784,001'
    row=table_structures(text)[0]['rows'][0]
    assert row['arithmetic']['status']=='different_with_unverified_basis'
    assert row['arithmetic']['difference_won']=='1'
    assert row['candidates'][0]['amount']['text']=='8,784,001'


def test_amounts_and_specifications_of_separate_products_are_not_joined():
    from submission.pps.table_structure import table_structures
    text='품명\n규격\n단위\n수량\n단가\n금액\n온도계\n10cm\n개\n2\n100\n200\n압력계\n20cm\n개\n3\n300\n900'
    table=table_structures(text)[0]
    assert len(table['rows'])==2
    for row,expected in zip(table['rows'],('온도계','압력계')):
        assert any(c['text']==expected for c in row['name_candidates'])
        assert expected in text[row['start']:row['end']]
    assert '900' not in text[table['rows'][0]['start']:table['rows'][0]['end']]


def test_candidate_cap_blocks_claims_of_complete_enumeration():
    from submission.pps.table_structure import table_structures,numeric_invariant
    text='품명\n규격\n단위\n수량\n단가\n금액\n온도계\n실외용\n개\n100\n200'
    table=table_structures(text,max_candidates=1)[0]
    assert table['candidate_search_truncated']
    assert not table['all_layouts_certified'] and not table['whole_purchase_certified']
    assert numeric_invariant(table['rows'][0],'quantity',upper=1000)['status']=='unknown'


def test_interleaved_columns_are_not_certified_as_an_observed_row_layout():
    from submission.pps.table_structure import table_structures
    text='품명\n규격\n단위\n수량\n온도계\n압력계\n10cm\n20cm\n개\n개\n2\n3'
    table=table_structures(text)[0]
    assert not table['all_layouts_certified']
    assert table['unresolved_source_ranges']
    column=next(c for c in table['complete_layout_candidates'] if c['family']=='column_major')
    assert [r['name']['text'] for r in column['rows']]==['온도계','압력계']
    assert [r['quantity']['text'] for r in column['rows']]==['2','3']


def test_layout_issue_candidates_do_not_delete_or_rewrite_original_text():
    from submission.pps.table_structure import reading_order_candidates
    text='자격을 갖추고\n부조리신고는 ☎ 1234, www.example.org 전일기준 업체여야 한다.\n3. 입찰참가자격 나. 전자입찰로 제출한다.'
    issues=reading_order_candidates(text)
    assert {i['kind'] for i in issues}>={'contact_banner_candidate','interleaved_heading_candidate'}
    assert all(i['text']==text[i['start']:i['end']] and not i['may_delete'] for i in issues)
    assert any('전일기준' not in i['text'] for i in issues if i['kind']=='contact_banner_candidate')


@pytest.mark.parametrize('amount,status', [('200','equal'),('201','inconsistent')])
def test_arithmetic_rule_can_be_checked_when_every_basis_is_explicit(amount,status):
    from submission.pps.table_structure import table_structures,numeric_invariant
    text=('품명 | 단위 | 수량(개) | 단가(원/개,부가세포함) | 금액(원,부가세포함) | 비고\n'
          '온도계 | 개 | 2 | 100 | '+amount+' | 금액은 수량×단가로 산정하며 반올림과 절사를 하지 않는다.')
    row=table_structures(text)[0]['rows'][0]
    assert row['arithmetic']['status']==status and row['arithmetic']['constraint_certified']
    answer=numeric_invariant(row,'amount',upper=1000)
    assert answer['status']==('invariant' if status=='equal' else 'unknown')


def test_typed_rows_do_not_claim_a_tax_conversion_or_pack_conversion():
    from submission.pps.table_structure import table_structures
    text=('품명 | 단위 | 수량(개) | 단가(원/개,부가세별도) | 금액(원,부가세포함) | 비고\n'
          '온도계 | 팩 | 2 | 100 | 220 | 금액은 수량×단가로 산정하며 반올림하지 않는다.')
    assert not table_structures(text)[0]['rows'][0]['arithmetic']['constraint_certified']


def test_dimension_label_and_ordinary_sentence_ending_are_not_product_or_column_headers():
    from submission.pps.table_structure import table_structures,reading_order_candidates
    text='품명\n규격\n단위\n수량\n매트\n700*400*3mm\n(가로*세로*두께)\n개\n2'
    row=table_structures(text)[0]['rows'][0]
    assert [c['text'] for c in row['name_candidates']]==['매트']
    assert not reading_order_candidates('7. 입찰무효 다음과 같이 판단합니다.\n가. 별도 사항')


def test_structure_queries_only_use_already_returned_source_and_keep_candidates_fallible():
    from submission.pps.purchase_reading import catalog_source_queries
    text='품명 | 규격 | 단위\n온도계 | 실외용 | 개\n2. 납품조건\n품명 | 규격 | 단위\n압력계 | 실내용 | 개'
    rec={'docs':[{'doc_id':'D0','type':'규격서','text':text}]}
    end=text.index('\n2. 납품조건')
    seed={'spans':[{'doc_index':0,'doc_type':'규격서','start':0,'end':end,'text':text[:end]}]}
    queries=catalog_source_queries(rec,seed,query_policy='table_candidates')
    assert queries[0]['query']=='온도계'
    assert not queries[0]['structure']['purchase_identity_certified']
    assert all('압력계' not in q['query'] for q in queries)
    assert all(q['evidence']['end']<=end for q in queries)


@pytest.mark.parametrize('fenced', [False,True])
def test_a_missing_last_value_is_not_erased_as_an_optional_pipe_fence(fenced):
    from submission.pps.table_structure import table_structures
    header='품명 | 단위 | 수량 | 단가 | 금액'
    row='온도계 | 개 | 2 | 100 | '
    if fenced:header='| '+header+' |';row='| '+row+' |'
    table=table_structures(header+'\n'+row)[0]
    assert len(table['rows'])==1
    assert table['rows'][0]['candidates'][0]['amount'] is None
    assert table['rows'][0]['candidates'][0]['unit_price']['text']=='100'


@pytest.mark.parametrize('note', [
    '예시: 금액은 수량×단가로 산정하며 반올림하지 않는다.',
    '금액은 수량×단가로 산정하며 반올림하지 않는다. 단, 10원 미만은 절사한다.',
])
def test_example_or_additional_truncation_does_not_certify_an_exact_arithmetic_rule(note):
    from submission.pps.table_structure import table_structures
    header='품명 | 단위 | 수량(개) | 단가(원/개,부가세포함) | 금액(원,부가세포함) | 비고'
    row=table_structures(header+'\n온도계 | 개 | 2 | 101 | 200 | '+note)[0]['rows'][0]
    assert not row['arithmetic']['constraint_certified']


def test_one_rows_rule_does_not_certify_another_rows_arithmetic_basis():
    from submission.pps.table_structure import table_structures
    text=('품명 | 단위 | 수량(개) | 단가(원/개,부가세포함) | 금액(원,부가세포함) | 비고\n'
          '온도계 | 개 | 2 | 100 | 200 | 금액은 수량×단가로 산정하며 반올림하지 않는다.\n'
          '압력계 | 개 | 2 | 101 | 200 | 별도 협의')
    assert not table_structures(text)[0]['rows'][1]['arithmetic']['constraint_certified']
