"""D3: goods experience restrictions below the notice amount, and the statutory business-site anchor."""
from submission.pps.performance import performance_facts
from submission.pps.regions import OFFICE, above_ceiling_region_check, multiple_region_check


def record(body, *, work='물품(내자)', price=83_100_000, law='국가계약법', method='제한경쟁', title=''):
    return {'id': 'd3-case', 'meta': {
        '적용계약법': law, '업무구분': work, '계약방법': method,
        '입찰추정가격': price, '배정예산금액': int(price * 1.1)},
        'docs': [{'doc_id': 'notice', 'type': '공고문', 'text': title + '2. 입찰 참가자격\n' + body}],
        'input_completeness': {'공고문_실재': True, '추출_성공': True, '무탈락': True, '완전관측': True}}


def v2(rec, *, consumer=True):
    return performance_facts(rec, consumer=consumer)['overlays']['v2']


def test_goods_purchase_below_notice_with_a_held_record_is_item2():
    rec = record('가. 외국 학술지 공급계약을 3천만원 이상 체결한 실적이 있는 자')
    decision = v2(rec)
    assert decision['value'] == 1
    assert decision['reason'] == 'mandatory_goods_experience_below_supplied_notice'


def test_goods_branch_leaves_prompt_facts_unchanged():
    rec = record('가. 최근 3년 이내 드론 납품 실적이 있는 자')
    assert v2(rec, consumer=False)['value'] is None


def test_goods_at_or_above_notice_is_not_item2():
    rec = record('가. 동등·유사 장비 납품 실적이 있는 업체', price=272_727_273)
    assert v2(rec)['value'] == 0


def test_goods_record_with_an_alternative_certification_route_is_not_held():
    rec = record('③ “3년 내 납품실적(첨부된 규격서의 물품) 또는 사전품질인증을 통해 '
                 '품질적격판정을 받은 업체”이어야 합니다.')
    assert v2(rec)['value'] != 1


def test_note_that_mentions_experience_is_not_a_requirement():
    rec = record('- 전자조달시스템 첨부문서가 복호화되어 있어 입찰참가자격여부(실적)는 개찰 후 '
                 '확인 가능하여 무자격자의 예정가격 선택이 반영될 수 있습니다.')
    assert v2(rec)['value'] != 1


def test_goods_actual_small_quote_procedure_still_abstains():
    rec = record('가. 최근 3년 이내 간식 납품 실적이 있는 업체', law='지방계약법', method='수의계약',
                 title='초등돌봄교실 간식 구매 소액수의 견적제출 안내공고\n')
    assert v2(rec)['value'] is None


def test_service_branch_is_unchanged():
    rec = record('가. 최근 5년 이내 디자인서비스 용역 수행 실적이 있는 자', work='일반용역', price=90_454_545)
    assert v2(rec)['reason'] == 'mandatory_service_experience_below_supplied_notice'


def test_business_site_is_the_statutory_office_anchor():
    assert OFFICE.search('공고일 기준 사업장의 소재지가 울산광역시 또는 부산광역시에 위치한 업체')
    assert OFFICE.search('서울특별시 및 경기도 내에 주된 사업장 소재지가 있는 자')


def test_two_province_business_site_restriction_below_ceiling_is_item7():
    rec = record('다. 「국가를 당사자로 하는 계약에 관한 법률 시행령」제21조에 의거 공고일 기준 '
                 '사업장의 소재지가 울산광역시 또는 부산광역시에 위치한 업체',
                 work='일반용역', price=30_000_000)
    assert multiple_region_check(rec)['value'] == 1


def test_performance_site_is_not_a_bidder_location():
    rec = record('나. 사업장 소재지: 울산광역시 및 부산광역시 소재 수요기관 시설(과업 수행 장소)',
                 work='일반용역', price=30_000_000)
    assert multiple_region_check(rec) is None


def test_business_site_restriction_at_or_above_ceiling_is_item5():
    rec = record('ㅇ 공고일 현재 사업장 소재지가 강원특별자치도 내에 있는 사업자 또는 대리점이어야 한다.',
                 work='일반용역', price=1_000_000_000, method='일반경쟁')
    assert above_ceiling_region_check(rec)['value'] == 1
