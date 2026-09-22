"""Source-only title tag comparisons, with uncertainty and boundary controls."""
import pytest

from submission.pps.comparison import compare, positive_decision, title_tag_decision


def record(tag='제한경쟁·1억원미만', price=120_000_000, budget=132_000_000, method='제한경쟁'):
    return {'id': 'synthetic', 'meta': {'계약방법': method, '입찰추정가격': price, '배정예산금액': budget},
            'docs': [{'type': '공고문', 'text': '공고명: 시설 관리 용역(' + tag + ')'}]}


@pytest.mark.parametrize('separator', ['·', 'ㆍ', '・', '･', '.'])
def test_separator_and_exact_source_evidence(separator):
    rec = record('제한경쟁' + separator + '1억원미만')
    decision = positive_decision(rec, compare(rec))
    assert decision['value'] == 1
    assert decision['reason'] == 'title_tag_metadata_difference'
    assert decision['evidence'] == rec['docs'][0]['text']


@pytest.mark.parametrize('amount,value', [('2천만원', 20_000_000), ('5천만원', 50_000_000),
    ('1억원', 100_000_000), ('3억원', 300_000_000), ('10억원', 1_000_000_000),
    ('50억원', 5_000_000_000), ('100억원', 10_000_000_000)])
@pytest.mark.parametrize('relation,offset,expected', [('미만', -1, False), ('미만', 0, True),
    ('이하', 0, False), ('이하', 1, True), ('이상', 0, False), ('이상', -1, True),
    ('초과', 0, True), ('초과', 1, False)])
def test_exact_inequality_boundaries(amount, value, relation, offset, expected):
    assert bool(title_tag_decision(record('제한경쟁·' + amount + relation,
                                        value + offset, value + offset))) == expected


@pytest.mark.parametrize('price,budget', [(90_000_000, 110_000_000), (110_000_000, 90_000_000),
    (None, 110_000_000), (110_000_000, None), ('NaN', 110_000_000),
    (True, 110_000_000), (0, 110_000_000), (-1, 110_000_000)])
def test_amount_requires_two_known_contradictions(price, budget):
    assert title_tag_decision(record(price=price, budget=budget)) is None


def test_method_mismatch_independent_of_amounts():
    assert title_tag_decision(record(price=None, budget=None, method='일반경쟁'))['value'] == 1
    assert title_tag_decision(record(price=10, budget=11, method=None)) is None
    assert title_tag_decision(record(price=10, budget=11, method='협상에의한계약')) is None


@pytest.mark.parametrize('tag', ['제한경쟁·금액미상', '제한경쟁·1억원', '제한경쟁·1억억원미만',
    '알수없음·1억원미만', '제한경쟁/1억원미만', '제한경쟁·1억원미만 또는 3억원이상',
    '제한경쟁·억원미만', ''])
def test_unparsed_tag_abstains_even_if_method_differs(tag):
    assert title_tag_decision(record(tag, method='일반경쟁')) is None


def test_conflicting_and_duplicate_tags():
    rec = record()
    rec['docs'][0]['text'] += '\n다른 제목(제한경쟁·3억원미만)'
    assert title_tag_decision(rec) is None
    rec = record()
    rec['docs'][0]['text'] += '\n같은 제목（제한경쟁ㆍ1억원미만）'
    assert title_tag_decision(rec)['value'] == 1
    rec['docs'].append({'type': '공고문', 'text': '제목(일반경쟁·1억원미만)'})
    assert title_tag_decision(rec) is None


def test_valid_tag_does_not_hide_unparsed_candidate():
    rec = record()
    rec['docs'][0]['text'] += '\n제목(제한경쟁·금액미상)'
    assert title_tag_decision(rec) is None


def test_notice_only_and_line_length():
    rec = record()
    rec['docs'][0]['type'] = '제안요청서'
    assert title_tag_decision(rec) is None
    rec = record()
    rec['docs'][0]['text'] = '가' * 501 + rec['docs'][0]['text']
    assert title_tag_decision(rec) is None


def test_existing_positive_is_preserved():
    rec = record(price=10, budget=11)
    rec['docs'][0]['text'] += '\n사업예산: 55,000,000원(부가세 포함)'
    decision = positive_decision(rec, compare(rec))
    assert decision['value'] == 1
    assert decision['reason'] == 'same_semantic_field_difference'
