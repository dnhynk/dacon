"""B2: amount bands, the statutory office anchor, and the duplicate restriction."""
from submission.pps.performance import performance_facts, purchaser
from submission.pps.prices import project_prices
from submission.pps.region_thresholds import below_ceiling
from submission.pps.regions import OFFICE, multiple_region_check
from submission.pps.rules import joint_share_check, narrow_region_check

TOKEN = '[지역:r1|단위=기초|광역=경기도]'


def record(body, *, work='일반용역', price=80_000_000, law='국가계약법',
           method='제한경쟁', extra=()):
    docs = [{'doc_id': 'notice', 'type': '공고문',
             'text': '2. 입찰 참가자격\n' + body}]
    docs.extend({'doc_id': f'attach{i}', 'type': '제안요청서', 'text': t}
                for i, t in enumerate(extra))
    return {'id': 'b2-case', 'meta': {
        '적용계약법': law, '업무구분': work, '계약방법': method,
        '입찰추정가격': price, '배정예산금액': int(price * 1.1)}, 'docs': docs,
        'input_completeness': {'공고문_실재': True, '추출_성공': True,
                               '무탈락': True, '완전관측': True}}


def estimate(text, meta=80_000_000):
    rec = record(text, price=meta)
    return project_prices(rec)['estimated_price']


def test_conflicting_amounts_under_the_same_ceiling_still_prove_the_band():
    price = estimate('가. 추정가격: 113,636,364원\n나. 예비가격기초금액(추정가격): 125,000,000원')
    assert price['status'] == 'conflict'
    assert below_ceiling(price, 230_000_000) is True


def test_unresolved_tax_basis_bounds_the_estimate_from_above_only():
    price = estimate('가. 추 정 가 격: 94,600,000원(면세)')
    assert price['value_won'] is None and price['unresolved_tax_basis']
    assert below_ceiling(price, 230_000_000) is True
    assert below_ceiling(price, 50_000_000) is None


def test_office_anchor_covers_the_statutory_sole_proprietor_wording():
    assert OFFICE.search('주된 영업장의 소재지가 경기도에 있는 업체')
    assert OFFICE.search('사업자등록증 상 소재지가 대구광역시에 소재한 업체')
    assert not OFFICE.search('납품장소는 수요기관이 지정하는 장소')


def test_basic_region_restriction_written_as_an_office_location_assignment():
    rec = record(f'나. 입찰자는 계약체결일까지 주된 영업소가 경기도 {TOKEN}로 되어 있어야 합니다.')
    assert narrow_region_check(rec)['value'] == 1


def test_delivery_address_with_the_same_token_is_not_an_office_restriction():
    rec = record(f'나. 납품장소 : [수요기관(공공기관)](경기도 {TOKEN} [상세주소]) 지정장소')
    assert narrow_region_check(rec) is None


def test_adjacent_province_scope_may_be_written_with_the_statutory_si_do_name():
    rec = record('6) (참가 지역) 법인등기부상 본점 소재지가 [수요기관(대학)] 소재 '
                 '시·도 또는 이와 경계를 접한 인접 시·도에 있는 업체만 제안서를 제출할 수 있습니다.')
    assert multiple_region_check(rec)['value'] == 1


def test_public_education_purchaser_narrows_the_required_experience():
    n = '최근3년이내에교육청,교육지원청또는각급학교가발주한건설폐기물처리용역을1건이상완료한실적이있는자만'
    assert purchaser(n) == 'specific_purchaser_required'
    other = '공립초등학교의방과후프로그램운영실적을보유한업체만,다른교육시설의실적은자격으로인정하지않습니다'
    assert purchaser(other) == 'specific_purchaser_required'
    assert purchaser('공공기관등을대상으로교육실적3천만원이상수행한업체') == 'unspecified'


def test_goods_experience_restriction_is_not_scored_as_a_duplicate():
    """영 제21조①3호 allows a goods experience limit only for 물품제조계약.

    A supply record required by a 물품(내자) notice is therefore not one of the
    제한사항 the duplicate rule governs, and official PPS-DEV-057 labels exactly
    that combination 0. The judgment stays with the model.
    """
    body = ('가. 최근 3년 이내 유사 물품 납품 실적이 있는 업체\n'
            '나. 주된 영업소가 경기도에 소재한 업체')
    values = []
    for work in ('일반용역', '물품(내자)'):
        facts = performance_facts(record(body, work=work), consumer=True)
        assert facts['operative_regions']
        values.append(facts['overlays']['v8']['value'])
    assert values == [1, None]


def test_share_sum_note_does_not_erase_an_explicit_sub_minimum_share():
    rec = record('다. 공동수급(공동이행방식)\n- 구성원별 계약참여 최소 지분율은 2% 이상이어야 함',
                 extra=["※ 협정구분이 '공동'인 경우는 지분율의 합이 100%가 되어야 하며,\n"
                        '공동이행 구성원별로 확인합니다.'])
    decision = joint_share_check(rec)
    assert decision['value'] == 1
    assert [x['value'] for x in decision['parsed']] == [2.0]
