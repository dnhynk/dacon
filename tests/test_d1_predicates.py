"""D1: qualification headings, deemed special corporations inside a size clause,
an estimate band shared by every VAT reading, and registered private-contract routes."""
import copy

import pytest

from submission.pps.qualification import band_estimate, infer, inventory, qualification_facts
from submission.pps.knowledge import Knowledge
from tests.test_service_identity import DATA


def notice(header, clause, *, estimate=150_000_000, amount_line='', tail='5. 계약조건\n기한 준수'):
    return {'id': 'synthetic-d1',
        'meta': {'업무구분': '일반용역', '적용계약법': '국가계약법', '계약방법': '제한경쟁',
                 '입찰추정가격': estimate},
        'docs': [{'type': '공고문', 'doc_id': 'notice', 'text':
            '1. 사업개요\n사업명: 미확정업무\n' + amount_line + header + '\n' + clause + '\n' + tail}],
        'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


def facts_of(record):
    return qualification_facts(record, inventory(record))


def decide(record, state='general'):
    knowledge = Knowledge(DATA)
    knowledge.detailed_product_facts(record)
    baseline = {**{f'v{i}': '0' for i in range(1, 25)}, **{f'e{i}': '' for i in range(1, 25)}}
    _, facts = infer(record, baseline, knowledge._product_facts)
    return infer(record, baseline, knowledge._product_facts,
                 product_override={**facts['product'], 'status': state})


@pytest.mark.parametrize('header', [
    '3. 입찰 참가자격(아래의 자격 요건을 모두 갖춘 업체)',
    '4. 입찰참가자격 : 아래의 자격요건을 모두 갖춘 업체',
    '3. 입찰참가자격 : 다음 각 호의 자격요건을 모두 갖춘 사업자',
    '3. 견적서 제출 참가자격(아래의 자격요건 모두 충족)',
    '5. 입찰서 제출자격',
    '3. 입찰서 제출자격(아래 자격을 모두 갖춘 자)',
    '4. 입찰 참가자격 및 구비조건',
    '2. 견적제출 참가자격 및 계약방식',
])
def test_qualification_heading_variants_close_the_section(header):
    facts = facts_of(notice(header, '가. 사업자등록을 마친 업체이어야 합니다.'))
    assert facts['closed_eligibility'] and not facts['unclosed_eligibility']
    assert facts['no_size'] and facts['no_direct']


@pytest.mark.parametrize('header', [
    '3. 입찰 참가자격(아래의 자격 요건을 모두 갖춘 업체) - 작성 예시',
    '5. 입찰서 제출자격 삭제',
    '5. 입찰서 제출방법',
    '4. 입찰 참가자격 및 평가기준',
])
def test_examples_withdrawals_and_other_captions_do_not_open_it(header):
    assert not facts_of(notice(header, '가. 사업자등록을 마친 업체이어야 합니다.'))['no_size']


def test_a_size_clause_under_a_recognized_variant_is_operative():
    facts = facts_of(notice('5. 입찰서 제출자격',
        '가. 「중소기업기본법」 제2조에 따른 소기업 또는 「소상공인기본법」 제2조에 따른 소상공인으로서 '
        '소기업·소상공인확인서를 소지한 업체'))
    assert facts['allowed'] == ['micro', 'small'] and not facts['no_size']


@pytest.mark.parametrize('clause,allowed', [
    ('가. ｢중소기업제품 구매촉진 및 판로지원에 관한 법률｣ 제2조에 따른 소기업자(소상공인, 소기업 간주 특별법인 포함)로 '
     '｢중소기업 범위 및 확인에 관한 규정｣에 따라 발급된 소기업·소상공인 확인서를 소지한 자', ['micro', 'small']),
    ('③ 「중소기업기본법」 제2조에 따른 소기업 또는 「소상공인기본법」 제2조에 따른 소상공인(판로지원법 제33조제1항에 따라 '
     '중소기업자로 보는 법인 또는 단체 중 중소벤처기업부장관이 정하여 고시하는 기준에 해당하는 법인 또는 단체를 포함)으로서 '
     '「중소기업 범위 및 확인에 관한 규정」에 따라 발급된 소기업·소상공인 확인서(특별법인 소기업(소상공인) 간주 확인서 포함)를 '
     '소지한 자', ['micro', 'small']),
    ('3) 판로지원법 제2조의 규정에 의한 소기업으로 발급된 소기업, 소상공인 확인서를 소지한 업체 또는, '
     '판로지원법 제33조의 1항에 의거 소기업으로 간주되는 특별법인', ['micro', 'small']),
])
def test_deemed_special_corporations_inside_the_clause_keep_its_commercial_bound(clause, allowed):
    facts = facts_of(notice('3. 입찰참가자격', clause))
    assert facts['allowed'] == allowed and not facts['no_size']
    entry = next(e for e in facts['active_size'])
    assert entry['postprocessing_repair'] == 'deemed_special_corporation_inside_size_clause'


@pytest.mark.parametrize('clause', [
    '가. 소기업·소상공인 확인서를 소지한 업체 또는 「중소기업협동조합법」에 따른 중소기업협동조합으로서 적격조합 확인서를 소지한 자',
    '가. 소기업·소상공인 확인서를 소지한 업체 또는 벤처기업 확인서를 소지한 자',
    '가. 「판로지원법」 제33조제1항에 의거 중소기업자로 간주되는 특별법인임을 증명 가능한 확인서를 소지한 자',
    '※ 소기업·소상공인 확인서를 소지한 업체(소기업 간주 특별법인 포함)',
])
def test_other_branches_and_notes_stay_unresolved(clause):
    facts = facts_of(notice('3. 입찰참가자격', clause))
    assert not any(e.get('postprocessing_repair') == 'deemed_special_corporation_inside_size_clause'
                   for e in facts['inventory'])


def test_every_vat_reading_in_one_band_establishes_it():
    record = notice('3. 입찰참가자격', '가. 사업자등록을 마친 업체이어야 합니다.', estimate=70_000_000,
                    amount_line='추정가격 : 77,000,000원(부가가치세 포함)\n')
    original = copy.deepcopy(record)
    row, facts = decide(record)
    assert facts['product']['estimate_won'] is None
    assert band_estimate(facts['product'])[0] == 70_000_000
    assert facts['qualification']['estimate_band_readings']
    assert row['v18'] == '1' and record == original


@pytest.mark.parametrize('amount_line,estimate', [
    ('추정가격 : 105,000,000원(부가세 포함)\n', None),        # 105,000,000 or 95,454,545
    ('추정가격 : 105,000,000원(부가세 포함)\n', 105_000_000),
    ('추정가격 : 77,000,000원(부가세 포함)\n', 120_000_000),  # the registered estimate disagrees
])
def test_readings_that_straddle_a_threshold_leave_it_open(amount_line, estimate):
    record = notice('3. 입찰참가자격', '가. 사업자등록을 마친 업체이어야 합니다.', estimate=estimate,
                    amount_line=amount_line)
    row, facts = decide(record)
    assert facts['product']['estimate_won'] is None
    assert band_estimate(facts['product'])[0] is None
    assert row['v16'] == '0' and row['v18'] == '0'


@pytest.mark.parametrize('route', [
    '추정가격 2천만원 초과 1억원 이하 물품·용역(소기업 소상공인 계약)',
    '추정가격 2천만원 초과 1억원 이하 물품·용역(학술연구 원가계산 건설기술 등과 관련된 계약)',
    '추정가격 2천만원 초과 1억원 이하 물품·용역(여성기업, 장애인기업, 사회적기업, 사회적협동조합, 자활기업, 마을기업 계약)',
    '추정가격 2천만원 초과 5천만원 이하 물품·용역(청년창업기업)',
])
def test_registered_private_contract_route_is_not_a_missing_restriction(route):
    record = notice('3. 입찰참가자격', '가. 사업자등록을 마친 업체이어야 합니다.', estimate=45_000_000)
    record['meta'].update(계약방법='수의계약', 조항호내용=route)
    row, facts = decide(record)
    assert row['v18'] == '0'
    assert facts['decisions']['v18']['reason'] == 'registered_private_contract_route_not_a_missing_restriction'


@pytest.mark.parametrize('method,route', [
    ('제한경쟁', '추정가격 2천만원 초과 1억원 이하 물품·용역(소기업 소상공인 계약)'),
    ('수의계약', '추정가격 2천만원 이하 물품 또는 용역'),
    ('수의계약', '[판로지원법 시행령] 조합추천수의계약'),
    ('일반경쟁', '경쟁은 입찰의 방법으로 행함'),
])
def test_other_methods_and_routes_keep_the_absence_check(method, route):
    record = notice('3. 입찰참가자격', '가. 사업자등록을 마친 업체이어야 합니다.', estimate=45_000_000)
    record['meta'].update(계약방법=method, 조항호내용=route)
    row, _ = decide(record)
    assert row['v18'] == '1'


def test_an_estimate_above_the_registered_route_range_is_not_that_route():
    record = notice('3. 입찰참가자격', '가. 사업자등록을 마친 업체이어야 합니다.', estimate=103_824_000)
    record['meta'].update(계약방법='수의계약',
                          조항호내용='추정가격 2천만원 초과 1억원 이하 물품·용역(소기업 소상공인 계약)')
    row, facts = decide(record)
    assert row['v16'] == '1'
    assert facts['decisions']['v16']['reason'] == 'complete_general_middle_band_no_size_requirement'
