"""Monetary duplicates must agree before a literal value can be consumed."""
from decimal import Decimal
import pytest
from submission.pps.amounts import WON, won_value
from submission.pps.comparison import amount_facts, compare
from submission.pps.prices import project_prices, in_band
from submission.pps.performance import won as performance_won
from submission.pps.other_checks import won as other_won
from tests.test_comparison import record


@pytest.mark.parametrize('text,value', [
    ('123,270,400(일억이천삼백이십칠만사백)원',123270400),
    ('35,800,000 (금삼천오백팔십만) 원',35800000),
    ('75,100,000원(일금칠천오백일십만원정)',75100000),
    ('250,000,000원(금이억오천만원, 부가가치세 포함)',250000000),
    ('20억5천만원(이십억오천만원)',2050000000),
    ('1,030,000（백삼만원）원',1030000),
    ('100,000원정',100000),
    ('91,200,000원(금구천일백이십만원 / 부가세 포함)',91200000),
    ('82,000,000(금팔천이백만원)',82000000),
])
def test_numeric_and_spelled_out_values_agree_in_all_consumers(text,value):
    assert WON.fullmatch(text)
    assert won_value(text)==other_won(text)==performance_won(text)==Decimal(value)


@pytest.mark.parametrize('text', [
    '20,000,000(삼천만원)원',
    '90,000,000원(금일억원정)',
    '10,000,000(일천백천만원)원',
    '50,000,000원(일이만원)',
    '2 3원',
    '30,000,000(삼천만원',
])
def test_conflicting_or_malformed_literals_are_not_silently_repaired(text):
    assert won_value(text) is None


def test_annotation_tax_qualifier_stays_with_its_own_amount_field():
    rec=record('사업예산: 250,000,000원(금이억오천만원, 부가가치세 포함)\n\n추정가격: 220,000,000원')
    facts=amount_facts(rec)
    assert facts[0]['value']=='250000000' and facts[0]['basis']=='including_vat'
    assert facts[1]['basis']=='excluding_vat'


def test_source_amount_is_not_replaced_by_metadata_only_because_of_an_annotation():
    rec=record('사업예산: 120,000,000(일억이천만원)원(VAT포함)',배정예산금액=55_000_000)
    price=project_prices(rec)['budget']
    assert price['value_won']==120_000_000 and price['effective_source']=='notice'
    assert price['source_conflict']
    assert next(c for c in compare(rec)['comparisons'] if c['field']=='budget')['status']=='different'


@pytest.mark.parametrize('text', ['90,000,000원(일억원)', '2억1조원'])
def test_an_explicit_unreadable_notice_assignment_blocks_metadata_band_certification(text):
    rec=record('추정가격: '+text,입찰추정가격=50_000_000)
    price=project_prices(rec)['estimated_price']
    assert price['status']=='unknown' and price['value_won'] is None
    assert in_band(price,upper=100_000_000) is None


def test_generic_price_reference_is_not_an_unreadable_assignment():
    price=project_prices(record('추정가격은 관련 기준에 따른다.'))['estimated_price']
    assert price['value_won']==50_000_000


def test_total_prefix_does_not_change_the_meaning_of_the_amount_field():
    rec=record('기초금액: 전체 66,000,000원(부가세 포함)')
    fact=amount_facts(rec)[0]
    assert fact['value']=='66000000' and fact['field']=='base_price'
    assert next(c for c in compare(rec)['comparisons'] if c['field']=='budget')['status']=='no_comparable_document_value'


def test_exact_suffix_does_not_consume_the_start_of_an_approximation():
    fact=amount_facts(record('사업예산: 110,000,000원 정도'))[0]
    assert fact['scope']=='bounded' and fact['value_relation']=='정도'


def test_jeon_duplicate_preserves_fractional_won_and_does_not_round():
    from submission.pps.temporal import extract_amounts
    text='1,458.90원(금일천사백오십팔원구십전)'
    assert won_value(text)==other_won(text)==Decimal('1458.90')
    assert Decimal(amount_facts(record('기초금액: '+text))[0]['value'])==Decimal('1458.90')
    assert Decimal(extract_amounts(record('기초금액: '+text))[0]['value'])==Decimal('1458.90')
    with pytest.raises(ValueError):
        performance_won(text)


@pytest.mark.parametrize('text', [
    '추정가격: 2억1조원 미만인 사업에 해당하면 별도 절차를 적용한다.',
    '예시:\n추정가격: 2억1조원',
    '추정가격: 2억1조원\n이 금액은 적용하지 않는다.',
    '차년도 추정가격: 2억1조원',
])
def test_unreadable_threshold_example_or_partial_is_not_an_active_amount(text):
    rec=record('추정가격: 55,000,000원\n\n'+text)
    price=project_prices(rec)['estimated_price']
    assert price['status']=='known' and price['value_won']==55000000


def test_unreadable_notice_does_not_borrow_a_readable_attachment_assignment():
    rec=record('추정가격: 90,000,000원(일억원)')
    rec['docs'].append(dict(doc_id='attachment',type='제안요청서',text='추정가격: 50,000,000원'))
    price=project_prices(rec)['estimated_price']
    assert price['status']=='unknown' and price['effective_source']=='notice'
    assert price['all_observed_values_won']==[50000000]
    assert in_band(price,upper=100000000) is None


def test_numeric_spacing_is_not_erased_by_experience_extraction():
    from submission.pps.performance import amounts, span
    doc=dict(doc_id='notice',type='공고문',text='실적 2 3원 이상')
    readings=amounts(span(doc,0,0,len(doc['text'])),doc)
    assert len(readings)==1 and readings[0]['won'] is None
    assert readings[0]['evidence']['text']=='2 3원'


@pytest.mark.parametrize('note', ['(삼천만원', '(삼천만원）', '(삼천만원 및 별도협의)', '(삼천만원)(사천만원)'])
def test_scanner_does_not_escape_a_damaged_or_extra_duplicate(note):
    text='30,000,000원'+note
    for match in WON.finditer(text):
        assert won_value(match[0]) is None


def test_nonmonetary_parenthesis_is_not_misread_as_a_spelled_duplicate():
    for text in ['30,000,000원(이윤 포함)', '30,000,000원(일반 과업)', '30,000,000원(부가세 포함)']:
        match=WON.search(text)
        assert match and won_value(match[0])==30000000


def test_currency_annotations_do_not_hide_vat_from_temporal_reader():
    from submission.pps.temporal import extract_amounts
    fact=extract_amounts(record('사업예산: 90,000,000원(구천만원/부가세 포함)'))[0]
    assert fact['basis']=='budget_including_vat'


def test_unparsed_wider_project_does_not_replace_explicit_tender_scope():
    rec=record('사업예산: 90,000,000원(일억원)\n\n입찰대상금액: 30,000,000원(부가세 포함)')
    price=project_prices(rec)['budget']
    assert price['status']=='known' and price['value_won']==30000000
    wider=next(f for f in price['typed_observations'] if f['label']=='사업예산')
    assert wider['scope']=='project_total' and wider['value'] is None


def test_fractional_source_project_price_is_not_silently_replaced_or_rounded():
    price=project_prices(record('추정가격: 999.50원'))['estimated_price']
    assert price['status']=='unknown' and price['value_won'] is None
    assert in_band(price,upper=100000000) is None


@pytest.mark.parametrize('money', [
    '12,000,000원(일천이백만원)', '11,000,000(일천일백만원)원', '5천만원(오천만원)',
])
def test_exact_quote_offsets_follow_the_original_document(money):
    text='공고 개요\n\n가. 사업예산: '+money+'(VAT포함)\n\n나. 제출기한: 다음 주'
    rec=record(text)
    fact=amount_facts(rec)[0]
    assert text[fact['start']:fact['end']]=='가. 사업예산: '+money+'(VAT포함)'
    assert fact['scope']=='whole' and fact['basis']=='including_vat'
